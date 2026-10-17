"""
取得 TWSE 當日成交資料並選出高流動性股票。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import os
import ssl
import sys
import urllib.request
import pandas as pd

try:
    import certifi
except Exception:  # pragma: no cover
    certifi = None


TWSE_DAILY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
DEFAULT_CACHE_DIR = Path(__file__).parent / "cache"


@dataclass
class LiquidityResult:
    df: pd.DataFrame
    source: str
    cached: bool


def _build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if certifi is not None:
        ctx.load_verify_locations(certifi.where())
    return ctx


def _fetch_twse_daily_all() -> list[dict[str, object]]:
    request = urllib.request.Request(
        TWSE_DAILY_ALL_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "quant-trade/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=_build_ssl_context()) as resp:
            content_type = resp.headers.get_content_type()
            raw = resp.read().decode("utf-8-sig")
    except Exception as e:
        allow_insecure = os.getenv("ALLOW_INSECURE_MARKET_DATA", "").strip().lower() in {"1", "true", "yes", "on"}
        if not allow_insecure:
            raise RuntimeError("SSL verify failed and ALLOW_INSECURE_MARKET_DATA is not enabled") from e
        print(f"[WARN] SSL verify failed, fallback to unverified context: {e}", file=sys.stderr)
        with urllib.request.urlopen(request, timeout=30, context=ssl._create_unverified_context()) as resp:
            content_type = resp.headers.get_content_type()
            raw = resp.read().decode("utf-8-sig")

    if content_type != "application/json":
        raise RuntimeError(f"TWSE daily data returned unexpected content type: {content_type}")
    if not raw.strip():
        raise RuntimeError("TWSE daily data returned an empty response")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("TWSE daily data returned invalid JSON") from e

    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise RuntimeError("TWSE daily data returned an unexpected JSON structure")
    return payload


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def get_top_liquid_stocks(top_n: int = 300) -> LiquidityResult:
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    records = _fetch_twse_daily_all()
    if not records:
        raise RuntimeError("TWSE daily data is empty or unavailable")

    date_str = str(records[0].get("Date") or datetime.now().strftime("%Y%m%d"))

    cache_path = DEFAULT_CACHE_DIR / f"liquidity_{date_str}.csv"
    if cache_path.exists():
        df_cached = pd.read_csv(cache_path, dtype=str)
        return LiquidityResult(df=df_cached, source=str(cache_path), cached=True)

    df = pd.DataFrame.from_records(records)
    required_columns = {"Code", "TradeValue", "TradeVolume"}
    missing_columns = required_columns.difference(df.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise RuntimeError(f"TWSE daily data is missing required columns: {missing}")

    df = df[["Code", "TradeValue", "TradeVolume"]].copy()
    df = df.rename(columns={"Code": "code", "TradeValue": "turnover", "TradeVolume": "volume"})
    if "turnover" in df.columns:
        df["turnover"] = _to_numeric(df["turnover"])
    if "volume" in df.columns:
        df["volume"] = _to_numeric(df["volume"])

    df = df.dropna(subset=["code"])
    df = df[df["code"].astype(str).str.match(r"^\d{4}$")]

    if "turnover" in df.columns:
        df = df.sort_values("turnover", ascending=False)
    elif "volume" in df.columns:
        df = df.sort_values("volume", ascending=False)

    df = df.head(top_n).reset_index(drop=True)
    df.to_csv(cache_path, index=False, encoding="utf-8-sig")

    return LiquidityResult(df=df, source=TWSE_DAILY_ALL_URL, cached=False)


if __name__ == "__main__":
    result = get_top_liquid_stocks()
    print(f"Loaded {len(result.df)} liquid stocks from {result.source} (cached={result.cached})")
    print(result.df.head())
