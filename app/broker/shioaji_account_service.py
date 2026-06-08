from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from app.security import redact_sensitive


@dataclass(frozen=True)
class StockPosition:
    symbol: str
    quantity: int
    price: float = 0.0
    market_value: float = 0.0
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive(asdict(self))


@dataclass(frozen=True)
class SettlementItem:
    date: str
    amount: float
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive(asdict(self))


@dataclass(frozen=True)
class OpenOrder:
    order_id: str
    symbol: str
    side: str
    price: float
    quantity: int
    status: str
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive(asdict(self))


@dataclass(frozen=True)
class AccountSnapshot:
    simulation: bool
    query_ok: bool
    stock_account: dict[str, Any] | None
    available_cash: float | None
    positions: list[StockPosition] = field(default_factory=list)
    settlements: list[SettlementItem] = field(default_factory=list)
    open_orders: list[OpenOrder] = field(default_factory=list)
    checked_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["positions"] = [item.to_dict() for item in self.positions]
        payload["settlements"] = [item.to_dict() for item in self.settlements]
        payload["open_orders"] = [item.to_dict() for item in self.open_orders]
        payload["checked_at"] = self.checked_at.isoformat()
        return redact_sensitive(payload)


@dataclass(frozen=True)
class ShioajiHealthCheck:
    simulation: bool
    query_ready: bool
    simulation_order_ready: bool
    live_order_ready: bool
    checks: list[dict[str, Any]]
    shioaji_version: str = "unknown"
    checked_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checked_at"] = self.checked_at.isoformat()
        return redact_sensitive(payload)


class ShioajiAccountService:
    def __init__(
        self,
        *,
        env_path: str = ".env",
        api_factory: Callable[[bool], Any] | None = None,
    ):
        self.env_path = env_path
        load_dotenv(env_path, override=False)
        self.api_factory = api_factory

    def run_health_check(self, simulation: bool) -> ShioajiHealthCheck:
        checks: list[dict[str, Any]] = []
        api_key_ok = bool(os.getenv("SHIOAJI_APIKEY", "").strip())
        secret_ok = bool(os.getenv("SHIOAJI_SECRET", "").strip())
        ca_path = os.getenv("SHIOAJI_CA_PATH", "").strip()
        ca_password_ok = bool(os.getenv("SHIOAJI_CA_PASSWORD", "").strip())
        ca_path_ok = bool(ca_path and Path(ca_path).expanduser().exists())
        checks.extend(
            [
                {"name": "api_key", "passed": api_key_ok},
                {"name": "secret", "passed": secret_ok},
                {"name": "ca_path", "passed": bool(simulation or ca_path_ok)},
                {"name": "ca_password", "passed": bool(simulation or ca_password_ok)},
            ]
        )

        api = None
        login_ok = False
        stock_account_ok = False
        shioaji_version = "unknown"
        if api_key_ok and secret_ok:
            try:
                api, shioaji_version = self._login(simulation=simulation)
                login_ok = True
                stock_account_ok = getattr(api, "stock_account", None) is not None
            except Exception as exc:  # noqa: BLE001
                checks.append({"name": "login", "passed": False, "reason": type(exc).__name__})
            finally:
                self._logout(api)
        checks.append({"name": "login", "passed": login_ok})
        checks.append({"name": "stock_account", "passed": stock_account_ok})

        query_ready = bool(api_key_ok and secret_ok and login_ok and stock_account_ok)
        simulation_order_ready = bool(query_ready)
        live_order_ready = bool(
            query_ready
            and not simulation
            and ca_path_ok
            and ca_password_ok
            and os.getenv("SHIOAJI_ENABLE_LIVE_ORDERS", "").strip() == "1"
        )
        return ShioajiHealthCheck(
            simulation=bool(simulation),
            query_ready=query_ready,
            simulation_order_ready=simulation_order_ready,
            live_order_ready=live_order_ready,
            checks=checks,
            shioaji_version=shioaji_version,
        )

    def get_account_snapshot(self, simulation: bool) -> AccountSnapshot:
        api = None
        try:
            api, _ = self._login(simulation=simulation)
            account = getattr(api, "stock_account", None)
            return AccountSnapshot(
                simulation=bool(simulation),
                query_ok=True,
                stock_account=self._serialize(account),
                available_cash=self._read_available_cash(api, account),
                positions=self.get_positions(simulation=simulation, api=api),
                settlements=self.get_settlements(simulation=simulation, api=api),
                open_orders=self.get_open_orders(simulation=simulation, api=api),
            )
        finally:
            self._logout(api)

    def get_positions(self, simulation: bool, api: Any | None = None) -> list[StockPosition]:
        own_api = api is None
        if api is None:
            api, _ = self._login(simulation=simulation)
        try:
            account = getattr(api, "stock_account", None)
            rows = self._call_account_api(api, "list_positions", account) or []
            return [self._position_from_raw(item) for item in rows]
        finally:
            if own_api:
                self._logout(api)

    def get_settlements(self, simulation: bool, api: Any | None = None) -> list[SettlementItem]:
        own_api = api is None
        if api is None:
            api, _ = self._login(simulation=simulation)
        try:
            account = getattr(api, "stock_account", None)
            rows = self._call_account_api(api, "settlements", account)
            if rows is None:
                rows = self._call_account_api(api, "list_settlements", account)
            return [self._settlement_from_raw(item) for item in (rows or [])]
        finally:
            if own_api:
                self._logout(api)

    def get_open_orders(self, simulation: bool, api: Any | None = None) -> list[OpenOrder]:
        own_api = api is None
        if api is None:
            api, _ = self._login(simulation=simulation)
        try:
            rows = self._call_account_api(api, "list_trades", getattr(api, "stock_account", None)) or []
            output: list[OpenOrder] = []
            for item in rows:
                data = self._serialize(item)
                status = str(data.get("status", data.get("order", {}).get("status", "")) if isinstance(data, dict) else "")
                if status.upper() in {"FILLED", "CANCELLED", "FAILED"}:
                    continue
                output.append(self._open_order_from_raw(data))
            return output
        finally:
            if own_api:
                self._logout(api)

    def _login(self, *, simulation: bool):
        if self.api_factory is not None:
            api = self.api_factory(bool(simulation))
            return api, getattr(api, "shioaji_version", "test")
        try:
            import shioaji as sj
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("shioaji is not installed") from exc

        api_key = os.getenv("SHIOAJI_APIKEY", "").strip()
        secret = os.getenv("SHIOAJI_SECRET", "").strip()
        if not api_key or not secret:
            raise RuntimeError("SHIOAJI_APIKEY/SHIOAJI_SECRET missing")
        api = sj.Shioaji(simulation=bool(simulation))
        api.login(api_key=api_key, secret_key=secret)
        if not simulation:
            ca_path = os.getenv("SHIOAJI_CA_PATH", "").strip()
            ca_password = os.getenv("SHIOAJI_CA_PASSWORD", "").strip()
            if ca_path and ca_password and hasattr(api, "activate_ca"):
                api.activate_ca(ca_path=ca_path, ca_passwd=ca_password)
        return api, getattr(sj, "__version__", "unknown")

    @staticmethod
    def _logout(api: Any | None) -> None:
        if api is not None and hasattr(api, "logout"):
            try:
                api.logout()
            except Exception:
                return

    @classmethod
    def _serialize(cls, obj: Any) -> Any:
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return redact_sensitive({str(k): cls._serialize(v) for k, v in obj.items()})
        if isinstance(obj, (list, tuple)):
            return [cls._serialize(item) for item in obj]
        if hasattr(obj, "_asdict"):
            return redact_sensitive(cls._serialize(obj._asdict()))
        if hasattr(obj, "__dict__"):
            return redact_sensitive(cls._serialize(vars(obj)))
        return redact_sensitive(str(obj))

    @classmethod
    def _read_available_cash(cls, api: Any, account: Any) -> float | None:
        for args, kwargs in (
            ((), {"account": account}),
            ((account,), {}),
            ((), {}),
        ):
            try:
                payload = api.account_balance(*args, **kwargs)
            except Exception:
                continue
            value = cls._pick_number(payload, ("acc_balance", "available_balance", "available_cash", "cash"))
            if value is not None:
                return value
        return None

    @classmethod
    def _call_account_api(cls, api: Any, method_name: str, account: Any):
        method = getattr(api, method_name, None)
        if method is None:
            return None
        for args, kwargs in (
            ((), {"account": account}),
            ((account,), {}),
            ((), {}),
        ):
            try:
                return method(*args, **kwargs)
            except TypeError:
                continue
            except Exception:
                return None
        return None

    @classmethod
    def _position_from_raw(cls, raw: Any) -> StockPosition:
        data = cls._serialize(raw)
        source = data if isinstance(data, dict) else {}
        symbol = str(source.get("code") or source.get("symbol") or source.get("stock_id") or "")
        quantity = int(float(source.get("quantity") or source.get("qty") or source.get("shares") or 0))
        price = float(source.get("price") or source.get("last_price") or source.get("pnl_price") or 0.0)
        market_value = float(source.get("market_value") or price * quantity)
        return StockPosition(symbol=symbol, quantity=quantity, price=price, market_value=market_value, raw=source)

    @classmethod
    def _settlement_from_raw(cls, raw: Any) -> SettlementItem:
        data = cls._serialize(raw)
        source = data if isinstance(data, dict) else {}
        raw_date = source.get("date") or source.get("settlement_date") or source.get("T") or ""
        amount = cls._pick_number(source, ("amount", "t_money", "t1_money", "t2_money", "money")) or 0.0
        return SettlementItem(date=str(raw_date), amount=float(amount), raw=source)

    @classmethod
    def _open_order_from_raw(cls, raw: Any) -> OpenOrder:
        source = raw if isinstance(raw, dict) else {}
        order = source.get("order") if isinstance(source.get("order"), dict) else {}
        contract = source.get("contract") if isinstance(source.get("contract"), dict) else {}
        return OpenOrder(
            order_id=str(source.get("id") or source.get("order_id") or order.get("id") or ""),
            symbol=str(source.get("symbol") or order.get("symbol") or contract.get("code") or ""),
            side=str(source.get("side") or order.get("action") or ""),
            price=float(source.get("price") or order.get("price") or 0.0),
            quantity=int(float(source.get("quantity") or order.get("quantity") or 0)),
            status=str(source.get("status") or order.get("status") or ""),
            raw=source,
        )

    @classmethod
    def _pick_number(cls, payload: Any, keys: tuple[str, ...]) -> float | None:
        data = cls._serialize(payload)
        if isinstance(data, (int, float)):
            return float(data)
        if isinstance(data, list):
            for item in data:
                value = cls._pick_number(item, keys)
                if value is not None:
                    return value
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, (int, float)):
                    return float(value)
        return None
