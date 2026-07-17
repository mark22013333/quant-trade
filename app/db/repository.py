from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import and_, delete, distinct, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AttentionStock,
    AdvisorDecisionRecord,
    AdvisorObservationRecord,
    BrokerAggDaily,
    CandidateSnapshot,
    DailyRadarSnapshot,
    DailyBar,
    DispositionPeriod,
    FeatureSnapshot,
    FinancialStatementSummary,
    HoldingSharesPerDaily,
    IndustryIndexBar,
    InstitutionalChipDaily,
    Instrument,
    MonthlyRevenue,
    NewsEvent,
    OrderPreviewRecord,
    ShareholdingDaily,
    PromotionGateRecord,
    ReconciliationRecord,
    SyncJob,
    TradingExecutionRecord,
    Universe0050Snapshot,
    WatchPoolEntry,
)
from app.data.tpex_index_client import TPEX_MARKET_CONTEXT_NAME, TPEX_MARKET_INDEX_NAME, TPEX_SECTOR_INDEX_SOURCE
from app.data.twse_index_client import twse_index_name_for_industry
from app.security import redact_sensitive
from app.services.daily_radar_risk_quality import enrich_risk_quality
from app.services.market_context import classify_tw_symbol_group
from app.signals.registry import radar_outcome_bucket_for_tag


class TradingRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_instruments(self, symbols: Iterable[str | dict], market: str = "TW", is_0050: bool = True) -> int:
        def _find_pending(symbol: str) -> Instrument | None:
            for item in self.session.new:
                if isinstance(item, Instrument) and item.symbol == symbol:
                    return item
            return None

        count = 0
        for raw in symbols:
            if isinstance(raw, dict):
                symbol = str(raw.get("symbol", "")).strip()
                raw_name = str(raw.get("name", "") or "").strip()
                raw_industry = str(raw.get("industry", "") or raw.get("industry_category", "") or "").strip()
                raw_market = str(raw.get("market", "") or "").strip().upper()
                item_market = raw_market or str(market or "TW").strip().upper() or "TW"
                item_is_0050 = bool(raw.get("is_0050", is_0050))
            else:
                symbol = str(raw).strip()
                raw_name = ""
                raw_industry = ""
                item_market = str(market or "TW").strip().upper() or "TW"
                item_is_0050 = bool(is_0050)
            if not symbol:
                continue
            row = self.session.get(Instrument, symbol)
            if row is None:
                row = _find_pending(symbol)
            if row is None:
                row = Instrument(
                    symbol=symbol,
                    name=raw_name,
                    market=item_market,
                    industry=raw_industry,
                    is_0050=item_is_0050,
                    is_active=True,
                )
                self.session.add(row)
            else:
                row.market = item_market
                row.is_0050 = bool(row.is_0050 or item_is_0050)
                if raw_name:
                    row.name = raw_name
                if raw_industry:
                    row.industry = raw_industry
                row.is_active = True
            count += 1
        return count

    def replace_0050_snapshot(self, snapshot_date: date, symbols: Iterable[str]) -> int:
        self.session.execute(delete(Universe0050Snapshot).where(Universe0050Snapshot.snapshot_date == snapshot_date))
        rows = 0
        for raw in symbols:
            symbol = str(raw).strip()
            if not symbol:
                continue
            self.session.add(Universe0050Snapshot(snapshot_date=snapshot_date, symbol=symbol))
            rows += 1
        return rows

    def get_latest_0050_symbols(self) -> list[str]:
        max_date_stmt = select(Universe0050Snapshot.snapshot_date).order_by(Universe0050Snapshot.snapshot_date.desc()).limit(1)
        latest_date = self.session.execute(max_date_stmt).scalar_one_or_none()
        if latest_date is None:
            return []
        stmt = (
            select(Universe0050Snapshot.symbol)
            .where(Universe0050Snapshot.snapshot_date == latest_date)
            .order_by(Universe0050Snapshot.symbol.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_instrument_name(self, symbol: str) -> str:
        row = self.session.get(Instrument, symbol)
        if row is None:
            return ""
        return str(row.name or "").strip()

    def get_instrument_industry(self, symbol: str) -> str:
        row = self.session.get(Instrument, symbol)
        if row is None:
            return ""
        return str(row.industry or "").strip()

    def get_instrument_market(self, symbol: str) -> str:
        row = self.session.get(Instrument, symbol)
        if row is None:
            return ""
        return str(row.market or "").strip().upper()

    def get_symbols_by_markets(self, markets: Iterable[str]) -> list[str]:
        market_values = {str(item or "").strip().upper() for item in markets if str(item or "").strip()}
        if not market_values:
            return []
        stmt = (
            select(Instrument.symbol)
            .where(func.upper(Instrument.market).in_(market_values), Instrument.is_active.is_(True))
            .order_by(Instrument.symbol.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_instrument_industry_coverage(
        self,
        *,
        symbols: Iterable[str] | None = None,
        markets: Iterable[str] | None = None,
        sample_limit: int = 12,
    ) -> dict:
        self.session.flush()
        symbol_values = {str(item or "").strip().upper() for item in (symbols or []) if str(item or "").strip()}
        market_values = {str(item or "").strip().upper() for item in (markets or []) if str(item or "").strip()}
        stmt = select(Instrument.symbol, Instrument.market, Instrument.industry).where(Instrument.is_active.is_(True))
        if symbol_values:
            stmt = stmt.where(Instrument.symbol.in_(symbol_values))
        if market_values:
            stmt = stmt.where(func.upper(Instrument.market).in_(market_values))

        market_stats: dict[str, dict] = {}
        missing_symbols: list[str] = []
        official_count = 0
        total = 0
        for symbol, market, industry in self.session.execute(stmt.order_by(Instrument.symbol.asc())).all():
            symbol_text = str(symbol or "").strip().upper()
            market_text = str(market or "TW").strip().upper() or "TW"
            industry_text = str(industry or "").strip()
            entry = market_stats.setdefault(
                market_text,
                {
                    "market": market_text,
                    "label": market_text,
                    "total_count": 0,
                    "official_count": 0,
                    "missing_count": 0,
                    "missing_symbols": [],
                },
            )
            entry["total_count"] += 1
            total += 1
            if industry_text:
                entry["official_count"] += 1
                official_count += 1
            else:
                entry["missing_count"] += 1
                if len(entry["missing_symbols"]) < max(1, int(sample_limit)):
                    entry["missing_symbols"].append(symbol_text)
                if len(missing_symbols) < max(1, int(sample_limit)):
                    missing_symbols.append(symbol_text)

        def pct(count: int, base: int) -> float:
            return round((float(count) / float(base) * 100.0), 1) if base else 0.0

        for entry in market_stats.values():
            entry["coverage"] = (entry["official_count"] / entry["total_count"]) if entry["total_count"] else None
            entry["coverage_pct"] = pct(entry["official_count"], entry["total_count"])
            entry["label"] = {
                "TWSE": "上市",
                "TPEX": "上櫃",
                "TW": "台股",
            }.get(str(entry["market"]), str(entry["market"]))

        coverage = (official_count / total) if total else None
        status = "danger" if total <= 0 else "ok" if float(coverage or 0.0) >= 0.85 else "warn"
        return {
            "status": status,
            "scope": "active_instruments",
            "active_symbols": total,
            "official_industry_symbols": official_count,
            "missing_industry_count": max(0, total - official_count),
            "coverage": coverage,
            "coverage_pct": pct(official_count, total),
            "missing_symbols": missing_symbols,
            "markets": sorted(market_stats.values(), key=lambda item: str(item["market"])),
            "repair_command": ".venv/bin/python -m app.cli sync-twse-tpex-universe",
            "fallback": "symbol_proxy",
        }

    def get_recent_liquid_symbols(
        self,
        *,
        markets: Iterable[str] | None = None,
        exclude_symbols: Iterable[str] | None = None,
        limit: int = 100,
        lookback_days: int = 90,
        end_date: date | None = None,
        min_bars: int = 5,
    ) -> list[str]:
        latest_date = end_date
        if latest_date is None:
            latest_date = self.session.execute(select(func.max(DailyBar.date))).scalar_one_or_none()
        if latest_date is None:
            return []

        start_date = latest_date - timedelta(days=max(1, int(lookback_days)))
        market_values = {str(item or "").strip().upper() for item in (markets or []) if str(item or "").strip()}
        excluded = {str(item or "").strip().upper() for item in (exclude_symbols or []) if str(item or "").strip()}
        avg_turnover = func.avg(DailyBar.close * DailyBar.volume)
        bar_count = func.count(DailyBar.id)

        stmt = (
            select(DailyBar.symbol, avg_turnover.label("avg_turnover"))
            .join(Instrument, Instrument.symbol == DailyBar.symbol)
            .where(DailyBar.date >= start_date, DailyBar.date <= latest_date, Instrument.is_active.is_(True))
            .group_by(DailyBar.symbol)
            .having(bar_count >= max(1, int(min_bars)))
            .order_by(avg_turnover.desc(), DailyBar.symbol.asc())
            .limit(max(1, int(limit)))
        )
        if market_values:
            stmt = stmt.where(func.upper(Instrument.market).in_(market_values))
        if excluded:
            stmt = stmt.where(~DailyBar.symbol.in_(excluded))
        return [str(row[0]) for row in self.session.execute(stmt).all()]

    def get_recent_liquid_symbols_by_industry(
        self,
        *,
        markets: Iterable[str] | None = None,
        exclude_symbols: Iterable[str] | None = None,
        limit: int = 8,
        per_industry: int = 1,
        lookback_days: int = 90,
        end_date: date | None = None,
        min_bars: int = 5,
    ) -> list[dict]:
        latest_date = end_date
        if latest_date is None:
            latest_date = self.session.execute(select(func.max(DailyBar.date))).scalar_one_or_none()
        if latest_date is None:
            return []

        start_date = latest_date - timedelta(days=max(1, int(lookback_days)))
        market_values = {str(item or "").strip().upper() for item in (markets or []) if str(item or "").strip()}
        excluded = {str(item or "").strip().upper() for item in (exclude_symbols or []) if str(item or "").strip()}
        avg_turnover = func.avg(DailyBar.close * DailyBar.volume)
        bar_count = func.count(DailyBar.id)

        stmt = (
            select(
                DailyBar.symbol,
                Instrument.industry,
                avg_turnover.label("avg_turnover"),
            )
            .join(Instrument, Instrument.symbol == DailyBar.symbol)
            .where(
                DailyBar.date >= start_date,
                DailyBar.date <= latest_date,
                Instrument.is_active.is_(True),
            )
            .group_by(DailyBar.symbol, Instrument.industry)
            .having(bar_count >= max(1, int(min_bars)))
            .order_by(avg_turnover.desc(), DailyBar.symbol.asc())
        )
        if market_values:
            stmt = stmt.where(func.upper(Instrument.market).in_(market_values))
        if excluded:
            stmt = stmt.where(~DailyBar.symbol.in_(excluded))

        grouped: dict[str, list[dict]] = {}
        for symbol, industry, turnover in self.session.execute(stmt).all():
            raw_industry = str(industry or "").strip()
            group = raw_industry or classify_tw_symbol_group(str(symbol))
            if not group:
                continue
            grouped.setdefault(group, []).append(
                {
                    "symbol": str(symbol),
                    "industry": group,
                    "industry_source": "official" if raw_industry else "symbol_proxy",
                    "avg_turnover": float(turnover or 0.0),
                }
            )

        selected: list[dict] = []
        max_per_industry = max(1, int(per_industry or 1))
        max_total = max(1, int(limit or 1))
        industry_names = sorted(grouped)
        for rank in range(max_per_industry):
            for industry in industry_names:
                rows = grouped[industry]
                if rank >= len(rows):
                    continue
                selected.append(rows[rank])
                if len(selected) >= max_total:
                    return selected
        return selected

    def get_research_ready_symbols_by_industry(
        self,
        *,
        markets: Iterable[str] | None = None,
        exclude_symbols: Iterable[str] | None = None,
        limit: int = 80,
        per_industry: int = 4,
        history_lookback_days: int = 240,
        min_history_bars: int = 60,
        chip_lookback_days: int = 45,
        min_chip_days: int = 5,
        freshness_days: int = 5,
        end_date: date | None = None,
    ) -> list[dict]:
        latest_date = end_date
        if latest_date is None:
            latest_date = self.session.execute(select(func.max(DailyBar.date))).scalar_one_or_none()
        if latest_date is None:
            return []

        history_start = latest_date - timedelta(days=max(1, int(history_lookback_days)))
        chip_start = latest_date - timedelta(days=max(1, int(chip_lookback_days)))
        fresh_cutoff = latest_date - timedelta(days=max(1, int(freshness_days)))
        market_values = {str(item or "").strip().upper() for item in (markets or []) if str(item or "").strip()}
        excluded = {str(item or "").strip().upper() for item in (exclude_symbols or []) if str(item or "").strip()}

        bar_stats = (
            select(
                DailyBar.symbol.label("symbol"),
                func.avg(DailyBar.close * DailyBar.volume).label("avg_turnover"),
                func.count(DailyBar.id).label("history_bars"),
                func.max(DailyBar.date).label("latest_bar_date"),
            )
            .where(DailyBar.date >= history_start, DailyBar.date <= latest_date)
            .group_by(DailyBar.symbol)
            .having(func.count(DailyBar.id) >= max(1, int(min_history_bars)))
            .subquery()
        )
        chip_stats = (
            select(
                InstitutionalChipDaily.symbol.label("symbol"),
                func.count(InstitutionalChipDaily.id).label("chip_days"),
                func.max(InstitutionalChipDaily.date).label("latest_chip_date"),
            )
            .where(InstitutionalChipDaily.date >= chip_start, InstitutionalChipDaily.date <= latest_date)
            .group_by(InstitutionalChipDaily.symbol)
            .having(func.count(InstitutionalChipDaily.id) >= max(1, int(min_chip_days)))
            .subquery()
        )
        stmt = (
            select(
                bar_stats.c.symbol,
                Instrument.industry,
                bar_stats.c.avg_turnover,
                bar_stats.c.history_bars,
                bar_stats.c.latest_bar_date,
                chip_stats.c.chip_days,
                chip_stats.c.latest_chip_date,
            )
            .join(Instrument, Instrument.symbol == bar_stats.c.symbol)
            .join(chip_stats, chip_stats.c.symbol == bar_stats.c.symbol)
            .where(
                Instrument.is_active.is_(True),
                bar_stats.c.latest_bar_date >= fresh_cutoff,
                chip_stats.c.latest_chip_date >= fresh_cutoff,
            )
            .order_by(bar_stats.c.avg_turnover.desc(), bar_stats.c.symbol.asc())
        )
        if market_values:
            stmt = stmt.where(func.upper(Instrument.market).in_(market_values))
        if excluded:
            stmt = stmt.where(~bar_stats.c.symbol.in_(excluded))

        grouped: dict[str, list[dict]] = {}
        for symbol, industry, turnover, history_bars, latest_bar, chip_days, latest_chip in self.session.execute(stmt).all():
            raw_industry = str(industry or "").strip()
            group = raw_industry or classify_tw_symbol_group(str(symbol))
            if not group:
                continue
            grouped.setdefault(group, []).append(
                {
                    "symbol": str(symbol),
                    "industry": group,
                    "industry_source": "official" if raw_industry else "symbol_proxy",
                    "avg_turnover": float(turnover or 0.0),
                    "history_bars": int(history_bars or 0),
                    "latest_bar_date": latest_bar.isoformat() if latest_bar else None,
                    "chip_days": int(chip_days or 0),
                    "latest_chip_date": latest_chip.isoformat() if latest_chip else None,
                }
            )

        selected: list[dict] = []
        max_per_industry = max(1, int(per_industry or 1))
        max_total = max(1, int(limit or 1))
        for rank in range(max_per_industry):
            for industry in sorted(grouped):
                rows = grouped[industry]
                if rank >= len(rows):
                    continue
                selected.append(rows[rank])
                if len(selected) >= max_total:
                    return selected
        return selected

    def upsert_industry_index_bars(self, records: list[dict]) -> int:
        normalized: dict[tuple[str, str, date], dict] = {}
        for item in records or []:
            bar_date = self._parse_date(item.get("date"))
            index_name = str(item.get("index_name") or "").strip()
            industry = str(item.get("industry") or "").strip()
            source = str(item.get("source") or "twse_mi_index").strip() or "twse_mi_index"
            close = float(item.get("close") or 0.0)
            if bar_date is None or not index_name or not industry or close <= 0:
                continue
            normalized[(source, index_name, bar_date)] = {
                "source": source,
                "index_name": index_name,
                "industry": industry,
                "date": bar_date,
                "close": close,
                "change": float(item.get("change") or 0.0),
                "change_pct": float(item.get("change_pct") or 0.0),
            }

        rows = 0
        for item in normalized.values():
            row = next(
                (
                    pending
                    for pending in self.session.new
                    if isinstance(pending, IndustryIndexBar)
                    and pending.source == item["source"]
                    and pending.index_name == item["index_name"]
                    and pending.date == item["date"]
                ),
                None,
            )
            if row is None:
                stmt = select(IndustryIndexBar).where(
                    IndustryIndexBar.source == item["source"],
                    IndustryIndexBar.index_name == item["index_name"],
                    IndustryIndexBar.date == item["date"],
                )
                row = self.session.execute(stmt).scalar_one_or_none()
            if row is None:
                row = IndustryIndexBar(
                    source=item["source"],
                    index_name=item["index_name"],
                    industry=item["industry"],
                    date=item["date"],
                    close=item["close"],
                    change=item["change"],
                    change_pct=item["change_pct"],
                )
                self.session.add(row)
            else:
                row.industry = item["industry"]
                row.close = item["close"]
                row.change = item["change"]
                row.change_pct = item["change_pct"]
            rows += 1
        return rows

    def get_industry_index_return_context(
        self,
        *,
        industry: str,
        start_date: date,
        end_date: date,
        min_bars: int = 2,
    ) -> dict:
        group = str(industry or "").strip()
        if not group:
            return {"status": "insufficient", "reason": "industry_required"}
        index_name = twse_index_name_for_industry(group)
        stmt = (
            select(IndustryIndexBar)
            .where(
                IndustryIndexBar.source == "twse_mi_index",
                IndustryIndexBar.date >= start_date,
                IndustryIndexBar.date <= end_date,
            )
            .order_by(IndustryIndexBar.date.asc())
        )
        if index_name:
            stmt = stmt.where((IndustryIndexBar.industry == group) | (IndustryIndexBar.index_name == index_name))
        else:
            stmt = stmt.where(IndustryIndexBar.industry == group)
        bars = list(self.session.execute(stmt).scalars().all())
        if len(bars) < max(2, int(min_bars)):
            return {
                "status": "insufficient",
                "reason": "not_enough_industry_index_bars",
                "industry": group,
                "index_name": index_name,
                "bar_count": len(bars),
            }
        first = bars[0]
        last = bars[-1]
        if float(first.close or 0.0) <= 0:
            return {"status": "insufficient", "reason": "invalid_industry_index_base", "industry": group, "index_name": index_name}
        total_return = (float(last.close) / float(first.close)) - 1.0
        daily_returns = [
            (float(bars[idx].close) / float(bars[idx - 1].close)) - 1.0
            for idx in range(1, len(bars))
            if float(bars[idx - 1].close or 0.0) > 0
        ]
        breadth_source = daily_returns or [float(item.change_pct or 0.0) for item in bars if item.change_pct is not None]
        breadth = sum(1 for value in breadth_source if value > 0) / len(breadth_source) if breadth_source else 0.0
        if total_return > 0.05 and breadth >= 0.55:
            label = "tailwind"
        elif total_return < -0.05 and breadth <= 0.45:
            label = "headwind"
        elif abs(total_return) <= 0.02 or 0.4 <= breadth <= 0.6:
            label = "mixed"
        else:
            label = "neutral"
        return {
            "status": "ok",
            "industry": group,
            "index_name": str(last.index_name or index_name),
            "method": "twse_industry_price_index",
            "source": str(last.source or "twse_mi_index"),
            "start_date": first.date.isoformat(),
            "end_date": last.date.isoformat(),
            "symbol_count": 1,
            "bar_count": len(bars),
            "average_return": float(total_return),
            "breadth": float(breadth),
            "best_return": float(max(daily_returns) if daily_returns else total_return),
            "worst_return": float(min(daily_returns) if daily_returns else total_return),
            "label": label,
            "sample_symbols": [
                {
                    "symbol": str(last.index_name or index_name),
                    "start_date": first.date.isoformat(),
                    "end_date": last.date.isoformat(),
                    "return": float(total_return),
                }
            ],
        }

    def get_tpex_industry_index_return_context(
        self,
        *,
        industry: str,
        start_date: date,
        end_date: date,
        min_bars: int = 2,
    ) -> dict:
        group = str(industry or "").strip()
        if not group:
            return {"status": "insufficient", "reason": "industry_required"}
        stmt = (
            select(IndustryIndexBar)
            .where(
                IndustryIndexBar.source == TPEX_SECTOR_INDEX_SOURCE,
                IndustryIndexBar.industry == group,
                IndustryIndexBar.date >= start_date,
                IndustryIndexBar.date <= end_date,
            )
            .order_by(IndustryIndexBar.date.asc())
        )
        bars = list(self.session.execute(stmt).scalars().all())
        if len(bars) < max(2, int(min_bars)):
            return {
                "status": "insufficient",
                "reason": "not_enough_tpex_industry_index_bars",
                "industry": group,
                "bar_count": len(bars),
            }
        first = bars[0]
        last = bars[-1]
        if float(first.close or 0.0) <= 0:
            return {"status": "insufficient", "reason": "invalid_tpex_industry_index_base", "industry": group}
        total_return = (float(last.close) / float(first.close)) - 1.0
        daily_returns = [
            (float(bars[idx].close) / float(bars[idx - 1].close)) - 1.0
            for idx in range(1, len(bars))
            if float(bars[idx - 1].close or 0.0) > 0
        ]
        breadth_source = daily_returns or [float(item.change_pct or 0.0) for item in bars if item.change_pct is not None]
        breadth = sum(1 for value in breadth_source if value > 0) / len(breadth_source) if breadth_source else 0.0
        if total_return > 0.05 and breadth >= 0.55:
            label = "tailwind"
        elif total_return < -0.05 and breadth <= 0.45:
            label = "headwind"
        elif abs(total_return) <= 0.02 or 0.4 <= breadth <= 0.6:
            label = "mixed"
        else:
            label = "neutral"
        return {
            "status": "ok",
            "industry": group,
            "index_name": str(last.index_name or ""),
            "method": "tpex_sector_price_index",
            "source": TPEX_SECTOR_INDEX_SOURCE,
            "start_date": first.date.isoformat(),
            "end_date": last.date.isoformat(),
            "symbol_count": 1,
            "bar_count": len(bars),
            "average_return": float(total_return),
            "breadth": float(breadth),
            "best_return": float(max(daily_returns) if daily_returns else total_return),
            "worst_return": float(min(daily_returns) if daily_returns else total_return),
            "label": label,
            "sample_symbols": [
                {
                    "symbol": str(last.index_name or ""),
                    "start_date": first.date.isoformat(),
                    "end_date": last.date.isoformat(),
                    "return": float(total_return),
                }
            ],
        }

    def get_market_index_return_context(
        self,
        *,
        market: str,
        start_date: date,
        end_date: date,
        min_bars: int = 2,
    ) -> dict:
        market_value = str(market or "").strip().upper()
        if market_value != "TPEX":
            return {"status": "insufficient", "reason": "unsupported_market_index", "market": market_value}
        stmt = (
            select(IndustryIndexBar)
            .where(
                IndustryIndexBar.source == "tpex_index_info",
                IndustryIndexBar.index_name == TPEX_MARKET_INDEX_NAME,
                IndustryIndexBar.date >= start_date,
                IndustryIndexBar.date <= end_date,
            )
            .order_by(IndustryIndexBar.date.asc())
        )
        bars = list(self.session.execute(stmt).scalars().all())
        if len(bars) < max(2, int(min_bars)):
            return {
                "status": "insufficient",
                "reason": "not_enough_market_index_bars",
                "market": market_value,
                "index_name": TPEX_MARKET_INDEX_NAME,
                "bar_count": len(bars),
            }
        first = bars[0]
        last = bars[-1]
        if float(first.close or 0.0) <= 0:
            return {"status": "insufficient", "reason": "invalid_market_index_base", "market": market_value, "index_name": TPEX_MARKET_INDEX_NAME}
        total_return = (float(last.close) / float(first.close)) - 1.0
        daily_returns = [
            (float(bars[idx].close) / float(bars[idx - 1].close)) - 1.0
            for idx in range(1, len(bars))
            if float(bars[idx - 1].close or 0.0) > 0
        ]
        breadth = sum(1 for value in daily_returns if value > 0) / len(daily_returns) if daily_returns else 0.0
        if total_return > 0.05 and breadth >= 0.55:
            label = "tailwind"
        elif total_return < -0.05 and breadth <= 0.45:
            label = "headwind"
        elif abs(total_return) <= 0.02 or 0.4 <= breadth <= 0.6:
            label = "mixed"
        else:
            label = "neutral"
        return {
            "status": "ok",
            "industry": TPEX_MARKET_CONTEXT_NAME,
            "index_name": TPEX_MARKET_INDEX_NAME,
            "method": "tpex_market_price_index",
            "source": "tpex_index_info",
            "start_date": first.date.isoformat(),
            "end_date": last.date.isoformat(),
            "symbol_count": 1,
            "bar_count": len(bars),
            "average_return": float(total_return),
            "breadth": float(breadth),
            "best_return": float(max(daily_returns) if daily_returns else total_return),
            "worst_return": float(min(daily_returns) if daily_returns else total_return),
            "label": label,
            "sample_symbols": [
                {
                    "symbol": TPEX_MARKET_INDEX_NAME,
                    "start_date": first.date.isoformat(),
                    "end_date": last.date.isoformat(),
                    "return": float(total_return),
                }
            ],
        }

    def get_local_industry_return_context(
        self,
        *,
        industry: str,
        start_date: date,
        end_date: date,
        markets: Iterable[str] | None = None,
        max_symbols: int = 80,
        min_bars: int = 2,
    ) -> dict:
        group = str(industry or "").strip()
        if not group:
            return {"status": "insufficient", "reason": "industry_required"}
        market_values = {str(item or "").strip().upper() for item in (markets or []) if str(item or "").strip()}
        latest_turnover = func.avg(DailyBar.close * DailyBar.volume)
        symbol_stmt = (
            select(DailyBar.symbol, latest_turnover.label("avg_turnover"))
            .join(Instrument, Instrument.symbol == DailyBar.symbol)
            .where(
                Instrument.industry == group,
                Instrument.is_active.is_(True),
                DailyBar.date >= start_date,
                DailyBar.date <= end_date,
            )
            .group_by(DailyBar.symbol)
            .having(func.count(DailyBar.id) >= max(1, int(min_bars)))
            .order_by(latest_turnover.desc(), DailyBar.symbol.asc())
            .limit(max(1, int(max_symbols)))
        )
        if market_values:
            symbol_stmt = symbol_stmt.where(func.upper(Instrument.market).in_(market_values))
        symbols = [str(row[0]) for row in self.session.execute(symbol_stmt).all()]
        if not symbols:
            return {"status": "insufficient", "reason": "no_industry_bars", "industry": group}

        bar_stmt = (
            select(DailyBar.symbol, DailyBar.date, DailyBar.close)
            .where(DailyBar.symbol.in_(symbols), DailyBar.date >= start_date, DailyBar.date <= end_date)
            .order_by(DailyBar.symbol.asc(), DailyBar.date.asc())
        )
        grouped: dict[str, list[tuple[date, float]]] = {}
        for symbol, bar_date, close in self.session.execute(bar_stmt).all():
            price = float(close or 0.0)
            if price <= 0:
                continue
            grouped.setdefault(str(symbol), []).append((bar_date, price))

        rows: list[dict] = []
        for symbol, bars in grouped.items():
            if len(bars) < max(2, int(min_bars)):
                continue
            bars.sort(key=lambda item: item[0])
            first_date, first_close = bars[0]
            last_date, last_close = bars[-1]
            if first_close <= 0:
                continue
            total_return = (last_close / first_close) - 1.0
            rows.append(
                {
                    "symbol": symbol,
                    "start_date": first_date.isoformat(),
                    "end_date": last_date.isoformat(),
                    "return": float(total_return),
                }
            )
        if not rows:
            return {"status": "insufficient", "reason": "not_enough_industry_bars", "industry": group}

        returns = [float(row["return"]) for row in rows]
        breadth = sum(1 for value in returns if value > 0) / len(returns)
        average_return = sum(returns) / len(returns)
        if average_return > 0.05 and breadth >= 0.55:
            label = "tailwind"
        elif average_return < -0.05 and breadth <= 0.45:
            label = "headwind"
        elif abs(average_return) <= 0.02 or 0.4 <= breadth <= 0.6:
            label = "mixed"
        else:
            label = "neutral"
        return {
            "status": "ok",
            "industry": group,
            "method": "local_equal_weight_industry_return",
            "start_date": min(row["start_date"] for row in rows),
            "end_date": max(row["end_date"] for row in rows),
            "symbol_count": int(len(rows)),
            "average_return": float(average_return),
            "breadth": float(breadth),
            "best_return": float(max(returns)),
            "worst_return": float(min(returns)),
            "label": label,
            "sample_symbols": rows[: min(10, len(rows))],
        }

    def get_industry_return_context(
        self,
        *,
        industry: str,
        start_date: date,
        end_date: date,
        markets: Iterable[str] | None = None,
        max_symbols: int = 80,
        min_bars: int = 2,
    ) -> dict:
        group = str(industry or "").strip()
        if not group:
            return {"status": "insufficient", "reason": "industry_required"}
        market_values = {str(item or "").strip().upper() for item in (markets or []) if str(item or "").strip()}
        if not market_values or {"TW", "TWSE"} & market_values:
            index_context = self.get_industry_index_return_context(
                industry=group,
                start_date=start_date,
                end_date=end_date,
                min_bars=min_bars,
            )
            if index_context.get("status") == "ok":
                return index_context
        if "TPEX" in market_values:
            tpex_index_context = self.get_tpex_industry_index_return_context(
                industry=group,
                start_date=start_date,
                end_date=end_date,
                min_bars=min_bars,
            )
            if tpex_index_context.get("status") == "ok":
                return tpex_index_context
        local_context = self.get_local_industry_return_context(
            industry=group,
            start_date=start_date,
            end_date=end_date,
            markets=markets,
            max_symbols=max_symbols,
            min_bars=min_bars,
        )
        if local_context.get("status") == "ok":
            return local_context
        if "TPEX" in market_values:
            market_context = self.get_market_index_return_context(
                market="TPEX",
                start_date=start_date,
                end_date=end_date,
                min_bars=min_bars,
            )
            if market_context.get("status") == "ok":
                return market_context
        return local_context

    def upsert_daily_bars(self, symbol: str, records: list[dict]) -> int:
        rows = 0
        for item in records:
            bar_date = item["date"]
            stmt = select(DailyBar).where(DailyBar.symbol == symbol, DailyBar.date == bar_date).limit(1)
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = DailyBar(symbol=symbol, date=bar_date, source=item.get("source", "finmind"))
                self.session.add(existing)
            existing.open = float(item["open"])
            existing.high = float(item["high"])
            existing.low = float(item["low"])
            existing.close = float(item["close"])
            existing.volume = float(item["volume"])
            rows += 1
        return rows

    def get_daily_bars(self, symbol: str, start_date: date | None = None, end_date: date | None = None) -> list[DailyBar]:
        stmt = select(DailyBar).where(DailyBar.symbol == symbol).order_by(DailyBar.date.asc())
        if start_date is not None:
            stmt = stmt.where(DailyBar.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(DailyBar.date <= end_date)
        return list(self.session.execute(stmt).scalars().all())

    def get_latest_bar_date(self, symbol: str) -> date | None:
        stmt = select(func.max(DailyBar.date)).where(DailyBar.symbol == symbol)
        return self.session.execute(stmt).scalar_one_or_none()

    def count_symbols_with_recent_bars(self, symbols: list[str], min_date: date) -> int:
        if not symbols:
            return 0
        stmt = (
            select(func.count(distinct(DailyBar.symbol)))
            .where(DailyBar.symbol.in_(symbols), DailyBar.date >= min_date)
        )
        value = self.session.execute(stmt).scalar_one_or_none()
        return int(value or 0)

    def upsert_institutional_chip(self, symbol: str, records: list[dict]) -> int:
        rows = 0
        for item in records:
            row_date = item["date"]
            stmt = (
                select(InstitutionalChipDaily)
                .where(InstitutionalChipDaily.symbol == symbol, InstitutionalChipDaily.date == row_date)
                .limit(1)
            )
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = InstitutionalChipDaily(symbol=symbol, date=row_date, source=item.get("source", "finmind"))
                self.session.add(existing)
            existing.foreign_net_buy = float(item.get("foreign_net_buy", 0.0))
            existing.investment_trust_net_buy = float(item.get("investment_trust_net_buy", 0.0))
            existing.dealer_net_buy = float(item.get("dealer_net_buy", 0.0))
            rows += 1
        return rows

    def get_institutional_chip(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[InstitutionalChipDaily]:
        stmt = select(InstitutionalChipDaily).where(InstitutionalChipDaily.symbol == symbol).order_by(InstitutionalChipDaily.date.asc())
        if start_date is not None:
            stmt = stmt.where(InstitutionalChipDaily.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(InstitutionalChipDaily.date <= end_date)
        return list(self.session.execute(stmt).scalars().all())

    def get_institutional_chip_rollup(self, symbol: str, end_date: date, lookback_days: int = 5) -> dict:
        rows = self.get_institutional_chip(symbol=symbol, end_date=end_date)
        if not rows:
            return {
                "days": 0,
                "foreign_net_buy_sum": 0.0,
                "investment_trust_net_buy_sum": 0.0,
                "dealer_net_buy_sum": 0.0,
                "institutional_net_buy_sum": 0.0,
            }
        selected = rows[-max(1, int(lookback_days)) :]
        foreign = float(sum(float(item.foreign_net_buy) for item in selected))
        investment = float(sum(float(item.investment_trust_net_buy) for item in selected))
        dealer = float(sum(float(item.dealer_net_buy) for item in selected))
        return {
            "days": len(selected),
            "foreign_net_buy_sum": foreign,
            "investment_trust_net_buy_sum": investment,
            "dealer_net_buy_sum": dealer,
            "institutional_net_buy_sum": foreign + investment,
        }

    def upsert_broker_agg(self, symbol: str, records: list[dict]) -> int:
        rows = 0
        for item in records:
            row_date = item["date"]
            stmt = select(BrokerAggDaily).where(BrokerAggDaily.symbol == symbol, BrokerAggDaily.date == row_date).limit(1)
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = BrokerAggDaily(symbol=symbol, date=row_date, source=item.get("source", "finmind"))
                self.session.add(existing)
            existing.concentration_proxy = float(item.get("concentration_proxy", 0.0))
            existing.top5_net_buy = float(item.get("top5_net_buy", 0.0))
            rows += 1
        return rows

    def get_broker_agg(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[BrokerAggDaily]:
        stmt = select(BrokerAggDaily).where(BrokerAggDaily.symbol == symbol).order_by(BrokerAggDaily.date.asc())
        if start_date is not None:
            stmt = stmt.where(BrokerAggDaily.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(BrokerAggDaily.date <= end_date)
        return list(self.session.execute(stmt).scalars().all())

    def is_chip_concentration_up(self, symbol: str, end_date: date, days: int = 3) -> bool:
        rows = self.get_broker_agg(symbol=symbol, end_date=end_date)
        if len(rows) < max(2, int(days)):
            return False
        selected = rows[-max(2, int(days)) :]
        diffs = []
        for idx in range(1, len(selected)):
            diffs.append(float(selected[idx].concentration_proxy) - float(selected[idx - 1].concentration_proxy))
        return len(diffs) >= (int(days) - 1) and all(diff > 0 for diff in diffs[-(int(days) - 1) :])

    def upsert_disposition_periods(self, symbol: str, records: list[dict]) -> int:
        rows = 0
        for item in records:
            start = item["start_date"]
            end = item["end_date"]
            disposition_type = str(item.get("disposition_type", "") or "")
            reason = str(item.get("reason", "") or "")
            stmt = (
                select(DispositionPeriod)
                .where(
                    DispositionPeriod.symbol == symbol,
                    DispositionPeriod.start_date == start,
                    DispositionPeriod.end_date == end,
                    DispositionPeriod.disposition_type == disposition_type,
                    DispositionPeriod.reason == reason,
                )
                .limit(1)
            )
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = DispositionPeriod(
                    symbol=symbol,
                    start_date=start,
                    end_date=end,
                    disposition_type=disposition_type,
                    reason=reason,
                    source=item.get("source", "finmind"),
                )
                self.session.add(existing)
                rows += 1
        return rows

    def get_disposition_periods(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[DispositionPeriod]:
        stmt = select(DispositionPeriod).where(DispositionPeriod.symbol == symbol).order_by(DispositionPeriod.start_date.asc())
        if start_date is not None:
            stmt = stmt.where(DispositionPeriod.end_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(DispositionPeriod.start_date <= end_date)
        return list(self.session.execute(stmt).scalars().all())

    def get_active_disposition(self, symbol: str, on_date: date) -> dict:
        stmt = (
            select(DispositionPeriod)
            .where(
                DispositionPeriod.symbol == symbol,
                DispositionPeriod.start_date <= on_date,
                DispositionPeriod.end_date >= on_date,
            )
            .order_by(DispositionPeriod.start_date.asc())
        )
        rows = list(self.session.execute(stmt).scalars().all())
        if not rows:
            return {"active": False, "count": 0, "periods": []}
        periods = [
            {
                "start_date": row.start_date.isoformat(),
                "end_date": row.end_date.isoformat(),
                "disposition_type": row.disposition_type,
                "reason": row.reason,
            }
            for row in rows
        ]
        return {"active": True, "count": len(rows), "periods": periods}

    def upsert_attention_stocks(self, records: list[dict]) -> int:
        rows = 0
        for item in records:
            symbol = str(item.get("symbol") or item.get("code") or "").strip().upper()
            trade_date = item.get("trade_date")
            announced_date = item.get("announced_date") or trade_date
            if not symbol or trade_date is None or announced_date is None:
                continue
            reason = str(item.get("reason") or "")
            self.session.flush()
            stmt = (
                select(AttentionStock)
                .where(
                    AttentionStock.symbol == symbol,
                    AttentionStock.trade_date == trade_date,
                    AttentionStock.reason == reason,
                )
                .limit(1)
            )
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = AttentionStock(
                    symbol=symbol,
                    trade_date=trade_date,
                    announced_date=announced_date,
                    reason=reason,
                    source=str(item.get("source") or "shioaji_notice"),
                )
                self.session.add(existing)
                rows += 1
            existing.notice_updated_at = item.get("updated_at") or item.get("notice_updated_at")
            existing.close = float(item.get("close") or 0.0)
            existing.source = str(item.get("source") or existing.source or "shioaji_notice")
        return rows

    def get_attention_stocks(
        self,
        symbol: str | None = None,
        trade_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[AttentionStock]:
        stmt = select(AttentionStock).order_by(AttentionStock.trade_date.desc(), AttentionStock.symbol.asc())
        if symbol is not None:
            stmt = stmt.where(AttentionStock.symbol == str(symbol).strip().upper())
        if trade_date is not None:
            stmt = stmt.where(AttentionStock.trade_date == trade_date)
        if start_date is not None:
            stmt = stmt.where(AttentionStock.trade_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(AttentionStock.trade_date <= end_date)
        return list(self.session.execute(stmt).scalars().all())

    def get_active_attention(self, symbol: str, on_date: date) -> dict:
        rows = self.get_attention_stocks(symbol=symbol, trade_date=on_date)
        if not rows:
            return {"active": False, "count": 0, "records": []}
        records = [
            {
                "trade_date": row.trade_date.isoformat(),
                "announced_date": row.announced_date.isoformat(),
                "updated_at": row.notice_updated_at.isoformat() if row.notice_updated_at else None,
                "close": float(row.close),
                "reason": row.reason,
            }
            for row in rows
        ]
        return {"active": True, "count": len(rows), "records": records}

    def upsert_shareholding(self, symbol: str, records: list[dict]) -> int:
        rows = 0
        for item in records:
            row_date = item["date"]
            stmt = select(ShareholdingDaily).where(ShareholdingDaily.symbol == symbol, ShareholdingDaily.date == row_date).limit(1)
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = ShareholdingDaily(symbol=symbol, date=row_date, source=item.get("source", "finmind"))
                self.session.add(existing)
            existing.major_holder_ratio = float(item.get("major_holder_ratio", 0.0))
            existing.retail_holder_ratio = float(item.get("retail_holder_ratio", 0.0))
            rows += 1
        return rows

    def get_shareholding(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[ShareholdingDaily]:
        stmt = select(ShareholdingDaily).where(ShareholdingDaily.symbol == symbol).order_by(ShareholdingDaily.date.asc())
        if start_date is not None:
            stmt = stmt.where(ShareholdingDaily.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(ShareholdingDaily.date <= end_date)
        return list(self.session.execute(stmt).scalars().all())

    def get_latest_shareholding(self, symbol: str, on_or_before: date | None = None) -> ShareholdingDaily | None:
        stmt = select(ShareholdingDaily).where(ShareholdingDaily.symbol == symbol)
        if on_or_before is not None:
            stmt = stmt.where(ShareholdingDaily.date <= on_or_before)
        stmt = stmt.order_by(ShareholdingDaily.date.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def upsert_holding_shares_per(self, symbol: str, records: list[dict]) -> int:
        rows = 0
        for item in records:
            row_date = item["date"]
            stmt = (
                select(HoldingSharesPerDaily)
                .where(HoldingSharesPerDaily.symbol == symbol, HoldingSharesPerDaily.date == row_date)
                .limit(1)
            )
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = HoldingSharesPerDaily(symbol=symbol, date=row_date, source=item.get("source", "finmind"))
                self.session.add(existing)
            existing.concentration_proxy = float(item.get("concentration_proxy", 0.0))
            existing.dispersion_proxy = float(item.get("dispersion_proxy", 0.0))
            rows += 1
        return rows

    def get_holding_shares_per(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[HoldingSharesPerDaily]:
        stmt = select(HoldingSharesPerDaily).where(HoldingSharesPerDaily.symbol == symbol).order_by(HoldingSharesPerDaily.date.asc())
        if start_date is not None:
            stmt = stmt.where(HoldingSharesPerDaily.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(HoldingSharesPerDaily.date <= end_date)
        return list(self.session.execute(stmt).scalars().all())

    def get_latest_holding_shares_per(self, symbol: str, on_or_before: date | None = None) -> HoldingSharesPerDaily | None:
        stmt = select(HoldingSharesPerDaily).where(HoldingSharesPerDaily.symbol == symbol)
        if on_or_before is not None:
            stmt = stmt.where(HoldingSharesPerDaily.date <= on_or_before)
        stmt = stmt.order_by(HoldingSharesPerDaily.date.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def upsert_monthly_revenues(self, symbol: str, records: list[dict]) -> int:
        def _find_pending(period: str) -> MonthlyRevenue | None:
            for row in self.session.new:
                if isinstance(row, MonthlyRevenue) and row.symbol == symbol and row.period == period:
                    return row
            return None

        rows = 0
        for item in records:
            period = str(item.get("period", "") or "").strip()
            announce_date = item.get("announce_date")
            if not period or announce_date is None:
                continue
            stmt = select(MonthlyRevenue).where(MonthlyRevenue.symbol == symbol, MonthlyRevenue.period == period).limit(1)
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = _find_pending(period)
            if existing is None:
                existing = MonthlyRevenue(symbol=symbol, period=period, announce_date=announce_date, source=item.get("source", "finmind"))
                self.session.add(existing)
            existing.announce_date = announce_date
            existing.revenue = float(item.get("revenue", 0.0) or 0.0)
            existing.revenue_yoy_pct = self._optional_float(item.get("revenue_yoy_pct"))
            existing.revenue_mom_pct = self._optional_float(item.get("revenue_mom_pct"))
            rows += 1
        return rows

    def get_monthly_revenues(self, symbol: str, on_or_before: date | None = None) -> list[MonthlyRevenue]:
        stmt = select(MonthlyRevenue).where(MonthlyRevenue.symbol == symbol).order_by(MonthlyRevenue.announce_date.asc())
        if on_or_before is not None:
            stmt = stmt.where(MonthlyRevenue.announce_date <= on_or_before)
        return list(self.session.execute(stmt).scalars().all())

    def get_latest_monthly_revenue(self, symbol: str, on_or_before: date | None = None) -> MonthlyRevenue | None:
        stmt = select(MonthlyRevenue).where(MonthlyRevenue.symbol == symbol)
        if on_or_before is not None:
            stmt = stmt.where(MonthlyRevenue.announce_date <= on_or_before)
        stmt = stmt.order_by(MonthlyRevenue.announce_date.desc(), MonthlyRevenue.period.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def upsert_financial_statement_summaries(self, symbol: str, records: list[dict]) -> int:
        def _find_pending(period: str) -> FinancialStatementSummary | None:
            for row in self.session.new:
                if isinstance(row, FinancialStatementSummary) and row.symbol == symbol and row.period == period:
                    return row
            return None

        rows = 0
        for item in records:
            period = str(item.get("period", "") or "").strip()
            announce_date = item.get("announce_date")
            if not period or announce_date is None:
                continue
            stmt = (
                select(FinancialStatementSummary)
                .where(FinancialStatementSummary.symbol == symbol, FinancialStatementSummary.period == period)
                .limit(1)
            )
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = _find_pending(period)
            if existing is None:
                existing = FinancialStatementSummary(
                    symbol=symbol,
                    period=period,
                    announce_date=announce_date,
                    source=item.get("source", "finmind"),
                )
                self.session.add(existing)
            existing.announce_date = announce_date
            existing.eps = self._optional_float(item.get("eps"))
            existing.roe_pct = self._optional_float(item.get("roe_pct"))
            existing.gross_margin_pct = self._optional_float(item.get("gross_margin_pct"))
            existing.operating_margin_pct = self._optional_float(item.get("operating_margin_pct"))
            existing.debt_ratio_pct = self._optional_float(item.get("debt_ratio_pct"))
            existing.operating_cash_flow = self._optional_float(item.get("operating_cash_flow"))
            existing.raw_json = str(item.get("raw_json", "{}") or "{}")
            rows += 1
        return rows

    def get_latest_financial_summary(self, symbol: str, on_or_before: date | None = None) -> FinancialStatementSummary | None:
        stmt = select(FinancialStatementSummary).where(FinancialStatementSummary.symbol == symbol)
        if on_or_before is not None:
            stmt = stmt.where(FinancialStatementSummary.announce_date <= on_or_before)
        stmt = stmt.order_by(FinancialStatementSummary.announce_date.desc(), FinancialStatementSummary.period.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def upsert_news_events(self, symbol: str, records: list[dict]) -> int:
        def _find_pending(news_date: date, title: str) -> NewsEvent | None:
            for row in self.session.new:
                if isinstance(row, NewsEvent) and row.symbol == symbol and row.news_date == news_date and row.title == title:
                    return row
            return None

        rows = 0
        for item in records:
            news_date = item.get("date") or item.get("news_date")
            title = str(item.get("title", "") or "").strip()
            if news_date is None or not title:
                continue
            stmt = select(NewsEvent).where(NewsEvent.symbol == symbol, NewsEvent.news_date == news_date, NewsEvent.title == title).limit(1)
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = _find_pending(news_date, title)
            if existing is None:
                existing = NewsEvent(symbol=symbol, news_date=news_date, title=title, source=item.get("source", "finmind"))
                self.session.add(existing)
            existing.source_name = str(item.get("source_name", "") or "")
            existing.url = str(item.get("url", "") or "")
            existing.llm_summary = str(item.get("llm_summary", "") or "")
            existing.risk_tags_json = json.dumps(item.get("risk_tags", []), ensure_ascii=False)
            rows += 1
        return rows

    def list_recent_news_events(self, symbol: str, on_or_before: date, limit: int = 5) -> list[NewsEvent]:
        stmt = (
            select(NewsEvent)
            .where(NewsEvent.symbol == symbol, NewsEvent.news_date <= on_or_before)
            .order_by(NewsEvent.news_date.desc(), NewsEvent.updated_at.desc())
            .limit(max(1, int(limit)))
        )
        return list(self.session.execute(stmt).scalars().all())

    def upsert_feature_snapshots(self, symbol: str, records: list[dict]) -> int:
        rows = 0
        for item in records:
            row_date = item["date"]
            stmt = select(FeatureSnapshot).where(FeatureSnapshot.symbol == symbol, FeatureSnapshot.date == row_date).limit(1)
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = FeatureSnapshot(symbol=symbol, date=row_date)
                self.session.add(existing)
            existing.close = float(item.get("close", 0.0))
            existing.ma60 = float(item.get("ma60", 0.0))
            existing.volume = float(item.get("volume", 0.0))
            existing.volume_ma5 = float(item.get("volume_ma5", 0.0))
            existing.rsi3 = float(item.get("rsi3", 0.0))
            existing.k9 = float(item.get("k9", 0.0))
            existing.d9 = float(item.get("d9", 0.0))
            existing.atr14 = float(item.get("atr14", 0.0))
            existing.foreign_net_5d = float(item.get("foreign_net_5d", 0.0))
            existing.investment_net_5d = float(item.get("investment_net_5d", 0.0))
            existing.dealer_net_5d = float(item.get("dealer_net_5d", 0.0))
            existing.chip_concentration_proxy = float(item.get("chip_concentration_proxy", 0.0))
            existing.chip_concentration_up3 = bool(item.get("chip_concentration_up3", False))
            existing.disposition_active = bool(item.get("disposition_active", False))
            existing.revenue_score = float(item.get("revenue_score", 0.5) or 0.5)
            existing.quality_score = float(item.get("quality_score", 0.5) or 0.5)
            existing.valuation_or_growth_score = float(item.get("valuation_or_growth_score", 0.5) or 0.5)
            existing.news_risk_score = float(item.get("news_risk_score", 0.5) or 0.5)
            existing.fundamental_data_quality = str(item.get("fundamental_data_quality", "missing") or "missing")
            existing.entry_ready = bool(item.get("entry_ready", False))
            existing.meta_json = str(item.get("meta_json", "{}") or "{}")
            rows += 1
        return rows

    def get_feature_snapshots(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[FeatureSnapshot]:
        stmt = select(FeatureSnapshot).where(FeatureSnapshot.symbol == symbol).order_by(FeatureSnapshot.date.asc())
        if start_date is not None:
            stmt = stmt.where(FeatureSnapshot.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(FeatureSnapshot.date <= end_date)
        return list(self.session.execute(stmt).scalars().all())

    def add_sync_job(self, job_name: str, status: str, rows_upserted: int = 0, error_msg: str = "") -> None:
        self.session.add(
            SyncJob(job_name=job_name, status=status, rows_upserted=int(rows_upserted), error_msg=error_msg or "")
        )

    @staticmethod
    def _data_health_overview_score(scored_components: list[dict], *, overall_status: str) -> int:
        if not scored_components:
            return 0
        component_scores = {"ok": 100.0, "warn": 65.0, "danger": 25.0}
        raw_score = sum(
            component_scores.get(str(component.get("status") or "").lower(), component_scores["warn"])
            for component in scored_components
        ) / len(scored_components)
        score = int(round(max(0.0, min(100.0, raw_score))))
        status = str(overall_status or "").lower()
        if status == "danger":
            return min(score, 49)
        if status == "warn":
            return min(score, 89)
        return score

    def get_data_health_overview(self, *, as_of: date | None = None, freshness_days: int = 3) -> dict:
        today = as_of or datetime.now().date()
        fresh_days = max(1, int(freshness_days or 3))
        rank = {"ok": 0, "warn": 1, "danger": 2}

        def iso(value: date | datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        def date_status(value: date | None, *, ok_days: int = 3, warn_days: int = 7) -> tuple[str, int | None]:
            if value is None:
                return "danger", None
            age = max(0, (today - value).days)
            if age <= ok_days:
                return "ok", age
            if age <= warn_days:
                return "warn", age
            return "danger", age

        def coverage_status(value: float | None) -> str:
            if value is None:
                return "danger"
            if value >= 0.70:
                return "ok"
            if value >= 0.40:
                return "warn"
            return "danger"

        def worse_status(*statuses: str) -> str:
            return max((status or "warn" for status in statuses), key=lambda item: rank.get(item, 1))

        def parse_datetime(value: object) -> datetime | None:
            if isinstance(value, datetime):
                return value
            text = str(value or "").strip()
            if not text:
                return None
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                return None

        def latest_run(model, *, date_attr, run_attr="run_id") -> dict:
            run_id = self.session.execute(select(getattr(model, run_attr)).order_by(model.created_at.desc()).limit(1)).scalar_one_or_none()
            if not run_id:
                return {"run_id": "", "run_date": None, "row_count": 0, "status": "danger", "age_days": None}
            rows = list(self.session.execute(select(model).where(getattr(model, run_attr) == run_id)).scalars().all())
            run_date = max((getattr(row, date_attr) for row in rows if getattr(row, date_attr) is not None), default=None)
            status, age = date_status(run_date, ok_days=1, warn_days=3)
            return {
                "run_id": str(run_id),
                "run_date": iso(run_date),
                "row_count": len(rows),
                "status": status if rows else "danger",
                "age_days": age,
            }

        latest_bar_date = self.session.execute(select(func.max(DailyBar.date))).scalar_one_or_none()
        daily_status, daily_age = date_status(latest_bar_date)
        active_symbols = int(
            self.session.execute(select(func.count()).select_from(Instrument).where(Instrument.is_active.is_(True))).scalar_one()
            or 0
        )
        recent_cutoff = latest_bar_date - timedelta(days=fresh_days) if latest_bar_date else None
        if recent_cutoff is None:
            recent_symbols = 0
        else:
            recent_symbols = int(
                self.session.execute(
                    select(func.count(distinct(DailyBar.symbol)))
                    .join(Instrument, Instrument.symbol == DailyBar.symbol)
                    .where(
                        Instrument.is_active.is_(True),
                        DailyBar.date >= recent_cutoff,
                        DailyBar.date <= latest_bar_date,
                    )
                ).scalar_one()
                or 0
            )
        coverage = (recent_symbols / active_symbols) if active_symbols else None
        universe_status = coverage_status(coverage) if active_symbols else "danger"

        candidates = latest_run(CandidateSnapshot, date_attr="run_date")
        radar = latest_run(DailyRadarSnapshot, date_attr="run_date")
        if radar["run_id"]:
            ready_count = int(
                self.session.execute(
                    select(func.count())
                    .select_from(DailyRadarSnapshot)
                    .where(DailyRadarSnapshot.run_id == radar["run_id"], DailyRadarSnapshot.action_label == "ENTRY_READY")
                ).scalar_one()
                or 0
            )
        else:
            ready_count = 0
        radar["entry_ready_count"] = ready_count

        twse_index_date = self.session.execute(
            select(func.max(IndustryIndexBar.date)).where(IndustryIndexBar.source == "twse_mi_index")
        ).scalar_one_or_none()
        tpex_sector_index_date = self.session.execute(
            select(func.max(IndustryIndexBar.date)).where(IndustryIndexBar.source == TPEX_SECTOR_INDEX_SOURCE)
        ).scalar_one_or_none()
        tpex_index_date = self.session.execute(
            select(func.max(IndustryIndexBar.date)).where(IndustryIndexBar.source == "tpex_index_info")
        ).scalar_one_or_none()
        twse_status, twse_age = date_status(twse_index_date, ok_days=7, warn_days=14)
        tpex_official_date = max([day for day in [tpex_sector_index_date, tpex_index_date] if day is not None], default=None)
        tpex_status, tpex_age = date_status(tpex_official_date, ok_days=7, warn_days=14)
        external_rank = max({"ok": 0, "warn": 1, "danger": 2}[twse_status], {"ok": 0, "warn": 1, "danger": 2}[tpex_status])
        external_status = ["ok", "warn", "danger"][external_rank]
        external_count = int(self.session.execute(select(func.count()).select_from(IndustryIndexBar)).scalar_one() or 0)
        twse_industry_index_count = int(
            self.session.execute(
                select(func.count(distinct(IndustryIndexBar.industry))).where(
                    IndustryIndexBar.source == "twse_mi_index",
                    IndustryIndexBar.industry.is_not(None),
                    IndustryIndexBar.industry != "",
                )
            ).scalar_one()
            or 0
        )
        tpex_sector_index_count = int(
            self.session.execute(
                select(func.count(distinct(IndustryIndexBar.industry))).where(
                    IndustryIndexBar.source == TPEX_SECTOR_INDEX_SOURCE,
                    IndustryIndexBar.industry.is_not(None),
                    IndustryIndexBar.industry != "",
                )
            ).scalar_one()
            or 0
        )
        tpex_market_index_count = int(
            self.session.execute(
                select(func.count()).select_from(IndustryIndexBar).where(IndustryIndexBar.source == "tpex_index_info")
            ).scalar_one()
            or 0
        )
        tpex_active_industry_count = int(
            self.session.execute(
                select(func.count(distinct(Instrument.industry))).where(
                    Instrument.market == "TPEX",
                    Instrument.is_active.is_(True),
                    Instrument.industry.is_not(None),
                    Instrument.industry != "",
                )
            ).scalar_one()
            or 0
        )
        if recent_cutoff is None:
            tpex_recent_industry_count = 0
        else:
            tpex_recent_industry_count = int(
                self.session.execute(
                    select(func.count(distinct(Instrument.industry)))
                    .join(DailyBar, DailyBar.symbol == Instrument.symbol)
                    .where(
                        Instrument.market == "TPEX",
                        Instrument.is_active.is_(True),
                        Instrument.industry.is_not(None),
                        Instrument.industry != "",
                        DailyBar.date >= recent_cutoff,
                        DailyBar.date <= latest_bar_date,
                    )
                ).scalar_one()
                or 0
            )
        market_context_status = (
            "ok"
            if twse_industry_index_count > 0 and (tpex_sector_index_count > 0 or tpex_recent_industry_count > 0 or tpex_market_index_count >= 2)
            else "warn"
            if twse_industry_index_count > 0 or tpex_sector_index_count > 0 or tpex_recent_industry_count > 0 or tpex_market_index_count >= 2
            else "danger"
        )
        instrument_industry_coverage = self.get_instrument_industry_coverage()

        def market_label(market: str) -> str:
            return {
                "TWSE": "上市",
                "TPEX": "上櫃",
                "TW": "台股",
            }.get(str(market or "").strip().upper(), str(market or "").strip().upper() or "未分類")

        watched_jobs = [
            "sync_market_bundle",
            "sync_external_index_context",
            "sync_twse_industry_indices",
            "sync_tpex_sector_indices",
            "sync_tpex_market_index",
            "sync_daily_bars",
            "sync_institutional_chip",
            "sync_broker_agg_chip",
            "sync_disposition_periods",
            "sync_attention_stocks",
            "sync_mid100_universe",
            "invest_pipeline",
            "daily_radar",
        ]
        sync_rows = list(
            self.session.execute(
                select(SyncJob).where(SyncJob.job_name.in_(watched_jobs)).order_by(SyncJob.run_at.desc()).limit(240)
            ).scalars().all()
        )
        recent_reason_codes: list[str] = []
        recent_symbol_failures: dict[str, dict] = {}
        recent_unavailable_by_scope: dict[str, set[str]] = {}
        recent_unavailable_reason_by_scope: dict[str, dict[str, list[str]]] = {}
        market_data_skip_reason_codes = {
            "upstream_bad_request",
            "upstream_forbidden",
            "upstream_no_data",
            "upstream_not_found",
        }

        def scope_had_official_attempt(summary: dict, scopes: set[str]) -> bool:
            components = summary.get("components") if isinstance(summary.get("components"), list) else []
            component_by_name = {
                str(component.get("name") or "").strip(): component
                for component in components
                if isinstance(component, dict) and str(component.get("name") or "").strip()
            }
            for scope_text in scopes:
                component = component_by_name.get(scope_text)
                if not isinstance(component, dict):
                    continue
                if bool(component.get("prefer_official_bulk")):
                    return True
                if str(component.get("api_mode") or "") == "official_bulk":
                    return True
            return False

        def mark_unavailable(symbol: str, scopes: list[str], reasons: list[str]) -> None:
            for scope_text in scopes:
                recent_unavailable_by_scope.setdefault(scope_text, set()).add(symbol)
                reason_bucket = recent_unavailable_reason_by_scope.setdefault(scope_text, {}).setdefault(symbol, [])
                for reason in reasons:
                    if reason and reason not in reason_bucket:
                        reason_bucket.append(reason)

        for row in sync_rows:
            failure_summary = self._sync_job_failure_summary(row.error_msg)
            if not failure_summary:
                continue
            for code in failure_summary.get("reason_codes", []) if isinstance(failure_summary.get("reason_codes"), list) else []:
                code_text = str(code or "").strip()
                if code_text and code_text not in recent_reason_codes:
                    recent_reason_codes.append(code_text)
            for failure in failure_summary.get("symbol_failures", []) if isinstance(failure_summary.get("symbol_failures"), list) else []:
                if not isinstance(failure, dict):
                    continue
                symbol = str(failure.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                entry = recent_symbol_failures.setdefault(
                    symbol,
                    {"symbol": symbol, "failed_count": 0, "scopes": [], "reason_codes": []},
                )
                entry["failed_count"] = int(entry["failed_count"]) + int(failure.get("failed_count") or 1)
                scopes = [str(scope or "").strip() for scope in failure.get("scopes", []) if str(scope or "").strip()]
                reason_codes = [
                    str(code or "").strip()
                    for code in failure.get("reason_codes", [])
                    if str(code or "").strip()
                ]
                for scope_text in scopes:
                    if scope_text and scope_text not in entry["scopes"]:
                        entry["scopes"].append(scope_text)
                for code_text in reason_codes:
                    if code_text and code_text not in entry["reason_codes"]:
                        entry["reason_codes"].append(code_text)
                skip_reasons = sorted(set(reason_codes) & market_data_skip_reason_codes)
                if "upstream_payment_required" in reason_codes and scope_had_official_attempt(
                    failure_summary,
                    set(scopes),
                ):
                    skip_reasons.append("upstream_payment_required_after_official")
                if skip_reasons:
                    mark_unavailable(symbol, scopes, skip_reasons)
        recent_sync_failures = (
            {
                "reason_codes": recent_reason_codes[:12],
                "symbol_failures": sorted(
                    recent_symbol_failures.values(),
                    key=lambda item: (-int(item.get("failed_count") or 0), str(item.get("symbol") or "")),
                )[:200],
            }
            if recent_reason_codes or recent_symbol_failures
            else {}
        )

        active_market_rows = list(
            self.session.execute(
                select(Instrument.market, func.count())
                .where(Instrument.is_active.is_(True))
                .where(func.length(Instrument.symbol) == 4, Instrument.symbol >= "1000", Instrument.symbol <= "9999")
                .group_by(Instrument.market)
                .order_by(Instrument.market.asc())
            ).all()
        )

        def common_stock_filter(market: str):
            return (
                Instrument.is_active.is_(True),
                Instrument.market == market,
                func.length(Instrument.symbol) == 4,
                Instrument.symbol >= "1000",
                Instrument.symbol <= "9999",
            )

        def sync_repair_command(dataset_key: str, symbols: list[str]) -> str:
            start = (today - timedelta(days=max(7, fresh_days * 3))).isoformat()
            base = f".venv/bin/python -m app.cli sync-market-bundle --start-date {start} --end-date {today.isoformat()}"
            source_flag = " --prefer-official-bulk" if dataset_key in {"daily_bars", "institutional_chip"} else ""
            flags = {
                "daily_bars": "--include-bars --no-include-chip --no-include-broker-agg --no-include-disposition --no-include-attention",
                "institutional_chip": "--no-include-bars --include-chip --no-include-broker-agg --no-include-disposition --no-include-attention",
                "broker_agg_chip": "--no-include-bars --no-include-chip --include-broker-agg --no-include-disposition --no-include-attention",
            }.get(dataset_key, "--include-bars --include-chip --include-broker-agg --no-include-disposition --no-include-attention")
            sample_symbols = ",".join(symbols[:20])
            symbol_part = f" --symbols {sample_symbols}" if sample_symbols else ""
            return f"{base}{source_flag}{symbol_part} {flags}"

        premium_broker_requirement = {
            "plan": "FinMind sponsor",
            "reason": "FinMind 官方文件標示 TaiwanStockTradingDailyReportSecIdAgg 只限 sponsor 會員，且查詢需 data_id 與 securities_trader_id；未授權時無法用一般修復批次補齊全市場分點聚合。",
            "docs_url": "https://finmind.github.io/tutor/TaiwanMarket/Chip/#taiwanstocktradingdailyreportsecidagg",
            "parameters": ["data_id", "securities_trader_id", "start_date", "end_date"],
        }

        def dataset_market_coverage(
            *,
            dataset_key: str,
            label: str,
            model,
            date_column,
            optional: bool = False,
            repairable: bool = True,
            provider_requirement: dict | None = None,
        ) -> dict:
            latest_date = self.session.execute(select(func.max(date_column))).scalar_one_or_none()
            latest_status, latest_age = date_status(latest_date, ok_days=fresh_days, warn_days=fresh_days * 2)
            covered_symbols_stmt = None
            if latest_date is not None:
                cutoff = latest_date - timedelta(days=fresh_days)
                covered_symbols_stmt = select(distinct(model.symbol)).where(date_column >= cutoff, date_column <= latest_date)
            scope_unavailable_symbols = recent_unavailable_by_scope.get(dataset_key, set())
            scope_unavailable_reasons = recent_unavailable_reason_by_scope.get(dataset_key, {})
            markets: list[dict] = []
            statuses = [latest_status]
            total_active = 0
            total_raw_active = 0
            total_recent = 0
            total_unavailable = 0
            all_missing_symbols: list[str] = []
            all_unavailable_symbols: list[str] = []
            all_unavailable_reasons: dict[str, list[str]] = {}
            for raw_market, raw_active_count in active_market_rows:
                market = str(raw_market or "").strip().upper() or "TW"
                raw_active_count_int = int(raw_active_count or 0)
                total_raw_active += raw_active_count_int
                unavailable_filter = tuple()
                if scope_unavailable_symbols:
                    unavailable_filter = (Instrument.symbol.in_(sorted(scope_unavailable_symbols)),)
                if covered_symbols_stmt is None:
                    unavailable_symbols = (
                        list(
                            self.session.execute(
                                select(Instrument.symbol)
                                .where(*common_stock_filter(market), *unavailable_filter)
                                .order_by(Instrument.symbol.asc())
                            ).scalars()
                        )
                        if unavailable_filter
                        else []
                    )
                else:
                    unavailable_symbols = (
                        list(
                            self.session.execute(
                                select(Instrument.symbol)
                                .where(
                                    *common_stock_filter(market),
                                    *unavailable_filter,
                                    Instrument.symbol.not_in(covered_symbols_stmt),
                                )
                                .order_by(Instrument.symbol.asc())
                            ).scalars()
                        )
                        if unavailable_filter
                        else []
                    )
                unavailable_count = len(unavailable_symbols)
                active_count = max(0, raw_active_count_int - unavailable_count)
                total_active += active_count
                total_unavailable += unavailable_count
                for symbol in unavailable_symbols:
                    if symbol not in all_unavailable_symbols:
                        all_unavailable_symbols.append(symbol)
                    reasons = scope_unavailable_reasons.get(symbol, [])
                    if reasons:
                        all_unavailable_reasons[symbol] = reasons[:4]
                if covered_symbols_stmt is None:
                    recent_count = 0
                    missing_symbols = list(
                        self.session.execute(
                            select(Instrument.symbol)
                            .where(*common_stock_filter(market))
                            .where(Instrument.symbol.not_in(scope_unavailable_symbols) if scope_unavailable_symbols else True)
                            .order_by(Instrument.symbol.asc())
                            .limit(60)
                        ).scalars()
                    )
                else:
                    recent_count = int(
                        self.session.execute(
                            select(func.count(distinct(Instrument.symbol))).where(
                                *common_stock_filter(market),
                                Instrument.symbol.in_(covered_symbols_stmt),
                            )
                        ).scalar_one()
                        or 0
                    )
                    missing_symbols = list(
                        self.session.execute(
                            select(Instrument.symbol)
                            .where(
                                *common_stock_filter(market),
                                Instrument.symbol.not_in(covered_symbols_stmt),
                            )
                            .where(Instrument.symbol.not_in(scope_unavailable_symbols) if scope_unavailable_symbols else True)
                            .order_by(Instrument.symbol.asc())
                            .limit(60)
                        ).scalars()
                    )
                total_recent += recent_count
                coverage_ratio = (recent_count / active_count) if active_count else None
                market_status = worse_status(latest_status, coverage_status(coverage_ratio) if active_count else "warn")
                statuses.append(market_status)
                for symbol in missing_symbols:
                    if symbol not in all_missing_symbols:
                        all_missing_symbols.append(symbol)
                markets.append(
                    {
                        "market": market,
                        "label": market_label(market),
                        "active_symbols": active_count,
                        "raw_active_symbols": raw_active_count_int,
                        "recent_symbols": recent_count,
                        "unavailable_symbol_count": unavailable_count,
                        "unavailable_symbols": unavailable_symbols[:200],
                        "unavailable_symbol_reasons": {
                            symbol: scope_unavailable_reasons.get(symbol, [])[:4]
                            for symbol in unavailable_symbols[:200]
                            if scope_unavailable_reasons.get(symbol)
                        },
                        "coverage": coverage_ratio,
                        "coverage_pct": round(float(coverage_ratio or 0.0) * 100, 1),
                        "missing_count": max(0, active_count - recent_count),
                        "missing_symbols": missing_symbols,
                        "status": market_status,
                    }
                )
            total_coverage = (total_recent / total_active) if total_active else None
            dataset_status = worse_status(*(statuses or ["danger"]))
            if optional and str(dataset_status) == "danger" and total_recent <= 0:
                dataset_status = "warn"
            return {
                "key": dataset_key,
                "label": label,
                "status": dataset_status,
                "optional": bool(optional),
                "repairable": bool(repairable),
                "provider_requirement": provider_requirement or {},
                "latest_date": iso(latest_date),
                "age_days": latest_age,
                "active_symbols": total_active,
                "raw_active_symbols": total_raw_active,
                "recent_symbols": total_recent,
                "unavailable_symbol_count": total_unavailable,
                "unavailable_symbols": all_unavailable_symbols[:500],
                "unavailable_symbol_reasons": {
                    symbol: all_unavailable_reasons.get(symbol, [])[:4]
                    for symbol in all_unavailable_symbols[:500]
                    if all_unavailable_reasons.get(symbol)
                },
                "coverage": total_coverage,
                "coverage_pct": round(float(total_coverage or 0.0) * 100, 1),
                "missing_count": max(0, total_active - total_recent),
                "missing_symbols": all_missing_symbols[:20],
                "repair_command": sync_repair_command(dataset_key, all_missing_symbols),
                "markets": markets,
            }

        market_data_datasets = [
            dataset_market_coverage(
                dataset_key="daily_bars",
                label="日線資料",
                model=DailyBar,
                date_column=DailyBar.date,
            ),
            dataset_market_coverage(
                dataset_key="institutional_chip",
                label="法人買賣超",
                model=InstitutionalChipDaily,
                date_column=InstitutionalChipDaily.date,
            ),
            dataset_market_coverage(
                dataset_key="broker_agg_chip",
                label="分點聚合（加值）",
                model=BrokerAggDaily,
                date_column=BrokerAggDaily.date,
                optional=True,
                repairable=False,
                provider_requirement=premium_broker_requirement,
            ),
        ]
        core_market_data_datasets = [item for item in market_data_datasets if not item.get("optional")]
        market_data_status = worse_status(
            *(str(item.get("status") or "warn") for item in (core_market_data_datasets or market_data_datasets))
        )
        market_data_value = " / ".join(
            f"{item['label']} {item['coverage_pct']:.0f}%" + (" 加值" if item.get("optional") else "")
            for item in market_data_datasets
        )

        def benign_partial_market_gap(row: SyncJob, failure_summary: dict) -> bool:
            if str(row.status or "").lower() != "partial" or int(row.rows_upserted or 0) <= 0:
                return False
            reason_codes = {
                str(code or "").strip()
                for code in failure_summary.get("reason_codes", [])
                if str(code or "").strip()
            }
            if not reason_codes or not reason_codes.issubset({"upstream_no_data", "upstream_not_found"}):
                return False
            symbol_failures = (
                failure_summary.get("symbol_failures")
                if isinstance(failure_summary.get("symbol_failures"), list)
                else []
            )
            if not symbol_failures:
                return False
            market_scopes = {"daily_bars", "institutional_chip", "broker_agg_chip"}
            for item in symbol_failures:
                if not isinstance(item, dict):
                    return False
                scopes = {str(scope or "").strip() for scope in item.get("scopes", []) if str(scope or "").strip()}
                if not scopes or not scopes.issubset(market_scopes):
                    return False
            return True

        latest_jobs: dict[str, dict] = {}
        for row in sync_rows:
            if row.job_name in latest_jobs:
                continue
            run_day = row.run_at.date() if row.run_at else None
            job_freshness_status, job_age = date_status(run_day, ok_days=1, warn_days=3)
            raw_status = str(row.status or "").lower()
            failure_summary = self._sync_job_failure_summary(row.error_msg)
            partial_market_gap = benign_partial_market_gap(row, failure_summary) if failure_summary else False
            if raw_status == "partial" and partial_market_gap:
                job_health_status = job_freshness_status
            elif raw_status == "partial":
                job_health_status = worse_status(job_freshness_status, "warn")
            elif raw_status not in {"ok"}:
                job_health_status = "danger"
            else:
                job_health_status = job_freshness_status
            job_payload = {
                "job_name": row.job_name,
                "status": row.status,
                "health_status": job_health_status,
                "age_days": job_age,
                "rows_upserted": int(row.rows_upserted or 0),
                "run_at": row.run_at.isoformat(timespec="seconds") if row.run_at else None,
            }
            if partial_market_gap:
                job_payload["benign_partial_market_gap"] = True
            if failure_summary:
                job_payload["failure_summary"] = failure_summary
            latest_jobs[row.job_name] = job_payload
        job_values = list(latest_jobs.values())
        if not job_values:
            sync_status = "warn"
        elif any(str(row.get("health_status", "")).lower() == "danger" for row in job_values):
            sync_status = "danger"
        elif any(str(row.get("health_status", "")).lower() == "warn" for row in job_values):
            sync_status = "warn"
        else:
            sync_status = "ok"

        total_disposition_count = int(self.session.execute(select(func.count()).select_from(DispositionPeriod)).scalar_one() or 0)
        active_disposition_count = int(
            self.session.execute(
                select(func.count())
                .select_from(DispositionPeriod)
                .where(DispositionPeriod.start_date <= today, DispositionPeriod.end_date >= today)
            ).scalar_one()
            or 0
        )
        latest_disposition_updated_at = self.session.execute(select(func.max(DispositionPeriod.updated_at))).scalar_one_or_none()
        disposition_table_status, disposition_table_age = date_status(
            latest_disposition_updated_at.date() if latest_disposition_updated_at else None,
            ok_days=1,
            warn_days=3,
        )
        disposition_job = latest_jobs.get("sync_disposition_periods")
        if disposition_job:
            disposition_status = str(disposition_job.get("health_status", "warn"))
        elif latest_disposition_updated_at is not None:
            disposition_status = disposition_table_status
        else:
            disposition_status = "warn"

        latest_attention_date = self.session.execute(select(func.max(AttentionStock.trade_date))).scalar_one_or_none()
        attention_latest_count = (
            int(
                self.session.execute(
                    select(func.count()).select_from(AttentionStock).where(AttentionStock.trade_date == latest_attention_date)
                ).scalar_one()
                or 0
            )
            if latest_attention_date is not None
            else 0
        )
        active_attention_count = int(
            self.session.execute(select(func.count()).select_from(AttentionStock).where(AttentionStock.trade_date == today)).scalar_one()
            or 0
        )
        attention_table_status, attention_table_age = date_status(latest_attention_date, ok_days=1, warn_days=3)
        attention_job = latest_jobs.get("sync_attention_stocks")
        if attention_job:
            attention_status = str(attention_job.get("health_status", "warn"))
        elif latest_attention_date is not None:
            attention_status = attention_table_status
        else:
            attention_status = "warn"

        index_cache_error = ""
        try:
            from app.data.index_constituents import list_index_constituent_caches

            raw_index_caches = list_index_constituent_caches()
        except Exception as exc:  # noqa: BLE001
            raw_index_caches = []
            index_cache_error = redact_sensitive(str(exc))
        index_caches: list[dict] = []
        latest_index_cache_dt: datetime | None = None
        index_symbol_count = 0
        for cache in raw_index_caches:
            updated_at = parse_datetime(cache.get("updated_at"))
            if updated_at is not None and (latest_index_cache_dt is None or updated_at > latest_index_cache_dt):
                latest_index_cache_dt = updated_at
            cache_count = int(cache.get("count") or 0)
            index_symbol_count += cache_count
            cache_status, cache_age = date_status(updated_at.date() if updated_at else None, ok_days=7, warn_days=21)
            if cache_count <= 0:
                cache_status = "danger"
            index_caches.append(
                {
                    "index_key": str(cache.get("index_key") or ""),
                    "count": cache_count,
                    "file": str(cache.get("file") or ""),
                    "updated_at": cache.get("updated_at"),
                    "age_days": cache_age,
                    "status": cache_status,
                }
            )
        index_cache_count = len(index_caches)
        index_cache_optional = False
        if index_cache_error:
            index_cache_status = "warn"
            index_cache_age = None
            index_cache_meta = f"快取清單讀取失敗：{index_cache_error}"
        elif index_cache_count == 0:
            index_cache_status = "warn"
            index_cache_age = None
            index_cache_optional = True
            index_cache_meta = "尚未建立；使用 index:<key> 前請先匯入或設定官方 URL 同步"
        elif index_symbol_count <= 0:
            index_cache_status = "danger"
            index_cache_age = None
            index_cache_meta = "快取存在但沒有可解析的台股代號"
        else:
            index_cache_status, index_cache_age = date_status(
                latest_index_cache_dt.date() if latest_index_cache_dt else None,
                ok_days=7,
                warn_days=21,
            )
            latest_label = latest_index_cache_dt.isoformat(timespec="seconds") if latest_index_cache_dt else "未知"
            index_cache_meta = f"最新 {latest_label} · 共 {index_symbol_count} 檔"

        components = [
            {
                "key": "daily_bars",
                "label": "日線資料",
                "status": daily_status,
                "value": iso(latest_bar_date) or "無資料",
                "meta": f"距今 {daily_age} 天" if daily_age is not None else "尚未同步日線",
            },
            {
                "key": "universe",
                "label": "標的池覆蓋",
                "status": universe_status,
                "value": f"{recent_symbols}/{active_symbols}",
                "meta": f"近 {fresh_days} 天日線覆蓋 {coverage:.0%}" if coverage is not None else "尚無有效標的",
            },
            {
                "key": "instrument_industry",
                "label": "產業分類",
                "status": instrument_industry_coverage["status"],
                "value": (
                    f"{instrument_industry_coverage['official_industry_symbols']}/"
                    f"{instrument_industry_coverage['active_symbols']}"
                ),
                "meta": (
                    f"正式產業覆蓋 {instrument_industry_coverage['coverage_pct']:.0f}%；"
                    f"不足時使用代號 proxy"
                ),
            },
            {
                "key": "market_data_coverage",
                "label": "市場資料覆蓋",
                "status": market_data_status,
                "value": market_data_value,
                "meta": "TWSE/TPEX 普通股日線、法人與分點資料集覆蓋",
            },
            {
                "key": "index_constituents",
                "label": "指數成分股快取",
                "status": index_cache_status,
                "value": f"{index_cache_count} 組 / {index_symbol_count} 檔",
                "meta": index_cache_meta,
                "optional": index_cache_optional,
            },
            {
                "key": "candidates",
                "label": "一鍵候選",
                "status": candidates["status"],
                "value": str(candidates["row_count"]),
                "meta": f"{candidates['run_date'] or '尚無 run'} · {candidates['run_id'] or '-'}",
            },
            {
                "key": "daily_radar",
                "label": "每日雷達",
                "status": radar["status"],
                "value": f"{radar['entry_ready_count']}/{radar['row_count']}",
                "meta": f"可進場 / 總數 · {radar['run_date'] or '尚無 run'}",
            },
            {
                "key": "external_indices",
                "label": "外部指數",
                "status": external_status,
                "value": str(external_count),
                "meta": f"TWSE {iso(twse_index_date) or '-'} / TPEx 產業 {iso(tpex_sector_index_date) or '-'} / TPEx 市場 {iso(tpex_index_date) or '-'}",
            },
            {
                "key": "market_context",
                "label": "市場背景",
                "status": market_context_status,
                "value": f"TWSE {twse_industry_index_count} / TPEX 官方 {tpex_sector_index_count}",
                "meta": (
                    f"官方產業 {twse_industry_index_count}；"
                    f"TPEX 官方產業 {tpex_sector_index_count}；"
                    f"TPEX 等權 {tpex_recent_industry_count}/{tpex_active_industry_count}；"
                    f"保底 {iso(tpex_index_date) or '-'}"
                ),
            },
            {
                "key": "disposition_periods",
                "label": "處置股資料",
                "status": disposition_status,
                "value": f"{active_disposition_count} 檔",
                "meta": (
                    f"同步 {disposition_job.get('run_at') if disposition_job else '尚無紀錄'} · "
                    f"表內 {total_disposition_count} 筆"
                ),
            },
            {
                "key": "attention_stocks",
                "label": "注意股資料",
                "status": attention_status,
                "value": f"{active_attention_count} 檔",
                "meta": (
                    f"最近名單 {iso(latest_attention_date) or '無資料'}"
                    f"（{attention_latest_count} 檔） · 同步 {attention_job.get('run_at') if attention_job else '尚無紀錄'}"
                ),
            },
            {
                "key": "sync_jobs",
                "label": "同步工作",
                "status": sync_status,
                "value": str(len(job_values)),
                "meta": "最近同步狀態",
            },
        ]
        scored_components = [component for component in components if not component.get("optional")]
        overall_status = max((component["status"] for component in scored_components), key=lambda item: rank.get(item, 1))
        score = self._data_health_overview_score(scored_components, overall_status=overall_status)
        freshness_alerts = [
            {
                "key": str(component["key"]),
                "label": str(component["label"]),
                "status": str(component["status"]),
                "message": str(component["meta"]),
            }
            for component in components
            if str(component.get("status", "")).lower() != "ok"
        ]
        return {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "status": overall_status,
            "score": score,
            "freshness_days": fresh_days,
            "daily_bars": {
                "latest_date": iso(latest_bar_date),
                "age_days": daily_age,
                "status": daily_status,
            },
            "universe": {
                "active_symbols": active_symbols,
                "recent_symbols": recent_symbols,
                "coverage": coverage,
                "status": universe_status,
            },
            "instrument_industry_coverage": instrument_industry_coverage,
            "latest_candidates": candidates,
            "latest_radar": radar,
            "external_indices": {
                "status": external_status,
                "bar_count": external_count,
                "twse_latest_date": iso(twse_index_date),
                "twse_age_days": twse_age,
                "tpex_latest_date": iso(tpex_index_date),
                "tpex_age_days": tpex_age,
            },
            "market_context_coverage": {
                "status": market_context_status,
                "twse_industry_index_count": twse_industry_index_count,
                "twse_latest_date": iso(twse_index_date),
                "tpex_local_industry_count": tpex_active_industry_count,
                "tpex_recent_local_industry_count": tpex_recent_industry_count,
                "tpex_market_index_count": tpex_market_index_count,
                "tpex_market_latest_date": iso(tpex_index_date),
                "sources": [
                    {
                        "label": "TWSE 官方產業價格指數",
                        "method": "twse_industry_price_index",
                        "status": "ok" if twse_industry_index_count > 0 else "danger",
                        "count": twse_industry_index_count,
                        "latest_date": iso(twse_index_date),
                    },
                    {
                        "label": "TPEX 本機同產業等權背景",
                        "method": "local_equal_weight_industry_return",
                        "status": "ok" if tpex_recent_industry_count > 0 else "warn" if tpex_active_industry_count > 0 else "danger",
                        "count": tpex_recent_industry_count,
                        "total_count": tpex_active_industry_count,
                        "latest_date": iso(latest_bar_date),
                    },
                    {
                        "label": "TPEx 櫃買市場保底指數",
                        "method": "tpex_market_price_index",
                        "status": "ok" if tpex_market_index_count >= 2 else "danger",
                        "count": tpex_market_index_count,
                        "latest_date": iso(tpex_index_date),
                    },
                ],
            },
            "market_data_coverage": {
                "status": market_data_status,
                "scope": "common_stock",
                "freshness_days": fresh_days,
                "datasets": market_data_datasets,
            },
            "index_constituents": {
                "status": index_cache_status,
                "cache_count": index_cache_count,
                "symbol_count": index_symbol_count,
                "latest_updated_at": latest_index_cache_dt.isoformat(timespec="seconds") if latest_index_cache_dt else None,
                "latest_age_days": index_cache_age,
                "optional": index_cache_optional,
                "error": index_cache_error,
                "caches": index_caches,
            },
            "disposition_periods": {
                "status": disposition_status,
                "active_count": active_disposition_count,
                "total_count": total_disposition_count,
                "latest_updated_at": iso(latest_disposition_updated_at),
                "age_days": disposition_table_age,
                "latest_sync": disposition_job,
            },
            "attention_stocks": {
                "status": attention_status,
                "active_count": active_attention_count,
                "latest_trade_date": iso(latest_attention_date),
                "latest_count": attention_latest_count,
                "age_days": attention_table_age,
                "latest_sync": attention_job,
            },
            "sync_jobs": job_values,
            "recent_sync_failures": recent_sync_failures,
            "components": components,
            "freshness_alerts": freshness_alerts,
        }

    def add_advisor_decision_record(self, *, decision: dict, preview_id: str = "") -> None:
        proposal = decision.get("proposal") if isinstance(decision.get("proposal"), dict) else {}
        request = decision.get("request") if isinstance(decision.get("request"), dict) else {}
        symbol = str((proposal or {}).get("symbol") or (request or {}).get("symbol") or "").strip().upper()
        self.session.add(
            AdvisorDecisionRecord(
                decision_id=str(decision.get("decision_id", "")),
                advisor_name=str(decision.get("advisor_name", "")),
                advisor_version=str(decision.get("advisor_version", "")),
                symbol=symbol,
                action=str((proposal or {}).get("action", "reject") or "reject"),
                status=str(decision.get("status", "created") or "created"),
                confidence=float((proposal or {}).get("confidence", 0.0) or 0.0),
                preview_id=str(preview_id or decision.get("preview_id", "") or ""),
                request_json=json.dumps(request, ensure_ascii=False, default=str),
                decision_json=json.dumps(decision, ensure_ascii=False, default=str),
                validation_errors_json=json.dumps(list(decision.get("validation_errors") or []), ensure_ascii=False),
                rejected_reason=str(decision.get("rejected_reason", "") or ""),
            )
        )

    def update_advisor_decision_status(self, *, decision_id: str, status: str, rejected_reason: str = "") -> bool:
        row = self.session.execute(
            select(AdvisorDecisionRecord).where(AdvisorDecisionRecord.decision_id == str(decision_id)).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return False
        row.status = str(status)
        row.rejected_reason = str(rejected_reason or row.rejected_reason or "")
        return True

    def get_order_preview_record(self, preview_id: str) -> dict | None:
        row = self.session.execute(
            select(OrderPreviewRecord).where(OrderPreviewRecord.preview_id == str(preview_id)).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "preview_id": row.preview_id,
            "intent_id": row.intent_id,
            "strategy_name": row.strategy_name,
            "strategy_version": row.strategy_version,
            "signal_id": row.signal_id,
            "symbol": row.symbol,
            "side": row.side,
            "price": float(row.price),
            "quantity": int(row.qty),
            "estimated_total_cost": float(row.estimated_total_cost),
            "available_cash": float(row.available_cash) if row.available_cash is not None else None,
            "position_before": int(row.position_before),
            "status": row.status,
            "reason": row.reason,
            "preview": self._safe_json_dict(row.preview_json),
            "decision": self._safe_json_dict(row.decision_json),
            "intent": self._safe_json_dict(row.intent_json),
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def list_recent_advisor_decision_records(self, *, limit: int = 20) -> list[dict]:
        rows = self.session.execute(
            select(AdvisorDecisionRecord)
            .order_by(AdvisorDecisionRecord.created_at.desc())
            .limit(max(1, int(limit)))
        ).scalars()
        return [
            {
                "decision_id": row.decision_id,
                "advisor_name": row.advisor_name,
                "advisor_version": row.advisor_version,
                "symbol": row.symbol,
                "action": row.action,
                "status": row.status,
                "confidence": float(row.confidence),
                "preview_id": row.preview_id,
                "request": self._safe_json_dict(row.request_json),
                "decision": self._safe_json_dict(row.decision_json),
                "validation_errors": self._safe_json_list(row.validation_errors_json),
                "rejected_reason": row.rejected_reason,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    def upsert_candidate_snapshots(self, run_id: str, run_date: date, records: list[dict]) -> int:
        rows = 0
        for item in records:
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            stmt = select(CandidateSnapshot).where(CandidateSnapshot.run_id == run_id, CandidateSnapshot.symbol == symbol).limit(1)
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = CandidateSnapshot(run_id=run_id, run_date=run_date, symbol=symbol)
                self.session.add(existing)
            existing.rank = int(item.get("rank", 0))
            existing.score = float(item.get("score", 0.0))
            existing.risk_score = float(item.get("risk_score", 0.0))
            existing.candidate_type = str(item.get("candidate_type", "watch") or "watch")
            existing.reason_codes_json = json.dumps(item.get("reason_codes", []), ensure_ascii=False)
            existing.risk_flags_json = json.dumps(item.get("risk_flags", []), ensure_ascii=False)
            existing.data_quality = str(item.get("data_quality", "unknown") or "unknown")
            rows += 1
        return rows

    def get_latest_candidates(self, count: int = 10) -> list[dict]:
        latest_run_stmt = select(CandidateSnapshot.run_id).order_by(CandidateSnapshot.created_at.desc()).limit(1)
        latest_run_id = self.session.execute(latest_run_stmt).scalar_one_or_none()
        if not latest_run_id:
            return []
        stmt = (
            select(CandidateSnapshot)
            .where(CandidateSnapshot.run_id == latest_run_id)
            .order_by(CandidateSnapshot.rank.asc(), CandidateSnapshot.score.desc())
            .limit(max(1, int(count)))
        )
        rows = list(self.session.execute(stmt).scalars().all())
        output: list[dict] = []
        for row in rows:
            name = self.get_instrument_name(row.symbol)
            bar_stmt = (
                select(DailyBar.close, DailyBar.date)
                .where(DailyBar.symbol == row.symbol, DailyBar.date <= row.run_date)
                .order_by(DailyBar.date.desc())
                .limit(1)
            )
            bar_info = self.session.execute(bar_stmt).first()
            price = float(bar_info[0]) if bar_info and bar_info[0] is not None else 0.0
            last_bar_date = bar_info[1].isoformat() if bar_info and bar_info[1] is not None else None
            output.append(
                {
                    "run_id": row.run_id,
                    "run_date": row.run_date.isoformat(),
                    "symbol": row.symbol,
                    "name": name,
                    "price": price,
                    "rank": int(row.rank),
                    "score": float(row.score),
                    "risk_score": float(row.risk_score),
                    "candidate_type": row.candidate_type,
                    "reason_codes": self._safe_json_list(row.reason_codes_json),
                    "risk_flags": self._safe_json_list(row.risk_flags_json),
                    "last_bar_date": last_bar_date,
                    "data_quality": row.data_quality,
                }
            )
        return output

    def get_pipeline_runs(self, limit: int = 20) -> list[dict]:
        stmt = (
            select(SyncJob)
            .where(and_(SyncJob.job_name == "invest_pipeline"))
            .order_by(SyncJob.run_at.desc())
            .limit(max(1, int(limit)))
        )
        rows = list(self.session.execute(stmt).scalars().all())
        output: list[dict] = []
        for row in rows:
            meta = self._safe_json_dict(row.error_msg)
            output.append(
                {
                    "run_at": row.run_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": row.status,
                    "rows_upserted": int(row.rows_upserted or 0),
                    "run_id": str(meta.get("run_id", "")),
                    "trade_date": str(meta.get("trade_date", "")),
                    "target_count": int(meta.get("target_count", 0) or 0),
                    "candidate_mode": str(meta.get("candidate_mode", "")),
                    "degraded": bool(meta.get("degraded", False)),
                    "insufficient_candidates": bool(meta.get("insufficient_candidates", False)),
                    "degrade_reasons": list(meta.get("degrade_reasons", [])),
                    "quality_warnings": list(meta.get("quality_warnings", [])),
                    "skipped_steps": list(meta.get("skipped_steps", [])),
                }
            )
        return output

    def upsert_daily_radar_snapshots(self, run_id: str, run_date: date, records: list[dict]) -> int:
        rows = 0
        for item in records:
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            stmt = select(DailyRadarSnapshot).where(DailyRadarSnapshot.run_id == run_id, DailyRadarSnapshot.symbol == symbol).limit(1)
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = DailyRadarSnapshot(run_id=run_id, run_date=run_date, symbol=symbol)
                self.session.add(existing)
            existing.rank = int(item.get("rank", 0))
            existing.name = str(item.get("name", "") or "")
            existing.price = float(item.get("price", 0.0) or 0.0)
            existing.entry_score = float(item.get("entry_score", 0.0) or 0.0)
            existing.risk_score = float(item.get("risk_score", 0.0) or 0.0)
            existing.risk_adjusted_score = float(item.get("risk_adjusted_score", 0.0) or 0.0)
            existing.action_label = str(item.get("action_label", "WATCH_WAIT_TRIGGER") or "WATCH_WAIT_TRIGGER")
            existing.candidate_type = str(item.get("candidate_type", "watch") or "watch")
            existing.reason_tags_json = json.dumps(item.get("reason_tags", []), ensure_ascii=False)
            existing.blocker_tags_json = json.dumps(item.get("blocker_tags", []), ensure_ascii=False)
            existing.data_quality = str(item.get("data_quality", "unknown") or "unknown")
            existing.fundamental_data_quality = str(item.get("fundamental_data_quality", "missing") or "missing")
            existing.revenue_score = float(item.get("revenue_score", 0.5) or 0.5)
            existing.quality_score = float(item.get("quality_score", 0.5) or 0.5)
            existing.valuation_or_growth_score = float(item.get("valuation_or_growth_score", 0.5) or 0.5)
            existing.news_risk_score = float(item.get("news_risk_score", 0.5) or 0.5)
            existing.fundamental_summary_json = json.dumps(item.get("fundamental_summary", {}), ensure_ascii=False, default=str)
            existing.news_summary_json = json.dumps(item.get("news_summary", {}), ensure_ascii=False, default=str)
            existing.risk_quality_score = float(item.get("risk_quality_score", 0.0) or 0.0)
            existing.risk_quality_label = str(item.get("risk_quality_label", "") or "")
            existing.risk_quality_reason = str(item.get("risk_quality_reason", "") or "")
            rows += 1
        return rows

    def get_latest_daily_radar(self, count: int = 20) -> list[dict]:
        latest_run_stmt = select(DailyRadarSnapshot.run_id).order_by(DailyRadarSnapshot.created_at.desc()).limit(1)
        latest_run_id = self.session.execute(latest_run_stmt).scalar_one_or_none()
        if not latest_run_id:
            return []
        stmt = (
            select(DailyRadarSnapshot)
            .where(DailyRadarSnapshot.run_id == latest_run_id)
            .order_by(DailyRadarSnapshot.rank.asc(), DailyRadarSnapshot.entry_score.desc())
            .limit(max(1, int(count)))
        )
        rows = list(self.session.execute(stmt).scalars().all())
        return [self._radar_snapshot_to_dict(row) for row in rows]

    def get_daily_radar_quality_summary(self, count: int = 50) -> dict:
        items = self.get_latest_daily_radar(count=max(1, min(50, int(count or 50))))
        try:
            data_health_overview = self.get_data_health_overview(freshness_days=3)
        except Exception as exc:  # noqa: BLE001
            data_health_overview = {
                "status": "warn",
                "score": None,
                "error": redact_sensitive(str(exc)),
                "sync_jobs": [],
            }
        return self._daily_radar_quality_summary(items, data_health_overview=data_health_overview)

    def get_daily_radar_threshold_backtest(
        self,
        *,
        lookback_runs: int = 8,
        lookahead_bars_list: Iterable[int] = (5, 10, 20),
        count: int = 50,
        base_profile: str | None = None,
    ) -> dict:
        from app.reports.daily_radar_threshold_backtest import build_daily_radar_threshold_backtest

        run_limit = max(2, min(24, int(lookback_runs or 8)))
        item_limit = max(1, min(100, int(count or 50)))
        horizons = tuple(sorted({max(1, min(60, int(value or 0))) for value in lookahead_bars_list if int(value or 0) > 0}))
        runs, duplicate_run_count = self._latest_daily_radar_runs_by_trade_date(limit=run_limit)
        if not runs or not horizons:
            return {
                "status": "insufficient",
                "reason": "no_daily_radar",
                "verdict": "等待歷史樣本",
                "risk_level": "watch",
                "robustness_score": 0,
                "lookback_runs": run_limit,
                "available_runs": 0,
                "duplicate_run_count": duplicate_run_count,
                "lookahead_bars_list": list(horizons),
                "threshold_variants": [],
                "recommended_config": None,
                "optimization_candidates": [],
                "optimization_plan": {
                    "status": "empty",
                    "plan_type": "daily_radar_threshold_candidate_rerun",
                    "mode": "dry_run_plan",
                    "candidate_count": 0,
                    "run_count": 0,
                    "run_items": [],
                    "limitations": [],
                    "next_step": "先累積多輪每日雷達，再產生批次重跑計畫。",
                },
                "segments": {"market": [], "profile": [], "industry": []},
                "next_step": "先累積多輪每日雷達，再做跨期間門檻實跑。",
            }

        payload_runs: list[dict[str, Any]] = []
        for run_id, run_date, created_at in runs:
            radar_rows = self._daily_radar_rows_for_run(str(run_id))[:item_limit]
            if not radar_rows:
                continue
            symbols = [str(row.symbol) for row in radar_rows if str(row.symbol or "").strip()]
            market_by_symbol = {
                str(symbol): str(market or "TW")
                for symbol, market in self.session.execute(
                    select(Instrument.symbol, Instrument.market).where(Instrument.symbol.in_(symbols))
                ).all()
            }
            items = []
            items_by_symbol: dict[str, dict] = {}
            for row in radar_rows:
                item = self._radar_snapshot_to_dict(row)
                item["market"] = market_by_symbol.get(str(row.symbol), "TW")
                item["horizon_returns"] = {}
                item["horizon_matured"] = {}
                items.append(item)
                items_by_symbol[str(row.symbol)] = item
            for horizon in horizons:
                for outcome in self._radar_snapshot_outcome_rows(radar_rows, lookahead=horizon):
                    symbol = str(outcome.get("symbol") or "")
                    target = items_by_symbol.get(symbol)
                    if not target:
                        continue
                    target["horizon_returns"][int(horizon)] = outcome.get("lookahead_return")
                    target["horizon_matured"][int(horizon)] = bool(outcome.get("matured"))
            payload_runs.append(
                {
                    "run_id": str(run_id),
                    "run_date": run_date.isoformat() if run_date else None,
                    "created_at": created_at.isoformat() if created_at else None,
                    "items": items,
                }
            )

        adoption_feedback = self.get_daily_radar_threshold_adoption_summary(
            limit=12,
            lookahead_bars_list=horizons,
        )
        payload = build_daily_radar_threshold_backtest(
            payload_runs,
            base_profile=base_profile,
            lookahead_bars_list=horizons,
            adoption_feedback=adoption_feedback,
        )
        payload.update(
            {
                "lookback_runs": run_limit,
                "available_runs": len(payload_runs),
                "duplicate_run_count": duplicate_run_count,
                "sample_unit": "trade_date",
                "run_dates": [run.get("run_date") for run in payload_runs],
            }
        )
        return payload

    def get_daily_radar_threshold_adoption_summary(
        self,
        *,
        limit: int = 8,
        lookahead_bars_list: Iterable[int] = (5, 10, 20),
    ) -> dict:
        item_limit = max(1, min(24, int(limit or 8)))
        horizons = tuple(sorted({max(1, min(60, int(value or 0))) for value in lookahead_bars_list if int(value or 0) > 0}))
        stmt = (
            select(SyncJob)
            .where(SyncJob.job_name == "daily_radar")
            .order_by(SyncJob.run_at.desc(), SyncJob.id.desc())
            .limit(max(item_limit * 8, item_limit))
        )
        tracked: list[dict] = []
        for job in self.session.execute(stmt).scalars().all():
            meta = self._safe_json_dict(job.error_msg)
            adoption = meta.get("threshold_adoption") if isinstance(meta.get("threshold_adoption"), dict) else {}
            run_id = str(meta.get("run_id") or "")
            if not adoption or not run_id:
                continue
            radar_rows = self._daily_radar_rows_for_run(run_id)
            label_counts = meta.get("label_counts") if isinstance(meta.get("label_counts"), dict) else {}
            if not label_counts:
                label_counts = {
                    "ENTRY_READY": sum(1 for row in radar_rows if str(row.action_label or "") == "ENTRY_READY"),
                    "WATCH_WAIT_TRIGGER": sum(1 for row in radar_rows if str(row.action_label or "") == "WATCH_WAIT_TRIGGER"),
                    "NOT_RECOMMENDED": sum(1 for row in radar_rows if str(row.action_label or "") == "NOT_RECOMMENDED"),
                }
            top_symbols = [str(symbol or "").strip() for symbol in adoption.get("top_symbols", []) if str(symbol or "").strip()]
            top_set = set(top_symbols)
            outcome_rows = [row for row in radar_rows if not top_set or str(row.symbol) in top_set]
            if not outcome_rows:
                outcome_rows = radar_rows
            horizon_summary = []
            matured_any = False
            for horizon in horizons:
                overview = self._radar_outcome_overview(
                    self._radar_snapshot_outcome_rows(outcome_rows, lookahead=int(horizon)),
                    matured_only=True,
                )
                matured_any = matured_any or int(overview.get("matured_count") or 0) > 0
                horizon_summary.append({"lookahead_bars": int(horizon), **overview})
            tracked.append(
                {
                    "run_id": run_id,
                    "run_at": job.run_at.isoformat(timespec="seconds") if job.run_at else None,
                    "status": job.status,
                    "rows_upserted": int(job.rows_upserted or 0),
                    "trade_date": str(meta.get("trade_date") or ""),
                    "risk_profile": str(meta.get("risk_profile") or ""),
                    "threshold_offset": float(meta.get("threshold_offset") or 0.0),
                    "universe": str(meta.get("universe") or ""),
                    "degraded": bool(meta.get("degraded")),
                    "degrade_reasons": list(meta.get("degrade_reasons") or []),
                    "label_counts": label_counts,
                    "entry_ready_count": int(label_counts.get("ENTRY_READY") or 0),
                    "watch_count": int(label_counts.get("WATCH_WAIT_TRIGGER") or 0),
                    "actionable_count": int(label_counts.get("ENTRY_READY") or 0)
                    + int(label_counts.get("WATCH_WAIT_TRIGGER") or 0),
                    "adoption": adoption,
                    "horizon_summary": horizon_summary,
                    "matured": matured_any,
                }
            )
            if len(tracked) >= item_limit:
                break

        matured_runs = [item for item in tracked if bool(item.get("matured"))]
        latest = tracked[0] if tracked else None
        return {
            "status": "ok" if tracked else "empty",
            "verdict": "已有採用追蹤" if tracked else "尚未採用推薦設定",
            "risk_level": "ok" if matured_runs else "watch" if tracked else "neutral",
            "tracked_count": len(tracked),
            "matured_run_count": len(matured_runs),
            "latest": latest,
            "runs": tracked,
            "next_step": (
                "比較採用 run 的成熟 outcome，確認門檻推薦是否真的改善候選品質。"
                if matured_runs
                else "先累積採用後的 5 / 10 / 20 日 outcome，再判斷是否固定採用。"
            ),
        }

    def get_daily_radar_symbol_adoption_feedback(
        self,
        *,
        symbols: Iterable[str],
        lookahead_bars_list: Iterable[int] = (5, 10, 20),
        max_runs: int = 50,
        on_or_before: date | None = None,
    ) -> dict:
        symbol_set = {str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()}
        if not symbol_set:
            return {"status": "empty", "symbols": {}, "tracked_run_count": 0, "next_step": "沒有可評估的 symbol。"}
        horizons = tuple(sorted({max(1, min(60, int(value or 0))) for value in lookahead_bars_list if int(value or 0) > 0}))
        if not horizons:
            return {"status": "empty", "symbols": {}, "tracked_run_count": 0, "next_step": "沒有可評估的 lookahead horizon。"}
        stmt = (
            select(SyncJob)
            .where(SyncJob.job_name == "daily_radar")
            .order_by(SyncJob.run_at.desc(), SyncJob.id.desc())
            .limit(max(1, min(300, int(max_runs or 50) * 6)))
        )
        by_symbol: dict[str, dict[str, Any]] = {
            symbol: {
                "symbol": symbol,
                "run_ids": set(),
                "horizons": {},
                "returns": [],
            }
            for symbol in symbol_set
        }
        tracked_run_count = 0
        for job in self.session.execute(stmt).scalars().all():
            meta = self._safe_json_dict(job.error_msg)
            adoption = meta.get("threshold_adoption") if isinstance(meta.get("threshold_adoption"), dict) else {}
            run_id = str(meta.get("run_id") or "")
            if not adoption or not run_id:
                continue
            trade_date_text = str(meta.get("trade_date") or "")
            if on_or_before is not None and trade_date_text:
                try:
                    if datetime.strptime(trade_date_text[:10], "%Y-%m-%d").date() > on_or_before:
                        continue
                except Exception:
                    pass
            radar_rows = [row for row in self._daily_radar_rows_for_run(run_id) if str(row.symbol or "").strip().upper() in symbol_set]
            if not radar_rows:
                continue
            tracked_run_count += 1
            for horizon in horizons:
                for outcome in self._radar_snapshot_outcome_rows(radar_rows, lookahead=int(horizon)):
                    if not bool(outcome.get("matured")) or outcome.get("lookahead_return") is None:
                        continue
                    symbol = str(outcome.get("symbol") or "").strip().upper()
                    target = by_symbol.get(symbol)
                    if target is None:
                        continue
                    ret = float(outcome.get("lookahead_return") or 0.0)
                    target["run_ids"].add(run_id)
                    target["returns"].append(ret)
                    horizon_bucket = target["horizons"].setdefault(
                        int(horizon),
                        {"lookahead_bars": int(horizon), "evaluated_count": 0, "positive_count": 0, "return_sum": 0.0},
                    )
                    horizon_bucket["evaluated_count"] += 1
                    horizon_bucket["positive_count"] += 1 if ret > 0 else 0
                    horizon_bucket["return_sum"] += ret

        output: dict[str, dict] = {}
        for symbol, raw in by_symbol.items():
            returns = [float(value) for value in raw["returns"]]
            evaluated_count = len(returns)
            if evaluated_count <= 0:
                continue
            positive_count = sum(1 for value in returns if value > 0)
            positive_rate = positive_count / evaluated_count
            average_return = sum(returns) / evaluated_count
            horizon_summary = []
            for horizon, values in sorted(raw["horizons"].items()):
                count = int(values["evaluated_count"])
                if count <= 0:
                    continue
                horizon_summary.append(
                    {
                        "lookahead_bars": int(horizon),
                        "evaluated_count": count,
                        "positive_count": int(values["positive_count"]),
                        "positive_rate": int(values["positive_count"]) / count,
                        "average_return": float(values["return_sum"]) / count,
                    }
                )
            score = self._symbol_adoption_feedback_score(
                positive_rate=positive_rate,
                average_return=average_return,
                evaluated_count=evaluated_count,
                run_count=len(raw["run_ids"]),
            )
            confidence = self._symbol_adoption_feedback_confidence(
                evaluated_count=evaluated_count,
                run_count=len(raw["run_ids"]),
            )
            output[symbol] = {
                "symbol": symbol,
                "run_count": len(raw["run_ids"]),
                "evaluated_count": evaluated_count,
                "positive_count": positive_count,
                "positive_rate": positive_rate,
                "average_return": average_return,
                "score": score,
                "boost": round(score * 4.0, 2),
                "confidence_weight": confidence["weight"],
                "confidence_level": confidence["level"],
                "confidence_label": confidence["label"],
                "horizons": horizon_summary,
            }
        return {
            "status": "ok" if output else "empty",
            "symbols": output,
            "tracked_run_count": tracked_run_count,
            "next_step": (
                "將採用後成熟 outcome 回灌到標的排序。"
                if output
                else "採用後 outcome 尚未成熟，暫時不調整標的排序。"
            ),
        }

    def get_daily_radar_trading_feedback_summary(self, count: int = 50, limit: int = 100) -> dict:
        items = self.get_latest_daily_radar(count=max(1, min(50, int(count or 50))))
        rows = [item for item in items if isinstance(item, dict) and str(item.get("symbol") or "").strip()]
        if not rows:
            return {
                "status": "empty",
                "reason": "no_daily_radar",
                "verdict": "等待雷達資料",
                "risk_level": "neutral",
                "primary_action": "先產生每日雷達，再回看交易回饋。",
                "run_id": "",
                "run_date": "",
                "candidate_count": 0,
                "metrics": {},
                "alerts": [],
                "symbols": [],
                "latest_reconciliation": None,
                "latest_promotion_gate": None,
            }

        item_limit = max(10, min(300, int(limit or 100)))
        executions = self.list_recent_trading_execution_records(limit=item_limit)
        previews = self.list_recent_order_preview_records(limit=item_limit)
        promotion_gates = self.list_recent_promotion_gate_records(limit=10)
        reconciliations = self.list_recent_reconciliation_records(limit=10)
        symbols = {str(item.get("symbol") or "").strip() for item in rows}
        names = {str(item.get("symbol") or "").strip(): str(item.get("name") or "") for item in rows}
        actions = {str(item.get("symbol") or "").strip(): str(item.get("action_label") or "") for item in rows}
        ranks = {str(item.get("symbol") or "").strip(): int(float(item.get("rank") or 9999)) for item in rows}
        feedback_by_symbol: dict[str, dict] = {
            symbol: {
                "symbol": symbol,
                "name": names.get(symbol, ""),
                "action_label": actions.get(symbol, ""),
                "rank": ranks.get(symbol, 9999),
                "risk_level": "ok",
                "pending_preview_count": 0,
                "rejected_preview_count": 0,
                "accepted_preview_count": 0,
                "execution_count": 0,
                "live_execution_count": 0,
                "execution_error_count": 0,
                "execution_rejected_count": 0,
                "executed_count": 0,
                "issues": [],
                "latest_feedback_at": "",
            }
            for symbol in symbols
        }
        severity_rank = {"ok": 0, "watch": 1, "warn": 2, "danger": 3}

        def mark_symbol(symbol: str, *, severity: str, code: str, label: str, detail: str, action: str, at: object = None) -> None:
            entry = feedback_by_symbol.get(symbol)
            if not entry:
                return
            current = severity_rank.get(str(entry.get("risk_level") or "ok"), 0)
            incoming = severity_rank.get(severity, 1)
            if incoming > current:
                entry["risk_level"] = severity
            entry["issues"].append({"code": code, "label": label, "severity": severity, "detail": detail, "action": action})
            parsed = self._parse_datetime(at)
            if parsed:
                previous = self._parse_datetime(entry.get("latest_feedback_at"))
                if previous is None or parsed > previous:
                    entry["latest_feedback_at"] = parsed.isoformat(timespec="seconds")

        def update_latest(symbol: str, value: object) -> None:
            entry = feedback_by_symbol.get(symbol)
            parsed = self._parse_datetime(value)
            if not entry or not parsed:
                return
            previous = self._parse_datetime(entry.get("latest_feedback_at"))
            if previous is None or parsed > previous:
                entry["latest_feedback_at"] = parsed.isoformat(timespec="seconds")

        for row in previews:
            symbol = str(row.get("symbol") or "").strip()
            if symbol not in symbols:
                continue
            entry = feedback_by_symbol[symbol]
            status = str(row.get("status") or "").lower()
            update_latest(symbol, row.get("created_at"))
            if status in {"created", "decision_only"}:
                entry["pending_preview_count"] += 1
                mark_symbol(
                    symbol,
                    severity="warn",
                    code="pending_preview",
                    label="Preview 待決策",
                    detail=f"{symbol} 仍有 Preview 停在 {status or 'created'}。",
                    action="先確認、婉拒，或等過期後重建 Preview。",
                    at=row.get("created_at"),
                )
            elif status == "rejected":
                entry["rejected_preview_count"] += 1
                mark_symbol(
                    symbol,
                    severity="warn",
                    code="preview_rejected",
                    label="Preview 曾被婉拒",
                    detail=str(row.get("reason") or "近期 Preview 已被拒絕。"),
                    action="重建前先確認拒絕原因是否已解除。",
                    at=row.get("created_at"),
                )
            elif status == "accepted":
                entry["accepted_preview_count"] += 1

        for row in executions:
            symbol = str(row.get("symbol") or "").strip()
            if symbol not in symbols:
                continue
            entry = feedback_by_symbol[symbol]
            status = str(row.get("status") or "").lower()
            accepted = bool(row.get("accepted"))
            executed = bool(row.get("executed"))
            environment = str(row.get("environment") or "").lower()
            entry["execution_count"] += 1
            if environment == "live":
                entry["live_execution_count"] += 1
            if executed:
                entry["executed_count"] += 1
            update_latest(symbol, row.get("created_at"))
            if status == "error":
                entry["execution_error_count"] += 1
                mark_symbol(
                    symbol,
                    severity="danger",
                    code="execution_error",
                    label="送單錯誤",
                    detail=str(row.get("reason") or "近期 execution 發生 error。"),
                    action="先查 broker/API 回應與稽核紀錄，再評估是否重送。",
                    at=row.get("created_at"),
                )
            elif status == "rejected" or not accepted:
                entry["execution_rejected_count"] += 1
                mark_symbol(
                    symbol,
                    severity="warn",
                    code="execution_rejected",
                    label="送單被 gate 擋下",
                    detail=str(row.get("reason") or "近期 execution 未被接受。"),
                    action="確認是預期風控阻擋，或修正價格、部位、promotion gate 後再送。",
                    at=row.get("created_at"),
                )

        symbol_rows = [
            {
                **entry,
                "issue_count": len(entry["issues"]),
                "has_feedback": any(
                    int(entry.get(key) or 0) > 0
                    for key in (
                        "pending_preview_count",
                        "rejected_preview_count",
                        "accepted_preview_count",
                        "execution_count",
                        "execution_error_count",
                        "execution_rejected_count",
                    )
                ),
            }
            for entry in feedback_by_symbol.values()
        ]
        affected_symbols = [entry for entry in symbol_rows if entry["issue_count"] > 0 or entry["has_feedback"]]
        latest_reconciliation = reconciliations[0] if reconciliations else None
        latest_gate = promotion_gates[0] if promotion_gates else None

        pending_preview_count = sum(int(entry["pending_preview_count"]) for entry in symbol_rows)
        rejected_preview_count = sum(int(entry["rejected_preview_count"]) for entry in symbol_rows)
        execution_error_count = sum(int(entry["execution_error_count"]) for entry in symbol_rows)
        execution_rejected_count = sum(int(entry["execution_rejected_count"]) for entry in symbol_rows)
        live_execution_count = sum(int(entry["live_execution_count"]) for entry in symbol_rows)
        executed_count = sum(int(entry["executed_count"]) for entry in symbol_rows)

        alerts: list[dict] = []

        def add_alert(code: str, label: str, severity: str, detail: str, action: str) -> None:
            alerts.append({"code": code, "label": label, "severity": severity, "detail": detail, "action": action})

        if latest_reconciliation and not bool(latest_reconciliation.get("matched")):
            add_alert(
                "reconciliation_mismatch",
                "最近對帳不一致",
                "danger",
                ", ".join(latest_reconciliation.get("blocking_reasons") or ["reconciliation_mismatch"]),
                "先完成收盤對帳修正，再把候選標的升級到送單流程。",
            )
        elif not latest_reconciliation and (pending_preview_count + rejected_preview_count + live_execution_count + executed_count) > 0:
            add_alert(
                "reconciliation_missing",
                "尚無對帳紀錄",
                "warn",
                "已有 Preview 或 execution 回饋，但沒有最近對帳紀錄。",
                "收盤後補 reconcile，確認系統部位與券商一致。",
            )
        if latest_gate and not bool(latest_gate.get("accepted")):
            add_alert(
                "promotion_gate_blocked",
                "最近 promotion gate 未通過",
                "warn",
                str(latest_gate.get("reason") or ", ".join(latest_gate.get("blocking_reasons") or []) or "promotion gate 未通過"),
                "先補齊 paper 觀察、對帳與交易限制檢查。",
            )
        if execution_error_count > 0:
            add_alert(
                "execution_error",
                "候選標的有送單錯誤",
                "danger",
                f"{execution_error_count} 筆近期 execution error 命中目前候選。",
                "先查 execution error，再評估是否保留為可進場。",
            )
        if execution_rejected_count > 0:
            add_alert(
                "execution_rejected",
                "候選標的曾被 gate 擋下",
                "warn",
                f"{execution_rejected_count} 筆近期 execution rejected 命中目前候選。",
                "確認阻擋原因解除後，再建立新 Preview。",
            )
        if pending_preview_count > 0:
            add_alert(
                "pending_preview",
                "候選標的有待決策 Preview",
                "warn",
                f"{pending_preview_count} 筆 Preview 尚未決策。",
                "先清理待決策 Preview，避免同一標的重複研究或重複送單。",
            )

        danger_count = len([alert for alert in alerts if alert.get("severity") == "danger"]) + len(
            [entry for entry in symbol_rows if entry["risk_level"] == "danger"]
        )
        warn_count = len([alert for alert in alerts if alert.get("severity") == "warn"]) + len(
            [entry for entry in symbol_rows if entry["risk_level"] in {"warn", "watch"}]
        )
        if danger_count > 0:
            risk_level = "danger"
            verdict = "先處理交易風險"
            primary_action = "暫緩把命中標的升級到送單，先處理錯誤、對帳或 gate 阻擋。"
        elif warn_count > 0:
            risk_level = "warn"
            verdict = "交易回饋需留意"
            primary_action = "先清理待決策 Preview、確認 gate 阻擋原因，再挑無回饋風險標的。"
        else:
            risk_level = "ok"
            verdict = "交易回饋正常"
            primary_action = "可依候選品質、分散首選與配置診斷繼續研究。"

        sorted_symbols = sorted(
            affected_symbols,
            key=lambda item: (severity_rank.get(item["risk_level"], 0), item["issue_count"], -item["rank"]),
            reverse=True,
        )
        return {
            "status": "ok",
            "run_id": str(rows[0].get("run_id") or ""),
            "run_date": str(rows[0].get("run_date") or ""),
            "candidate_count": len(rows),
            "verdict": verdict,
            "risk_level": risk_level,
            "primary_action": primary_action,
            "metrics": {
                "affected_symbol_count": len(affected_symbols),
                "pending_preview_count": pending_preview_count,
                "rejected_preview_count": rejected_preview_count,
                "execution_error_count": execution_error_count,
                "execution_rejected_count": execution_rejected_count,
                "live_execution_count": live_execution_count,
                "executed_count": executed_count,
                "latest_reconciliation_matched": bool(latest_reconciliation.get("matched")) if latest_reconciliation else None,
                "latest_promotion_gate_accepted": bool(latest_gate.get("accepted")) if latest_gate else None,
            },
            "alerts": alerts[:6],
            "symbols": sorted_symbols[:8],
            "latest_reconciliation": latest_reconciliation,
            "latest_promotion_gate": latest_gate,
        }

    @staticmethod
    def _daily_radar_quality_summary(items: list[dict], data_health_overview: dict | None = None) -> dict:
        rows = [item for item in (items if isinstance(items, list) else []) if isinstance(item, dict)]
        if not rows:
            return {
                "status": "empty",
                "reason": "no_daily_radar",
                "verdict": "等待雷達資料",
                "risk_level": "neutral",
                "quality_score": 0,
                "run_id": "",
                "run_date": "",
                "candidate_count": 0,
                "metrics": {},
                "data_readiness": {
                    "status": "neutral",
                    "label": "等待資料",
                    "detail": "尚未有每日雷達候選，無法判讀資料缺口是否影響標的。",
                    "health_status": "",
                    "health_score": None,
                    "failed_scopes": [],
                    "affected_candidate_symbols": [],
                    "reason_codes": [],
                    "score_penalty": 0,
                },
                "alerts": [],
                "top_picks": [],
                "top_pick_scope": "none",
                "top_pick_scope_label": "等待候選",
                "group_summary": [],
            }

        def _num(value: object, default: float = 0.0) -> float:
            try:
                return float(value)
            except Exception:
                return default

        def _ratio(part: int | float, total: int | float) -> float:
            total_value = float(total or 0.0)
            return float(part or 0.0) / total_value if total_value > 0 else 0.0

        def _quality_good(value: object) -> bool:
            return str(value or "").strip().lower() in {"fresh", "complete", "ok", "good"}

        def _fundamental_weight(value: object) -> float:
            text = str(value or "").strip().lower()
            if text == "fresh":
                return 1.0
            if text == "partial":
                return 0.55
            return 0.0

        def _data_readiness(rows: list[dict], overview: dict | None) -> dict:
            if not isinstance(overview, dict) or not overview:
                return {
                    "status": "neutral",
                    "label": "未納入資料健康度",
                    "detail": "候選品質僅依雷達清單本身判讀。",
                    "health_status": "",
                    "health_score": None,
                    "failed_scopes": [],
                    "affected_candidate_symbols": [],
                    "reason_codes": [],
                    "score_penalty": 0,
                }
            candidate_by_symbol = {
                str(item.get("symbol") or "").strip(): item
                for item in rows
                if str(item.get("symbol") or "").strip()
            }
            candidate_symbols = set(candidate_by_symbol)
            sync_jobs = overview.get("sync_jobs") if isinstance(overview.get("sync_jobs"), list) else []
            candidate_critical_scopes = {"daily_bars", "institutional_chip"}
            advisory_scopes = {"attention_stocks", "disposition_periods"}
            optional_scopes = {"broker_agg_chip", "monthly_revenues", "news_events"}
            market_coverage = overview.get("market_data_coverage") if isinstance(overview.get("market_data_coverage"), dict) else {}
            market_datasets = market_coverage.get("datasets") if isinstance(market_coverage.get("datasets"), list) else []
            for dataset in market_datasets:
                if not isinstance(dataset, dict):
                    continue
                key = str(dataset.get("key") or "").strip()
                if key and (dataset.get("optional") or dataset.get("repairable") is False):
                    optional_scopes.add(key)
            scope_stats: dict[str, dict] = {}
            symbol_stats: dict[str, dict] = {}
            reason_codes: set[str] = set()
            partial_jobs: list[str] = []

            def tag_value(tags: object, prefix: str) -> str:
                tag_list = tags if isinstance(tags, list) else []
                for tag in tag_list:
                    text = str(tag or "")
                    if text.startswith(prefix):
                        return text[len(prefix) :].strip()
                return ""

            def scope_still_unresolved(symbol: str, scope: str) -> bool:
                item = candidate_by_symbol.get(symbol, {})
                if scope == "daily_bars" and _quality_good(item.get("data_quality")):
                    return False
                if scope == "institutional_chip":
                    chip_available = tag_value(item.get("reason_tags"), "CHIP_AVAILABLE=").lower()
                    if chip_available == "true":
                        return False
                return True

            def add_scope(scope: object, failed_count: object = 1, codes: list | None = None, symbols: list | None = None) -> None:
                key = str(scope or "").strip()
                if not key:
                    return
                row = scope_stats.setdefault(
                    key,
                    {
                        "scope": key,
                        "failed_count": 0,
                        "reason_codes": set(),
                        "symbols": set(),
                        "optional": key in optional_scopes,
                    },
                )
                try:
                    row["failed_count"] += int(failed_count or 1)
                except Exception:
                    row["failed_count"] += 1
                for code in codes or []:
                    text = str(code or "").strip()
                    if text:
                        row["reason_codes"].add(text)
                        reason_codes.add(text)
                for symbol in symbols or []:
                    text = str(symbol or "").strip()
                    if text:
                        row["symbols"].add(text)

            def add_symbol(symbol: object, failed_count: object = 1, scopes: list | None = None, codes: list | None = None) -> None:
                key = str(symbol or "").strip()
                if not key or key not in candidate_symbols:
                    return
                core_scopes = sorted({str(scope or "").strip() for scope in (scopes or []) if str(scope or "").strip() in candidate_critical_scopes})
                core_scopes = [scope for scope in core_scopes if scope_still_unresolved(key, scope)]
                if not core_scopes:
                    return
                row = symbol_stats.setdefault(key, {"symbol": key, "failed_count": 0, "scopes": set(), "reason_codes": set()})
                try:
                    row["failed_count"] += int(failed_count or 1)
                except Exception:
                    row["failed_count"] += 1
                for scope in core_scopes:
                    text = str(scope or "").strip()
                    if text:
                        row["scopes"].add(text)
                for code in codes or []:
                    text = str(code or "").strip()
                    if text:
                        row["reason_codes"].add(text)
                        reason_codes.add(text)

            for job in sync_jobs:
                if not isinstance(job, dict):
                    continue
                status = str(job.get("status") or job.get("health_status") or "").lower()
                if status and status != "ok":
                    partial_jobs.append(str(job.get("job_name") or "sync_job"))
                summary = job.get("failure_summary") if isinstance(job.get("failure_summary"), dict) else {}
                for code in summary.get("reason_codes") or []:
                    text = str(code or "").strip()
                    if text:
                        reason_codes.add(text)
                scope_rows = summary.get("scope_failures") if isinstance(summary.get("scope_failures"), list) else []
                if scope_rows:
                    for row in scope_rows:
                        if not isinstance(row, dict):
                            continue
                        add_scope(
                            row.get("scope") or row.get("name"),
                            row.get("failed_count"),
                            row.get("reason_codes") if isinstance(row.get("reason_codes"), list) else [],
                            row.get("symbols") if isinstance(row.get("symbols"), list) else [],
                        )
                else:
                    for row in summary.get("components") if isinstance(summary.get("components"), list) else []:
                        if not isinstance(row, dict):
                            continue
                        component_status = str(row.get("status") or "").lower()
                        if component_status and component_status != "ok":
                            add_scope(row.get("name"), row.get("failed_count"), [])
                for row in summary.get("symbol_failures") if isinstance(summary.get("symbol_failures"), list) else []:
                    if not isinstance(row, dict):
                        continue
                    add_symbol(
                        row.get("symbol"),
                        row.get("failed_count"),
                        row.get("scopes") if isinstance(row.get("scopes"), list) else [],
                        row.get("reason_codes") if isinstance(row.get("reason_codes"), list) else [],
                    )

            failed_scopes = [
                {
                    "scope": scope,
                    "failed_count": int(data.get("failed_count") or 0),
                    "reason_codes": sorted(data.get("reason_codes") or []),
                    "symbols": sorted(data.get("symbols") or [])[:8],
                    "optional": bool(data.get("optional")),
                }
                for scope, data in scope_stats.items()
            ]
            failed_scopes.sort(key=lambda item: (-int(item["failed_count"]), item["scope"]))
            core_failed_scopes = [row for row in failed_scopes if row["scope"] in candidate_critical_scopes]
            advisory_failed_scopes = [row for row in failed_scopes if row["scope"] in advisory_scopes]
            non_optional_failed_scopes = [row for row in failed_scopes if not row.get("optional")]
            optional_failed_scopes = [row for row in failed_scopes if row.get("optional")]
            affected_symbols = [
                {
                    "symbol": symbol,
                    "failed_count": int(data.get("failed_count") or 0),
                    "scopes": sorted(data.get("scopes") or []),
                    "reason_codes": sorted(data.get("reason_codes") or []),
                }
                for symbol, data in symbol_stats.items()
            ]
            affected_symbols.sort(key=lambda item: (-int(item["failed_count"]), item["symbol"]))

            health_status = str(overview.get("status") or "").lower()
            health_score = overview.get("score")
            upstream_blocked = bool({"upstream_forbidden", "upstream_unauthorized"} & reason_codes)
            notice_unavailable = "shioaji_notice_unavailable" in reason_codes
            if affected_symbols or (core_failed_scopes and upstream_blocked):
                status = "danger"
                label = "資料缺口影響候選"
                score_penalty = 18
            elif core_failed_scopes or advisory_failed_scopes or non_optional_failed_scopes or health_status in {"danger", "warn"}:
                status = "warn"
                label = "資料可信度需確認"
                score_penalty = 8 if (core_failed_scopes or advisory_failed_scopes or non_optional_failed_scopes) else 4
            else:
                status = "ok"
                label = "資料可用"
                score_penalty = 0
            detail_parts = []
            if affected_symbols:
                detail_parts.append(f"{len(affected_symbols)} 檔候選命中資料缺口")
            detail_failed_scopes = non_optional_failed_scopes or optional_failed_scopes
            if detail_failed_scopes:
                prefix = "缺口" if non_optional_failed_scopes else "加值缺口"
                detail_parts.append(prefix + "：" + "、".join(row["scope"] for row in detail_failed_scopes[:3]))
            if upstream_blocked:
                detail_parts.append("FinMind 權限/token 需確認")
            if notice_unavailable:
                detail_parts.append("Shioaji 注意股 API 不可用")
            if not detail_parts and health_status:
                detail_parts.append(f"資料健康度 {health_status}")
            return {
                "status": status,
                "label": label,
                "detail": "；".join(detail_parts) or "最新同步未回報會影響候選的資料缺口。",
                "health_status": health_status,
                "health_score": health_score,
                "failed_scopes": failed_scopes[:8],
                "optional_failed_scopes": optional_failed_scopes[:8],
                "affected_candidate_symbols": affected_symbols[:8],
                "reason_codes": sorted(reason_codes),
                "partial_jobs": partial_jobs[:8],
                "score_penalty": score_penalty,
            }

        count = len(rows)
        data_readiness = _data_readiness(rows, data_health_overview)
        symbol_data_gaps = {
            str(item.get("symbol") or ""): item for item in data_readiness.get("affected_candidate_symbols", [])
        }
        entry_count = sum(1 for item in rows if str(item.get("action_label") or "") == "ENTRY_READY")
        watch_count = sum(1 for item in rows if str(item.get("action_label") or "") == "WATCH_WAIT_TRIGGER")
        avoid_count = sum(1 for item in rows if str(item.get("action_label") or "") == "NOT_RECOMMENDED")
        blocker_count = sum(len(item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []) for item in rows)
        no_blocker_count = sum(
            1 for item in rows if not (isinstance(item.get("blocker_tags"), list) and len(item.get("blocker_tags") or []) > 0)
        )
        fresh_count = sum(1 for item in rows if _quality_good(item.get("data_quality")))
        fundamental_score_sum = sum(_fundamental_weight(item.get("fundamental_data_quality")) for item in rows)
        low_risk_count = sum(1 for item in rows if _num(item.get("risk_score")) <= 0.25)
        hot_count = sum(1 for item in rows if str(item.get("atr_heat") or "").lower() == "hot")
        elevated_count = sum(1 for item in rows if str(item.get("atr_heat") or "").lower() == "elevated")
        reliability_positive_count = sum(
            1
            for item in rows
            if int(_num(item.get("outcome_reliability_samples"))) > 0 and _num(item.get("outcome_reliability_boost")) > 0
        )
        reliability_sampled_count = sum(1 for item in rows if int(_num(item.get("outcome_reliability_samples"))) > 0)
        group_counts = Counter(str(item.get("industry_group") or "其他") for item in rows)
        group_count = len(group_counts)
        top_group, top_group_count = group_counts.most_common(1)[0]
        top_group_share = _ratio(top_group_count, count)
        entry_ratio = _ratio(entry_count, count)
        no_blocker_ratio = _ratio(no_blocker_count, count)
        fresh_ratio = _ratio(fresh_count, count)
        fundamental_ratio = _ratio(fundamental_score_sum, count)
        low_risk_ratio = _ratio(low_risk_count, count)
        reliability_ratio = _ratio(reliability_positive_count, count)
        diversification_ratio = min(1.0, _ratio(group_count, min(count, 5)))
        concentration_penalty = max(0.0, top_group_share - 0.45) * 0.55
        heat_penalty = _ratio((hot_count * 1.0) + (elevated_count * 0.45), count) * 10.0
        quality_score = (
            (entry_ratio * 22.0)
            + (no_blocker_ratio * 18.0)
            + (fresh_ratio * 16.0)
            + (fundamental_ratio * 12.0)
            + (low_risk_ratio * 12.0)
            + (reliability_ratio * 10.0)
            + (max(0.0, diversification_ratio - concentration_penalty) * 10.0)
            - heat_penalty
        )
        quality_score -= float(data_readiness.get("score_penalty") or 0)
        quality_score = max(0, min(100, int(round(quality_score))))
        average_entry = sum(_num(item.get("entry_score")) for item in rows) / count
        average_risk = sum(_num(item.get("risk_score")) for item in rows) / count
        average_reliability_boost = sum(_num(item.get("outcome_reliability_boost")) for item in rows) / count

        alerts: list[dict] = []

        def add_alert(code: str, label: str, severity: str, detail: str, action: str) -> None:
            alerts.append({"code": code, "label": label, "severity": severity, "detail": detail, "action": action})

        if data_readiness.get("status") == "danger":
            add_alert(
                "data_integrity_blocked",
                "資料缺口影響候選可信度",
                "danger",
                str(data_readiness.get("detail") or "候選命中資料缺口。"),
                "先處理資料源修復佇列，重跑同步後再挑首選標的。",
            )
        elif data_readiness.get("status") == "warn":
            add_alert(
                "data_integrity_watch",
                "資料可信度需確認",
                "warn",
                str(data_readiness.get("detail") or "資料健康度仍需確認。"),
                "先確認資料健康度與 sync job，再用觀察池而非直接進場。",
            )
        if data_readiness.get("affected_candidate_symbols"):
            add_alert(
                "candidate_data_gap",
                "候選命中代號缺口",
                "danger",
                "、".join(item["symbol"] for item in data_readiness.get("affected_candidate_symbols", [])[:5]),
                "先避開命中資料缺口的候選，或重試小範圍同步後重新整理品質摘要。",
            )
        if entry_count == 0:
            add_alert("no_entry_ready", "沒有可進場標的", "warn", "本輪沒有 ENTRY_READY，清單主要用於觀察。", "先看觸發觀察與穩定候選，不急著建立 Preview。")
        if no_blocker_ratio < 0.55:
            add_alert(
                "blockers_high",
                "阻擋偏多",
                "danger" if no_blocker_ratio < 0.35 else "warn",
                f"無阻擋標的占比 {no_blocker_ratio:.0%}，需要先處理資料、流動性或事件風險。",
                "先切到無阻擋篩選，再檢查剩餘標的。",
            )
        if fresh_ratio < 0.70:
            add_alert(
                "data_freshness",
                "資料 freshness 偏弱",
                "warn",
                f"資料新鮮標的占比 {fresh_ratio:.0%}，可能影響分數可信度。",
                "先重新同步日線、籌碼與注意/處置股資料。",
            )
        if fundamental_ratio < 0.45:
            add_alert(
                "fundamental_coverage",
                "基本面覆蓋不足",
                "watch",
                f"基本面可用度 {fundamental_ratio:.0%}，營收/財報/新聞分數參考性偏低。",
                "先補 FinMind 基本面與新聞資料，再看基本面雷達。",
            )
        if top_group_share >= 0.60 and count >= 4:
            add_alert(
                "group_concentration",
                "族群集中",
                "warn",
                f"{top_group} 佔 {top_group_share:.0%}，候選池分散度不足。",
                "先使用分散首選或套用族群篩選，避免清單被單一族群主導。",
            )
        if hot_count > 0:
            add_alert(
                "atr_hot",
                "存在過熱波動",
                "watch",
                f"{hot_count} 檔 ATR 標示過熱。",
                "進 Preview 前先確認價格保護與停損距離。",
            )
        if reliability_sampled_count == 0:
            add_alert(
                "reliability_missing",
                "可靠度樣本不足",
                "watch",
                "本輪候選尚未累積成熟 outcome 樣本。",
                "先保留觀察，等待 5/10/20 日後續表現回饋。",
            )

        if quality_score >= 76 and entry_count > 0 and no_blocker_ratio >= 0.70:
            verdict = "可優先研究"
            risk_level = "ok"
            primary_action = "先檢查高分、無阻擋且族群分散的標的。"
        elif quality_score >= 60:
            verdict = "可研究，先分散"
            risk_level = "watch"
            primary_action = "先看分散首選與無阻擋標的，再決定是否建立 Preview。"
        elif quality_score >= 45:
            verdict = "偏觀察"
            risk_level = "warn"
            primary_action = "先補資料、處理阻擋或等觸發條件成熟。"
        else:
            verdict = "先處理風險"
            risk_level = "danger"
            primary_action = "暫不急著研究個股，先處理資料品質、阻擋與族群集中。"
        if data_readiness.get("status") == "danger":
            verdict = "先修資料再選股"
            risk_level = "danger"
            primary_action = "候選命中資料缺口，先修資料源並重跑每日雷達。"
        elif data_readiness.get("status") == "warn" and risk_level in {"ok", "watch"}:
            verdict = "可觀察，先驗資料"
            risk_level = "warn"
            primary_action = "先確認資料健康度，首選標的只作觀察，不急著建立 Preview。"

        def _pick_score(item: dict) -> float:
            no_blocker = not (isinstance(item.get("blocker_tags"), list) and len(item.get("blocker_tags") or []) > 0)
            risk_adjusted = _num(item.get("risk_adjusted_score"))
            symbol = str(item.get("symbol") or "").strip()
            symbol_gap_penalty = 14.0 if symbol in symbol_data_gaps else 0.0
            global_gap_penalty = 4.0 if data_readiness.get("status") == "warn" else 7.0 if data_readiness.get("status") == "danger" else 0.0
            base_score = (
                risk_adjusted
                if risk_adjusted > 0
                else (_num(item.get("entry_score")) - (min(1.0, _num(item.get("risk_score"))) * 20.0))
            )
            return (
                base_score
                + (4.0 if no_blocker else -8.0)
                + (3.0 if _quality_good(item.get("data_quality")) else -3.0)
                + (_fundamental_weight(item.get("fundamental_data_quality")) * 3.0)
                - symbol_gap_penalty
                - global_gap_penalty
            )

        sorted_quality_rows = sorted(rows, key=lambda row: (_pick_score(row), -_num(row.get("rank"), 9999)), reverse=True)

        def _pick_payload(item: dict) -> dict:
            symbol = str(item.get("symbol") or "")
            gap = symbol_data_gaps.get(symbol, {})
            blocker_tags = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
            return {
                "symbol": symbol,
                "name": str(item.get("name") or ""),
                "industry_group": str(item.get("industry_group") or "其他"),
                "action_label": str(item.get("action_label") or ""),
                "entry_score": _num(item.get("entry_score")),
                "risk_adjusted_score": _num(item.get("risk_adjusted_score")),
                "risk_score": _num(item.get("risk_score")),
                "candidate_quality_score": round(_pick_score(item), 2),
                "data_quality": str(item.get("data_quality") or "unknown"),
                "fundamental_data_quality": str(item.get("fundamental_data_quality") or "missing"),
                "outcome_reliability_boost": _num(item.get("outcome_reliability_boost")),
                "blocker_count": len(blocker_tags),
                "atr_heat": str(item.get("atr_heat") or "normal"),
                "data_gap_count": int(gap.get("failed_count") or 0) if isinstance(gap, dict) else 0,
                "data_gap_scopes": list(gap.get("scopes") or []) if isinstance(gap, dict) else [],
            }

        lane_meta = {
            "research_now": {
                "label": "可先研究",
                "summary": "資料站穩、無阻擋，適合先打開詳情或建立 Preview。",
                "action": "先檢查價格、部位與觸發條件。",
            },
            "observe_only": {
                "label": "只觀察",
                "summary": "分數可看，但仍需等觸發、資料確認或 ATR 風險降溫。",
                "action": "先放觀察池，等條件成熟再升級。",
            },
            "repair_first": {
                "label": "修資料後再看",
                "summary": "候選命中資料缺口，分數可信度不足。",
                "action": "先重試同步，再重新整理候選品質。",
            },
            "skip_for_now": {
                "label": "先略過",
                "summary": "存在阻擋、資料過期、非觀察型過熱或不建議狀態。",
                "action": "除非阻擋解除，否則先不要建立 Preview。",
            },
        }

        def _quality_lane_key(item: dict) -> str:
            symbol = str(item.get("symbol") or "").strip()
            blocker_tags = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
            action_label = str(item.get("action_label") or "")
            has_gap = symbol in symbol_data_gaps
            if has_gap:
                return "repair_first"
            if action_label == "WATCH_WAIT_TRIGGER" and not blocker_tags and _quality_good(item.get("data_quality")):
                return "observe_only"
            if (
                blocker_tags
                or action_label == "NOT_RECOMMENDED"
                or not _quality_good(item.get("data_quality"))
                or str(item.get("atr_heat") or "").lower() == "hot"
            ):
                return "skip_for_now"
            if data_readiness.get("status") != "danger" and action_label == "ENTRY_READY":
                return "research_now"
            return "observe_only"

        lane_buckets: dict[str, list[dict]] = {key: [] for key in lane_meta}
        for item in sorted_quality_rows:
            lane_key = _quality_lane_key(item)
            lane_buckets[lane_key].append(_pick_payload(item))
        quality_lanes = [
            {
                "key": key,
                "label": meta["label"],
                "summary": meta["summary"],
                "action": meta["action"],
                "count": len(lane_buckets[key]),
                "items": lane_buckets[key][:4],
            }
            for key, meta in lane_meta.items()
        ]
        top_pick_rows = [
            item
            for item in sorted_quality_rows
            if _quality_lane_key(item) in {"research_now", "observe_only"}
        ]
        top_picks = [_pick_payload(item) for item in top_pick_rows[:5]]
        top_pick_scope = "actionable_quality" if top_picks else "no_actionable_candidates"
        top_pick_scope_label = "可研究/只觀察" if top_picks else "沒有可行動首選"

        return {
            "status": "ok",
            "run_id": str(rows[0].get("run_id") or ""),
            "run_date": str(rows[0].get("run_date") or ""),
            "candidate_count": count,
            "quality_score": quality_score,
            "verdict": verdict,
            "risk_level": risk_level,
            "primary_action": primary_action,
            "data_readiness": data_readiness,
            "metrics": {
                "entry_count": entry_count,
                "watch_count": watch_count,
                "avoid_count": avoid_count,
                "entry_ratio": entry_ratio,
                "no_blocker_count": no_blocker_count,
                "no_blocker_ratio": no_blocker_ratio,
                "blocker_count": blocker_count,
                "fresh_count": fresh_count,
                "fresh_ratio": fresh_ratio,
                "fundamental_coverage_ratio": fundamental_ratio,
                "low_risk_count": low_risk_count,
                "low_risk_ratio": low_risk_ratio,
                "average_entry_score": average_entry,
                "average_risk_score": average_risk,
                "reliability_positive_count": reliability_positive_count,
                "reliability_sampled_count": reliability_sampled_count,
                "reliability_positive_ratio": reliability_ratio,
                "average_reliability_boost": average_reliability_boost,
                "group_count": group_count,
                "top_group": top_group,
                "top_group_count": top_group_count,
                "top_group_share": top_group_share,
                "hot_count": hot_count,
                "elevated_count": elevated_count,
                "data_readiness_status": data_readiness.get("status"),
                "data_readiness_score_penalty": data_readiness.get("score_penalty"),
                "data_gap_candidate_count": len(data_readiness.get("affected_candidate_symbols") or []),
            },
            "alerts": alerts[:6],
            "top_picks": top_picks,
            "top_pick_scope": top_pick_scope,
            "top_pick_scope_label": top_pick_scope_label,
            "quality_lanes": quality_lanes,
            "group_summary": [
                {"industry_group": group, "count": group_count_value, "share": _ratio(group_count_value, count)}
                for group, group_count_value in group_counts.most_common(6)
            ],
        }

    def get_daily_radar_change_summary(self, *, limit: int = 8) -> dict:
        item_limit = max(1, min(20, int(limit or 8)))
        runs, duplicate_run_count = self._latest_daily_radar_runs_by_trade_date(limit=2)
        sample_metadata = {
            "sample_unit": "trade_date",
            "available_days": len(runs),
            "duplicate_run_count": duplicate_run_count,
        }
        if not runs:
            return {
                "status": "insufficient",
                "reason": "no_daily_radar",
                **sample_metadata,
                "latest_run_id": "",
                "previous_run_id": "",
                "new_items": [],
                "recurring_items": [],
                "dropped_items": [],
                "improved_items": [],
                "weakened_items": [],
            }

        latest_run_id = str(runs[0][0])
        latest_rows = self._daily_radar_rows_for_run(latest_run_id)
        latest_items = {item["symbol"]: item for item in (self._radar_snapshot_to_dict(row) for row in latest_rows)}
        if len(runs) < 2:
            return {
                "status": "insufficient",
                "reason": "no_previous_daily_radar",
                **sample_metadata,
                "latest_run_id": latest_run_id,
                "latest_run_date": latest_rows[0].run_date.isoformat() if latest_rows else None,
                "previous_run_id": "",
                "previous_run_date": None,
                "latest_count": len(latest_items),
                "previous_count": 0,
                "new_count": len(latest_items),
                "recurring_count": 0,
                "dropped_count": 0,
                "entry_ready_new_count": sum(1 for item in latest_items.values() if item.get("action_label") == "ENTRY_READY"),
                "entry_ready_recurring_count": 0,
                "improved_count": 0,
                "weakened_count": 0,
                "new_items": [self._daily_radar_change_item(item, None) for item in self._ranked_items(latest_items.values())[:item_limit]],
                "recurring_items": [],
                "dropped_items": [],
                "improved_items": [],
                "weakened_items": [],
            }

        previous_run_id = str(runs[1][0])
        previous_rows = self._daily_radar_rows_for_run(previous_run_id)
        previous_items = {item["symbol"]: item for item in (self._radar_snapshot_to_dict(row) for row in previous_rows)}
        latest_symbols = set(latest_items)
        previous_symbols = set(previous_items)
        new_symbols = latest_symbols - previous_symbols
        recurring_symbols = latest_symbols & previous_symbols
        dropped_symbols = previous_symbols - latest_symbols
        change_items = [
            self._daily_radar_change_item(latest_items[symbol], previous_items[symbol])
            for symbol in recurring_symbols
        ]
        improved_items = [
            item
            for item in change_items
            if float(item.get("entry_score_delta") or 0.0) > 0.0001 or int(item.get("rank_delta") or 0) < 0
        ]
        weakened_items = [
            item
            for item in change_items
            if float(item.get("entry_score_delta") or 0.0) < -0.0001 or int(item.get("rank_delta") or 0) > 0
        ]
        return {
            "status": "ok",
            "reason": "",
            **sample_metadata,
            "latest_run_id": latest_run_id,
            "latest_run_date": latest_rows[0].run_date.isoformat() if latest_rows else None,
            "previous_run_id": previous_run_id,
            "previous_run_date": previous_rows[0].run_date.isoformat() if previous_rows else None,
            "latest_count": len(latest_items),
            "previous_count": len(previous_items),
            "new_count": len(new_symbols),
            "recurring_count": len(recurring_symbols),
            "dropped_count": len(dropped_symbols),
            "entry_ready_new_count": sum(
                1 for symbol in new_symbols if latest_items[symbol].get("action_label") == "ENTRY_READY"
            ),
            "entry_ready_recurring_count": sum(
                1 for symbol in recurring_symbols if latest_items[symbol].get("action_label") == "ENTRY_READY"
            ),
            "improved_count": len(improved_items),
            "weakened_count": len(weakened_items),
            "new_items": [
                self._daily_radar_change_item(latest_items[symbol], None)
                for symbol in self._ranked_symbols(new_symbols, latest_items)[:item_limit]
            ],
            "recurring_items": [
                self._daily_radar_change_item(latest_items[symbol], previous_items[symbol])
                for symbol in self._ranked_symbols(recurring_symbols, latest_items)[:item_limit]
            ],
            "dropped_items": [
                self._daily_radar_change_item(None, previous_items[symbol])
                for symbol in self._ranked_symbols(dropped_symbols, previous_items)[:item_limit]
            ],
            "improved_items": sorted(
                improved_items,
                key=lambda item: (
                    -float(item.get("entry_score_delta") or 0.0),
                    int(item.get("rank_delta") or 0),
                    int(item.get("rank") or 9999),
                ),
            )[:item_limit],
            "weakened_items": sorted(
                weakened_items,
                key=lambda item: (
                    float(item.get("entry_score_delta") or 0.0),
                    -int(item.get("rank_delta") or 0),
                    int(item.get("rank") or 9999),
                ),
            )[:item_limit],
        }

    def _latest_daily_radar_runs_by_trade_date(self, *, limit: int) -> tuple[list[tuple], int]:
        run_limit = max(1, min(20, int(limit or 1)))
        run_stmt = (
            select(
                DailyRadarSnapshot.run_id,
                func.max(DailyRadarSnapshot.created_at).label("latest_created"),
                func.max(DailyRadarSnapshot.run_date).label("run_date"),
            )
            .group_by(DailyRadarSnapshot.run_id)
            .order_by(func.max(DailyRadarSnapshot.created_at).desc())
            .limit(min(240, max(run_limit * 8, run_limit)))
        )
        raw_runs = list(self.session.execute(run_stmt).all())
        unique_runs: list[tuple] = []
        seen_dates: set[date] = set()
        duplicate_run_count = 0
        for run in raw_runs:
            run_date = run[2]
            if run_date in seen_dates:
                duplicate_run_count += 1
                continue
            seen_dates.add(run_date)
            if len(unique_runs) >= run_limit:
                continue
            unique_runs.append(run)
        return unique_runs, duplicate_run_count

    def _daily_radar_rows_for_run(self, run_id: str) -> list[DailyRadarSnapshot]:
        stmt = (
            select(DailyRadarSnapshot)
            .where(DailyRadarSnapshot.run_id == run_id)
            .order_by(DailyRadarSnapshot.rank.asc(), DailyRadarSnapshot.entry_score.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    @staticmethod
    def _ranked_items(items: Iterable[dict]) -> list[dict]:
        def key(item: dict) -> tuple[int, float, str]:
            try:
                rank = int(item.get("rank") or 9999)
            except Exception:
                rank = 9999
            try:
                score = float(item.get("entry_score") or 0.0)
            except Exception:
                score = 0.0
            return rank, -score, str(item.get("symbol") or "")

        return sorted(items, key=key)

    def _ranked_symbols(self, symbols: set[str], items: dict[str, dict]) -> list[str]:
        return [item["symbol"] for item in self._ranked_items(items[symbol] for symbol in symbols if symbol in items)]

    @staticmethod
    def _daily_radar_change_item(current: dict | None, previous: dict | None) -> dict:
        base = current or previous or {}
        current_rank = current.get("rank") if current else None
        previous_rank = previous.get("rank") if previous else None
        current_score = current.get("entry_score") if current else None
        previous_score = previous.get("entry_score") if previous else None
        rank_delta = (
            int(current_rank) - int(previous_rank)
            if current_rank is not None and previous_rank is not None
            else None
        )
        entry_score_delta = (
            float(current_score) - float(previous_score)
            if current_score is not None and previous_score is not None
            else None
        )
        blocker_tags = current.get("blocker_tags") if current else previous.get("blocker_tags") if previous else []
        return {
            "symbol": base.get("symbol", ""),
            "name": base.get("name", ""),
            "industry_group": base.get("industry_group", "其他"),
            "rank": current_rank,
            "previous_rank": previous_rank,
            "rank_delta": rank_delta,
            "entry_score": current_score,
            "previous_entry_score": previous_score,
            "entry_score_delta": entry_score_delta,
            "action_label": current.get("action_label") if current else None,
            "previous_action_label": previous.get("action_label") if previous else None,
            "risk_score": current.get("risk_score") if current else None,
            "previous_risk_score": previous.get("risk_score") if previous else None,
            "blocker_count": len(blocker_tags) if isinstance(blocker_tags, list) else 0,
        }

    def get_daily_radar_stability_summary(self, *, lookback_runs: int = 5, limit: int = 8) -> dict:
        run_limit = max(2, min(20, int(lookback_runs or 5)))
        item_limit = max(1, min(20, int(limit or 8)))
        runs, duplicate_run_count = self._latest_daily_radar_runs_by_trade_date(limit=run_limit)
        sample_metadata = {
            "sample_unit": "trade_date",
            "lookback_days": run_limit,
            "available_days": len(runs),
            "duplicate_run_count": duplicate_run_count,
        }
        if not runs:
            return {
                "status": "insufficient",
                "reason": "no_daily_radar",
                "lookback_runs": run_limit,
                "available_runs": 0,
                **sample_metadata,
                "stable_items": [],
            }
        run_ids = [str(row[0]) for row in runs]
        run_dates = [row[2].isoformat() if row[2] else None for row in runs]
        run_items = [
            {
                item["symbol"]: item
                for item in enrich_risk_quality(
                    [self._radar_snapshot_to_dict(row) for row in self._daily_radar_rows_for_run(run_id)]
                )
            }
            for run_id in run_ids
        ]
        latest_items = run_items[0] if run_items else {}
        if len(run_items) < 2:
            return {
                "status": "insufficient",
                "reason": "not_enough_runs",
                "lookback_runs": run_limit,
                "available_runs": len(run_items),
                **sample_metadata,
                "latest_run_id": run_ids[0],
                "latest_run_date": run_dates[0],
                "latest_count": len(latest_items),
                "stable_count": 0,
                "stable_items": [],
            }

        stability_items = [
            self._daily_radar_stability_item(symbol, run_items, len(run_items))
            for symbol in latest_items
        ]
        recurring_items = [item for item in stability_items if int(item.get("streak_count") or 0) >= 2]
        actionable_labels = {"ENTRY_READY", "WATCH_WAIT_TRIGGER"}
        non_actionable_stable_count = sum(
            1 for item in recurring_items if str(item.get("action_label") or "") not in actionable_labels
        )
        stable_items = [
            item for item in recurring_items if str(item.get("action_label") or "") in actionable_labels
        ]
        stable_items = sorted(
            stable_items,
            key=lambda item: (
                -float(item.get("stability_score") or 0.0),
                -int(item.get("streak_count") or 0),
                -int(item.get("seen_count") or 0),
                int(item.get("rank") or 9999),
                str(item.get("symbol") or ""),
            ),
        )
        entry_ready_stable = [item for item in stable_items if item.get("action_label") == "ENTRY_READY"]
        risk_quality_scores = [
            float(item.get("risk_quality_score") or 0.0)
            for item in stable_items
            if item.get("risk_quality_score") not in (None, "")
        ]
        improving_quality_count = sum(1 for item in stable_items if str(item.get("risk_quality_trend") or "") == "improving")
        weakening_quality_count = sum(1 for item in stable_items if str(item.get("risk_quality_trend") or "") == "weakening")
        average_streak = (
            sum(int(item.get("streak_count") or 0) for item in stable_items) / len(stable_items)
            if stable_items
            else 0.0
        )
        return {
            "status": "ok",
            "reason": "",
            "lookback_runs": run_limit,
            "available_runs": len(run_items),
            **sample_metadata,
            "latest_run_id": run_ids[0],
            "latest_run_date": run_dates[0],
            "oldest_run_id": run_ids[-1],
            "oldest_run_date": run_dates[-1],
            "latest_count": len(latest_items),
            "stable_count": len(stable_items),
            "non_actionable_stable_count": non_actionable_stable_count,
            "strong_streak_count": sum(1 for item in stable_items if int(item.get("streak_count") or 0) >= 3),
            "entry_ready_stable_count": len(entry_ready_stable),
            "average_risk_quality_score": (
                sum(risk_quality_scores) / len(risk_quality_scores)
                if risk_quality_scores
                else 0.0
            ),
            "improving_risk_quality_count": improving_quality_count,
            "weakening_risk_quality_count": weakening_quality_count,
            "average_streak": average_streak,
            "stable_items": stable_items[:item_limit],
        }

    @staticmethod
    def _daily_radar_stability_item(symbol: str, run_items: list[dict[str, dict]], run_count: int) -> dict:
        appearances = [items[symbol] for items in run_items if symbol in items]
        latest = run_items[0].get(symbol, {}) if run_items else {}
        streak_count = 0
        for items in run_items:
            if symbol not in items:
                break
            streak_count += 1
        ranks = [int(item.get("rank") or 9999) for item in appearances]
        scores = [float(item.get("entry_score") or 0.0) for item in appearances]
        risk_quality_scores = [float(item.get("risk_quality_score") or 0.0) for item in appearances]
        latest_score = float(latest.get("entry_score") or 0.0)
        latest_risk_quality = float(latest.get("risk_quality_score") or 0.0)
        oldest = appearances[-1] if appearances else latest
        score_delta = latest_score - float(oldest.get("entry_score") or 0.0)
        risk_quality_delta = latest_risk_quality - float(oldest.get("risk_quality_score") or 0.0)
        rank_delta = int(latest.get("rank") or 9999) - int(oldest.get("rank") or 9999)
        blocker_runs = sum(
            1
            for item in appearances
            if isinstance(item.get("blocker_tags"), list) and len(item.get("blocker_tags")) > 0
        )
        entry_ready_count = sum(1 for item in appearances if item.get("action_label") == "ENTRY_READY")
        seen_count = len(appearances)
        if risk_quality_delta >= 5.0:
            risk_quality_trend = "improving"
            risk_quality_trend_label = "風險品質轉強"
        elif risk_quality_delta <= -5.0:
            risk_quality_trend = "weakening"
            risk_quality_trend_label = "風險品質轉弱"
        else:
            risk_quality_trend = "steady"
            risk_quality_trend_label = "風險品質持平"
        stability_score = (
            (streak_count / max(1, run_count) * 42.0)
            + (seen_count / max(1, run_count) * 24.0)
            + (latest_score / 100.0 * 14.0)
            + (latest_risk_quality / 100.0 * 10.0)
            + (entry_ready_count / max(1, seen_count) * 8.0)
            + (0.0 if blocker_runs else 6.0)
            + max(-4.0, min(4.0, risk_quality_delta / 5.0))
        )
        return {
            "symbol": symbol,
            "name": latest.get("name", ""),
            "industry_group": latest.get("industry_group", "其他"),
            "rank": latest.get("rank"),
            "streak_count": streak_count,
            "seen_count": seen_count,
            "lookback_runs": run_count,
            "lookback_days": run_count,
            "entry_ready_count": entry_ready_count,
            "blocker_runs": blocker_runs,
            "average_rank": sum(ranks) / len(ranks) if ranks else None,
            "best_rank": min(ranks) if ranks else None,
            "worst_rank": max(ranks) if ranks else None,
            "entry_score": latest_score,
            "average_entry_score": sum(scores) / len(scores) if scores else None,
            "entry_score_delta": score_delta,
            "risk_quality_score": latest_risk_quality,
            "risk_quality_label": latest.get("risk_quality_label", ""),
            "risk_quality_reason": latest.get("risk_quality_reason", ""),
            "average_risk_quality_score": sum(risk_quality_scores) / len(risk_quality_scores) if risk_quality_scores else None,
            "risk_quality_score_delta": risk_quality_delta,
            "risk_quality_trend": risk_quality_trend,
            "risk_quality_trend_label": risk_quality_trend_label,
            "rank_delta": rank_delta,
            "action_label": latest.get("action_label", ""),
            "risk_score": latest.get("risk_score"),
            "stability_score": stability_score,
            "blocker_tags": latest.get("blocker_tags", []),
        }

    def get_daily_radar_research_priority_summary(self, *, lookback_runs: int = 5, limit: int = 8) -> dict:
        item_limit = max(1, min(20, int(limit or 8)))
        run_count = max(2, min(20, int(lookback_runs or 5)))
        latest_items = self.get_latest_daily_radar(count=50)
        if not latest_items:
            return {
                "status": "insufficient",
                "reason": "no_daily_radar",
                "lookback_runs": run_count,
                "candidate_count": 0,
                "priority_items": [],
            }
        stability = self.get_daily_radar_stability_summary(lookback_runs=run_count, limit=20)
        changes = self.get_daily_radar_change_summary(limit=20)
        stability_by_symbol = {
            str(item.get("symbol") or ""): item
            for item in stability.get("stable_items", [])
            if item.get("symbol")
        }
        change_by_symbol: dict[str, dict] = {}
        for key, label in (
            ("new_items", "new"),
            ("improved_items", "improved"),
            ("weakened_items", "weakened"),
            ("recurring_items", "recurring"),
        ):
            for item in changes.get(key, []) if isinstance(changes.get(key), list) else []:
                symbol = str(item.get("symbol") or "")
                if symbol and symbol not in change_by_symbol:
                    change_by_symbol[symbol] = {**item, "change_type": label}
        trading_feedback = self.get_daily_radar_trading_feedback_summary(count=50, limit=100)
        feedback_by_symbol = {
            str(item.get("symbol") or ""): item
            for item in trading_feedback.get("symbols", [])
            if isinstance(item, dict) and item.get("symbol")
        }

        priority_items = [
            self._daily_radar_priority_item_with_trading_feedback(
                self._daily_radar_research_priority_item(
                    item,
                    stability_by_symbol.get(str(item.get("symbol") or "")),
                    change_by_symbol.get(str(item.get("symbol") or "")),
                ),
                feedback_by_symbol.get(str(item.get("symbol") or "")),
            )
            for item in latest_items
        ]
        priority_items = sorted(
            priority_items,
            key=lambda item: (
                -float(item.get("priority_score") or 0.0),
                int(item.get("rank") or 9999),
                str(item.get("symbol") or ""),
            ),
        )
        top_items = priority_items[:item_limit]
        top_groups = Counter(str(item.get("industry_group") or "其他") for item in top_items)
        top_group_name, top_group_count = top_groups.most_common(1)[0] if top_groups else ("", 0)
        top_group_share = (top_group_count / len(top_items)) if top_items else 0.0
        if len(top_items) >= 3 and top_group_share >= 0.60:
            concentration_level = "warn"
        elif len(top_items) >= 3 and top_group_share >= 0.45:
            concentration_level = "watch"
        else:
            concentration_level = "ok"
        diversified_priority_items = self._daily_radar_diversified_priority_items(
            priority_items,
            limit=min(5, item_limit),
        )
        next_action_counts = Counter(str(item.get("priority_next_action") or "保留觀察") for item in priority_items)
        primary_next_action = next(
            (
                action
                for action in ("先清交易回饋", "先處理阻擋", "可建立 Preview", "設觸發觀察", "保留觀察", "先略過")
                if next_action_counts.get(action, 0) > 0
            ),
            "",
        )
        return {
            "status": "ok",
            "reason": "",
            "lookback_runs": run_count,
            "available_runs": stability.get("available_runs", 0),
            "sample_unit": stability.get("sample_unit", "trade_date"),
            "available_days": stability.get("available_days", stability.get("available_runs", 0)),
            "duplicate_run_count": stability.get("duplicate_run_count", 0),
            "latest_run_id": latest_items[0].get("run_id", ""),
            "latest_run_date": latest_items[0].get("run_date"),
            "candidate_count": len(latest_items),
            "priority_count": len(priority_items),
            "entry_ready_count": sum(1 for item in priority_items if item.get("action_label") == "ENTRY_READY"),
            "stable_priority_count": sum(
                1
                for item in priority_items
                if int(item.get("streak_count") or 0) >= 2
                and str(item.get("action_label") or "") in {"ENTRY_READY", "WATCH_WAIT_TRIGGER"}
            ),
            "trading_feedback_risk_count": sum(1 for item in priority_items if int(item.get("trading_feedback_issue_count") or 0) > 0),
            "trading_feedback_danger_count": sum(1 for item in priority_items if item.get("trading_feedback_risk_level") == "danger"),
            "improved_count": sum(1 for item in priority_items if item.get("change_type") == "improved"),
            "new_count": sum(1 for item in priority_items if item.get("change_type") == "new"),
            "next_action_counts": dict(next_action_counts),
            "primary_next_action": primary_next_action,
            "top_industry_group": top_group_name,
            "top_industry_group_count": top_group_count,
            "top_industry_group_share": top_group_share,
            "priority_group_count": len(top_groups),
            "priority_concentration_level": concentration_level,
            "diversified_priority_count": len(diversified_priority_items),
            "diversified_priority_group_count": len(
                {str(item.get("industry_group") or "其他") for item in diversified_priority_items}
            ),
            "diversified_priority_items": diversified_priority_items,
            "trading_feedback": trading_feedback,
            "priority_items": top_items,
        }

    @staticmethod
    def _daily_radar_diversified_priority_items(priority_items: list[dict], *, limit: int = 5) -> list[dict]:
        item_limit = max(1, min(8, int(limit or 5)))
        rows = [item for item in priority_items if isinstance(item, dict)]
        if not rows:
            return []
        preferred = [
            item
            for item in rows
            if str(item.get("priority_next_action") or "") not in {"先清交易回饋", "先處理阻擋", "先略過"}
        ]
        candidates = preferred or rows
        selected: list[dict] = []
        selected_symbols: set[str] = set()
        used_groups: set[str] = set()

        def add_item(item: dict) -> None:
            symbol = str(item.get("symbol") or "")
            if not symbol or symbol in selected_symbols or len(selected) >= item_limit:
                return
            selected_symbols.add(symbol)
            used_groups.add(str(item.get("industry_group") or "其他"))
            selected.append({**item, "diversified_priority_rank": len(selected) + 1})

        for item in candidates:
            group = str(item.get("industry_group") or "其他")
            if group in used_groups:
                continue
            add_item(item)
        for item in candidates:
            add_item(item)
        return selected

    @staticmethod
    def _daily_radar_research_priority_item(item: dict, stability: dict | None, change: dict | None) -> dict:
        def _num(value: object) -> float:
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                return 0.0

        entry_score = float(item.get("entry_score") or 0.0)
        risk_score = float(item.get("risk_score") or 0.0)
        action = str(item.get("action_label") or "")
        blocker_tags = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
        blocker_count = len(blocker_tags)
        reliability_boost = float(item.get("outcome_reliability_boost") or 0.0)
        reliability_samples = int(item.get("outcome_reliability_samples") or 0)
        stability_score = float((stability or {}).get("stability_score") or 0.0)
        streak_count = int((stability or {}).get("streak_count") or 0)
        seen_count = int((stability or {}).get("seen_count") or 0)
        change_type = str((change or {}).get("change_type") or "")

        action_bonus = 12.0 if action == "ENTRY_READY" else 4.0 if action == "WATCH_WAIT_TRIGGER" else -24.0
        change_bonus = {
            "improved": 7.0,
            "new": 4.0,
            "recurring": 2.0,
            "weakened": -8.0,
        }.get(change_type, 0.0)
        blocker_adjustment = 8.0 if blocker_count == 0 else -min(28.0, blocker_count * 12.0)
        reliability_adjustment = (
            (4.0 if reliability_boost > 0 else -4.0 if reliability_boost < 0 else 0.0)
            + min(5.0, reliability_samples / 8.0)
        )
        trigger_plan = item.get("trigger_plan") if isinstance(item.get("trigger_plan"), dict) else {}
        if action == "WATCH_WAIT_TRIGGER" and not trigger_plan:
            trigger_plan = TradingRepository._daily_radar_trigger_plan(item)
        trigger_readiness_score = _num(trigger_plan.get("trigger_readiness_score")) if trigger_plan else 0.0
        trigger_readiness_adjustment = (
            max(0.0, min(6.0, ((trigger_readiness_score - 35.0) / 65.0) * 6.0))
            if action == "WATCH_WAIT_TRIGGER"
            else 0.0
        )
        score_components = {
            "entry": entry_score * 0.42,
            "stability": stability_score * 0.18,
            "action": action_bonus,
            "change": change_bonus,
            "blocker": blocker_adjustment,
            "reliability": reliability_adjustment,
            "trigger_readiness": trigger_readiness_adjustment,
            "risk": -min(14.0, risk_score * 14.0),
        }
        score_components = {
            key: round(float(value), 4)
            for key, value in score_components.items()
        }
        priority_score = sum(score_components.values())
        priority_score = max(0.0, min(100.0, priority_score))
        tier = TradingRepository._daily_radar_priority_tier(
            action=action,
            priority_score=priority_score,
            blocker_count=blocker_count,
        )
        next_action = TradingRepository._daily_radar_priority_next_action(
            action=action,
            tier=tier,
            blocker_count=blocker_count,
        )

        reasons = [f"Entry {entry_score:.1f}"]
        if action == "ENTRY_READY":
            reasons.append("可進場")
        if streak_count >= 2:
            reasons.append(f"連續 {streak_count} 日")
        if change_type == "improved":
            reasons.append("轉強")
        elif change_type == "new":
            reasons.append("新進")
        elif change_type == "weakened":
            reasons.append("轉弱")
        if reliability_boost > 0:
            reasons.append("可靠度加分")
        if trigger_readiness_adjustment > 0 and trigger_plan:
            label = str(trigger_plan.get("trigger_readiness_label") or "觸發成熟度")
            reasons.append(f"觸發成熟度{label} {trigger_readiness_score:.1f}")
        if blocker_count == 0:
            reasons.append("無阻擋")
        else:
            reasons.append(f"{blocker_count} 個阻擋")

        return {
            "symbol": item.get("symbol", ""),
            "name": item.get("name", ""),
            "industry_group": item.get("industry_group", "其他"),
            "rank": item.get("rank"),
            "action_label": action,
            "price": float(item.get("price") or 0.0),
            "atr_heat": item.get("atr_heat", "normal"),
            "reason_tags": item.get("reason_tags", []) if isinstance(item.get("reason_tags"), list) else [],
            "entry_score": entry_score,
            "risk_score": risk_score,
            "risk_adjusted_score": float(item.get("risk_adjusted_score") or 0.0),
            "priority_score": priority_score,
            "priority_score_components": score_components,
            "priority_score_component_summary": TradingRepository._daily_radar_priority_score_component_summary(
                score_components
            ),
            "priority_tier": tier,
            "priority_next_action": next_action,
            "reason_summary": reasons[:5],
            "change_type": change_type,
            "stability_score": stability_score,
            "streak_count": streak_count,
            "seen_count": seen_count,
            "entry_score_delta": (stability or change or {}).get("entry_score_delta"),
            "rank_delta": (stability or change or {}).get("rank_delta"),
            "outcome_reliability_boost": reliability_boost,
            "outcome_reliability_samples": reliability_samples,
            "symbol_adoption_feedback_boost": float(item.get("symbol_adoption_feedback_boost") or 0.0),
            "symbol_adoption_feedback_raw_boost": float(item.get("symbol_adoption_feedback_raw_boost") or item.get("symbol_adoption_feedback_boost") or 0.0),
            "symbol_adoption_feedback_samples": int(item.get("symbol_adoption_feedback_samples") or 0),
            "symbol_adoption_feedback_confidence_weight": float(item.get("symbol_adoption_feedback_confidence_weight") or 0.0),
            "symbol_adoption_feedback_confidence_level": str(item.get("symbol_adoption_feedback_confidence_level") or "none"),
            "blocker_count": blocker_count,
            "blocker_tags": blocker_tags,
            "trigger_plan": trigger_plan,
            "trigger_readiness_score": trigger_readiness_score if trigger_plan else None,
            "trigger_readiness_label": trigger_plan.get("trigger_readiness_label") if trigger_plan else "",
        }

    @staticmethod
    def _daily_radar_priority_score_component_summary(components: dict | None) -> list[str]:
        if not isinstance(components, dict):
            return []
        labels = {
            "entry": "Entry",
            "stability": "穩定度",
            "action": "動作",
            "change": "變化",
            "blocker": "阻擋",
            "reliability": "可靠度",
            "trigger_readiness": "觸發",
            "risk": "風險",
            "trading_feedback": "回饋",
        }
        order = {key: idx for idx, key in enumerate(labels)}
        rows: list[tuple[str, float]] = []
        for key, value in components.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if abs(number) < 0.01:
                continue
            rows.append((str(key), number))
        rows = sorted(rows, key=lambda row: (-abs(row[1]), order.get(row[0], 999), row[0]))
        return [
            f"{labels.get(key, key)} {value:+.1f}"
            for key, value in rows[:5]
        ]

    @staticmethod
    def _daily_radar_priority_tier(
        *,
        action: str,
        priority_score: float,
        blocker_count: int = 0,
        trading_feedback_risk_level: str = "ok",
        trading_feedback_issue_count: int = 0,
    ) -> str:
        risk_level = str(trading_feedback_risk_level or "ok")
        if risk_level == "danger":
            return "先清風險"
        if risk_level in {"warn", "watch"} or int(trading_feedback_issue_count or 0) > 0:
            return "先清回饋"
        if int(blocker_count or 0) > 0:
            return "先處理阻擋"
        action_text = str(action or "")
        if action_text == "NOT_RECOMMENDED":
            return "先略過"
        score = float(priority_score or 0.0)
        if score >= 74:
            return "優先研究"
        if score >= 60:
            return "候選研究"
        if score >= 45 or action_text == "WATCH_WAIT_TRIGGER":
            return "保留觀察"
        return "先略過"

    @staticmethod
    def _daily_radar_priority_next_action(
        *,
        action: str,
        tier: str,
        blocker_count: int = 0,
        trading_feedback_risk_level: str = "ok",
        trading_feedback_issue_count: int = 0,
    ) -> str:
        risk_level = str(trading_feedback_risk_level or "ok")
        if risk_level in {"danger", "warn", "watch"} or int(trading_feedback_issue_count or 0) > 0:
            return "先清交易回饋"
        if int(blocker_count or 0) > 0 or tier == "先處理阻擋":
            return "先處理阻擋"
        if tier == "先略過" or str(action or "") == "NOT_RECOMMENDED":
            return "先略過"
        if str(action or "") == "ENTRY_READY" and tier in {"優先研究", "候選研究"}:
            return "可建立 Preview"
        if str(action or "") == "WATCH_WAIT_TRIGGER" or tier == "保留觀察":
            return "設觸發觀察"
        return "保留觀察"

    def get_daily_radar_allocation_plan(
        self,
        *,
        portfolio_equity: float = 1_000_000.0,
        max_exposure_pct: float = 0.35,
        max_single_pct: float = 0.08,
        max_group_pct: float = 0.18,
        lookback_runs: int = 5,
        limit: int = 6,
        calibration_profile: str = "standard",
    ) -> dict:
        equity = max(10_000.0, min(100_000_000.0, float(portfolio_equity or 1_000_000.0)))
        exposure_pct = max(0.01, min(1.0, float(max_exposure_pct or 0.35)))
        single_pct = max(0.005, min(exposure_pct, float(max_single_pct or 0.08)))
        group_pct = max(single_pct, min(exposure_pct, float(max_group_pct or 0.18)))
        item_limit = max(1, min(12, int(limit or 6)))
        profile = self._daily_radar_allocation_calibration_profile(calibration_profile)
        priority = self.get_daily_radar_research_priority_summary(
            lookback_runs=max(2, min(20, int(lookback_runs or 5))),
            limit=max(item_limit * 2, item_limit),
        )
        if priority.get("status") != "ok":
            return {
                "status": "insufficient",
                "reason": priority.get("reason") or "no_priority_items",
                "portfolio_equity": equity,
                "max_exposure_pct": exposure_pct,
                "max_single_pct": single_pct,
                "max_group_pct": group_pct,
                "calibration_profile": profile,
                "research_budget": equity * exposure_pct,
                "allocation_items": [],
                "skipped_items": [],
            }

        candidates = priority.get("priority_items", []) if isinstance(priority.get("priority_items"), list) else []
        trading_feedback = priority.get("trading_feedback") if isinstance(priority.get("trading_feedback"), dict) else {}
        calibration = (
            self.get_daily_radar_allocation_calibration(
                lookahead_bars=5,
                max_snapshots=300,
                min_evaluated=4,
                calibration_profile=profile["key"],
            )
            if profile["enabled"]
            else {
                "status": "disabled",
                "reason": "calibration_disabled",
                "profile": profile,
                "groups": {},
                "top_positive": [],
                "top_negative": [],
            }
        )
        allocation = self._daily_radar_allocation_from_priority_items(
            candidates,
            equity=equity,
            exposure_pct=exposure_pct,
            single_pct=single_pct,
            group_pct=group_pct,
            item_limit=item_limit,
            calibration=calibration,
            calibration_profile=profile,
        )
        return {
            "status": "ok",
            "reason": "",
            "latest_run_id": priority.get("latest_run_id", ""),
            "latest_run_date": priority.get("latest_run_date"),
            "portfolio_equity": equity,
            "max_exposure_pct": exposure_pct,
            "max_single_pct": single_pct,
            "max_group_pct": group_pct,
            "calibration_profile": profile,
            "calibration": self._daily_radar_allocation_calibration_public_summary(calibration),
            "trading_feedback": trading_feedback,
            **allocation,
        }

    @staticmethod
    def _daily_radar_priority_item_with_trading_feedback(item: dict, feedback: dict | None) -> dict:
        def _score_components_with_feedback(penalty: float = 0.0) -> dict:
            source = item.get("priority_score_components") if isinstance(item.get("priority_score_components"), dict) else {}
            components: dict[str, float] = {}
            for key, value in source.items():
                try:
                    components[str(key)] = round(float(value), 4)
                except (TypeError, ValueError):
                    continue
            if penalty > 0:
                components["trading_feedback"] = round(-float(penalty), 4)
            elif "trading_feedback" in components:
                components["trading_feedback"] = round(float(components["trading_feedback"]), 4)
            return components

        if not isinstance(feedback, dict):
            next_action = item.get("priority_next_action") or TradingRepository._daily_radar_priority_next_action(
                action=str(item.get("action_label") or ""),
                tier=str(item.get("priority_tier") or ""),
                blocker_count=int(item.get("blocker_count") or 0),
            )
            components = _score_components_with_feedback()
            return {
                **item,
                "priority_score_raw": float(item.get("priority_score") or 0.0),
                "priority_score_components": components,
                "priority_score_component_summary": TradingRepository._daily_radar_priority_score_component_summary(
                    components
                ),
                "priority_next_action": next_action,
                "trading_feedback_risk_level": "ok",
                "trading_feedback_issue_count": 0,
                "trading_feedback_issues": [],
                "trading_feedback_pending_preview_count": 0,
                "trading_feedback_execution_rejected_count": 0,
                "trading_feedback_execution_error_count": 0,
            }
        issues = feedback.get("issues") if isinstance(feedback.get("issues"), list) else []
        issue_count = int(feedback.get("issue_count") or len(issues) or 0)
        risk_level = str(feedback.get("risk_level") or "ok")
        raw_score = float(item.get("priority_score") or 0.0)
        penalty = 0.0
        if risk_level == "danger":
            penalty = 28.0
        elif risk_level == "warn":
            penalty = 18.0
        elif risk_level == "watch" or issue_count > 0:
            penalty = 12.0
        adjusted_score = max(0.0, raw_score - penalty)
        tier = TradingRepository._daily_radar_priority_tier(
            action=str(item.get("action_label") or ""),
            priority_score=adjusted_score,
            blocker_count=int(item.get("blocker_count") or 0),
            trading_feedback_risk_level=risk_level,
            trading_feedback_issue_count=issue_count,
        )
        next_action = TradingRepository._daily_radar_priority_next_action(
            action=str(item.get("action_label") or ""),
            tier=tier,
            blocker_count=int(item.get("blocker_count") or 0),
            trading_feedback_risk_level=risk_level,
            trading_feedback_issue_count=issue_count,
        )
        reasons = list(item.get("reason_summary") if isinstance(item.get("reason_summary"), list) else [])
        feedback_labels = [
            str(issue.get("label") or issue.get("code") or "").strip()
            for issue in issues
            if isinstance(issue, dict) and str(issue.get("label") or issue.get("code") or "").strip()
        ]
        if issue_count > 0:
            reasons = ["交易回饋待處理", *feedback_labels[:2], *reasons]
        components = _score_components_with_feedback(penalty)
        return {
            **item,
            "priority_score_raw": raw_score,
            "priority_score": adjusted_score,
            "priority_score_components": components,
            "priority_score_component_summary": TradingRepository._daily_radar_priority_score_component_summary(
                components
            ),
            "priority_tier": tier,
            "priority_next_action": next_action,
            "reason_summary": list(dict.fromkeys(str(reason) for reason in reasons if str(reason).strip()))[:5],
            "trading_feedback_risk_level": risk_level,
            "trading_feedback_issue_count": issue_count,
            "trading_feedback_issues": issues[:4],
            "trading_feedback_pending_preview_count": int(feedback.get("pending_preview_count") or 0),
            "trading_feedback_execution_rejected_count": int(feedback.get("execution_rejected_count") or 0),
            "trading_feedback_execution_error_count": int(feedback.get("execution_error_count") or 0),
            "trading_feedback_latest_at": str(feedback.get("latest_feedback_at") or ""),
        }

    def _daily_radar_allocation_from_priority_items(
        self,
        priority_items: list[dict],
        *,
        equity: float,
        exposure_pct: float,
        single_pct: float,
        group_pct: float,
        item_limit: int,
        calibration: dict | None = None,
        calibration_profile: dict | None = None,
    ) -> dict:
        scored_items: list[dict] = []
        skipped_items: list[dict] = []
        for item in priority_items if isinstance(priority_items, list) else []:
            scored = self._daily_radar_allocation_candidate(
                item,
                calibration=calibration,
                calibration_profile=calibration_profile,
            )
            if scored["eligible"]:
                scored_items.append(scored)
            else:
                skipped_items.append(scored)
        scored_items = sorted(
            scored_items,
            key=lambda item: (
                -float(item.get("priority_score") or 0.0),
                -float(item.get("allocation_weight") or 0.0),
                int(item.get("rank") or 9999),
                str(item.get("symbol") or ""),
            ),
        )
        scored_items = self._daily_radar_apply_allocation_group_balance(scored_items)

        research_budget = float(equity) * float(exposure_pct)
        max_single_value = float(equity) * float(single_pct)
        max_group_value = float(equity) * float(group_pct)
        total_weight = sum(float(item.get("allocation_weight") or 0.0) for item in scored_items) or 1.0
        group_allocated: Counter[str] = Counter()
        allocation_items: list[dict] = []
        allocated_value = 0.0
        for item in scored_items[: item_limit * 2]:
            if len(allocation_items) >= item_limit:
                skipped_items.append({**item, "eligible": False, "skip_reason": "超過本次研究席次"})
                continue
            group = str(item.get("industry_group") or "其他")
            raw_value = research_budget * (float(item.get("allocation_weight") or 0.0) / total_weight)
            remaining_group_value = max(0.0, max_group_value - float(group_allocated[group]))
            suggested_value = min(raw_value, max_single_value, remaining_group_value)
            if suggested_value < 1_000:
                skipped_items.append({**item, "eligible": False, "skip_reason": "族群或單檔上限已滿"})
                continue
            allocated = self._daily_radar_allocation_item(item, suggested_value)
            allocation_items.append(allocated)
            allocated_value += float(allocated["suggested_value"])
            group_allocated[group] += float(allocated["suggested_value"])

        group_summary = [
            {
                "industry_group": group,
                "allocated_value": value,
                "allocated_pct": value / float(equity) if equity > 0 else 0.0,
            }
            for group, value in group_allocated.most_common()
        ]
        diagnostics = self._daily_radar_allocation_diagnostics(
            allocation_items=allocation_items,
            skipped_items=skipped_items,
            research_budget=research_budget,
            allocated_value=allocated_value,
            group_summary=group_summary,
            item_limit=item_limit,
        )
        return {
            "research_budget": research_budget,
            "allocated_value": allocated_value,
            "cash_buffer": max(0.0, research_budget - allocated_value),
            "allocation_count": len(allocation_items),
            "skipped_count": len(skipped_items),
            "group_summary": group_summary,
            "allocation_diagnostics": diagnostics,
            "allocation_items": allocation_items,
            "skipped_items": skipped_items[:6],
        }

    @staticmethod
    def _daily_radar_apply_allocation_group_balance(scored_items: list[dict]) -> list[dict]:
        if len(scored_items) < 3:
            return scored_items
        group_total = Counter(str(item.get("industry_group") or "其他") for item in scored_items)
        if len(group_total) <= 1:
            return scored_items
        seen: Counter[str] = Counter()
        balanced: list[dict] = []
        for item in scored_items:
            group = str(item.get("industry_group") or "其他")
            seen[group] += 1
            base_item = {
                **item,
                "premarket_multiplier": float(item.get("premarket_multiplier") or 1.0),
                "premarket_reasons": list(item.get("premarket_reasons") if isinstance(item.get("premarket_reasons"), list) else []),
            }
            if seen[group] <= 1 or group_total[group] <= 1:
                balanced.append(base_item)
                continue
            multiplier = 0.94 if seen[group] == 2 else 0.88
            reasons = list(base_item.get("premarket_reasons") if isinstance(base_item.get("premarket_reasons"), list) else [])
            reasons.append(
                {
                    "type": "group_balance",
                    "label": f"{group} 重複配置降權",
                    "multiplier": multiplier,
                }
            )
            balanced.append(
                {
                    **item,
                    "allocation_weight": float(item.get("allocation_weight") or 0.0) * multiplier,
                    "premarket_multiplier": round(float(base_item.get("premarket_multiplier") or 1.0) * multiplier, 4),
                    "premarket_reasons": reasons[:5],
                }
            )
        return sorted(
            balanced,
            key=lambda item: (
                -float(item.get("priority_score") or 0.0),
                -float(item.get("allocation_weight") or 0.0),
                int(item.get("rank") or 9999),
                str(item.get("symbol") or ""),
            ),
        )

    @staticmethod
    def _daily_radar_allocation_diagnostics(
        *,
        allocation_items: list[dict],
        skipped_items: list[dict],
        research_budget: float,
        allocated_value: float,
        group_summary: list[dict],
        item_limit: int,
    ) -> dict:
        items = allocation_items if isinstance(allocation_items, list) else []
        skipped = skipped_items if isinstance(skipped_items, list) else []
        budget = max(0.0, float(research_budget or 0.0))
        allocated = max(0.0, float(allocated_value or 0.0))
        utilization = allocated / budget if budget > 0 else 0.0
        max_group_value = max((float(row.get("allocated_value") or 0.0) for row in group_summary if isinstance(row, dict)), default=0.0)
        max_group_share = max_group_value / budget if budget > 0 else 0.0
        group_count = len([row for row in group_summary if isinstance(row, dict) and float(row.get("allocated_value") or 0.0) > 0])
        entry_count = sum(1 for item in items if str(item.get("action_label") or "") == "ENTRY_READY")
        watch_count = sum(1 for item in items if str(item.get("action_label") or "") == "WATCH_WAIT_TRIGGER")
        blocker_count = sum(int(item.get("blocker_count") or 0) for item in items)
        feedback_risk_items = [
            item
            for item in [*items, *skipped]
            if str(item.get("trading_feedback_risk_level") or "ok") in {"danger", "warn"}
            or int(item.get("trading_feedback_issue_count") or 0) > 0
            or str(item.get("skip_reason") or "") == "交易回饋需先處理"
        ]
        feedback_danger_count = sum(1 for item in feedback_risk_items if str(item.get("trading_feedback_risk_level") or "") == "danger")
        monitor_only_items = [
            item
            for item in skipped
            if item.get("monitor_only") is True or str(item.get("allocation_lane") or "") == "monitor_only"
        ]
        risk_values = [float(item.get("risk_score") or 0.0) for item in items]
        average_risk = sum(risk_values) / len(risk_values) if risk_values else None
        multipliers = [float(item.get("calibration_multiplier") or 1.0) for item in items]
        calibration_spread = (max(multipliers) - min(multipliers)) if multipliers else 0.0
        review_queue = TradingRepository._daily_radar_allocation_review_queue(items, skipped)

        alerts: list[dict] = []
        next_actions: list[str] = []

        def add_alert(alert_type: str, label: str, severity: str, detail: str, action: str) -> None:
            alerts.append({"type": alert_type, "label": label, "severity": severity, "detail": detail})
            if action and action not in next_actions:
                next_actions.append(action)

        if not items:
            add_alert(
                "no_allocation",
                "沒有配置項目",
                "warn",
                "研究優先序、阻擋或上限條件讓本輪沒有可配置標的。",
                "先檢查研究優先序與阻擋標籤，再決定是否放寬候選池。",
            )
        if items and utilization < 0.45:
            add_alert(
                "low_utilization",
                "研究額度使用偏低",
                "watch",
                f"目前只使用 {utilization:.0%} 研究預算，可能代表候選太少或上限太保守。",
                "先補看跳過標的與族群上限，避免因條件過窄錯過候選。",
            )
        if items and max_group_share >= 0.45 and group_count <= 2:
            add_alert(
                "group_concentration",
                "族群集中",
                "warn" if max_group_share >= 0.60 else "watch",
                f"最大族群吃掉 {max_group_share:.0%} 研究預算，分散度不足。",
                "先找第二、第三族群的可替代標的，再決定是否保留集中配置。",
            )
        if items and len(items) < min(3, max(1, int(item_limit or 1))):
            add_alert(
                "too_few_names",
                "配置檔數偏少",
                "watch",
                f"本輪只有 {len(items)} 檔配置標的，候選池抗雜訊能力較弱。",
                "先把穩定候選與分散首選一起檢查，補足備選名單。",
            )
        if items and entry_count == 0:
            add_alert(
                "no_entry_ready",
                "沒有可進場配置",
                "watch",
                "配置項目都還在觀察狀態，尚未形成可直接建立 Preview 的清單。",
                "先設定觸發價或資料補齊條件，不急著建立正式 Preview。",
            )
        if monitor_only_items:
            add_alert(
                "monitor_only_watchlist",
                "有盯盤備選未配置",
                "watch",
                f"{len(monitor_only_items)} 檔仍可盯盤，但分數未達配置門檻，不占用研究額度。",
                "先把盯盤備選設成價格、Entry Score 或 ATR 降溫觸發。",
            )
        if blocker_count > 0:
            add_alert(
                "blockers_present",
                "配置仍含阻擋",
                "warn",
                f"配置項目合計仍有 {blocker_count} 個阻擋標籤。",
                "先處理資料、處置股、流動性或排除清單問題。",
            )
        if feedback_risk_items:
            add_alert(
                "trading_feedback_risk",
                "交易回饋未清",
                "warn" if feedback_danger_count == 0 else "warn",
                f"{len(feedback_risk_items)} 檔候選命中未清 Preview、送單阻擋、錯誤或對帳/gate 風險。",
                "先處理交易回饋，再把標的移回 Preview 或觸發觀察行動線。",
            )
        if calibration_spread >= 0.12:
            add_alert(
                "calibration_spread",
                "校準差距偏大",
                "watch",
                f"配置校準 multiplier 最大差距 {calibration_spread:.2f}，歷史證據正在明顯改變權重。",
                "切到保守或關閉 profile 交叉檢查，避免單一歷史樣本音量過大。",
            )
        if skipped and len(skipped) >= max(3, int(item_limit or 1)):
            skipped_detail = f"有 {len(skipped)} 檔未配置。"
            if monitor_only_items:
                skipped_detail = f"有 {len(skipped)} 檔未配置，其中 {len(monitor_only_items)} 檔為盯盤備選。"
            add_alert(
                "many_skipped",
                "跳過項目偏多",
                "watch",
                skipped_detail,
                "先看跳過原因是否集中在資料 freshness 或族群上限。",
            )

        severities = {str(row.get("severity") or "") for row in alerts}
        if "warn" in severities:
            risk_level = "warn"
            verdict = "先處理配置風險"
        elif "watch" in severities:
            risk_level = "watch"
            verdict = "可小額研究，先補檢查"
        elif items:
            risk_level = "ok"
            verdict = "配置可先檢查"
            next_actions.append("依配置順序檢查價格、部位與 promotion gate。")
        else:
            risk_level = "warn"
            verdict = "等待更多候選"

        return {
            "risk_level": risk_level,
            "verdict": verdict,
            "alerts": alerts[:6],
            "next_actions": next_actions[:4],
            "review_queue": review_queue,
            "summary_metrics": {
                "budget_utilization": utilization,
                "max_group_budget_share": max_group_share,
                "group_count": group_count,
                "entry_count": entry_count,
                "watch_count": watch_count,
                "blocker_count": blocker_count,
                "average_risk_score": average_risk,
                "calibration_spread": calibration_spread,
                "skipped_count": len(skipped),
                "monitor_only_count": len(monitor_only_items),
                "trading_feedback_risk_count": len(feedback_risk_items),
                "trading_feedback_danger_count": feedback_danger_count,
            },
        }

    @staticmethod
    def _daily_radar_allocation_review_queue(allocation_items: list[dict], skipped_items: list[dict]) -> dict:
        lanes = {
            "preview": {
                "label": "可先建立 Preview",
                "hint": "配置、動作與阻擋條件相對完整",
                "items": [],
            },
            "trigger": {
                "label": "設觸發觀察",
                "hint": "已配置或盯盤備選，等價格或資料條件到位",
                "items": [],
            },
            "risk": {
                "label": "先處理風險",
                "hint": "阻擋、資料或上限問題要先確認",
                "items": [],
            },
        }

        def queue_checklist(item: dict, lane: str, *, allocated: bool, reason: str) -> list[str]:
            checks: list[str] = []
            trigger_plan = TradingRepository._daily_radar_trigger_plan(item) if lane == "trigger" else {}
            if lane == "preview":
                checks.extend(
                    [
                        "確認即時價格與參考價偏離",
                        "查詢目前持股、可賣股數與未成交委託",
                        "確認 promotion gate、單檔與投組曝險上限",
                    ]
                )
            elif lane == "trigger":
                trigger_checks = trigger_plan.get("checks") if isinstance(trigger_plan.get("checks"), list) else []
                checks.extend(
                    trigger_checks
                    or [
                        "設定價格、Entry Score 或資料 freshness 觸發條件",
                        "觸發後重新整理雷達與配置草稿",
                        "條件到位後再移入 Preview 行動線",
                    ]
                )
                if not allocated and item.get("monitor_only") is True:
                    checks.insert(0, "只盯盤不配置，等優先分回到 45 以上再納入研究額度")
            else:
                checks.extend(
                    [
                        "先閱讀阻擋標籤、資料 freshness 或跳過原因",
                        "處理處置股、注意股、流動性或資料缺口",
                        "風險解除後重新整理配置草稿",
                    ]
                )
            if not allocated and reason:
                checks.insert(0, f"確認跳過原因：{reason}")
            feedback_issues = item.get("trading_feedback_issues") if isinstance(item.get("trading_feedback_issues"), list) else []
            feedback_labels = [
                str(issue.get("label") or issue.get("code") or "").strip()
                for issue in feedback_issues
                if isinstance(issue, dict) and str(issue.get("label") or issue.get("code") or "").strip()
            ]
            if feedback_labels:
                checks.insert(0, "處理交易回饋：" + " / ".join(feedback_labels[:3]))
            blocker_tags = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
            if blocker_tags:
                checks.append("處理阻擋：" + " / ".join(str(tag) for tag in blocker_tags[:3]))
            multiplier = float(item.get("calibration_multiplier") or 1.0)
            if abs(multiplier - 1.0) >= 0.08:
                checks.append(f"用保守或關閉校準交叉檢查 x{multiplier:.2f}")
            return list(dict.fromkeys(checks))[:5]

        def queue_alert_rule(item: dict, lane: str, *, allocated: bool) -> str:
            if not allocated and lane == "trigger" and item.get("monitor_only") is True:
                return "優先分回到 45 或觸發條件成熟後，才納入研究額度"
            if not allocated:
                return "跳過原因解除並重新通過配置檢查後，才納入研究額度"
            if lane == "preview":
                return "即時價格、部位與 promotion gate 都通過後建立 Preview"
            if lane == "trigger":
                trigger_plan = TradingRepository._daily_radar_trigger_plan(item)
                return str(trigger_plan.get("summary") or "價格、Entry Score 或資料 freshness 觸發後再建立 Preview")
            return "阻擋、資料或上限風險解除後才回到候選池"

        def lane_alert_plan(lane_key: str, sorted_items: list[dict]) -> dict:
            symbols = [
                str(item.get("symbol") or "").strip()
                for item in sorted_items
                if isinstance(item, dict) and str(item.get("symbol") or "").strip()
            ]
            if lane_key == "preview":
                checklist = ["先檢查即時價格", "再查部位與可賣股數", "最後確認 promotion/live gate"]
                rule = "清單內標的可優先建立 Preview，但仍需逐檔通過交易 gate。"
            elif lane_key == "trigger":
                checklist = ["設定觸發條件", "條件觸發後重新整理配置", "確認阻擋為零再建立 Preview"]
                rule = "清單內標的先放 watchlist；已配置者保留研究額度，盯盤備選只等條件成熟。"
            else:
                checklist = ["先處理阻擋或跳過原因", "補齊 freshness / 流動性 / 上限檢查", "解除後重新跑雷達"]
                rule = "清單內標的暫不建立 Preview，先把風險待辦清乾淨。"
            return {
                "title": lanes[lane_key]["label"],
                "rule": rule,
                "symbols": symbols[:12],
                "checklist": checklist,
                "checklist_text": " | ".join(checklist),
            }

        def queue_handoff(lanes_payload: dict[str, dict]) -> dict:
            counts = {key: int((lane.get("count") if isinstance(lane, dict) else 0) or 0) for key, lane in lanes_payload.items()}
            primary_key = ""
            for candidate in ("risk", "preview", "trigger"):
                if counts.get(candidate, 0) > 0:
                    primary_key = candidate
                    break
            if not primary_key:
                return {
                    "primary_lane": "",
                    "primary_label": "等待配置行動線",
                    "primary_action": "等待更多可用候選",
                    "next_session_note": "目前沒有需要接手的配置行動線。",
                    "top_symbols": [],
                    "total_review_count": sum(counts.values()),
                }
            lane = lanes_payload[primary_key]
            top_symbols = [
                str(item.get("symbol") or "").strip()
                for item in lane.get("items", [])
                if isinstance(item, dict) and str(item.get("symbol") or "").strip()
            ][:5]
            label = str(lane.get("label") or primary_key)
            if primary_key == "risk":
                action = "先清風險再看配置"
            elif primary_key == "preview":
                action = "先檢查可建立 Preview"
            else:
                action = "先設定觸發觀察"
            symbol_text = "、".join(top_symbols) if top_symbols else "暫無代表標的"
            return {
                "primary_lane": primary_key,
                "primary_label": label,
                "primary_action": action,
                "next_session_note": f"{label} {counts.get(primary_key, 0)} 檔：{symbol_text}",
                "top_symbols": top_symbols,
                "total_review_count": sum(counts.values()),
            }

        def queue_item(item: dict, lane: str, *, allocated: bool, reason: str) -> dict:
            trigger_plan = TradingRepository._daily_radar_trigger_plan(item) if lane == "trigger" else {}
            alert_rule = queue_alert_rule(item, lane, allocated=allocated)
            checklist = queue_checklist(item, lane, allocated=allocated, reason=reason)
            allocation_status = "盯盤備選" if item.get("monitor_only") is True else "已配置" if allocated else "未配置"
            return {
                "lane": lane,
                "lane_label": lanes[lane]["label"],
                "symbol": item.get("symbol", ""),
                "name": item.get("name", ""),
                "industry_group": item.get("industry_group", "其他"),
                "action_label": item.get("action_label", ""),
                "priority_score": float(item.get("priority_score") or 0.0),
                "entry_score": float(item.get("entry_score") or 0.0),
                "risk_score": float(item.get("risk_score") or 0.0),
                "price": float(item.get("price") or 0.0),
                "suggested_value": float(item.get("suggested_value") or 0.0),
                "allocation_weight": float(item.get("allocation_weight") or 0.0),
                "premarket_multiplier": float(item.get("premarket_multiplier") or 1.0),
                "premarket_reasons": item.get("premarket_reasons", [])
                if isinstance(item.get("premarket_reasons"), list)
                else [],
                "symbol_adoption_feedback_boost": float(item.get("symbol_adoption_feedback_boost") or 0.0),
                "symbol_adoption_feedback_confidence_weight": float(item.get("symbol_adoption_feedback_confidence_weight") or 0.0),
                "symbol_adoption_feedback_confidence_level": str(item.get("symbol_adoption_feedback_confidence_level") or "none"),
                "calibration_multiplier": float(item.get("calibration_multiplier") or 1.0),
                "trading_feedback_risk_level": str(item.get("trading_feedback_risk_level") or "ok"),
                "trading_feedback_issue_count": int(item.get("trading_feedback_issue_count") or 0),
                "trading_feedback_issues": item.get("trading_feedback_issues", [])
                if isinstance(item.get("trading_feedback_issues"), list)
                else [],
                "trading_feedback_pending_preview_count": int(item.get("trading_feedback_pending_preview_count") or 0),
                "trading_feedback_execution_rejected_count": int(item.get("trading_feedback_execution_rejected_count") or 0),
                "trading_feedback_execution_error_count": int(item.get("trading_feedback_execution_error_count") or 0),
                "allocated": allocated,
                "monitor_only": bool(item.get("monitor_only")),
                "allocation_lane": str(item.get("allocation_lane") or ""),
                "reason": reason,
                "next_step": item.get("next_step") or item.get("skip_reason") or reason,
                "alert_rule": alert_rule,
                "checklist": checklist,
                "checklist_text": " | ".join(checklist),
                "trigger_plan": trigger_plan,
                "trigger_watchlist_note": TradingRepository._daily_radar_trigger_watchlist_note(
                    item,
                    trigger_plan,
                    allocation_status=allocation_status,
                )
                if lane == "trigger"
                else "",
            }

        def skipped_item_needs_risk_review(item: dict) -> bool:
            blocker_tags = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
            feedback_risk = str(item.get("trading_feedback_risk_level") or "ok")
            feedback_issue_count = int(item.get("trading_feedback_issue_count") or 0)
            skip_reason = str(item.get("skip_reason") or "")
            if feedback_risk in {"danger", "warn"} or feedback_issue_count > 0:
                return True
            if int(item.get("blocker_count") or 0) > 0 or blocker_tags:
                return True
            return skip_reason in {"交易回饋需先處理", "阻擋原因偏多"}

        def skipped_item_needs_trigger_watch(item: dict) -> bool:
            blocker_tags = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
            feedback_risk = str(item.get("trading_feedback_risk_level") or "ok")
            feedback_issue_count = int(item.get("trading_feedback_issue_count") or 0)
            return (
                str(item.get("action_label") or "") == "WATCH_WAIT_TRIGGER"
                and (item.get("monitor_only") is True or str(item.get("allocation_lane") or "") == "monitor_only")
                and feedback_risk not in {"danger", "warn"}
                and feedback_issue_count <= 0
                and int(item.get("blocker_count") or 0) <= 0
                and not blocker_tags
            )

        for item in allocation_items if isinstance(allocation_items, list) else []:
            if not isinstance(item, dict):
                continue
            blocker_count = int(item.get("blocker_count") or 0)
            feedback_risk = str(item.get("trading_feedback_risk_level") or "ok")
            feedback_issue_count = int(item.get("trading_feedback_issue_count") or 0)
            action = str(item.get("action_label") or "")
            if feedback_risk in {"danger", "warn"} or feedback_issue_count > 0:
                lanes["risk"]["items"].append(queue_item(item, "risk", allocated=True, reason="交易回饋需先處理"))
            elif blocker_count > 0:
                lanes["risk"]["items"].append(queue_item(item, "risk", allocated=True, reason="配置項目仍含阻擋標籤"))
            elif action == "ENTRY_READY":
                lanes["preview"]["items"].append(queue_item(item, "preview", allocated=True, reason="可先檢查價格、部位與正式 gate"))
            elif action == "WATCH_WAIT_TRIGGER":
                lanes["trigger"]["items"].append(queue_item(item, "trigger", allocated=True, reason="等待觸發條件後再建立 Preview"))
            else:
                lanes["risk"]["items"].append(queue_item(item, "risk", allocated=True, reason="動作狀態不適合直接建立 Preview"))

        for item in skipped_items if isinstance(skipped_items, list) else []:
            if not isinstance(item, dict):
                continue
            if skipped_item_needs_trigger_watch(item):
                lanes["trigger"]["items"].append(
                    queue_item(
                        item,
                        "trigger",
                        allocated=False,
                        reason="盯盤備選：觀察分數未達配置門檻",
                    )
                )
                continue
            if not skipped_item_needs_risk_review(item):
                continue
            lanes["risk"]["items"].append(
                queue_item(
                    item,
                    "risk",
                    allocated=False,
                    reason=str(item.get("skip_reason") or "本輪未納入配置"),
                )
            )

        for lane_key, lane in lanes.items():
            sorted_items = sorted(
                lane["items"],
                key=lambda row: (
                    not bool(row.get("allocated")),
                    -float(row.get("suggested_value") or 0.0),
                    -float(row.get("priority_score") or 0.0),
                    str(row.get("symbol") or ""),
                ),
            )
            lane["count"] = len(sorted_items)
            lane["items"] = sorted_items[:4]
            lane["alert_plan"] = lane_alert_plan(lane_key, sorted_items)

        return {
            "lanes": lanes,
            "summary": {
                "preview_count": lanes["preview"]["count"],
                "trigger_count": lanes["trigger"]["count"],
                "risk_count": lanes["risk"]["count"],
            },
            "handoff": queue_handoff(lanes),
        }

    @staticmethod
    def _daily_radar_allocation_candidate(
        item: dict,
        calibration: dict | None = None,
        calibration_profile: dict | None = None,
    ) -> dict:
        def _num(value: object) -> float:
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                return 0.0

        priority_score = float(item.get("priority_score") or 0.0)
        risk_score = float(item.get("risk_score") or 0.0)
        action = str(item.get("action_label") or "")
        blocker_count = int(item.get("blocker_count") or 0)
        tier = str(item.get("priority_tier") or "")
        feedback_risk = str(item.get("trading_feedback_risk_level") or "ok")
        feedback_issue_count = int(item.get("trading_feedback_issue_count") or 0)
        skip_reason = ""
        monitor_only = False
        if action == "NOT_RECOMMENDED":
            skip_reason = "最新雷達不建議"
        elif feedback_risk in {"danger", "warn"} or feedback_issue_count > 0:
            skip_reason = "交易回饋需先處理"
        elif blocker_count >= 2:
            skip_reason = "阻擋原因偏多"
        elif action == "WATCH_WAIT_TRIGGER" and priority_score < 45.0:
            skip_reason = "觀察分數未達配置門檻"
            monitor_only = True
        elif tier == "先略過" or priority_score < 45.0:
            skip_reason = "研究優先序不足"
        action_multiplier = 1.20 if action == "ENTRY_READY" else 0.78 if action == "WATCH_WAIT_TRIGGER" else 0.35
        blocker_multiplier = 1.0 if blocker_count == 0 else 0.72
        feedback_multiplier = 0.55 if feedback_risk == "danger" else 0.72 if feedback_risk == "warn" or feedback_issue_count > 0 else 1.0
        risk_multiplier = max(0.30, 1.0 - min(1.0, risk_score) * 0.55)
        reliability_boost = float(item.get("outcome_reliability_boost") or 0.0)
        reliability_multiplier = 1.08 if reliability_boost > 0 else 0.92 if reliability_boost < 0 else 1.0
        premarket_info = TradingRepository._daily_radar_allocation_premarket_multiplier(item)
        premarket_multiplier = float(premarket_info.get("multiplier") or 1.0)
        trigger_plan = item.get("trigger_plan") if isinstance(item.get("trigger_plan"), dict) else {}
        trigger_readiness_score = _num(trigger_plan.get("trigger_readiness_score") or item.get("trigger_readiness_score"))
        trigger_readiness_multiplier = (
            max(0.88, min(1.12, 0.88 + (trigger_readiness_score / 100.0) * 0.30))
            if action == "WATCH_WAIT_TRIGGER"
            else 1.0
        )
        calibration_info = TradingRepository._daily_radar_allocation_calibration_for_item(
            item,
            calibration,
            calibration_profile=calibration_profile,
        )
        calibration_multiplier = float(calibration_info.get("multiplier") or 1.0)
        weight = (
            max(0.0, priority_score / 100.0)
            * action_multiplier
            * blocker_multiplier
            * feedback_multiplier
            * risk_multiplier
            * reliability_multiplier
            * premarket_multiplier
            * trigger_readiness_multiplier
            * calibration_multiplier
        )
        return {
            **item,
            "eligible": not bool(skip_reason),
            "skip_reason": skip_reason,
            "monitor_only": monitor_only,
            "allocation_lane": "monitor_only" if monitor_only else "allocated" if not skip_reason else "skipped",
            "allocation_weight": weight,
            "premarket_multiplier": premarket_multiplier,
            "premarket_reasons": premarket_info.get("reasons", []),
            "trigger_readiness_multiplier": trigger_readiness_multiplier,
            "calibration_multiplier": calibration_multiplier,
            "calibration_reasons": calibration_info.get("reasons", []),
        }

    @staticmethod
    def _daily_radar_allocation_premarket_multiplier(item: dict) -> dict:
        def _num(value: object) -> float:
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                return 0.0

        blocker_count = int(item.get("blocker_count") or 0)
        adoption_boost = _num(item.get("symbol_adoption_feedback_boost"))
        adoption_samples = int(item.get("symbol_adoption_feedback_samples") or 0)
        confidence_weight = max(0.0, min(1.0, _num(item.get("symbol_adoption_feedback_confidence_weight"))))
        confidence_level = str(item.get("symbol_adoption_feedback_confidence_level") or "none")
        risk_score = max(0.0, min(1.0, _num(item.get("risk_score"))))
        multiplier = 1.0
        reasons: list[dict] = []

        if blocker_count <= 0:
            multiplier *= 1.03
            reasons.append({"type": "no_blocker", "label": "無阻擋", "multiplier": 1.03})
        if adoption_samples > 0 and adoption_boost > 0:
            if confidence_weight >= 0.35:
                adoption_multiplier = 1.0 + min(0.08, adoption_boost * 0.02 * max(0.35, confidence_weight))
                multiplier *= adoption_multiplier
                reasons.append(
                    {
                        "type": "adoption_feedback",
                        "label": f"採用回饋{confidence_level}",
                        "multiplier": round(adoption_multiplier, 4),
                    }
                )
            else:
                reasons.append({"type": "adoption_feedback_low_confidence", "label": "採用回饋低信心", "multiplier": 1.0})
        elif adoption_samples > 0 and adoption_boost < 0:
            adoption_multiplier = max(0.92, 1.0 + adoption_boost * 0.02)
            multiplier *= adoption_multiplier
            reasons.append({"type": "adoption_feedback_negative", "label": "採用回饋扣分", "multiplier": round(adoption_multiplier, 4)})

        if risk_score >= 0.65:
            multiplier *= 0.94
            reasons.append({"type": "high_risk", "label": "盤前風險偏高", "multiplier": 0.94})

        multiplier = max(0.82, min(1.14, multiplier))
        return {"multiplier": round(multiplier, 4), "reasons": reasons[:4]}

    @staticmethod
    def _daily_radar_allocation_item(item: dict, suggested_value: float) -> dict:
        price = float(item.get("price") or 0.0)
        shares = int(suggested_value // price) if price > 0 else 0
        board_lots = shares // 1000 if shares > 0 else 0
        action = str(item.get("action_label") or "")
        blocker_count = int(item.get("blocker_count") or 0)
        feedback_risk = str(item.get("trading_feedback_risk_level") or "ok")
        feedback_issue_count = int(item.get("trading_feedback_issue_count") or 0)
        if blocker_count > 0:
            next_step = "先處理阻擋，再決定是否保留研究額度"
        elif feedback_risk in {"danger", "warn"} or feedback_issue_count > 0:
            next_step = "先處理交易回饋，再決定是否建立 Preview"
        elif action == "ENTRY_READY":
            next_step = "可建立 paper/Preview，仍需通過 promotion 與 live gate"
        else:
            next_step = "保留觀察額度，等待觸發條件再建立 Preview"
        return {
            "symbol": item.get("symbol", ""),
            "name": item.get("name", ""),
            "industry_group": item.get("industry_group", "其他"),
            "rank": item.get("rank"),
            "action_label": action,
            "priority_score": float(item.get("priority_score") or 0.0),
            "priority_tier": item.get("priority_tier", ""),
            "entry_score": float(item.get("entry_score") or 0.0),
            "risk_score": float(item.get("risk_score") or 0.0),
            "risk_adjusted_score": float(item.get("risk_adjusted_score") or 0.0),
            "price": price,
            "suggested_value": float(suggested_value),
            "suggested_odd_lot_shares": shares,
            "suggested_board_lots": board_lots,
            "allocation_weight": float(item.get("allocation_weight") or 0.0),
            "monitor_only": bool(item.get("monitor_only")),
            "allocation_lane": str(item.get("allocation_lane") or "allocated"),
            "trigger_readiness_score": item.get("trigger_readiness_score"),
            "trigger_readiness_label": item.get("trigger_readiness_label", ""),
            "trigger_readiness_multiplier": float(item.get("trigger_readiness_multiplier") or 1.0),
            "trigger_plan": item.get("trigger_plan", {}) if isinstance(item.get("trigger_plan"), dict) else {},
            "premarket_multiplier": float(item.get("premarket_multiplier") or 1.0),
            "premarket_reasons": item.get("premarket_reasons", []),
            "symbol_adoption_feedback_boost": float(item.get("symbol_adoption_feedback_boost") or 0.0),
            "symbol_adoption_feedback_confidence_weight": float(item.get("symbol_adoption_feedback_confidence_weight") or 0.0),
            "symbol_adoption_feedback_confidence_level": str(item.get("symbol_adoption_feedback_confidence_level") or "none"),
            "calibration_multiplier": float(item.get("calibration_multiplier") or 1.0),
            "calibration_reasons": item.get("calibration_reasons", []),
            "blocker_count": blocker_count,
            "blocker_tags": item.get("blocker_tags", []),
            "atr_heat": str(item.get("atr_heat") or "normal"),
            "data_quality": str(item.get("data_quality") or "unknown"),
            "trading_feedback_risk_level": feedback_risk,
            "trading_feedback_issue_count": feedback_issue_count,
            "trading_feedback_issues": item.get("trading_feedback_issues", []),
            "trading_feedback_pending_preview_count": int(item.get("trading_feedback_pending_preview_count") or 0),
            "trading_feedback_execution_rejected_count": int(item.get("trading_feedback_execution_rejected_count") or 0),
            "trading_feedback_execution_error_count": int(item.get("trading_feedback_execution_error_count") or 0),
            "next_step": next_step,
            "reason_summary": item.get("reason_summary", []),
        }

    @staticmethod
    def _daily_radar_trigger_plan(item: dict) -> dict:
        def num(value: object) -> float:
            try:
                return float(value or 0.0)
            except Exception:
                return 0.0

        price = num(item.get("price"))
        entry_score = num(item.get("entry_score"))
        risk_score = max(0.0, min(1.0, num(item.get("risk_score"))))
        risk_adjusted = num(item.get("risk_adjusted_score") or item.get("priority_score"))
        atr_heat = str(item.get("atr_heat") or "normal").lower()
        data_quality = str(item.get("data_quality") or "unknown").lower()
        blocker_tags = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
        reason_summary = item.get("reason_summary") if isinstance(item.get("reason_summary"), list) else []

        target_entry = min(82.0, max(70.0, entry_score + 3.0 if entry_score else 70.0))
        pullback_pct = 0.02 if risk_score <= 0.25 else 0.035 if risk_score <= 0.45 else 0.05
        breakout_price = round(price * 1.01, 2) if price > 0 else 0.0
        pullback_price = round(price * (1.0 - pullback_pct), 2) if price > 0 else 0.0
        entry_gap = max(0.0, target_entry - entry_score) if entry_score > 0 else target_entry
        breakout_gap_pct = ((breakout_price - price) / price) if price > 0 and breakout_price > 0 else None
        pullback_gap_pct = ((price - pullback_price) / price) if price > 0 and pullback_price > 0 else None
        price_gap_source = pullback_gap_pct if (atr_heat == "hot" or risk_score >= 0.45) else min(
            value for value in [breakout_gap_pct, pullback_gap_pct] if value is not None
        ) if price > 0 else None
        entry_component = max(0.0, 1.0 - (entry_gap / 12.0))
        price_component = max(0.0, 1.0 - ((price_gap_source or 0.0) / 0.08)) if price_gap_source is not None else 0.0
        risk_penalty = 0.18 if atr_heat == "hot" else 0.08 if atr_heat == "elevated" else 0.0
        blocker_penalty = min(0.25, len(blocker_tags) * 0.08)
        readiness_score = max(0.0, min(1.0, (entry_component * 0.50) + (price_component * 0.35) + ((1.0 - risk_score) * 0.15) - risk_penalty - blocker_penalty))
        if readiness_score >= 0.72 and atr_heat != "hot" and not blocker_tags:
            readiness_label = "接近觸發"
        elif readiness_score >= 0.45:
            readiness_label = "等待確認"
        else:
            readiness_label = "還早"
        checks: list[str] = []
        rules: list[str] = []

        if entry_score > 0:
            rules.append(f"Entry Score 重新整理後站上 {target_entry:.0f} 分")
            checks.append(f"等 Entry Score 從 {entry_score:.1f} 提升到至少 {target_entry:.0f}")
        else:
            rules.append("Entry Score 重新整理後回到可評估區間")
            checks.append("先重跑每日雷達，確認 Entry Score 已產生")

        if price > 0:
            if atr_heat == "hot" or risk_score >= 0.45:
                rules.append(f"價格回落到 {pullback_price:.2f} 附近後再評估風險")
                checks.append(f"避免追高，等價格接近 {pullback_price:.2f} 或風險分數下降")
            else:
                rules.append(f"價格站回 {breakout_price:.2f} 或回測 {pullback_price:.2f} 後重新檢查")
                checks.append(f"設定 {breakout_price:.2f} 突破與 {pullback_price:.2f} 回測兩段提醒")
        else:
            rules.append("補齊最新價格後再設定觸發提醒")
            checks.append("先確認最新日線或即時價格可用")

        if atr_heat == "hot":
            rules.append("ATR 熱度降到 elevated/normal")
            checks.append("ATR 過熱解除前不建立 Preview")
        elif atr_heat == "elevated":
            rules.append("ATR 熱度不再升高")
            checks.append("觸發後確認停損距離仍在可接受範圍")

        if data_quality and data_quality not in {"unknown", "fresh", "ok"}:
            rules.append("資料 freshness 回到 fresh")
            checks.append("觸發前重新同步日線與法人資料")
        if blocker_tags:
            tags = " / ".join(str(tag) for tag in blocker_tags[:3])
            rules.append(f"阻擋解除：{tags}")
            checks.append(f"先處理阻擋標籤：{tags}")

        if reason_summary:
            checks.append("重新檢查理由：" + " / ".join(str(item) for item in reason_summary[:2]))

        summary = "；".join(rules[:3])
        return {
            "summary": summary,
            "rules": rules[:5],
            "checks": list(dict.fromkeys(checks))[:5],
            "target_entry_score": round(target_entry, 1),
            "breakout_price": breakout_price or None,
            "pullback_price": pullback_price or None,
            "reference_price": price or None,
            "entry_gap": round(entry_gap, 1),
            "breakout_gap_pct": round(breakout_gap_pct, 4) if breakout_gap_pct is not None else None,
            "pullback_gap_pct": round(pullback_gap_pct, 4) if pullback_gap_pct is not None else None,
            "trigger_readiness_score": round(readiness_score * 100.0, 1),
            "trigger_readiness_label": readiness_label,
            "risk_adjusted_score": round(risk_adjusted, 2),
            "atr_heat": atr_heat,
        }

    @staticmethod
    def _daily_radar_trigger_watchlist_note(
        item: dict,
        trigger_plan: dict | None = None,
        *,
        allocation_status: str = "",
    ) -> str:
        def num_text(value: object, digits: int = 2) -> str:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return ""
            if abs(number - round(number)) < 0.005:
                return f"{number:.0f}"
            return f"{number:.{digits}f}"

        plan = trigger_plan if isinstance(trigger_plan, dict) else {}
        parts: list[str] = []
        symbol = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or "").strip()
        if symbol or name:
            parts.append(" ".join(part for part in (symbol, name) if part).strip())
        status = str(allocation_status or "").strip()
        if status:
            parts.append(status)
        target_entry = num_text(plan.get("target_entry_score"), 0)
        if target_entry:
            parts.append(f"Entry>={target_entry}")
        breakout = num_text(plan.get("breakout_price"), 2)
        if breakout:
            parts.append(f"突破 {breakout}")
        pullback = num_text(plan.get("pullback_price"), 2)
        if pullback:
            parts.append(f"回測 {pullback}")
        atr_heat = str(plan.get("atr_heat") or item.get("atr_heat") or "").strip().lower()
        if atr_heat == "hot":
            parts.append("ATR降溫 elevated/normal")
        elif atr_heat == "elevated":
            parts.append("ATR不升高")
        priority_score = num_text(item.get("priority_score"), 1)
        if priority_score:
            parts.append(f"優先分 {priority_score}")
        readiness = num_text(plan.get("trigger_readiness_score"), 1)
        readiness_label = str(plan.get("trigger_readiness_label") or "").strip()
        if readiness:
            parts.append(f"成熟度 {readiness_label or readiness}")
        return " | ".join(part for part in parts if part)

    def get_daily_radar_allocation_calibration(
        self,
        *,
        lookahead_bars: int = 5,
        max_snapshots: int = 300,
        min_evaluated: int = 4,
        calibration_profile: str = "standard",
    ) -> dict:
        lookahead = max(1, min(60, int(lookahead_bars or 5)))
        snapshot_limit = max(20, min(2000, int(max_snapshots or 300)))
        min_samples = max(2, min(50, int(min_evaluated or 4)))
        profile = self._daily_radar_allocation_calibration_profile(calibration_profile)
        raw_snapshot_limit = min(5_000, max(snapshot_limit, snapshot_limit * 4))
        raw_radar_rows = list(
            self.session.execute(
                select(DailyRadarSnapshot)
                .where(DailyRadarSnapshot.action_label.in_(("ENTRY_READY", "WATCH_WAIT_TRIGGER")))
                .order_by(DailyRadarSnapshot.run_date.desc(), DailyRadarSnapshot.created_at.desc(), DailyRadarSnapshot.rank.asc())
                .limit(raw_snapshot_limit)
            ).scalars().all()
        )
        unique_radar_rows = self._deduplicate_radar_snapshots_by_symbol_day(raw_radar_rows)
        radar_rows = unique_radar_rows[:snapshot_limit]
        duplicate_snapshot_count = len(raw_radar_rows) - len(unique_radar_rows)
        outcome_rows = [
            row
            for row in self._radar_snapshot_outcome_rows(radar_rows, lookahead=lookahead)
            if row.get("lookahead_return") is not None and bool(row.get("matured"))
        ]
        if not outcome_rows:
            return {
                "status": "insufficient",
                "reason": "no_matured_outcomes",
                "lookahead_bars": lookahead,
                "min_evaluated": min_samples,
                "sample_unit": "symbol_trade_date",
                "input_snapshot_count": len(raw_radar_rows),
                "deduplicated_snapshot_count": len(radar_rows),
                "duplicate_snapshot_count": duplicate_snapshot_count,
                "evaluated_count": 0,
                "profile": profile,
                "baseline_positive_rate": None,
                "baseline_average_return": None,
                "groups": {},
            }

        returns = [float(row["lookahead_return"]) for row in outcome_rows]
        positive_count = sum(1 for value in returns if value > 0)
        baseline_positive_rate = positive_count / len(returns)
        baseline_average_return = sum(returns) / len(returns)

        groups = {
            "industry": self._daily_radar_allocation_calibration_group(
                outcome_rows,
                lambda row: [(str(row.get("industry_group") or "其他"), str(row.get("industry_group") or "其他"))],
                baseline_positive_rate=baseline_positive_rate,
                baseline_average_return=baseline_average_return,
                min_samples=min_samples,
                profile=profile,
            ),
            "action": self._daily_radar_allocation_calibration_group(
                outcome_rows,
                lambda row: [(str(row.get("action_label") or ""), str(row.get("action_label") or ""))],
                baseline_positive_rate=baseline_positive_rate,
                baseline_average_return=baseline_average_return,
                min_samples=min_samples,
                profile=profile,
            ),
            "atr_heat": self._daily_radar_allocation_calibration_group(
                outcome_rows,
                lambda row: [(str(row.get("atr_heat") or "normal").lower(), str(row.get("atr_heat") or "normal").lower())],
                baseline_positive_rate=baseline_positive_rate,
                baseline_average_return=baseline_average_return,
                min_samples=min_samples,
                profile=profile,
            ),
            "reason": self._daily_radar_allocation_calibration_group(
                outcome_rows,
                self._radar_outcome_reason_buckets,
                baseline_positive_rate=baseline_positive_rate,
                baseline_average_return=baseline_average_return,
                min_samples=min_samples,
                profile=profile,
            ),
        }
        active_count = sum(len(items) for items in groups.values())
        return {
            "status": "ok" if active_count else "insufficient",
            "reason": "" if active_count else "no_bucket_with_min_samples",
            "lookahead_bars": lookahead,
            "min_evaluated": min_samples,
            "sample_unit": "symbol_trade_date",
            "input_snapshot_count": len(raw_radar_rows),
            "deduplicated_snapshot_count": len(radar_rows),
            "duplicate_snapshot_count": duplicate_snapshot_count,
            "evaluated_count": len(outcome_rows),
            "positive_count": positive_count,
            "profile": profile,
            "baseline_positive_rate": baseline_positive_rate,
            "baseline_average_return": baseline_average_return,
            "groups": groups,
            "top_positive": self._daily_radar_allocation_calibration_extremes(groups, reverse=True),
            "top_negative": self._daily_radar_allocation_calibration_extremes(groups, reverse=False),
        }

    @classmethod
    def _daily_radar_allocation_calibration_group(
        cls,
        rows: list[dict],
        bucket_fn,
        *,
        baseline_positive_rate: float,
        baseline_average_return: float,
        min_samples: int,
        profile: dict,
    ) -> dict[str, dict]:
        output: dict[str, dict] = {}
        for item in cls._radar_outcome_aggregates(rows, bucket_fn):
            evaluated = int(item.get("evaluated_count") or 0)
            if evaluated < min_samples or item.get("average_return") is None or item.get("positive_rate") is None:
                continue
            average_return = float(item.get("average_return") or 0.0)
            positive_rate = float(item.get("positive_rate") or 0.0)
            alpha = average_return - float(baseline_average_return)
            hit_delta = positive_rate - float(baseline_positive_rate)
            raw_multiplier = cls._daily_radar_allocation_calibration_multiplier(alpha=alpha, hit_delta=hit_delta)
            multiplier = cls._daily_radar_allocation_apply_calibration_profile(raw_multiplier, profile)
            tone = "positive" if multiplier > 1.01 else "negative" if multiplier < 0.99 else "neutral"
            output[str(item["key"])] = {
                "key": str(item["key"]),
                "label": str(item.get("label") or item["key"]),
                "evaluated_count": evaluated,
                "positive_rate": positive_rate,
                "average_return": average_return,
                "baseline_average_return": baseline_average_return,
                "alpha": alpha,
                "hit_delta": hit_delta,
                "raw_multiplier": raw_multiplier,
                "multiplier": multiplier,
                "tone": tone,
            }
        return output

    @staticmethod
    def _daily_radar_allocation_calibration_multiplier(*, alpha: float, hit_delta: float) -> float:
        raw = 1.0 + (float(alpha) * 1.25) + (float(hit_delta) * 0.10)
        return max(0.88, min(1.12, raw))

    @staticmethod
    def _daily_radar_allocation_calibration_profile(profile: str | dict | None) -> dict:
        if isinstance(profile, dict):
            key = str(profile.get("key") or "standard").strip().lower()
        else:
            key = str(profile or "standard").strip().lower()
        profiles = {
            "off": {
                "key": "off",
                "label": "關閉",
                "enabled": False,
                "scale": 0.0,
                "min_multiplier": 1.0,
                "max_multiplier": 1.0,
            },
            "conservative": {
                "key": "conservative",
                "label": "保守",
                "enabled": True,
                "scale": 0.5,
                "min_multiplier": 0.94,
                "max_multiplier": 1.06,
            },
            "standard": {
                "key": "standard",
                "label": "標準",
                "enabled": True,
                "scale": 1.0,
                "min_multiplier": 0.88,
                "max_multiplier": 1.12,
            },
            "aggressive": {
                "key": "aggressive",
                "label": "積極",
                "enabled": True,
                "scale": 1.5,
                "min_multiplier": 0.82,
                "max_multiplier": 1.18,
            },
        }
        return profiles.get(key, profiles["standard"])

    @staticmethod
    def _daily_radar_allocation_apply_calibration_profile(raw_multiplier: float, profile: dict | None) -> float:
        config = TradingRepository._daily_radar_allocation_calibration_profile(profile)
        if not config.get("enabled"):
            return 1.0
        raw = float(raw_multiplier or 1.0)
        scaled = 1.0 + ((raw - 1.0) * float(config.get("scale") or 0.0))
        return max(float(config["min_multiplier"]), min(float(config["max_multiplier"]), scaled))

    @staticmethod
    def _daily_radar_allocation_calibration_extremes(groups: dict, *, reverse: bool) -> list[dict]:
        rows: list[dict] = []
        for group_name, buckets in (groups or {}).items():
            for item in (buckets or {}).values():
                multiplier = float(item.get("multiplier") or 1.0)
                if reverse and multiplier <= 1.005:
                    continue
                if not reverse and multiplier >= 0.995:
                    continue
                rows.append({"group": group_name, **item})
        rows.sort(
            key=lambda item: (
                float(item.get("multiplier") or 1.0),
                float(item.get("alpha") or 0.0),
                int(item.get("evaluated_count") or 0),
            ),
            reverse=reverse,
        )
        return rows[:5]

    @staticmethod
    def _daily_radar_allocation_calibration_public_summary(calibration: dict | None) -> dict:
        if not isinstance(calibration, dict):
            return {"status": "insufficient", "reason": "no_calibration"}
        return {
            "status": calibration.get("status", "insufficient"),
            "reason": calibration.get("reason", ""),
            "lookahead_bars": calibration.get("lookahead_bars"),
            "min_evaluated": calibration.get("min_evaluated"),
            "evaluated_count": calibration.get("evaluated_count", 0),
            "profile": calibration.get("profile") or calibration.get("calibration_profile"),
            "baseline_positive_rate": calibration.get("baseline_positive_rate"),
            "baseline_average_return": calibration.get("baseline_average_return"),
            "top_positive": calibration.get("top_positive", []),
            "top_negative": calibration.get("top_negative", []),
        }

    @staticmethod
    def _daily_radar_allocation_calibration_for_item(
        item: dict,
        calibration: dict | None,
        calibration_profile: dict | None = None,
    ) -> dict:
        if not isinstance(calibration, dict) or calibration.get("status") != "ok":
            return {"multiplier": 1.0, "reasons": []}
        groups = calibration.get("groups") if isinstance(calibration.get("groups"), dict) else {}
        profile = calibration_profile or calibration.get("profile") or calibration.get("calibration_profile")
        matches: list[dict] = []
        for group_name, key, fallback_label in TradingRepository._daily_radar_allocation_calibration_keys_for_item(item):
            bucket = (groups.get(group_name) or {}).get(key)
            if not isinstance(bucket, dict):
                continue
            raw_multiplier = float(bucket.get("raw_multiplier") or bucket.get("multiplier") or 1.0)
            multiplier = TradingRepository._daily_radar_allocation_apply_calibration_profile(raw_multiplier, profile)
            if abs(multiplier - 1.0) < 0.005:
                continue
            matches.append(
                {
                    "group": group_name,
                    "key": key,
                    "label": str(bucket.get("label") or fallback_label or key),
                    "raw_multiplier": raw_multiplier,
                    "multiplier": multiplier,
                    "alpha": bucket.get("alpha"),
                    "evaluated_count": int(bucket.get("evaluated_count") or 0),
                    "tone": bucket.get("tone", "neutral"),
                }
            )
        if not matches:
            return {"multiplier": 1.0, "reasons": []}
        deltas = [float(match["multiplier"]) - 1.0 for match in matches]
        combined = 1.0 + (sum(deltas) / len(deltas))
        profile_config = TradingRepository._daily_radar_allocation_calibration_profile(profile)
        combined = max(float(profile_config["min_multiplier"]), min(float(profile_config["max_multiplier"]), combined))
        matches.sort(key=lambda match: abs(float(match.get("multiplier") or 1.0) - 1.0), reverse=True)
        return {"multiplier": combined, "reasons": matches[:4]}

    @staticmethod
    def _daily_radar_allocation_calibration_keys_for_item(item: dict) -> list[tuple[str, str, str]]:
        keys: list[tuple[str, str, str]] = []
        industry = str(item.get("industry_group") or "其他")
        action = str(item.get("action_label") or "")
        heat = str(item.get("atr_heat") or "normal").lower()
        if industry:
            keys.append(("industry", industry, industry))
        if action:
            keys.append(("action", action, action))
        if heat:
            keys.append(("atr_heat", heat, heat))
        seen: set[str] = set()
        for tag in item.get("reason_tags", []) if isinstance(item.get("reason_tags"), list) else []:
            bucket = radar_outcome_bucket_for_tag(str(tag or ""))
            if bucket and bucket not in seen:
                keys.append(("reason", bucket, bucket))
                seen.add(bucket)
        return keys

    def get_daily_radar_allocation_outcome_summary(
        self,
        *,
        portfolio_equity: float = 1_000_000.0,
        max_exposure_pct: float = 0.35,
        max_single_pct: float = 0.08,
        max_group_pct: float = 0.18,
        lookahead_bars: int = 5,
        max_runs: int = 8,
        limit: int = 6,
    ) -> dict:
        equity = max(10_000.0, min(100_000_000.0, float(portfolio_equity or 1_000_000.0)))
        exposure_pct = max(0.01, min(1.0, float(max_exposure_pct or 0.35)))
        single_pct = max(0.005, min(exposure_pct, float(max_single_pct or 0.08)))
        group_pct = max(single_pct, min(exposure_pct, float(max_group_pct or 0.18)))
        lookahead = max(1, min(60, int(lookahead_bars or 5)))
        run_limit = max(1, min(24, int(max_runs or 8)))
        item_limit = max(1, min(12, int(limit or 6)))
        run_stmt = (
            select(
                DailyRadarSnapshot.run_id,
                func.max(DailyRadarSnapshot.run_date).label("run_date"),
                func.max(DailyRadarSnapshot.created_at).label("created_at"),
            )
            .group_by(DailyRadarSnapshot.run_id)
            .order_by(func.max(DailyRadarSnapshot.created_at).desc())
            .limit(min(240, max(run_limit * 8, run_limit)))
        )
        raw_candidate_runs = list(self.session.execute(run_stmt).all())
        candidate_runs = []
        seen_run_dates: set[date] = set()
        duplicate_run_count = 0
        for candidate in raw_candidate_runs:
            run_date = candidate[1]
            if run_date in seen_run_dates:
                duplicate_run_count += 1
                continue
            seen_run_dates.add(run_date)
            candidate_runs.append(candidate)
        run_results: list[dict] = []
        skipped_run_count = 0
        for run_id, run_date, created_at in candidate_runs:
            radar_rows = self._daily_radar_rows_for_run(str(run_id))
            if not radar_rows:
                skipped_run_count += 1
                continue
            priority_items = [
                self._daily_radar_research_priority_item(self._radar_snapshot_to_dict(row), None, None)
                for row in radar_rows
            ]
            allocation = self._daily_radar_allocation_from_priority_items(
                priority_items,
                equity=equity,
                exposure_pct=exposure_pct,
                single_pct=single_pct,
                group_pct=group_pct,
                item_limit=item_limit,
            )
            allocation_items = allocation.get("allocation_items", [])
            if not allocation_items:
                skipped_run_count += 1
                continue
            rows_by_symbol = {str(row.symbol): row for row in radar_rows}
            allocated_rows = [
                rows_by_symbol[str(item.get("symbol"))]
                for item in allocation_items
                if str(item.get("symbol")) in rows_by_symbol
            ]
            outcome_by_symbol = {
                str(row.get("symbol")): row
                for row in self._radar_snapshot_outcome_rows(allocated_rows, lookahead=lookahead)
                if row.get("lookahead_return") is not None and bool(row.get("matured"))
            }
            evaluated_allocations = [
                {
                    **item,
                    "lookahead_return": float(outcome_by_symbol[str(item.get("symbol"))]["lookahead_return"]),
                    "evaluation_date": outcome_by_symbol[str(item.get("symbol"))].get("evaluation_date"),
                }
                for item in allocation_items
                if str(item.get("symbol")) in outcome_by_symbol
            ]
            if not evaluated_allocations:
                skipped_run_count += 1
                continue

            allocated_value = sum(float(item.get("suggested_value") or 0.0) for item in evaluated_allocations)
            allocation_return = (
                sum(float(item.get("suggested_value") or 0.0) * float(item.get("lookahead_return") or 0.0) for item in evaluated_allocations)
                / allocated_value
                if allocated_value > 0
                else None
            )
            baseline_return = self._daily_radar_rank_baseline_return(
                radar_rows,
                lookahead=lookahead,
                item_count=max(1, len(evaluated_allocations)),
            )
            alpha = (
                float(allocation_return) - float(baseline_return)
                if allocation_return is not None and baseline_return is not None
                else None
            )
            run_results.append(
                {
                    "run_id": str(run_id),
                    "run_date": run_date.isoformat() if run_date else None,
                    "created_at": created_at.isoformat() if created_at else None,
                    "lookahead_bars": lookahead,
                    "allocation_return": allocation_return,
                    "baseline_return": baseline_return,
                    "alpha": alpha,
                    "allocated_value": allocated_value,
                    "allocated_count": len(allocation_items),
                    "evaluated_count": len(evaluated_allocations),
                    "positive_count": sum(1 for item in evaluated_allocations if float(item.get("lookahead_return") or 0.0) > 0),
                    "top_symbols": [str(item.get("symbol")) for item in evaluated_allocations[:4]],
                    "allocation_items": evaluated_allocations[:item_limit],
                }
            )
            if len(run_results) >= run_limit:
                break

        if not run_results:
            return {
                "status": "insufficient",
                "reason": "no_matured_allocation_runs",
                "lookahead_bars": lookahead,
                "max_runs": run_limit,
                "portfolio_equity": equity,
                "max_exposure_pct": exposure_pct,
                "max_single_pct": single_pct,
                "max_group_pct": group_pct,
                "research_budget": equity * exposure_pct,
                "evaluated_run_count": 0,
                "candidate_run_count": len(candidate_runs),
                "duplicate_run_count": duplicate_run_count,
                "sample_unit": "trade_date",
                "recent_runs": [],
            }

        allocation_returns = [float(row["allocation_return"]) for row in run_results if row.get("allocation_return") is not None]
        baseline_returns = [float(row["baseline_return"]) for row in run_results if row.get("baseline_return") is not None]
        alphas = [float(row["alpha"]) for row in run_results if row.get("alpha") is not None]
        evaluated_items = sum(int(row.get("evaluated_count") or 0) for row in run_results)
        positive_items = sum(int(row.get("positive_count") or 0) for row in run_results)
        average_alpha = (sum(alphas) / len(alphas)) if alphas else None
        if average_alpha is None:
            verdict = "等待基準"
        elif average_alpha > 0.005:
            verdict = "配置優於排名基準"
        elif average_alpha < -0.005:
            verdict = "配置落後排名基準"
        else:
            verdict = "配置接近排名基準"
        return {
            "status": "ok",
            "reason": "",
            "lookahead_bars": lookahead,
            "max_runs": run_limit,
            "portfolio_equity": equity,
            "max_exposure_pct": exposure_pct,
            "max_single_pct": single_pct,
            "max_group_pct": group_pct,
            "research_budget": equity * exposure_pct,
            "evaluated_run_count": len(run_results),
            "skipped_run_count": skipped_run_count,
            "candidate_run_count": len(candidate_runs),
            "duplicate_run_count": duplicate_run_count,
            "sample_unit": "trade_date",
            "allocation_positive_rate": (
                sum(1 for value in allocation_returns if value > 0) / len(allocation_returns)
                if allocation_returns
                else None
            ),
            "allocation_average_return": (sum(allocation_returns) / len(allocation_returns)) if allocation_returns else None,
            "allocation_best_return": max(allocation_returns) if allocation_returns else None,
            "allocation_worst_return": min(allocation_returns) if allocation_returns else None,
            "baseline_average_return": (sum(baseline_returns) / len(baseline_returns)) if baseline_returns else None,
            "average_alpha": average_alpha,
            "item_positive_rate": (positive_items / evaluated_items) if evaluated_items else None,
            "evaluated_item_count": evaluated_items,
            "verdict": verdict,
            "recent_runs": run_results,
        }

    def _daily_radar_rank_baseline_return(
        self,
        radar_rows: list[DailyRadarSnapshot],
        *,
        lookahead: int,
        item_count: int,
    ) -> float | None:
        baseline_rows = sorted(radar_rows, key=lambda row: (int(row.rank or 9999), -float(row.entry_score or 0.0)))[
            : max(1, int(item_count or 1))
        ]
        outcome_rows = [
            row
            for row in self._radar_snapshot_outcome_rows(baseline_rows, lookahead=lookahead)
            if row.get("lookahead_return") is not None and bool(row.get("matured"))
        ]
        returns = [float(row["lookahead_return"]) for row in outcome_rows]
        return (sum(returns) / len(returns)) if returns else None

    def get_latest_radar_outcomes(self, *, lookahead_bars: int = 5, count: int = 20) -> dict:
        lookahead = max(1, min(60, int(lookahead_bars or 5)))
        limit = max(1, min(100, int(count or 20)))
        latest_run_id = self.session.execute(
            select(DailyRadarSnapshot.run_id).order_by(DailyRadarSnapshot.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if not latest_run_id:
            return {
                "status": "insufficient",
                "reason": "no_daily_radar",
                "lookahead_bars": lookahead,
                "by_action": {},
                "by_industry": [],
                "by_reason": [],
                "by_atr_heat": [],
                "horizons": [],
                "rows": [],
            }

        radar_rows = list(
            self.session.execute(
                select(DailyRadarSnapshot)
                .where(DailyRadarSnapshot.run_id == latest_run_id)
                .order_by(DailyRadarSnapshot.rank.asc(), DailyRadarSnapshot.entry_score.desc())
                .limit(limit)
            ).scalars().all()
        )
        outcome_rows = self._radar_snapshot_outcome_rows(radar_rows, lookahead=lookahead)
        overview = self._radar_outcome_overview(outcome_rows)
        horizon_values = sorted({lookahead, 5, 10, 20})
        horizons = [
            {
                "lookahead_bars": horizon,
                **self._radar_outcome_overview(
                    outcome_rows if horizon == lookahead else self._radar_snapshot_outcome_rows(radar_rows, lookahead=horizon),
                    matured_only=True,
                ),
            }
            for horizon in horizon_values
        ]
        by_action: dict[str, dict] = {}
        for action in sorted({str(row["action_label"]) for row in outcome_rows}):
            by_action[action] = self._radar_outcome_stats(
                [row for row in outcome_rows if row["action_label"] == action]
            )

        by_industry = self._radar_outcome_aggregates(
            outcome_rows,
            lambda row: [(str(row.get("industry_group") or "其他"), str(row.get("industry_group") or "其他"))],
        )
        by_atr_heat = self._radar_outcome_aggregates(
            outcome_rows,
            lambda row: [(str(row.get("atr_heat") or "normal").lower(), str(row.get("atr_heat") or "normal").lower())],
        )
        by_reason = self._radar_outcome_aggregates(outcome_rows, self._radar_outcome_reason_buckets, limit=12)

        return {
            "status": "ok" if overview["evaluated_count"] else "insufficient",
            "reason": "" if overview["evaluated_count"] else "no_future_bars",
            "run_id": str(latest_run_id),
            "run_date": radar_rows[0].run_date.isoformat() if radar_rows else None,
            "lookahead_bars": lookahead,
            "candidate_count": overview["candidate_count"],
            "evaluated_count": overview["evaluated_count"],
            "matured_count": overview["matured_count"],
            "positive_count": overview["positive_count"],
            "positive_rate": overview["positive_rate"],
            "average_return": overview["average_return"],
            "best_return": overview["best_return"],
            "worst_return": overview["worst_return"],
            "by_action": by_action,
            "by_industry": by_industry,
            "by_reason": by_reason,
            "by_atr_heat": by_atr_heat,
            "horizons": horizons,
            "rows": outcome_rows,
        }

    def _radar_snapshot_outcome_rows(self, radar_rows: list[DailyRadarSnapshot], *, lookahead: int) -> list[dict]:
        outcome_rows: list[dict] = []
        safe_lookahead = max(1, min(60, int(lookahead or 5)))
        for row in radar_rows:
            base_price = float(row.price or 0.0)
            base_date = row.run_date
            if base_price <= 0:
                base_info = self.session.execute(
                    select(DailyBar.close, DailyBar.date)
                    .where(DailyBar.symbol == row.symbol, DailyBar.date <= row.run_date)
                    .order_by(DailyBar.date.desc())
                    .limit(1)
                ).first()
                if base_info:
                    base_price = float(base_info[0] or 0.0)
                    base_date = base_info[1]
            future_bars = list(
                self.session.execute(
                    select(DailyBar)
                    .where(DailyBar.symbol == row.symbol, DailyBar.date > row.run_date)
                    .order_by(DailyBar.date.asc())
                    .limit(safe_lookahead)
                ).scalars().all()
            )
            evaluation_bar = future_bars[-1] if future_bars else None
            return_value = (
                (float(evaluation_bar.close) / base_price) - 1.0
                if evaluation_bar is not None and base_price > 0
                else None
            )
            radar_item = self._radar_snapshot_to_dict(row)
            outcome_rows.append(
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "rank": int(row.rank),
                    "run_id": row.run_id,
                    "run_date": row.run_date.isoformat(),
                    "action_label": row.action_label,
                    "entry_score": float(row.entry_score),
                    "risk_score": float(row.risk_score),
                    "industry_group": radar_item.get("industry_group", "其他"),
                    "industry_rotation_score": float(radar_item.get("industry_rotation_score", 0.0) or 0.0),
                    "atr_heat": str(radar_item.get("atr_heat") or "normal"),
                    "reason_tags": list(radar_item.get("reason_tags", [])),
                    "blocker_tags": list(radar_item.get("blocker_tags", [])),
                    "base_date": base_date.isoformat() if base_date else None,
                    "base_price": float(base_price) if base_price > 0 else None,
                    "evaluation_date": evaluation_bar.date.isoformat() if evaluation_bar else None,
                    "evaluation_close": float(evaluation_bar.close) if evaluation_bar else None,
                    "lookahead_return": float(return_value) if return_value is not None else None,
                    "positive": bool(return_value > 0) if return_value is not None else None,
                    "bars_observed": len(future_bars),
                    "matured": len(future_bars) >= safe_lookahead,
                }
            )
        return outcome_rows

    @staticmethod
    def _deduplicate_radar_snapshots_by_symbol_day(
        radar_rows: Iterable[DailyRadarSnapshot],
    ) -> list[DailyRadarSnapshot]:
        """Keep the newest radar revision for each symbol and trading date.

        Same-day reruns are revisions of one observation, not independent outcome samples.
        """
        selected: dict[tuple[str, date], DailyRadarSnapshot] = {}
        for row in radar_rows:
            key = (str(row.symbol or ""), row.run_date)
            existing = selected.get(key)
            if existing is None:
                selected[key] = row
                continue
            existing_key = (existing.created_at or datetime.min, int(existing.id or 0), str(existing.run_id or ""))
            candidate_key = (row.created_at or datetime.min, int(row.id or 0), str(row.run_id or ""))
            if candidate_key > existing_key:
                selected[key] = row
        return sorted(
            selected.values(),
            key=lambda row: (
                row.run_date or date.min,
                row.created_at or datetime.min,
                -int(row.rank or 0),
                int(row.id or 0),
            ),
            reverse=True,
        )

    @staticmethod
    def _radar_outcome_overview(outcome_rows: list[dict], *, matured_only: bool = False) -> dict:
        evaluated = [
            row
            for row in outcome_rows
            if row.get("lookahead_return") is not None and (not matured_only or bool(row.get("matured")))
        ]
        matured = [row for row in evaluated if row.get("matured")]
        returns = [float(row["lookahead_return"]) for row in evaluated]
        positive_count = sum(1 for value in returns if value > 0)
        return {
            "candidate_count": len(outcome_rows),
            "evaluated_count": len(evaluated),
            "matured_count": len(matured),
            "positive_count": positive_count,
            "positive_rate": (positive_count / len(evaluated)) if evaluated else None,
            "average_return": (sum(returns) / len(returns)) if returns else None,
            "best_return": max(returns) if returns else None,
            "worst_return": min(returns) if returns else None,
        }

    def get_radar_signal_reliability(
        self,
        *,
        lookahead_bars: int = 5,
        max_snapshots: int = 300,
        min_evaluated: int = 4,
        on_or_before: date | None = None,
    ) -> dict:
        lookahead = max(1, min(60, int(lookahead_bars or 5)))
        limit = max(20, min(2000, int(max_snapshots or 300)))
        min_samples = max(1, int(min_evaluated or 4))
        raw_limit = min(5_000, max(limit, limit * 4))
        stmt = (
            select(DailyRadarSnapshot)
            .where(DailyRadarSnapshot.action_label.in_(("ENTRY_READY", "WATCH_WAIT_TRIGGER")))
        )
        if on_or_before is not None:
            stmt = stmt.where(DailyRadarSnapshot.run_date <= on_or_before)
        stmt = stmt.order_by(
            DailyRadarSnapshot.run_date.desc(),
            DailyRadarSnapshot.created_at.desc(),
            DailyRadarSnapshot.rank.asc(),
        ).limit(raw_limit)
        raw_radar_rows = list(self.session.execute(stmt).scalars().all())
        unique_radar_rows = self._deduplicate_radar_snapshots_by_symbol_day(raw_radar_rows)
        radar_rows = unique_radar_rows[:limit]
        duplicate_snapshot_count = len(raw_radar_rows) - len(unique_radar_rows)
        outcome_rows: list[dict] = []
        for row in radar_rows:
            base_price = float(row.price or 0.0)
            if base_price <= 0:
                base_info = self.session.execute(
                    select(DailyBar.close)
                    .where(DailyBar.symbol == row.symbol, DailyBar.date <= row.run_date)
                    .order_by(DailyBar.date.desc())
                    .limit(1)
                ).first()
                if base_info:
                    base_price = float(base_info[0] or 0.0)
            future_bars = list(
                self.session.execute(
                    select(DailyBar)
                    .where(DailyBar.symbol == row.symbol, DailyBar.date > row.run_date)
                    .order_by(DailyBar.date.asc())
                    .limit(lookahead)
                ).scalars().all()
            )
            if len(future_bars) < lookahead or base_price <= 0:
                continue
            evaluation_bar = future_bars[-1]
            return_value = (float(evaluation_bar.close) / base_price) - 1.0
            radar_item = self._radar_snapshot_to_dict(row)
            outcome_rows.append(
                {
                    "symbol": row.symbol,
                    "run_id": row.run_id,
                    "run_date": row.run_date.isoformat(),
                    "lookahead_return": float(return_value),
                    "positive": bool(return_value > 0),
                    "reason_tags": list(radar_item.get("reason_tags", [])),
                }
            )

        if not outcome_rows:
            return {
                "status": "insufficient",
                "reason": "no_matured_outcomes",
                "lookahead_bars": lookahead,
                "min_evaluated": min_samples,
                "sample_unit": "symbol_trade_date",
                "input_snapshot_count": len(raw_radar_rows),
                "deduplicated_snapshot_count": len(radar_rows),
                "duplicate_snapshot_count": duplicate_snapshot_count,
                "evaluated_count": 0,
                "baseline_positive_rate": None,
                "baseline_average_return": None,
                "buckets": {},
            }

        returns = [float(row["lookahead_return"]) for row in outcome_rows]
        positive_count = sum(1 for value in returns if value > 0)
        baseline_positive_rate = positive_count / len(returns)
        baseline_average_return = sum(returns) / len(returns)
        bucket_rows = self._radar_outcome_aggregates(
            outcome_rows,
            self._radar_outcome_reason_buckets,
        )
        buckets = {
            str(item["key"]): item
            for item in bucket_rows
            if int(item.get("evaluated_count", 0) or 0) >= min_samples
            and item.get("positive_rate") is not None
        }
        return {
            "status": "ok" if buckets else "insufficient",
            "reason": "" if buckets else "no_bucket_with_min_samples",
            "lookahead_bars": lookahead,
            "min_evaluated": min_samples,
            "sample_unit": "symbol_trade_date",
            "input_snapshot_count": len(raw_radar_rows),
            "deduplicated_snapshot_count": len(radar_rows),
            "duplicate_snapshot_count": duplicate_snapshot_count,
            "evaluated_count": len(outcome_rows),
            "positive_count": positive_count,
            "return_sum": sum(returns),
            "baseline_positive_rate": baseline_positive_rate,
            "baseline_average_return": baseline_average_return,
            "buckets": buckets,
        }

    def get_radar_signal_reliability_horizons(
        self,
        *,
        lookahead_bars_list: Iterable[int] = (5, 10, 20),
        max_snapshots: int = 300,
        min_evaluated: int = 4,
        on_or_before: date | None = None,
    ) -> dict:
        horizons: list[dict] = []
        for raw_horizon in lookahead_bars_list:
            try:
                horizon = max(1, min(60, int(raw_horizon or 5)))
            except (TypeError, ValueError):
                continue
            result = self.get_radar_signal_reliability(
                lookahead_bars=horizon,
                max_snapshots=max_snapshots,
                min_evaluated=1,
                on_or_before=on_or_before,
            )
            horizons.append(result)

        evaluated_count = sum(int(item.get("evaluated_count", 0) or 0) for item in horizons)
        positive_count = sum(int(item.get("positive_count", 0) or 0) for item in horizons)
        return_sum = sum(float(item.get("return_sum", 0.0) or 0.0) for item in horizons)
        combined: dict[str, dict] = {}
        for horizon_result in horizons:
            horizon = int(horizon_result.get("lookahead_bars", 0) or 0)
            for key, stats in (horizon_result.get("buckets") or {}).items():
                evaluated = int(stats.get("evaluated_count", 0) or 0)
                if evaluated <= 0:
                    continue
                target = combined.setdefault(
                    str(key),
                    {
                        "key": str(key),
                        "label": str(stats.get("label") or key),
                        "count": 0,
                        "evaluated_count": 0,
                        "positive_count": 0,
                        "return_sum": 0.0,
                        "best_return": None,
                        "worst_return": None,
                        "horizons": [],
                    },
                )
                average_return = float(stats.get("average_return", 0.0) or 0.0)
                best_return = stats.get("best_return")
                worst_return = stats.get("worst_return")
                target["count"] = int(target["count"]) + int(stats.get("count", 0) or 0)
                target["evaluated_count"] = int(target["evaluated_count"]) + evaluated
                target["positive_count"] = int(target["positive_count"]) + int(stats.get("positive_count", 0) or 0)
                target["return_sum"] = float(target["return_sum"]) + (average_return * evaluated)
                if best_return is not None:
                    target["best_return"] = max(float(best_return), float(target["best_return"])) if target["best_return"] is not None else float(best_return)
                if worst_return is not None:
                    target["worst_return"] = min(float(worst_return), float(target["worst_return"])) if target["worst_return"] is not None else float(worst_return)
                target["horizons"].append(
                    {
                        "lookahead_bars": horizon,
                        "count": int(stats.get("count", 0) or 0),
                        "evaluated_count": evaluated,
                        "positive_count": int(stats.get("positive_count", 0) or 0),
                        "positive_rate": stats.get("positive_rate"),
                        "average_return": average_return,
                        "best_return": best_return,
                        "worst_return": worst_return,
                    }
                )

        min_samples = max(1, int(min_evaluated or 4))
        buckets: dict[str, dict] = {}
        for key, stats in combined.items():
            evaluated = int(stats["evaluated_count"])
            if evaluated < min_samples:
                continue
            stats["positive_rate"] = (int(stats["positive_count"]) / evaluated) if evaluated else None
            stats["average_return"] = (float(stats["return_sum"]) / evaluated) if evaluated else None
            buckets[key] = {
                "key": stats["key"],
                "label": stats["label"],
                "count": int(stats["count"]),
                "evaluated_count": evaluated,
                "positive_count": int(stats["positive_count"]),
                "positive_rate": stats["positive_rate"],
                "average_return": stats["average_return"],
                "best_return": stats["best_return"],
                "worst_return": stats["worst_return"],
                "horizons": sorted(stats["horizons"], key=lambda item: int(item.get("lookahead_bars", 0) or 0)),
            }

        return {
            "status": "ok" if buckets else "insufficient",
            "reason": "" if buckets else "no_bucket_with_min_samples",
            "lookahead_bars_list": [int(item.get("lookahead_bars", 0) or 0) for item in horizons],
            "min_evaluated": min_samples,
            "evaluated_count": evaluated_count,
            "positive_count": positive_count,
            "return_sum": return_sum,
            "baseline_positive_rate": (positive_count / evaluated_count) if evaluated_count else None,
            "baseline_average_return": (return_sum / evaluated_count) if evaluated_count else None,
            "buckets": buckets,
            "horizons": [
                {
                    "lookahead_bars": item.get("lookahead_bars"),
                    "status": item.get("status"),
                    "evaluated_count": item.get("evaluated_count", 0),
                    "positive_count": item.get("positive_count", 0),
                    "positive_rate": item.get("baseline_positive_rate"),
                    "average_return": item.get("baseline_average_return"),
                }
                for item in horizons
            ],
        }

    def get_symbol_decision_detail(self, symbol: str, *, peer_limit: int = 5, bar_limit: int = 20) -> dict:
        code = str(symbol or "").strip().upper()
        if not code:
            return {"status": "not_found", "symbol": ""}

        instrument = self.session.get(Instrument, code)
        radar_row = self.session.execute(
            select(DailyRadarSnapshot)
            .where(DailyRadarSnapshot.symbol == code)
            .order_by(DailyRadarSnapshot.created_at.desc(), DailyRadarSnapshot.run_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        candidate_row = self.session.execute(
            select(CandidateSnapshot)
            .where(CandidateSnapshot.symbol == code)
            .order_by(CandidateSnapshot.created_at.desc(), CandidateSnapshot.run_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        watch_row = self.session.execute(
            select(WatchPoolEntry).where(WatchPoolEntry.symbol == code).limit(1)
        ).scalar_one_or_none()

        radar = self._radar_snapshot_to_dict(radar_row) if radar_row else None
        candidate = self._candidate_snapshot_to_dict(candidate_row) if candidate_row else None
        watch = (
            {
                "status": watch_row.status,
                "action_label": watch_row.action_label,
                "entry_score": float(watch_row.entry_score),
                "risk_score": float(watch_row.risk_score),
                "reason_tags": self._safe_json_list(watch_row.reason_tags_json),
                "blocker_tags": self._safe_json_list(watch_row.blocker_tags_json),
                "first_seen_date": watch_row.first_seen_date.isoformat() if watch_row.first_seen_date else None,
                "last_seen_date": watch_row.last_seen_date.isoformat() if watch_row.last_seen_date else None,
                "active": bool(watch_row.active),
            }
            if watch_row
            else None
        )

        bar_rows_desc = list(
            self.session.execute(
                select(DailyBar)
                .where(DailyBar.symbol == code)
                .order_by(DailyBar.date.desc())
                .limit(max(2, int(bar_limit)))
            ).scalars().all()
        )
        bar_rows = list(reversed(bar_rows_desc))
        bar_items = [
            {
                "date": row.date.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
                "source": row.source,
            }
            for row in bar_rows
        ]
        latest_bar = bar_rows[-1] if bar_rows else None
        previous_bar = bar_rows[-2] if len(bar_rows) >= 2 else None
        first_bar = bar_rows[0] if bar_rows else None
        one_day_return = (
            (float(latest_bar.close) / float(previous_bar.close)) - 1.0
            if latest_bar is not None and previous_bar is not None and float(previous_bar.close or 0.0) > 0
            else None
        )
        lookback_return = (
            (float(latest_bar.close) / float(first_bar.close)) - 1.0
            if latest_bar is not None and first_bar is not None and float(first_bar.close or 0.0) > 0
            else None
        )
        avg_volume = (
            sum(float(row.volume or 0.0) for row in bar_rows) / len(bar_rows)
            if bar_rows
            else None
        )

        def count_rows(model) -> int:
            return int(self.session.execute(select(func.count()).select_from(model).where(model.symbol == code)).scalar_one() or 0)

        latest_feature_date = self.session.execute(
            select(func.max(FeatureSnapshot.date)).where(FeatureSnapshot.symbol == code)
        ).scalar_one_or_none()
        latest_chip_date = self.session.execute(
            select(func.max(InstitutionalChipDaily.date)).where(InstitutionalChipDaily.symbol == code)
        ).scalar_one_or_none()
        latest_broker_date = self.session.execute(
            select(func.max(BrokerAggDaily.date)).where(BrokerAggDaily.symbol == code)
        ).scalar_one_or_none()
        data_health = {
            "daily_bars_count": count_rows(DailyBar),
            "institutional_chip_count": count_rows(InstitutionalChipDaily),
            "broker_agg_count": count_rows(BrokerAggDaily),
            "disposition_periods_count": count_rows(DispositionPeriod),
            "feature_snapshots_count": count_rows(FeatureSnapshot),
            "latest_bar_date": latest_bar.date.isoformat() if latest_bar else None,
            "latest_feature_date": latest_feature_date.isoformat() if latest_feature_date else None,
            "latest_chip_date": latest_chip_date.isoformat() if latest_chip_date else None,
            "latest_broker_date": latest_broker_date.isoformat() if latest_broker_date else None,
        }

        industry_group = ""
        if radar:
            industry_group = str(radar.get("industry_group") or "").strip()
        if not industry_group and instrument is not None:
            industry_group = str(instrument.industry or "").strip()
        industry_group = industry_group or "其他"

        peers: list[dict] = []
        if radar and radar.get("run_id"):
            peer_rows = list(
                self.session.execute(
                    select(DailyRadarSnapshot)
                    .where(DailyRadarSnapshot.run_id == str(radar["run_id"]), DailyRadarSnapshot.symbol != code)
                    .order_by(DailyRadarSnapshot.rank.asc(), DailyRadarSnapshot.entry_score.desc())
                ).scalars().all()
            )
            for row in peer_rows:
                item = self._radar_snapshot_to_dict(row)
                if str(item.get("industry_group") or "其他") != industry_group:
                    continue
                peers.append(item)
                if len(peers) >= max(1, int(peer_limit)):
                    break

        found = instrument is not None or radar is not None or candidate is not None or bool(bar_rows)
        return {
            "status": "ok" if found else "not_found",
            "symbol": code,
            "instrument": {
                "symbol": code,
                "name": str(instrument.name or "") if instrument is not None else (str(radar.get("name") or "") if radar else ""),
                "market": str(instrument.market or "") if instrument is not None else "",
                "industry": str(instrument.industry or "") if instrument is not None else industry_group,
                "is_active": bool(instrument.is_active) if instrument is not None else None,
            },
            "radar": radar,
            "candidate": candidate,
            "watch_pool": watch,
            "industry_group": industry_group,
            "peer_radar": peers,
            "price_summary": {
                "latest_date": latest_bar.date.isoformat() if latest_bar else None,
                "latest_close": float(latest_bar.close) if latest_bar else None,
                "one_day_return": one_day_return,
                "lookback_return": lookback_return,
                "avg_volume": avg_volume,
                "bar_count": len(bar_rows),
            },
            "recent_bars": bar_items,
            "data_health": data_health,
        }

    def upsert_watch_pool(self, run_id: str, run_date: date, records: list[dict]) -> int:
        # Only actionable symbols remain active in the watch pool.
        actionable_labels = {"ENTRY_READY", "WATCH_WAIT_TRIGGER"}
        enriched_records = enrich_risk_quality([item for item in records if isinstance(item, dict)])
        rows = 0
        seen_symbols: set[str] = set()
        for item in enriched_records:
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            action_label = str(item.get("action_label", "NOT_RECOMMENDED") or "NOT_RECOMMENDED")
            seen_symbols.add(symbol)
            stmt = select(WatchPoolEntry).where(WatchPoolEntry.symbol == symbol).limit(1)
            existing = self.session.execute(stmt).scalar_one_or_none()
            if existing is None:
                existing = WatchPoolEntry(symbol=symbol, first_seen_date=run_date, last_seen_date=run_date)
                self.session.add(existing)
            existing.name = str(item.get("name", "") or "")
            existing.action_label = action_label
            existing.entry_score = float(item.get("entry_score", 0.0) or 0.0)
            existing.risk_score = float(item.get("risk_score", 0.0) or 0.0)
            existing.risk_adjusted_score = float(item.get("risk_adjusted_score", 0.0) or 0.0)
            existing.risk_quality_score = float(item.get("risk_quality_score", 0.0) or 0.0)
            existing.risk_quality_label = str(item.get("risk_quality_label", "") or "")
            existing.risk_quality_reason = str(item.get("risk_quality_reason", "") or "")
            existing.reason_tags_json = json.dumps(item.get("reason_tags", []), ensure_ascii=False)
            existing.blocker_tags_json = json.dumps(item.get("blocker_tags", []), ensure_ascii=False)
            existing.source_run_id = run_id
            existing.last_seen_date = run_date
            existing.active = action_label in actionable_labels
            if action_label == "ENTRY_READY":
                existing.status = "ready"
            elif action_label == "WATCH_WAIT_TRIGGER":
                existing.status = "watch"
            else:
                existing.status = "avoid"
            rows += 1

        # Expire symbols that disappear for 10+ trading days (calendar-day approximation here).
        if seen_symbols:
            stale_stmt = select(WatchPoolEntry).where(WatchPoolEntry.active.is_(True), ~WatchPoolEntry.symbol.in_(seen_symbols))
            stale_rows = list(self.session.execute(stale_stmt).scalars().all())
            for row in stale_rows:
                if row.last_seen_date and (run_date - row.last_seen_date).days >= 10:
                    row.active = False
                    row.status = "expired"
                    row.last_seen_date = run_date
                    row.source_run_id = run_id
        return rows

    def get_watch_pool(self, limit: int = 50, active_only: bool = True) -> list[dict]:
        stmt = select(WatchPoolEntry).order_by(
            WatchPoolEntry.risk_quality_score.desc(),
            WatchPoolEntry.risk_adjusted_score.desc(),
            WatchPoolEntry.entry_score.desc(),
            WatchPoolEntry.updated_at.desc(),
        )
        if active_only:
            stmt = stmt.where(WatchPoolEntry.active.is_(True))
        rows = list(self.session.execute(stmt).scalars().all())
        items = [
            {
                "symbol": row.symbol,
                "name": row.name,
                "status": row.status,
                "action_label": row.action_label,
                "entry_score": float(row.entry_score),
                "risk_score": float(row.risk_score),
                "risk_adjusted_score": float(row.risk_adjusted_score),
                "risk_quality_score": float(row.risk_quality_score),
                "risk_quality_label": row.risk_quality_label,
                "risk_quality_reason": row.risk_quality_reason,
                "reason_tags": self._safe_json_list(row.reason_tags_json),
                "blocker_tags": self._safe_json_list(row.blocker_tags_json),
                "source_run_id": row.source_run_id,
                "first_seen_date": row.first_seen_date.isoformat() if row.first_seen_date else None,
                "last_seen_date": row.last_seen_date.isoformat() if row.last_seen_date else None,
                "active": bool(row.active),
            }
            for row in rows
        ]
        return items[: max(1, int(limit))]

    def _candidate_snapshot_to_dict(self, row: CandidateSnapshot) -> dict:
        return {
            "run_id": row.run_id,
            "run_date": row.run_date.isoformat(),
            "symbol": row.symbol,
            "name": self.get_instrument_name(row.symbol),
            "rank": int(row.rank),
            "score": float(row.score),
            "risk_score": float(row.risk_score),
            "candidate_type": row.candidate_type,
            "reason_codes": self._safe_json_list(row.reason_codes_json),
            "risk_flags": self._safe_json_list(row.risk_flags_json),
            "data_quality": row.data_quality,
        }

    def _radar_snapshot_to_dict(self, row: DailyRadarSnapshot) -> dict:
        reason_tags = self._safe_json_list(row.reason_tags_json)
        industry_group = self._tag_value(reason_tags, "INDUSTRY_GROUP=") or self.get_instrument_industry(row.symbol) or "其他"
        payload = {
            "run_id": row.run_id,
            "run_date": row.run_date.isoformat(),
            "symbol": row.symbol,
            "name": row.name,
            "price": float(row.price),
            "rank": int(row.rank),
            "entry_score": float(row.entry_score),
            "risk_score": float(row.risk_score),
            "risk_adjusted_score": float(row.risk_adjusted_score),
            "action_label": row.action_label,
            "candidate_type": row.candidate_type,
            "reason_tags": reason_tags,
            "blocker_tags": self._safe_json_list(row.blocker_tags_json),
            "data_quality": row.data_quality,
            "fundamental_data_quality": row.fundamental_data_quality,
            "revenue_score": float(row.revenue_score),
            "quality_score": float(row.quality_score),
            "valuation_or_growth_score": float(row.valuation_or_growth_score),
            "news_risk_score": float(row.news_risk_score),
            "fundamental_summary": self._safe_json_dict(row.fundamental_summary_json),
            "news_summary": self._safe_json_dict(row.news_summary_json),
            "risk_quality_score": float(row.risk_quality_score),
            "risk_quality_label": row.risk_quality_label,
            "risk_quality_reason": row.risk_quality_reason,
            "industry_group": industry_group,
            "industry_rotation_score": self._tag_number(reason_tags, "INDUSTRY_ROTATION_SCORE=", scale=100.0),
            "industry_trend_label": self._tag_value(reason_tags, "INDUSTRY_TREND=") or "neutral",
            "industry_trend_score": self._tag_number(reason_tags, "INDUSTRY_TREND_SCORE=", scale=100.0),
            "industry_short_return": self._tag_number(reason_tags, "INDUSTRY_SHORT_RETURN=", scale=100.0),
            "industry_medium_return": self._tag_number(reason_tags, "INDUSTRY_MEDIUM_RETURN=", scale=100.0),
            "industry_breadth": self._tag_number(reason_tags, "INDUSTRY_BREADTH=", scale=100.0),
            "atr_pct": self._tag_number(reason_tags, "ATR_PCT=", scale=100.0),
            "atr_heat": self._tag_value(reason_tags, "ATR_HEAT=") or "normal",
            "outcome_reliability_score": self._tag_number(reason_tags, "OUTCOME_RELIABILITY_SCORE="),
            "outcome_reliability_boost": self._tag_number(reason_tags, "OUTCOME_RELIABILITY_BOOST="),
            "outcome_reliability_samples": int(self._tag_number(reason_tags, "OUTCOME_RELIABILITY_SAMPLES=")),
            "outcome_reliability_horizons": self._parse_reliability_horizons(
                self._tag_value(reason_tags, "OUTCOME_RELIABILITY_HORIZONS=")
            ),
            "symbol_adoption_feedback_score": self._tag_number(reason_tags, "SYMBOL_ADOPTION_FEEDBACK_SCORE="),
            "symbol_adoption_feedback_raw_boost": self._tag_number(reason_tags, "SYMBOL_ADOPTION_FEEDBACK_RAW_BOOST="),
            "symbol_adoption_feedback_boost": self._tag_number(reason_tags, "SYMBOL_ADOPTION_FEEDBACK_BOOST="),
            "symbol_adoption_feedback_samples": int(self._tag_number(reason_tags, "SYMBOL_ADOPTION_FEEDBACK_SAMPLES=")),
            "symbol_adoption_feedback_confidence_weight": self._tag_number(
                reason_tags, "SYMBOL_ADOPTION_FEEDBACK_CONFIDENCE_WEIGHT="
            ),
            "symbol_adoption_feedback_confidence_level": self._tag_value(
                reason_tags, "SYMBOL_ADOPTION_FEEDBACK_CONFIDENCE="
            )
            or "none",
            "symbol_adoption_feedback_horizons": self._parse_reliability_horizons(
                self._tag_value(reason_tags, "SYMBOL_ADOPTION_FEEDBACK_HORIZONS=")
            ),
        }
        if str(row.action_label or "") == "WATCH_WAIT_TRIGGER":
            payload["trigger_plan"] = self._daily_radar_trigger_plan(payload)
        return payload

    @staticmethod
    def _safe_json_list(value: str) -> list[str]:
        try:
            payload = json.loads(str(value or "[]"))
            if isinstance(payload, list):
                return [str(item) for item in payload]
        except Exception:
            pass
        return []

    @staticmethod
    def _safe_json_dict(value: str) -> dict:
        try:
            payload = json.loads(str(value or "{}"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {}

    @staticmethod
    def _sync_job_failure_summary(value: str) -> dict:
        payload = TradingRepository._safe_json_dict(value)
        if not payload:
            return {}

        def safe_int(raw: object) -> int:
            try:
                return int(raw or 0)
            except Exception:
                return 0

        def error_code(text: str, error_type: str = "") -> str:
            lower = text.lower()
            if str(error_type or "") == "NoDataError" or "no daily bars returned" in lower or "no data" in lower:
                return "upstream_no_data"
            if "notice api is unavailable" in lower:
                return "shioaji_notice_unavailable"
            if "400" in lower or "bad request" in lower or "422" in lower or "unprocessable entity" in lower:
                return "upstream_bad_request"
            if "402" in lower or "payment required" in lower:
                return "upstream_payment_required"
            if "403" in lower or "forbidden" in lower:
                return "upstream_forbidden"
            if "401" in lower or "unauthorized" in lower:
                return "upstream_unauthorized"
            if "429" in lower or "rate limit" in lower:
                return "upstream_rate_limited"
            if "404" in lower or "not found" in lower:
                return "upstream_not_found"
            if "timeout" in lower or "timed out" in lower:
                return "upstream_timeout"
            return str(error_type or "upstream_error")

        def compact_error(text: object) -> str:
            message = redact_sensitive(str(text or ""))
            message = message.split(" for url:", 1)[0]
            message = message.split(" url:", 1)[0]
            return " ".join(message.split())[:180]

        samples: list[dict] = []
        reason_codes: list[str] = []
        scope_stats: dict[str, dict] = {}
        symbol_stats: dict[str, dict] = {}

        def track_scope(
            scope: str,
            *,
            symbol: str = "",
            reason_code: str = "",
            failed_count: int = 1,
            aggregate: bool = False,
        ) -> None:
            key = scope or "unknown"
            entry = scope_stats.setdefault(key, {"scope": key, "failed_count": 0, "reason_codes": [], "symbols": []})
            count = max(1, int(failed_count or 1))
            if aggregate:
                entry["failed_count"] = max(int(entry["failed_count"]), count)
            else:
                entry["failed_count"] = int(entry["failed_count"]) + count
            if reason_code and reason_code not in entry["reason_codes"]:
                entry["reason_codes"].append(reason_code)
            if symbol and symbol not in entry["symbols"]:
                entry["symbols"].append(symbol)

        def track_symbol(symbol: str, *, scope: str = "", reason_code: str = "", failed_count: int = 1) -> None:
            if not symbol:
                return
            entry = symbol_stats.setdefault(symbol, {"symbol": symbol, "failed_count": 0, "scopes": [], "reason_codes": []})
            entry["failed_count"] = int(entry["failed_count"]) + max(1, int(failed_count or 1))
            if scope and scope not in entry["scopes"]:
                entry["scopes"].append(scope)
            if reason_code and reason_code not in entry["reason_codes"]:
                entry["reason_codes"].append(reason_code)

        for sample in payload.get("failed_samples", []) if isinstance(payload.get("failed_samples"), list) else []:
            if not isinstance(sample, dict):
                continue
            err = compact_error(sample.get("error"))
            code = error_code(err, str(sample.get("error_type") or ""))
            scope = str(sample.get("scope") or "")
            symbol = str(sample.get("symbol") or "")
            reason_codes.append(code)
            track_scope(scope, symbol=symbol, reason_code=code)
            track_symbol(symbol, scope=scope, reason_code=code)
            samples.append(
                {
                    "scope": scope,
                    "symbol": symbol,
                    "date": str(sample.get("date") or sample.get("month") or ""),
                    "error_type": str(sample.get("error_type") or ""),
                    "reason_code": code,
                    "message": err,
                }
            )

        for item in payload.get("failed_symbol_scopes", []) if isinstance(payload.get("failed_symbol_scopes"), list) else []:
            if not isinstance(item, dict):
                continue
            scope = str(item.get("scope") or "")
            symbol = str(item.get("symbol") or "").strip().upper()
            if not scope or not symbol:
                continue
            scope_entry = scope_stats.setdefault(scope, {"scope": scope, "failed_count": 0, "reason_codes": [], "symbols": []})
            reason_code = str(item.get("reason_code") or "")
            if not reason_code:
                reason_code = str((scope_entry.get("reason_codes") or [""])[0] or "")
            if symbol not in scope_entry["symbols"]:
                scope_entry["symbols"].append(symbol)
            symbol_entry = symbol_stats.get(symbol)
            if symbol_entry and scope in symbol_entry.get("scopes", []):
                if reason_code and reason_code not in symbol_entry["reason_codes"]:
                    symbol_entry["reason_codes"].append(reason_code)
            else:
                track_symbol(symbol, scope=scope, reason_code=reason_code)

        components: list[dict] = []
        sync_quality = payload.get("sync_quality") if isinstance(payload.get("sync_quality"), dict) else {}
        raw_components = sync_quality.get("components") if isinstance(sync_quality.get("components"), dict) else {}
        for name, component in raw_components.items():
            if not isinstance(component, dict):
                continue
            failed_count = safe_int(component.get("failed_count"))
            status = str(component.get("status") or "")
            if failed_count <= 0 and status not in {"partial", "failed", "danger", "warn"}:
                continue
            if failed_count > 0:
                track_scope(str(name), failed_count=failed_count, aggregate=True)
            components.append(
                {
                    "name": str(name),
                    "status": status,
                    "failed_count": failed_count,
                    "rows_upserted": safe_int(component.get("rows_upserted")),
                    "official_fallback_rows": safe_int(component.get("official_fallback_rows")),
                    "prefer_official_bulk": bool(component.get("prefer_official_bulk")),
                    "api_mode": str(component.get("api_mode") or ""),
                    "requested_start": str(component.get("requested_start") or ""),
                    "requested_end": str(component.get("requested_end") or ""),
                }
            )

        failed_count = safe_int(payload.get("failed_count") or payload.get("failed_count_total"))
        if failed_count <= 0:
            failed_count = sum(int(item.get("failed_count") or 0) for item in components)
        if not failed_count and not samples and not components and not payload.get("degraded"):
            return {}
        if payload.get("degrade_reasons"):
            reason_codes.extend(str(item) for item in payload.get("degrade_reasons", []) if item)
        return {
            "failed_count": failed_count,
            "partial_failure": bool(payload.get("partial_failure")) or failed_count > 0,
            "reason_codes": list(dict.fromkeys(reason_codes))[:8],
            "components": components[:12],
            "samples": samples[:5],
            "scope_failures": sorted(scope_stats.values(), key=lambda item: (-int(item["failed_count"]), str(item["scope"])))[:8],
            "symbol_failures": sorted(symbol_stats.values(), key=lambda item: (-int(item["failed_count"]), str(item["symbol"])))[:200],
        }

    @staticmethod
    def _tag_value(tags: list[str], prefix: str) -> str:
        for tag in tags:
            text = str(tag or "")
            if text.startswith(prefix):
                return text[len(prefix) :].strip()
        return ""

    @staticmethod
    def _tag_number(tags: list[str], prefix: str, *, scale: float = 1.0) -> float:
        raw = TradingRepository._tag_value(tags, prefix)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        divisor = float(scale or 1.0)
        return value / divisor

    @staticmethod
    def _parse_reliability_horizons(value: str) -> list[dict]:
        output: list[dict] = []
        for item in str(value or "").split("|"):
            text = item.strip()
            if not text or ":" not in text:
                continue
            horizon_text, rate_text = text.split(":", 1)
            try:
                horizon = int(horizon_text.strip().rstrip("dD"))
                positive_rate = float(rate_text.strip())
            except (TypeError, ValueError):
                continue
            if horizon <= 0:
                continue
            output.append({"lookahead_bars": horizon, "positive_rate": positive_rate})
        return output

    @staticmethod
    def _radar_outcome_stats(rows: list[dict]) -> dict:
        evaluated = [row for row in rows if row.get("lookahead_return") is not None]
        returns = [float(row["lookahead_return"]) for row in evaluated]
        positive_count = sum(1 for value in returns if value > 0)
        return {
            "count": len(rows),
            "evaluated_count": len(evaluated),
            "positive_count": positive_count,
            "positive_rate": (positive_count / len(evaluated)) if evaluated else None,
            "average_return": (sum(returns) / len(returns)) if returns else None,
            "best_return": max(returns) if returns else None,
            "worst_return": min(returns) if returns else None,
        }

    @staticmethod
    def _symbol_adoption_feedback_score(
        *,
        positive_rate: float,
        average_return: float,
        evaluated_count: int,
        run_count: int,
    ) -> float:
        if evaluated_count <= 0:
            return 0.0
        evidence_weight = TradingRepository._symbol_adoption_feedback_confidence(
            evaluated_count=evaluated_count,
            run_count=run_count,
        )["weight"]
        hit_component = (max(0.0, min(1.0, float(positive_rate))) - 0.5) * 1.25
        return_component = max(-0.06, min(0.06, float(average_return))) * 8.0
        return round(max(-1.0, min(1.0, (hit_component + return_component) * evidence_weight)), 4)

    @staticmethod
    def _symbol_adoption_feedback_confidence(*, evaluated_count: int, run_count: int) -> dict[str, float | str]:
        if evaluated_count <= 0 or run_count <= 0:
            return {"weight": 0.0, "level": "none", "label": "尚無樣本"}
        weight = min(1.0, (float(evaluated_count) / 6.0) * 0.65 + (float(run_count) / 3.0) * 0.35)
        if weight >= 0.75:
            level = "high"
            label = "高信心"
        elif weight >= 0.35:
            level = "medium"
            label = "中信心"
        else:
            level = "low"
            label = "低信心"
        return {"weight": round(weight, 4), "level": level, "label": label}

    @classmethod
    def _radar_outcome_aggregates(cls, rows: list[dict], bucket_fn, *, limit: int | None = None) -> list[dict]:
        grouped: dict[str, list[dict]] = {}
        labels: dict[str, str] = {}
        for row in rows:
            for raw_key, raw_label in bucket_fn(row):
                key = str(raw_key or "").strip()
                if not key:
                    continue
                grouped.setdefault(key, []).append(row)
                labels.setdefault(key, str(raw_label or key).strip() or key)

        output: list[dict] = []
        for key, bucket_rows in grouped.items():
            output.append({"key": key, "label": labels.get(key, key), **cls._radar_outcome_stats(bucket_rows)})

        def _sort_key(item: dict) -> tuple:
            positive_rate = item["positive_rate"] if item["positive_rate"] is not None else -1.0
            average_return = item["average_return"] if item["average_return"] is not None else -99.0
            return (-int(item["evaluated_count"]), -float(positive_rate), -float(average_return), -int(item["count"]), str(item["key"]))

        output.sort(key=_sort_key)
        if limit is not None:
            return output[: max(1, int(limit))]
        return output

    @classmethod
    def _radar_outcome_reason_buckets(cls, row: dict) -> list[tuple[str, str]]:
        buckets: list[tuple[str, str]] = []
        seen: set[str] = set()
        for tag in row.get("reason_tags", []):
            bucket = cls._radar_outcome_reason_bucket(str(tag or ""))
            if not bucket or bucket in seen:
                continue
            buckets.append((bucket, bucket))
            seen.add(bucket)
        return buckets

    @staticmethod
    def _radar_outcome_reason_bucket(tag: str) -> str:
        return radar_outcome_bucket_for_tag(tag)

    @staticmethod
    def _optional_float(value) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def add_trading_execution_record(self, *, intent: dict, result: dict) -> None:
        pretrade = result.get("pretrade") or {}
        row = TradingExecutionRecord(
            intent_id=str(intent.get("intent_id") or result.get("intent_id")),
            source=str(intent.get("source", "")),
            environment=str(result.get("environment") or intent.get("environment", "")),
            symbol=str(result.get("symbol") or intent.get("symbol", "")),
            side=str(result.get("side") or intent.get("side", "")),
            price=float(result.get("price") or intent.get("price") or 0.0),
            qty=int(result.get("quantity") or 0),
            accepted=bool(result.get("accepted")),
            executed=bool(result.get("executed")),
            status=str(result.get("status", "")),
            reason=str(pretrade.get("reason", "")),
            intent_json=json.dumps(intent, ensure_ascii=False, default=str),
            pretrade_json=json.dumps(pretrade, ensure_ascii=False, default=str),
            execution_json=json.dumps(result, ensure_ascii=False, default=str),
        )
        self.session.add(row)

    def add_order_preview_record(self, *, preview: dict, intent: dict | None = None) -> None:
        safe_preview = redact_sensitive(dict(preview or {}))
        safe_intent = redact_sensitive(dict(intent or {}))
        row = OrderPreviewRecord(
            preview_id=str(safe_preview.get("preview_id", "")),
            intent_id=str(safe_intent.get("intent_id", "")),
            strategy_name=str(safe_preview.get("strategy_name", "")),
            strategy_version=str(safe_preview.get("strategy_version", "")),
            signal_id=str(safe_preview.get("signal_id", "")),
            symbol=str(safe_preview.get("symbol", "")),
            side=str(safe_preview.get("side", "")),
            price=float(safe_preview.get("price") or 0.0),
            qty=int(safe_preview.get("quantity") or 0),
            estimated_total_cost=float(safe_preview.get("estimated_total_cost") or 0.0),
            available_cash=(
                float(safe_preview["available_cash"]) if safe_preview.get("available_cash") is not None else None
            ),
            position_before=int(safe_preview.get("position_before") or 0),
            status="created",
            reason="",
            intent_json=json.dumps(safe_intent, ensure_ascii=False, default=str),
            preview_json=json.dumps(safe_preview, ensure_ascii=False, default=str),
            decision_json="{}",
            expires_at=self._parse_datetime(safe_preview.get("expires_at")) or datetime.utcnow(),
            created_at=self._parse_datetime(safe_preview.get("created_at")) or datetime.utcnow(),
        )
        self.session.add(row)

    def update_order_preview_decision(self, *, decision: dict) -> None:
        safe_decision = redact_sensitive(dict(decision or {}))
        preview_id = str(safe_decision.get("preview_id", ""))
        if not preview_id:
            return
        row = self.session.execute(
            select(OrderPreviewRecord).where(OrderPreviewRecord.preview_id == preview_id).limit(1)
        ).scalar_one_or_none()
        if row is None:
            row = OrderPreviewRecord(
                preview_id=preview_id,
                intent_id="",
                symbol="",
                side="",
                price=0.0,
                qty=0,
                expires_at=datetime.utcnow(),
                status="decision_only",
            )
            self.session.add(row)
        row.status = "accepted" if bool(safe_decision.get("accepted")) else "rejected"
        row.reason = str(safe_decision.get("reason", ""))
        row.decision_json = json.dumps(safe_decision, ensure_ascii=False, default=str)
        row.decided_at = datetime.utcnow()

    def add_promotion_gate_record(self, *, decision: dict) -> None:
        safe_decision = redact_sensitive(dict(decision or {}))
        row = PromotionGateRecord(
            strategy_name=str(safe_decision.get("strategy_name", "")),
            strategy_version=str(safe_decision.get("strategy_version", "")),
            paper_days=int(safe_decision.get("paper_days") or 0),
            paper_trades=int(safe_decision.get("paper_trades") or 0),
            max_drawdown=float(safe_decision.get("max_drawdown") or 0.0),
            accepted=bool(safe_decision.get("accepted")),
            reason=str(safe_decision.get("reason", "")),
            blocking_reasons_json=json.dumps(list(safe_decision.get("blocking_reasons") or []), ensure_ascii=False),
            decision_json=json.dumps(safe_decision, ensure_ascii=False, default=str),
        )
        self.session.add(row)

    def add_reconciliation_record(self, *, result: dict) -> None:
        safe_result = redact_sensitive(dict(result or {}))
        row = ReconciliationRecord(
            matched=bool(safe_result.get("matched")),
            cash_diff=float(safe_result.get("cash_diff") or 0.0),
            blocking_reasons_json=json.dumps(list(safe_result.get("blocking_reasons") or []), ensure_ascii=False),
            result_json=json.dumps(safe_result, ensure_ascii=False, default=str),
            checked_at=self._parse_datetime(safe_result.get("checked_at")) or datetime.utcnow(),
        )
        self.session.add(row)

    def add_advisor_observation_record(self, *, record: dict) -> dict:
        safe_record = redact_sensitive(dict(record or {}))
        observation_date = self._parse_date(safe_record.get("observation_date")) or datetime.utcnow().date()
        blockers = [str(item) for item in safe_record.get("blocker_reasons") or [] if str(item).strip()]
        row = AdvisorObservationRecord(
            observation_date=observation_date,
            symbol=str(safe_record.get("symbol", "") or "").strip().upper(),
            advisor_name=str(safe_record.get("advisor_name", "stub") or "stub"),
            advisor_version=str(safe_record.get("advisor_version", "") or ""),
            preview_created=bool(safe_record.get("preview_created")),
            human_action=str(safe_record.get("human_action", "skipped") or "skipped"),
            simulation_result=str(safe_record.get("simulation_result", "skipped") or "skipped"),
            data_quality=str(safe_record.get("data_quality", "unknown") or "unknown"),
            passed=bool(safe_record.get("passed")),
            blocker_reasons_json=json.dumps(blockers, ensure_ascii=False),
            notes=str(safe_record.get("notes", "") or ""),
            meta_json=json.dumps(dict(safe_record.get("meta") or {}), ensure_ascii=False, default=str),
        )
        self.session.add(row)
        self.session.flush()
        return self._advisor_observation_to_dict(row)

    def list_recent_advisor_observation_records(
        self,
        *,
        limit: int = 20,
        advisor_name: str | None = None,
    ) -> list[dict]:
        stmt = select(AdvisorObservationRecord).order_by(
            AdvisorObservationRecord.observation_date.desc(),
            AdvisorObservationRecord.created_at.desc(),
        )
        if advisor_name:
            stmt = stmt.where(AdvisorObservationRecord.advisor_name == str(advisor_name))
        stmt = stmt.limit(max(1, int(limit)))
        rows = self.session.execute(stmt).scalars()
        return [self._advisor_observation_to_dict(row) for row in rows]

    def summarize_advisor_observation_period(
        self,
        *,
        advisor_name: str = "stub",
        min_days: int = 3,
        limit: int = 100,
    ) -> dict:
        rows = self.list_recent_advisor_observation_records(
            limit=max(int(limit), int(min_days), 1),
            advisor_name=advisor_name,
        )
        observed_dates = sorted({str(row["observation_date"]) for row in rows if row.get("observation_date")})
        failed_rows = [row for row in rows if not row.get("passed")]
        blocker_counts: dict[str, int] = {}
        for row in failed_rows:
            reasons = row.get("blocker_reasons") or []
            if not reasons:
                reasons = ["observation_not_passed"]
            for reason in reasons:
                key = str(reason)
                blocker_counts[key] = blocker_counts.get(key, 0) + 1
        blocking_reasons = sorted(blocker_counts)
        if len(observed_dates) < int(min_days):
            blocking_reasons.insert(0, "observation_days_insufficient")
        ready = not blocking_reasons and len(observed_dates) >= int(min_days)
        return {
            "advisor_name": advisor_name,
            "min_days": int(min_days),
            "observed_days": len(observed_dates),
            "observed_dates": observed_dates,
            "total_records": len(rows),
            "passed_records": len([row for row in rows if row.get("passed")]),
            "failed_records": len(failed_rows),
            "ready_for_next_phase": ready,
            "blocking_reasons": blocking_reasons,
            "recent_records": rows[: min(10, len(rows))],
        }

    def _advisor_observation_to_dict(self, row: AdvisorObservationRecord) -> dict:
        return {
            "id": int(row.id),
            "observation_date": row.observation_date.isoformat() if row.observation_date else None,
            "symbol": row.symbol,
            "advisor_name": row.advisor_name,
            "advisor_version": row.advisor_version,
            "preview_created": bool(row.preview_created),
            "human_action": row.human_action,
            "simulation_result": row.simulation_result,
            "data_quality": row.data_quality,
            "passed": bool(row.passed),
            "blocker_reasons": self._safe_json_list(row.blocker_reasons_json),
            "notes": row.notes,
            "meta": self._safe_json_dict(row.meta_json),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def list_recent_trading_execution_records(self, *, limit: int = 20) -> list[dict]:
        rows = self.session.execute(
            select(TradingExecutionRecord)
            .order_by(TradingExecutionRecord.created_at.desc())
            .limit(max(1, int(limit)))
        ).scalars()
        return [
            {
                "intent_id": row.intent_id,
                "source": row.source,
                "environment": row.environment,
                "symbol": row.symbol,
                "side": row.side,
                "price": float(row.price),
                "quantity": int(row.qty),
                "accepted": bool(row.accepted),
                "executed": bool(row.executed),
                "status": row.status,
                "reason": row.reason,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    def list_recent_order_preview_records(self, *, limit: int = 20) -> list[dict]:
        rows = self.session.execute(
            select(OrderPreviewRecord).order_by(OrderPreviewRecord.created_at.desc()).limit(max(1, int(limit)))
        ).scalars()
        return [
            {
                "preview_id": row.preview_id,
                "intent_id": row.intent_id,
                "strategy_name": row.strategy_name,
                "strategy_version": row.strategy_version,
                "signal_id": row.signal_id,
                "symbol": row.symbol,
                "side": row.side,
                "price": float(row.price),
                "quantity": int(row.qty),
                "estimated_total_cost": float(row.estimated_total_cost),
                "available_cash": float(row.available_cash) if row.available_cash is not None else None,
                "position_before": int(row.position_before),
                "status": row.status,
                "reason": row.reason,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    def expire_order_preview_records(self, *, now: datetime | None = None) -> int:
        current = now or datetime.utcnow()
        rows = list(
            self.session.execute(
                select(OrderPreviewRecord).where(
                    OrderPreviewRecord.status.in_(("created", "decision_only")),
                    OrderPreviewRecord.expires_at <= current,
                )
            ).scalars()
        )
        for row in rows:
            row.status = "expired"
            row.reason = "preview_expired"
            row.decided_at = current
        return len(rows)

    def list_recent_promotion_gate_records(self, *, limit: int = 20) -> list[dict]:
        rows = self.session.execute(
            select(PromotionGateRecord).order_by(PromotionGateRecord.created_at.desc()).limit(max(1, int(limit)))
        ).scalars()
        return [
            {
                "strategy_name": row.strategy_name,
                "strategy_version": row.strategy_version,
                "paper_days": int(row.paper_days),
                "paper_trades": int(row.paper_trades),
                "max_drawdown": float(row.max_drawdown),
                "accepted": bool(row.accepted),
                "reason": row.reason,
                "blocking_reasons": self._safe_json_list(row.blocking_reasons_json),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    def list_recent_reconciliation_records(self, *, limit: int = 20) -> list[dict]:
        rows = self.session.execute(
            select(ReconciliationRecord).order_by(ReconciliationRecord.checked_at.desc()).limit(max(1, int(limit)))
        ).scalars()
        return [
            {
                "matched": bool(row.matched),
                "cash_diff": float(row.cash_diff),
                "blocking_reasons": self._safe_json_list(row.blocking_reasons_json),
                "checked_at": row.checked_at.isoformat() if row.checked_at else None,
            }
            for row in rows
        ]

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _parse_date(value: object) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not value:
            return None
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
