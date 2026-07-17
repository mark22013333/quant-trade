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
                    "degraded": bool(meta.get("degraded", False)),
                    "insufficient_candidates": bool(meta.get("insufficient_candidates", False)),
                    "degrade_reasons": list(meta.get("degrade_reasons", [])),
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
        return [
            {
                "run_id": row.run_id,
                "run_date": row.run_date.isoformat(),
                "symbol": row.symbol,
                "name": row.name,
                "price": float(row.price),
                "rank": int(row.rank),
                "entry_score": float(row.entry_score),
                "risk_score": float(row.risk_score),
                "action_label": row.action_label,
                "candidate_type": row.candidate_type,
                "reason_tags": self._safe_json_list(row.reason_tags_json),
                "blocker_tags": self._safe_json_list(row.blocker_tags_json),
                "data_quality": row.data_quality,
                "fundamental_data_quality": row.fundamental_data_quality,
                "revenue_score": float(row.revenue_score),
                "quality_score": float(row.quality_score),
                "valuation_or_growth_score": float(row.valuation_or_growth_score),
                "news_risk_score": float(row.news_risk_score),
                "fundamental_summary": self._safe_json_dict(row.fundamental_summary_json),
                "news_summary": self._safe_json_dict(row.news_summary_json),
            }
            for row in rows
        ]

    def upsert_watch_pool(self, run_id: str, run_date: date, records: list[dict]) -> int:
        # Only actionable symbols remain active in the watch pool.
        actionable_labels = {"ENTRY_READY", "WATCH_WAIT_TRIGGER"}
        rows = 0
        seen_symbols: set[str] = set()
        for item in records:
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
        stmt = select(WatchPoolEntry).order_by(WatchPoolEntry.entry_score.desc(), WatchPoolEntry.updated_at.desc())
        if active_only:
            stmt = stmt.where(WatchPoolEntry.active.is_(True))
        stmt = stmt.limit(max(1, int(limit)))
        rows = list(self.session.execute(stmt).scalars().all())
        return [
            {
                "symbol": row.symbol,
                "name": row.name,
                "status": row.status,
                "action_label": row.action_label,
                "entry_score": float(row.entry_score),
                "risk_score": float(row.risk_score),
                "reason_tags": self._safe_json_list(row.reason_tags_json),
                "blocker_tags": self._safe_json_list(row.blocker_tags_json),
                "source_run_id": row.source_run_id,
                "first_seen_date": row.first_seen_date.isoformat() if row.first_seen_date else None,
                "last_seen_date": row.last_seen_date.isoformat() if row.last_seen_date else None,
                "active": bool(row.active),
            }
            for row in rows
        ]

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
