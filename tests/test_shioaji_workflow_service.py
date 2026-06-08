from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from web.services.shioaji_gateway import ProductionPermissionError
from web.services.shioaji_gateway import ShioajiGateway as WebShioajiGateway
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

    def get_stock_contract(self, api, stock_code):
        return api.Contracts.Stocks[str(stock_code)]

    def pick_reference_price(self, contract, fallback=10.0):
        return getattr(contract, "reference", fallback)


def _build_service() -> ShioajiWorkflowService:
    return ShioajiWorkflowService(gateway=DummyGateway())


def test_stock_order_test_blocks_live_when_not_allowed():
    service = _build_service()
    config = OrderTestConfig(simulation=False, allow_live_order=False)

    result = service.run_stock_order_test(config)

    assert result["passed"] is False
    assert result["error"] == "live_order_locked"


def test_stock_order_test_requires_backend_live_unlock(monkeypatch):
    monkeypatch.delenv("SHIOAJI_ENABLE_LIVE_ORDERS", raising=False)
    service = _build_service()
    config = OrderTestConfig(simulation=False, allow_live_order=True)

    result = service.run_stock_order_test(config)

    assert result["passed"] is False
    assert result["error"] == "live_order_env_locked"


def test_stock_order_test_rejects_live_nonce_mismatch(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    monkeypatch.setenv("SHIOAJI_LIVE_ORDER_NONCE", "confirm-live")
    service = _build_service()
    config = OrderTestConfig(simulation=False, allow_live_order=True, live_order_nonce="wrong-nonce")

    result = service.run_stock_order_test(config)

    assert result["passed"] is False
    assert result["error"] == "live_order_nonce_mismatch"


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
    service = ShioajiWorkflowService(gateway=WebShioajiGateway(Path(".env")))
    account = SimpleNamespace(account_id="1234567890", account_type="stock", signed=True)
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
    assert result["accounts"][0]["account_id"] != "1234567890"


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


def test_call_account_api_fallback_to_positional_argument():
    service = _build_service()
    token = object()

    class Api:
        @staticmethod
        def method(acc):
            return {"ok": acc}

    result = service._call_account_api(Api.method, token, allow_no_account=False)
    assert result["ok"] is token


def test_call_account_api_fallback_to_no_argument():
    service = _build_service()
    token = object()

    class Api:
        @staticmethod
        def method():
            return {"ok": True}

    result = service._call_account_api(Api.method, token, allow_no_account=True)
    assert result["ok"] is True


def test_web_gateway_serializer_reuses_core_redaction_contract():
    gateway = WebShioajiGateway(Path(".env"))

    result = gateway.serialize(
        {
            "account_id": "1234567890",
            "token": "secret-token-value",
            "nested": SimpleNamespace(ca_path="/Users/cheng/certs/prod.pfx"),
        }
    )

    assert result["account_id"] != "1234567890"
    assert result["token"] != "secret-token-value"
    assert result["nested"]["ca_path"] != "/Users/cheng/certs/prod.pfx"


def test_stock_order_execution_uses_order_intent_pipeline():
    service = _build_service()

    class Api:
        def __init__(self):
            self.stock_account = SimpleNamespace(account_id="STOCK-ACC")
            self.Contracts = SimpleNamespace(
                Stocks={"2330": SimpleNamespace(code="2330", reference=100.0, limit_down=90.0, limit_up=110.0)}
            )
            self.placed = None

        def account_balance(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(acc_balance=5000.0)

        def Order(self, **kwargs):
            return SimpleNamespace(**kwargs)

        def place_order(self, contract, order):
            self.placed = (contract, order)
            return SimpleNamespace(status=SimpleNamespace(status="Submitted"), token="secret-token-value")

    api = Api()

    result = service._execute_stock_order(
        api=api,
        stock_code="2330",
        quantity=1,
        order_price=100.0,
        simulation=True,
    )

    assert result["passed"] is True
    assert result["intent"]["source"] == "web"
    assert result["execution"]["executed"] is True
    assert result["execution"]["raw_trade"]["token"] != "secret-token-value"
    assert api.placed is not None
