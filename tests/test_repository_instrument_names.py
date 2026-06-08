from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data.sync_service import SyncService
from app.db.models import Base, Instrument
from app.db.repository import TradingRepository


def _build_repo() -> tuple[TradingRepository, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    return TradingRepository(session), session


def test_upsert_instruments_profile_writes_name_and_preserves_0050_flag() -> None:
    repo, session = _build_repo()
    repo.upsert_instruments(["2330"], market="TW", is_0050=True)
    repo.upsert_instruments(
        [
            {
                "symbol": "2330",
                "name": "台積電",
                "market": "TWSE",
                "is_0050": False,
            }
        ],
        market="TWSE",
        is_0050=False,
    )
    session.commit()

    row = session.get(Instrument, "2330")
    assert row is not None
    assert row.name == "台積電"
    assert row.market == "TWSE"
    assert row.is_0050 is True


def test_sync_twse_tpex_universe_persists_symbol_names() -> None:
    class _StubFinMind:
        def fetch_stock_info(self) -> list[dict]:
            return [
                {"symbol": "2330", "name": "台積電", "market": "TWSE"},
                {"symbol": "6443", "name": "元晶", "market": "TPEX"},
            ]

    repo, session = _build_repo()
    service = SyncService(repo=repo, finmind=_StubFinMind())
    result = service.sync_twse_tpex_universe()
    session.commit()

    assert result["symbols_total"] == 2
    assert repo.get_instrument_name("2330") == "台積電"
    assert repo.get_instrument_name("6443") == "元晶"
