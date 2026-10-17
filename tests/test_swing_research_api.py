"""研究 API 邊界：隔離資料庫、人工確認、設定雜湊與錯誤清理。"""
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.session as db_session
from web.control_panel_app import app


def config():
    return {
        "experiment_key": "api-test", "mode": "historical_exploratory", "etf_symbol": "0050",
        "stock_pool": ["2330"], "start_date": "2026-07-27", "end_date": "2026-07-31",
        "initial_cash_e4": 100000000, "stop_equity_e4": 80000000,
        "strategy": {"version": "swing-v1", "hold_days": 20, "parameters": {}, "stop_loss_pct": "0.10"},
        "risk": {"max_position_e4": 50000000, "max_total_exposure_e4": 90000000, "max_single_risk_e4": 10000000},
        "fees": {p: {"commission_rate": "0.001425", "minimum_fee_e4": 10000, "sell_tax_rate": "0.003"} for p in ("stock", "etf")},
        "execution": {"slippage_bps": "5", "settlement_sessions": 2, "price_basis": "raw", "corporate_actions": "cash_and_split", "odd_lot_model": "daily_proxy", "rounding": "ROUND_HALF_UP"},
        "data_policy": {"warmup_bars": 1, "missing_required": "block"},
        "observation_sessions": 20, "code_version": "api-test-v1",
    }


@pytest.fixture
def api(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def constraints(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=ON")

    with engine.connect() as connection:
        connection.connection.driver_connection.executescript(
            (Path(__file__).parents[1] / ".spec/tw-swing-ai-validation/deploy.sql").read_text())
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
    monkeypatch.setattr(db_session, "init_db", lambda: None)
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def create(client):
    response = client.post("/api/swing/experiments", json={"config": config(), "confirmed_by": "測試使用者", "confirmation_note": "確認研究設定"})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_preview_confirmation_and_shared_idempotency(api):
    client, factory = api
    preview = client.post("/api/swing/experiments/preview", json={"config": config()})
    assert preview.status_code == 200
    assert preview.json()["data"]["missing_fields"] == []
    missing = client.post("/api/swing/experiments", json={"config": config()})
    assert missing.status_code == 422
    assert set(missing.json()["detail"]["missing_fields"]) == {"confirmed_by", "confirmation_note"}
    first, replay = create(client), create(client)
    assert first["experiment_id"] == replay["experiment_id"]
    assert len(first["accounts"]) == 3
    with factory() as session:
        assert session.execute(text("SELECT COUNT(*) FROM swing_paper_accounts")).scalar_one() == 3
    changed = deepcopy(config())
    changed["strategy"]["hold_days"] = 30
    conflict = client.post("/api/swing/experiments", json={"config": changed, "confirmed_by": "使用者", "confirmation_note": "確認"})
    assert conflict.status_code == 409


def test_missing_costs_and_forged_inputs_rejected(api):
    client, _ = api
    incomplete = config()
    del incomplete["fees"]
    response = client.post("/api/swing/experiments/preview", json={"config": incomplete})
    assert response.status_code == 422
    assert "fees" in response.json()["detail"]["missing_fields"]
    experiment = create(client)
    path = f'/api/swing/experiments/{experiment["experiment_id"]}/run'
    response = client.post(path, json={"config_hash": experiment["config_hash"], "run_key": "forged", "cutoff_at": "2026-07-27T15:00:00+08:00", "inputs": {"verified": True}})
    assert response.status_code == 422
    response = client.post(path, json={"config_hash": "0" * 64, "run_key": "wrong-config", "cutoff_at": "2026-07-27T15:00:00+08:00"})
    assert response.status_code == 409


def test_security_and_coverage(api, monkeypatch):
    client, _ = api
    experiment = create(client)
    response = client.get(f'/api/swing/experiments/{experiment["experiment_id"]}/preflight')
    assert response.status_code == 200
    assert response.json()["data"]["qualified"] is False
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "local-test-token")
    assert client.get("/api/swing/experiments").status_code == 401
    assert client.get("/api/swing/experiments", headers={"Authorization": "Bearer local-test-token"}).status_code == 200


def test_undeployed_schema_does_not_expose_sql(api, monkeypatch):
    client, _ = api
    empty = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_session, "get_session_factory", lambda: sessionmaker(bind=empty))
    response = client.get("/api/swing/experiments")
    assert response.status_code == 503
    assert response.json()["error"] == "swing_schema_not_migrated"
    assert "SELECT" not in response.text and "no such table" not in response.text
    empty.dispose()


def test_cli_requires_explicit_inputs():
    from app.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["swing-research", "run", "--experiment-id", "1", "--config-hash", "a" * 64, "--run-key", "close-1", "--cutoff-at", "2026-07-27T15:00:00+08:00"])
    assert args.swing_action == "run"
    with pytest.raises(SystemExit):
        parser.parse_args(["swing-research", "create", "--config", "config.json"])
