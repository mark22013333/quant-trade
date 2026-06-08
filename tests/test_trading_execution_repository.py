from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, TradingExecutionRecord
from app.db.repository import TradingRepository


def test_repository_persists_trading_execution_record():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as session:
        repo = TradingRepository(session)
        repo.add_trading_execution_record(
            intent={
                "intent_id": "intent-1",
                "source": "test",
                "environment": "simulation",
                "symbol": "2330",
                "side": "buy",
                "price": 100.0,
            },
            result={
                "intent_id": "intent-1",
                "environment": "simulation",
                "symbol": "2330",
                "side": "buy",
                "price": 100.0,
                "quantity": 10,
                "accepted": True,
                "executed": True,
                "status": "submitted",
                "pretrade": {"reason": "ok"},
            },
        )
        session.commit()

        row = session.query(TradingExecutionRecord).filter_by(intent_id="intent-1").one()

    assert row.symbol == "2330"
    assert row.accepted is True
    assert row.executed is True
    assert row.status == "submitted"
