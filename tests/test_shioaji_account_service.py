from __future__ import annotations

from types import SimpleNamespace

from app.broker.shioaji_account_service import ShioajiAccountService


def test_shioaji_health_check_missing_key_returns_safe_status(monkeypatch, tmp_path):
    monkeypatch.delenv("SHIOAJI_APIKEY", raising=False)
    monkeypatch.delenv("SHIOAJI_SECRET", raising=False)

    result = ShioajiAccountService(env_path=str(tmp_path / "missing.env")).run_health_check(simulation=True)

    assert result.query_ready is False
    assert result.simulation_order_ready is False
    assert any(item["name"] == "api_key" and item["passed"] is False for item in result.checks)


def test_account_snapshot_redacts_sensitive_fields(monkeypatch):
    monkeypatch.setenv("SHIOAJI_APIKEY", "key")
    monkeypatch.setenv("SHIOAJI_SECRET", "secret")

    class Api:
        shioaji_version = "test"

        def __init__(self):
            self.stock_account = SimpleNamespace(account_id="1234567890", token="secret-token-value")
            self.Unit = SimpleNamespace(Share="Share")
            self.position_units = []

        def account_balance(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(acc_balance=5000.0, account_id="1234567890")

        def list_positions(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.position_units.append(kwargs.get("unit"))
            return [SimpleNamespace(code="2330", quantity=3000, yd_quantity=2000, price=100.0, pnl=-120.5, account_id="1234567890")]

        def list_profit_loss_summary(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(total_pnl=50.0, account_id="1234567890")

        def settlements(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return [SimpleNamespace(T=2, amount=-1000.0, token="secret-token-value")]

        def list_trades(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return [SimpleNamespace(id="O1", status="Submitted", order=SimpleNamespace(action="Buy", price=100, quantity=1))]

    service = ShioajiAccountService(api_factory=lambda simulation: Api())

    payload = service.get_account_snapshot(simulation=True).to_dict()

    assert payload["available_cash"] == 5000.0
    assert payload["unrealized_pnl"] == -120.5
    assert payload["realized_pnl"] == 50.0
    assert payload["daily_pnl"] == -70.5
    assert payload["pnl_source"] == "realized_today_plus_unrealized_positions"
    assert payload["stock_account"]["account_id"] != "1234567890"
    assert payload["positions"][0]["quantity"] == 3000
    assert payload["positions"][0]["sellable_quantity"] == 2000
    assert payload["positions"][0]["yesterday_quantity"] == 2000
    assert payload["positions"][0]["sellable_source"] == "yd_quantity"
    assert payload["positions"][0]["raw"]["account_id"] != "1234567890"
    assert payload["positions"][0]["raw"]["_query_unit"] == "Share"
    assert payload["positions"][0]["pnl"] == -120.5
    assert payload["settlements"][0]["raw"]["token"] != "secret-token-value"


def test_position_parser_converts_common_lots_to_shares():
    position = ShioajiAccountService._position_from_raw(
        SimpleNamespace(code="2317", quantity=2, yd_quantity=1, unit="Common", price=100.0),
        query_unit="Common",
    )

    assert position.quantity == 2000
    assert position.sellable_quantity == 1000
    assert position.yesterday_quantity == 1000
    assert position.quantity_unit == "shares"
    assert position.sellable_source == "yd_quantity"


def test_account_snapshot_does_not_activate_ca_for_read_only_queries(monkeypatch):
    monkeypatch.setenv("SHIOAJI_APIKEY", "key")
    monkeypatch.setenv("SHIOAJI_SECRET", "secret")
    login_calls = []

    class Api:
        stock_account = SimpleNamespace(account_id="1234567890")
        Unit = SimpleNamespace(Share="Share")

        def account_balance(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(acc_balance=0.0)

        def list_positions(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return []

        def list_profit_loss_summary(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(total_pnl=0.0)

        def settlements(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return []

        def list_trades(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return []

    class Service(ShioajiAccountService):
        def _login(self, *, simulation: bool, activate_ca: bool = True):
            login_calls.append((simulation, activate_ca))
            return Api(), "test"

    Service().get_account_snapshot(simulation=False)

    assert login_calls == [(False, False)]
