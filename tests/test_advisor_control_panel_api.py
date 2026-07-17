from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from web.control_panel_app import app


def _seed_small_live_readiness(strategy_name: str = "advisor:stub", strategy_version: str = "v1") -> None:
    from sqlalchemy import delete

    from app.db.models import AdvisorObservationRecord, PromotionGateRecord
    from app.db.repository import TradingRepository
    from app.db.session import get_session_factory, init_db
    from app.execution.promotion_gate import PromotionGate

    init_db()
    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            delete(AdvisorObservationRecord).where(AdvisorObservationRecord.advisor_name == "stub")
        )
        session.execute(
            delete(PromotionGateRecord).where(
                PromotionGateRecord.strategy_name == strategy_name,
                PromotionGateRecord.strategy_version == strategy_version,
            )
        )
        repo = TradingRepository(session)
        for idx, day in enumerate(["2026-06-10", "2026-06-11", "2026-06-12"], start=1):
            repo.add_advisor_observation_record(
                record={
                    "observation_date": day,
                    "symbol": f"233{idx}",
                    "advisor_name": "stub",
                    "advisor_version": strategy_version,
                    "preview_created": True,
                    "human_action": "confirmed",
                    "simulation_result": "submitted",
                    "data_quality": "fresh",
                    "passed": True,
                }
            )
        PromotionGate(repository=repo).evaluate(
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            paper_days=20,
            paper_trades=6,
            max_drawdown=0.02,
            slippage_report={"avg_slippage_bps": 3},
            reconciliation_matched=True,
            single_order_value=5_000,
            daily_order_value=10_000,
            daily_order_count=1,
        )
        session.commit()


def test_advisor_proposal_endpoint_returns_decision(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/advisor/proposals",
        json={"symbol": "2330", "trade_date": "2026-01-01", "available_cash": 10000, "create_preview": False},
    )

    assert response.status_code == 200
    assert response.json()["data"]["decision"]["request"]["symbol"] == "2330"


def test_order_approve_execute_requires_manual_and_promotion(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": "missing-preview", "manual_confirmed": False, "promotion_gate_accepted": False},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "manual_confirmation_required"


def test_promotion_readiness_endpoint_returns_machine_readable_status(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/tw-live/promotion-readiness",
        json={
            "advisor_name": "stub",
            "strategy_name": "manual",
            "strategy_version": "manual",
            "target_phase": "paper",
            "min_observation_days": 3,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["target_phase"] == "paper"
    assert "readiness" in data
    assert "accepted" in data
    assert isinstance(data["blocking_reasons"], list)
    assert "observation_summary" in data


def test_order_approve_execute_blocks_live_when_readiness_not_accepted(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "buy",
            "price": 100,
            "reference_price": 100,
            "quantity": 1,
            "available_cash": 10000,
            "position_before": 0,
            "strategy_name": "advisor:stub",
            "strategy_version": "readiness-blocked-test",
            "signal_id": "S1",
        },
    )
    preview_id = preview_response.json()["data"]["preview_id"]

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": preview_id, "manual_confirmed": True, "promotion_gate_accepted": True, "simulation": False},
    )

    assert response.status_code == 200
    payload = response.json()
    result = payload["data"]
    assert result["accepted"] is False
    assert result["executed"] is False
    assert result["pretrade"]["reason"] == "promotion_readiness_blocked"
    assert result["pretrade"]["checks"][0]["name"] == "promotion_readiness"
    assert payload["readiness"]["accepted"] is False


def test_order_approve_execute_rejects_preview_environment_mismatch(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "buy",
            "price": 100,
            "quantity": 1,
            "available_cash": 10000,
            "position_before": 0,
            "strategy_name": "advisor:stub",
            "strategy_version": "v1",
            "signal_id": "S1",
        },
    )
    preview_id = preview_response.json()["data"]["preview_id"]

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": preview_id, "manual_confirmed": True, "promotion_gate_accepted": True, "simulation": True},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "preview_environment_mismatch"


def test_order_approve_execute_rejects_live_price_deviation_guard(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    _seed_small_live_readiness()
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "buy",
            "price": 105,
            "reference_price": 100,
            "quantity": 1,
            "available_cash": 10000,
            "position_before": 0,
            "strategy_name": "advisor:stub",
            "strategy_version": "v1",
            "signal_id": "S1",
        },
    )
    preview_id = preview_response.json()["data"]["preview_id"]

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": preview_id, "manual_confirmed": True, "promotion_gate_accepted": True, "simulation": False},
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["accepted"] is False
    assert result["executed"] is False
    assert result["pretrade"]["reason"] == "price_deviation_guard"


def test_tw_live_order_preview_uses_order_lot_price_guard_defaults(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    _seed_small_live_readiness()
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "buy",
            "order_lot": "Common",
            "price": 101,
            "reference_price": 100,
            "quantity": 1,
            "available_cash": 10000,
            "position_before": 0,
            "strategy_name": "advisor:stub",
            "strategy_version": "v1",
            "signal_id": "S1",
        },
    )

    assert preview_response.status_code == 200
    preview = preview_response.json()["data"]
    assert preview["order_lot"] == "Common"

    approve_response = client.post(
        "/api/tw-live/order-approve-execute",
        json={
            "preview_id": preview["preview_id"],
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "simulation": False,
        },
    )

    assert approve_response.status_code == 200
    result = approve_response.json()["data"]
    assert result["accepted"] is False
    assert result["pretrade"]["reason"] == "price_deviation_guard"
    assert result["pretrade"]["checks"][0]["max_ticks"] == 2
    assert result["pretrade"]["checks"][0]["max_pct"] == 0.005


def test_order_approve_execute_rejects_live_sell_more_than_position(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    _seed_small_live_readiness()
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "sell",
            "price": 100,
            "reference_price": 100,
            "quantity": 3,
            "available_cash": 10000,
            "position_before": 2,
            "strategy_name": "advisor:stub",
            "strategy_version": "v1",
            "signal_id": "S1",
        },
    )
    preview_id = preview_response.json()["data"]["preview_id"]

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": preview_id, "manual_confirmed": True, "promotion_gate_accepted": True, "simulation": False},
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["accepted"] is False
    assert result["executed"] is False
    assert result["pretrade"]["reason"] == "position_quantity_for_sell"


def test_order_approve_execute_rejects_live_sell_more_than_sellable_quantity(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    _seed_small_live_readiness()
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "sell",
            "price": 100,
            "reference_price": 100,
            "quantity": 3,
            "available_cash": 10000,
            "position_before": 5,
            "sellable_quantity": 2,
            "strategy_name": "advisor:stub",
            "strategy_version": "v1",
            "signal_id": "S1",
        },
    )
    preview_id = preview_response.json()["data"]["preview_id"]

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": preview_id, "manual_confirmed": True, "promotion_gate_accepted": True, "simulation": False},
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["accepted"] is False
    assert result["executed"] is False
    assert result["pretrade"]["reason"] == "sellable_quantity_for_sell"
    assert result["pretrade"]["checks"][0]["sellable_quantity"] == 2


def test_order_approve_execute_rejects_live_disposition_preview(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    _seed_small_live_readiness()
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "buy",
            "price": 100,
            "reference_price": 100,
            "quantity": 1,
            "available_cash": 10000,
            "position_before": 0,
            "disposition_active": True,
            "strategy_name": "advisor:stub",
            "strategy_version": "v1",
            "signal_id": "S1",
        },
    )
    preview_id = preview_response.json()["data"]["preview_id"]

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": preview_id, "manual_confirmed": True, "promotion_gate_accepted": True, "simulation": False},
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["accepted"] is False
    assert result["executed"] is False
    assert result["pretrade"]["reason"] == "disposition_attention_guard"


def test_order_approve_execute_rejects_live_attention_preview(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    _seed_small_live_readiness()
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "buy",
            "price": 100,
            "reference_price": 100,
            "quantity": 1,
            "available_cash": 10000,
            "position_before": 0,
            "attention_active": True,
            "strategy_name": "advisor:stub",
            "strategy_version": "v1",
            "signal_id": "S1",
        },
    )
    preview_id = preview_response.json()["data"]["preview_id"]

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": preview_id, "manual_confirmed": True, "promotion_gate_accepted": True, "simulation": False},
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["accepted"] is False
    assert result["executed"] is False
    assert result["pretrade"]["reason"] == "disposition_attention_guard"
    assert result["pretrade"]["checks"][0]["attention_active"] is True


def test_order_approve_execute_rejects_live_buy_at_limit_up(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    _seed_small_live_readiness()
    client = TestClient(app)

    preview_response = client.post(
        "/api/tw-live/order-preview",
        json={
            "simulation": False,
            "symbol": "2330",
            "side": "buy",
            "price": 110,
            "reference_price": 100,
            "limit_up_price": 110,
            "limit_down_price": 90,
            "quantity": 1,
            "available_cash": 10000,
            "position_before": 0,
            "strategy_name": "advisor:stub",
            "strategy_version": "v1",
            "signal_id": "S1",
        },
    )
    preview_id = preview_response.json()["data"]["preview_id"]

    response = client.post(
        "/api/tw-live/order-approve-execute",
        json={"preview_id": preview_id, "manual_confirmed": True, "promotion_gate_accepted": True, "simulation": False},
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["accepted"] is False
    assert result["executed"] is False
    assert result["pretrade"]["reason"] == "limit_up_buy_blocked"


def test_tw_live_reference_price_endpoint_returns_resolved_price(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")

    class _Api:
        def __init__(self):
            self.logged_out = False

        def logout(self):
            self.logged_out = True

    class _Gateway:
        def __init__(self, config):
            self.config = config
            self.api = _Api()

        def resolve_order_price(self, symbol):
            assert symbol == "2330"
            assert self.config.simulation is True
            return 101.5

    monkeypatch.setattr("app.broker.shioaji_gateway.ShioajiGateway", _Gateway)
    client = TestClient(app)

    response = client.get("/api/tw-live/reference-price?symbol=2330.TW&simulation=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["symbol"] == "2330"
    assert payload["data"]["reference_price"] == 101.5


def test_tw_live_reference_price_endpoint_returns_limit_prices(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")

    class _Api:
        def logout(self):
            pass

    class _Gateway:
        def __init__(self, config):
            self.config = config
            self.api = _Api()

        def get_stock_price_reference(self, symbol):
            assert symbol == "2330"
            return {
                "symbol": "2330",
                "reference_price": 100.0,
                "limit_up_price": 110.0,
                "limit_down_price": 90.0,
            }

    monkeypatch.setattr("app.broker.shioaji_gateway.ShioajiGateway", _Gateway)
    client = TestClient(app)

    response = client.get("/api/tw-live/reference-price?symbol=2330.TW&simulation=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["reference_price"] == 100.0
    assert payload["data"]["limit_up_price"] == 110.0
    assert payload["data"]["limit_down_price"] == 90.0


def test_advisor_backtest_endpoint_handles_missing_data(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/advisor/backtest",
        json={
            "symbol": "NOPE",
            "start_date": "2026-01-01",
            "end_date": "2026-01-10",
            "initial_cash": 10000,
            "max_days": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["passed"] is False


def test_advisor_backtest_export_endpoint_returns_report_urls(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/advisor/backtest/export",
        json={
            "symbol": "NOPE",
            "start_date": "2026-01-01",
            "end_date": "2026-01-10",
            "initial_cash": 10000,
            "max_days": 5,
        },
    )

    assert response.status_code == 200
    urls = response.json()["data"]["export"]["urls"]
    assert urls["summary_json"].startswith("/reports/")
    assert urls["html_report"].endswith(".html")


def test_advisor_proposal_endpoint_codex_provider_fails_closed(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_ADVISOR_ENABLED", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    client = TestClient(app)

    response = client.post(
        "/api/advisor/proposals",
        json={
            "symbol": "2330",
            "trade_date": "2026-01-01",
            "available_cash": 10000,
            "create_preview": False,
            "advisor_provider": "codex",
        },
    )

    assert response.status_code == 200
    decision = response.json()["data"]["decision"]
    assert decision["status"] == "rejected"
    assert decision["rejected_reason"] == "codex_advisor_disabled"
