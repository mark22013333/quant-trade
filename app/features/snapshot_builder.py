from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd
from app.services.scoring import _fundamental_scores

if TYPE_CHECKING:
    from app.db.repository import TradingRepository


def _bars_to_frame(rows) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "date": row.date,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
            }
            for row in rows
        ]
    )
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").set_index("date")
    return frame


def _calc_rsi(close: pd.Series, period: int = 3) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    return (100 - (100 / (1 + rs))).fillna(100.0)


def _calc_kd(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 9,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> tuple[pd.Series, pd.Series]:
    low_n = low.rolling(window=period, min_periods=period).min()
    high_n = high.rolling(window=period, min_periods=period).max()
    rsv = ((close - low_n) / (high_n - low_n).replace(0.0, float("nan")) * 100.0).fillna(50.0)
    k = rsv.ewm(alpha=1 / smooth_k, adjust=False).mean()
    d = k.ewm(alpha=1 / smooth_d, adjust=False).mean()
    return k, d


def _calc_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def _normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    for suffix in (".TW", ".TWO"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return value


def rebuild_feature_snapshots(
    repo: TradingRepository,
    *,
    start_date: date,
    end_date: date,
    symbols: list[str] | None = None,
    chip_threshold: float = 1000.0,
) -> dict:
    symbols = symbols or repo.get_latest_0050_symbols()
    symbols = sorted({_normalize_symbol(symbol) for symbol in symbols if _normalize_symbol(symbol)})

    total_rows = 0
    skipped_symbols: list[str] = []
    failed: list[dict] = []
    processed_symbols = 0

    for symbol in symbols:
        try:
            bars = repo.get_daily_bars(symbol=symbol, start_date=start_date, end_date=end_date)
            if len(bars) < 65:
                skipped_symbols.append(symbol)
                continue

            frame = _bars_to_frame(bars)
            frame["ma60"] = frame["close"].rolling(window=60, min_periods=60).mean()
            frame["volume_ma5"] = frame["volume"].rolling(window=5, min_periods=5).mean()
            frame["rsi3"] = _calc_rsi(frame["close"], period=3)
            frame["k9"], frame["d9"] = _calc_kd(frame["high"], frame["low"], frame["close"], period=9, smooth_k=3, smooth_d=3)
            frame["atr14"] = _calc_atr(frame, period=14)

            chip_rows = repo.get_institutional_chip(symbol=symbol, start_date=start_date, end_date=end_date)
            chip_df = pd.DataFrame(
                [
                    {
                        "date": row.date,
                        "foreign_net_buy": float(row.foreign_net_buy),
                        "investment_trust_net_buy": float(row.investment_trust_net_buy),
                        "dealer_net_buy": float(row.dealer_net_buy),
                    }
                    for row in chip_rows
                ]
            )
            if chip_df.empty:
                chip_df = pd.DataFrame(
                    {
                        "date": frame.index.date,
                        "foreign_net_buy": 0.0,
                        "investment_trust_net_buy": 0.0,
                        "dealer_net_buy": 0.0,
                    }
                )
            chip_df["date"] = pd.to_datetime(chip_df["date"])
            chip_df = chip_df.sort_values("date").set_index("date")

            broker_rows = repo.get_broker_agg(symbol=symbol, start_date=start_date, end_date=end_date)
            broker_df = pd.DataFrame(
                [
                    {
                        "date": row.date,
                        "chip_concentration_proxy": float(row.concentration_proxy),
                    }
                    for row in broker_rows
                ]
            )
            if broker_df.empty:
                broker_df = pd.DataFrame({"date": frame.index.date, "chip_concentration_proxy": 0.0})
            broker_df["date"] = pd.to_datetime(broker_df["date"])
            broker_df = broker_df.sort_values("date").set_index("date")

            merged = frame.join(chip_df, how="left").join(broker_df, how="left")
            merged[["foreign_net_buy", "investment_trust_net_buy", "dealer_net_buy", "chip_concentration_proxy"]] = (
                merged[["foreign_net_buy", "investment_trust_net_buy", "dealer_net_buy", "chip_concentration_proxy"]]
                .fillna(0.0)
                .astype(float)
            )

            merged["foreign_net_5d"] = merged["foreign_net_buy"].rolling(window=5, min_periods=1).sum()
            merged["investment_net_5d"] = merged["investment_trust_net_buy"].rolling(window=5, min_periods=1).sum()
            merged["dealer_net_5d"] = merged["dealer_net_buy"].rolling(window=5, min_periods=1).sum()
            merged["chip_concentration_up3"] = (
                merged["chip_concentration_proxy"].diff().gt(0).rolling(window=3, min_periods=3).sum() == 3
            )

            disposition_periods = repo.get_disposition_periods(symbol=symbol, start_date=start_date, end_date=end_date)

            def _is_disposition_active(ts: pd.Timestamp) -> bool:
                day = ts.date()
                for period in disposition_periods:
                    if period.start_date <= day <= period.end_date:
                        return True
                return False

            merged["disposition_active"] = merged.index.to_series().apply(_is_disposition_active)
            merged["trend_ok"] = merged["close"] > merged["ma60"]
            merged["vol_ok"] = merged["volume"] > (merged["volume_ma5"] * 1.5)
            merged["rsi_trigger"] = merged["rsi3"] < 20
            merged["kd_trigger"] = (
                merged["k9"].lt(20)
                & merged["d9"].lt(20)
                & merged["k9"].gt(merged["d9"])
                & merged["k9"].shift(1).le(merged["d9"].shift(1))
            )
            merged["chip_ok"] = (
                (merged["foreign_net_5d"] + merged["investment_net_5d"] > float(chip_threshold))
                & merged["chip_concentration_up3"]
            )
            merged["entry_ready"] = (
                merged["trend_ok"]
                & merged["vol_ok"]
                & (merged["rsi_trigger"] | merged["kd_trigger"])
                & merged["chip_ok"]
                & (~merged["disposition_active"])
            )

            records: list[dict] = []
            for idx, row in merged.iterrows():
                if idx.date() < start_date or idx.date() > end_date:
                    continue
                if pd.isna(row["ma60"]) or pd.isna(row["volume_ma5"]) or pd.isna(row["atr14"]):
                    continue
                meta = {
                    "trend_ok": bool(row["trend_ok"]),
                    "vol_ok": bool(row["vol_ok"]),
                    "rsi_trigger": bool(row["rsi_trigger"]),
                    "kd_trigger": bool(row["kd_trigger"]),
                    "chip_ok": bool(row["chip_ok"]),
                }
                fundamentals = _fundamental_scores(repo, symbol=symbol, trade_date=idx.date())
                meta["fundamental_summary"] = fundamentals["fundamental_summary"]
                meta["news_summary"] = fundamentals["news_summary"]
                records.append(
                    {
                        "date": idx.date(),
                        "close": float(row["close"]),
                        "ma60": float(row["ma60"]),
                        "volume": float(row["volume"]),
                        "volume_ma5": float(row["volume_ma5"]),
                        "rsi3": float(row["rsi3"]),
                        "k9": float(row["k9"]),
                        "d9": float(row["d9"]),
                        "atr14": float(row["atr14"]),
                        "foreign_net_5d": float(row["foreign_net_5d"]),
                        "investment_net_5d": float(row["investment_net_5d"]),
                        "dealer_net_5d": float(row["dealer_net_5d"]),
                        "chip_concentration_proxy": float(row["chip_concentration_proxy"]),
                        "chip_concentration_up3": bool(row["chip_concentration_up3"]),
                        "disposition_active": bool(row["disposition_active"]),
                        "revenue_score": float(fundamentals["revenue_score"]),
                        "quality_score": float(fundamentals["quality_score"]),
                        "valuation_or_growth_score": float(fundamentals["valuation_or_growth_score"]),
                        "news_risk_score": float(fundamentals["news_risk_score"]),
                        "fundamental_data_quality": str(fundamentals["fundamental_data_quality"]),
                        "entry_ready": bool(row["entry_ready"]),
                        "meta_json": json.dumps(meta, ensure_ascii=False),
                    }
                )
            upserted = repo.upsert_feature_snapshots(symbol=symbol, records=records)
            total_rows += upserted
            processed_symbols += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"symbol": symbol, "error": str(exc)})

    return {
        "symbols_total": len(symbols),
        "symbols_processed": processed_symbols,
        "symbols_skipped": len(skipped_symbols),
        "skipped_symbols": skipped_symbols[:20],
        "rows_upserted": total_rows,
        "failed_count": len(failed),
        "failed_samples": failed[:10],
    }
