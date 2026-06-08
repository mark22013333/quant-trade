from __future__ import annotations

import json

from web.control_panel_app import StrategyBacktestRequest, strategy_backtest_export


def test_strategy_backtest_export_returns_400_for_invalid_config():
    response = strategy_backtest_export(
        StrategyBacktestRequest(
            symbol="2330.TW",
            market="TW",
            enabled={"bad_strategy": True},
        )
    )

    payload = json.loads(response.body)

    assert response.status_code == 400
    assert payload["status"] == "error"
    assert payload["code"] == "invalid_config"
