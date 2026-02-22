"""
短期投資適合度排行器。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from data.market_data import MarketData
from data.providers.yfinance_provider import YFinanceProvider
from data.universe import get_twse_universe
from data.liquidity import get_top_liquid_stocks


@dataclass
class RankingOutput:
    full_df: pd.DataFrame
    top20_df: pd.DataFrame
    full_csv: Path
    top20_csv: Path


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _compute_atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    atr_pct = atr / df["Close"] * 100
    return atr_pct


def compute_features(df: pd.DataFrame) -> Dict[str, float]:
    if df is None or df.empty:
        return {}
    if not {"Open", "High", "Low", "Close", "Volume"}.issubset(set(df.columns)):
        return {}

    df = df.dropna(subset=["Close", "Volume"])
    if len(df) < 30:
        return {}

    close = df["Close"]
    rsi = _compute_rsi(close, 14)
    atr_pct = _compute_atr_pct(df, 14)

    def pct_change(n):
        return close.pct_change(n).iloc[-1]

    avg_turnover_20 = (df["Close"] * df["Volume"]).rolling(window=20).mean().iloc[-1]

    return {
        "ret_5d": float(pct_change(5)),
        "ret_10d": float(pct_change(10)),
        "ret_20d": float(pct_change(20)),
        "rsi_14": float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else np.nan,
        "atr_pct": float(atr_pct.iloc[-1]) if not np.isnan(atr_pct.iloc[-1]) else np.nan,
        "avg_turnover_20d": float(avg_turnover_20) if not np.isnan(avg_turnover_20) else np.nan,
    }


def score_stocks(features_df: pd.DataFrame) -> pd.DataFrame:
    df = features_df.copy()
    required = ["ret_5d", "ret_10d", "ret_20d", "rsi_14", "atr_pct", "avg_turnover_20d"]
    df = df.dropna(subset=required)
    if df.empty:
        return df

    # percentile rank
    rank_5 = df["ret_5d"].rank(pct=True)
    rank_10 = df["ret_10d"].rank(pct=True)
    rank_20 = df["ret_20d"].rank(pct=True)
    rank_liq = df["avg_turnover_20d"].rank(pct=True)
    rank_atr = df["atr_pct"].rank(pct=True)

    momentum_score = 0.5 * rank_20 + 0.3 * rank_10 + 0.2 * rank_5
    liquidity_score = rank_liq
    risk_score = 1 - rank_atr

    # RSI score: closer to 55 is better
    rsi_score = 1 - (df["rsi_14"] - 55).abs().clip(0, 45) / 45

    total_score = 100 * (
        0.45 * momentum_score +
        0.25 * liquidity_score +
        0.15 * rsi_score +
        0.15 * risk_score
    )

    df["momentum_score"] = momentum_score
    df["liquidity_score"] = liquidity_score
    df["risk_score"] = risk_score
    df["rsi_score"] = rsi_score
    df["total_score"] = total_score

    return df


def run_short_term_ranking(
    top_n: int = 20,
    preselect_n: int = 300,
    lookback_days: int = 90,
    progress_fn=None,
) -> RankingOutput:
    universe = get_twse_universe().df
    liquidity = get_top_liquid_stocks(top_n=preselect_n).df

    # 只保留流動性清單內的股票
    candidates = universe[universe["code"].isin(liquidity["code"].astype(str))].copy()
    symbols = candidates["symbol"].tolist()

    provider = YFinanceProvider()
    market_data = MarketData(provider)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    rows = []
    total = len(candidates)
    for idx, row in enumerate(candidates.itertuples(index=False), start=1):
        if progress_fn:
            progress_fn(idx, total)
        symbol = row.symbol
        data = market_data.load_data(symbol, start_date, end_date)
        features = compute_features(data)
        if not features:
            continue
        features.update({
            "code": row.code,
            "symbol": symbol,
            "name": row.name,
            "industry": getattr(row, "industry", ""),
        })
        rows.append(features)

    full_df = pd.DataFrame(rows)
    scored = score_stocks(full_df)
    if scored.empty:
        raise RuntimeError("No valid data to rank. Check data availability.")

    scored = scored.sort_values("total_score", ascending=False).reset_index(drop=True)
    top20 = scored.head(top_n).copy()

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    full_csv = reports_dir / f"short_term_full_{timestamp}.csv"
    top20_csv = reports_dir / f"short_term_top20_{timestamp}.csv"

    scored.to_csv(full_csv, index=False, encoding="utf-8-sig")
    top20.to_csv(top20_csv, index=False, encoding="utf-8-sig")

    return RankingOutput(full_df=scored, top20_df=top20, full_csv=full_csv, top20_csv=top20_csv)


if __name__ == "__main__":
    output = run_short_term_ranking()
    print(output.top20_df.head(10))
    print("CSV:", output.full_csv, output.top20_csv)
