from __future__ import annotations

import pandas as pd


def ensure_required_columns(df: pd.DataFrame, required: list[str]) -> None:
    missing = [name for name in required if name not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period, min_periods=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period, min_periods=period).mean()
    rs = gain / loss.replace(0.0, pd.NA)
    return 100 - (100 / (1 + rs))


def bollinger_bands(series: pd.Series, period: int = 20, std_multiplier: float = 2.0):
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = mid + (std * std_multiplier)
    lower = mid - (std * std_multiplier)
    return mid, upper, lower


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift(1)).abs()
    low_close = (df["Low"] - df["Close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def with_common_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    ensure_required_columns(out, ["Open", "High", "Low", "Close", "Volume"])
    out["MA20"] = sma(out["Close"], 20)
    out["MA60"] = sma(out["Close"], 60)
    out["RSI14"] = rsi(out["Close"], 14)
    bb_mid, bb_upper, bb_lower = bollinger_bands(out["Close"], 20, 2.0)
    out["BB_Mid"] = bb_mid
    out["BB_Upper"] = bb_upper
    out["BB_Lower"] = bb_lower
    out["ATR14"] = atr(out, 14)
    out["Volume_MA5"] = sma(out["Volume"], 5)
    out["Donchian_High_20"] = out["High"].shift(1).rolling(window=20, min_periods=20).max()
    return out

