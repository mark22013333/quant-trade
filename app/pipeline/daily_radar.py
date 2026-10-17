from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.alerts.radar_engine import generate_daily_radar
from app.data.sync_service import SyncService
from app.reports.daily_radar_export import export_daily_radar_files
from app.services.daily_radar_risk_quality import enrich_risk_quality
from app.services.market_context import market_regime_advice
from app.services.universe import (
    normalize_risk_profile,
    normalize_symbol_list,
    normalize_trade_date,
    resolve_incremental_start,
    resolve_tw_universe,
)


@dataclass
class DailyRadarRequest:
    trade_date: date | str | None = None
    target_count: int = 15
    risk_profile: str = "balanced"
    threshold_offset: float = 0.0
    universe: str = "curated_watchlist"
    data_freshness_required: bool = True
    include_attention: bool = True
    include_extended_data: bool = False
    prefer_official_bulk: bool = True
    symbols: list[str] | None = None
    threshold_adoption: dict[str, Any] | None = None

    def normalized_trade_date(self) -> date:
        return normalize_trade_date(self.trade_date)

    def normalized_target_count(self) -> int:
        return max(10, min(30, int(self.target_count or 15)))

    def normalized_risk_profile(self) -> str:
        return normalize_risk_profile(self.risk_profile)

    def normalized_threshold_offset(self) -> float:
        return max(-10.0, min(10.0, float(self.threshold_offset or 0.0)))

    def normalized_symbols(self) -> list[str] | None:
        return normalize_symbol_list(self.symbols)

    def normalized_threshold_adoption(self) -> dict[str, Any]:
        return _compact_threshold_adoption(self.threshold_adoption)


class DailyRadarService:
    def __init__(self, repo, sync_service: SyncService):
        self.repo = repo
        self.sync_service = sync_service
        self.reports_dir = Path(__file__).resolve().parents[2] / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        request: DailyRadarRequest,
        progress_hook: Callable[[int, str, str | None], None] | None = None,
    ) -> dict:
        trade_date = request.normalized_trade_date()
        target_count = request.normalized_target_count()
        risk_profile = request.normalized_risk_profile()
        threshold_offset = request.normalized_threshold_offset()
        threshold_adoption = request.normalized_threshold_adoption()
        run_id = f"radar_{trade_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        step_metrics: dict[str, dict] = {}
        degrade_reasons: list[str] = []

        def _progress(progress: int, message: str, append_log: str | None = None) -> None:
            if progress_hook is None:
                return
            progress_hook(int(progress), str(message), append_log)

        def _mark_step(name: str, started_at: datetime, extra: dict) -> None:
            step_metrics[name] = {
                "duration_sec": max((datetime.now() - started_at).total_seconds(), 0.0),
                **extra,
            }

        _progress(8, "初始化每日雷達", "init daily radar")
        started = datetime.now()
        custom_symbols = request.normalized_symbols()
        if custom_symbols:
            symbols = custom_symbols
            universe_mode = "custom"
        else:
            symbols = self._resolve_universe(request.universe)
            universe_mode = request.universe or "curated_watchlist"
        if not symbols:
            symbols = self.sync_service._resolve_symbols(None)  # noqa: SLF001
            degrade_reasons.append("universe_fallback_to_0050")

        def _count_missing_names(items: list[str]) -> int:
            getter = getattr(self.repo, "get_instrument_name", None)
            if not callable(getter):
                return 0
            return sum(1 for symbol in items if not str(getter(symbol) or "").strip())

        def _industry_coverage(items: list[str]) -> dict:
            method = getattr(self.repo, "get_instrument_industry_coverage", None)
            if not callable(method):
                return {}
            try:
                payload = method(symbols=items)
            except TypeError:
                payload = method()
            return payload if isinstance(payload, dict) else {}

        missing_names_before = _count_missing_names(symbols)
        industry_coverage_before = _industry_coverage(symbols)
        missing_industries_before = int(industry_coverage_before.get("missing_industry_count") or 0)
        name_sync_attempted = False
        if (missing_names_before > 0 or missing_industries_before > 0) and hasattr(self.sync_service, "sync_twse_tpex_universe"):
            name_sync_attempted = True
            try:
                self.sync_service.sync_twse_tpex_universe()
            except Exception:
                degrade_reasons.append("instrument_metadata_sync_failed")
        missing_names_after = _count_missing_names(symbols)
        industry_coverage_after = _industry_coverage(symbols)
        missing_industries_after = int(industry_coverage_after.get("missing_industry_count") or 0)
        if missing_names_after > 0:
            degrade_reasons.append("instrument_name_missing")
        if missing_industries_after > 0:
            degrade_reasons.append("instrument_industry_missing")
        _mark_step(
            "resolve_universe",
            started,
            {
                "symbols": len(symbols),
                "universe": universe_mode,
                "name_sync_attempted": name_sync_attempted,
                "missing_names_before": missing_names_before,
                "missing_names_after": missing_names_after,
                "missing_industries_before": missing_industries_before,
                "missing_industries_after": missing_industries_after,
                "industry_coverage_pct": industry_coverage_after.get("coverage_pct"),
            },
        )

        _progress(28, "同步雷達所需市場資料", "sync market data for radar")
        started = datetime.now()
        start_date = self._resolve_sync_start(symbols=symbols, trade_date=trade_date)
        sync_result = self.sync_service.sync_market_bundle(
            start_date=start_date,
            end_date=trade_date,
            symbols=symbols,
            include_bars=True,
            include_institutional=True,
            include_broker_agg=bool(request.include_extended_data),
            include_disposition=bool(request.include_extended_data),
            include_attention=bool(request.include_attention),
            include_fundamentals=bool(request.include_extended_data),
            include_news=bool(request.include_extended_data),
            include_industry_indices=True,
            prefer_official_bulk=bool(request.prefer_official_bulk),
        )
        if not bool(request.include_attention):
            degrade_reasons.append("attention_sync_skipped")
        if bool(sync_result.get("partial_failure")):
            degrade_reasons.append("sync_partial_failure")
        _mark_step(
            "sync_market_data",
            started,
            {
                "rows_upserted_total": int(sync_result.get("rows_upserted_total", 0) or 0),
                "partial_failure": bool(sync_result.get("partial_failure")),
                "failed_count_total": int(sync_result.get("failed_count_total", 0) or 0),
                "prefer_official_bulk": bool(request.prefer_official_bulk),
            },
        )
        flush = getattr(getattr(self.repo, "session", None), "flush", None)
        if callable(flush):
            flush()
        market_regime = market_regime_advice(
            self.repo,
            symbols=symbols,
            trade_date=trade_date,
            requested_risk_profile=risk_profile,
            requested_threshold_offset=threshold_offset,
        )
        effective_risk_profile = str(market_regime.get("effective_risk_profile") or risk_profile)
        effective_threshold_offset = float(market_regime.get("effective_threshold_offset", threshold_offset) or 0.0)

        _progress(62, "計算雷達分數與進場標籤", "score daily radar")
        started = datetime.now()
        radar_items = generate_daily_radar(
            repo=self.repo,
            symbols=symbols,
            trade_date=trade_date,
            target_count=target_count,
            data_freshness_required=bool(request.data_freshness_required),
            risk_profile=effective_risk_profile,
            threshold_offset=effective_threshold_offset,
        )
        radar_items = enrich_risk_quality(radar_items if isinstance(radar_items, list) else [])
        label_counts = {"ENTRY_READY": 0, "WATCH_WAIT_TRIGGER": 0, "NOT_RECOMMENDED": 0}
        for item in radar_items:
            action_label = str(item.get("action_label", "NOT_RECOMMENDED"))
            label_counts[action_label] = int(label_counts.get(action_label, 0)) + 1
        _mark_step("score_radar", started, {"items": len(radar_items), "label_counts": label_counts})

        _progress(82, "寫入快照與觀察池", "persist radar and watch pool")
        self.repo.upsert_daily_radar_snapshots(run_id=run_id, run_date=trade_date, records=radar_items)
        self.repo.upsert_watch_pool(run_id=run_id, run_date=trade_date, records=radar_items)
        export = self._export(run_id=run_id, trade_date=trade_date, payload=radar_items)

        degraded = len(degrade_reasons) > 0
        self.repo.add_sync_job(
            "daily_radar",
            status="partial" if degraded else "ok",
            rows_upserted=len(radar_items),
            error_msg=json.dumps(
                {
                    "run_id": run_id,
                    "trade_date": trade_date.isoformat(),
                    "target_count": target_count,
                    "risk_profile": effective_risk_profile,
                    "threshold_offset": effective_threshold_offset,
                    "requested_risk_profile": risk_profile,
                    "requested_threshold_offset": threshold_offset,
                    "market_regime": market_regime,
                    "threshold_adoption": threshold_adoption,
                    "degraded": degraded,
                    "degrade_reasons": degrade_reasons,
                    "label_counts": label_counts,
                    "partial_failure": bool(sync_result.get("partial_failure")),
                    "failed_count_total": int(sync_result.get("failed_count_total", 0) or 0),
                    "failed_samples": sync_result.get("failed_samples", []),
                    "sync_quality": sync_result.get("sync_quality", {}),
                    "prefer_official_bulk": bool(request.prefer_official_bulk),
                },
                ensure_ascii=False,
            ),
        )

        _progress(96, "每日雷達完成", f"items={len(radar_items)}")
        return {
            "run_id": run_id,
            "trade_date": trade_date.isoformat(),
            "target_count": target_count,
            "risk_profile": effective_risk_profile,
            "threshold_offset": effective_threshold_offset,
            "requested_risk_profile": risk_profile,
            "requested_threshold_offset": threshold_offset,
            "market_regime": market_regime,
            "threshold_adoption": threshold_adoption,
            "universe": universe_mode,
            "symbols": symbols,
            "items": radar_items,
            "label_counts": label_counts,
            "degraded": degraded,
            "degrade_reasons": degrade_reasons,
            "step_metrics": step_metrics,
            "sync_result": sync_result,
            "export": export,
        }

    def _resolve_universe(self, universe: str) -> list[str]:
        return resolve_tw_universe(self.repo, self.sync_service, universe)

    def _resolve_sync_start(self, symbols: list[str], trade_date: date) -> date:
        return resolve_incremental_start(
            self.repo,
            symbols=symbols,
            trade_date=trade_date,
            fresh_coverage_threshold=0.7,
            fresh_window_days=30,
            fresh_lookback_days=30,
            cold_lookback_days=180,
        )

    def _repo_summary(self, method_name: str, **kwargs: Any) -> dict:
        method = getattr(self.repo, method_name, None)
        if callable(method):
            try:
                result = method(**kwargs)
                return result if isinstance(result, dict) else {}
            except Exception:
                return {"status": "error", "reason": f"{method_name}_unavailable"}
        return {}

    def _export(self, run_id: str, trade_date: date, payload: list[dict]) -> dict:
        allocation_plan = self._repo_summary(
            "get_daily_radar_allocation_plan",
            calibration_profile="standard",
        )
        allocation_outcomes = self._repo_summary("get_daily_radar_allocation_outcome_summary")
        allocation_calibration = self._repo_summary(
            "get_daily_radar_allocation_calibration",
            calibration_profile="standard",
        )
        trading_feedback = self._repo_summary(
            "get_daily_radar_trading_feedback_summary",
            count=max(1, len(payload)),
            limit=100,
        )
        data_health_overview = self._repo_summary(
            "get_data_health_overview",
            as_of=trade_date,
            freshness_days=3,
        )
        return export_daily_radar_files(
            payload,
            output_dir=self.reports_dir,
            report_date=trade_date,
            run_id=run_id,
            allocation_plan=allocation_plan,
            allocation_outcomes=allocation_outcomes,
            allocation_calibration=allocation_calibration,
            trading_feedback=trading_feedback,
            data_health_overview=data_health_overview,
        )


def _compact_threshold_adoption(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {}

    def text(key: str, limit: int = 160) -> str:
        return " ".join(str(payload.get(key) or "").split())[:limit]

    def number(key: str) -> float | None:
        try:
            value = float(payload.get(key))
        except Exception:
            return None
        return value if value == value else None

    top_symbols = payload.get("top_symbols")
    if isinstance(top_symbols, str):
        raw_symbols = top_symbols.replace("\n", ",").split(",")
    elif isinstance(top_symbols, (list, tuple)):
        raw_symbols = top_symbols
    else:
        raw_symbols = []
    clean_symbols: list[str] = []
    for raw in raw_symbols:
        symbol = str(raw or "").strip().upper()
        if symbol and symbol not in clean_symbols:
            clean_symbols.append(symbol)

    out: dict[str, Any] = {
        "source": text("source", 80) or "threshold_backtest",
        "source_run_id": text("source_run_id", 80),
        "action": text("action", 40),
        "label": text("label", 80),
        "reason": text("reason", 240),
        "pool_key": text("pool_key", 100),
        "pool_label": text("pool_label", 120),
        "pool_type": text("pool_type", 40),
        "profile": text("profile", 40),
        "profile_label": text("profile_label", 80),
        "top_symbols": clean_symbols[:12],
    }
    for key in (
        "candidate_rank",
        "entry_offset",
        "threshold_offset",
        "decision_score",
        "candidate_score",
        "robustness_score",
        "evaluated_count",
        "evaluated_run_count",
    ):
        value = number(key)
        if value is not None:
            out[key] = value
    return {key: value for key, value in out.items() if value not in ("", [], None)}
