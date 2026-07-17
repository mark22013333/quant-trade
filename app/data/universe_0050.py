from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.data.universe_mid100 import fetch_yuanta_etf_pcf, parse_yuanta_etf_constituents


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYMBOLS_FILE = PROJECT_ROOT / "data" / "cache" / "tw0050_symbols.txt"
YUANTA_0050_ETF = "0050"


# Fallback list to keep local workflow executable when the ETF source is unavailable.
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


def _normalize_tw_symbol(raw: str) -> str:
    code = str(raw or "").strip().upper()
    for suffix in (".TW", ".TWO"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    return code


def load_0050_symbols(symbols_file: Path | None = None, *, fallback: bool = True) -> list[str]:
    """
    Load 0050 symbols from local cache file first, fallback to built-in list.
    """
    path = symbols_file or DEFAULT_SYMBOLS_FILE
    if path.exists():
        symbols: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                symbols.append(_normalize_tw_symbol(line))
        if symbols:
            return sorted({item for item in symbols if item})
    return sorted(set(FALLBACK_0050_SYMBOLS)) if fallback else []


def sync_yuanta_0050_symbols(
    *,
    symbols_file: Path | None = None,
    payload: dict | None = None,
    session=None,
) -> dict:
    pcf_payload = payload or fetch_yuanta_etf_pcf(YUANTA_0050_ETF, session=session)
    rows = parse_yuanta_etf_constituents(pcf_payload)
    if not rows:
        raise RuntimeError("Yuanta 0050 PCF did not include stock constituents")

    pcf = pcf_payload.get("PCF") or {}
    trade_date = str(pcf.get("trandate") or "").strip()
    update_time = str(pcf.get("upddate") or "").strip()
    path = symbols_file or DEFAULT_SYMBOLS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# quant-trade tw0050_symbols",
        "# source=yuanta_0050_pcf url=https://www.yuantaetfs.com/product/detail/0050/ratio",
        f"# trade_date={trade_date or 'unknown'} update_time={update_time or 'unknown'} synced_at={datetime.utcnow().isoformat(timespec='seconds')}Z",
    ]
    for row in rows:
        name = row.get("name") or ""
        qty = row.get("quantity") or 0
        suffix = f" # {name} qty={qty:g}" if name or qty else ""
        lines.append(f"{row['symbol']}{suffix}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "source": "yuanta_0050_pcf",
        "ticker": YUANTA_0050_ETF,
        "trade_date": trade_date,
        "update_time": update_time,
        "symbols": [row["symbol"] for row in rows],
        "constituents": rows,
        "count": len(rows),
        "file": str(path),
    }
