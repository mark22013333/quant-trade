from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from web.services.shioaji_gateway import ProductionPermissionError
from web.services.shioaji_workflow import OrderTestConfig, ShioajiWorkflowService


class DummyGateway:
    def serialize(self, obj):
        return obj

    def pick_stock_account(self, _api, _accounts=None):
        return {"account": "stock"}

    def pick_futures_account(self, _api, _accounts=None):
        return {"account": "futures"}

    def call_with_timeout(self, func, _timeout, *args, **kwargs):
        return func(*args, **kwargs)

    def as_dict(self, obj):
        if isinstance(obj, dict):
            return obj
        return {}


def _build_service() -> ShioajiWorkflowService:
    return ShioajiWorkflowService(gateway=DummyGateway())


def test_stock_order_test_blocks_live_when_not_allowed():
    service = _build_service()
    config = OrderTestConfig(simulation=False, allow_live_order=False)

    result = service.run_stock_order_test(config)

    assert result["passed"] is False
    assert result["error"] == "live_order_locked"


def test_simulation_suite_aggregates_step_results(monkeypatch):
    service = _build_service()
    context = SimpleNamespace(
        accounts=[{"id": "A1"}],
        production_permission=True,
        ca_activated=False,
        env_status={},
        shioaji_version="1.2.0",
    )

    @contextmanager
    def fake_session(*, simulation, activate_ca):  # noqa: ARG001
        yield object(), context

    monkeypatch.setattr(service, "_session", fake_session)
    monkeypatch.setattr(
        service,
        "_execute_stock_order",
        lambda **kwargs: {"passed": True, "message": "stock ok", "status": "Filled"},
    )
    monkeypatch.setattr(
        service,
        "_execute_futures_order",
        lambda **kwargs: {"passed": False, "message": "futures failed", "status": "Failed"},
    )

    result = service.run_simulation_suite(OrderTestConfig(simulation=True))

    assert result["passed"] is False
    assert result["checks"]["login"]["passed"] is True
    assert result["checks"]["stock_order"]["passed"] is True
    assert result["checks"]["futures_order"]["passed"] is False


def test_verify_production_ready_success(monkeypatch):
    service = _build_service()
    account = SimpleNamespace(account_id="123", account_type="stock", signed=True)
    context = SimpleNamespace(
        accounts=[account],
        production_permission=True,
        ca_activated=True,
        env_status={"SHIOAJI_CA_PATH": True, "SHIOAJI_CA_PASSWORD": True},
        shioaji_version="1.2.0",
    )

    @contextmanager
    def fake_session(*, simulation, activate_ca):  # noqa: ARG001
        yield object(), context

    monkeypatch.setattr(service, "_session", fake_session)

    result = service.verify_production_ready()

    assert result["passed"] is True
    assert result["signed_count"] == 1
    assert result["production_permission"] is True
    assert result["ca_configured"] is True


def test_verify_production_handles_permission_error(monkeypatch):
    service = _build_service()

    @contextmanager
    def fake_session(*, simulation, activate_ca):  # noqa: ARG001
        raise ProductionPermissionError("token missing prod permission")
        yield

    monkeypatch.setattr(service, "_session", fake_session)

    result = service.verify_production_ready()

    assert result["passed"] is False
    assert result["error"] == "production_permission"
