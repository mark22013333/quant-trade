from __future__ import annotations

import json
from datetime import date
from typing import Iterable

from sqlalchemy import and_, delete, distinct, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BrokerAggDaily,
    CandidateSnapshot,
    DailyRadarSnapshot,
    DailyBar,
    DispositionPeriod,
    FeatureSnapshot,
    HoldingSharesPerDaily,
    InstitutionalChipDaily,
    Instrument,
    ShareholdingDaily,
    SyncJob,
    TradingExecutionRecord,
    Universe0050Snapshot,
    WatchPoolEntry,
)


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
                raw_market = str(raw.get("market", "") or "").strip().upper()
                item_market = raw_market or str(market or "TW").strip().upper() or "TW"
                item_is_0050 = bool(raw.get("is_0050", is_0050))
            else:
                symbol = str(raw).strip()
                raw_name = ""
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
                    is_0050=item_is_0050,
                    is_active=True,
                )
                self.session.add(row)
            else:
                row.market = item_market
                row.is_0050 = bool(row.is_0050 or item_is_0050)
                if raw_name:
                    row.name = raw_name
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
