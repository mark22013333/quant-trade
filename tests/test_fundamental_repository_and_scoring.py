from __future__ import annotations

from datetime import date, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repository import TradingRepository
from app.db.session import install_sqlite_protection
from app.db.swing_repository import SwingRepository
from app.services.scoring import _fundamental_scores, score_symbol


def _build_repo() -> tuple[TradingRepository, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    install_sqlite_protection(engine)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    return TradingRepository(session), session


def _seed_bars(repo: TradingRepository, symbol: str = "2330") -> None:
    start = date(2024, 1, 1)
    rows = []
    for idx in range(75):
        close = 100 + idx * 0.1
        rows.append(
            {
                "date": start + timedelta(days=idx),
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": 2000,
            }
        )
    repo.upsert_daily_bars(symbol, rows)


def test_fundamental_upserts_are_idempotent_and_query_by_announce_date() -> None:
    repo, session = _build_repo()
    repo.upsert_monthly_revenues(
        "2330",
        [
            {"period": "2024-01", "announce_date": date(2024, 2, 10), "revenue": 100, "revenue_yoy_pct": 10},
            {"period": "2024-01", "announce_date": date(2024, 2, 10), "revenue": 120, "revenue_yoy_pct": 20},
        ],
    )
    repo.upsert_financial_statement_summaries(
        "2330",
        [
            {"period": "2024-Q1", "announce_date": date(2024, 5, 15), "eps": 2.0, "roe_pct": 18.0},
            {"period": "2024-Q1", "announce_date": date(2024, 5, 15), "eps": 2.5, "roe_pct": 20.0},
        ],
    )
    repo.upsert_news_events(
        "2330",
        [
            {"date": date(2024, 5, 20), "title": "創高", "risk_tags": ["positive_event"]},
            {"date": date(2024, 5, 20), "title": "創高", "risk_tags": ["positive_event"]},
        ],
    )
    session.commit()

    revenue = repo.get_latest_monthly_revenue("2330", on_or_before=date(2024, 5, 1))
    future_financial = repo.get_latest_financial_summary("2330", on_or_before=date(2024, 5, 1))
    financial = repo.get_latest_financial_summary("2330", on_or_before=date(2024, 5, 16))
    news = repo.list_recent_news_events("2330", on_or_before=date(2024, 5, 21))

    assert revenue is not None
    assert revenue.revenue == 120.0
    assert future_financial is None
    assert financial is not None
    assert financial.eps == 2.5
    assert len(news) == 1


def test_score_symbol_uses_only_announced_fundamentals() -> None:
    repo, session = _build_repo()
    _seed_bars(repo)
    session.commit()
    engine = session.get_bind()
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261016_0015_swing_research.py"
    spec = spec_from_file_location("fundamental_migration_test", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration._check_sqlite(connection)
        for statement in migration._statements(migration._sql(rollback=False)):
            connection.exec_driver_sql(statement)
    sources = SwingRepository(session)

    def revenue(period, amount, public_at, **overrides):
        return sources.append_source({"source": "official", "kind": "monthly_revenue", "source_key": "2330:"+period,
            "symbol": "2330", "data_period": period, "public_at": public_at, "obtained_at": public_at,
            "verified_at": public_at, "quality": "verified", "payload": {"period": period, "revenue": amount}, **overrides})

    revenue("2023-01", 50, "2023-02-10T10:00:00+08:00")
    original = revenue("2024-01", 100, "2024-02-10T10:00:00+08:00")
    revenue("2024-03", 1000, "2024-04-10T10:00:00+08:00")
    revenue("2024-01", 9999, "2024-02-10T10:00:00+08:00", supersedes_id=original["id"],
        revised_at="2024-04-12T10:00:00+08:00", obtained_at="2024-04-12T10:01:00+08:00", verified_at="2024-04-12T10:01:00+08:00")
    sources.append_source({"source": "official", "kind": "financial_summary", "source_key": "2330:2024-Q1", "symbol": "2330",
        "data_period": "2024-Q1", "public_at": "2024-05-15T10:00:00+08:00", "obtained_at": "2024-05-15T10:00:00+08:00",
        "verified_at": "2024-05-15T10:00:00+08:00", "quality": "verified", "payload": {"eps": 9.9, "roe_pct": 50}})
    session.commit()

    def fake_signal(_df):  # noqa: ANN001
        return {
            "entry": True,
            "trend_ok": True,
            "vol_ok": True,
            "trigger": True,
            "close": 110.0,
            "ma60": 100.0,
            "volume": 3000.0,
            "volume_ma5": 1500.0,
            "score": 0.8,
            "risk_score": 0.7,
        }

    unpublished = _fundamental_scores(repo, symbol="2330", trade_date=date(2023, 2, 9))
    before = score_symbol(repo, symbol="2330", trade_date=date(2024, 3, 31), signal_evaluator=fake_signal)
    after = score_symbol(repo, symbol="2330", trade_date=date(2024, 4, 11), signal_evaluator=fake_signal)

    assert before is not None
    assert after is not None
    assert unpublished["fundamental_summary"]["revenue"]["available"] is False
    assert unpublished["revenue_score"] == 0.5
    assert before.fundamental_summary["revenue"]["period"] == "2024-01"
    assert before.fundamental_summary["revenue"]["revenue_yoy_pct"] == 100.0
    assert after.fundamental_summary["revenue"]["period"] == "2024-03"
    assert before.fundamental_summary["financial"]["available"] is False
