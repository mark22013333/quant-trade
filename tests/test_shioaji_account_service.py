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

        def account_balance(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(acc_balance=5000.0, account_id="1234567890")

        def list_positions(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return [SimpleNamespace(code="2330", quantity=3, price=100.0, account_id="1234567890")]

        def settlements(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return [SimpleNamespace(T=2, amount=-1000.0, token="secret-token-value")]

        def list_trades(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return [SimpleNamespace(id="O1", status="Submitted", order=SimpleNamespace(action="Buy", price=100, quantity=1))]

    service = ShioajiAccountService(api_factory=lambda simulation: Api())

    payload = service.get_account_snapshot(simulation=True).to_dict()

    assert payload["available_cash"] == 5000.0
    assert payload["stock_account"]["account_id"] != "1234567890"
    assert payload["positions"][0]["raw"]["account_id"] != "1234567890"
    assert payload["settlements"][0]["raw"]["token"] != "secret-token-value"
