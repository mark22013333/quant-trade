from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    market: Mapped[str] = mapped_column(String(10), default="TW")
    is_0050: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Universe0050Snapshot(Base):
    __tablename__ = "universe_0050_snapshot"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    __table_args__ = (UniqueConstraint("snapshot_date", "symbol", name="uq_universe_0050_snapshot"),)


class DailyBar(Base):
    __tablename__ = "daily_bars"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(30), default="finmind")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_daily_bars_symbol_date"),)


class InstitutionalChipDaily(Base):
    __tablename__ = "institutional_chip_daily"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    foreign_net_buy: Mapped[float] = mapped_column(Float, default=0.0)
    investment_trust_net_buy: Mapped[float] = mapped_column(Float, default=0.0)
    dealer_net_buy: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(30), default="finmind")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_institutional_chip_symbol_date"),)


class BrokerAggDaily(Base):
    __tablename__ = "broker_agg_daily"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    concentration_proxy: Mapped[float] = mapped_column(Float, default=0.0)
    top5_net_buy: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(30), default="finmind")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_broker_agg_symbol_date"),)


class DispositionPeriod(Base):
    __tablename__ = "disposition_periods"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    disposition_type: Mapped[str] = mapped_column(String(120), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(30), default="finmind")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "start_date",
            "end_date",
            "disposition_type",
            "reason",
            name="uq_disposition_symbol_period_reason",
        ),
    )


class ShareholdingDaily(Base):
    __tablename__ = "shareholding_daily"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    major_holder_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    retail_holder_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(30), default="finmind")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_shareholding_symbol_date"),)


class HoldingSharesPerDaily(Base):
    __tablename__ = "holding_shares_per_daily"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    concentration_proxy: Mapped[float] = mapped_column(Float, default=0.0)
    dispersion_proxy: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(30), default="finmind")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_holding_shares_per_symbol_date"),)


class MonthlyRevenue(Base):
    __tablename__ = "monthly_revenues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    announce_date: Mapped[date] = mapped_column(Date, index=True)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    revenue_yoy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_mom_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="finmind")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("symbol", "period", name="uq_monthly_revenue_symbol_period"),)


class FinancialStatementSummary(Base):
    __tablename__ = "financial_statement_summaries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    period: Mapped[str] = mapped_column(String(10), index=True)
    announce_date: Mapped[date] = mapped_column(Date, index=True)
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    roe_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_margin_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_ratio_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_cash_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="finmind")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("symbol", "period", name="uq_financial_summary_symbol_period"),)


class NewsEvent(Base):
    __tablename__ = "news_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    news_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    source_name: Mapped[str] = mapped_column(String(80), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    llm_summary: Mapped[str] = mapped_column(Text, default="")
    risk_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(30), default="finmind")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("symbol", "news_date", "title", name="uq_news_event_symbol_date_title"),)


class AdvisorDecisionRecord(Base):
    __tablename__ = "advisor_decision_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    advisor_name: Mapped[str] = mapped_column(String(80), default="", index=True)
    advisor_version: Mapped[str] = mapped_column(String(80), default="")
    symbol: Mapped[str] = mapped_column(String(20), default="", index=True)
    action: Mapped[str] = mapped_column(String(16), default="reject", index=True)
    status: Mapped[str] = mapped_column(String(24), default="created", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    preview_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_errors_json: Mapped[str] = mapped_column(Text, default="[]")
    rejected_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[float] = mapped_column(Float)
    ma60: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    volume_ma5: Mapped[float] = mapped_column(Float, default=0.0)
    rsi3: Mapped[float] = mapped_column(Float, default=0.0)
    k9: Mapped[float] = mapped_column(Float, default=0.0)
    d9: Mapped[float] = mapped_column(Float, default=0.0)
    atr14: Mapped[float] = mapped_column(Float, default=0.0)
    foreign_net_5d: Mapped[float] = mapped_column(Float, default=0.0)
    investment_net_5d: Mapped[float] = mapped_column(Float, default=0.0)
    dealer_net_5d: Mapped[float] = mapped_column(Float, default=0.0)
    chip_concentration_proxy: Mapped[float] = mapped_column(Float, default=0.0)
    chip_concentration_up3: Mapped[bool] = mapped_column(Boolean, default=False)
    disposition_active: Mapped[bool] = mapped_column(Boolean, default=False)
    revenue_score: Mapped[float] = mapped_column(Float, default=0.5)
    quality_score: Mapped[float] = mapped_column(Float, default=0.5)
    valuation_or_growth_score: Mapped[float] = mapped_column(Float, default=0.5)
    news_risk_score: Mapped[float] = mapped_column(Float, default=0.5)
    fundamental_data_quality: Mapped[str] = mapped_column(String(20), default="missing")
    entry_ready: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_feature_snapshot_symbol_date"),)


class SignalRecord(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy_name: Mapped[str] = mapped_column(String(50))
    signal_type: Mapped[str] = mapped_column(String(20))
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OrderRecord(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer)
    est_cost: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="created")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class FillRecord(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    fill_price: Mapped[float] = mapped_column(Float)
    fill_qty: Mapped[int] = mapped_column(Integer)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    tax: Mapped[float] = mapped_column(Float, default=0.0)
    filled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TradingExecutionRecord(Base):
    __tablename__ = "trading_execution_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intent_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(24), index=True)
    environment: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    executed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    intent_json: Mapped[str] = mapped_column(Text, default="{}")
    pretrade_json: Mapped[str] = mapped_column(Text, default="{}")
    execution_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OrderPreviewRecord(Base):
    __tablename__ = "order_preview_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    preview_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    intent_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    strategy_name: Mapped[str] = mapped_column(String(80), default="", index=True)
    strategy_version: Mapped[str] = mapped_column(String(80), default="")
    signal_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    estimated_total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    available_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_before: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    intent_json: Mapped[str] = mapped_column(Text, default="{}")
    preview_json: Mapped[str] = mapped_column(Text, default="{}")
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PromotionGateRecord(Base):
    __tablename__ = "promotion_gate_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(80), default="", index=True)
    strategy_version: Mapped[str] = mapped_column(String(80), default="", index=True)
    paper_days: Mapped[int] = mapped_column(Integer, default=0)
    paper_trades: Mapped[int] = mapped_column(Integer, default=0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    blocking_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ReconciliationRecord(Base):
    __tablename__ = "reconciliation_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    cash_diff: Mapped[float] = mapped_column(Float, default=0.0)
    blocking_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    cash_available: Mapped[float] = mapped_column(Float)
    cash_settled: Mapped[float] = mapped_column(Float, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    equity: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(50), index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    rows_upserted: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[str] = mapped_column(Text, default="")


class CandidateSnapshot(Base):
    __tablename__ = "candidate_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    candidate_type: Mapped[str] = mapped_column(String(20), default="watch")
    reason_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    risk_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    data_quality: Mapped[str] = mapped_column(String(20), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    __table_args__ = (UniqueConstraint("run_id", "symbol", name="uq_candidate_snapshot_run_symbol"),)


class DailyRadarSnapshot(Base):
    __tablename__ = "daily_radar_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(100), default="")
    price: Mapped[float] = mapped_column(Float, default=0.0)
    entry_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    action_label: Mapped[str] = mapped_column(String(32), default="WATCH_WAIT_TRIGGER")
    candidate_type: Mapped[str] = mapped_column(String(20), default="watch")
    reason_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    blocker_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    data_quality: Mapped[str] = mapped_column(String(20), default="unknown")
    fundamental_data_quality: Mapped[str] = mapped_column(String(20), default="missing")
    revenue_score: Mapped[float] = mapped_column(Float, default=0.5)
    quality_score: Mapped[float] = mapped_column(Float, default=0.5)
    valuation_or_growth_score: Mapped[float] = mapped_column(Float, default=0.5)
    news_risk_score: Mapped[float] = mapped_column(Float, default=0.5)
    fundamental_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    news_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    __table_args__ = (UniqueConstraint("run_id", "symbol", name="uq_daily_radar_run_symbol"),)


class WatchPoolEntry(Base):
    __tablename__ = "watch_pool_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(16), default="watch", index=True)
    action_label: Mapped[str] = mapped_column(String(32), default="WATCH_WAIT_TRIGGER")
    entry_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    reason_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    blocker_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    source_run_id: Mapped[str] = mapped_column(String(40), default="")
    first_seen_date: Mapped[date] = mapped_column(Date, index=True)
    last_seen_date: Mapped[date] = mapped_column(Date, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
