from __future__ import annotations

import ast
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db.models import Base
from app.db.schema import ALEMBIC_HEAD_REVISION, initialize_schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INITIAL_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260605_0001_initial_schema.py"
TRADING_EXECUTION_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260608_0002_trading_execution_records.py"
HEAD_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260608_0003_trading_audit_records.py"
MARKET_FUNDAMENTALS_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260608_0004_market_fundamentals_news.py"
TRADING_ADVISOR_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260609_0005_trading_advisor.py"
ORDER_PREVIEW_INTENT_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260609_0006_order_preview_intent_json.py"
ADVISOR_OBSERVATION_REVISION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "20260612_0007_advisor_observation_records.py"
)
INSTRUMENT_INDUSTRY_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260625_0008_instrument_industry.py"
INDUSTRY_INDEX_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260625_0009_industry_index_bars.py"
ATTENTION_STOCKS_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260626_0010_attention_stocks.py"
RADAR_RISK_ADJUSTED_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260627_0011_radar_risk_adjusted_score.py"
WATCH_POOL_RISK_QUALITY_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260628_0012_watch_pool_risk_quality.py"
DAILY_RADAR_RISK_QUALITY_REVISION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "20260629_0013_daily_radar_risk_quality.py"
)


def _revision_assignments() -> dict[str, object]:
    tree = ast.parse(DAILY_RADAR_RISK_QUALITY_REVISION_PATH.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id in {"revision", "down_revision"}:
            values[node.targets[0].id] = ast.literal_eval(node.value)
    return values


def test_alembic_scaffold_points_to_current_schema_head():
    assert (PROJECT_ROOT / "alembic.ini").exists()
    assert (PROJECT_ROOT / "alembic" / "env.py").exists()

    values = _revision_assignments()

    assert values["revision"] == ALEMBIC_HEAD_REVISION
    assert values["down_revision"] == "20260628_0012"


def test_initial_revision_uses_application_metadata():
    text = INITIAL_REVISION_PATH.read_text(encoding="utf-8")

    assert "Base.metadata.create_all" in text
    assert "Base.metadata.sorted_tables" in text
    assert "daily_bars" in Base.metadata.tables
    assert "sync_jobs" in Base.metadata.tables


def test_trading_execution_records_revision_is_checkfirst_safe():
    text = TRADING_EXECUTION_REVISION_PATH.read_text(encoding="utf-8")

    assert "TradingExecutionRecord" in text
    assert "checkfirst=True" in text
    assert "trading_execution_records" in Base.metadata.tables


def test_trading_audit_records_revision_is_checkfirst_safe():
    text = HEAD_REVISION_PATH.read_text(encoding="utf-8")

    assert "OrderPreviewRecord" in text
    assert "PromotionGateRecord" in text
    assert "ReconciliationRecord" in text
    assert "checkfirst=True" in text
    assert "order_preview_records" in Base.metadata.tables


def test_market_fundamentals_revision_is_checkfirst_safe():
    text = MARKET_FUNDAMENTALS_REVISION_PATH.read_text(encoding="utf-8")

    assert "MonthlyRevenue" in text
    assert "FinancialStatementSummary" in text
    assert "NewsEvent" in text
    assert "checkfirst=True" in text
    assert "monthly_revenues" in Base.metadata.tables
    assert "financial_statement_summaries" in Base.metadata.tables
    assert "news_events" in Base.metadata.tables


def test_trading_advisor_revision_is_checkfirst_safe():
    text = TRADING_ADVISOR_REVISION_PATH.read_text(encoding="utf-8")

    assert "AdvisorDecisionRecord" in text
    assert "checkfirst=True" in text
    assert "advisor_decision_records" in Base.metadata.tables


def test_order_preview_intent_revision_is_checkfirst_safe():
    text = ORDER_PREVIEW_INTENT_REVISION_PATH.read_text(encoding="utf-8")

    assert "intent_json" in text
    assert "order_preview_records" in text
    assert "inspector.has_table" in text
    assert "intent_json" in Base.metadata.tables["order_preview_records"].columns


def test_advisor_observation_revision_is_checkfirst_safe():
    text = ADVISOR_OBSERVATION_REVISION_PATH.read_text(encoding="utf-8")

    assert "AdvisorObservationRecord" in text
    assert "checkfirst=True" in text
    assert "advisor_observation_records" in Base.metadata.tables


def test_instrument_industry_revision_is_checkfirst_safe():
    text = INSTRUMENT_INDUSTRY_REVISION_PATH.read_text(encoding="utf-8")

    assert "industry" in text
    assert "instruments" in text
    assert "inspector.has_table" in text
    assert "industry" in Base.metadata.tables["instruments"].columns


def test_industry_index_revision_is_checkfirst_safe():
    text = INDUSTRY_INDEX_REVISION_PATH.read_text(encoding="utf-8")

    assert "IndustryIndexBar" in text
    assert "checkfirst=True" in text
    assert "industry_index_bars" in Base.metadata.tables


def test_attention_stocks_revision_is_checkfirst_safe():
    text = ATTENTION_STOCKS_REVISION_PATH.read_text(encoding="utf-8")

    assert "AttentionStock" in text
    assert "checkfirst=True" in text
    assert "attention_stocks" in Base.metadata.tables


def test_radar_score_revisions_are_checkfirst_safe():
    for path in (
        RADAR_RISK_ADJUSTED_REVISION_PATH,
        WATCH_POOL_RISK_QUALITY_REVISION_PATH,
        DAILY_RADAR_RISK_QUALITY_REVISION_PATH,
    ):
        revision_text = path.read_text(encoding="utf-8")
        assert "inspector.has_table" in revision_text
        assert "inspector.get_columns" in revision_text


def test_fresh_database_can_upgrade_to_head(monkeypatch, tmp_path):
    database_path = tmp_path / "fresh-upgrade.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    inspector = inspect(engine)
    daily_radar_columns = {column["name"] for column in inspector.get_columns("daily_radar_snapshots")}
    watch_pool_columns = {column["name"] for column in inspector.get_columns("watch_pool_entries")}
    assert {"risk_adjusted_score", "risk_quality_score", "risk_quality_label", "risk_quality_reason"} <= daily_radar_columns
    assert {"risk_adjusted_score", "risk_quality_score", "risk_quality_label", "risk_quality_reason"} <= watch_pool_columns


def test_initialize_schema_backfills_market_fundamental_columns_for_legacy_sqlite():
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE instruments (
                    symbol VARCHAR(20) PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    market VARCHAR(10) NOT NULL,
                    is_0050 BOOLEAN NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE feature_snapshots (
                    id INTEGER PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    date DATE NOT NULL,
                    close FLOAT NOT NULL,
                    ma60 FLOAT NOT NULL,
                    volume FLOAT NOT NULL,
                    volume_ma5 FLOAT NOT NULL,
                    rsi3 FLOAT NOT NULL,
                    k9 FLOAT NOT NULL,
                    d9 FLOAT NOT NULL,
                    atr14 FLOAT NOT NULL,
                    foreign_net_5d FLOAT NOT NULL,
                    investment_net_5d FLOAT NOT NULL,
                    dealer_net_5d FLOAT NOT NULL,
                    chip_concentration_proxy FLOAT NOT NULL,
                    chip_concentration_up3 BOOLEAN NOT NULL,
                    disposition_active BOOLEAN NOT NULL,
                    entry_ready BOOLEAN NOT NULL,
                    meta_json TEXT NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE daily_radar_snapshots (
                    id INTEGER PRIMARY KEY,
                    run_id VARCHAR(40) NOT NULL,
                    run_date DATE NOT NULL,
                    symbol VARCHAR(20) NOT NULL,
                    rank INTEGER NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    price FLOAT NOT NULL,
                    entry_score FLOAT NOT NULL,
                    risk_score FLOAT NOT NULL,
                    action_label VARCHAR(32) NOT NULL,
                    candidate_type VARCHAR(20) NOT NULL,
                    reason_tags_json TEXT NOT NULL,
                    blocker_tags_json TEXT NOT NULL,
                    data_quality VARCHAR(20) NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE watch_pool_entries (
                    id INTEGER PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    action_label VARCHAR(32) NOT NULL,
                    entry_score FLOAT NOT NULL,
                    risk_score FLOAT NOT NULL,
                    risk_adjusted_score FLOAT NOT NULL,
                    reason_tags_json TEXT NOT NULL,
                    blocker_tags_json TEXT NOT NULL,
                    source_run_id VARCHAR(40) NOT NULL,
                    first_seen_date DATE NOT NULL,
                    last_seen_date DATE NOT NULL,
                    active BOOLEAN NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )

    initialize_schema(engine)

    inspector = inspect(engine)
    instrument_columns = {column["name"] for column in inspector.get_columns("instruments")}
    feature_columns = {column["name"] for column in inspector.get_columns("feature_snapshots")}
    radar_columns = {column["name"] for column in inspector.get_columns("daily_radar_snapshots")}
    watch_pool_columns = {column["name"] for column in inspector.get_columns("watch_pool_entries")}

    assert "industry" in instrument_columns
    assert {
        "revenue_score",
        "quality_score",
        "valuation_or_growth_score",
        "news_risk_score",
        "fundamental_data_quality",
    } <= feature_columns
    assert {
        "fundamental_data_quality",
        "revenue_score",
        "quality_score",
        "valuation_or_growth_score",
        "news_risk_score",
        "fundamental_summary_json",
            "news_summary_json",
            "risk_adjusted_score",
            "risk_quality_score",
            "risk_quality_label",
            "risk_quality_reason",
        } <= radar_columns
    assert {
        "risk_adjusted_score",
        "risk_quality_score",
        "risk_quality_label",
        "risk_quality_reason",
    } <= watch_pool_columns
