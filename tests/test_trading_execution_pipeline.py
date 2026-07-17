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


def test_trading_execution_service_rejects_live_without_preview_gate():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=100,
        quantity=1,
        strategy_name="manual",
        signal_id="S1",
        metadata={"strategy_version": "v1"},
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "preview_required"


def test_trading_execution_service_rejects_live_price_deviation_before_gateway():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=105,
        quantity=1,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "reference_price": 100,
            "max_price_deviation_ticks": 3,
            "max_price_deviation_pct": 0.01,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "price_deviation_guard"
    assert result.pretrade.checks[0]["name"] == "price_deviation_guard"
    assert result.pretrade.checks[0]["passed"] is False
    assert result.pretrade.checks[0]["reference_price"] == 100.0


def test_trading_execution_service_validates_preview_before_price_deviation():
    class RejectingPreviewService:
        def approve(self, **_kwargs):
            return SimpleNamespace(accepted=False, reason="preview_not_found")

    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=105,
        quantity=1,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "missing-preview",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "reference_price": 100,
        },
    )

    result = TradingExecutionService(preview_service=RejectingPreviewService()).execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "preview_not_found"


def test_trading_execution_service_rejects_live_sell_without_position_quantity():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="sell",
        price=100,
        quantity=1,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "position_quantity_required_for_sell"
    assert result.pretrade.checks[0]["name"] == "position_quantity_for_sell"


def test_trading_execution_service_rejects_live_sell_more_than_position():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="sell",
        price=100,
        quantity=3,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "position_quantity": 2,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "position_quantity_for_sell"
    assert result.pretrade.checks[0]["position_quantity"] == 2


def test_trading_execution_service_rejects_live_sell_more_than_sellable_quantity():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="sell",
        price=100,
        quantity=3,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "position_quantity": 5,
            "sellable_quantity": 2,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "sellable_quantity_for_sell"
    assert result.pretrade.checks[0]["position_quantity"] == 5
    assert result.pretrade.checks[0]["sellable_quantity"] == 2
    assert result.pretrade.checks[0]["effective_quantity_source"] == "sellable_quantity"


def test_trading_execution_service_converts_common_sell_lots_to_shares():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="sell",
        price=100,
        quantity=1,
        order_lot="Common",
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "position_quantity": 999,
            "sellable_quantity": 999,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "sellable_quantity_for_sell"
    assert result.pretrade.checks[0]["quantity"] == 1
    assert result.pretrade.checks[0]["requested_shares"] == 1000
    assert result.pretrade.checks[0]["sellable_quantity"] == 999


def test_trading_execution_service_rejects_live_disposition_attention_before_gateway():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=100,
        quantity=1,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "is_disposition_or_attention": True,
            "disposition_active": True,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "disposition_attention_guard"
    assert result.pretrade.checks[0]["name"] == "disposition_attention_guard"
    assert result.pretrade.checks[0]["disposition_active"] is True


def test_trading_execution_service_rejects_live_buy_at_limit_up_before_gateway():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=110,
        quantity=1,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "limit_up_price": 110,
            "limit_down_price": 90,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "limit_up_buy_blocked"
    assert result.pretrade.checks[0]["name"] == "limit_up_down_fillability"


def test_trading_execution_service_allows_live_sell_with_enough_position_to_continue():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="sell",
        price=100,
        quantity=2,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "position_quantity": 2,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "execution_gateway_missing"


def test_trading_execution_service_allows_live_price_within_reference_guard_to_continue():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=100.5,
        quantity=1,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "reference_price": 100,
            "max_price_deviation_ticks": 3,
            "max_price_deviation_pct": 0.01,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "execution_gateway_missing"


def test_trading_execution_service_uses_stricter_common_lot_price_guard():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=101,
        quantity=1,
        order_lot="Common",
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "reference_price": 100,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "price_deviation_guard"
    assert result.pretrade.checks[0]["max_ticks"] == 2
    assert result.pretrade.checks[0]["max_pct"] == 0.005


def test_trading_execution_service_keeps_intraday_odd_price_guard_more_tolerant():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=101,
        quantity=1,
        order_lot="IntradayOdd",
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "reference_price": 100,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "execution_gateway_missing"


def test_trading_execution_service_rejects_live_max_portfolio_exposure_before_gateway():
    intent = OrderIntent(
        source="web",
        environment="live",
        symbol="2330",
        side="buy",
        price=100,
        quantity=2,
        strategy_name="manual",
        signal_id="S1",
        metadata={
            "strategy_version": "v1",
            "preview_id": "preview-1",
            "manual_confirmed": True,
            "promotion_gate_accepted": True,
            "portfolio_equity": 10_000,
            "current_portfolio_exposure": 9_400,
            "max_portfolio_exposure_pct": 0.95,
        },
    )

    result = TradingExecutionService().execute_intent(intent)

    assert result.accepted is False
    assert result.executed is False
    assert result.pretrade.reason == "max_portfolio_exposure"
    assert result.pretrade.checks[0]["name"] == "max_portfolio_exposure"
    assert result.pretrade.checks[0]["projected_portfolio_exposure"] == 9_600.0
    assert result.pretrade.checks[0]["limit_value"] == 9_500.0
