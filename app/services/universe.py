from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from app.data.curated_watchlist import load_curated_watchlist_symbols
from app.data.index_constituents import load_index_constituent_symbols
from app.data.symbol_exclusions import load_symbol_exclusions
from app.data.universe_0050 import load_0050_symbols
from app.data.universe_mid100 import load_mid100_symbols


DYNAMIC_UNIVERSE_MARKETS = ["TWSE", "TPEX"]
DYNAMIC_LIQUID_LIMIT = 80
DYNAMIC_BALANCED_LIMIT = 60
DYNAMIC_BALANCED_PER_INDUSTRY = 3
DYNAMIC_LOOKBACK_DAYS = 90
DYNAMIC_MIN_BARS = 5
RESEARCH_READY_LIMIT = 80
RESEARCH_READY_PER_INDUSTRY = 8
RESEARCH_READY_HISTORY_LOOKBACK_DAYS = 240
RESEARCH_READY_MIN_HISTORY_BARS = 60
RESEARCH_READY_CHIP_LOOKBACK_DAYS = 45
RESEARCH_READY_MIN_CHIP_DAYS = 5
RESEARCH_READY_FRESHNESS_DAYS = 5


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
    if text in {"conservative", "defensive", "safe", "low_beta", "low-beta"}:
        return "conservative"
    if text in {"aggressive", "active", "high_beta", "high-beta"}:
        return "aggressive"
    return "balanced"


def _load_no_touch_symbols() -> list[str]:
    try:
        return sorted(load_symbol_exclusions().keys())
    except Exception:
        return []


def _fallback_universe(fallback_to_default: bool) -> list[str]:
    return load_curated_watchlist_symbols() if fallback_to_default else []


def _source_label(source: str) -> str:
    return {
        "curated_watchlist": "精選觀察池",
        "custom": "自訂觀察池",
        "dynamic_liquid": "動態流動性池",
        "dynamic_balanced": "動態族群分散池",
        "dynamic_balanced_liquid_fallback": "動態流動性備援",
        "research_ready": "研究就緒池",
        "research_ready_balanced_fallback": "研究就緒池備援",
        "tw0050_snapshot": "台灣 50 DB snapshot",
        "tw0050_cache": "台灣 50 本機快取",
        "tw_mid100_cache": "中型 100 本機快取",
        "tw_mid100_liquidity": "中型 100 流動性推估",
        "tw_mid100_builtin": "中型 100 內建清單",
        "index_cache": "指數成分股快取",
        "twse_tpex": "上市 + 上櫃 DB universe",
        "fallback_curated": "精選觀察池備援",
        "unknown": "未知來源",
    }.get(source, source)


def _industry_counts(repo: Any, symbols: list[str], rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        industry = str(row.get("industry") or "").strip() or "未分類"
        counter[industry] += 1
    if not counter and hasattr(repo, "get_instrument_industry"):
        for symbol in symbols:
            try:
                industry = str(repo.get_instrument_industry(symbol) or "").strip() or "未分類"
            except Exception:
                industry = "未分類"
            counter[industry] += 1
    return [
        {"industry": industry, "count": int(count)}
        for industry, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:8]
    ]


def _industry_source_counts(rows: list[dict[str, Any]] | None) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        source = str(row.get("industry_source") or "official").strip() or "official"
        counter[source] += 1
    return {key: int(counter[key]) for key in sorted(counter)}


def preview_tw_universe(
    repo: Any,
    sync_service: Any,
    universe: str,
    *,
    symbols: list[str] | None = None,
    trade_date: date | str | None = None,
    freshness_days: int = 3,
    sample_size: int = 12,
) -> dict[str, Any]:
    """Describe the selected universe without mutating market data."""
    mode = str(universe or "curated_watchlist").strip().lower()
    as_of = normalize_trade_date(trade_date)
    freshness_window = max(1, int(freshness_days or 3))
    sample_limit = max(1, int(sample_size or 12))
    no_touch_symbols = _load_no_touch_symbols()
    no_touch_set = set(no_touch_symbols)
    source = "unknown"
    resolved: list[str] = []
    dynamic_rows: list[dict[str, Any]] = []
    fallback_used = False
    messages: list[str] = []

    if mode in {"custom", "manual"}:
        resolved = normalize_symbol_list(symbols) or []
        source = "custom"
        if not resolved:
            messages.append("自訂觀察池尚未輸入代號。")
    elif mode.startswith("index:") or mode.startswith("constituents:"):
        _, _, key = mode.partition(":")
        resolved = load_index_constituent_symbols(key)
        source = "index_cache"
        if not resolved:
            fallback_used = True
            source = "fallback_curated"
            resolved = _fallback_universe(True)
            messages.append(f"`{mode}` 尚無可用成分股快取，暫用精選觀察池備援。")
    elif mode in {"curated", "curated_watchlist", "watchlist"}:
        resolved = load_curated_watchlist_symbols()
        source = "curated_watchlist"
    elif mode in {"dynamic_liquid", "liquid", "recent_liquid", "liquidity"}:
        if hasattr(repo, "get_recent_liquid_symbols"):
            resolved = repo.get_recent_liquid_symbols(
                markets=DYNAMIC_UNIVERSE_MARKETS,
                exclude_symbols=no_touch_symbols,
                limit=DYNAMIC_LIQUID_LIMIT,
                lookback_days=DYNAMIC_LOOKBACK_DAYS,
                min_bars=DYNAMIC_MIN_BARS,
            )
        source = "dynamic_liquid"
        if not resolved:
            fallback_used = True
            source = "fallback_curated"
            resolved = _fallback_universe(True)
            messages.append("動態流動性池目前沒有足夠資料，暫用精選觀察池備援。")
    elif mode in {"dynamic_balanced", "dynamic_industry", "industry_balanced", "balanced_liquid"}:
        if hasattr(repo, "get_recent_liquid_symbols_by_industry"):
            dynamic_rows = repo.get_recent_liquid_symbols_by_industry(
                markets=DYNAMIC_UNIVERSE_MARKETS,
                exclude_symbols=no_touch_symbols,
                limit=DYNAMIC_BALANCED_LIMIT,
                per_industry=DYNAMIC_BALANCED_PER_INDUSTRY,
                lookback_days=DYNAMIC_LOOKBACK_DAYS,
                min_bars=DYNAMIC_MIN_BARS,
            )
            resolved = [
                str(row.get("symbol") or "").strip().upper()
                for row in dynamic_rows
                if isinstance(row, dict) and str(row.get("symbol") or "").strip()
            ]
        source = "dynamic_balanced"
        if not resolved and hasattr(repo, "get_recent_liquid_symbols"):
            resolved = repo.get_recent_liquid_symbols(
                markets=DYNAMIC_UNIVERSE_MARKETS,
                exclude_symbols=no_touch_symbols,
                limit=DYNAMIC_BALANCED_LIMIT,
                lookback_days=DYNAMIC_LOOKBACK_DAYS,
                min_bars=DYNAMIC_MIN_BARS,
            )
            source = "dynamic_balanced_liquid_fallback"
            if resolved:
                messages.append("產業分散資料不足，改用動態流動性備援。")
        if not resolved:
            fallback_used = True
            source = "fallback_curated"
            resolved = _fallback_universe(True)
            messages.append("動態族群分散池目前沒有足夠資料，暫用精選觀察池備援。")
    elif mode in {"research_ready", "research", "quality_universe", "ready_universe"}:
        if hasattr(repo, "get_research_ready_symbols_by_industry"):
            dynamic_rows = repo.get_research_ready_symbols_by_industry(
                markets=DYNAMIC_UNIVERSE_MARKETS,
                exclude_symbols=no_touch_symbols,
                limit=RESEARCH_READY_LIMIT,
                per_industry=RESEARCH_READY_PER_INDUSTRY,
                history_lookback_days=RESEARCH_READY_HISTORY_LOOKBACK_DAYS,
                min_history_bars=RESEARCH_READY_MIN_HISTORY_BARS,
                chip_lookback_days=RESEARCH_READY_CHIP_LOOKBACK_DAYS,
                min_chip_days=RESEARCH_READY_MIN_CHIP_DAYS,
                freshness_days=RESEARCH_READY_FRESHNESS_DAYS,
            )
            resolved = [
                str(row.get("symbol") or "").strip().upper()
                for row in dynamic_rows
                if isinstance(row, dict) and str(row.get("symbol") or "").strip()
            ]
        source = "research_ready"
        if not resolved and hasattr(repo, "get_recent_liquid_symbols_by_industry"):
            dynamic_rows = repo.get_recent_liquid_symbols_by_industry(
                markets=DYNAMIC_UNIVERSE_MARKETS,
                exclude_symbols=no_touch_symbols,
                limit=DYNAMIC_BALANCED_LIMIT,
                per_industry=DYNAMIC_BALANCED_PER_INDUSTRY,
                lookback_days=DYNAMIC_LOOKBACK_DAYS,
                min_bars=DYNAMIC_MIN_BARS,
            )
            resolved = [
                str(row.get("symbol") or "").strip().upper()
                for row in dynamic_rows
                if isinstance(row, dict) and str(row.get("symbol") or "").strip()
            ]
            source = "research_ready_balanced_fallback"
            if resolved:
                fallback_used = True
                messages.append("嚴格研究門檻目前沒有足夠標的，暫用動態族群分散池備援。")
        if not resolved:
            fallback_used = True
            source = "fallback_curated"
            resolved = _fallback_universe(True)
            messages.append("研究就緒池目前沒有足夠資料，暫用精選觀察池備援。")
    elif mode in {"tw0050", "0050", "taiwan50", "taiwan_50"}:
        resolved = repo.get_latest_0050_symbols() or []
        source = "tw0050_snapshot" if resolved else "tw0050_cache"
        if not resolved:
            resolved = load_0050_symbols()
    elif mode in {"twmid100", "tw_mid100", "mid100", "taiwan_mid100", "taiwan_mid_cap_100"}:
        resolved = load_mid100_symbols(fallback=False)
        source = "tw_mid100_cache" if resolved else "tw_mid100_liquidity"
        if not resolved and hasattr(repo, "get_recent_liquid_symbols"):
            exclude_symbols = repo.get_latest_0050_symbols() or load_0050_symbols()
            resolved = repo.get_recent_liquid_symbols(
                markets=["TWSE", "TPEX"],
                exclude_symbols=exclude_symbols,
                limit=100,
                lookback_days=90,
                min_bars=5,
            )
        if not resolved:
            resolved = load_mid100_symbols()
            source = "tw_mid100_builtin"
            fallback_used = True
            messages.append("中型 100 快取與流動性推估不足，暫用內建清單。")
    elif mode == "twse_tpex":
        resolved = repo.get_symbols_by_markets(["TWSE", "TPEX"]) if hasattr(repo, "get_symbols_by_markets") else []
        source = "twse_tpex"
        if not resolved:
            messages.append("上市 + 上櫃 universe 尚未同步，請先執行市場池同步。")
    else:
        resolved = sync_service._resolve_symbols(symbols) if hasattr(sync_service, "_resolve_symbols") else []
        source = "unknown"
        if resolved:
            messages.append(f"未知市場池 `{mode}`，使用同步服務預設解析結果。")

    resolved = normalize_symbol_list(resolved) or []
    no_touch_overlap = sorted(symbol for symbol in resolved if symbol in no_touch_set)
    recent_count: int | None = None
    freshness_coverage: float | None = None
    if resolved and hasattr(repo, "count_symbols_with_recent_bars"):
        try:
            recent_count = int(
                repo.count_symbols_with_recent_bars(
                    symbols=resolved,
                    min_date=as_of - timedelta(days=freshness_window),
                )
            )
            freshness_coverage = recent_count / max(len(resolved), 1)
        except Exception:
            recent_count = None
            freshness_coverage = None

    dynamic_sources = {"dynamic_balanced", "research_ready", "research_ready_balanced_fallback"}
    industries = _industry_counts(repo, resolved, dynamic_rows if source in dynamic_sources else None)
    industry_sources = _industry_source_counts(dynamic_rows if source in dynamic_sources else None)
    proxy_count = int(industry_sources.get("symbol_proxy", 0))
    proxy_ratio = proxy_count / max(len(resolved), 1) if resolved else 0.0
    top_industry_ratio = (industries[0]["count"] / len(resolved)) if industries and resolved else 0.0
    if no_touch_overlap:
        messages.append(f"{len(no_touch_overlap)} 檔命中不碰清單，實際雷達可能會降級或排除。")
    if proxy_ratio >= 0.5:
        messages.append("正式產業分類不足，目前主要用代號前綴 proxy 分群。")
    if freshness_coverage is not None and freshness_coverage < 0.7:
        messages.append("近期日線覆蓋偏低，建議先修市場資料再跑雷達。")
    if top_industry_ratio >= 0.4 and len(resolved) >= 10:
        messages.append("產業集中度偏高，建議改用動態族群分散池或擴大觀察池。")

    if not resolved:
        status = "danger"
        verdict = "市場池沒有可用代號"
        primary_action = "先同步或匯入市場池。"
    elif freshness_coverage is not None and freshness_coverage < 0.5:
        status = "danger"
        verdict = "資料覆蓋不足"
        primary_action = "先修市場資料覆蓋缺口，再重跑每日雷達。"
    elif (
        fallback_used
        or source in {"dynamic_balanced_liquid_fallback", "research_ready_balanced_fallback", "tw_mid100_builtin", "unknown"}
        or no_touch_overlap
        or proxy_ratio >= 0.5
        or top_industry_ratio >= 0.4
        or (freshness_coverage is not None and freshness_coverage < 0.8)
    ):
        status = "warn"
        verdict = "可用但需留意"
        primary_action = "跑雷達前先確認備援來源、資料 freshness 或不碰清單。"
    else:
        status = "ok"
        verdict = "可作為雷達輸入"
        primary_action = "可用這個市場池執行一鍵候選或每日雷達。"

    return {
        "requested_universe": str(universe or "curated_watchlist"),
        "mode": mode or "curated_watchlist",
        "status": status,
        "verdict": verdict,
        "primary_action": primary_action,
        "source": source,
        "source_label": _source_label(source),
        "fallback_used": fallback_used,
        "symbol_count": len(resolved),
        "sample_symbols": resolved[:sample_limit],
        "symbols": resolved,
        "excluded_symbol_count": len(no_touch_symbols),
        "no_touch_overlap": no_touch_overlap[:sample_limit],
        "freshness": {
            "as_of": as_of.isoformat(),
            "freshness_days": freshness_window,
            "recent_count": recent_count,
            "coverage": freshness_coverage,
            "coverage_pct": round(freshness_coverage * 100, 1) if freshness_coverage is not None else None,
        },
        "industry_counts": industries,
        "industry_source_counts": industry_sources,
        "industry_proxy_ratio": round(proxy_ratio, 4),
        "top_industry_ratio": round(top_industry_ratio, 4),
        "messages": messages,
        "settings": {
            "markets": DYNAMIC_UNIVERSE_MARKETS,
            "dynamic_liquid_limit": DYNAMIC_LIQUID_LIMIT,
            "dynamic_balanced_limit": DYNAMIC_BALANCED_LIMIT,
            "dynamic_balanced_per_industry": DYNAMIC_BALANCED_PER_INDUSTRY,
            "dynamic_lookback_days": DYNAMIC_LOOKBACK_DAYS,
            "dynamic_min_bars": DYNAMIC_MIN_BARS,
            "research_ready_limit": RESEARCH_READY_LIMIT,
            "research_ready_per_industry": RESEARCH_READY_PER_INDUSTRY,
            "research_ready_history_lookback_days": RESEARCH_READY_HISTORY_LOOKBACK_DAYS,
            "research_ready_min_history_bars": RESEARCH_READY_MIN_HISTORY_BARS,
            "research_ready_chip_lookback_days": RESEARCH_READY_CHIP_LOOKBACK_DAYS,
            "research_ready_min_chip_days": RESEARCH_READY_MIN_CHIP_DAYS,
            "research_ready_freshness_days": RESEARCH_READY_FRESHNESS_DAYS,
        },
    }


def resolve_tw_universe(repo, sync_service, universe: str, *, fallback_to_default: bool = True) -> list[str]:
    mode = str(universe or "curated_watchlist").strip().lower()
    if mode.startswith("index:") or mode.startswith("constituents:"):
        _, _, key = mode.partition(":")
        symbols = load_index_constituent_symbols(key)
        if symbols:
            return symbols
        return _fallback_universe(fallback_to_default)
    if mode in {"curated", "curated_watchlist", "watchlist"}:
        return load_curated_watchlist_symbols()
    if mode in {"dynamic_liquid", "liquid", "recent_liquid", "liquidity"}:
        if hasattr(repo, "get_recent_liquid_symbols"):
            symbols = repo.get_recent_liquid_symbols(
                markets=DYNAMIC_UNIVERSE_MARKETS,
                exclude_symbols=_load_no_touch_symbols(),
                limit=DYNAMIC_LIQUID_LIMIT,
                lookback_days=DYNAMIC_LOOKBACK_DAYS,
                min_bars=DYNAMIC_MIN_BARS,
            )
            if symbols:
                return symbols
        return _fallback_universe(fallback_to_default)
    if mode in {"dynamic_balanced", "dynamic_industry", "industry_balanced", "balanced_liquid"}:
        if hasattr(repo, "get_recent_liquid_symbols_by_industry"):
            rows = repo.get_recent_liquid_symbols_by_industry(
                markets=DYNAMIC_UNIVERSE_MARKETS,
                exclude_symbols=_load_no_touch_symbols(),
                limit=DYNAMIC_BALANCED_LIMIT,
                per_industry=DYNAMIC_BALANCED_PER_INDUSTRY,
                lookback_days=DYNAMIC_LOOKBACK_DAYS,
                min_bars=DYNAMIC_MIN_BARS,
            )
            symbols = [
                str(row.get("symbol") or "").strip().upper()
                for row in rows
                if str(row.get("symbol") or "").strip()
            ]
            if symbols:
                return symbols
        if hasattr(repo, "get_recent_liquid_symbols"):
            symbols = repo.get_recent_liquid_symbols(
                markets=DYNAMIC_UNIVERSE_MARKETS,
                exclude_symbols=_load_no_touch_symbols(),
                limit=DYNAMIC_BALANCED_LIMIT,
                lookback_days=DYNAMIC_LOOKBACK_DAYS,
                min_bars=DYNAMIC_MIN_BARS,
            )
            if symbols:
                return symbols
        return _fallback_universe(fallback_to_default)
    if mode in {"research_ready", "research", "quality_universe", "ready_universe"}:
        if hasattr(repo, "get_research_ready_symbols_by_industry"):
            rows = repo.get_research_ready_symbols_by_industry(
                markets=DYNAMIC_UNIVERSE_MARKETS,
                exclude_symbols=_load_no_touch_symbols(),
                limit=RESEARCH_READY_LIMIT,
                per_industry=RESEARCH_READY_PER_INDUSTRY,
                history_lookback_days=RESEARCH_READY_HISTORY_LOOKBACK_DAYS,
                min_history_bars=RESEARCH_READY_MIN_HISTORY_BARS,
                chip_lookback_days=RESEARCH_READY_CHIP_LOOKBACK_DAYS,
                min_chip_days=RESEARCH_READY_MIN_CHIP_DAYS,
                freshness_days=RESEARCH_READY_FRESHNESS_DAYS,
            )
            symbols = [
                str(row.get("symbol") or "").strip().upper()
                for row in rows
                if isinstance(row, dict) and str(row.get("symbol") or "").strip()
            ]
            if symbols:
                return symbols
        return resolve_tw_universe(repo, sync_service, "dynamic_balanced", fallback_to_default=fallback_to_default)
    if mode in {"tw0050", "0050", "taiwan50", "taiwan_50"}:
        return repo.get_latest_0050_symbols() or load_0050_symbols()
    if mode in {"twmid100", "tw_mid100", "mid100", "taiwan_mid100", "taiwan_mid_cap_100"}:
        local_symbols = load_mid100_symbols(fallback=False)
        if local_symbols:
            return local_symbols
        exclude_symbols = repo.get_latest_0050_symbols() or load_0050_symbols()
        if hasattr(repo, "get_recent_liquid_symbols"):
            symbols = repo.get_recent_liquid_symbols(
                markets=["TWSE", "TPEX"],
                exclude_symbols=exclude_symbols,
                limit=100,
                lookback_days=90,
                min_bars=5,
            )
            if symbols:
                return symbols
        return load_mid100_symbols()
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
