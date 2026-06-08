from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYMBOLS_FILE = PROJECT_ROOT / "data" / "cache" / "tw0050_symbols.txt"


# Fallback list to keep local workflow executable.
# You can replace this by automated source sync in next iteration.
FALLBACK_0050_SYMBOLS = [
    "1101",
    "1216",
    "1301",
    "1303",
    "1326",
    "1402",
    "1590",
    "2002",
    "2207",
    "2303",
    "2308",
    "2317",
    "2327",
    "2330",
    "2345",
    "2357",
    "2379",
    "2382",
    "2395",
    "2408",
    "2412",
    "2454",
    "2603",
    "2609",
    "2615",
    "2801",
    "2880",
    "2881",
    "2882",
    "2883",
    "2884",
    "2885",
    "2886",
    "2887",
    "2890",
    "2891",
    "2892",
    "2912",
    "3008",
    "3034",
    "3045",
    "3711",
    "4904",
    "5871",
    "5880",
    "6415",
    "6505",
    "6669",
    "8454",
    "9904",
]


def load_0050_symbols(symbols_file: Path | None = None) -> list[str]:
    """
    Load 0050 symbols from local cache file first, fallback to built-in list.
    """
    path = symbols_file or DEFAULT_SYMBOLS_FILE
    if path.exists():
        symbols = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if symbols:
            return sorted(set(symbols))
    return sorted(set(FALLBACK_0050_SYMBOLS))
