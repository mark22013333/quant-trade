from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.alerts.radar_engine import generate_daily_radar
from app.data.sync_service import SyncService
from app.security.reporting import csv_join_pipe, csv_join_row, sanitize_report_payload
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
    universe: str = "twse_tpex"
    data_freshness_required: bool = True
    symbols: list[str] | None = None

    def normalized_trade_date(self) -> date:
        return normalize_trade_date(self.trade_date)

    def normalized_target_count(self) -> int:
        return max(10, min(20, int(self.target_count or 15)))

    def normalized_risk_profile(self) -> str:
        return normalize_risk_profile(self.risk_profile)

    def normalized_symbols(self) -> list[str] | None:
        return normalize_symbol_list(self.symbols)


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
            universe_mode = request.universe
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
                "symbols": len(symbols),
                "universe": universe_mode,
                "name_sync_attempted": name_sync_attempted,
                "missing_names_before": missing_names_before,
                "missing_names_after": missing_names_after,
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
            include_broker_agg=True,
            include_disposition=True,
        )
        if bool(sync_result.get("partial_failure")):
            degrade_reasons.append("sync_partial_failure")
        _mark_step(
            "sync_market_data",
            started,
            {
                "rows_upserted_total": int(sync_result.get("rows_upserted_total", 0) or 0),
                "partial_failure": bool(sync_result.get("partial_failure")),
            },
        )

        _progress(62, "計算雷達分數與進場標籤", "score daily radar")
        started = datetime.now()
        radar_items = generate_daily_radar(
            repo=self.repo,
            symbols=symbols,
            trade_date=trade_date,
            target_count=target_count,
            data_freshness_required=bool(request.data_freshness_required),
            risk_profile=risk_profile,
        )
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
                    "risk_profile": risk_profile,
                    "degraded": degraded,
                    "degrade_reasons": degrade_reasons,
                    "label_counts": label_counts,
                },
                ensure_ascii=False,
            ),
        )

        _progress(96, "每日雷達完成", f"items={len(radar_items)}")
        return {
            "run_id": run_id,
            "trade_date": trade_date.isoformat(),
            "target_count": target_count,
            "risk_profile": risk_profile,
            "universe": universe_mode,
            "symbols": symbols,
            "items": radar_items,
            "label_counts": label_counts,
            "degraded": degraded,
            "degrade_reasons": degrade_reasons,
            "step_metrics": step_metrics,
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

    def _export(self, run_id: str, trade_date: date, payload: list[dict]) -> dict:
        safe_payload = sanitize_report_payload(payload)
        json_name = f"daily_radar_{run_id}.json"
        csv_name = f"daily_radar_{run_id}.csv"
        json_path = self.reports_dir / json_name
        csv_path = self.reports_dir / csv_name
        json_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "trade_date": trade_date.isoformat(),
                    "count": len(safe_payload),
                    "items": safe_payload,
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
            "entry_score",
            "risk_score",
            "action_label",
            "candidate_type",
            "last_bar_date",
            "data_quality",
            "reason_tags",
            "blocker_tags",
        ]
        lines = [csv_join_row(header)]
        for row in safe_payload:
            values = [
                row.get("rank", ""),
                row.get("symbol", ""),
                row.get("name", ""),
                row.get("price", ""),
                row.get("entry_score", ""),
                row.get("risk_score", ""),
                row.get("action_label", ""),
                row.get("candidate_type", ""),
                row.get("last_bar_date", ""),
                row.get("data_quality", ""),
                csv_join_pipe(row.get("reason_tags", [])),
                csv_join_pipe(row.get("blocker_tags", [])),
            ]
            lines.append(csv_join_row(values))
        csv_path.write_text("\n".join(lines), encoding="utf-8")
        return {
            "files": {"json": json_name, "csv": csv_name},
            "urls": {"json": f"/reports/{json_name}", "csv": f"/reports/{csv_name}"},
        }
