from __future__ import annotations

import logging
import os
import threading
import time as time_module
from collections import deque
from datetime import date, datetime, time, timedelta
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from app.backtest.costs import estimate_buy_total_cost
from app.broker.pre_trade_check import (
    LiveOrderUnlockConfig,
    PreTradeCheckError,
    enforce_absolute_capital_guard,
    validate_live_order_unlock,
)
from app.execution.models import ExecutionResult, OrderIntent, PreTradeDecision
from app.market_data import DataQualityStatus
from app.security import redact_sensitive


logger = logging.getLogger(__name__)


@dataclass
class ShioajiConfig:
    simulation: bool = True
    env_path: str = ".env"
    order_timeout_sec: int = 20
    max_daily_orders: int = 20
    duplicate_window_sec: int = 10
    enforce_session_check: bool = False
    allow_live_order: bool = False
    live_order_nonce: str = ""


class StreamingQuoteBuffer:
    """Bounded in-memory quote buffer for stock tick/bidask callbacks."""

    def __init__(
        self,
        *,
        normalize_symbol: Any,
        max_ticks_per_symbol: int = 200,
        max_bidasks_per_symbol: int = 100,
        max_events: int = 100,
    ):
        self._normalize_symbol = normalize_symbol
        self._max_ticks_per_symbol = max(1, int(max_ticks_per_symbol))
        self._max_bidasks_per_symbol = max(1, int(max_bidasks_per_symbol))
        self._latest: dict[str, dict[str, Any]] = {}
        self._ticks: dict[str, deque[dict[str, Any]]] = {}
        self._bidasks: dict[str, deque[dict[str, Any]]] = {}
        self._tick_totals: dict[str, int] = {}
        self._bidask_totals: dict[str, int] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=max(1, int(max_events)))
        self._lock = threading.Lock()

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    @classmethod
    def _first_float(cls, values: Any) -> float | None:
        if isinstance(values, (list, tuple)):
            for item in values:
                value = cls._safe_float(item)
                if value is not None and value > 0:
                    return value
            return None
        value = cls._safe_float(values)
        return value if value is not None and value > 0 else None

    @staticmethod
    def _checked_at_from_payload(payload: dict[str, Any]) -> str:
        raw_dt = payload.get("datetime") or payload.get("ts")
        if isinstance(raw_dt, datetime):
            return raw_dt.isoformat(timespec="seconds")
        if raw_dt:
            return str(raw_dt)
        return datetime.now().isoformat(timespec="seconds")

    def add_tick(self, raw: Any) -> str | None:
        data = raw if isinstance(raw, dict) else {}
        code = self._normalize_symbol(str(data.get("code") or ""))
        price = self._safe_float(data.get("close"))
        if not code or price is None or price <= 0:
            return None
        item = {
            "symbol": code,
            "price": float(price),
            "source": "shioaji_streaming_tick",
            "checked_at": self._checked_at_from_payload(data),
            "open": self._safe_float(data.get("open")),
            "high": self._safe_float(data.get("high")),
            "low": self._safe_float(data.get("low")),
            "close": self._safe_float(data.get("close")),
            "volume": self._safe_float(data.get("volume")),
            "total_volume": self._safe_float(data.get("total_volume")),
            "change_price": self._safe_float(data.get("price_chg") or data.get("change_price")),
            "change_rate": self._safe_float(data.get("pct_chg") or data.get("change_rate")),
            "raw_tick": raw,
            "quote_buffer_status": "streaming_tick",
        }
        with self._lock:
            previous = self._latest.get(code, {})
            merged = {**previous, **{key: value for key, value in item.items() if value not in (None, "")}}
            if previous.get("raw_bidask") is not None:
                merged["raw_bidask"] = previous.get("raw_bidask")
            self._ticks.setdefault(code, deque(maxlen=self._max_ticks_per_symbol)).append(dict(item))
            self._tick_totals[code] = int(self._tick_totals.get(code, 0)) + 1
            self._latest[code] = self._with_symbol_stats(code, merged)
        return code

    def add_bidask(self, raw: Any) -> str | None:
        data = raw if isinstance(raw, dict) else {}
        code = self._normalize_symbol(str(data.get("code") or ""))
        bid_price = self._first_float(data.get("bid_price"))
        ask_price = self._first_float(data.get("ask_price"))
        price = None
        if bid_price is not None and ask_price is not None:
            price = (bid_price + ask_price) / 2.0
        elif ask_price is not None:
            price = ask_price
        elif bid_price is not None:
            price = bid_price
        if not code or price is None or price <= 0:
            return None
        item = {
            "symbol": code,
            "price": float(price),
            "source": "shioaji_streaming_bidask",
            "checked_at": self._checked_at_from_payload(data),
            "buy_price": bid_price,
            "sell_price": ask_price,
            "raw_bidask": raw,
            "quote_buffer_status": "streaming_bidask",
        }
        with self._lock:
            previous = self._latest.get(code, {})
            merged = dict(previous)
            if previous.get("source") != "shioaji_streaming_tick":
                merged.update({key: value for key, value in item.items() if value not in (None, "")})
            else:
                merged.update(
                    {
                        key: value
                        for key, value in item.items()
                        if key in {"buy_price", "sell_price", "raw_bidask"} and value not in (None, "")
                    }
                )
            self._bidasks.setdefault(code, deque(maxlen=self._max_bidasks_per_symbol)).append(dict(item))
            self._bidask_totals[code] = int(self._bidask_totals.get(code, 0)) + 1
            self._latest[code] = self._with_symbol_stats(code, merged)
        return code

    def record_event(self, resp_code: Any, event_code: Any, info: Any, event: Any) -> None:
        with self._lock:
            self._events.append(
                {
                    "resp_code": resp_code,
                    "event_code": event_code,
                    "info": str(info or ""),
                    "event": str(event or ""),
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            for symbol, item in list(self._latest.items()):
                self._latest[symbol] = self._with_symbol_stats(symbol, item)

    def latest_count(self, symbols: list[str] | tuple[str, ...]) -> int:
        clean = {self._normalize_symbol(str(symbol or "")) for symbol in symbols}
        clean.discard("")
        with self._lock:
            return sum(1 for symbol in clean if symbol in self._latest)

    def snapshot(self, symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, dict[str, Any]]:
        with self._lock:
            if symbols is None:
                selected = sorted(self._latest)
            else:
                selected = []
                for symbol in symbols:
                    code = self._normalize_symbol(str(symbol or ""))
                    if code and code not in selected:
                        selected.append(code)
            return {symbol: dict(self._latest[symbol]) for symbol in selected if symbol in self._latest}

    def stats(self, symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        with self._lock:
            selected = (
                sorted(self._latest)
                if symbols is None
                else [self._normalize_symbol(str(symbol or "")) for symbol in symbols if self._normalize_symbol(str(symbol or ""))]
            )
            selected = list(dict.fromkeys(selected))
            tick_counts = {symbol: int(self._tick_totals.get(symbol, 0)) for symbol in selected}
            bidask_counts = {symbol: int(self._bidask_totals.get(symbol, 0)) for symbol in selected}
            retained_tick_counts = {symbol: len(self._ticks.get(symbol, ())) for symbol in selected}
            retained_bidask_counts = {symbol: len(self._bidasks.get(symbol, ())) for symbol in selected}
            return {
                "symbols": [symbol for symbol in selected if symbol in self._latest],
                "latest_count": sum(1 for symbol in selected if symbol in self._latest),
                "tick_count": sum(tick_counts.values()),
                "bidask_count": sum(bidask_counts.values()),
                "retained_tick_count": sum(retained_tick_counts.values()),
                "retained_bidask_count": sum(retained_bidask_counts.values()),
                "event_count": len(self._events),
                "tick_counts": tick_counts,
                "bidask_counts": bidask_counts,
                "retained_tick_counts": retained_tick_counts,
                "retained_bidask_counts": retained_bidask_counts,
                "last_event": dict(self._events[-1]) if self._events else None,
            }

    def _with_symbol_stats(self, symbol: str, item: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(item)
        enriched["quote_buffer_tick_count"] = int(self._tick_totals.get(symbol, 0))
        enriched["quote_buffer_bidask_count"] = int(self._bidask_totals.get(symbol, 0))
        enriched["quote_buffer_retained_tick_count"] = len(self._ticks.get(symbol, ()))
        enriched["quote_buffer_retained_bidask_count"] = len(self._bidasks.get(symbol, ()))
        enriched["quote_buffer_event_count"] = len(self._events)
        if self._events:
            enriched["quote_buffer_last_event"] = dict(self._events[-1])
        return enriched


class ShioajiStreamingQuoteManager:
    """Manage Shioaji stock quote subscriptions and reconnect-aware status."""

    _TICK_FIELDS = (
        "code",
        "datetime",
        "open",
        "avg_price",
        "close",
        "high",
        "low",
        "amount",
        "total_amount",
        "volume",
        "total_volume",
        "tick_type",
        "chg_type",
        "price_chg",
        "pct_chg",
        "bid_side_total_vol",
        "ask_side_total_vol",
        "bid_side_total_cnt",
        "ask_side_total_cnt",
        "closing_oddlot_shares",
        "fixed_trade_vol",
        "suspend",
        "simtrade",
        "intraday_odd",
    )
    _BIDASK_FIELDS = (
        "code",
        "datetime",
        "bid_price",
        "bid_volume",
        "diff_bid_vol",
        "ask_price",
        "ask_volume",
        "diff_ask_vol",
        "suspend",
        "simtrade",
        "intraday_odd",
    )

    def __init__(
        self,
        *,
        api: Any,
        normalize_symbol: Any,
        get_stock_contract: Any,
        serialize: Any,
        resolve_constant: Any,
        include_bidask: bool = True,
        subscribe_batch_size: int = 45,
        subscribe_batch_pause_seconds: float = 5.0,
        max_ticks_per_symbol: int = 200,
        max_bidasks_per_symbol: int = 100,
    ):
        self.api = api
        self.quote = getattr(api, "quote", None)
        self._normalize_symbol = normalize_symbol
        self._get_stock_contract = get_stock_contract
        self._serialize = serialize
        self._resolve_constant = resolve_constant
        self._include_bidask = bool(include_bidask)
        self._subscribe_batch_size = max(1, int(subscribe_batch_size or 1))
        self._subscribe_batch_pause_seconds = max(0.0, float(subscribe_batch_pause_seconds or 0.0))
        self.buffer = StreamingQuoteBuffer(
            normalize_symbol=normalize_symbol,
            max_ticks_per_symbol=max_ticks_per_symbol,
            max_bidasks_per_symbol=max_bidasks_per_symbol,
        )
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._contracts: dict[str, Any] = {}
        self._known_symbols: set[str] = set()
        self._subscriptions: list[tuple[str, Any, Any]] = []
        self._callback_installed = False
        self._event_callback_method: str | None = None
        self._state = "idle"
        self._connection_status = "not_started"
        self._started_at: str | None = None
        self._stopped_at: str | None = None
        self._last_heartbeat_at: str | None = None
        self._last_reconnect_at: str | None = None
        self._last_resubscribe_at: str | None = None
        self._reconnect_count = 0
        self._disconnect_count = 0
        self._resubscribe_count = 0
        self._subscription_call_count = 0

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _event_code_int(value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    def _quote_types(self) -> tuple[Any, Any]:
        try:
            import shioaji as sj
        except Exception:  # noqa: BLE001
            sj = None
        tick_type = self._resolve_constant(sj, "QuoteType", "Tick", "Tick")
        bidask_type = self._resolve_constant(sj, "QuoteType", "BidAsk", "BidAsk")
        return tick_type, bidask_type

    def _install_callbacks(self) -> None:
        if self.quote is None:
            raise RuntimeError("shioaji quote API is unavailable")
        if self._callback_installed:
            return

        def update_tick(tick: Any) -> None:
            raw = self._serialize_stream_payload(tick, self._TICK_FIELDS)
            if self.buffer.add_tick(raw):
                self._event.set()

        def update_bidask(bidask: Any) -> None:
            raw = self._serialize_stream_payload(bidask, self._BIDASK_FIELDS)
            if self.buffer.add_bidask(raw):
                self._event.set()

        tick_callback = lambda _exchange, tick: update_tick(tick)  # noqa: E731
        bidask_callback = lambda _exchange, bidask: update_bidask(bidask)  # noqa: E731
        tick_setter = getattr(self.quote, "set_on_tick_stk_v1_callback", None)
        bidask_setter = getattr(self.quote, "set_on_bidask_stk_v1_callback", None)
        if callable(tick_setter):
            tick_setter(tick_callback)
        if self._include_bidask and callable(bidask_setter):
            bidask_setter(bidask_callback)
        self._event_callback_method = ShioajiGateway._set_quote_event_callback(self.quote, self._handle_quote_event)
        self._callback_installed = True

    def _serialize_stream_payload(self, value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
        raw = self._serialize(value)
        payload = {
            field: raw[field]
            for field in fields
            if isinstance(raw, dict) and field in raw
        }
        for field in fields:
            if field not in payload and hasattr(value, field):
                payload[field] = self._serialize(getattr(value, field))
        return payload

    def _handle_quote_event(self, resp_code: Any, event_code: Any, info: Any, event_name: Any) -> None:
        self.buffer.record_event(resp_code, event_code, info, event_name)
        code = self._event_code_int(event_code)
        should_resubscribe = False
        with self._lock:
            if code == 0:
                self._connection_status = "connected"
                self._last_heartbeat_at = self._now()
            elif code == 1:
                self._connection_status = "connected"
            elif code == 2:
                self._connection_status = "disconnected"
                self._disconnect_count += 1
            elif code == 3:
                self._connection_status = "reconnecting"
            elif code == 4:
                self._connection_status = "connected"
                self._last_reconnect_at = self._now()
                self._reconnect_count += 1
                should_resubscribe = self._state == "active" and bool(self._subscriptions)
        if should_resubscribe:
            self.resubscribe_all(reason="reconnected")

    def start(self, symbols: list[str] | tuple[str, ...]) -> None:
        if self.quote is None:
            raise RuntimeError("shioaji quote API is unavailable")
        self._install_callbacks()
        subscribe = getattr(self.quote, "subscribe", None)
        if not callable(subscribe):
            raise RuntimeError("shioaji quote.subscribe API is unavailable")
        tick_type, bidask_type = self._quote_types()
        clean_symbols: list[str] = []
        for symbol in symbols:
            code = self._normalize_symbol(str(symbol or ""))
            if code and code not in clean_symbols:
                clean_symbols.append(code)
        with self._lock:
            if self._state == "idle":
                self._started_at = self._now()
                self._stopped_at = None
            self._state = "active"
            if self._connection_status == "not_started":
                self._connection_status = "connected"
            self._known_symbols.update(clean_symbols)
        total_calls = len([symbol for symbol in clean_symbols if symbol not in self._contracts]) * (2 if self._include_bidask else 1)
        for symbol in clean_symbols:
            if symbol in self._contracts:
                continue
            contract = self._get_stock_contract(symbol)
            self._contracts[symbol] = contract
            self._subscribe(symbol, contract, tick_type, total_calls=total_calls, record=True)
            if self._include_bidask:
                self._subscribe(symbol, contract, bidask_type, total_calls=total_calls, record=True)

    def stop(self) -> None:
        if self.quote is None:
            with self._lock:
                self._state = "stopped"
                self._stopped_at = self._now()
            return
        unsubscribe = getattr(self.quote, "unsubscribe", None)
        subscriptions = list(self._subscriptions)
        if callable(unsubscribe):
            for _symbol, contract, quote_type in subscriptions:
                try:
                    unsubscribe(contract, quote_type=quote_type)
                except Exception:
                    continue
        with self._lock:
            self._subscriptions.clear()
            self._contracts.clear()
            self._state = "stopped"
            self._stopped_at = self._now()

    def resubscribe_all(self, *, reason: str = "manual") -> None:
        if self.quote is None:
            return
        subscribe = getattr(self.quote, "subscribe", None)
        if not callable(subscribe):
            return
        subscriptions = list(self._subscriptions)
        if not subscriptions:
            return
        with self._lock:
            self._resubscribe_count += 1
            self._last_resubscribe_at = self._now()
            self._connection_status = "connected"
        total_calls = len(subscriptions)
        for index, (_symbol, contract, quote_type) in enumerate(subscriptions, start=1):
            try:
                subscribe(contract, quote_type=quote_type)
            except Exception as exc:  # noqa: BLE001
                self.buffer.record_event("error", "resubscribe_failed", f"{reason}:{type(exc).__name__}", "quote")
                continue
            self._pause_for_quote_rate_limit(index, total_calls=total_calls)

    def wait_until_ready(self, symbols: list[str] | tuple[str, ...], timeout: float) -> None:
        clean_symbols = [self._normalize_symbol(str(symbol or "")) for symbol in symbols]
        clean_symbols = [symbol for symbol in dict.fromkeys(clean_symbols) if symbol]
        deadline = time_module.monotonic() + max(0.0, float(timeout or 0.0))
        while self.buffer.latest_count(clean_symbols) < len(clean_symbols) and time_module.monotonic() < deadline:
            self._event.wait(timeout=min(0.2, max(0.0, deadline - time_module.monotonic())))
            self._event.clear()

    def snapshot(self, symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, dict[str, Any]]:
        output = self.buffer.snapshot(symbols)
        buffer_stats = self.buffer.stats(symbols)
        manager_status = self.status()
        for item in output.values():
            item["quote_buffer"] = buffer_stats
            item["quote_manager"] = manager_status
        return output

    def status(self) -> dict[str, Any]:
        with self._lock:
            subscribed_symbols = sorted(self._known_symbols or set(self._contracts))
            return {
                "state": self._state,
                "connection_status": self._connection_status,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "subscribed_symbols": subscribed_symbols,
                "subscription_count": len(self._subscriptions),
                "subscription_call_count": self._subscription_call_count,
                "include_bidask": self._include_bidask,
                "event_callback_method": self._event_callback_method,
                "last_heartbeat_at": self._last_heartbeat_at,
                "last_reconnect_at": self._last_reconnect_at,
                "last_resubscribe_at": self._last_resubscribe_at,
                "reconnect_count": self._reconnect_count,
                "disconnect_count": self._disconnect_count,
                "resubscribe_count": self._resubscribe_count,
                "buffer": self.buffer.stats(subscribed_symbols),
            }

    def _subscribe(self, symbol: str, contract: Any, quote_type: Any, *, total_calls: int, record: bool) -> None:
        subscribe = getattr(self.quote, "subscribe", None)
        if not callable(subscribe):
            raise RuntimeError("shioaji quote.subscribe API is unavailable")
        subscribe(contract, quote_type=quote_type)
        with self._lock:
            self._subscription_call_count += 1
            if record:
                self._subscriptions.append((symbol, contract, quote_type))
        self._pause_for_quote_rate_limit(self._subscription_call_count, total_calls=total_calls)

    def _pause_for_quote_rate_limit(self, call_count: int, *, total_calls: int) -> None:
        if call_count > 0 and call_count < int(total_calls) and call_count % self._subscribe_batch_size == 0:
            time_module.sleep(self._subscribe_batch_pause_seconds)


class ShioajiGateway:
    """
    Phase 3 live-trading gateway:
    1) read live available cash
    2) absolute-capital pre-trade guard
    3) second cash check immediately before place_order
    4) place order only when all checks pass
    """

    def __init__(self, config: ShioajiConfig | None = None):
        self.config = config or ShioajiConfig()
        load_dotenv(self.config.env_path, override=False)
        self.api = None
        self._daily_order_count: dict[str, int] = {}
        self._recent_order_ts: dict[tuple[str, str, int], datetime] = {}

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        code = (symbol or "").strip().upper()
        for suffix in (".TW", ".TWO"):
            if code.endswith(suffix):
                code = code[: -len(suffix)]
        return code

    def login(self):
        try:
            import shioaji as sj
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("shioaji is not installed") from exc

        api_key = os.getenv("SHIOAJI_APIKEY", "").strip()
        secret = os.getenv("SHIOAJI_SECRET", "").strip()
        if not api_key or not secret:
            raise RuntimeError("SHIOAJI_APIKEY/SHIOAJI_SECRET missing")
        self.api = sj.Shioaji(simulation=self.config.simulation)
        self.api.login(api_key=api_key, secret_key=secret)
        return self.api

    @staticmethod
    def _extract_balance_value(payload: Any) -> float | None:
        if payload is None:
            return None
        if isinstance(payload, (int, float)):
            return float(payload)
        if isinstance(payload, (list, tuple)):
            for item in payload:
                value = ShioajiGateway._extract_balance_value(item)
                if value is not None:
                    return value
            return None

        for key in ("acc_balance", "available_balance", "available_funds", "available_amount", "available_cash"):
            if isinstance(payload, dict) and isinstance(payload.get(key), (int, float)):
                return float(payload[key])
            value = getattr(payload, key, None)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    @staticmethod
    def _tw_tick_size(price: float) -> float:
        value = float(price)
        if value < 10:
            return 0.01
        if value < 50:
            return 0.05
        if value < 100:
            return 0.1
        if value < 500:
            return 0.5
        if value < 1000:
            return 1.0
        return 5.0

    @staticmethod
    def _is_valid_tick(price: float) -> bool:
        tick = ShioajiGateway._tw_tick_size(price)
        if tick <= 0:
            return False
        scaled = round(float(price) / tick)
        return abs((scaled * tick) - float(price)) < 1e-8

    @staticmethod
    def _is_trading_session(now_dt: datetime | None = None) -> bool:
        now_dt = now_dt or datetime.now()
        if now_dt.weekday() >= 5:
            return False
        now_time = now_dt.time()
        return time(9, 0) <= now_time <= time(13, 30)

    @staticmethod
    def _is_within_price_band(contract: Any, price: float) -> bool:
        limit_down = getattr(contract, "limit_down", None)
        limit_up = getattr(contract, "limit_up", None)
        if isinstance(limit_down, (int, float)) and float(price) < float(limit_down):
            return False
        if isinstance(limit_up, (int, float)) and float(price) > float(limit_up):
            return False
        return True

    @classmethod
    def _tick_distance(cls, left: float, right: float) -> int:
        low = min(float(left), float(right))
        high = max(float(left), float(right))
        if high <= low:
            return 0
        distance = 0
        cursor = low
        while cursor < high and distance <= 10_000:
            cursor += cls._tw_tick_size(cursor)
            distance += 1
        return distance

    def _is_duplicate_order(self, symbol: str, qty: int, price: float) -> bool:
        now_dt = datetime.now()
        key = (self._normalize_symbol(symbol), str(int(qty)), int(round(float(price) * 100)))
        last = self._recent_order_ts.get(key)
        if last is None:
            return False
        return (now_dt - last) <= timedelta(seconds=max(1, int(self.config.duplicate_window_sec)))

    def _mark_order_seen(self, symbol: str, qty: int, price: float) -> None:
        key = (self._normalize_symbol(symbol), str(int(qty)), int(round(float(price) * 100)))
        self._recent_order_ts[key] = datetime.now()

    def _check_daily_order_limit(self) -> bool:
        day_key = datetime.now().strftime("%Y-%m-%d")
        count = int(self._daily_order_count.get(day_key, 0))
        return count < max(1, int(self.config.max_daily_orders))

    def _increase_daily_order_count(self) -> None:
        day_key = datetime.now().strftime("%Y-%m-%d")
        self._daily_order_count[day_key] = int(self._daily_order_count.get(day_key, 0)) + 1

    def get_available_cash(self) -> float:
        if self.api is None:
            self.login()
        account = getattr(self.api, "stock_account", None)
        if account is None:
            raise RuntimeError("stock account not found")

        for call in (
            lambda: self.api.account_balance(account=account),
            lambda: self.api.account_balance(account),
            lambda: self.api.account_balance(),
        ):
            try:
                result = call()
            except Exception:
                continue
            value = self._extract_balance_value(result)
            if value is not None:
                return float(value)
        raise RuntimeError("unable to read account balance")

    def _get_available_cash_for_order(self) -> tuple[float, str]:
        try:
            cash = float(self.get_available_cash())
            if cash > 0 or not self.config.simulation:
                return cash, "account_balance"
        except Exception as exc:  # noqa: BLE001
            if not self.config.simulation:
                raise
            reason = f"simulation_cash_fallback:{type(exc).__name__}"
        else:
            reason = "simulation_cash_fallback:zero_balance"
        fallback = float(os.getenv("SHIOAJI_SIMULATION_CASH_FALLBACK", "1000000") or 1_000_000)
        return max(1.0, fallback), reason

    def _get_stock_contract(self, symbol: str):
        if self.api is None:
            self.login()
        code = self._normalize_symbol(symbol)
        stocks = getattr(getattr(self.api, "Contracts", None), "Stocks", None)
        if stocks is None:
            raise RuntimeError("unable to access Contracts.Stocks")
        for getter in (
            lambda: stocks[code],
            lambda: getattr(stocks, "TSE", {})[code],
            lambda: getattr(stocks, "OTC", {})[code],
        ):
            try:
                return getter()
            except Exception:
                continue
        raise RuntimeError(f"stock contract not found: {code}")

    def _get_futures_contract(self, symbol: str):
        if self.api is None:
            self.login()
        code = self._normalize_symbol(symbol)
        futures = getattr(getattr(self.api, "Contracts", None), "Futures", None)
        if futures is None:
            raise RuntimeError("unable to access Contracts.Futures")
        for getter in (
            lambda: futures[code],
            lambda: getattr(futures, code)[code],
            lambda: getattr(futures, "TXF", {})[code],
            lambda: getattr(futures, "MXF", {})[code],
        ):
            try:
                return getter()
            except Exception:
                continue
        raise RuntimeError(f"futures contract not found: {code}")

    def resolve_order_price(self, symbol: str, explicit_price: float | None = None, price_offset_ticks: int = 0) -> float:
        if explicit_price is not None and float(explicit_price) > 0:
            base_price = float(explicit_price)
        else:
            contract = self._get_stock_contract(symbol)
            for key in ("reference", "close"):
                value = getattr(contract, key, None)
                if isinstance(value, (int, float)) and float(value) > 0:
                    base_price = float(value)
                    break
            else:
                raise RuntimeError("unable to resolve reference price")

        adjusted = float(base_price)
        steps = int(price_offset_ticks)
        if steps > 0:
            for _ in range(steps):
                adjusted += self._tw_tick_size(adjusted)
        elif steps < 0:
            for _ in range(abs(steps)):
                adjusted = max(self._tw_tick_size(adjusted), adjusted - self._tw_tick_size(adjusted))
        return float(adjusted)

    def get_stock_price_reference(self, symbol: str) -> dict[str, float | str | None]:
        contract = self._get_stock_contract(symbol)
        reference_price = None
        for key in ("reference", "close"):
            value = getattr(contract, key, None)
            if isinstance(value, (int, float)) and float(value) > 0:
                reference_price = float(value)
                break
        if reference_price is None:
            raise RuntimeError("unable to resolve reference price")

        def _optional_contract_float(name: str) -> float | None:
            value = getattr(contract, name, None)
            return float(value) if isinstance(value, (int, float)) and float(value) > 0 else None

        return {
            "symbol": self._normalize_symbol(symbol),
            "reference_price": float(reference_price),
            "limit_up_price": _optional_contract_float("limit_up"),
            "limit_down_price": _optional_contract_float("limit_down"),
        }

    def get_stock_snapshots(self, symbols: list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
        if self.api is None:
            self.login()
        clean_symbols: list[str] = []
        contracts: list[Any] = []
        for symbol in symbols:
            code = self._normalize_symbol(str(symbol or ""))
            if not code or code in clean_symbols:
                continue
            contracts.append(self._get_stock_contract(code))
            clean_symbols.append(code)
        if not contracts:
            return {}
        snapshots_fn = getattr(self.api, "snapshots", None)
        if not callable(snapshots_fn):
            raise RuntimeError("shioaji snapshots API is unavailable")
        snapshots = snapshots_fn(contracts)
        checked_at = datetime.now().isoformat(timespec="seconds")

        def optional_float(value: Any) -> float | None:
            try:
                if value is None or value == "":
                    return None
                return float(value)
            except Exception:
                return None

        output: dict[str, dict[str, Any]] = {}
        for idx, snapshot in enumerate(list(snapshots or [])):
            raw = self._serialize(snapshot)
            data = raw if isinstance(raw, dict) else {}
            fallback_code = clean_symbols[idx] if idx < len(clean_symbols) else ""
            code = self._normalize_symbol(str(data.get("code") or fallback_code))
            price = optional_float(data.get("close"))
            if price is None or price <= 0:
                price = optional_float(data.get("buy_price")) or optional_float(data.get("sell_price"))
            if not code or price is None or price <= 0:
                continue
            output[code] = {
                "symbol": code,
                "price": float(price),
                "source": "shioaji_snapshot",
                "checked_at": checked_at,
                "open": optional_float(data.get("open")),
                "high": optional_float(data.get("high")),
                "low": optional_float(data.get("low")),
                "close": optional_float(data.get("close")),
                "buy_price": optional_float(data.get("buy_price")),
                "sell_price": optional_float(data.get("sell_price")),
                "volume": optional_float(data.get("volume")),
                "total_volume": optional_float(data.get("total_volume")),
                "change_price": optional_float(data.get("change_price")),
                "change_rate": optional_float(data.get("change_rate")),
                "raw": raw,
            }
        return output

    def get_stock_streaming_quotes(
        self,
        symbols: list[str] | tuple[str, ...],
        *,
        collect_seconds: float = 3.0,
        include_bidask: bool = True,
        fallback_to_snapshot: bool = True,
        subscribe_batch_size: int = 45,
        subscribe_batch_pause_seconds: float = 5.0,
    ) -> dict[str, dict[str, Any]]:
        if self.api is None:
            self.login()
        clean_symbols: list[str] = []
        for symbol in symbols:
            code = self._normalize_symbol(str(symbol or ""))
            if not code or code in clean_symbols:
                continue
            clean_symbols.append(code)
        if not clean_symbols:
            return {}

        quote = getattr(self.api, "quote", None)
        if quote is None:
            if fallback_to_snapshot:
                return self._streaming_snapshot_fallback(clean_symbols)
            raise RuntimeError("shioaji quote API is unavailable")

        subscribe = getattr(quote, "subscribe", None)
        if not callable(subscribe):
            if fallback_to_snapshot:
                return self._streaming_snapshot_fallback(clean_symbols)
            raise RuntimeError("shioaji quote.subscribe API is unavailable")

        manager = self.create_stock_streaming_quote_manager(
            include_bidask=include_bidask,
            subscribe_batch_size=subscribe_batch_size,
            subscribe_batch_pause_seconds=subscribe_batch_pause_seconds,
        )
        try:
            manager.start(clean_symbols)
            manager.wait_until_ready(clean_symbols, collect_seconds)
        finally:
            manager.stop()

        output = manager.snapshot(clean_symbols)
        if fallback_to_snapshot:
            missing = [symbol for symbol in clean_symbols if symbol not in output]
            if missing:
                fallback = self._streaming_snapshot_fallback(missing)
                manager_status = manager.status()
                for item in fallback.values():
                    item["quote_manager"] = manager_status
                output.update(fallback)
        return output

    def create_stock_streaming_quote_manager(
        self,
        *,
        include_bidask: bool = True,
        subscribe_batch_size: int = 45,
        subscribe_batch_pause_seconds: float = 5.0,
        max_ticks_per_symbol: int = 200,
        max_bidasks_per_symbol: int = 100,
    ) -> ShioajiStreamingQuoteManager:
        if self.api is None:
            self.login()
        return ShioajiStreamingQuoteManager(
            api=self.api,
            normalize_symbol=self._normalize_symbol,
            get_stock_contract=self._get_stock_contract,
            serialize=self._serialize,
            resolve_constant=self._resolve_constant,
            include_bidask=include_bidask,
            subscribe_batch_size=subscribe_batch_size,
            subscribe_batch_pause_seconds=subscribe_batch_pause_seconds,
            max_ticks_per_symbol=max_ticks_per_symbol,
            max_bidasks_per_symbol=max_bidasks_per_symbol,
        )

    def _streaming_snapshot_fallback(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        fallback = self.get_stock_snapshots(symbols)
        for item in fallback.values():
            item["source"] = "shioaji_streaming_fallback_snapshot"
            item["streaming_fallback"] = True
            item["quote_buffer_status"] = "fallback_snapshot"
        return fallback

    @staticmethod
    def _set_quote_event_callback(quote: Any, callback: Any) -> str | None:
        for name in ("set_event_callback", "set_on_event_callback"):
            setter = getattr(quote, name, None)
            if callable(setter):
                try:
                    setter(callback)
                    return name
                except TypeError:
                    continue
        on_event = getattr(quote, "on_event", None)
        if callable(on_event):
            try:
                on_event(callback)
                return "on_event"
            except TypeError:
                try:
                    on_event()(callback)
                    return "on_event"
                except TypeError:
                    return None
        return None

    @staticmethod
    def _pause_for_quote_rate_limit(
        call_count: int,
        *,
        total_calls: int,
        batch_size: int,
        pause_seconds: float,
    ) -> None:
        safe_batch_size = max(1, int(batch_size or 1))
        if call_count > 0 and call_count < int(total_calls) and call_count % safe_batch_size == 0:
            time_module.sleep(max(0.0, float(pause_seconds or 0.0)))

    def fetch_attention_stocks(self, trade_date: date | None = None) -> list[dict[str, Any]]:
        if self.api is None:
            self.login()
        notice_fn = getattr(self.api, "notice", None)
        if not callable(notice_fn):
            raise RuntimeError("shioaji notice API is unavailable")
        payload = self._serialize(notice_fn())
        source = payload if isinstance(payload, dict) else {}
        codes = list(source.get("code") or [])
        updated_at = list(source.get("updated_at") or [])
        closes = list(source.get("close") or [])
        reasons = list(source.get("reason") or [])
        announced_dates = list(source.get("announced_date") or [])
        sync_date = trade_date or datetime.now().date()
        rows: list[dict[str, Any]] = []
        for idx, raw_code in enumerate(codes):
            symbol = self._normalize_symbol(str(raw_code or ""))
            if not symbol:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": sync_date,
                    "announced_date": self._coerce_date(self._list_get(announced_dates, idx), fallback=sync_date),
                    "updated_at": self._coerce_datetime(self._list_get(updated_at, idx)),
                    "close": self._optional_float(self._list_get(closes, idx)) or 0.0,
                    "reason": str(self._list_get(reasons, idx) or ""),
                    "source": "shioaji_notice",
                }
            )
        return rows

    @staticmethod
    def _list_get(values: list[Any], idx: int) -> Any:
        return values[idx] if 0 <= idx < len(values) else None

    @staticmethod
    def _coerce_date(value: Any, *, fallback: date) -> date:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        try:
            return datetime.fromisoformat(str(value)).date()
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.min)
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _resolve_constant(module: Any, enum_name: str, member_name: str, fallback: str):
        const = getattr(getattr(module, "constant", None), enum_name, None)
        if const is None:
            return fallback
        return getattr(const, member_name, fallback)

    def _build_order(self, *, price: float, qty: int, account: Any, use_odd_lot: bool, side: str = "buy"):
        try:
            import shioaji as sj
        except Exception:  # noqa: BLE001
            sj = None

        action_buy = self._resolve_constant(sj, "Action", "Buy", "Buy")
        action_sell = self._resolve_constant(sj, "Action", "Sell", "Sell")
        price_type_lmt = self._resolve_constant(sj, "StockPriceType", "LMT", "LMT")
        order_type_rod = self._resolve_constant(sj, "OrderType", "ROD", "ROD")
        lot_enum = "IntradayOdd" if use_odd_lot else "Common"
        order_lot = self._resolve_constant(sj, "StockOrderLot", lot_enum, lot_enum)

        order_kwargs = {
            "price": float(price),
            "quantity": int(qty),
            "action": action_sell if str(side).lower() == "sell" else action_buy,
            "price_type": price_type_lmt,
            "order_type": order_type_rod,
            "account": account,
        }

        if use_odd_lot:
            order_kwargs["order_lot"] = order_lot
        try:
            return self.api.Order(**order_kwargs)
        except TypeError:
            order_kwargs.pop("order_lot", None)
            try:
                return self.api.Order(**order_kwargs)
            except TypeError:
                order_kwargs.pop("account", None)
                return self.api.Order(**order_kwargs)

    def _build_futures_order(self, *, price: float, qty: int, account: Any, side: str = "buy"):
        try:
            import shioaji as sj
        except Exception:  # noqa: BLE001
            sj = None

        action_buy = self._resolve_constant(sj, "Action", "Buy", "Buy")
        action_sell = self._resolve_constant(sj, "Action", "Sell", "Sell")
        price_type_lmt = self._resolve_constant(sj, "FuturesPriceType", "LMT", "LMT")
        order_type_rod = self._resolve_constant(sj, "OrderType", "ROD", "ROD")
        octype_auto = self._resolve_constant(sj, "FuturesOCType", "Auto", "Auto")
        order_kwargs = {
            "price": float(price),
            "quantity": int(qty),
            "action": action_sell if str(side).lower() == "sell" else action_buy,
            "price_type": price_type_lmt,
            "order_type": order_type_rod,
            "octype": octype_auto,
            "account": account,
        }
        try:
            return self.api.Order(**order_kwargs)
        except TypeError:
            order_kwargs.pop("account", None)
            return self.api.Order(**order_kwargs)

    @staticmethod
    def _extract_trade_status(trade: Any) -> str:
        if trade is None:
            return "UNKNOWN"
        status_obj = getattr(trade, "status", None)
        if status_obj is None:
            return "UNKNOWN"
        status = getattr(status_obj, "status", None)
        if status:
            return str(status)
        return str(status_obj) if status_obj is not None else "UNKNOWN"

    @staticmethod
    def _serialize(obj: Any):
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, list):
            return [ShioajiGateway._serialize(item) for item in obj]
        if isinstance(obj, tuple):
            return [ShioajiGateway._serialize(item) for item in obj]
        if isinstance(obj, dict):
            return redact_sensitive({key: ShioajiGateway._serialize(value) for key, value in obj.items()})
        if hasattr(obj, "_asdict"):
            try:
                return redact_sensitive(ShioajiGateway._serialize(obj._asdict()))
            except Exception:
                return str(obj)
        if hasattr(obj, "__dict__"):
            try:
                return redact_sensitive(ShioajiGateway._serialize(vars(obj)))
            except Exception:
                return str(obj)
        return str(obj)

    def safe_place_stock_order(
        self,
        symbol: str,
        current_price: float,
        side: str = "buy",
        quantity: int | None = None,
        use_odd_lot: bool = True,
        enforce_session_check: bool | None = None,
        data_quality: DataQualityStatus | None = None,
        require_chip: bool = False,
        position_quantity: int | None = None,
        reference_price: float | None = None,
        pending_settlement_amount: float = 0.0,
        is_disposition_or_attention: bool = False,
        avg_volume_20: float | None = None,
        portfolio_equity: float | None = None,
        current_position_value: float = 0.0,
        current_portfolio_exposure: float | None = None,
        daily_pnl: float = 0.0,
        max_price_deviation_ticks: int = 3,
        max_price_deviation_pct: float = 0.01,
        min_avg_volume_20: float = 500_000.0,
        max_single_position_pct: float = 0.10,
        max_portfolio_exposure_pct: float = 0.95,
        max_daily_loss_pct: float = 0.02,
    ) -> dict[str, Any]:
        """
        Absolute capital guard (strict):
        - first check with current live balance
        - second check immediately before place_order
        """
        if self.api is None:
            self.login()

        checks: list[dict[str, Any]] = []
        normalized_side = str(side or "buy").strip().lower()
        if normalized_side not in {"buy", "sell"}:
            raise PreTradeCheckError("pre-trade check rejected: unsupported_side")

        live_unlock = validate_live_order_unlock(
            LiveOrderUnlockConfig(
                simulation=bool(self.config.simulation),
                allow_live_order=bool(self.config.allow_live_order),
                live_order_nonce=str(self.config.live_order_nonce or ""),
            )
        )
        checks.append({"name": "live_order_unlock", "passed": bool(live_unlock.accepted), "reason": live_unlock.reason})
        if not live_unlock.accepted:
            raise PreTradeCheckError(f"pre-trade check rejected: {live_unlock.reason}")

        quality = data_quality or DataQualityStatus()
        data_quality_ok = not (not self.config.simulation and quality.blocks_live_order(require_chip=require_chip))
        checks.append({"name": "data_quality", "passed": bool(data_quality_ok), "reason": quality.status})
        checks.append({"name": "data_freshness", "passed": bool(data_quality_ok), "reason": quality.freshness_status})
        if not data_quality_ok:
            raise PreTradeCheckError(f"pre-trade check rejected: {quality.status}")

        enforce_session = self.config.enforce_session_check if enforce_session_check is None else bool(enforce_session_check)
        session_ok = (not enforce_session) or self._is_trading_session()
        checks.append({"name": "trading_session", "passed": bool(session_ok), "enforced": bool(enforce_session)})
        if not session_ok:
            raise PreTradeCheckError("pre-trade check rejected: trading_session_closed")

        tick_ok = self._is_valid_tick(float(current_price))
        checks.append({"name": "tick_size", "passed": bool(tick_ok)})
        if not tick_ok:
            raise PreTradeCheckError("pre-trade check rejected: invalid_tick_size")

        if reference_price is not None and float(reference_price) > 0:
            tick_distance = self._tick_distance(float(current_price), float(reference_price))
            pct_distance = abs(float(current_price) - float(reference_price)) / float(reference_price)
            deviation_ok = tick_distance <= int(max_price_deviation_ticks) and pct_distance <= float(max_price_deviation_pct)
        else:
            tick_distance = 0
            pct_distance = 0.0
            deviation_ok = True
        checks.append(
            {
                "name": "price_deviation_guard",
                "passed": bool(deviation_ok),
                "reference_price": float(reference_price) if reference_price is not None else None,
                "tick_distance": int(tick_distance),
                "pct_distance": float(pct_distance),
            }
        )
        if not deviation_ok:
            raise PreTradeCheckError("pre-trade check rejected: price_deviation_guard")

        if not self._check_daily_order_limit():
            checks.append({"name": "daily_order_limit", "passed": False})
            raise PreTradeCheckError("pre-trade check rejected: daily_order_limit")
        checks.append({"name": "daily_order_limit", "passed": True})

        if normalized_side == "buy":
            first_available_cash, first_cash_source = self._get_available_cash_for_order()
            settlement_adjusted_cash = float(first_available_cash) - max(0.0, float(pending_settlement_amount or 0.0))
            if quantity is not None:
                approved_qty = int(quantity)
                approved_shares = self._stock_order_quantity_to_shares(approved_qty, use_odd_lot=use_odd_lot)
                order_value = float(approved_shares) * float(current_price)
                estimated_total_cost = estimate_buy_total_cost(order_value)
                capital_reason = "ok" if approved_qty > 0 and estimated_total_cost <= settlement_adjusted_cash else "capital_guard_rejected"
                capital_ok = approved_qty > 0 and estimated_total_cost <= settlement_adjusted_cash
            elif use_odd_lot:
                check = enforce_absolute_capital_guard(available_cash=settlement_adjusted_cash, current_price=float(current_price))
                approved_qty = int(check.qty)
                approved_shares = approved_qty
                estimated_total_cost = float(check.estimated_total_cost)
                capital_reason = str(check.reason)
                capital_ok = bool(check.accepted)
            else:
                one_lot_cost = estimate_buy_total_cost(float(current_price) * 1000.0)
                approved_qty = int(settlement_adjusted_cash // one_lot_cost) if one_lot_cost > 0 else 0
                approved_shares = self._stock_order_quantity_to_shares(approved_qty, use_odd_lot=use_odd_lot)
                estimated_total_cost = estimate_buy_total_cost(float(approved_shares) * float(current_price))
                capital_ok = approved_qty > 0 and estimated_total_cost <= settlement_adjusted_cash
                capital_reason = "ok" if capital_ok else "capital_guard_rejected"
        else:
            first_available_cash = None
            first_cash_source = "sell_order_no_cash_required"
            settlement_adjusted_cash = None
            approved_qty = int(quantity or 0)
            approved_shares = self._stock_order_quantity_to_shares(approved_qty, use_odd_lot=use_odd_lot)
            estimated_total_cost = 0.0
            capital_reason = "sell_order_no_cash_required"
            capital_ok = approved_shares > 0
        checks.append(
            {
                "name": "capital_guard_first",
                "passed": bool(capital_ok),
                "available_cash": float(first_available_cash) if first_available_cash is not None else None,
                "cash_source": first_cash_source,
                "settlement_adjusted_cash": float(settlement_adjusted_cash) if settlement_adjusted_cash is not None else None,
                "qty": int(approved_qty),
                "share_quantity": int(approved_shares),
                "estimated_total_cost": float(estimated_total_cost),
                "reason": capital_reason,
            }
        )
        if not capital_ok:
            logger.error(
                "pre-trade rejected before order build: symbol=%s price=%s reason=%s cash=%s",
                symbol,
                current_price,
                capital_reason,
                first_available_cash,
            )
            raise PreTradeCheckError(f"pre-trade check rejected: {capital_reason}")

        contract = self._get_stock_contract(symbol)
        band_ok = self._is_within_price_band(contract, float(current_price))
        checks.append({"name": "price_band", "passed": bool(band_ok)})
        if not band_ok:
            raise PreTradeCheckError("pre-trade check rejected: price_out_of_band")

        limit_up = getattr(contract, "limit_up", None)
        limit_down = getattr(contract, "limit_down", None)
        limit_fillable = True
        limit_reason = "ok"
        if normalized_side == "buy" and isinstance(limit_up, (int, float)) and float(current_price) >= float(limit_up):
            limit_fillable = False
            limit_reason = "limit_up_buy_blocked"
        if normalized_side == "sell" and isinstance(limit_down, (int, float)) and float(current_price) <= float(limit_down):
            limit_fillable = False
            limit_reason = "limit_down_sell_blocked"
        checks.append({"name": "limit_up_down_fillability", "passed": bool(limit_fillable), "reason": limit_reason})
        if not limit_fillable:
            raise PreTradeCheckError(f"pre-trade check rejected: {limit_reason}")

        duplicate_ok = not self._is_duplicate_order(symbol=symbol, qty=int(approved_qty), price=float(current_price))
        checks.append({"name": "duplicate_order", "passed": bool(duplicate_ok)})
        if not duplicate_ok:
            raise PreTradeCheckError("pre-trade check rejected: duplicate_order")

        if normalized_side == "sell":
            position_ok = position_quantity is None or int(position_quantity) >= int(approved_shares)
        else:
            position_ok = True
        checks.append(
            {
                "name": "position_quantity_for_sell",
                "passed": bool(position_ok),
                "position_quantity": int(position_quantity) if position_quantity is not None else None,
                "requested_quantity": int(approved_qty),
                "requested_shares": int(approved_shares),
            }
        )
        if not position_ok:
            raise PreTradeCheckError("pre-trade check rejected: position_quantity_for_sell")

        second_cash_source = "sell_order_no_cash_required"
        if normalized_side == "buy":
            second_available_cash, second_cash_source = self._get_available_cash_for_order()
        else:
            second_available_cash = None
        second_adjusted_cash = (
            float(second_available_cash) - max(0.0, float(pending_settlement_amount or 0.0))
            if second_available_cash is not None
            else None
        )
        second_capital_ok = normalized_side == "sell" or float(estimated_total_cost) <= float(second_adjusted_cash or 0.0)
        checks.append(
            {
                "name": "capital_guard_second",
                "passed": bool(second_capital_ok),
                "available_cash": float(second_available_cash) if second_available_cash is not None else None,
                "cash_source": second_cash_source,
                "settlement_adjusted_cash": float(second_adjusted_cash) if second_adjusted_cash is not None else None,
                "estimated_total_cost": float(estimated_total_cost),
            }
        )
        if not second_capital_ok:
            logger.error(
                "pre-trade rejected by second balance check: symbol=%s est_cost=%.2f cash=%.2f",
                symbol,
                estimated_total_cost,
                second_available_cash,
            )
            raise PreTradeCheckError("estimated total cost exceeds live available cash")

        disposition_ok = self.config.simulation or not bool(is_disposition_or_attention)
        checks.append({"name": "disposition_attention_guard", "passed": bool(disposition_ok)})
        if not disposition_ok:
            raise PreTradeCheckError("pre-trade check rejected: disposition_attention_guard")

        liquidity_ok = self.config.simulation or avg_volume_20 is None or float(avg_volume_20) >= float(min_avg_volume_20)
        checks.append(
            {
                "name": "liquidity_guard",
                "passed": bool(liquidity_ok),
                "avg_volume_20": float(avg_volume_20) if avg_volume_20 is not None else None,
                "min_avg_volume_20": float(min_avg_volume_20),
            }
        )
        if not liquidity_ok:
            raise PreTradeCheckError("pre-trade check rejected: liquidity_guard")

        if portfolio_equity is not None and float(portfolio_equity) > 0:
            projected_position_value = max(0.0, float(current_position_value or 0.0))
            if normalized_side == "buy":
                projected_position_value += float(approved_shares) * float(current_price)
            else:
                projected_position_value = max(0.0, projected_position_value - float(approved_shares) * float(current_price))
            single_position_ok = projected_position_value <= float(portfolio_equity) * float(max_single_position_pct)
        else:
            projected_position_value = None
            single_position_ok = True
        checks.append(
            {
                "name": "single_position_limit",
                "passed": bool(single_position_ok),
                "projected_position_value": projected_position_value,
                "portfolio_equity": float(portfolio_equity) if portfolio_equity is not None else None,
            }
        )
        if not single_position_ok:
            raise PreTradeCheckError("pre-trade check rejected: single_position_limit")

        if portfolio_equity is not None and float(portfolio_equity) > 0:
            daily_loss_ok = float(daily_pnl) >= -(float(portfolio_equity) * float(max_daily_loss_pct))
        else:
            daily_loss_ok = True
        checks.append(
            {
                "name": "daily_loss_limit",
                "passed": bool(daily_loss_ok),
                "daily_pnl": float(daily_pnl),
                "portfolio_equity": float(portfolio_equity) if portfolio_equity is not None else None,
            }
        )
        if not daily_loss_ok:
            raise PreTradeCheckError("pre-trade check rejected: daily_loss_limit")

        if portfolio_equity is not None and float(portfolio_equity) > 0 and current_portfolio_exposure is not None:
            projected_portfolio_exposure = max(0.0, float(current_portfolio_exposure))
            if normalized_side == "buy":
                projected_portfolio_exposure += float(approved_shares) * float(current_price)
            else:
                projected_portfolio_exposure = max(
                    0.0,
                    projected_portfolio_exposure - float(approved_shares) * float(current_price),
                )
            max_exposure_value = float(portfolio_equity) * float(max_portfolio_exposure_pct)
            portfolio_exposure_ok = projected_portfolio_exposure <= max_exposure_value
            portfolio_exposure_reason = "checked"
        else:
            projected_portfolio_exposure = None
            max_exposure_value = None
            portfolio_exposure_ok = True
            portfolio_exposure_reason = "metadata_missing"
        checks.append(
            {
                "name": "max_portfolio_exposure",
                "passed": bool(portfolio_exposure_ok),
                "reason": portfolio_exposure_reason,
                "portfolio_equity": float(portfolio_equity) if portfolio_equity is not None else None,
                "current_portfolio_exposure": (
                    float(current_portfolio_exposure) if current_portfolio_exposure is not None else None
                ),
                "projected_portfolio_exposure": (
                    float(projected_portfolio_exposure) if projected_portfolio_exposure is not None else None
                ),
                "max_portfolio_exposure_pct": float(max_portfolio_exposure_pct),
                "limit_value": float(max_exposure_value) if max_exposure_value is not None else None,
            }
        )
        if not portfolio_exposure_ok:
            raise PreTradeCheckError("pre-trade check rejected: max_portfolio_exposure")

        checks.extend(
            [
                {"name": "position_limit", "passed": bool(position_ok), "reason": "checked"},
                {"name": "disposition_stock_block", "passed": bool(disposition_ok), "reason": "checked"},
                {"name": "liquidity_threshold", "passed": bool(liquidity_ok), "reason": "checked"},
            ]
        )

        account = getattr(self.api, "stock_account", None)
        if account is None:
            raise RuntimeError("stock account not found")
        order = self._build_order(
            price=float(current_price),
            qty=int(approved_qty),
            account=account,
            use_odd_lot=use_odd_lot,
            side=normalized_side,
        )

        trade = self.api.place_order(contract, order)
        self._increase_daily_order_count()
        self._mark_order_seen(symbol=symbol, qty=int(approved_qty), price=float(current_price))
        status = self._extract_trade_status(trade)
        return {
            "passed": True,
            "symbol": self._normalize_symbol(symbol),
            "side": normalized_side,
            "price": float(current_price),
            "qty": int(approved_qty),
            "share_quantity": int(approved_shares),
            "estimated_total_cost": float(estimated_total_cost),
            "available_cash_before": float(first_available_cash) if first_available_cash is not None else None,
            "available_cash_before_order": float(second_available_cash) if second_available_cash is not None else None,
            "settlement_adjusted_cash": float(second_adjusted_cash) if second_adjusted_cash is not None else None,
            "simulation": self.config.simulation,
            "order_lot": "IntradayOdd" if use_odd_lot else "Common",
            "status": status,
            "data_quality_status": quality.status,
            "pretrade_checks": checks,
            "trade": self._serialize(trade),
        }

    def safe_place_futures_order(
        self,
        symbol: str,
        current_price: float,
        side: str = "buy",
        quantity: int | None = None,
        enforce_session_check: bool | None = None,
        data_quality: DataQualityStatus | None = None,
        require_chip: bool = False,
    ) -> dict[str, Any]:
        if self.api is None:
            self.login()

        checks: list[dict[str, Any]] = []
        normalized_side = str(side or "buy").strip().lower()
        if normalized_side not in {"buy", "sell"}:
            raise PreTradeCheckError("pre-trade check rejected: unsupported_side")

        live_unlock = validate_live_order_unlock(
            LiveOrderUnlockConfig(
                simulation=bool(self.config.simulation),
                allow_live_order=bool(self.config.allow_live_order),
                live_order_nonce=str(self.config.live_order_nonce or ""),
            )
        )
        checks.append({"name": "live_order_unlock", "passed": bool(live_unlock.accepted), "reason": live_unlock.reason})
        if not live_unlock.accepted:
            raise PreTradeCheckError(f"pre-trade check rejected: {live_unlock.reason}")

        quality = data_quality or DataQualityStatus()
        data_quality_ok = not (not self.config.simulation and quality.blocks_live_order(require_chip=require_chip))
        checks.append({"name": "data_quality", "passed": bool(data_quality_ok), "reason": quality.status})
        checks.append({"name": "data_freshness", "passed": bool(data_quality_ok), "reason": quality.freshness_status})
        if not data_quality_ok:
            raise PreTradeCheckError(f"pre-trade check rejected: {quality.status}")

        enforce_session = self.config.enforce_session_check if enforce_session_check is None else bool(enforce_session_check)
        session_ok = (not enforce_session) or self._is_trading_session()
        checks.append({"name": "trading_session", "passed": bool(session_ok), "enforced": bool(enforce_session)})
        if not session_ok:
            raise PreTradeCheckError("pre-trade check rejected: trading_session_closed")

        price_ok = float(current_price) > 0
        checks.append({"name": "tick_size", "passed": bool(price_ok), "reason": "positive_price"})
        if not price_ok:
            raise PreTradeCheckError("pre-trade check rejected: invalid_price")

        if not self._check_daily_order_limit():
            checks.append({"name": "daily_order_limit", "passed": False})
            raise PreTradeCheckError("pre-trade check rejected: daily_order_limit")
        checks.append({"name": "daily_order_limit", "passed": True})

        approved_qty = int(quantity or 0)
        quantity_ok = approved_qty > 0
        checks.append({"name": "capital_guard_first", "passed": bool(quantity_ok), "reason": "futures_margin_external"})
        if not quantity_ok:
            raise PreTradeCheckError("pre-trade check rejected: invalid_quantity")

        contract = self._get_futures_contract(symbol)
        band_ok = self._is_within_price_band(contract, float(current_price))
        checks.append({"name": "price_band", "passed": bool(band_ok)})
        if not band_ok:
            raise PreTradeCheckError("pre-trade check rejected: price_out_of_band")

        duplicate_ok = not self._is_duplicate_order(symbol=symbol, qty=int(approved_qty), price=float(current_price))
        checks.append({"name": "duplicate_order", "passed": bool(duplicate_ok)})
        if not duplicate_ok:
            raise PreTradeCheckError("pre-trade check rejected: duplicate_order")

        checks.append({"name": "capital_guard_second", "passed": True, "reason": "futures_margin_external"})
        checks.extend(
            [
                {"name": "position_limit", "passed": True, "reason": "not_enforced"},
                {"name": "max_portfolio_exposure", "passed": True, "reason": "not_enforced"},
                {"name": "disposition_stock_block", "passed": True, "reason": "not_applicable"},
                {"name": "liquidity_threshold", "passed": True, "reason": "not_enforced"},
            ]
        )

        account = getattr(self.api, "futopt_account", None) or getattr(self.api, "futures_account", None)
        if account is None:
            raise RuntimeError("futures account not found")
        order = self._build_futures_order(
            price=float(current_price),
            qty=int(approved_qty),
            account=account,
            side=normalized_side,
        )
        trade = self.api.place_order(contract, order)
        self._increase_daily_order_count()
        self._mark_order_seen(symbol=symbol, qty=int(approved_qty), price=float(current_price))
        status = self._extract_trade_status(trade)
        return {
            "passed": True,
            "symbol": self._normalize_symbol(symbol),
            "side": normalized_side,
            "price": float(current_price),
            "qty": int(approved_qty),
            "estimated_total_cost": 0.0,
            "available_cash_before": None,
            "available_cash_before_order": None,
            "simulation": self.config.simulation,
            "order_lot": "Futures",
            "status": status,
            "data_quality_status": quality.status,
            "pretrade_checks": checks,
            "trade": self._serialize(trade),
        }

    def execute_intent(
        self,
        intent: OrderIntent,
        *,
        data_quality: DataQualityStatus | None = None,
        require_chip: bool = False,
    ) -> ExecutionResult:
        try:
            metadata = dict(intent.metadata or {})
            if str(intent.metadata.get("instrument_type") or "").lower() == "futures":
                payload = self.safe_place_futures_order(
                    symbol=intent.symbol,
                    current_price=float(intent.price),
                    side=intent.side,
                    quantity=int(intent.quantity) if intent.quantity is not None else None,
                    data_quality=data_quality,
                    require_chip=require_chip,
                )
            else:
                payload = self.safe_place_stock_order(
                    symbol=intent.symbol,
                    current_price=float(intent.price),
                    side=intent.side,
                    quantity=int(intent.quantity) if intent.quantity is not None else None,
                    use_odd_lot=(intent.order_lot == "IntradayOdd"),
                    data_quality=data_quality,
                    require_chip=require_chip,
                    position_quantity=self._optional_int(metadata.get("position_quantity")),
                    reference_price=self._optional_float(metadata.get("reference_price")),
                    pending_settlement_amount=float(metadata.get("pending_settlement_amount") or 0.0),
                    is_disposition_or_attention=bool(metadata.get("is_disposition_or_attention", False)),
                    avg_volume_20=self._optional_float(metadata.get("avg_volume_20")),
                    portfolio_equity=self._optional_float(metadata.get("portfolio_equity")),
                    current_position_value=float(metadata.get("current_position_value") or 0.0),
                    current_portfolio_exposure=self._metadata_portfolio_exposure(metadata),
                    daily_pnl=float(metadata.get("daily_pnl") or 0.0),
                    max_single_position_pct=self._positive_float_or_default(
                        metadata.get("max_single_position_pct"),
                        0.10,
                    ),
                    max_portfolio_exposure_pct=self._positive_float_or_default(
                        metadata.get("max_portfolio_exposure_pct"),
                        0.95,
                    ),
                    max_daily_loss_pct=self._positive_float_or_default(
                        metadata.get("max_daily_loss_pct"),
                        0.02,
                    ),
                )
            pretrade = PreTradeDecision(
                intent_id=intent.intent_id,
                accepted=True,
                reason="ok",
                checks=list(payload.get("pretrade_checks") or []),
                approved_quantity=int(payload.get("qty") or 0),
                estimated_total_cost=float(payload.get("estimated_total_cost") or 0.0),
                available_cash_before=float(payload.get("available_cash_before") or 0.0),
                data_quality_status=str(payload.get("data_quality_status") or "unknown"),
            )
            return ExecutionResult(
                intent_id=intent.intent_id,
                accepted=True,
                executed=True,
                environment="live" if intent.environment == "live" else "simulation",
                symbol=str(payload.get("symbol") or intent.symbol),
                side=str(payload.get("side") or intent.side),
                price=float(payload.get("price") or intent.price),
                quantity=int(payload.get("qty") or 0),
                status=str(payload.get("status") or "submitted"),
                broker_order_id=None,
                raw_trade=payload.get("trade"),
                pretrade=pretrade,
            )
        except PreTradeCheckError as exc:
            pretrade = PreTradeDecision(
                intent_id=intent.intent_id,
                accepted=False,
                reason=str(exc),
                checks=[{"name": "pre_trade", "passed": False, "reason": str(exc)}],
                data_quality_status=(data_quality or DataQualityStatus()).status,
            )
            return ExecutionResult(
                intent_id=intent.intent_id,
                accepted=False,
                executed=False,
                environment="live" if intent.environment == "live" else "simulation",
                symbol=intent.symbol,
                side=intent.side,
                price=float(intent.price),
                quantity=int(intent.quantity or 0),
                status="rejected",
                broker_order_id=None,
                raw_trade=None,
                pretrade=pretrade,
            )

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        return float(value)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        return int(value)

    @staticmethod
    def _stock_order_quantity_to_shares(quantity: int | float | None, *, use_odd_lot: bool) -> int:
        qty = max(0, int(quantity or 0))
        return qty if use_odd_lot else qty * 1000

    def _metadata_portfolio_exposure(self, metadata: dict[str, Any]) -> float | None:
        exposure = self._optional_float(metadata.get("current_portfolio_exposure"))
        if exposure is None:
            exposure = self._optional_float(metadata.get("portfolio_exposure"))
        return exposure

    def _positive_float_or_default(self, value: Any, default: float) -> float:
        parsed = self._optional_float(value)
        if parsed is None or parsed <= 0:
            return float(default)
        return float(parsed)

    def execute_stock_order_in_thread(
        self,
        symbol: str,
        current_price: float,
        use_odd_lot: bool = True,
        timeout_sec: int | None = None,
        enforce_session_check: bool | None = None,
    ) -> dict[str, Any]:
        """
        Execute one guarded order in a dedicated thread.
        If pre-trade check fails, log error and end that thread immediately.
        """
        result_box: dict[str, Any] = {}
        timeout = int(timeout_sec or self.config.order_timeout_sec)

        def _worker() -> None:
            try:
                result_box["result"] = self.safe_place_stock_order(
                    symbol=symbol,
                    current_price=float(current_price),
                    use_odd_lot=use_odd_lot,
                    enforce_session_check=enforce_session_check,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("order thread aborted: symbol=%s error=%s", symbol, exc)
                result_box["error"] = exc

        thread = threading.Thread(target=_worker, name=f"shioaji-order-{self._normalize_symbol(symbol)}", daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            raise TimeoutError(f"order thread timeout (> {timeout}s)")
        if "error" in result_box:
            raise result_box["error"]
        return result_box["result"]
