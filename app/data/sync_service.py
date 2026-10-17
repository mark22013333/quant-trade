from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from app.data.finmind_client import FinMindClient
from app.data.tpex_index_client import TpexIndexClient
from app.data.tpex_stock_client import TpexStockClient
from app.data.twse_index_client import TwseIndexClient
from app.data.twse_stock_client import TwseStockClient
from app.data.index_constituents import write_index_constituents_cache
from app.data.universe_0050 import load_0050_symbols, sync_yuanta_0050_symbols
from app.data.universe_mid100 import sync_yuanta_mid100_symbols
from app.db.repository import TradingRepository


class NoDataError(RuntimeError):
    """Raised when a data source responds successfully but returns no rows."""


class SyncService:
    def __init__(
        self,
        repo: TradingRepository,
        finmind: FinMindClient | None = None,
        twse_index: TwseIndexClient | None = None,
        twse_stock: TwseStockClient | None = None,
        tpex_index: TpexIndexClient | None = None,
        tpex_stock: TpexStockClient | None = None,
        shioaji_gateway: Any | None = None,
        local_data_dir: Path | None = None,
    ):
        self.repo = repo
        self.finmind = finmind or FinMindClient()
        self.twse_index = twse_index or TwseIndexClient()
        self.twse_stock = twse_stock or TwseStockClient()
        self.tpex_index = tpex_index or TpexIndexClient()
        self.tpex_stock = tpex_stock or TpexStockClient()
        self.shioaji_gateway = shioaji_gateway
        self.local_data_dir = local_data_dir or (Path(__file__).resolve().parents[2] / "data" / "stock_data")

    def sync_0050_universe(self, snapshot_date: date | None = None, *, prefer_remote: bool = True) -> dict:
        snapshot_date = snapshot_date or datetime.now().date()
        result: dict[str, Any] = {}
        remote_error = ""
        if prefer_remote:
            try:
                result = sync_yuanta_0050_symbols()
            except Exception as exc:  # noqa: BLE001
                remote_error = str(exc)
        symbols = list(result.get("symbols") or load_0050_symbols())
        profiles = [
            {
                "symbol": str(item.get("symbol", "")).strip(),
                "name": str(item.get("name", "") or "").strip(),
                "market": "TWSE",
                "industry": "",
                "is_0050": True,
            }
            for item in result.get("constituents", [])
            if str(item.get("symbol", "")).strip()
        ]
        inst_rows = (
            self.repo.upsert_instruments(profiles, market="TWSE", is_0050=True)
            if profiles
            else self.repo.upsert_instruments(symbols, market="TW", is_0050=True)
        )
        snapshot_rows = self.repo.replace_0050_snapshot(snapshot_date, symbols)
        index_cache = self._write_index_constituents_cache(
            index_key="tw0050",
            constituents=result.get("constituents", []),
            symbols=symbols,
            source=str(result.get("source") or "yuanta_0050_pcf"),
        )
        self.repo.add_sync_job("sync_0050_universe", status="ok", rows_upserted=snapshot_rows)
        return {
            "source": result.get("source", "local_0050_cache_after_remote_error" if remote_error else "local_0050_cache"),
            "ticker": result.get("ticker", "0050"),
            "trade_date": result.get("trade_date", ""),
            "update_time": result.get("update_time", ""),
            "snapshot_date": snapshot_date.isoformat(),
            "symbols": len(symbols),
            "symbols_total": len(symbols),
            "file": result.get("file", ""),
            "remote_error": remote_error,
            "instruments_upserted": inst_rows,
            "snapshot_rows": snapshot_rows,
            "index_constituents_cache": index_cache,
        }

    def sync_twse_tpex_universe(self) -> dict:
        rows = self.finmind.fetch_stock_info()
        twse_profiles = [
            {
                "symbol": str(item.get("symbol", "")).strip(),
                "name": str(item.get("name", "") or "").strip(),
                "market": "TWSE",
                "industry": str(item.get("industry", "") or item.get("industry_category", "") or "").strip(),
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
                "industry": str(item.get("industry", "") or item.get("industry_category", "") or "").strip(),
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

    def sync_mid100_universe(self) -> dict:
        result = sync_yuanta_mid100_symbols()
        profiles = [
            {
                "symbol": str(item.get("symbol", "")).strip(),
                "name": str(item.get("name", "") or "").strip(),
                "market": "TWSE",
                "industry": "",
                "is_0050": False,
            }
            for item in result.get("constituents", [])
            if str(item.get("symbol", "")).strip()
        ]
        upserted = self.repo.upsert_instruments(profiles, market="TWSE", is_0050=False) if profiles else 0
        symbols = [row["symbol"] for row in profiles] or [str(symbol).strip() for symbol in result.get("symbols", []) if str(symbol).strip()]
        index_cache = self._write_index_constituents_cache(
            index_key="tw_mid100",
            constituents=result.get("constituents", []),
            symbols=symbols,
            source=str(result.get("source") or "yuanta_0051_pcf"),
        )
        self.repo.add_sync_job("sync_mid100_universe", status="ok", rows_upserted=int(result.get("count", 0) or 0))
        return {
            "source": result.get("source", "yuanta_0051_pcf"),
            "ticker": result.get("ticker", "0051"),
            "trade_date": result.get("trade_date", ""),
            "update_time": result.get("update_time", ""),
            "symbols_total": int(result.get("count", 0) or 0),
            "file": result.get("file", ""),
            "instruments_upserted": upserted,
            "index_constituents_cache": index_cache,
        }

    def _write_index_constituents_cache(
        self,
        *,
        index_key: str,
        constituents: Iterable[dict] | None = None,
        symbols: Iterable[str] | None = None,
        source: str = "sync_service",
    ) -> dict:
        rows: list[dict] = []
        for item in constituents or []:
            symbol = str(item.get("symbol") or "").strip()
            if symbol:
                rows.append(
                    {
                        "symbol": symbol,
                        "name": str(item.get("name") or "").strip(),
                        "source": str(item.get("source") or source),
                    }
                )
        if not rows:
            rows = [
                {"symbol": str(symbol).strip(), "name": "", "source": source}
                for symbol in (symbols or [])
                if str(symbol).strip()
            ]
        if not rows:
            return {"index_key": index_key, "count": 0, "error": "no_symbols"}
        try:
            return write_index_constituents_cache(rows, index_key=index_key, source=source)
        except Exception as exc:  # noqa: BLE001
            return {"index_key": index_key, "count": 0, "error": str(exc)}

    def sync_daily_bars(
        self,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        *,
        prefer_official_bulk: bool = False,
    ) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        fallback_rows = 0
        official_fallback_rows = 0
        official_fallback_symbols: list[str] = []
        failed: list[dict] = []
        official_bulk = (
            self._load_official_daily_bars_by_symbol(symbols=symbols, start_date=start_date, end_date=end_date)
            if prefer_official_bulk
            else {}
        )
        for symbol in symbols:
            if symbol in official_bulk:
                rows = self.repo.upsert_daily_bars(symbol, official_bulk[symbol])
                total_rows += rows
                official_fallback_rows += rows
                official_fallback_symbols.append(symbol)
                continue
            source_error: Exception | None = None
            try:
                bars = self.finmind.fetch_daily_bars(symbol, start_date, end_date)
            except Exception as exc:  # noqa: BLE001
                source_error = exc
                bars = []
            if bars:
                rows = self.repo.upsert_daily_bars(symbol, bars)
                total_rows += rows
                continue
            local_bars = self._load_local_parquet_daily_bars(symbol=symbol, start_date=start_date, end_date=end_date)
            if local_bars:
                rows = self.repo.upsert_daily_bars(symbol, local_bars)
                total_rows += rows
                fallback_rows += rows
                continue
            official_bars = self._load_official_daily_bars(symbol=symbol, start_date=start_date, end_date=end_date)
            if official_bars:
                rows = self.repo.upsert_daily_bars(symbol, official_bars)
                total_rows += rows
                official_fallback_rows += rows
                official_fallback_symbols.append(symbol)
                continue
            failed.append(
                self._failure(
                    "daily_bars",
                    source_error or NoDataError(f"no daily bars returned for {symbol}"),
                    symbol=symbol,
                )
            )

        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
            "fallback_rows": fallback_rows,
            "official_fallback_rows": official_fallback_rows,
            "official_fallback_symbols": official_fallback_symbols[:20],
            "prefer_official_bulk": bool(prefer_official_bulk),
            "api_mode": "official_bulk" if prefer_official_bulk and official_bulk else "",
        }
        return self._finalize_sync_result("sync_daily_bars", result, failed, start_date=start_date, end_date=end_date)

    def sync_institutional_chip(
        self,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        *,
        prefer_official_bulk: bool = False,
    ) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        official_fallback_rows = 0
        official_fallback_symbols: list[str] = []
        failed: list[dict] = []
        official_bulk = (
            self._load_official_institutional_chip_by_symbol(symbols=symbols, start_date=start_date, end_date=end_date)
            if prefer_official_bulk
            else {}
        )
        for symbol in symbols:
            if symbol in official_bulk:
                upserted = self.repo.upsert_institutional_chip(symbol, official_bulk[symbol])
                total_rows += upserted
                official_fallback_rows += upserted
                official_fallback_symbols.append(symbol)
                continue
            source_error: Exception | None = None
            try:
                rows = self.finmind.fetch_institutional_chip(symbol, start_date, end_date)
            except Exception as exc:  # noqa: BLE001
                source_error = exc
                rows = []
            if not rows:
                rows = self._load_official_institutional_chip(symbol=symbol, start_date=start_date, end_date=end_date)
                if rows:
                    upserted = self.repo.upsert_institutional_chip(symbol, rows)
                    total_rows += upserted
                    official_fallback_rows += upserted
                    official_fallback_symbols.append(symbol)
                    continue
            if not rows:
                failed.append(
                    self._failure(
                        "institutional_chip",
                        source_error or NoDataError(f"no institutional chip rows returned for {symbol}"),
                        symbol=symbol,
                    )
                )
                continue
            upserted = self.repo.upsert_institutional_chip(symbol, rows)
            total_rows += upserted
        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
            "official_fallback_rows": official_fallback_rows,
            "official_fallback_symbols": official_fallback_symbols[:20],
            "prefer_official_bulk": bool(prefer_official_bulk),
            "api_mode": "official_bulk" if prefer_official_bulk and official_bulk else "",
        }
        return self._finalize_sync_result("sync_institutional_chip", result, failed, start_date=start_date, end_date=end_date)

    def sync_broker_agg_chip(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        failed: list[dict] = []
        for symbol in symbols:
            try:
                rows = self.finmind.fetch_broker_agg_chip(symbol, start_date, end_date)
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("broker_agg_chip", exc, symbol=symbol))
                continue
            if not rows:
                failed.append(self._failure("broker_agg_chip", NoDataError(f"no broker agg rows returned for {symbol}"), symbol=symbol))
                continue
            upserted = self.repo.upsert_broker_agg(symbol, rows)
            total_rows += upserted
        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
        }
        return self._finalize_sync_result("sync_broker_agg_chip", result, failed, start_date=start_date, end_date=end_date)

    def sync_twse_industry_indices(self, start_date: date, end_date: date) -> dict:
        total_rows = 0
        failed: list[dict] = []
        for day in self._iter_weekdays(start_date, end_date):
            try:
                rows = self.twse_index.fetch_industry_index_bars(day)
                total_rows += self.repo.upsert_industry_index_bars(rows)
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("twse_industry_indices", exc, date=day.isoformat()))
        result = {
            "rows_upserted": total_rows,
            "date_count": len(list(self._iter_weekdays(start_date, end_date))),
            "source": "twse_mi_index",
        }
        return self._finalize_sync_result("sync_twse_industry_indices", result, failed, start_date=start_date, end_date=end_date)

    def sync_tpex_market_index(self, start_date: date, end_date: date) -> dict:
        total_rows = 0
        failed: list[dict] = []
        for month in self._iter_months(start_date, end_date):
            try:
                rows = [
                    row
                    for row in self.tpex_index.fetch_market_index_bars(month)
                    if start_date <= row.get("date", start_date) <= end_date
                ]
                total_rows += self.repo.upsert_industry_index_bars(rows)
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("tpex_market_index", exc, month=month.strftime("%Y-%m")))
        result = {
            "rows_upserted": total_rows,
            "month_count": len(list(self._iter_months(start_date, end_date))),
            "source": "tpex_index_info",
        }
        return self._finalize_sync_result("sync_tpex_market_index", result, failed, start_date=start_date, end_date=end_date)

    def sync_tpex_sector_indices(self, start_date: date, end_date: date) -> dict:
        total_rows = 0
        failed: list[dict] = []
        for month in self._iter_months(start_date, end_date):
            try:
                rows = [
                    row
                    for row in self.tpex_index.fetch_sector_index_bars(month)
                    if start_date <= row.get("date", start_date) <= end_date
                ]
                total_rows += self.repo.upsert_industry_index_bars(rows)
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("tpex_sector_indices", exc, month=month.strftime("%Y-%m")))
        result = {
            "rows_upserted": total_rows,
            "month_count": len(list(self._iter_months(start_date, end_date))),
            "source": "tpex_sector_index",
        }
        return self._finalize_sync_result("sync_tpex_sector_indices", result, failed, start_date=start_date, end_date=end_date)

    def sync_external_index_context(self, start_date: date, end_date: date) -> dict:
        bundle = {
            "twse_industry_indices": self.sync_twse_industry_indices(start_date=start_date, end_date=end_date),
            "tpex_sector_indices": self.sync_tpex_sector_indices(start_date=start_date, end_date=end_date),
            "tpex_market_index": self.sync_tpex_market_index(start_date=start_date, end_date=end_date),
        }
        summary = self._bundle_summary(bundle)
        total_rows = summary["rows_upserted_total"]
        self.repo.add_sync_job(
            "sync_external_index_context",
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

    def sync_disposition_periods(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        requested_symbols = symbols
        symbols = self._resolve_symbols(symbols)
        symbol_set = set(symbols)
        total_rows = 0
        official_rows = 0
        official_fallback_rows = 0
        official_symbols: list[str] = []
        official_fallback_symbols: list[str] = []
        failed: list[dict] = []
        api_mode = "per_symbol"

        if requested_symbols is None or len(symbols) > 20:
            rows, official_failed = self._load_official_disposition_periods(
                start_date=start_date,
                end_date=end_date,
                allowed_symbols=symbol_set,
            )
            if rows or not official_failed:
                total_rows += self._upsert_disposition_rows(rows, allowed_symbols=symbol_set)
                official_rows = len(rows)
                official_symbols = sorted({str(row.get("symbol") or "").strip().upper() for row in rows if row.get("symbol")})[:20]
                result = {
                    "symbols_total": len(symbols),
                    "rows_upserted": total_rows,
                    "official_rows": official_rows,
                    "official_symbols": official_symbols,
                    "official_fallback_rows": 0,
                    "official_fallback_symbols": [],
                    "api_mode": "official_by_range",
                }
                return self._finalize_sync_result("sync_disposition_periods", result, [], start_date=start_date, end_date=end_date)
            failed.extend(official_failed)
            api_mode = "official_failed"

        missing_symbols: list[str] = []
        for symbol in symbols:
            try:
                rows = self.finmind.fetch_disposition_periods(symbol, start_date, end_date)
            except Exception as exc:  # noqa: BLE001
                rows = []
                failed.append(self._failure("disposition_periods", exc, symbol=symbol))
            if rows:
                upserted = self.repo.upsert_disposition_periods(symbol, rows)
                total_rows += upserted
            else:
                missing_symbols.append(symbol)

        if missing_symbols:
            rows, official_failed = self._load_official_disposition_periods(
                start_date=start_date,
                end_date=end_date,
                allowed_symbols=set(missing_symbols),
            )
            if rows or not official_failed:
                upserted = self._upsert_disposition_rows(rows, allowed_symbols=set(missing_symbols))
                total_rows += upserted
                official_fallback_rows = len(rows)
                official_fallback_symbols = sorted({str(row.get("symbol") or "").strip().upper() for row in rows if row.get("symbol")})[:20]
                api_mode = "mixed"
                if rows or not official_failed:
                    failed = [
                        item
                        for item in failed
                        if str(item.get("symbol") or "").strip().upper() not in set(missing_symbols)
                    ]
            else:
                failed.extend(official_failed)
        result = {
            "symbols_total": len(symbols),
            "rows_upserted": total_rows,
            "official_rows": official_rows,
            "official_symbols": official_symbols,
            "official_fallback_rows": official_fallback_rows,
            "official_fallback_symbols": official_fallback_symbols,
            "api_mode": api_mode,
        }
        return self._finalize_sync_result("sync_disposition_periods", result, failed, start_date=start_date, end_date=end_date)

    def sync_attention_stocks(self, trade_date: date | None = None) -> dict:
        sync_date = trade_date or datetime.now().date()
        failed: list[dict] = []
        rows: list[dict] = []
        source_mode = "official"
        official_fallback_rows = 0
        official_symbols: list[str] = []
        gateway = self.shioaji_gateway
        own_gateway = False

        if gateway is not None:
            source_mode = "shioaji_notice"
            try:
                rows = gateway.fetch_attention_stocks(trade_date=sync_date)
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("attention_stocks", exc))
                rows = []

        if gateway is None or not rows:
            official_rows, official_failed = self._load_official_attention_stocks(trade_date=sync_date)
            if official_rows or not official_failed:
                if failed:
                    official_fallback_rows = len(official_rows)
                    source_mode = "official_fallback"
                    failed = []
                else:
                    source_mode = "official"
                rows = official_rows
            else:
                failed.extend(official_failed)

        if not rows and gateway is None and failed:
            try:
                from app.broker.shioaji_gateway import ShioajiConfig, ShioajiGateway

                gateway = ShioajiGateway(ShioajiConfig(simulation=True))
                own_gateway = True
                rows = gateway.fetch_attention_stocks(trade_date=sync_date)
                source_mode = "shioaji_notice_fallback"
                failed = []
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure("attention_stocks", exc))

        try:
            upserted = self.repo.upsert_attention_stocks(rows)
            official_symbols = sorted(
                {str(row.get("symbol") or "").strip().upper() for row in rows if str(row.get("source") or "").startswith(("twse", "tpex"))}
            )[:20]
            result = {
                "trade_date": sync_date.isoformat(),
                "rows_fetched": len(rows),
                "rows_upserted": upserted,
                "official_fallback_rows": official_fallback_rows,
                "official_symbols": official_symbols,
                "api_mode": source_mode,
            }
        finally:
            if own_gateway and gateway is not None:
                api = getattr(gateway, "api", None)
                logout = getattr(api, "logout", None)
                if callable(logout):
                    try:
                        logout()
                    except Exception:
                        pass
        return self._finalize_sync_result("sync_attention_stocks", result, failed, start_date=sync_date, end_date=sync_date)

    def sync_monthly_revenues(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> dict:
        symbols = self._resolve_symbols(symbols)
        total_rows = 0
        failed: list[dict] = []
        for symbol in symbols:
            try:
                rows = self.finmind.fetch_monthly_revenue(symbol, start_date, end_date)
                if hasattr(self.repo, "record_fundamental_sources"):
                    total_rows += self.repo.record_fundamental_sources(symbol, rows, kind="monthly_revenue")
                    self.repo.upsert_monthly_revenues(symbol, [item for item in rows if item.get("announce_date") is not None])
                else:
                    total_rows += self.repo.upsert_monthly_revenues(symbol, [item for item in rows if item.get("announce_date") is not None])
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
                if hasattr(self.repo, "record_fundamental_sources"):
                    total_rows += self.repo.record_fundamental_sources(symbol, rows, kind="financial_summary")
                    self.repo.upsert_financial_statement_summaries(symbol, [item for item in rows if item.get("announce_date") is not None])
                else:
                    total_rows += self.repo.upsert_financial_statement_summaries(symbol, [item for item in rows if item.get("announce_date") is not None])
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
        include_attention: bool = False,
        include_fundamentals: bool = False,
        include_news: bool = False,
        include_industry_indices: bool = False,
        prefer_official_bulk: bool = False,
    ) -> dict:
        bundle: dict[str, dict] = {}
        if include_bars:
            bundle["daily_bars"] = self.sync_daily_bars(
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
                prefer_official_bulk=prefer_official_bulk,
            )
        if include_institutional:
            bundle["institutional_chip"] = self.sync_institutional_chip(
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
                prefer_official_bulk=prefer_official_bulk,
            )
        if include_broker_agg:
            bundle["broker_agg_chip"] = self.sync_broker_agg_chip(start_date=start_date, end_date=end_date, symbols=symbols)
        if include_disposition:
            bundle["disposition_periods"] = self.sync_disposition_periods(start_date=start_date, end_date=end_date, symbols=symbols)
        if include_attention:
            bundle["attention_stocks"] = self.sync_attention_stocks(trade_date=end_date)
        if include_fundamentals:
            bundle["monthly_revenues"] = self.sync_monthly_revenues(start_date=start_date, end_date=end_date, symbols=symbols)
            bundle["financial_statement_summaries"] = self.sync_financial_statement_summaries(
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
            )
        if include_news:
            bundle["news_events"] = self.sync_news_events(start_date=start_date, end_date=end_date, symbols=symbols)
        if include_industry_indices:
            bundle["external_index_context"] = self.sync_external_index_context(start_date=start_date, end_date=end_date)
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
        failed_symbol_scopes = [
            {"symbol": str(item.get("symbol") or "").strip().upper(), "scope": str(item.get("scope") or "").strip()}
            for item in failed
            if str(item.get("symbol") or "").strip() and str(item.get("scope") or "").strip()
        ]
        payload = {
            **result,
            "failed_count": failed_count,
            "failed_samples": failed[:10],
            "failed_symbol_scopes": failed_symbol_scopes[:200],
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
                    "failed_symbol_scopes": failed_symbol_scopes[:200],
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
        rows_upserted_total = int(
            sum(
                int((payload or {}).get("rows_upserted") or (payload or {}).get("rows_upserted_total") or 0)
                for payload in bundle.values()
            )
        )
        failed_samples: list[dict] = []
        failed_symbol_scopes: list[dict] = []
        for name, payload in bundle.items():
            for sample in (payload or {}).get("failed_samples", []) if isinstance((payload or {}).get("failed_samples"), list) else []:
                if isinstance(sample, dict):
                    failed_samples.append({"component": name, **sample})
            for item in (payload or {}).get("failed_symbol_scopes", []) if isinstance((payload or {}).get("failed_symbol_scopes"), list) else []:
                if isinstance(item, dict):
                    failed_symbol_scopes.append({"component": name, **item})
        partial_failure = failed_count_total > 0
        return {
            "rows_upserted_total": rows_upserted_total,
            "failed_count_total": failed_count_total,
            "degraded_count": degraded_count,
            "partial_failure": partial_failure,
            "failed_samples": failed_samples[:20],
            "failed_symbol_scopes": failed_symbol_scopes[:300],
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
        official_fallback_rows = int(result.get("official_fallback_rows") or 0)
        rows_upserted = int(result.get("rows_upserted") or 0)
        official_bulk_mode = str(result.get("api_mode") or "") == "official_bulk"
        if failed_count > 0:
            status = "partial"
        elif degraded_count > 0 or fallback_rows > 0 or (official_fallback_rows > 0 and not official_bulk_mode):
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
            "official_fallback_rows": official_fallback_rows,
            "prefer_official_bulk": bool(result.get("prefer_official_bulk")),
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
    def _iter_months(start_date: date, end_date: date) -> Iterable[date]:
        cursor = min(start_date, end_date).replace(day=1)
        end = max(start_date, end_date).replace(day=1)
        while cursor <= end:
            yield cursor
            year = cursor.year + (1 if cursor.month == 12 else 0)
            month = 1 if cursor.month == 12 else cursor.month + 1
            cursor = date(year, month, 1)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        code = str(symbol).strip().upper()
        for suffix in (".TW", ".TWO"):
            if code.endswith(suffix):
                code = code[: -len(suffix)]
        return code

    def _instrument_market(self, symbol: str) -> str:
        method = getattr(self.repo, "get_instrument_market", None)
        if not callable(method):
            return ""
        try:
            return str(method(symbol) or "").strip().upper()
        except Exception:
            return ""

    def _official_stock_clients_for_symbol(self, symbol: str) -> list[object]:
        market = self._instrument_market(symbol)
        if market == "TWSE":
            return [self.twse_stock]
        if market in {"TPEX", "TWO", "OTC"}:
            return [self.tpex_stock]
        return [self.twse_stock, self.tpex_stock]

    def _official_bulk_client_symbol_groups(self, symbols: list[str]) -> list[tuple[object, set[str]]]:
        allow = {str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()}
        if not allow:
            return []

        twse_symbols: set[str] = set()
        tpex_symbols: set[str] = set()
        unknown_symbols: set[str] = set()
        for symbol in allow:
            market = self._instrument_market(symbol)
            if market == "TWSE":
                twse_symbols.add(symbol)
            elif market in {"TPEX", "TWO", "OTC"}:
                tpex_symbols.add(symbol)
            else:
                unknown_symbols.add(symbol)

        if unknown_symbols:
            twse_symbols.update(unknown_symbols)
            tpex_symbols.update(unknown_symbols)

        groups: list[tuple[object, set[str]]] = []
        if twse_symbols:
            groups.append((self.twse_stock, twse_symbols))
        if tpex_symbols:
            groups.append((self.tpex_stock, tpex_symbols))
        return groups

    def _load_official_daily_bars(self, symbol: str, start_date: date, end_date: date) -> list[dict]:
        for client in self._official_stock_clients_for_symbol(symbol):
            try:
                rows = client.fetch_daily_bars(symbol, start_date, end_date)
            except Exception:
                rows = []
            if rows:
                return rows
        return []

    def _load_official_daily_bars_by_symbol(self, *, symbols: list[str], start_date: date, end_date: date) -> dict[str, list[dict]]:
        allow = {str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()}
        rows_by_symbol_date: dict[str, dict[date, dict]] = {symbol: {} for symbol in allow}
        client_groups = self._official_bulk_client_symbol_groups(symbols)
        for day in self._iter_weekdays(start_date, end_date):
            for client, client_symbols in client_groups:
                fetcher = getattr(client, "fetch_daily_bars_by_date", None)
                if not callable(fetcher):
                    continue
                try:
                    fetched = fetcher(day, symbols=client_symbols)
                except Exception:
                    continue
                for row in fetched or []:
                    symbol = str(row.get("symbol") or "").strip().upper()
                    row_date = row.get("date")
                    if symbol in allow and isinstance(row_date, date):
                        rows_by_symbol_date.setdefault(symbol, {})[row_date] = row
        return {
            symbol: [rows_by_date[key] for key in sorted(rows_by_date)]
            for symbol, rows_by_date in rows_by_symbol_date.items()
            if rows_by_date
        }

    def _load_official_institutional_chip(self, symbol: str, start_date: date, end_date: date) -> list[dict]:
        for client in self._official_stock_clients_for_symbol(symbol):
            if not hasattr(client, "fetch_institutional_chip"):
                continue
            try:
                rows = client.fetch_institutional_chip(symbol, start_date, end_date)
            except Exception:
                rows = []
            if rows:
                return rows
        return []

    def _load_official_institutional_chip_by_symbol(self, *, symbols: list[str], start_date: date, end_date: date) -> dict[str, list[dict]]:
        allow = {str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()}
        rows_by_symbol_date: dict[str, dict[date, dict]] = {symbol: {} for symbol in allow}
        client_groups = self._official_bulk_client_symbol_groups(symbols)
        for day in self._iter_weekdays(start_date, end_date):
            for client, client_symbols in client_groups:
                fetcher = getattr(client, "fetch_institutional_chip_by_date", None)
                if not callable(fetcher):
                    continue
                try:
                    fetched = fetcher(day, symbols=client_symbols)
                except Exception:
                    continue
                for row in fetched or []:
                    symbol = str(row.get("symbol") or "").strip().upper()
                    row_date = row.get("date")
                    if symbol in allow and isinstance(row_date, date):
                        rows_by_symbol_date.setdefault(symbol, {})[row_date] = row
        return {
            symbol: [rows_by_date[key] for key in sorted(rows_by_date)]
            for symbol, rows_by_date in rows_by_symbol_date.items()
            if rows_by_date
        }

    def _load_official_attention_stocks(self, trade_date: date) -> tuple[list[dict], list[dict]]:
        rows: list[dict] = []
        failed: list[dict] = []
        for source_name, client in (("twse_notice", self.twse_stock), ("tpex_attention", self.tpex_stock)):
            fetcher = getattr(client, "fetch_attention_stocks", None)
            if not callable(fetcher):
                continue
            try:
                rows.extend(fetcher(trade_date))
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure(source_name, exc, date=trade_date.isoformat()))
        return rows, failed

    def _load_official_disposition_periods(
        self,
        *,
        start_date: date,
        end_date: date,
        allowed_symbols: set[str] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        rows: list[dict] = []
        failed: list[dict] = []
        allow = allowed_symbols or set()
        for source_name, client in (("twse_punish", self.twse_stock), ("tpex_disposal", self.tpex_stock)):
            fetcher = getattr(client, "fetch_disposition_periods", None)
            if not callable(fetcher):
                continue
            try:
                fetched = fetcher(start_date, end_date)
                rows.extend(row for row in fetched if not allow or str(row.get("symbol") or "").strip().upper() in allow)
            except Exception as exc:  # noqa: BLE001
                failed.append(self._failure(source_name, exc, start_date=start_date.isoformat(), end_date=end_date.isoformat()))
        return rows, failed

    def _upsert_disposition_rows(self, rows: list[dict], allowed_symbols: set[str] | None = None) -> int:
        total_rows = 0
        for symbol, payload in self._group_rows_by_symbol(rows, allowed_symbols=allowed_symbols).items():
            total_rows += self.repo.upsert_disposition_periods(symbol, payload)
        return total_rows

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
