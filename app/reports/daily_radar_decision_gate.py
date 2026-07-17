from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.reports.daily_radar_health import DailyRadarDeploymentHealthService


STATUS_RANK = {"ok": 0, "watch": 1, "warn": 1, "danger": 2}


def _status_max(*statuses: str) -> str:
    best = "ok"
    best_rank = 0
    for status in statuses:
        key = str(status or "ok").lower()
        rank = STATUS_RANK.get(key, 0)
        if rank > best_rank:
            best = "warn" if key == "watch" else key
            best_rank = rank
    return best


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number != number:
            return default
        return number
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _pct(value: Any) -> float | None:
    if value is None:
        return None
    number = _safe_float(value, -1.0)
    if number < 0:
        return None
    if number <= 1:
        number *= 100
    return round(number, 1)


def _call_data_health_overview(repo: Any, *, as_of: date, freshness_days: int) -> dict[str, Any]:
    method = getattr(repo, "get_data_health_overview", None)
    if not callable(method):
        return {}
    try:
        payload = method(as_of=as_of, freshness_days=freshness_days)
    except TypeError:
        payload = method()
    return payload if isinstance(payload, dict) else {}


def _call_quality_summary(repo: Any, *, count: int) -> dict[str, Any]:
    method = getattr(repo, "get_daily_radar_quality_summary", None)
    if not callable(method):
        return {}
    try:
        payload = method(count=count)
    except TypeError:
        payload = method()
    return payload if isinstance(payload, dict) else {}


def _dataset_map(data_health: dict[str, Any]) -> dict[str, dict[str, Any]]:
    coverage = data_health.get("market_data_coverage")
    datasets = coverage.get("datasets") if isinstance(coverage, dict) else []
    if not isinstance(datasets, list):
        return {}
    return {
        str(row.get("key") or ""): row
        for row in datasets
        if isinstance(row, dict) and str(row.get("key") or "")
    }


def _coverage_pct(row: dict[str, Any] | None) -> float | None:
    if not isinstance(row, dict):
        return None
    if row.get("coverage_pct") is not None:
        return _pct(row.get("coverage_pct"))
    return _pct(row.get("coverage"))


def _missing_symbols(row: dict[str, Any] | None, limit: int = 8) -> list[str]:
    symbols = row.get("missing_symbols") if isinstance(row, dict) else []
    if not isinstance(symbols, list):
        return []
    return [str(symbol).strip() for symbol in symbols if str(symbol).strip()][:limit]


def _metric_value(value: Any, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.0f}{suffix}"
    return f"{value}{suffix}"


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class DailyRadarDecisionGateService:
    """Combine data readiness, radar freshness, and screener quality into one daily gate."""

    def __init__(
        self,
        *,
        freshness_days: int = 3,
        max_age_days: int = 3,
        warn_age_days: int = 1,
        min_rows: int = 5,
        min_entry_ready: int = 1,
        min_universe_coverage_pct: float = 50.0,
        min_core_coverage_pct: float = 70.0,
        min_quality_score: int = 60,
    ) -> None:
        self.freshness_days = max(1, int(freshness_days))
        self.max_age_days = max(0, int(max_age_days))
        self.warn_age_days = max(0, int(warn_age_days))
        self.min_rows = max(1, int(min_rows))
        self.min_entry_ready = max(0, int(min_entry_ready))
        self.min_universe_coverage_pct = max(0.0, float(min_universe_coverage_pct))
        self.min_core_coverage_pct = max(0.0, float(min_core_coverage_pct))
        self.min_quality_score = max(0, int(min_quality_score))

    def evaluate(self, repo: Any, *, as_of: date | None = None) -> dict[str, Any]:
        today = as_of or datetime.now().date()
        data_health = _call_data_health_overview(repo, as_of=today, freshness_days=self.freshness_days)
        radar_health = DailyRadarDeploymentHealthService(
            max_age_days=self.max_age_days,
            warn_age_days=self.warn_age_days,
            min_rows=self.min_rows,
            min_entry_ready=self.min_entry_ready,
        ).evaluate(repo, as_of=today)
        quality = _call_quality_summary(repo, count=50)
        return self.evaluate_payloads(
            data_health=data_health,
            radar_health=radar_health,
            quality=quality,
            as_of=today,
        )

    def evaluate_payloads(
        self,
        *,
        data_health: dict[str, Any],
        radar_health: dict[str, Any],
        quality: dict[str, Any],
        as_of: date,
    ) -> dict[str, Any]:
        datasets = _dataset_map(data_health)
        daily_bars = datasets.get("daily_bars", {})
        institutional_chip = datasets.get("institutional_chip", {})
        universe = data_health.get("universe") if isinstance(data_health.get("universe"), dict) else {}
        quality_metrics = quality.get("metrics") if isinstance(quality.get("metrics"), dict) else {}
        data_readiness = quality.get("data_readiness") if isinstance(quality.get("data_readiness"), dict) else {}

        universe_pct = _pct(universe.get("coverage"))
        daily_pct = _coverage_pct(daily_bars)
        chip_pct = _coverage_pct(institutional_chip)
        core_values = [value for value in [daily_pct, chip_pct] if value is not None]
        core_pct = round(min(core_values), 1) if core_values else None
        quality_score = _safe_int(quality.get("quality_score"), 0)
        radar_rows = _safe_int(radar_health.get("row_count"), _safe_int(quality.get("candidate_count"), 0))
        entry_ready = _safe_int(radar_health.get("entry_ready_count"), _safe_int(quality_metrics.get("entry_count"), 0))
        repair_first_count = _safe_int(quality_metrics.get("data_gap_candidate_count"), 0)
        affected_candidates = [
            str(row.get("symbol") or "").strip()
            for row in _list_value(data_readiness.get("affected_candidate_symbols"))
            if isinstance(row, dict) and str(row.get("symbol") or "").strip()
        ]

        data_status = str(data_health.get("status") or "neutral").lower()
        radar_status = str(radar_health.get("status") or "neutral").lower()
        quality_status = str(quality.get("risk_level") or quality.get("status") or "neutral").lower()
        readiness_status = str(data_readiness.get("status") or "neutral").lower()
        latest_sync_job = _dict_value(radar_health.get("latest_sync_job"))
        latest_sync_status = str(latest_sync_job.get("status") or "").lower()
        latest_sync_health = str(latest_sync_job.get("health_status") or "").lower()
        sync_issues = _list_value(radar_health.get("sync_issues"))

        raw_data_coverage_blocked = (
            data_status == "danger"
            or (universe_pct is not None and universe_pct < self.min_universe_coverage_pct)
            or (core_pct is not None and core_pct < self.min_core_coverage_pct)
        )
        radar_blocked = radar_status == "danger"
        quality_blocked = quality_status == "danger" or readiness_status == "danger" or quality_score < 40
        no_entry_ready = entry_ready < self.min_entry_ready
        candidate_scope_ready = (
            radar_rows >= self.min_rows
            and not radar_blocked
            and latest_sync_status == "ok"
            and latest_sync_health in {"", "ok"}
            and not sync_issues
            and repair_first_count <= 0
            and not affected_candidates
            and quality_score >= 40
        )
        data_coverage_warn_only = bool(raw_data_coverage_blocked and candidate_scope_ready)
        data_coverage_blocked = bool(raw_data_coverage_blocked and not data_coverage_warn_only)

        if data_coverage_blocked:
            status = "danger"
            title = "先修資料，再研究標的"
            verdict = "日線、法人或標的池覆蓋不足時，雷達排名只能當修復清單，不適合直接拿來挑股。"
        elif radar_blocked:
            status = "danger"
            title = "雷達部署異常，先重跑"
            verdict = "最新每日雷達不存在、過期或候選數不足；先讓排程恢復，再判讀投資標的。"
        elif quality_blocked:
            status = "danger"
            title = "候選品質偏弱，先看缺口"
            verdict = "品質分數或資料 readiness 太低；先處理候選缺口與阻擋原因。"
        elif no_entry_ready:
            status = "warn"
            title = "今天偏觀察，不急著進場"
            if data_coverage_warn_only:
                verdict = "全市場覆蓋仍需排程補齊，但最新雷達同步正常且候選未命中資料缺口；今天先看觀察線，避免硬湊交易。"
            else:
                verdict = "雷達有產出，但目前沒有可進場標的；可研究觀察線，避免硬湊交易。"
        else:
            status = _status_max(data_status, radar_status, quality_status, readiness_status)
            if data_coverage_warn_only and status == "danger":
                status = "warn"
            title = "可以開始研究候選"
            verdict = "資料與雷達狀態可用，仍需依 Preview、風控與人工 gate 做最後確認。"
            if data_coverage_warn_only:
                title = "可研究，但全市場資料仍要補"
                verdict = "最新雷達候選沒有資料缺口，可先研究；全市場資料覆蓋仍需背景修復，不應擋住今日候選判讀。"
            elif status == "warn":
                title = "可研究，但先看提醒"
                verdict = "候選清單可用，但資料 freshness、分散度或品質警示仍需要盤前確認。"

        evidence = [
            {
                "key": "data_coverage",
                "label": "資料覆蓋",
                "status": (
                    "danger"
                    if data_coverage_blocked
                    else "warn"
                    if data_coverage_warn_only or data_status in {"warn", "partial"}
                    else "ok"
                ),
                "value": _metric_value(core_pct, "%"),
                "detail": (
                    f"標的池 {_metric_value(universe_pct, '%')}；"
                    f"日線 {_metric_value(daily_pct, '%')}；法人 {_metric_value(chip_pct, '%')}"
                    + ("；本輪候選未命中資料缺口" if data_coverage_warn_only else "")
                ),
            },
            {
                "key": "radar_health",
                "label": "雷達部署",
                "status": radar_status if radar_status in {"ok", "warn", "danger"} else "warn",
                "value": f"{entry_ready}/{radar_rows}",
                "detail": f"可進場 / 全部；最新 run {radar_health.get('latest_run_date') or '-'}",
            },
            {
                "key": "quality",
                "label": "候選品質",
                "status": "danger" if quality_blocked else ("warn" if quality_status in {"warn", "watch"} else "ok"),
                "value": f"{quality_score}/100",
                "detail": str(quality.get("verdict") or data_readiness.get("label") or "-"),
            },
        ]

        actions = self._actions(
            data_coverage_blocked=data_coverage_blocked,
            data_coverage_warn_only=data_coverage_warn_only,
            radar_blocked=radar_blocked,
            quality_blocked=quality_blocked,
            no_entry_ready=no_entry_ready,
            repair_first_count=repair_first_count,
            candidate_scope_ready=candidate_scope_ready,
        )
        next_steps = self._next_steps(
            data_coverage_blocked=data_coverage_blocked,
            data_coverage_warn_only=data_coverage_warn_only,
            radar_blocked=radar_blocked,
            quality_blocked=quality_blocked,
            no_entry_ready=no_entry_ready,
        )

        return {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "as_of": as_of.isoformat(),
            "status": status,
            "status_text": {"ok": "可研究", "warn": "先留意", "danger": "先處理"}.get(status, "先留意"),
            "title": title,
            "verdict": verdict,
            "metrics": {
                "universe_coverage_pct": universe_pct,
                "core_coverage_pct": core_pct,
                "daily_bars_coverage_pct": daily_pct,
                "institutional_chip_coverage_pct": chip_pct,
                "quality_score": quality_score,
                "radar_rows": radar_rows,
                "entry_ready_count": entry_ready,
                "repair_first_count": repair_first_count,
                "candidate_scope_ready": candidate_scope_ready,
                "data_coverage_warn_only": data_coverage_warn_only,
                "daily_bars_missing_count": _safe_int(daily_bars.get("missing_count"), 0),
                "institutional_chip_missing_count": _safe_int(institutional_chip.get("missing_count"), 0),
            },
            "thresholds": {
                "min_universe_coverage_pct": self.min_universe_coverage_pct,
                "min_core_coverage_pct": self.min_core_coverage_pct,
                "min_quality_score": self.min_quality_score,
                "min_entry_ready": self.min_entry_ready,
            },
            "evidence": evidence,
            "actions": actions,
            "primary_action": actions[0] if actions else None,
            "next_steps": next_steps,
            "samples": {
                "daily_bars_missing_symbols": _missing_symbols(daily_bars),
                "institutional_chip_missing_symbols": _missing_symbols(institutional_chip),
                "data_gap_candidate_symbols": affected_candidates[:8],
            },
            "source_status": {
                "data_health": data_status,
                "radar_health": radar_status,
                "quality": quality_status,
                "data_readiness": readiness_status,
            },
        }

    def _actions(
        self,
        *,
        data_coverage_blocked: bool,
        data_coverage_warn_only: bool,
        radar_blocked: bool,
        quality_blocked: bool,
        no_entry_ready: bool,
        repair_first_count: int,
        candidate_scope_ready: bool,
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        if data_coverage_blocked or repair_first_count > 0:
            actions.append(
                {
                    "key": "repair_market_data",
                    "label": "先修資料覆蓋",
                    "target": "data_repair_queue",
                    "priority": "primary",
                    "detail": "補日線與法人缺口，修完再重跑每日雷達。",
                }
            )
        if data_coverage_warn_only and repair_first_count <= 0:
            actions.append(
                {
                    "key": "inspect_quality",
                    "label": "先看觀察線",
                    "target": "radar_quality",
                    "priority": "primary",
                    "detail": "本輪候選資料乾淨，先看只觀察、先略過與觸發條件。",
                }
            )
        if radar_blocked or (not data_coverage_blocked and (quality_blocked or (no_entry_ready and not candidate_scope_ready))):
            actions.append(
                {
                    "key": "rerun_daily_radar",
                    "label": "重跑每日雷達",
                    "target": "daily_radar",
                    "priority": "primary" if not actions else "secondary",
                    "detail": "用最新資料重新產生候選與行動線。",
                }
            )
        if data_coverage_warn_only:
            actions.append(
                {
                    "key": "repair_market_data",
                    "label": "背景補全市場資料",
                    "target": "data_repair_queue",
                    "priority": "secondary",
                    "detail": "不阻擋今日候選，但仍應持續補日線與法人覆蓋。",
                }
            )
        if not any(item.get("key") == "inspect_quality" for item in actions):
            actions.append(
                {
                    "key": "inspect_quality",
                    "label": "查看候選品質",
                    "target": "radar_quality",
                    "priority": "secondary",
                    "detail": "看 Top picks、修資料後再看、只觀察與先略過分流。",
                }
            )
        actions.append(
            {
                "key": "open_ai_decision",
                "label": "打開 AI 決策",
                "target": "pipeline",
                "priority": "secondary",
                "detail": "進入完整雷達清單與篩選器。",
            }
        )
        return actions

    def _next_steps(
        self,
        *,
        data_coverage_blocked: bool,
        data_coverage_warn_only: bool,
        radar_blocked: bool,
        quality_blocked: bool,
        no_entry_ready: bool,
    ) -> list[str]:
        if data_coverage_blocked:
            return [
                "先執行資料源修復佇列，優先補日線與法人買賣超。",
                "修復後重跑每日雷達，確認 ENTRY_READY 與候選品質是否回升。",
            ]
        if data_coverage_warn_only and no_entry_ready:
            return [
                "本輪候選未命中資料缺口，先研究觀察線與觸發條件。",
                "全市場覆蓋仍低，保留背景修復佇列，不要讓冷門缺口擋住今日工作流。",
            ]
        if data_coverage_warn_only:
            return [
                "可先研究最新雷達候選；全市場資料覆蓋仍安排背景修復。",
                "若候選品質分流出現修資料後再看，再回到資料源修復佇列。",
            ]
        if radar_blocked:
            return ["先重跑每日雷達或檢查排程健康度，再看候選清單。"]
        if quality_blocked:
            return ["先查看候選品質總覽，把修資料後再看的標的排除或補齊。"]
        if no_entry_ready:
            return ["今天先研究觀察線與觸發條件，不要為了交易而交易。"]
        return ["可進入 AI 決策清單，逐檔做 Preview、風控與人工 gate。"]
