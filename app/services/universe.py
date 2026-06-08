from __future__ import annotations

from datetime import date, datetime, timedelta


def normalize_tw_symbol(raw: str) -> str:
    code = str(raw or "").strip().upper()
    for suffix in (".TW", ".TWO"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    return code


def normalize_symbol_list(symbols: list[str] | None) -> list[str] | None:
    if not symbols:
        return None
    normalized = [normalize_tw_symbol(item) for item in symbols]
    unique = sorted({item for item in normalized if item})
    return unique or None


def normalize_trade_date(value: date | str | None) -> date:
    if value is None:
        return datetime.now().date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return datetime.now().date()
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def normalize_risk_profile(value: str | None) -> str:
    text = str(value or "balanced").strip().lower()
    if text in {"aggressive", "active", "high_beta", "high-beta"}:
        return "aggressive"
    return "balanced"


def resolve_tw_universe(repo, sync_service, universe: str, *, fallback_to_default: bool = True) -> list[str]:
    mode = str(universe or "twse_tpex").strip().lower()
    if mode != "twse_tpex":
        return sync_service._resolve_symbols(None) if fallback_to_default else []  # noqa: SLF001
    symbols = repo.get_symbols_by_markets(["TWSE", "TPEX"])
    if symbols:
        return symbols
    try:
        sync_service.sync_twse_tpex_universe()
    except Exception:
        return []
    return repo.get_symbols_by_markets(["TWSE", "TPEX"])


def resolve_incremental_start(
    repo,
    *,
    symbols: list[str],
    trade_date: date,
    fresh_coverage_threshold: float,
    fresh_window_days: int,
    fresh_lookback_days: int,
    cold_lookback_days: int,
) -> date:
    if not symbols:
        return trade_date - timedelta(days=int(cold_lookback_days))
    recent_threshold = trade_date - timedelta(days=int(fresh_window_days))
    recent_count = repo.count_symbols_with_recent_bars(symbols=symbols, min_date=recent_threshold)
    coverage = recent_count / max(len(symbols), 1)
    if coverage >= float(fresh_coverage_threshold):
        return trade_date - timedelta(days=int(fresh_lookback_days))
    return trade_date - timedelta(days=int(cold_lookback_days))
