from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from app.data.finmind_client import FinMindClient
from app.data.universe_0050 import load_0050_symbols
from app.db.repository import TradingRepository


class SyncService:
    def __init__(
        self,
        repo: TradingRepository,
        finmind: FinMindClient | None = None,
        local_data_dir: Path | None = None,
    ):
        self.repo = repo
        self.finmind = finmind or FinMindClient()
        self.local_data_dir = local_data_dir or (Path(__file__).resolve().parents[2] / "data" / "stock_data")

    def sync_0050_universe(self, snapshot_date: date | None = None) -> dict:
        snapshot_date = snapshot_date or datetime.now().date()
        symbols = load_0050_symbols()
        inst_rows = self.repo.upsert_instruments(symbols, market="TW", is_0050=True)
        snapshot_rows = self.repo.replace_0050_snapshot(snapshot_date, symbols)
        self.repo.add_sync_job("sync_0050_universe", status="ok", rows_upserted=snapshot_rows)
        return {
            "snapshot_date": snapshot_date.isoformat(),
            "symbols": len(symbols),
            "instruments_upserted": inst_rows,
            "snapshot_rows": snapshot_rows,
        }

    def sync_twse_tpex_universe(self) -> dict:
        rows = self.finmind.fetch_stock_info()
        twse_profiles = [
            {
                "symbol": str(item.get("symbol", "")).strip(),
                "name": str(item.get("name", "") or "").strip(),
                "market": "TWSE",
                "is_0050": False,
            }
            for item in rows
            if str(item.get("market", "")).strip().upper() == "TWSE" and str(item.get("symbol", "")).strip()
        ]
        tpex_profiles = [
            {
                "symbol": str(item.get("symbol", "")).strip(),
                "name": str(item.get("name", "") or "").strip(),
                "market": "TPEX",
                "is_0050": False,
            }
            for item in rows
            if str(item.get("market", "")).strip().upper() == "TPEX" and str(item.get("symbol", "")).strip()
        ]
        upserted_total = 0
        if twse_profiles:
            upserted_total += self.repo.upsert_instruments(twse_profiles, market="TWSE", is_0050=False)
        if tpex_profiles:
            upserted_total += self.repo.upsert_instruments(tpex_profiles, market="TPEX", is_0050=False)
        self.repo.add_sync_job("sync_twse_tpex_universe", status="ok", rows_upserted=upserted_total)
        return {
            "symbols_total": len(twse_profiles) + len(tpex_profiles),
            "twse_symbols": len(twse_profiles),
            "tpex_symbols": len(tpex_profiles),
            "rows_upserted": upserted_total,
        }

    def sync_daily_bars(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        fallback_rows = 0
        failed: list[dict] = []
        for symbol in symbols:
            try:
                bars = self.finmind.fetch_daily_bars(symbol, start_date, end_date)
                rows = self.repo.upsert_daily_bars(symbol, bars)
                total_rows += rows
            except Exception as exc:  # noqa: BLE001
                local_bars = self._load_local_parquet_daily_bars(symbol=symbol, start_date=start_date, end_date=end_date)
                if local_bars:
                    rows = self.repo.upsert_daily_bars(symbol, local_bars)
                    total_rows += rows
                    fallback_rows += rows
                    continue
                failed.append(self._failure("daily_bars", exc, symbol=symbol))

        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
            "fallback_rows": fallback_rows,
        }
        return self._finalize_sync_result("sync_daily_bars", result, failed, start_date=start_date, end_date=end_date)

    def sync_institutional_chip(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        failed: list[dict] = []
        for symbol in symbols:
            try:
                rows = self.finmind.fetch_institutional_chip(symbol, start_date, end_date)
                upserted = self.repo.upsert_institutional_chip(symbol, rows)
                total_rows += upserted
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("institutional_chip", exc, symbol=symbol))
        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
        }
        return self._finalize_sync_result("sync_institutional_chip", result, failed, start_date=start_date, end_date=end_date)

    def sync_broker_agg_chip(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        failed: list[dict] = []
        for symbol in symbols:
            try:
                rows = self.finmind.fetch_broker_agg_chip(symbol, start_date, end_date)
                upserted = self.repo.upsert_broker_agg(symbol, rows)
                total_rows += upserted
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("broker_agg_chip", exc, symbol=symbol))
        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
        }
        return self._finalize_sync_result("sync_broker_agg_chip", result, failed, start_date=start_date, end_date=end_date)

    def sync_disposition_periods(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        failed: list[dict] = []
        for symbol in symbols:
            try:
                rows = self.finmind.fetch_disposition_periods(symbol, start_date, end_date)
                upserted = self.repo.upsert_disposition_periods(symbol, rows)
                total_rows += upserted
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("disposition_periods", exc, symbol=symbol))
        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
        }
        return self._finalize_sync_result("sync_disposition_periods", result, failed, start_date=start_date, end_date=end_date)

    def sync_monthly_revenues(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        failed: list[dict] = []
        for symbol in symbols:
            try:
                rows = self.finmind.fetch_monthly_revenue(symbol, start_date, end_date)
                total_rows += self.repo.upsert_monthly_revenues(symbol, rows)
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("monthly_revenue", exc, symbol=symbol))
        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
        }
        return self._finalize_sync_result("sync_monthly_revenues", result, failed, start_date=start_date, end_date=end_date)

    def sync_financial_statement_summaries(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        failed: list[dict] = []
        for symbol in symbols:
            try:
                rows = self.finmind.fetch_financial_statement_summary(symbol, start_date, end_date)
                total_rows += self.repo.upsert_financial_statement_summaries(symbol, rows)
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("financial_statement_summary", exc, symbol=symbol))
        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
        }
        return self._finalize_sync_result(
            "sync_financial_statement_summaries",
            result,
            failed,
            start_date=start_date,
            end_date=end_date,
        )

    def sync_news_events(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        failed: list[dict] = []
        for symbol in symbols:
            try:
                rows = self.finmind.fetch_stock_news(symbol, start_date, end_date)
                total_rows += self.repo.upsert_news_events(symbol, rows)
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("news_events", exc, symbol=symbol))
        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
        }
        return self._finalize_sync_result("sync_news_events", result, failed, start_date=start_date, end_date=end_date)

    def sync_fundamental_bundle(
        self,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        include_news: bool = True,
    ) -> dict:
        bundle = {
            "monthly_revenues": self.sync_monthly_revenues(start_date=start_date, end_date=end_date, symbols=symbols),
            "financial_statement_summaries": self.sync_financial_statement_summaries(
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
            ),
        }
        if include_news:
            bundle["news_events"] = self.sync_news_events(start_date=start_date, end_date=end_date, symbols=symbols)
        summary = self._bundle_summary(bundle)
        total_rows = summary["rows_upserted_total"]
        self.repo.add_sync_job(
            "sync_fundamental_bundle",
            status="partial" if summary["partial_failure"] else "ok",
            rows_upserted=total_rows,
            error_msg=self._sync_metadata({"sync_quality": summary["sync_quality"], **summary}),
        )
        return {
            "rows_upserted_total": total_rows,
            "partial_failure": summary["partial_failure"],
            "failed_count_total": summary["failed_count_total"],
            "degraded_count": summary["degraded_count"],
            "sync_quality": summary["sync_quality"],
            "bundle": bundle,
        }

    def sync_shareholding(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        symbol_set = set(symbols)
        total_rows = 0
        failed: list[dict] = []
        degraded_count = 0
        api_mode = "by_date"
        for day in self._iter_weekdays(start_date, end_date):
            try:
                rows = self.finmind.fetch_shareholding_by_date(day)
                grouped = self._group_rows_by_symbol(rows, allowed_symbols=symbol_set)
                for symbol, payload in grouped.items():
                    total_rows += self.repo.upsert_shareholding(symbol, payload)
            except Exception as exc:  # noqa: BLE001
                degraded_count += 1
                failed.append(self._failure("shareholding_by_date", exc, date=day.isoformat()))
                api_mode = "mixed"

        if degraded_count > 0:
            api_mode = "mixed"
            for symbol in symbols:
                try:
                    rows = self.finmind.fetch_shareholding(symbol, start_date, end_date)
                    total_rows += self.repo.upsert_shareholding(symbol, rows)
                except Exception as exc:  # noqa: BLE001
                    failed.append(self._failure("shareholding_per_symbol", exc, symbol=symbol))
            if len(failed) >= len(symbols):
                api_mode = "per_symbol"

        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
            "degraded_count": degraded_count,
            "api_mode": api_mode,
        }
        return self._finalize_sync_result("sync_shareholding", result, failed, start_date=start_date, end_date=end_date)

    def sync_holding_shares_per(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        symbol_set = set(symbols)
        total_rows = 0
        failed: list[dict] = []
        degraded_count = 0
        api_mode = "by_date"
        for day in self._iter_weekdays(start_date, end_date):
            try:
                rows = self.finmind.fetch_holding_shares_per_by_date(day)
                grouped = self._group_rows_by_symbol(rows, allowed_symbols=symbol_set)
                for symbol, payload in grouped.items():
                    total_rows += self.repo.upsert_holding_shares_per(symbol, payload)
            except Exception as exc:  # noqa: BLE001
                degraded_count += 1
                failed.append(self._failure("holding_shares_per_by_date", exc, date=day.isoformat()))
                api_mode = "mixed"

        if degraded_count > 0:
            for symbol in symbols:
                try:
                    rows = self.finmind.fetch_holding_shares_per(symbol, start_date, end_date)
                    total_rows += self.repo.upsert_holding_shares_per(symbol, rows)
                except Exception as exc:  # noqa: BLE001
                    failed.append(self._failure("holding_shares_per_symbol", exc, symbol=symbol))
            if len(failed) >= len(symbols):
                api_mode = "per_symbol"

        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
            "degraded_count": degraded_count,
            "api_mode": api_mode,
        }
        return self._finalize_sync_result("sync_holding_shares_per", result, failed, start_date=start_date, end_date=end_date)

    def sync_broker_agg_chip_by_date(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        symbol_set = set(symbols)
        total_rows = 0
        failed: list[dict] = []
        degraded_count = 0
        api_mode = "by_date"
        for day in self._iter_weekdays(start_date, end_date):
            try:
                rows = self.finmind.fetch_broker_agg_by_date(day, async_mode=True)
                grouped = self._group_rows_by_symbol(rows, allowed_symbols=symbol_set)
                for symbol, payload in grouped.items():
                    total_rows += self.repo.upsert_broker_agg(symbol, payload)
            except Exception as exc:  # noqa: BLE001
                degraded_count += 1
                failed.append(self._failure("broker_agg_by_date", exc, date=day.isoformat()))
                api_mode = "mixed"

        if degraded_count > 0:
            for symbol in symbols:
                try:
                    rows = self.finmind.fetch_broker_agg_chip(symbol, start_date, end_date)
                    total_rows += self.repo.upsert_broker_agg(symbol, rows)
                except Exception as exc:  # noqa: BLE001
                    failed.append(self._failure("broker_agg_per_symbol", exc, symbol=symbol))
            if len(failed) >= len(symbols):
                api_mode = "per_symbol"

        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
            "degraded_count": degraded_count,
            "api_mode": api_mode,
        }
        return self._finalize_sync_result("sync_broker_agg_chip_by_date", result, failed, start_date=start_date, end_date=end_date)

    def sync_extended_chip_bundle(
        self,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
    ) -> dict:
        bundle = {
            "broker_agg_chip": self.sync_broker_agg_chip_by_date(start_date=start_date, end_date=end_date, symbols=symbols),
            "shareholding": self.sync_shareholding(start_date=start_date, end_date=end_date, symbols=symbols),
            "holding_shares_per": self.sync_holding_shares_per(start_date=start_date, end_date=end_date, symbols=symbols),
        }
        summary = self._bundle_summary(bundle)
        total_rows = summary["rows_upserted_total"]
        self.repo.add_sync_job(
            "sync_extended_chip_bundle",
            status="partial" if summary["partial_failure"] else "ok",
            rows_upserted=total_rows,
            error_msg=self._sync_metadata({"sync_quality": summary["sync_quality"], **summary}),
        )
        return {
            "rows_upserted_total": total_rows,
            "partial_failure": summary["partial_failure"],
            "failed_count_total": summary["failed_count_total"],
            "degraded_count": summary["degraded_count"],
            "sync_quality": summary["sync_quality"],
            "bundle": bundle,
        }

    def sync_market_bundle(
        self,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        include_bars: bool = True,
        include_institutional: bool = True,
        include_broker_agg: bool = True,
        include_disposition: bool = True,
        include_fundamentals: bool = False,
        include_news: bool = False,
    ) -> dict:
        bundle: dict[str, dict] = {}
        if include_bars:
            bundle["daily_bars"] = self.sync_daily_bars(start_date=start_date, end_date=end_date, symbols=symbols)
        if include_institutional:
            bundle["institutional_chip"] = self.sync_institutional_chip(start_date=start_date, end_date=end_date, symbols=symbols)
        if include_broker_agg:
            bundle["broker_agg_chip"] = self.sync_broker_agg_chip(start_date=start_date, end_date=end_date, symbols=symbols)
        if include_disposition:
            bundle["disposition_periods"] = self.sync_disposition_periods(start_date=start_date, end_date=end_date, symbols=symbols)
        if include_fundamentals:
            bundle["monthly_revenues"] = self.sync_monthly_revenues(start_date=start_date, end_date=end_date, symbols=symbols)
            bundle["financial_statement_summaries"] = self.sync_financial_statement_summaries(
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
            )
        if include_news:
            bundle["news_events"] = self.sync_news_events(start_date=start_date, end_date=end_date, symbols=symbols)
        summary = self._bundle_summary(bundle)
        total_rows = summary["rows_upserted_total"]
        self.repo.add_sync_job(
            "sync_market_bundle",
            status="partial" if summary["partial_failure"] else "ok",
            rows_upserted=total_rows,
            error_msg=self._sync_metadata({"sync_quality": summary["sync_quality"], **summary}),
        )
        return {
            "rows_upserted_total": total_rows,
            "partial_failure": summary["partial_failure"],
            "failed_count_total": summary["failed_count_total"],
            "degraded_count": summary["degraded_count"],
            "sync_quality": summary["sync_quality"],
            "bundle": bundle,
        }

    def _finalize_sync_result(
        self,
        job_name: str,
        result: dict,
        failed: list[dict],
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        failed_count = len(failed)
        sync_quality = self._sync_quality(result, failed_count=failed_count, start_date=start_date, end_date=end_date)
        payload = {
            **result,
            "failed_count": failed_count,
            "failed_samples": failed[:10],
            "sync_quality": sync_quality,
        }
        status = "ok" if failed_count == 0 else "partial"
        self.repo.add_sync_job(
            job_name,
            status=status,
            rows_upserted=int(payload.get("rows_upserted") or 0),
            error_msg=self._sync_metadata(
                {
                    "sync_quality": sync_quality,
                    "failed_count": failed_count,
                    "failed_samples": failed[:5],
                }
            ),
        )
        return payload

    @staticmethod
    def _failure(scope: str, exc: Exception, **context: object) -> dict:
        return {
            **context,
            "scope": scope,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }

    @staticmethod
    def _bundle_summary(bundle: dict[str, dict]) -> dict:
        failed_count_total = int(sum(int((payload or {}).get("failed_count") or 0) for payload in bundle.values()))
        degraded_count = int(sum(int((payload or {}).get("degraded_count") or 0) for payload in bundle.values()))
        rows_upserted_total = int(sum(int((payload or {}).get("rows_upserted") or 0) for payload in bundle.values()))
        partial_failure = failed_count_total > 0
        return {
            "rows_upserted_total": rows_upserted_total,
            "failed_count_total": failed_count_total,
            "degraded_count": degraded_count,
            "partial_failure": partial_failure,
            "sync_quality": {
                "status": "partial" if partial_failure else "complete",
                "rows_upserted": rows_upserted_total,
                "failed_count": failed_count_total,
                "degraded_count": degraded_count,
                "components": {
                    name: (payload or {}).get("sync_quality", {})
                    for name, payload in bundle.items()
                },
            },
        }

    @staticmethod
    def _sync_quality(
        result: dict,
        *,
        failed_count: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        degraded_count = int(result.get("degraded_count") or 0)
        fallback_rows = int(result.get("fallback_rows") or 0)
        rows_upserted = int(result.get("rows_upserted") or 0)
        if failed_count > 0:
            status = "partial"
        elif degraded_count > 0 or fallback_rows > 0:
            status = "degraded"
        else:
            status = "complete"
        return {
            "status": status,
            "requested_start": start_date.isoformat() if start_date else None,
            "requested_end": end_date.isoformat() if end_date else None,
            "rows_upserted": rows_upserted,
            "failed_count": failed_count,
            "degraded_count": degraded_count,
            "fallback_rows": fallback_rows,
            "api_mode": str(result.get("api_mode", "") or ""),
        }

    @staticmethod
    def _sync_metadata(payload: dict) -> str:
        if not payload:
            return ""
        return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)

    def _resolve_symbols(self, symbols: list[str] | None) -> list[str]:
        value = symbols or self.repo.get_latest_0050_symbols() or load_0050_symbols()
        return sorted({self._normalize_symbol(symbol) for symbol in value if self._normalize_symbol(symbol)})

    @staticmethod
    def _group_rows_by_symbol(rows: list[dict], allowed_symbols: set[str] | None = None) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        allow = allowed_symbols or set()
        for row in rows or []:
            symbol = str(row.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            if allow and symbol not in allow:
                continue
            grouped.setdefault(symbol, []).append(row)
        return grouped

    @staticmethod
    def _iter_weekdays(start_date: date, end_date: date) -> Iterable[date]:
        cursor = min(start_date, end_date)
        end = max(start_date, end_date)
        while cursor <= end:
            if cursor.weekday() < 5:
                yield cursor
            cursor = cursor + timedelta(days=1)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        code = str(symbol).strip().upper()
        for suffix in (".TW", ".TWO"):
            if code.endswith(suffix):
                code = code[: -len(suffix)]
        return code

    def _load_local_parquet_daily_bars(self, symbol: str, start_date: date, end_date: date) -> list[dict]:
        try:
            import pandas as pd
        except Exception:
            return []

        code = str(symbol).strip().upper()
        candidates = [
            self.local_data_dir / f"{code}.TW_1d.parquet",
            self.local_data_dir / f"{code}.TWO_1d.parquet",
            self.local_data_dir / f"{code}_1d.parquet",
        ]
        parquet_path = next((path for path in candidates if path.exists()), None)
        if parquet_path is None:
            return []

        try:
            raw = pd.read_parquet(parquet_path)
        except Exception:
            return []
        if raw.empty:
            return []

        df = raw.copy()
        if "Date" in df.columns and df["Date"].notna().any():
            date_series = pd.to_datetime(df["Date"], errors="coerce")
        else:
            date_series = pd.to_datetime(df.index, errors="coerce")
        date_series = pd.Series(date_series, index=df.index)

        def pick_col(*names: str):
            for name in names:
                if name in df.columns and df[name].notna().any():
                    return pd.to_numeric(df[name], errors="coerce")
            return pd.Series([float("nan")] * len(df), index=df.index)

        # Support both flat columns and tuple-string columns generated by some exporters.
        open_col = pick_col("Open", f"('Open', '{code}.TW')", f"('Open', '{code}.TWO')")
        high_col = pick_col("High", f"('High', '{code}.TW')", f"('High', '{code}.TWO')")
        low_col = pick_col("Low", f"('Low', '{code}.TW')", f"('Low', '{code}.TWO')")
        close_col = pick_col("Close", f"('Close', '{code}.TW')", f"('Close', '{code}.TWO')")
        volume_col = pick_col("Volume", f"('Volume', '{code}.TW')", f"('Volume', '{code}.TWO')")

        normalized = pd.DataFrame(
            {
                "date": date_series.dt.date,
                "open": open_col,
                "high": high_col,
                "low": low_col,
                "close": close_col,
                "volume": volume_col,
            }
        )
        normalized = normalized.dropna(subset=["date", "open", "high", "low", "close", "volume"])
        if normalized.empty:
            return []

        mask = (normalized["date"] >= start_date) & (normalized["date"] <= end_date)
        clipped = normalized.loc[mask].sort_values("date")
        if clipped.empty:
            return []

        rows: list[dict] = []
        for _, row in clipped.iterrows():
            rows.append(
                {
                    "date": row["date"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "source": "local_parquet",
                }
            )
        return rows
