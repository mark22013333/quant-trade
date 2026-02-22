from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from dotenv import load_dotenv


class ShioajiGatewayError(RuntimeError):
    """Base error for gateway level failures."""


class ProductionPermissionError(ShioajiGatewayError):
    """Raised when token has no production permission."""


@dataclass
class LoginContext:
    simulation: bool
    shioaji_version: str
    production_permission: bool
    ca_activated: bool
    env_status: Dict[str, Any]
    accounts: List[Any]


class ShioajiGateway:
    REQUIRED_ENV_KEYS = ("SHIOAJI_APIKEY", "SHIOAJI_SECRET")
    OPTIONAL_ENV_KEYS = ("SHIOAJI_CA_PATH", "SHIOAJI_CA_PASSWORD", "SHIOAJI_CA_PERSON_ID")

    def __init__(self, env_path: Path):
        self.env_path = env_path

    def load_env(self) -> None:
        load_dotenv(self.env_path, override=False)

    def env_status(self) -> Dict[str, Any]:
        self.load_env()
        payload: Dict[str, Any] = {
            "ENV_PATH": str(self.env_path),
            "ENV_EXISTS": self.env_path.exists(),
        }
        for key in self.REQUIRED_ENV_KEYS + self.OPTIONAL_ENV_KEYS:
            payload[key] = bool(os.getenv(key))
        return payload

    def call_with_timeout(self, func, timeout_sec: int, *args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func, *args, **kwargs)
            try:
                return future.result(timeout=timeout_sec)
            except FutureTimeout as exc:
                raise ShioajiGatewayError(f"API 呼叫超時（>{timeout_sec}s）") from exc

    def login(self, *, simulation: bool, activate_ca: bool = True, timeout_sec: int = 20):
        self.load_env()
        api_key = os.getenv("SHIOAJI_APIKEY")
        secret_key = os.getenv("SHIOAJI_SECRET")
        if not api_key or not secret_key:
            raise ShioajiGatewayError("缺少 SHIOAJI_APIKEY / SHIOAJI_SECRET")

        try:
            import shioaji as sj
        except Exception as exc:  # noqa: BLE001
            raise ShioajiGatewayError(f"shioaji 未安裝或無法載入：{exc}") from exc

        api = sj.Shioaji(simulation=simulation)
        try:
            accounts = self.call_with_timeout(
                api.login,
                timeout_sec,
                api_key=api_key,
                secret_key=secret_key,
            )
            production_permission = True
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "Token doesn't have production permission" in message:
                raise ProductionPermissionError(
                    "此 API token 尚未開通正式盤權限。請到 https://www.sinotrade.com.tw/newweb/PythonAPIKey/ 開啟正式環境，重新產生 key 後更新 .env。"
                ) from exc
            raise ShioajiGatewayError(f"Shioaji 登入失敗：{message}") from exc

        ca_activated = self._activate_ca(api) if activate_ca else False
        context = LoginContext(
            simulation=simulation,
            shioaji_version=getattr(sj, "__version__", "unknown"),
            production_permission=production_permission,
            ca_activated=ca_activated,
            env_status=self.env_status(),
            accounts=list(accounts or []),
        )
        return api, context

    def _activate_ca(self, api) -> bool:
        ca_path = os.getenv("SHIOAJI_CA_PATH")
        ca_password = os.getenv("SHIOAJI_CA_PASSWORD")
        person_id = os.getenv("SHIOAJI_CA_PERSON_ID")
        if not ca_path or not ca_password:
            return False

        kwargs = {"ca_path": ca_path, "ca_passwd": ca_password}
        if person_id:
            kwargs["person_id"] = person_id
        try:
            self.call_with_timeout(api.activate_ca, 10, **kwargs)
            return True
        except TypeError:
            kwargs.pop("person_id", None)
            try:
                self.call_with_timeout(api.activate_ca, 10, **kwargs)
                return True
            except Exception:
                return False
        except Exception:
            return False

    def logout(self, api) -> None:
        if api is None:
            return
        try:
            api.logout()
        except Exception:
            return

    def pick_stock_account(self, api, accounts: Sequence[Any] | None = None):
        account = getattr(api, "stock_account", None)
        if account is not None:
            return account
        if accounts is None and hasattr(api, "list_accounts"):
            try:
                accounts = self.call_with_timeout(api.list_accounts, 10)
            except Exception:
                accounts = None
        return self._pick_account_by_type(accounts, ("stock", "securities"))

    def pick_futures_account(self, api, accounts: Sequence[Any] | None = None):
        account = getattr(api, "futopt_account", None)
        if account is not None:
            return account
        if accounts is None and hasattr(api, "list_accounts"):
            try:
                accounts = self.call_with_timeout(api.list_accounts, 10)
            except Exception:
                accounts = None
        return self._pick_account_by_type(accounts, ("future", "futopt"))

    @staticmethod
    def _pick_account_by_type(accounts: Sequence[Any] | None, keywords: Sequence[str]):
        if not accounts:
            return None
        for account in accounts:
            account_type = str(getattr(account, "account_type", "")).lower()
            if any(key in account_type for key in keywords):
                return account
        return accounts[0]

    def get_stock_contract(self, api, stock_code: str):
        code = self.normalize_stock_code(stock_code)
        stocks = getattr(getattr(api, "Contracts", None), "Stocks", None)
        if stocks is None:
            raise ShioajiGatewayError("無法讀取 Stocks 合約列表")

        for getter in (
            lambda: stocks[code],
            lambda: getattr(stocks, "TSE", {})[code],
            lambda: getattr(stocks, "OTC", {})[code],
        ):
            try:
                return getter()
            except Exception:
                continue
        raise ShioajiGatewayError(f"找不到股票合約：{code}")

    def get_futures_contract(self, api, futures_code: str | None):
        raw_code = (futures_code or "TXF").upper().strip()
        futures = getattr(getattr(api, "Contracts", None), "Futures", None)
        if futures is None:
            raise ShioajiGatewayError("無法讀取 Futures 合約列表")

        for candidate_code in (raw_code, self._to_current_month_alias(raw_code)):
            if not candidate_code:
                continue
            try:
                candidate = futures[candidate_code]
            except Exception:
                continue
            if self._is_orderable_futures_contract(candidate):
                return candidate

        product = "".join(ch for ch in raw_code if ch.isalpha()) or raw_code
        group = getattr(futures, product, None)
        candidates = self._collect_contracts(group)
        if not candidates:
            try:
                maybe_group = futures[product]
            except Exception:
                maybe_group = None
            candidates = self._collect_contracts(maybe_group)
        if not candidates:
            raise ShioajiGatewayError(f"找不到期貨合約：{raw_code}")

        orderable = [item for item in candidates if self._is_orderable_futures_contract(item)]
        filtered = [item for item in orderable if not str(getattr(item, "code", "")).endswith(("R1", "R2"))]
        pool = filtered or candidates
        pool.sort(key=self._futures_sort_key)
        if not pool:
            raise ShioajiGatewayError(f"找不到可下單期貨合約：{raw_code}")
        return pool[0]

    @staticmethod
    def _to_current_month_alias(raw_code: str) -> str | None:
        if not raw_code:
            return None
        if raw_code.endswith("C0"):
            return raw_code
        # TXF -> TXFC0, MXF -> MXFC0
        if raw_code.isalpha() and 2 <= len(raw_code) <= 4:
            return f"{raw_code}C0"
        return None

    @staticmethod
    def _is_orderable_futures_contract(contract) -> bool:
        if contract is None:
            return False
        cls_name = type(contract).__name__
        # Stream contract is for quote subscription, not order placement.
        if "Stream" in cls_name or "MultiContract" in cls_name:
            return False
        code = getattr(contract, "code", None)
        symbol = getattr(contract, "symbol", None)
        return bool(code and symbol)

    @staticmethod
    def _collect_contracts(value) -> List[Any]:
        if value is None:
            return []
        if isinstance(value, dict):
            return list(value.values())
        if isinstance(value, (list, tuple)):
            return list(value)
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
            try:
                return list(value)
            except Exception:
                return [value]
        if hasattr(value, "__dict__"):
            nested = [v for v in vars(value).values() if v is not None]
            if nested:
                return nested
        return [value]

    @staticmethod
    def _futures_sort_key(contract) -> tuple[str, str]:
        delivery_date = str(getattr(contract, "delivery_date", "9999/99/99")).replace("/", "").replace("-", "")
        code = str(getattr(contract, "code", "ZZZZ"))
        return delivery_date, code

    @staticmethod
    def normalize_stock_code(stock_code: str) -> str:
        code = (stock_code or "").strip().upper()
        for suffix in (".TW", ".TWO"):
            if code.endswith(suffix):
                code = code[: -len(suffix)]
        return code

    @staticmethod
    def pick_reference_price(contract, fallback: float = 1.0) -> float:
        for key in ("reference", "close", "limit_down", "limit_up"):
            value = getattr(contract, key, None)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        return float(fallback)

    @staticmethod
    def extract_trade_status(trade) -> str:
        if trade is None:
            return "UNKNOWN"
        status_obj = getattr(trade, "status", None)
        if status_obj is None:
            return "UNKNOWN"
        value = getattr(status_obj, "status", None)
        if value:
            return str(value)
        text = str(status_obj)
        return text if text else "UNKNOWN"

    @staticmethod
    def as_dict(obj) -> Dict[str, Any]:
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "_asdict"):
            try:
                return obj._asdict()
            except Exception:
                return {}
        if hasattr(obj, "data"):
            try:
                data = obj.data()
                if isinstance(data, dict):
                    return data
            except Exception:
                return {}
        if hasattr(obj, "__dict__"):
            return dict(vars(obj))
        return {}

    @classmethod
    def serialize(cls, obj):
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, list):
            return [cls.serialize(item) for item in obj]
        if isinstance(obj, tuple):
            return [cls.serialize(item) for item in obj]
        if isinstance(obj, dict):
            return {key: cls.serialize(value) for key, value in obj.items()}
        if hasattr(obj, "_asdict"):
            try:
                return cls.serialize(obj._asdict())
            except Exception:
                return str(obj)
        if hasattr(obj, "__dict__"):
            try:
                return cls.serialize(vars(obj))
            except Exception:
                return str(obj)
        return str(obj)
