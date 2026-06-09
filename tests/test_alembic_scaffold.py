from __future__ import annotations

import ast
from pathlib import Path

from app.db.models import Base
from app.db.schema import ALEMBIC_HEAD_REVISION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INITIAL_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260605_0001_initial_schema.py"
TRADING_EXECUTION_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260608_0002_trading_execution_records.py"
HEAD_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260608_0003_trading_audit_records.py"
MARKET_FUNDAMENTALS_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260608_0004_market_fundamentals_news.py"
TRADING_ADVISOR_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260609_0005_trading_advisor.py"
ORDER_PREVIEW_INTENT_REVISION_PATH = PROJECT_ROOT / "alembic" / "versions" / "20260609_0006_order_preview_intent_json.py"


def _revision_assignments() -> dict[str, object]:
    tree = ast.parse(ORDER_PREVIEW_INTENT_REVISION_PATH.read_text(encoding="utf-8"))
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
    assert values["down_revision"] == "20260609_0005"


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
