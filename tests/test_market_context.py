from __future__ import annotations

from datetime import date

from app.services.market_context import (
    atr_heat_level,
    classify_tw_symbol_group,
    compute_group_trend_metrics,
    compute_group_rotation_scores,
    group_limit_for_target,
    select_with_group_diversity,
)


def test_classify_tw_symbol_group_maps_common_tw_groups():
    assert classify_tw_symbol_group("2330") == "電子/科技"
    assert classify_tw_symbol_group("2881") == "金融/保險"
    assert classify_tw_symbol_group("2603") == "航運/運輸"
    assert classify_tw_symbol_group("1216") == "食品"
    assert classify_tw_symbol_group("") == "其他"


def test_atr_heat_level_uses_risk_profile_thresholds():
    assert atr_heat_level(0.052, risk_profile="conservative") == "hot"
    assert atr_heat_level(0.052, risk_profile="balanced") == "elevated"
    assert atr_heat_level(0.061, risk_profile="balanced") == "hot"
    assert atr_heat_level(0.061, risk_profile="aggressive") == "elevated"
    assert atr_heat_level(0.035, risk_profile="balanced") == "normal"


def test_group_limit_for_target_uses_risk_profile():
    assert group_limit_for_target(10, risk_profile="conservative") == 3
    assert group_limit_for_target(10, risk_profile="balanced") == 4
    assert group_limit_for_target(10, risk_profile="aggressive") == 5


def test_select_with_group_diversity_caps_group_when_alternatives_exist():
    items = [{"symbol": f"23{i:02d}", "group": "電子/科技"} for i in range(8)]
    items.extend({"symbol": f"28{i:02d}", "group": "金融/保險"} for i in range(4))
    items.extend({"symbol": f"12{i:02d}", "group": "食品"} for i in range(4))

    selected = select_with_group_diversity(
        items,
        target_count=10,
        group_getter=lambda item: item["group"],
        risk_profile="balanced",
    )

    electronics = [item for item in selected if item["group"] == "電子/科技"]
    assert len(selected) == 10
    assert len(electronics) <= 4


def test_compute_group_rotation_scores_rewards_stronger_broader_groups():
    items = [
        {"group": "半導體業", "score": 0.90},
        {"group": "半導體業", "score": 0.86},
        {"group": "半導體業", "score": 0.82},
        {"group": "金融/保險", "score": 0.72},
        {"group": "金融/保險", "score": 0.70},
    ]

    scores = compute_group_rotation_scores(
        items,
        group_getter=lambda item: item["group"],
        score_getter=lambda item: item["score"],
    )

    assert scores["半導體業"] > scores["金融/保險"]
    assert 0.0 <= scores["半導體業"] <= 1.0


def test_compute_group_trend_metrics_uses_short_medium_return_and_breadth():
    class _Repo:
        def __init__(self):
            self.bars = {
                "2301": [{"date": f"2024-01-{day:02d}", "close": 100 + day} for day in range(1, 22)],
                "2302": [{"date": f"2024-01-{day:02d}", "close": 90 + (day * 0.8)} for day in range(1, 22)],
                "2801": [{"date": f"2024-01-{day:02d}", "close": 100 - day} for day in range(1, 22)],
            }

        def get_daily_bars(self, symbol, end_date=None):  # noqa: ANN001, ARG002
            return self.bars[symbol]

    metrics = compute_group_trend_metrics(
        [
            {"symbol": "2301", "group": "電子/科技"},
            {"symbol": "2302", "group": "電子/科技"},
            {"symbol": "2801", "group": "金融/保險"},
        ],
        repo=_Repo(),
        trade_date=date(2024, 1, 21),
        symbol_getter=lambda item: item["symbol"],
        group_getter=lambda item: item["group"],
    )

    assert metrics["電子/科技"]["label"] in {"warming", "firm"}
    assert metrics["電子/科技"]["breadth"] == 1.0
    assert metrics["電子/科技"]["score"] > metrics["金融/保險"]["score"]
    assert metrics["金融/保險"]["label"] in {"cooling", "weak"}
