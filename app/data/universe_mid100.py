from __future__ import annotations

from datetime import datetime
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYMBOLS_FILE = PROJECT_ROOT / "data" / "cache" / "tw_mid100_symbols.txt"
YUANTA_ETF_API_URL = "https://etfapi.yuantaetfs.com/ectranslation/api/bridge"
YUANTA_MID100_ETF = "0051"


FALLBACK_MID100_SYMBOLS = [
    "1101",
    "1102",
    "1210",
    "1229",
    "1402",
    "1476",
    "1504",
    "1513",
    "1536",
    "1560",
    "1605",
    "1707",
    "1717",
    "1722",
    "1802",
    "2014",
    "2027",
    "2049",
    "2105",
    "2201",
    "2204",
    "2227",
    "2301",
    "2324",
    "2345",
    "2353",
    "2354",
    "2356",
    "2359",
    "2376",
    "2385",
    "2395",
    "2408",
    "2449",
    "2474",
    "2481",
    "2492",
    "2504",
    "2539",
    "2542",
    "2606",
    "2610",
    "2618",
    "2633",
    "2637",
    "2801",
    "2809",
    "2812",
    "2834",
    "2845",
    "2855",
    "2867",
    "2885",
    "2886",
    "2887",
    "2888",
    "2890",
    "2912",
    "3005",
    "3014",
    "3023",
    "3035",
    "3044",
    "3045",
    "3105",
    "3189",
    "3260",
    "3376",
    "3443",
    "3532",
    "3533",
    "3545",
    "3653",
    "3702",
    "3706",
    "3708",
    "4746",
    "4904",
    "4915",
    "4938",
    "4958",
    "4966",
    "5269",
    "5347",
    "5434",
    "5483",
    "5534",
    "5608",
    "6116",
    "6176",
    "6239",
    "6271",
    "6415",
    "6472",
    "6504",
    "6515",
    "6531",
    "6805",
    "8046",
    "8069",
    "8086",
    "8299",
    "9910",
    "9914",
    "9917",
    "9933",
    "9938",
    "9945",
]


def _normalize_tw_symbol(raw: str) -> str:
    code = str(raw or "").strip().upper()
    for suffix in (".TW", ".TWO"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    return code


def load_mid100_symbols(symbols_file: Path | None = None, *, fallback: bool = True) -> list[str]:
    path = symbols_file or DEFAULT_SYMBOLS_FILE
    if path.exists():
        symbols: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                symbols.append(_normalize_tw_symbol(line))
        if symbols:
            return sorted({item for item in symbols if item})
    return sorted(set(FALLBACK_MID100_SYMBOLS)) if fallback else []


def fetch_yuanta_etf_pcf(
    ticker: str = YUANTA_MID100_ETF,
    *,
    timeout_sec: int = 15,
    session=None,
) -> dict:
    client = session or requests
    ticker = _normalize_tw_symbol(ticker) or YUANTA_MID100_ETF
    params = {
        "APIType": "ETFAPI",
        "CompanyName": "YUANTAFUNDS",
        "PageName": f"/product/detail/{ticker}/ratio",
        "DeviceId": "null",
        "FuncId": "PCF/Daily",
        "AppName": "ETF",
        "Device": "3",
        "Platform": "ETF",
        "ticker": ticker,
    }
    response = client.get(
        YUANTA_ETF_API_URL,
        params=params,
        timeout=timeout_sec,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://www.yuantaetfs.com/product/detail/{ticker}/ratio",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Yuanta ETF PCF response is not a JSON object")
    if "ResultCode" in payload:
        if int(payload.get("ResultCode") or 0) != 0:
            raise RuntimeError(str(payload.get("ResultMsg") or "Yuanta ETF PCF request failed"))
        payload = payload.get("Data") or {}
    if not isinstance(payload, dict):
        raise RuntimeError("Yuanta ETF PCF data is not a JSON object")
    return payload


def parse_yuanta_etf_constituents(payload: dict) -> list[dict]:
    composition = (
        ((payload or {}).get("InKind") or {}).get("FundComposition")
        or (payload or {}).get("FundComposition")
        or []
    )
    if not isinstance(composition, list):
        return []
    rows: list[dict] = []
    for item in composition:
        if not isinstance(item, dict):
            continue
        symbol = _normalize_tw_symbol(item.get("stkcd") or item.get("symbol") or item.get("code"))
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": str(item.get("name") or item.get("stock_name") or "").strip(),
                "ename": str(item.get("ename") or "").strip(),
                "quantity": float(item.get("qty") or item.get("quantity") or 0.0),
                "cash_in_lieu": str(item.get("cashinlieu") or "").strip(),
                "minimum": str(item.get("minimum") or "").strip(),
            }
        )
    return rows


def sync_yuanta_mid100_symbols(
    *,
    symbols_file: Path | None = None,
    payload: dict | None = None,
    session=None,
) -> dict:
    pcf_payload = payload or fetch_yuanta_etf_pcf(YUANTA_MID100_ETF, session=session)
    rows = parse_yuanta_etf_constituents(pcf_payload)
    if not rows:
        raise RuntimeError("Yuanta 0051 PCF did not include stock constituents")

    pcf = pcf_payload.get("PCF") or {}
    trade_date = str(pcf.get("trandate") or "").strip()
    update_time = str(pcf.get("upddate") or "").strip()
    path = symbols_file or DEFAULT_SYMBOLS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# quant-trade tw_mid100_symbols",
        "# source=yuanta_0051_pcf url=https://www.yuantaetfs.com/product/detail/0051/ratio",
        f"# trade_date={trade_date or 'unknown'} update_time={update_time or 'unknown'} synced_at={datetime.utcnow().isoformat(timespec='seconds')}Z",
    ]
    for row in rows:
        name = row.get("name") or ""
        qty = row.get("quantity") or 0
        suffix = f" # {name} qty={qty:g}" if name or qty else ""
        lines.append(f"{row['symbol']}{suffix}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "source": "yuanta_0051_pcf",
        "ticker": YUANTA_MID100_ETF,
        "trade_date": trade_date,
        "update_time": update_time,
        "symbols": [row["symbol"] for row in rows],
        "constituents": rows,
        "count": len(rows),
        "file": str(path),
    }
