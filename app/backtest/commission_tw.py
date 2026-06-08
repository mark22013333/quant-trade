from __future__ import annotations

from dataclasses import dataclass

from app.backtest.costs import estimate_fee, estimate_tax


@dataclass
class TaiwanStockCostModel:
    fee_rate: float = 0.001425
    min_fee: float = 20.0
    tax_rate: float = 0.003

    def buy_fee(self, price: float, size: int) -> float:
        value = float(price) * int(size)
        return estimate_fee(order_value=value, fee_rate=self.fee_rate, min_fee=self.min_fee)

    def sell_fee(self, price: float, size: int) -> float:
        value = float(price) * int(size)
        return estimate_fee(order_value=value, fee_rate=self.fee_rate, min_fee=self.min_fee)

    def sell_tax(self, price: float, size: int) -> float:
        value = float(price) * int(size)
        return estimate_tax(order_value=value, tax_rate=self.tax_rate)


try:
    import backtrader as bt
except Exception:  # noqa: BLE001
    bt = None


if bt is not None:
    class TaiwanStockCommissionInfo(bt.CommInfoBase):  # type: ignore[misc]
        params = (
            ("commission", 0.001425),
            ("stocklike", True),
            ("commtype", bt.CommInfoBase.COMM_PERC),
            ("percabs", True),
            ("min_commission", 20.0),
            ("tax", 0.003),
        )

        def _getcommission(self, size, price, pseudoexec):  # noqa: ARG002
            value = abs(size) * price
            if size > 0:
                return max(self.p.min_commission, value * self.p.commission)
            return max(self.p.min_commission, value * self.p.commission) + (value * self.p.tax)
else:
    class TaiwanStockCommissionInfo:
        def __init__(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            raise RuntimeError("backtrader is required for TaiwanStockCommissionInfo")
