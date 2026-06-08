from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.alerts.candidate_engine import generate_candidate_suggestions
from app.data.sync_service import SyncService
from app.features.snapshot_builder import rebuild_feature_snapshots
from app.security.reporting import csv_join_pipe, csv_join_row, sanitize_report_payload
from app.services.universe import (
    normalize_risk_profile,
    normalize_symbol_list,
    normalize_trade_date,
    resolve_incremental_start,
    resolve_tw_universe,
)


@dataclass
class OneClickPipelineRequest:
    trade_date: date | str | None = None
    target_count: int = 8
    risk_profile: str = "balanced"
    universe: str = "twse_tpex"
    data_freshness_required: bool = True
    symbols: list[str] | None = None

    def normalized_trade_date(self) -> date:
        return normalize_trade_date(self.trade_date)

    def normalized_target_count(self) -> int:
        return max(5, min(10, int(self.target_count or 8)))

    def normalized_risk_profile(self) -> str:
        return normalize_risk_profile(self.risk_profile)

    def normalized_symbols(self) -> list[str] | None:
        return normalize_symbol_list(self.symbols)


class InvestPipelineService:
    def __init__(self, repo, sync_service: SyncService):
        self.repo = repo
        self.sync_service = sync_service
        self.reports_dir = Path(__file__).resolve().parents[2] / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        request: OneClickPipelineRequest,
        progress_hook: Callable[[int, str, str | None], None] | None = None,
    ) -> dict:
        trade_date = request.normalized_trade_date()
        target_count = request.normalized_target_count()
        risk_profile = request.normalized_risk_profile()
        run_id = f"run_{trade_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        step_metrics: dict[str, Any] = {}
        degrade_reasons: list[str] = []

        def _progress(progress: int, message: str, append_log: str | None = None) -> None:
            if progress_hook is None:
                return
            progress_hook(int(progress), str(message), append_log)

        def _mark_step(name: str, started_at: datetime, extra: dict[str, Any]) -> None:
            step_metrics[name] = {
                "duration_sec": max((datetime.now() - started_at).total_seconds(), 0.0),
                **extra,
            }

        # Step 0: resolve universe
        _progress(12, "Step 1/4：解析股票池（上市+上櫃）", "resolve universe")
        started = datetime.now()
        custom_symbols = request.normalized_symbols()
        if custom_symbols:
            symbols = custom_symbols
        else:
            symbols = self._resolve_universe(request.universe)
        if not symbols:
            symbols = self.sync_service._resolve_symbols(None)  # noqa: SLF001
            degrade_reasons.append("universe_fallback_to_0050")

        def _count_missing_names(items: list[str]) -> int:
            getter = getattr(self.repo, "get_instrument_name", None)
            if not callable(getter):
                return 0
            return sum(1 for symbol in items if not str(getter(symbol) or "").strip())

        missing_names_before = _count_missing_names(symbols)
        name_sync_attempted = False
        if missing_names_before > 0 and hasattr(self.sync_service, "sync_twse_tpex_universe"):
            name_sync_attempted = True
            try:
                self.sync_service.sync_twse_tpex_universe()
            except Exception:
                degrade_reasons.append("instrument_name_sync_failed")
        missing_names_after = _count_missing_names(symbols)
        if missing_names_after > 0:
            degrade_reasons.append("instrument_name_missing")
        _mark_step(
            "resolve_universe",
            started,
            {
                "universe": "custom" if custom_symbols else request.universe,
                "symbols": len(symbols),
                "name_sync_attempted": name_sync_attempted,
                "missing_names_before": missing_names_before,
                "missing_names_after": missing_names_after,
            },
        )

        # Step 1: sync core bundle
        _progress(30, "Step 1/4：同步核心市場資料", f"sync core {len(symbols)} symbols")
        started = datetime.now()
        start_date = self._resolve_incremental_start(symbols=symbols, trade_date=trade_date)
        core_sync = self.sync_service.sync_market_bundle(
            start_date=start_date,
            end_date=trade_date,
            symbols=symbols,
            include_bars=True,
            include_institutional=True,
            include_broker_agg=False,
            include_disposition=True,
        )
        if bool(core_sync.get("partial_failure")):
            degrade_reasons.append("core_sync_partial_failure")
        _mark_step(
            "sync_core",
            started,
            {
                "start_date": start_date.isoformat(),
                "end_date": trade_date.isoformat(),
                "rows_upserted_total": int(core_sync.get("rows_upserted_total", 0) or 0),
                "partial_failure": bool(core_sync.get("partial_failure")),
            },
        )

        # Step 2: sync extended chip bundle
        _progress(48, "Step 2/4：同步擴展籌碼資料", "sync extended chip bundle")
        started = datetime.now()
        extended_sync = self.sync_service.sync_extended_chip_bundle(
            start_date=start_date,
            end_date=trade_date,
            symbols=symbols,
        )
        if bool(extended_sync.get("partial_failure")):
            degrade_reasons.append("extended_chip_partial_failure")
        if int(extended_sync.get("degraded_count", 0) or 0) > 0:
            degrade_reasons.append("extended_chip_degraded")
        _mark_step(
            "sync_extended_chip",
            started,
            {
                "rows_upserted_total": int(extended_sync.get("rows_upserted_total", 0) or 0),
                "partial_failure": bool(extended_sync.get("partial_failure")),
                "degraded_count": int(extended_sync.get("degraded_count", 0) or 0),
            },
        )

        # Step 3: rebuild features
        _progress(66, "Step 3/4：重建特徵快照", "rebuild feature snapshots")
        started = datetime.now()
        rebuild = rebuild_feature_snapshots(
            repo=self.repo,
            start_date=start_date,
            end_date=trade_date,
            symbols=symbols,
        )
        if int(rebuild.get("failed_count", 0) or 0) > 0:
            degrade_reasons.append("feature_rebuild_partial_failure")
        _mark_step(
            "rebuild_features",
            started,
            {
                "rows_upserted": int(rebuild.get("rows_upserted", 0) or 0),
                "failed_count": int(rebuild.get("failed_count", 0) or 0),
                "symbols_processed": int(rebuild.get("symbols_processed", 0) or 0),
            },
        )

        # Step 4: candidate scoring
        _progress(82, "Step 4/4：評分排序並輸出候選", "score candidates")
        started = datetime.now()
        candidates, relaxed_applied = generate_candidate_suggestions(
            repo=self.repo,
            symbols=symbols,
            trade_date=trade_date,
            target_count=target_count,
            data_freshness_required=bool(request.data_freshness_required),
            risk_profile=risk_profile,
        )
        if relaxed_applied:
            degrade_reasons.append("relaxed_trigger_applied")
        insufficient_candidates = len(candidates) < 5
        if insufficient_candidates:
            degrade_reasons.append("insufficient_candidates")
        _mark_step(
            "score_candidates",
            started,
            {
                "candidates": len(candidates),
                "target_count": target_count,
                "relaxed_applied": bool(relaxed_applied),
            },
        )

        # Persist snapshots
        _progress(92, "保存候選快照與匯出檔案", "persist candidate snapshots")
        self.repo.upsert_candidate_snapshots(run_id=run_id, run_date=trade_date, records=candidates)
        export = self._export_candidates(run_id=run_id, trade_date=trade_date, payload=candidates)
        degraded = len(degrade_reasons) > 0
        status = "partial" if degraded else "ok"
        self.repo.add_sync_job(
            "invest_pipeline",
            status=status,
            rows_upserted=len(candidates),
            error_msg=json.dumps(
                {
                    "run_id": run_id,
                    "trade_date": trade_date.isoformat(),
                    "target_count": target_count,
                    "risk_profile": risk_profile,
                    "degraded": degraded,
                    "insufficient_candidates": insufficient_candidates,
                    "degrade_reasons": degrade_reasons,
                },
                ensure_ascii=False,
            ),
        )
        _progress(98, "一鍵流程收尾完成", f"candidates={len(candidates)}")

        return {
            "run_id": run_id,
            "trade_date": trade_date.isoformat(),
            "target_count": target_count,
            "risk_profile": risk_profile,
            "universe": "custom" if custom_symbols else "twse_tpex",
            "symbols": symbols,
            "candidates": candidates,
            "insufficient_candidates": insufficient_candidates,
            "degraded": degraded,
            "degrade_reasons": degrade_reasons,
            "step_metrics": step_metrics,
            "export": export,
        }

    def _resolve_universe(self, universe: str) -> list[str]:
        return resolve_tw_universe(self.repo, self.sync_service, universe)

    def _resolve_incremental_start(self, symbols: list[str], trade_date: date) -> date:
        return resolve_incremental_start(
            self.repo,
            symbols=symbols,
            trade_date=trade_date,
            fresh_coverage_threshold=0.6,
            fresh_window_days=30,
            fresh_lookback_days=7,
            cold_lookback_days=120,
        )

    def _export_candidates(self, run_id: str, trade_date: date, payload: list[dict]) -> dict:
        safe_payload = sanitize_report_payload(payload)
        json_name = f"invest_candidates_{run_id}.json"
        csv_name = f"invest_candidates_{run_id}.csv"
        json_path = self.reports_dir / json_name
        csv_path = self.reports_dir / csv_name
        json_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "trade_date": trade_date.isoformat(),
                    "count": len(safe_payload),
                    "candidates": safe_payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        header = [
            "rank",
            "symbol",
            "name",
            "price",
            "score",
            "risk_score",
            "candidate_type",
            "last_bar_date",
            "data_quality",
            "reason_codes",
            "risk_flags",
        ]
        lines = [csv_join_row(header)]
        for row in safe_payload:
            values = [
                row.get("rank", ""),
                row.get("symbol", ""),
                row.get("name", ""),
                row.get("price", ""),
                row.get("score", ""),
                row.get("risk_score", ""),
                row.get("candidate_type", ""),
                row.get("last_bar_date", ""),
                row.get("data_quality", ""),
                csv_join_pipe(row.get("reason_codes", [])),
                csv_join_pipe(row.get("risk_flags", [])),
            ]
            lines.append(csv_join_row(values))
        csv_path.write_text("\n".join(lines), encoding="utf-8")
        return {
            "files": {
                "json": json_name,
                "csv": csv_name,
            },
            "urls": {
                "json": f"/reports/{json_name}",
                "csv": f"/reports/{csv_name}",
            },
        }
