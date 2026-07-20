from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.broker.pre_trade_check import PreTradeCheckError
from app.broker.shioaji_gateway import ShioajiConfig, ShioajiGateway, ShioajiStreamingQuoteManager, StreamingQuoteBuffer
from app.execution import OrderIntent
from app.market_data import DataQualityStatus


class DummyQuote:
    def __init__(self):
        self.tick_callback = None
        self.bidask_callback = None
        self.event_callback = None
        self.subscriptions = []
        self.unsubscriptions = []

    def set_on_tick_stk_v1_callback(self, callback):
        self.tick_callback = callback

    def set_on_bidask_stk_v1_callback(self, callback):
        self.bidask_callback = callback

    def on_event(self, callback):
        self.event_callback = callback
        return callback

    def subscribe(self, contract, quote_type=None, **kwargs):  # noqa: ANN001, ANN003
        self.subscriptions.append((getattr(contract, "code", ""), str(quote_type)))
        if self.event_callback is not None:
            self.event_callback(0, 16, f"subscribe {getattr(contract, 'code', '')}", "quote")
        quote_name = " ".join(
            str(value).lower()
            for value in (
                quote_type,
                getattr(quote_type, "name", ""),
                getattr(quote_type, "value", ""),
            )
        )
        if "tick" in quote_name and self.tick_callback is not None:
            self.tick_callback(
                "TSE",
                SimpleNamespace(
                    code=getattr(contract, "code", ""),
                    datetime=datetime.now(),
                    open=100.0,
                    high=104.0,
                    low=99.0,
                    close=103.5,
                    volume=2,
                    total_volume=4567,
                    price_chg=3.5,
                    pct_chg=3.5,
                ),
            )
        if "bidask" in quote_name and self.bidask_callback is not None:
            self.bidask_callback(
                "TSE",
                SimpleNamespace(
                    code=getattr(contract, "code", ""),
                    datetime=datetime.now(),
                    bid_price=[103.0],
                    ask_price=[104.0],
                    bid_volume=[3],
                    ask_volume=[5],
                ),
            )

    def unsubscribe(self, contract, quote_type=None, **kwargs):  # noqa: ANN001, ANN003
        self.unsubscriptions.append((getattr(contract, "code", ""), str(quote_type)))
        if self.event_callback is not None:
            self.event_callback(0, 17, f"unsubscribe {getattr(contract, 'code', '')}", "quote")


class QuietDummyQuote(DummyQuote):
    def subscribe(self, contract, quote_type=None, **kwargs):  # noqa: ANN001, ANN003
        self.subscriptions.append((getattr(contract, "code", ""), str(quote_type)))
        if self.event_callback is not None:
            self.event_callback(0, 16, f"subscribe {getattr(contract, 'code', '')}", "quote")


class SlotTick:
    __slots__ = ("__dict__", "code", "datetime", "close", "volume", "total_volume")

    def __init__(self):
        self.code = "2330"
        self.datetime = datetime.now()
        self.close = 103.5
        self.volume = 2
        self.total_volume = 4567


class SlotBidAsk:
    __slots__ = ("__dict__", "code", "datetime", "bid_price", "ask_price", "bid_volume", "ask_volume")

    def __init__(self):
        self.code = "2330"
        self.datetime = datetime.now()
        self.bid_price = [103.0]
        self.ask_price = [104.0]
        self.bid_volume = [3]
        self.ask_volume = [5]


class DummyAPI:
    def __init__(self, balances: list[float], contract: SimpleNamespace | None = None):
        self._balances = list(balances)
        self.stock_account = SimpleNamespace(account_id="STOCK-ACC")
        self.Contracts = SimpleNamespace(
            Stocks={"2330": contract or SimpleNamespace(code="2330", reference=100.0, limit_down=90.0, limit_up=110.0)}
        )
        self.placed = None
        self.last_order_kwargs = None
        self.snapshot_contracts = []
        self.quote = DummyQuote()

    def account_balance(self, *args, **kwargs):  # noqa: ANN002, ANN003
        value = self._balances.pop(0) if self._balances else 0.0
        return SimpleNamespace(acc_balance=float(value))

    def Order(self, **kwargs):
        self.last_order_kwargs = kwargs
        return SimpleNamespace(**kwargs)

    def place_order(self, contract, order):
        self.placed = (contract, order)
        return SimpleNamespace(
            status=SimpleNamespace(status="Submitted"),
            account_id="1234567890",
            token="secret-token-value",
        )

    def snapshots(self, contracts):
        self.snapshot_contracts = list(contracts)
        return [
            SimpleNamespace(
                code=getattr(contract, "code", ""),
                open=100.0,
                high=103.0,
                low=99.5,
                close=102.5,
                buy_price=102.0,
                sell_price=102.5,
                volume=10,
                total_volume=1234,
                change_price=2.5,
                change_rate=2.5,
            )
            for contract in contracts
        ]

    def notice(self):
        return SimpleNamespace(
            code=["2330"],
            updated_at=["2026-06-26T18:00:00"],
            close=[100.5],
            reason=["注意交易資訊"],
            announced_date=["2026-06-26"],
        )


class BalanceFailingAPI(DummyAPI):
    def account_balance(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("balance unavailable")


def test_safe_place_stock_order_places_order_after_double_cash_check():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert result["passed"] is True
    assert result["qty"] > 0
    assert result["estimated_total_cost"] <= 5000.0
    assert result["status"] == "Submitted"
    assert isinstance(result.get("pretrade_checks"), list)
    assert any(item.get("name") == "capital_guard_first" for item in result["pretrade_checks"])
    assert result["trade"]["account_id"] != "1234567890"
    assert result["trade"]["token"] != "secret-token-value"
    assert gateway.api.placed is not None
    names = {item.get("name") for item in result["pretrade_checks"]}
    assert {
        "live_order_unlock",
        "data_freshness",
        "data_quality",
        "tick_size",
        "price_band",
        "capital_guard_first",
        "capital_guard_second",
        "duplicate_order",
        "daily_order_limit",
        "position_limit",
        "max_portfolio_exposure",
        "disposition_stock_block",
        "liquidity_threshold",
    }.issubset(names)


def test_get_stock_price_reference_returns_reference_and_limit_prices():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0])

    result = gateway.get_stock_price_reference("2330.TW")

    assert result == {
        "symbol": "2330",
        "reference_price": 100.0,
        "limit_up_price": 110.0,
        "limit_down_price": 90.0,
    }


def test_get_stock_snapshots_returns_normalized_intraday_prices():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0])

    result = gateway.get_stock_snapshots(["2330.TW", "2330"])

    assert list(result) == ["2330"]
    assert result["2330"]["price"] == 102.5
    assert result["2330"]["source"] == "shioaji_snapshot"
    assert result["2330"]["buy_price"] == 102.0
    assert result["2330"]["sell_price"] == 102.5
    assert result["2330"]["total_volume"] == 1234.0
    assert result["2330"]["raw"]["code"] == "2330"
    assert len(gateway.api.snapshot_contracts) == 1


def test_streaming_quote_buffer_keeps_bounded_history_and_tick_priority():
    buffer = StreamingQuoteBuffer(
        normalize_symbol=lambda value: str(value).replace(".TW", ""),
        max_ticks_per_symbol=1,
        max_bidasks_per_symbol=1,
    )

    assert buffer.add_bidask({"code": "2330.TW", "datetime": "2026-07-13T09:00:00", "bid_price": [100.0], "ask_price": [102.0]}) == "2330"
    assert buffer.add_tick({"code": "2330.TW", "datetime": "2026-07-13T09:00:01", "close": 103.0, "volume": 1, "total_volume": 10}) == "2330"
    assert buffer.add_tick({"code": "2330.TW", "datetime": "2026-07-13T09:00:02", "close": 104.0, "volume": 2, "total_volume": 12}) == "2330"
    buffer.record_event(0, 4, "reconnected", "quote")

    item = buffer.snapshot(["2330"])["2330"]
    stats = buffer.stats(["2330"])

    assert item["price"] == 104.0
    assert item["source"] == "shioaji_streaming_tick"
    assert item["buy_price"] == 100.0
    assert item["sell_price"] == 102.0
    assert item["quote_buffer_tick_count"] == 2
    assert item["quote_buffer_bidask_count"] == 1
    assert item["quote_buffer_retained_tick_count"] == 1
    assert item["quote_buffer_retained_bidask_count"] == 1
    assert item["quote_buffer_event_count"] == 1
    assert stats["tick_count"] == 2
    assert stats["retained_tick_count"] == 1
    assert stats["last_event"]["event_code"] == 4


def test_streaming_quote_manager_resubscribes_after_reconnect():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0])
    manager = gateway.create_stock_streaming_quote_manager(include_bidask=True)

    assert isinstance(manager, ShioajiStreamingQuoteManager)
    manager.start(["2330.TW"])
    assert len(gateway.api.quote.subscriptions) == 2

    gateway.api.quote.event_callback(0, 2, "disconnect", "quote")
    gateway.api.quote.event_callback(0, 4, "reconnect", "quote")

    snapshot = manager.snapshot(["2330"])
    status = manager.status()

    assert len(gateway.api.quote.subscriptions) == 4
    assert snapshot["2330"]["price"] == 103.5
    assert snapshot["2330"]["quote_buffer_tick_count"] == 2
    assert snapshot["2330"]["quote_buffer_bidask_count"] == 2
    assert status["connection_status"] == "connected"
    assert status["disconnect_count"] == 1
    assert status["reconnect_count"] == 1
    assert status["resubscribe_count"] == 1
    assert status["subscribed_symbols"] == ["2330"]
    assert status["subscription_count"] == 2
    assert status["buffer"]["tick_count"] == 2

    manager.stop()

    stopped = manager.status()
    assert len(gateway.api.quote.unsubscriptions) == 2
    assert stopped["state"] == "stopped"
    assert stopped["subscribed_symbols"] == ["2330"]


def test_streaming_quote_manager_serializes_slot_based_shioaji_payloads():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0])
    gateway.api.quote = QuietDummyQuote()
    manager = gateway.create_stock_streaming_quote_manager(include_bidask=True)

    assert vars(SlotTick()) == {}
    assert vars(SlotBidAsk()) == {}

    manager.start(["2330"])
    gateway.api.quote.tick_callback("TSE", SlotTick())
    gateway.api.quote.bidask_callback("TSE", SlotBidAsk())

    snapshot = manager.snapshot(["2330"])
    status = manager.status()

    assert snapshot["2330"]["price"] == 103.5
    assert snapshot["2330"]["buy_price"] == 103.0
    assert snapshot["2330"]["sell_price"] == 104.0
    assert status["buffer"]["tick_count"] == 1
    assert status["buffer"]["bidask_count"] == 1


def test_get_stock_streaming_quotes_collects_tick_and_bidask_prices():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0])

    result = gateway.get_stock_streaming_quotes(["2330.TW"], collect_seconds=0.01)

    assert list(result) == ["2330"]
    assert result["2330"]["price"] == 103.5
    assert result["2330"]["source"] == "shioaji_streaming_tick"
    assert result["2330"]["buy_price"] == 103.0
    assert result["2330"]["sell_price"] == 104.0
    assert result["2330"]["total_volume"] == 4567.0
    assert result["2330"]["raw_tick"]["code"] == "2330"
    assert result["2330"]["raw_bidask"]["code"] == "2330"
    assert result["2330"]["quote_buffer_status"] == "streaming_tick"
    assert result["2330"]["quote_buffer_tick_count"] == 1
    assert result["2330"]["quote_buffer_bidask_count"] == 1
    assert result["2330"]["quote_buffer_event_count"] == 4
    assert result["2330"]["quote_buffer"]["latest_count"] == 1
    assert result["2330"]["quote_buffer"]["tick_count"] == 1
    assert result["2330"]["quote_buffer"]["bidask_count"] == 1
    assert result["2330"]["quote_manager"]["state"] == "stopped"
    assert result["2330"]["quote_manager"]["subscribed_symbols"] == ["2330"]
    assert result["2330"]["quote_manager"]["buffer"]["tick_count"] == 1
    assert len(gateway.api.quote.subscriptions) == 2
    assert len(gateway.api.quote.unsubscriptions) == 2


def test_get_stock_streaming_quotes_falls_back_to_snapshot_when_no_streaming_quote():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0])
    gateway.api.quote.tick_callback = None
    gateway.api.quote.bidask_callback = None
    gateway.api.quote.set_on_tick_stk_v1_callback = lambda callback: None
    gateway.api.quote.set_on_bidask_stk_v1_callback = lambda callback: None

    result = gateway.get_stock_streaming_quotes(["2330"], collect_seconds=0.0)

    assert result["2330"]["price"] == 102.5
    assert result["2330"]["source"] == "shioaji_streaming_fallback_snapshot"
    assert result["2330"]["streaming_fallback"] is True
    assert result["2330"]["quote_buffer_status"] == "fallback_snapshot"


def test_fetch_attention_stocks_normalizes_notice_payload():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0])

    rows = gateway.fetch_attention_stocks(trade_date=date(2026, 6, 26))

    assert rows == [
        {
            "symbol": "2330",
            "trade_date": date(2026, 6, 26),
            "announced_date": date(2026, 6, 26),
            "updated_at": datetime(2026, 6, 26, 18, 0, 0),
            "close": 100.5,
            "reason": "注意交易資訊",
            "source": "shioaji_notice",
        }
    ]


def test_safe_place_stock_order_rejects_when_pre_trade_guard_fails():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([1000.0, 1000.0])

    with pytest.raises(PreTradeCheckError):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_when_second_cash_check_fails():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 1000.0])

    with pytest.raises(PreTradeCheckError):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_uses_simulation_cash_fallback_when_balance_unavailable(monkeypatch):
    monkeypatch.setenv("SHIOAJI_SIMULATION_CASH_FALLBACK", "20000")
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = BalanceFailingAPI([0.0, 0.0])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, quantity=1, use_odd_lot=True)

    assert result["passed"] is True
    checks = {item["name"]: item for item in result["pretrade_checks"]}
    assert checks["capital_guard_first"]["cash_source"].startswith("simulation_cash_fallback")
    assert checks["capital_guard_second"]["cash_source"].startswith("simulation_cash_fallback")


def test_safe_place_stock_order_does_not_fallback_live_balance(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = BalanceFailingAPI([0.0, 0.0])

    with pytest.raises(RuntimeError, match="unable to read account balance"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, quantity=1, use_odd_lot=True)

    assert gateway.api.placed is None


def test_execute_stock_order_in_thread_aborts_failed_order_thread():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([1000.0, 1000.0])

    with pytest.raises(PreTradeCheckError):
        gateway.execute_stock_order_in_thread(symbol="2330", current_price=100.0, timeout_sec=3)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_invalid_tick_size():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.03, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_live_without_backend_unlock(monkeypatch):
    monkeypatch.delenv("SHIOAJI_ENABLE_LIVE_ORDERS", raising=False)
    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="live_order_env_locked"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_allows_live_with_backend_unlock(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    monkeypatch.setenv("SHIOAJI_LIVE_ORDER_NONCE", "confirm-live")
    gateway = ShioajiGateway(
        ShioajiConfig(simulation=False, allow_live_order=True, live_order_nonce="confirm-live")
    )
    gateway.api = DummyAPI([5000.0, 5000.0])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert result["passed"] is True
    assert gateway.api.placed is not None


def test_safe_place_stock_order_rejects_live_nonce_mismatch(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    monkeypatch.setenv("SHIOAJI_LIVE_ORDER_NONCE", "confirm-live")
    gateway = ShioajiGateway(
        ShioajiConfig(simulation=False, allow_live_order=True, live_order_nonce="wrong-nonce")
    )
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="live_order_nonce_mismatch"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_daily_order_limit():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True, max_daily_orders=1))
    gateway.api = DummyAPI([5000.0, 5000.0])
    gateway._increase_daily_order_count()

    with pytest.raises(PreTradeCheckError, match="daily_order_limit"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_duplicate_order():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True, duplicate_window_sec=60))
    gateway.api = DummyAPI([5000.0, 5000.0, 5000.0, 5000.0])

    first = gateway.safe_place_stock_order(symbol="2330.TW", current_price=100.0, use_odd_lot=True)

    with pytest.raises(PreTradeCheckError, match="duplicate_order"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, use_odd_lot=True)

    assert first["passed"] is True
    assert gateway.api.placed is not None


def test_safe_place_stock_order_rejects_price_out_of_band():
    contract = SimpleNamespace(code="2330", reference=100.0, limit_down=90.0, limit_up=105.0)
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0], contract=contract)

    with pytest.raises(PreTradeCheckError, match="price_out_of_band"):
        gateway.safe_place_stock_order(symbol="2330", current_price=106.0, use_odd_lot=True)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_price_deviation_guard():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="price_deviation_guard"):
        gateway.safe_place_stock_order(symbol="2330", current_price=105.0, reference_price=100.0, quantity=1)

    assert gateway.api.placed is None


def test_safe_place_stock_order_rejects_sell_more_than_position():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="position_quantity_for_sell"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, side="sell", quantity=3, position_quantity=2)

    assert gateway.api.placed is None


def test_safe_place_stock_order_converts_common_sell_lots_to_shares():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="position_quantity_for_sell"):
        gateway.safe_place_stock_order(
            symbol="2330",
            current_price=100.0,
            side="sell",
            quantity=1,
            use_odd_lot=False,
            position_quantity=999,
        )

    assert gateway.api.placed is None


def test_safe_place_stock_order_prices_common_lots_as_1000_shares():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([200_000.0, 200_000.0])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, quantity=1, use_odd_lot=False)

    assert result["passed"] is True
    assert result["qty"] == 1
    assert result["share_quantity"] == 1000
    assert result["estimated_total_cost"] > 100_000
    assert result["order_lot"] == "Common"
    assert gateway.api.last_order_kwargs["quantity"] == 1


def test_safe_place_stock_order_rejects_live_disposition_and_low_liquidity(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    with pytest.raises(PreTradeCheckError, match="disposition_attention_guard"):
        gateway.safe_place_stock_order(
            symbol="2330",
            current_price=100.0,
            quantity=1,
            is_disposition_or_attention=True,
        )

    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = DummyAPI([5000.0, 5000.0])
    with pytest.raises(PreTradeCheckError, match="liquidity_guard"):
        gateway.safe_place_stock_order(symbol="2330", current_price=100.0, quantity=1, avg_volume_20=100.0)


def test_safe_place_stock_order_rejects_max_portfolio_exposure():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([10_000.0, 10_000.0])

    with pytest.raises(PreTradeCheckError, match="max_portfolio_exposure"):
        gateway.safe_place_stock_order(
            symbol="2330",
            current_price=100.0,
            quantity=2,
            portfolio_equity=10_000,
            current_portfolio_exposure=9_400,
            max_portfolio_exposure_pct=0.95,
        )

    assert gateway.api.placed is None


def test_execute_intent_returns_execution_result():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])
    intent = OrderIntent(source="cli", environment="simulation", symbol="2330.TW", side="buy", price=100.0)

    result = gateway.execute_intent(intent)

    assert result.intent_id == intent.intent_id
    assert result.accepted is True
    assert result.executed is True
    assert result.symbol == "2330"
    assert result.pretrade.accepted is True
    assert result.raw_trade["token"] != "secret-token-value"


def test_safe_place_stock_order_supports_sell_side_with_requested_quantity():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = DummyAPI([5000.0, 5000.0])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, side="sell", quantity=3)

    assert result["passed"] is True
    assert result["side"] == "sell"
    assert result["qty"] == 3
    assert gateway.api.last_order_kwargs["action"] != "Buy"


def test_safe_place_stock_sell_does_not_require_cash_lookup():
    gateway = ShioajiGateway(ShioajiConfig(simulation=True))
    gateway.api = BalanceFailingAPI([])

    result = gateway.safe_place_stock_order(symbol="2330", current_price=100.0, side="sell", quantity=3)

    assert result["passed"] is True
    assert result["side"] == "sell"
    assert result["available_cash_before"] is None
    assert result["available_cash_before_order"] is None
    assert gateway.api.placed is not None


def test_execute_intent_rejects_live_insecure_data_quality(monkeypatch):
    monkeypatch.setenv("SHIOAJI_ENABLE_LIVE_ORDERS", "1")
    gateway = ShioajiGateway(ShioajiConfig(simulation=False, allow_live_order=True))
    gateway.api = DummyAPI([5000.0, 5000.0])
    intent = OrderIntent(source="cli", environment="live", symbol="2330", side="buy", price=100.0)
    quality = DataQualityStatus(freshness_status="ok", insecure_transport=True)

    result = gateway.execute_intent(intent, data_quality=quality)

    assert result.accepted is False
    assert result.executed is False
    assert "insecure_transport" in result.pretrade.reason
    assert gateway.api.placed is None
