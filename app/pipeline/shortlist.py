from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.models import (
    DailyBar,
    NewsEvent,
    ResearchEvidence,
    ScheduledJobRun,
    ShortlistItem,
    ShortlistRun,
)
from app.services.subscription_research import (
    CodexSubscriptionResearcher,
    PROMPT_VERSION,
    inspect_codex_subscription,
    validate_research_item,
)


class ShortlistService:
    def __init__(self, repo, researcher: CodexSubscriptionResearcher | None = None):
        self.repo = repo
        self.session = repo.session
        self.researcher = researcher or CodexSubscriptionResearcher()

    def prepare(self, *, radar_run_id: str | None = None, cutoff_at: datetime | None = None) -> dict[str, Any]:
        cutoff = cutoff_at or datetime.now().replace(microsecond=0)
        rows = self._radar_rows(radar_run_id=radar_run_id, limit=30)
        if not rows:
            raise RuntimeError("尚無每日雷達資料，請先執行全市場量化分析。")
        source_run_id = str(rows[0].get("run_id") or "")
        run_date = date.fromisoformat(str(rows[0].get("run_date") or cutoff.date().isoformat()))
        existing = self.session.execute(
            select(ShortlistRun)
            .where(ShortlistRun.source_radar_run_id == source_run_id)
            .order_by(ShortlistRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return self._run_summary(existing)

        run_id = f"shortlist_{run_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        run = ShortlistRun(
            run_id=run_id,
            run_date=run_date,
            source_radar_run_id=source_run_id,
            cutoff_at=cutoff,
            status="quant_ready",
            ai_status="pending",
            candidate_count=len(rows),
        )
        self.session.add(run)
        for index, row in enumerate(rows, start=1):
            self.session.add(
                ShortlistItem(
                    run_id=run_id,
                    symbol=str(row.get("symbol") or "").strip().upper(),
                    name=str(row.get("name") or ""),
                    quant_rank=index,
                    quant_score=float(row.get("risk_adjusted_score") or row.get("entry_score") or 0.0),
                    price=float(row.get("price") or 0.0),
                    reason_tags_json=json.dumps(row.get("reason_tags") or [], ensure_ascii=False),
                    blocker_tags_json=json.dumps(row.get("blocker_tags") or [], ensure_ascii=False),
                )
            )
        self.session.flush()
        return self._run_summary(run)

    def enrich(self, run_id: str | None = None, *, scope: str = "daily", force: bool = False) -> dict[str, Any]:
        run = self._get_run(run_id)
        items = self._items_for_run(run.run_id)
        if not items:
            raise RuntimeError("短期候選清單沒有任何股票。")

        packets: list[dict[str, Any]] = []
        packet_hashes: dict[str, str] = {}
        cached = 0
        for item in items:
            packet = self._candidate_packet(item, cutoff_at=run.cutoff_at)
            input_hash = hashlib.sha256(
                json.dumps(packet, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            packet_hashes[item.symbol] = input_hash
            cached_evidence = None if force else self._cached_evidence(item.symbol, input_hash)
            if cached_evidence is not None:
                evidence = self._upsert_evidence(
                    run=run,
                    symbol=item.symbol,
                    scope=scope,
                    input_hash=input_hash,
                    payload=self._evidence_payload(cached_evidence),
                    status="accepted",
                    errors=[],
                )
                self._apply_evidence(item, evidence)
                cached += 1
            else:
                packets.append(packet)

        accepted = cached
        rejected = 0
        runner_statuses: list[str] = []
        candidate_symbols = {item.symbol for item in items}
        for offset in range(0, len(packets), 10):
            batch = packets[offset : offset + 10]
            result = self.researcher.analyze_batch(batch, cutoff_at=run.cutoff_at)
            runner_status = str(result.get("status") or "error")
            runner_statuses.append(runner_status)
            returned = {
                str(row.get("symbol") or "").strip().upper(): row
                for row in (result.get("items") if isinstance(result.get("items"), list) else [])
                if isinstance(row, dict)
            }
            for packet in batch:
                symbol = str(packet["symbol"])
                item = next(row for row in items if row.symbol == symbol)
                raw = returned.get(symbol)
                if raw is None:
                    errors = [f"codex_{runner_status}", "missing_symbol_result"]
                    evidence = self._upsert_evidence(
                        run=run,
                        symbol=symbol,
                        scope=scope,
                        input_hash=packet_hashes[symbol],
                        payload={},
                        status="rejected",
                        errors=errors,
                    )
                    self._apply_fallback(item, evidence_status=runner_status)
                    rejected += 1
                    continue
                clean, errors = validate_research_item(
                    raw,
                    candidate_symbols=candidate_symbols,
                    cutoff_at=run.cutoff_at,
                )
                status = "rejected" if errors else "accepted"
                evidence = self._upsert_evidence(
                    run=run,
                    symbol=symbol,
                    scope=scope,
                    input_hash=packet_hashes[symbol],
                    payload=clean,
                    status=status,
                    errors=errors,
                )
                if errors:
                    self._apply_fallback(item, evidence_status="rejected")
                    rejected += 1
                else:
                    self._apply_evidence(item, evidence)
                    accepted += 1

        if not packets:
            ai_status = "normal"
        elif accepted and rejected:
            ai_status = "partial"
        elif accepted:
            ai_status = "normal"
        else:
            ai_status = next((status for status in runner_statuses if status != "normal"), "error")
        run.ai_status = ai_status
        run.status = "ai_enriched" if accepted else "quant_ready"
        run.degraded = ai_status != "normal"
        run.error_message = "" if ai_status == "normal" else f"AI 分析狀態：{ai_status}"
        self.session.flush()
        return {
            **self._run_summary(run),
            "accepted": accepted,
            "rejected": rejected,
            "cached": cached,
        }

    def refresh_premarket(
        self,
        run_id: str | None = None,
        *,
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Research only the current Top 10 without changing the frozen ranking."""
        run = self._get_run(run_id)
        if run.status != "completed":
            raise RuntimeError("最新短期清單尚未完成，無法執行盤前事件檢查。")
        cutoff = (cutoff_at or datetime.now()).replace(microsecond=0)
        scope = f"premarket_{cutoff.strftime('%Y%m%d')}"
        items = [item for item in self._items_for_run(run.run_id) if item.selected]
        if not items:
            raise RuntimeError("最新短期清單沒有可追蹤的 Top 10。")

        packets: list[dict[str, Any]] = []
        packet_hashes: dict[str, str] = {}
        accepted = 0
        cached = 0
        for item in items:
            packet = self._candidate_packet(item, cutoff_at=cutoff)
            input_hash = hashlib.sha256(
                json.dumps(packet, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            packet_hashes[item.symbol] = input_hash
            cached_evidence = self._cached_evidence(
                item.symbol,
                input_hash,
                scope_prefix="premarket_",
            )
            if cached_evidence is None:
                packets.append(packet)
                continue
            self._upsert_evidence(
                run=run,
                symbol=item.symbol,
                scope=scope,
                input_hash=input_hash,
                payload=self._evidence_payload(cached_evidence),
                status="accepted",
                errors=[],
                cutoff_at=cutoff,
            )
            accepted += 1
            cached += 1

        candidate_symbols = {item.symbol for item in items}
        rejected = 0
        runner_statuses: list[str] = []
        for offset in range(0, len(packets), 10):
            batch = packets[offset : offset + 10]
            result = self.researcher.analyze_batch(batch, cutoff_at=cutoff)
            runner_status = str(result.get("status") or "error")
            runner_statuses.append(runner_status)
            returned = {
                str(row.get("symbol") or "").strip().upper(): row
                for row in (result.get("items") if isinstance(result.get("items"), list) else [])
                if isinstance(row, dict)
            }
            for packet in batch:
                symbol = str(packet["symbol"])
                raw = returned.get(symbol)
                if raw is None:
                    self._upsert_evidence(
                        run=run,
                        symbol=symbol,
                        scope=scope,
                        input_hash=packet_hashes[symbol],
                        payload={},
                        status="rejected",
                        errors=[f"codex_{runner_status}", "missing_symbol_result"],
                        cutoff_at=cutoff,
                    )
                    rejected += 1
                    continue
                clean, errors = validate_research_item(
                    raw,
                    candidate_symbols=candidate_symbols,
                    cutoff_at=cutoff,
                )
                self._upsert_evidence(
                    run=run,
                    symbol=symbol,
                    scope=scope,
                    input_hash=packet_hashes[symbol],
                    payload=clean,
                    status="rejected" if errors else "accepted",
                    errors=errors,
                    cutoff_at=cutoff,
                )
                if errors:
                    rejected += 1
                else:
                    accepted += 1

        # Deliberately do not mutate ShortlistItem or ShortlistRun: the prior
        # day's ranking and its point-in-time AI evidence remain immutable.
        self.session.flush()
        if accepted and rejected:
            status = "partial"
        elif accepted:
            status = "normal"
        else:
            status = next((value for value in runner_statuses if value != "normal"), "error")
        return {
            "run_id": run.run_id,
            "scope": scope,
            "cutoff_at": cutoff.isoformat(),
            "status": status,
            "items": len(items),
            "accepted": accepted,
            "rejected": rejected,
            "cached": cached,
        }

    def finalize(self, run_id: str | None = None) -> dict[str, Any]:
        run = self._get_run(run_id)
        items = self._items_for_run(run.run_id)
        final_rank = 0
        for item in items:
            item.selected = False
            item.final_rank = 0
            if item.excluded_by_ai_risk:
                continue
            if final_rank < 10:
                final_rank += 1
                item.selected = True
                item.final_rank = final_rank
        if final_rank < 10:
            for item in items:
                if final_rank >= 10:
                    break
                if item.selected:
                    continue
                final_rank += 1
                item.selected = True
                item.final_rank = final_rank
        run.selected_count = final_rank
        run.status = "completed"
        run.degraded = bool(run.degraded or final_rank < 10)
        self.session.flush()
        return self.get_run(run.run_id)

    def get_latest(self, *, limit: int = 10) -> dict[str, Any]:
        run = self.session.execute(
            select(ShortlistRun)
            .where(ShortlistRun.status == "completed")
            .order_by(ShortlistRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if run is None:
            return {
                "status": "empty",
                "message": "尚無短期投資清單，請執行第一次全市場分析。",
                "items": [],
                "ai": inspect_codex_subscription().to_dict(),
            }
        return self.get_run(run.run_id, limit=limit)

    def get_run(self, run_id: str, *, limit: int = 30) -> dict[str, Any]:
        run = self._get_run(run_id)
        items = self._items_for_run(run.run_id)
        selected = [item for item in items if item.selected]
        ordered = sorted(selected or items, key=lambda row: row.final_rank or row.quant_rank)
        return {
            **self._run_summary(run),
            "items": [self._item_payload(item) for item in ordered[: max(1, min(30, int(limit)))]],
        }

    def status(self) -> dict[str, Any]:
        latest = self.session.execute(
            select(ShortlistRun).order_by(ShortlistRun.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        jobs = list(
            self.session.execute(
                select(ScheduledJobRun).order_by(ScheduledJobRun.started_at.desc()).limit(20)
            ).scalars().all()
        )
        return {
            "codex": inspect_codex_subscription().to_dict(),
            "latest_run": self._run_summary(latest) if latest else None,
            "jobs": [
                {
                    "job_name": job.job_name,
                    "run_key": job.run_key,
                    "status": job.status,
                    "started_at": job.started_at.isoformat(),
                    "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                    "duration_seconds": float(job.duration_seconds or 0.0),
                    "rows_affected": int(job.rows_affected or 0),
                    "message": job.message,
                }
                for job in jobs
            ],
        }

    def update_outcomes(self) -> dict[str, int]:
        rows = list(
            self.session.execute(
                select(ShortlistItem, ShortlistRun)
                .join(ShortlistRun, ShortlistRun.run_id == ShortlistItem.run_id)
                .where(ShortlistRun.status == "completed")
            ).all()
        )
        updated = 0
        for item, run in rows:
            bars = list(
                self.session.execute(
                    select(DailyBar)
                    .where(DailyBar.symbol == item.symbol, DailyBar.date > run.run_date)
                    .order_by(DailyBar.date.asc())
                    .limit(60)
                ).scalars().all()
            )
            if item.price <= 0:
                continue
            for horizon in (5, 20, 40, 60):
                if len(bars) < horizon:
                    continue
                attr = f"forward_return_{horizon}d"
                value = (float(bars[horizon - 1].close) / float(item.price)) - 1.0
                if getattr(item, attr) is None:
                    setattr(item, attr, value)
                    updated += 1
        self.session.flush()
        return {"updated": updated, "items": len(rows)}

    def _get_run(self, run_id: str | None) -> ShortlistRun:
        stmt = select(ShortlistRun)
        if run_id:
            stmt = stmt.where(ShortlistRun.run_id == str(run_id))
        stmt = stmt.order_by(ShortlistRun.created_at.desc()).limit(1)
        run = self.session.execute(stmt).scalar_one_or_none()
        if run is None:
            raise RuntimeError("找不到短期投資分析執行紀錄。")
        return run

    def _radar_rows(self, *, radar_run_id: str | None, limit: int) -> list[dict[str, Any]]:
        if radar_run_id:
            getter = getattr(self.repo, "_daily_radar_rows_for_run", None)
            if callable(getter):
                return [self.repo._radar_snapshot_to_dict(row) for row in getter(radar_run_id)][:limit]
        return list(self.repo.get_latest_daily_radar(count=limit))

    def _items_for_run(self, run_id: str) -> list[ShortlistItem]:
        return list(
            self.session.execute(
                select(ShortlistItem)
                .where(ShortlistItem.run_id == run_id)
                .order_by(ShortlistItem.quant_rank.asc())
            ).scalars().all()
        )

    def _candidate_packet(self, item: ShortlistItem, *, cutoff_at: datetime) -> dict[str, Any]:
        news = list(
            self.session.execute(
                select(NewsEvent)
                .where(NewsEvent.symbol == item.symbol, NewsEvent.news_date <= cutoff_at.date())
                .order_by(NewsEvent.news_date.desc(), NewsEvent.updated_at.desc())
                .limit(8)
            ).scalars().all()
        )
        return {
            "symbol": item.symbol,
            "name": item.name,
            "quant_rank": item.quant_rank,
            "quant_score": item.quant_score,
            "price": item.price,
            "reason_tags": _json_list(item.reason_tags_json)[:8],
            "blocker_tags": _json_list(item.blocker_tags_json)[:8],
            "news": [
                {
                    "title": row.title,
                    "source_name": row.source_name,
                    "url": row.url,
                    "published_at": datetime.combine(row.news_date, datetime.min.time()).isoformat(),
                }
                for row in news
            ],
        }

    def _cached_evidence(
        self,
        symbol: str,
        input_hash: str,
        *,
        scope_prefix: str | None = None,
    ) -> ResearchEvidence | None:
        cutoff = datetime.utcnow() - timedelta(days=30)
        stmt = select(ResearchEvidence).where(
                ResearchEvidence.symbol == symbol,
                ResearchEvidence.input_hash == input_hash,
                ResearchEvidence.status == "accepted",
                ResearchEvidence.created_at >= cutoff,
            )
        if scope_prefix:
            stmt = stmt.where(ResearchEvidence.research_scope.like(f"{scope_prefix}%"))
        return self.session.execute(
            stmt.order_by(ResearchEvidence.created_at.desc()).limit(1)
        ).scalar_one_or_none()

    def _upsert_evidence(
        self,
        *,
        run: ShortlistRun,
        symbol: str,
        scope: str,
        input_hash: str,
        payload: dict[str, Any],
        status: str,
        errors: list[str],
        cutoff_at: datetime | None = None,
    ) -> ResearchEvidence:
        evidence = self.session.execute(
            select(ResearchEvidence)
            .where(
                ResearchEvidence.run_id == run.run_id,
                ResearchEvidence.symbol == symbol,
                ResearchEvidence.research_scope == scope,
            )
            .limit(1)
        ).scalar_one_or_none()
        if evidence is None:
            evidence = ResearchEvidence(
                evidence_id=f"evidence_{uuid.uuid4().hex}",
                run_id=run.run_id,
                symbol=symbol,
                research_scope=scope,
                cutoff_at=cutoff_at or run.cutoff_at,
            )
            self.session.add(evidence)
        evidence.cutoff_at = cutoff_at or run.cutoff_at
        evidence.input_hash = input_hash
        evidence.status = status
        evidence.event_type = str(payload.get("event_type") or "")
        evidence.sentiment = str(payload.get("sentiment") or "neutral")
        evidence.relevance = float(payload.get("relevance") or 0.0)
        evidence.confidence = float(payload.get("confidence") or 0.0)
        evidence.impact_horizon = str(payload.get("impact_horizon") or "")
        evidence.summary = str(payload.get("summary") or "")
        evidence.risk_tags_json = json.dumps(payload.get("risk_tags") or [], ensure_ascii=False)
        evidence.sources_json = json.dumps(payload.get("sources") or [], ensure_ascii=False)
        evidence.exclude_recommended = bool(payload.get("exclude_recommended"))
        evidence.validation_errors_json = json.dumps(errors, ensure_ascii=False)
        evidence.prompt_version = PROMPT_VERSION
        self.session.flush()
        return evidence

    def _apply_evidence(self, item: ShortlistItem, evidence: ResearchEvidence) -> None:
        item.ai_status = "accepted"
        item.ai_summary = evidence.summary
        item.ai_sentiment = evidence.sentiment
        item.ai_as_of = evidence.created_at or datetime.utcnow()
        item.evidence_id = evidence.evidence_id
        item.risk_tags_json = evidence.risk_tags_json
        item.excluded_by_ai_risk = bool(
            evidence.exclude_recommended and evidence.confidence >= 0.8 and evidence.relevance >= 0.8
        )

    def _apply_fallback(self, item: ShortlistItem, *, evidence_status: str) -> None:
        latest = self.session.execute(
            select(ResearchEvidence)
            .where(ResearchEvidence.symbol == item.symbol, ResearchEvidence.status == "accepted")
            .order_by(ResearchEvidence.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is None:
            item.ai_status = evidence_status
            return
        self._apply_evidence(item, latest)
        item.ai_status = f"stale_{evidence_status}"
        item.excluded_by_ai_risk = False

    @staticmethod
    def _evidence_payload(evidence: ResearchEvidence) -> dict[str, Any]:
        return {
            "event_type": evidence.event_type,
            "sentiment": evidence.sentiment,
            "relevance": evidence.relevance,
            "confidence": evidence.confidence,
            "impact_horizon": evidence.impact_horizon,
            "summary": evidence.summary,
            "risk_tags": _json_list(evidence.risk_tags_json),
            "sources": _json_list(evidence.sources_json),
            "exclude_recommended": evidence.exclude_recommended,
        }

    @staticmethod
    def _run_summary(run: ShortlistRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "run_date": run.run_date.isoformat(),
            "cutoff_at": run.cutoff_at.isoformat(),
            "status": run.status,
            "ai_status": run.ai_status,
            "candidate_count": int(run.candidate_count or 0),
            "selected_count": int(run.selected_count or 0),
            "degraded": bool(run.degraded),
            "error_message": run.error_message,
            "created_at": run.created_at.isoformat(),
        }

    def _item_payload(self, item: ShortlistItem) -> dict[str, Any]:
        evidence = None
        if item.evidence_id:
            evidence = self.session.execute(
                select(ResearchEvidence).where(ResearchEvidence.evidence_id == item.evidence_id).limit(1)
            ).scalar_one_or_none()
        premarket = self.session.execute(
            select(ResearchEvidence)
            .where(
                ResearchEvidence.run_id == item.run_id,
                ResearchEvidence.symbol == item.symbol,
                ResearchEvidence.research_scope.like("premarket_%"),
                ResearchEvidence.status == "accepted",
            )
            .order_by(ResearchEvidence.cutoff_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        display_evidence = premarket or evidence
        return {
            "rank": int(item.final_rank or item.quant_rank),
            "quant_rank": int(item.quant_rank),
            "symbol": item.symbol,
            "name": item.name,
            "price": float(item.price or 0.0),
            "quant_score": float(item.quant_score or 0.0),
            "reason_tags": _json_list(item.reason_tags_json),
            "blocker_tags": _json_list(item.blocker_tags_json),
            "risk_tags": (
                _json_list(premarket.risk_tags_json) if premarket else _json_list(item.risk_tags_json)
            ),
            "ai_status": "premarket" if premarket else item.ai_status,
            "ai_summary": premarket.summary if premarket else item.ai_summary,
            "ai_sentiment": premarket.sentiment if premarket else item.ai_sentiment,
            "ai_as_of": (
                premarket.cutoff_at.isoformat()
                if premarket
                else (item.ai_as_of.isoformat() if item.ai_as_of else None)
            ),
            "ai_scope": premarket.research_scope if premarket else "daily",
            "excluded_by_ai_risk": bool(item.excluded_by_ai_risk),
            "sources": _json_list(display_evidence.sources_json) if display_evidence else [],
            "outcomes": {
                "5d": item.forward_return_5d,
                "20d": item.forward_return_20d,
                "40d": item.forward_return_40d,
                "60d": item.forward_return_60d,
            },
        }


def _json_list(raw: str) -> list[Any]:
    try:
        value = json.loads(str(raw or "[]"))
        return value if isinstance(value, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
