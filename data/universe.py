"""
取得 TWSE 上市公司清單（排除 ETF/ETN/特別股），並快取到本地。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import io
import ssl
import sys
import pandas as pd
import urllib.request

try:
    import certifi
except Exception:  # pragma: no cover - optional dependency
    certifi = None


TWSE_LISTED_URL = "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv"
DEFAULT_CACHE_PATH = Path(__file__).parent / "cache" / "universe.csv"


@dataclass
class UniverseResult:
    df: pd.DataFrame
    source: str
    cached: bool


def _build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if certifi is not None:
        ctx.load_verify_locations(certifi.where())
    return ctx


def _download_bytes(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=30, context=_build_ssl_context()) as resp:
            return resp.read()
    except Exception as e:
        print(f"[WARN] SSL verify failed, fallback to unverified context: {e}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=30, context=ssl._create_unverified_context()) as resp:
            return resp.read()


def _read_csv_with_fallback(url: str) -> pd.DataFrame:
    """讀取 CSV，嘗試 utf-8-sig，失敗則 fallback big5。"""
    raw = _download_bytes(url)
    try:
        return pd.read_csv(io.BytesIO(raw), dtype=str, encoding="utf-8-sig")
    except Exception:
        return pd.read_csv(io.BytesIO(raw), dtype=str, encoding="big5", encoding_errors="ignore")


def _is_cache_fresh(path: Path, max_age_days: int) -> bool:
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return datetime.now() - mtime < timedelta(days=max_age_days)


def _clean_universe(df: pd.DataFrame) -> pd.DataFrame:
    # 常見欄位：公司代號、公司名稱、產業別
    col_code = "公司代號" if "公司代號" in df.columns else df.columns[0]
    col_name = "公司名稱" if "公司名稱" in df.columns else df.columns[1]
    col_industry = "產業別" if "產業別" in df.columns else None

    out = pd.DataFrame({
        "code": df[col_code].astype(str).str.strip(),
        "name": df[col_name].astype(str).str.strip(),
    })
    if col_industry:
        out["industry"] = df[col_industry].astype(str).str.strip()
    else:
        out["industry"] = ""

    # 僅保留 4 碼數字
    out = out[out["code"].str.match(r"^\d{4}$")]

    # 排除 ETF/ETN/特別股
    name_upper = out["name"].str.upper()
    exclude_keywords = ["ETF", "ETN", "受益憑證", "特別股"]
    mask_exclude = False
    for kw in exclude_keywords:
        mask_exclude = mask_exclude | name_upper.str.contains(kw, regex=False)
    out = out[~mask_exclude]

    # 建立 .TW 代號
    out["symbol"] = out["code"] + ".TW"

    out = out.drop_duplicates(subset=["code"]).reset_index(drop=True)
    return out


def get_twse_universe(cache_path: Path = DEFAULT_CACHE_PATH, max_age_days: int = 7) -> UniverseResult:
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if _is_cache_fresh(cache_path, max_age_days):
        df = pd.read_csv(cache_path, dtype=str)
        return UniverseResult(df=df, source=str(cache_path), cached=True)

    df_raw = _read_csv_with_fallback(TWSE_LISTED_URL)
    df = _clean_universe(df_raw)
    df.to_csv(cache_path, index=False, encoding="utf-8-sig")
    return UniverseResult(df=df, source=TWSE_LISTED_URL, cached=False)


if __name__ == "__main__":
    result = get_twse_universe()
    print(f"Loaded {len(result.df)} symbols from {result.source} (cached={result.cached})")
    print(result.df.head())
