"""
取得 TWSE 當日成交資料並選出高流動性股票。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import ssl
import sys
import urllib.request
import pandas as pd

try:
    import certifi
except Exception:  # pragma: no cover
    certifi = None


TWSE_DAILY_ALL_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json"
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


def _fetch_twse_daily_all() -> dict:
    try:
        with urllib.request.urlopen(TWSE_DAILY_ALL_URL, timeout=30, context=_build_ssl_context()) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        print(f"[WARN] SSL verify failed, fallback to unverified context: {e}", file=sys.stderr)
        with urllib.request.urlopen(TWSE_DAILY_ALL_URL, timeout=30, context=ssl._create_unverified_context()) as resp:
            raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def get_top_liquid_stocks(top_n: int = 300) -> LiquidityResult:
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    payload = _fetch_twse_daily_all()
    fields = payload.get("fields", [])
    data = payload.get("data", [])
    date_str = payload.get("date") or datetime.now().strftime("%Y%m%d")

    cache_path = DEFAULT_CACHE_DIR / f"liquidity_{date_str}.csv"
    if cache_path.exists():
        df_cached = pd.read_csv(cache_path, dtype=str)
        return LiquidityResult(df=df_cached, source=str(cache_path), cached=True)

    if not fields or not data:
        raise RuntimeError("TWSE daily data is empty or unavailable")

    df = pd.DataFrame(data, columns=fields)

    # 欄位可能包含：證券代號、成交股數、成交金額
    col_code = "證券代號" if "證券代號" in df.columns else df.columns[0]
    col_amount = "成交金額" if "成交金額" in df.columns else None
    col_volume = "成交股數" if "成交股數" in df.columns else None

    df = df[[c for c in [col_code, col_amount, col_volume] if c in df.columns]].copy()
    df = df.rename(columns={col_code: "code", col_amount: "turnover", col_volume: "volume"})
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
