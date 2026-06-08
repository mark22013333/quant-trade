from __future__ import annotations

from dataclasses import dataclass
import os

from app.backtest.sizer_absolute_capital import AbsoluteSizerConfig, compute_order_size


class PreTradeCheckError(RuntimeError):
    """Raised when a pre-trade absolute-capital guard fails."""


@dataclass
class PreTradeResult:
    accepted: bool
    qty: int
    estimated_total_cost: float
    reason: str


@dataclass
class LiveOrderUnlockConfig:
    simulation: bool = True
    allow_live_order: bool = False
    live_order_nonce: str = ""


@dataclass
class LiveOrderUnlockResult:
    accepted: bool
    reason: str
    message: str


def enforce_absolute_capital_guard(
    *,
    available_cash: float,
    current_price: float,
    config: AbsoluteSizerConfig | None = None,
) -> PreTradeResult:
    sizing = compute_order_size(available_cash=available_cash, current_price=current_price, config=config)
    if not sizing.get("accepted"):
        return PreTradeResult(
            accepted=False,
            qty=0,
            estimated_total_cost=0.0,
            reason=str(sizing.get("reason", "rejected")),
        )
    return PreTradeResult(
        accepted=True,
        qty=int(sizing["qty"]),
        estimated_total_cost=float(sizing["estimated_total_cost"]),
        reason="ok",
    )


def validate_live_order_unlock(config: LiveOrderUnlockConfig) -> LiveOrderUnlockResult:
    if config.simulation:
        return LiveOrderUnlockResult(True, "simulation", "模擬模式")
    if not config.allow_live_order:
        return LiveOrderUnlockResult(
            False,
            "live_order_locked",
            "正式環境下單已鎖定。若要送出真單，請在請求中明確允許正式下單。",
        )
    enabled = os.getenv("SHIOAJI_ENABLE_LIVE_ORDERS", "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return LiveOrderUnlockResult(
            False,
            "live_order_env_locked",
            "後端未設定 SHIOAJI_ENABLE_LIVE_ORDERS=1，拒絕正式環境下單。",
        )
    expected_nonce = os.getenv("SHIOAJI_LIVE_ORDER_NONCE", "").strip()
    if expected_nonce and str(config.live_order_nonce or "").strip() != expected_nonce:
        return LiveOrderUnlockResult(False, "live_order_nonce_mismatch", "正式下單 nonce 不符，拒絕送出真單。")
    return LiveOrderUnlockResult(True, "ok", "正式下單後端鎖已解除")
