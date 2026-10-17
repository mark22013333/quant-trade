from __future__ import annotations

from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.data.finmind_client import FinMindClient
from app.db.models import Base, MonthlyRevenue
from app.db.repository import TradingRepository
from app.db.session import install_sqlite_protection
from app.db.swing_repository import SwingRepository, content_hash, immediate_transaction, to_us
from app.services.scoring import _fundamental_scores

ROOT = Path(__file__).resolve().parents[1]


def _source(**overrides):
    return {"source": "official", "kind": "monthly_revenue", "source_key": "2330:2026-06", "symbol": "2330",
            "data_period": "2026-06", "public_at": "2026-07-10T10:00:00+08:00", "obtained_at": "2026-07-10T10:01:00+08:00",
            "verified_at": "2026-07-10T10:01:00+08:00", "quality": "verified", "payload": {"period": "2026-06", "revenue": 100}, **overrides}


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    install_sqlite_protection(engine)
    with engine.begin() as connection:
        spec = spec_from_file_location("swing_migration_test", ROOT / "alembic/versions/20261016_0015_swing_research.py")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        module._check_sqlite(connection)
        for statement in module._statements(module._sql(rollback=False)):
            connection.exec_driver_sql(statement)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_pit_revision_quarantine_release_and_retry(db):
    repo = SwingRepository(db)
    old = repo.append_source(_source())
    repeated = repo.append_source(_source(obtained_at="2026-07-11T10:01:00+08:00", verified_at="2026-07-11T10:01:00+08:00"))
    assert repeated["id"] == old["id"]
    new = repo.append_source(_source(revised_at="2026-07-28T09:00:00+08:00", obtained_at="2026-07-28T09:01:00+08:00",
                                     verified_at="2026-07-28T09:01:00+08:00", supersedes_id=old["id"], payload={"revenue": 200}))
    assert [r["id"] for r in repo.eligible_sources("monthly_revenue", "2330", "2026-07-27T15:00:00+08:00")] == [old["id"]]
    assert [r["id"] for r in repo.eligible_sources("monthly_revenue", "2330", "2026-07-29T15:00:00+08:00")] == [new["id"]]
    preview = repo.preview_repair(repair_key="q-preview", old_version_id=new["id"], action="quarantine", actor="Cheng", reason="錯誤原文")
    with pytest.raises(ValueError, match="before_hash"):
        repo.apply_repair(preview_id=preview["id"], repair_key="q", actor="Cheng", expected_hash="0"*64)
    applied = repo.apply_repair(preview_id=preview["id"], repair_key="q", actor="Cheng", expected_hash=new["payload_hash"])
    assert repo.apply_repair(preview_id=preview["id"], repair_key="q", actor="Cheng", expected_hash=new["payload_hash"])["id"] == applied["id"]
    assert not repo.eligible_sources("monthly_revenue", "2330", "2026-07-29T15:00:00+08:00")
    release = repo.preview_repair(repair_key="release-preview", old_version_id=new["id"], action="release", actor="Cheng", reason="重新核對")
    repo.apply_repair(preview_id=release["id"], repair_key="release", actor="Cheng", expected_hash=new["payload_hash"])
    assert repo.eligible_sources("monthly_revenue", "2330", "2026-07-29T15:00:00+08:00")[0]["id"] == new["id"]


def test_unknown_publication_and_late_obtained_are_ineligible(db):
    repo = SwingRepository(db)
    repo.append_source(_source(quality="unknown_publication", public_at=None, verified_at=None))
    assert not repo.eligible_sources("monthly_revenue", "2330", "2026-07-27T15:00:00+08:00")
    row = repo.append_source(_source(obtained_at="2026-07-28T10:00:00+08:00", verified_at="2026-07-28T10:01:00+08:00"))
    assert repo.eligible_sources("monthly_revenue", "2330", "2026-07-27T15:00:00+08:00")[0]["id"] == row["id"]
    assert not repo.eligible_sources("monthly_revenue", "2330", "2026-07-27T15:00:00+08:00", mode="prospective")
    for invalid in ("2026-07-10", "2026-07-10T10:00:00", date(2026, 7, 10)):
        with pytest.raises(ValueError):
            to_us(invalid)


def test_repair_creates_new_version_without_mutating_previous(db):
    repo = SwingRepository(db)
    old = repo.append_source(_source())
    replacement = _source(payload={"revenue": 125}, revised_at="2026-07-15T12:00:00+08:00")
    preview = repo.preview_repair(repair_key="fix-preview", old_version_id=old["id"], action="repair", actor="Cheng", replacement=replacement, reason="重算營收")
    result = repo.apply_repair(preview_id=preview["id"], repair_key="fix", actor="Cheng", expected_hash=old["payload_hash"])
    assert result["new_version_id"] != old["id"]
    assert repo.eligible_sources("monthly_revenue", "2330", "2026-07-27T15:00:00+08:00")[0]["payload"]["revenue"] == 125
    assert db.execute(text("SELECT payload_hash FROM swing_source_versions WHERE id=:id"), {"id": old["id"]}).scalar() == old["payload_hash"]


def test_source_truth_boundary_never_reads_legacy(db):
    Base.metadata.create_all(db.get_bind())
    db.add(MonthlyRevenue(symbol="2330", period="2026-06", announce_date=date(2026, 6, 1), revenue=100, revenue_yoy_pct=2026))
    db.flush()
    trading = TradingRepository(db)
    scores = _fundamental_scores(trading, symbol="2330", trade_date=date(2026, 7, 27))
    assert scores["fundamental_data_quality"] == "missing"
    findings = SwingRepository(db).preview_legacy_contamination()
    assert "year_misread_as_growth" in findings[0]["reasons"]
    result = SwingRepository(db).quarantine_legacy(findings[0], repair_key="legacy-q", actor="Cheng")
    assert result["stage"] == "applied"
    assert SwingRepository(db).quarantine_legacy(findings[0], repair_key="legacy-q", actor="Cheng")["id"] == result["id"]
    assert db.execute(text("SELECT revenue_yoy_pct FROM monthly_revenues")).scalar() == 2026


def test_missing_schema_is_explicit_without_legacy_fallback():
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        result = TradingRepository(session).get_point_in_time_fundamentals("2330", "2026-07-27T15:00:00+08:00")
        assert result["status"] == "schema_unavailable" and result["revenue"] is None
        with pytest.raises(ValueError, match="swing_schema_not_migrated"):
            SwingRepository(session).append_source(_source())


def test_growth_uses_only_same_cutoff_eligible_base_versions(db):
    repo = SwingRepository(db)
    base = repo.append_source(_source(source_key="2330:2025-06", data_period="2025-06",
        payload={"period": "2025-06", "revenue": 50}))
    repo.append_source(_source(payload={"period": "2026-06", "revenue": 100, "revenue_yoy_pct": 2026}))
    repo.append_source(_source(source_key="2330:2025-06", data_period="2025-06", supersedes_id=base["id"],
        revised_at="2026-07-28T09:00:00+08:00", payload={"period": "2025-06", "revenue": 80}))
    before = TradingRepository(db).get_point_in_time_fundamentals("2330", "2026-07-27T15:00:00+08:00")
    after = TradingRepository(db).get_point_in_time_fundamentals("2330", "2026-07-29T15:00:00+08:00")
    assert before["revenue"].revenue_yoy_pct == 100
    assert after["revenue"].revenue_yoy_pct == 25
    assert before["revenue"].revenue_mom_pct is None
    quarantine = repo.preview_repair(repair_key="base-preview", old_version_id=base["id"], action="quarantine", actor="Cheng", reason="錯誤基期")
    repo.apply_repair(preview_id=quarantine["id"], repair_key="base-q", actor="Cheng", expected_hash=base["payload_hash"])
    assert TradingRepository(db).get_point_in_time_fundamentals("2330", "2026-07-27T15:00:00+08:00")["revenue"].revenue_yoy_pct is None


def test_repair_conflict_rolls_back_only_its_own_writes(db):
    repo = SwingRepository(db)
    old = repo.append_source(_source())
    first = repo.preview_repair(repair_key="preview-1", old_version_id=old["id"], action="repair", actor="Cheng",
        replacement=_source(payload={"revenue": 125}, revised_at="2026-07-15T12:00:00+08:00"), reason="修正一")
    repo.apply_repair(preview_id=first["id"], repair_key="apply-shared", actor="Cheng", expected_hash=old["payload_hash"])
    second = repo.preview_repair(repair_key="preview-2", old_version_id=old["id"], action="repair", actor="Cheng",
        replacement=_source(payload={"revenue": 150}, revised_at="2026-07-16T12:00:00+08:00"), reason="修正二")
    before = db.execute(text("SELECT count(*) FROM swing_source_versions")).scalar()
    with pytest.raises(ValueError, match="idempotency_conflict"):
        repo.apply_repair(preview_id=second["id"], repair_key="apply-shared", actor="Cheng", expected_hash=old["payload_hash"])
    assert db.execute(text("SELECT count(*) FROM swing_source_versions")).scalar() == before
    assert db.execute(text("SELECT id FROM swing_data_repairs WHERE id=:id"), {"id": second["id"]}).scalar() == second["id"]


def test_parser_never_promotes_quarter_end_or_month_label(monkeypatch):
    client = FinMindClient(api_key="offline")
    monkeypatch.setattr(client, "_request_dataset", lambda **kwargs: [{"date": "2026-03-31", "type": "EPS", "value": 5}])
    financial = client.fetch_financial_statement_summary("2330", "2026-01-01", "2026-07-31")
    assert financial[0]["period"] == "2026-Q1"
    assert financial[0]["announce_date"] is None and financial[0]["public_at"] is None
    assert "2026-03-31" in financial[0]["raw_json"]


def test_immediate_transaction_preserves_callers_work(db):
    db.execute(text("SELECT 1"))
    assert not db.connection().connection.driver_connection.in_transaction
    with immediate_transaction(db):
        SwingRepository(db).append_source(_source())
    assert db.in_transaction()
    db.rollback()
    assert not db.execute(text("SELECT id FROM swing_source_versions")).first()


def test_protection_on_every_connection_and_populated_downgrade(monkeypatch, tmp_path):
    database = tmp_path / "upgrade.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database}")
    install_sqlite_protection(engine)
    for _ in range(2):
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA recursive_triggers").scalar() == 1
        engine.dispose()
    with Session(engine) as session:
        repo = SwingRepository(session)
        old = repo.append_source(_source())
        repo.append_source(_source(payload={"revenue": 150}, supersedes_id=old["id"], revised_at="2026-07-11T10:00:00+08:00"))
        preview = repo.preview_repair(repair_key="q-preview", old_version_id=old["id"], action="quarantine", actor="Cheng", reason="測試")
        repo.apply_repair(preview_id=preview["id"], repair_key="q", actor="Cheng", expected_hash=old["payload_hash"])
        session.commit()
    command.downgrade(config, "20260729_0014")
    assert not any(name.startswith("swing_") for name in inspect(engine).get_table_names())
    command.upgrade(config, "head")
    assert "swing_source_versions" in inspect(engine).get_table_names()
    engine.dispose()
