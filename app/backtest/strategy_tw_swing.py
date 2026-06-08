from __future__ import annotations

try:
    import backtrader as bt
except Exception:  # noqa: BLE001
    bt = None


if bt is not None:
    class TwSwingStrategy(bt.Strategy):  # type: ignore[misc]
        params = dict(
            ma_period=60,
            vol_period=5,
            vol_factor=1.5,
            rsi_period=3,
            kd_period=9,
            atr_period=14,
            max_hold_days=5,
        )

        def __init__(self):
            self.ma60 = bt.indicators.SMA(self.data.close, period=self.p.ma_period)
            self.vol_ma5 = bt.indicators.SMA(self.data.volume, period=self.p.vol_period)
            self.rsi3 = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
            self.stoch = bt.indicators.Stochastic(self.data, period=self.p.kd_period, period_dfast=3, period_dslow=3)
            self.atr14 = bt.indicators.ATR(self.data, period=self.p.atr_period)
            self.entry_bar_index = None
            self.entry_price = None

        def next(self):
            close = float(self.data.close[0])
            volume = float(self.data.volume[0])
            trend_ok = close > float(self.ma60[0])
            vol_ok = volume > float(self.vol_ma5[0]) * float(self.p.vol_factor)
            rsi_signal = float(self.rsi3[0]) < 20.0
            k_now = float(self.stoch.percK[0])
            d_now = float(self.stoch.percD[0])
            k_prev = float(self.stoch.percK[-1]) if len(self) > 1 else k_now
            d_prev = float(self.stoch.percD[-1]) if len(self) > 1 else d_now
            kd_signal = (k_now < 20.0 and d_now < 20.0 and k_now > d_now and k_prev <= d_prev)
            entry_signal = trend_ok and vol_ok and (rsi_signal or kd_signal)

            if not self.position:
                if entry_signal:
                    self.buy()
                    self.entry_price = close
                    self.entry_bar_index = len(self)
                return

            if self.entry_price is None or self.entry_bar_index is None:
                return

            atr = float(self.atr14[0])
            pnl = close - float(self.entry_price)
            if pnl > (2.0 * atr):
                self.close()
                return
            if pnl < (-1.0 * atr):
                self.close()
                return
            if (len(self) - int(self.entry_bar_index)) >= int(self.p.max_hold_days):
                self.close()
else:
    class TwSwingStrategy:
        def __init__(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            raise RuntimeError("backtrader is required for TwSwingStrategy")
