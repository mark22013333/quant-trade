import pandas as pd

from broker.paper_broker import PaperBroker


def test_paper_broker_simulation_basic():
    dates = pd.date_range(start="2022-01-03", periods=10, freq="B")
    prices = [100, 101, 102, 103, 104, 110, 109, 108, 107, 106]
    df = pd.DataFrame({"Close": prices}, index=dates)

    # Long from day 0 to day 5
    df["position"] = 0
    df.loc[df.index[0]:df.index[5], "position"] = 1

    broker = PaperBroker(init_cash=1_000_000)
    result = broker.simulate(df, symbol="TEST")

    assert result["trade_count"] == 1
    assert result["total_return"] > 0
    assert result["win_rate"] == 1.0
