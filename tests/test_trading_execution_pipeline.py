from __future__ import annotations

from types import SimpleNamespace

from app.execution import OrderIntent, TradingExecutionService
from broker.shioaji_broker import ShioajiBroker
from live_trading.trader import LiveTrader


class DummyExecutionService:
    def __init__(self):
        self.intents = []

    def execute_intent(self, intent):
        self.intents.append(intent)
        return SimpleNamespace(executed=True, pretrade=SimpleNamespace(reason="ok"))


def test_order_intent_normalizes_symbol_and_redacts_metadata():
    intent = OrderIntent(
        source="web",
        environment="simulation",
        symbol="2330.TW",
        side="buy",
        price=100,
        metadata={"token": "secret-token-value"},
    )

    payload = intent.to_dict()

    assert payload["symbol"] == "2330"
    assert payload["metadata"]["token"] != "secret-token-value"


def test_legacy_shioaji_broker_delegates_to_execution_service():
    service = DummyExecutionService()
    broker = ShioajiBroker(simulation=True, api=SimpleNamespace())
    broker._connected = True
    broker.execution_service = service

    ok = broker.place_order(None, 100.0, 1, "buy", "2330.TW")

    assert ok is True
    assert len(service.intents) == 1
    assert service.intents[0].symbol == "2330"
    assert service.intents[0].source == "live_trader"


def test_live_trader_delegates_orders_to_execution_service():
    class Strategy:
        def generate_signals(self, data):
            return data.assign(position=1)

    class Broker:
        simulation = True

        def __init__(self):
            self.place_order_called = False

        def get_position(self, symbol):
            return 0

        def get_balance(self):
            return 5000

        def place_order(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.place_order_called = True
            return True

    class Provider:
        pass

    import pandas as pd

    service = DummyExecutionService()
    broker = Broker()
    trader = LiveTrader(
        strategy=Strategy(),
        broker=broker,
        data_provider=Provider(),
        symbols=["2330"],
        execution_service=service,
    )
    trader.market_data = {
        "2330": pd.DataFrame(
            [{"Close": 100.0, "Open": 100.0, "High": 101.0, "Low": 99.0, "Volume": 1000}]
        )
    }
    trader._generate_signals()
    trader._execute_trades()

    assert broker.place_order_called is False
    assert len(service.intents) == 1
    assert service.intents[0].source == "live_trader"


def test_trading_execution_service_rejects_missing_gateway():
    intent = OrderIntent(source="test", environment="simulation", symbol="2330", side="buy", price=100)
    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "execution_gateway_missing"
