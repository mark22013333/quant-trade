from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WATCHLIST_FILE = PROJECT_ROOT / "data" / "cache" / "curated_watchlist.txt"


FALLBACK_CURATED_WATCHLIST = [
    "1216",
    "1303",
    "1326",
    "1590",
    "2207",
    "2308",
    "2317",
    "2330",
    "2344",
    "2357",
    "2367",
    "2379",
    "2382",
    "2409",
    "2454",
    "2603",
    "2615",
    "2881",
    "2882",
    "2891",
    "3017",
    "3034",
    "3231",
    "3481",
    "3661",
    "3711",
    "5871",
    "6443",
    "6669",
    "8150",
]


def _normalize_tw_symbol(raw: str) -> str:
    code = str(raw or "").strip().upper()
    for suffix in (".TW", ".TWO"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    return code


def load_curated_watchlist_symbols(symbols_file: Path | None = None) -> list[str]:
    path = symbols_file or DEFAULT_WATCHLIST_FILE
    if path.exists():
        symbols: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                symbols.append(_normalize_tw_symbol(line))
        if symbols:
            return sorted({item for item in symbols if item})
    return sorted(set(FALLBACK_CURATED_WATCHLIST))
