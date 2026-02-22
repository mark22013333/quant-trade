from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

from .shioaji_gateway import ProductionPermissionError, ShioajiGateway, ShioajiGatewayError


def _mode_text(simulation: bool) -> str:
    return "模擬帳戶" if simulation else "正式帳戶"


@dataclass
class OrderTestConfig:
    simulation: bool
    stock_code: str = "2890"
    stock_quantity: int = 1
    stock_price: float | None = None
    futures_code: str = "TXF"
    futures_quantity: int = 1
    futures_price: float | None = None
    interval_sec: float = 1.1
    allow_live_order: bool = False


class ShioajiWorkflowService:
    def __init__(self, gateway: ShioajiGateway):
        self.gateway = gateway

    @contextmanager
    def _session(self, *, simulation: bool, activate_ca: bool):
        api = None
        try:
            api, context = self.gateway.login(simulation=simulation, activate_ca=activate_ca)
            yield api, context
        finally:
            if api is not None:
                self.gateway.logout(api)

    def run_login_test(self, simulation: bool) -> Dict[str, Any]:
        try:
            with self._session(simulation=simulation, activate_ca=not simulation) as (api, context):
                stock_account = self.gateway.pick_stock_account(api, context.accounts)
                futures_account = self.gateway.pick_futures_account(api, context.accounts)
                mode = _mode_text(simulation)
                return {
                    "passed": True,
                    "mode": mode,
                    "simulation": simulation,
                    "message": f"{mode}登入成功",
                    "accounts_count": len(context.accounts),
                    "stock_account": self.gateway.serialize(stock_account),
                    "futures_account": self.gateway.serialize(futures_account),
                    "ca_activated": context.ca_activated,
                    "production_permission": context.production_permission,
                    "shioaji_version": context.shioaji_version,
                    "accounts": self.gateway.serialize(context.accounts),
                }
        except ProductionPermissionError as exc:
            return {
                "passed": False,
                "simulation": simulation,
                "message": str(exc),
                "error": "production_permission",
                "next_steps": [
                    "到永豐 API Key 管理頁開啟正式環境權限",
                    "重新產生 key/secret 並更新到 .env",
                    "重啟控制台後再次執行正式環境檢核",
                ],
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "passed": False,
                "simulation": simulation,
                "message": str(exc),
                "error": "login_failed",
            }

    def run_stock_order_test(self, config: OrderTestConfig) -> Dict[str, Any]:
        if not config.simulation and not config.allow_live_order:
            return {
                "passed": False,
                "simulation": False,
                "error": "live_order_locked",
                "message": "正式環境下單測試已鎖定。若要送出真單，請勾選允許正式下單。",
                "next_steps": [
                    "先在模擬模式完成測試流程",
                    "確認正式環境檢核已通過",
                    "確認無誤後再勾選允許正式下單",
                ],
            }
        try:
            with self._session(simulation=config.simulation, activate_ca=not config.simulation) as (api, context):
                result = self._execute_stock_order(
                    api=api,
                    stock_code=config.stock_code,
                    quantity=config.stock_quantity,
                    order_price=config.stock_price,
                )
                result["simulation"] = config.simulation
                result["mode"] = _mode_text(config.simulation)
                result["ca_activated"] = context.ca_activated
                result["production_permission"] = context.production_permission
                return result
        except Exception as exc:  # noqa: BLE001
            return {
                "passed": False,
                "simulation": config.simulation,
                "error": "stock_order_failed",
                "message": str(exc),
            }

    def run_futures_order_test(self, config: OrderTestConfig) -> Dict[str, Any]:
        if not config.simulation and not config.allow_live_order:
            return {
                "passed": False,
                "simulation": False,
                "error": "live_order_locked",
                "message": "正式環境下單測試已鎖定。若要送出真單，請勾選允許正式下單。",
                "next_steps": [
                    "先在模擬模式完成測試流程",
                    "確認正式環境檢核已通過",
                    "確認無誤後再勾選允許正式下單",
                ],
            }
        try:
            with self._session(simulation=config.simulation, activate_ca=not config.simulation) as (api, context):
                result = self._execute_futures_order(
                    api=api,
                    futures_code=config.futures_code,
                    quantity=config.futures_quantity,
                    order_price=config.futures_price,
                )
                result["simulation"] = config.simulation
                result["mode"] = _mode_text(config.simulation)
                result["ca_activated"] = context.ca_activated
                result["production_permission"] = context.production_permission
                return result
        except Exception as exc:  # noqa: BLE001
            return {
                "passed": False,
                "simulation": config.simulation,
                "error": "futures_order_failed",
                "message": str(exc),
            }

    def run_simulation_suite(self, config: OrderTestConfig) -> Dict[str, Any]:
        interval_sec = max(1.0, float(config.interval_sec))
        try:
            with self._session(simulation=True, activate_ca=False) as (api, context):
                login_ok = {
                    "passed": True,
                    "message": "模擬模式登入成功",
                    "stock_account": self.gateway.serialize(self.gateway.pick_stock_account(api, context.accounts)),
                    "futures_account": self.gateway.serialize(self.gateway.pick_futures_account(api, context.accounts)),
                }
                stock_result = self._execute_stock_order(
                    api=api,
                    stock_code=config.stock_code,
                    quantity=config.stock_quantity,
                    order_price=config.stock_price,
                )
                time.sleep(interval_sec)
                futures_result = self._execute_futures_order(
                    api=api,
                    futures_code=config.futures_code,
                    quantity=config.futures_quantity,
                    order_price=config.futures_price,
                )
                passed = bool(login_ok["passed"] and stock_result.get("passed") and futures_result.get("passed"))
                failed_checks = []
                if not login_ok["passed"]:
                    failed_checks.append("登入")
                if not stock_result.get("passed"):
                    failed_checks.append("證券下單")
                if not futures_result.get("passed"):
                    failed_checks.append("期貨下單")
                if passed:
                    summary_message = "模擬測試流程完成"
                else:
                    summary_message = f"模擬測試未通過（失敗項目：{'、'.join(failed_checks)}）"
                return {
                    "passed": passed,
                    "simulation": True,
                    "mode": "模擬帳戶",
                    "message": summary_message,
                    "checks": {
                        "login": login_ok,
                        "stock_order": stock_result,
                        "futures_order": futures_result,
                        "required_interval_seconds": interval_sec,
                    },
                    "tested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
        except Exception as exc:  # noqa: BLE001
            return {
                "passed": False,
                "simulation": True,
                "error": "simulation_suite_failed",
                "message": str(exc),
            }

    def verify_production_ready(self) -> Dict[str, Any]:
        try:
            with self._session(simulation=False, activate_ca=True) as (api, context):
                accounts = context.accounts
                if not accounts and hasattr(api, "list_accounts"):
                    try:
                        accounts = self.gateway.call_with_timeout(api.list_accounts, 10)
                    except Exception:
                        accounts = []

                account_rows = []
                signed_count = 0
                for account in accounts or []:
                    signed = getattr(account, "signed", None)
                    row = {
                        "account_id": getattr(account, "account_id", None),
                        "account_type": str(getattr(account, "account_type", "")),
                        "signed": signed,
                    }
                    if signed is True:
                        signed_count += 1
                    account_rows.append(row)

                ca_configured = bool(context.env_status.get("SHIOAJI_CA_PATH") and context.env_status.get("SHIOAJI_CA_PASSWORD"))
                ready_for_live = bool(context.production_permission and signed_count > 0 and ca_configured)
                next_steps = self._build_next_steps(
                    production_permission=context.production_permission,
                    signed_count=signed_count,
                    ca_configured=ca_configured,
                )

                return {
                    "passed": ready_for_live,
                    "simulation": False,
                    "mode": "正式帳戶",
                    "message": "正式環境可切換" if ready_for_live else "尚未符合正式環境切換條件",
                    "production_permission": context.production_permission,
                    "ca_activated": context.ca_activated,
                    "ca_configured": ca_configured,
                    "signed_count": signed_count,
                    "accounts": account_rows,
                    "next_steps": next_steps,
                    "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
        except ProductionPermissionError as exc:
            return {
                "passed": False,
                "simulation": False,
                "mode": "正式帳戶",
                "message": str(exc),
                "error": "production_permission",
                "production_permission": False,
                "next_steps": [
                    "到永豐 API Key 管理頁（https://www.sinotrade.com.tw/newweb/PythonAPIKey/）開啟正式環境權限",
                    "確認 API key 綁定到正確帳戶",
                    "重新產生 key/secret 並更新到 .env",
                    "重新執行正式環境檢核",
                ],
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "passed": False,
                "simulation": False,
                "mode": "正式帳戶",
                "message": str(exc),
                "error": "verify_production_failed",
            }

    def query_account_balance(self, simulation: bool) -> Dict[str, Any]:
        with self._session(simulation=simulation, activate_ca=not simulation) as (api, context):
            account = self.gateway.pick_stock_account(api, context.accounts)
            if account is None:
                raise ShioajiGatewayError("找不到股票帳戶 (stock_account)")
            balance = self._call_account_api(api.account_balance, account, timeout_sec=15, allow_no_account=True)
            amount = getattr(balance, "acc_balance", None)
            return {
                "mode": _mode_text(simulation),
                "simulation": simulation,
                "account": self.gateway.serialize(account),
                "acc_balance": amount,
                "raw": self.gateway.serialize(balance),
            }

    def query_account_funds(self, simulation: bool) -> Dict[str, Any]:
        with self._session(simulation=simulation, activate_ca=not simulation) as (api, context):
            account = self.gateway.pick_stock_account(api, context.accounts)
            if account is None:
                raise ShioajiGatewayError("找不到股票帳戶 (stock_account)")
            funds_data = None
            if hasattr(api, "get_stock_account_funds"):
                try:
                    funds_data = self._call_account_api(
                        api.get_stock_account_funds,
                        account,
                        timeout_sec=15,
                        allow_no_account=True,
                    )
                except Exception:
                    funds_data = None
            if funds_data is None:
                funds_data = self._call_account_api(api.account_balance, account, timeout_sec=15, allow_no_account=True)
            data = self.gateway.as_dict(funds_data)
            amount, key = self._pick_amount(data)
            return {
                "mode": _mode_text(simulation),
                "simulation": simulation,
                "account": self.gateway.serialize(account),
                "available_amount": amount,
                "amount_key": key,
                "raw": self.gateway.serialize(funds_data),
            }

    def query_settlements(self, simulation: bool) -> Dict[str, Any]:
        with self._session(simulation=simulation, activate_ca=not simulation) as (api, context):
            account = self.gateway.pick_stock_account(api, context.accounts)
            if account is None:
                raise ShioajiGatewayError("找不到股票帳戶 (stock_account)")
            settlements = None
            if hasattr(api, "settlements"):
                settlements = self._call_account_api(api.settlements, account, timeout_sec=15, allow_no_account=True)
            if settlements is None and hasattr(api, "list_settlements"):
                settlements = self._call_account_api(
                    api.list_settlements,
                    account,
                    timeout_sec=15,
                    allow_no_account=True,
                )
            summary = self._format_settlements(settlements)
            return {
                "mode": _mode_text(simulation),
                "simulation": simulation,
                "account": self.gateway.serialize(account),
                "summary": summary,
                "raw": self.gateway.serialize(settlements),
            }

    def query_positions(self, simulation: bool) -> Dict[str, Any]:
        with self._session(simulation=simulation, activate_ca=not simulation) as (api, context):
            account = self.gateway.pick_stock_account(api, context.accounts)
            if account is None:
                raise ShioajiGatewayError("找不到股票帳戶 (stock_account)")
            positions = self._call_account_api(api.list_positions, account, timeout_sec=15, allow_no_account=True)
            summary, detail = self._format_positions(positions)
            return {
                "mode": _mode_text(simulation),
                "simulation": simulation,
                "account": self.gateway.serialize(account),
                "summary": summary,
                "detail": detail,
                "raw": self.gateway.serialize(positions),
            }

    def run_account_diagnose(self, simulation: bool) -> Dict[str, Any]:
        with self._session(simulation=simulation, activate_ca=not simulation) as (api, context):
            accounts = context.accounts
            if not accounts and hasattr(api, "list_accounts"):
                try:
                    accounts = self.gateway.call_with_timeout(api.list_accounts, 10)
                except Exception:
                    accounts = []
            account = self.gateway.pick_stock_account(api, accounts)
            if account is None:
                raise ShioajiGatewayError("找不到股票帳戶 (stock_account)")

            balance = self._call_account_api(api.account_balance, account, timeout_sec=15, allow_no_account=True)
            try:
                funds = self._call_account_api(
                    api.get_stock_account_funds,
                    account,
                    timeout_sec=15,
                    allow_no_account=True,
                )
            except Exception:
                funds = balance
            positions = self._call_account_api(api.list_positions, account, timeout_sec=15, allow_no_account=True)
            settlements = None
            if hasattr(api, "settlements"):
                settlements = self._call_account_api(api.settlements, account, timeout_sec=15, allow_no_account=True)
            if settlements is None and hasattr(api, "list_settlements"):
                settlements = self._call_account_api(
                    api.list_settlements,
                    account,
                    timeout_sec=15,
                    allow_no_account=True,
                )

            return {
                "mode": _mode_text(simulation),
                "simulation": simulation,
                "env": context.env_status,
                "shioaji_version": context.shioaji_version,
                "production_permission": context.production_permission,
                "ca_activated": context.ca_activated,
                "accounts": self.gateway.serialize(accounts),
                "account": self.gateway.serialize(account),
                "balance": self.gateway.serialize(balance),
                "funds": self.gateway.serialize(funds),
                "positions": self.gateway.serialize(positions),
                "settlements": self.gateway.serialize(settlements),
            }

    def _execute_stock_order(self, *, api, stock_code: str, quantity: int, order_price: float | None) -> Dict[str, Any]:
        import shioaji as sj

        account = self.gateway.pick_stock_account(api)
        if account is None:
            raise ShioajiGatewayError("找不到股票帳戶，無法執行證券下單測試")
        contract = self.gateway.get_stock_contract(api, stock_code)
        price = float(order_price) if isinstance(order_price, (int, float)) and order_price > 0 else self.gateway.pick_reference_price(contract, fallback=10.0)
        order = api.Order(
            price=price,
            quantity=int(quantity),
            action=sj.constant.Action.Buy,
            price_type=sj.constant.StockPriceType.LMT,
            order_type=sj.constant.OrderType.ROD,
            account=account,
        )
        trade = self.gateway.call_with_timeout(api.place_order, 15, contract, order)
        self._update_order_status(api, account)
        status = self.gateway.extract_trade_status(trade)
        passed = status.upper() not in {"FAILED", "REJECTED", "CANCELLED"}
        return {
            "passed": passed,
            "message": "證券下單測試通過" if passed else f"證券下單狀態異常：{status}",
            "stock_code": getattr(contract, "code", stock_code),
            "order_price": price,
            "quantity": quantity,
            "status": status,
            "trade": self.gateway.serialize(trade),
        }

    def _execute_futures_order(self, *, api, futures_code: str, quantity: int, order_price: float | None) -> Dict[str, Any]:
        import shioaji as sj

        account = self.gateway.pick_futures_account(api)
        if account is None:
            raise ShioajiGatewayError("找不到期貨帳戶，無法執行期貨下單測試")
        contract = self.gateway.get_futures_contract(api, futures_code)
        price = float(order_price) if isinstance(order_price, (int, float)) and order_price > 0 else self.gateway.pick_reference_price(contract, fallback=1.0)
        order = api.Order(
            price=price,
            quantity=int(quantity),
            action=sj.constant.Action.Buy,
            price_type=sj.constant.FuturesPriceType.LMT,
            order_type=sj.constant.OrderType.ROD,
            octype=sj.constant.FuturesOCType.Auto,
            account=account,
        )
        trade = self.gateway.call_with_timeout(api.place_order, 15, contract, order)
        self._update_order_status(api, account)
        status = self.gateway.extract_trade_status(trade)
        passed = status.upper() not in {"FAILED", "REJECTED", "CANCELLED"}
        return {
            "passed": passed,
            "message": "期貨下單測試通過" if passed else f"期貨下單狀態異常：{status}",
            "futures_code": getattr(contract, "code", futures_code),
            "order_price": price,
            "quantity": quantity,
            "status": status,
            "trade": self.gateway.serialize(trade),
        }

    def _update_order_status(self, api, account) -> None:
        try:
            self._call_account_api(api.update_status, account, timeout_sec=12, allow_no_account=True)
        except Exception:
            return

    def _call_account_api(self, method, account, *, timeout_sec: int = 15, allow_no_account: bool = False):
        attempts = [
            {"args": (), "kwargs": {"account": account}},
            {"args": (account,), "kwargs": {}},
        ]
        if allow_no_account:
            attempts.append({"args": (), "kwargs": {}})

        last_type_error = None
        for attempt in attempts:
            try:
                return self.gateway.call_with_timeout(
                    method,
                    timeout_sec,
                    *attempt["args"],
                    **attempt["kwargs"],
                )
            except TypeError as exc:
                last_type_error = exc
                continue
        if last_type_error is not None:
            raise last_type_error
        raise RuntimeError("account api call failed")

    @staticmethod
    def _pick_amount(data: Dict[str, Any]):
        candidate_keys = (
            "available_balance",
            "available_funds",
            "available_amount",
            "available_cash",
            "cash_balance",
            "cash",
            "acc_balance",
        )
        for key in candidate_keys:
            value = data.get(key)
            if isinstance(value, (int, float)):
                return value, key
        return None, "unknown"

    def _format_settlements(self, settlements) -> str:
        if not settlements:
            return "無資料"
        if isinstance(settlements, list):
            chunks = []
            for item in settlements[:3]:
                data = self.gateway.as_dict(item)
                if hasattr(item, "T"):
                    chunks.append(f"T+{getattr(item, 'T', '?')}: {getattr(item, 'amount', 0):,.0f}")
                    continue
                t_money = data.get("t_money") or data.get("tmoney") or 0
                t1_money = data.get("t1_money") or 0
                t2_money = data.get("t2_money") or 0
                chunks.append(f"T:{t_money:,.0f} T+1:{t1_money:,.0f} T+2:{t2_money:,.0f}")
            return " | ".join(chunks) if chunks else "無資料"
        return str(settlements)

    def _format_positions(self, positions) -> tuple[str, str]:
        if not positions:
            return "無持倉", "[]"
        summary_items: List[str] = []
        detail_items: List[str] = []
        for pos in positions:
            data = self.gateway.as_dict(pos)
            code = data.get("code") or data.get("symbol") or "UNKNOWN"
            qty = data.get("quantity") or data.get("qty") or 0
            summary_items.append(f"{code} x{qty}")
            detail_items.append(str(data))
        return ", ".join(summary_items[:6]), " | ".join(detail_items[:6])

    @staticmethod
    def _build_next_steps(*, production_permission: bool, signed_count: int, ca_configured: bool) -> List[str]:
        steps: List[str] = []
        if not production_permission:
            steps.append("到永豐 API Key 管理頁開通正式環境權限，並重新產生 key/secret")
            steps.append("更新 .env 的 SHIOAJI_APIKEY 與 SHIOAJI_SECRET 後重啟控制台")
        if signed_count <= 0:
            steps.append("完成官方模擬測試並等待審核（約 5 分鐘）")
            steps.append("重新登入正式環境，確認帳戶欄位 signed=True")
        if not ca_configured:
            steps.append("在 .env 補上 SHIOAJI_CA_PATH 與 SHIOAJI_CA_PASSWORD")
        if not steps:
            steps.append("可切換 simulation=False 進行正式環境操作")
            steps.append("建議先用最小張數/口數進行一次小額驗證")
        return steps
