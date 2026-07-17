from __future__ import annotations

from app.signals.registry import (
    DEFAULT_SIGNAL_REGISTRY,
    list_registered_signals,
    match_signal_tag,
    outcome_bucket_for_tag,
    radar_outcome_bucket_for_tag,
)


def test_signal_registry_lists_core_radar_signals() -> None:
    signal_ids = {item["signal_id"] for item in list_registered_signals()}

    assert "trend_ok" in signal_ids
    assert "atr_heat" in signal_ids
    assert "industry_trend" in signal_ids
    assert "entry_score" in signal_ids
    assert "outcome_reliability_boost" in signal_ids
    assert "outcome_reliability_horizons" in signal_ids
    assert "symbol_adoption_feedback_boost" in signal_ids
    assert "symbol_adoption_feedback_confidence" in signal_ids
    assert "symbol_adoption_feedback_horizons" in signal_ids
    assert "blocker_data_stale" in signal_ids
    assert "blocker_attention_active" in signal_ids
    assert "technical" in DEFAULT_SIGNAL_REGISTRY.categories()
    assert "blocker" in DEFAULT_SIGNAL_REGISTRY.categories()


def test_signal_registry_matches_reason_and_blocker_tags() -> None:
    trend = match_signal_tag("TREND_OK=true")
    blocker = match_signal_tag("B_DATA_STALE")
    attention = match_signal_tag("B_ATTENTION_ACTIVE")

    assert trend is not None
    assert trend.signal_id == "trend_ok"
    assert blocker is not None
    assert blocker.signal_id == "blocker_data_stale"
    assert attention is not None
    assert attention.signal_id == "blocker_attention_active"


def test_outcome_bucket_uses_registry_policy() -> None:
    assert outcome_bucket_for_tag("TREND_OK=true") == "TREND_OK=true"
    assert outcome_bucket_for_tag("ATR_HEAT=hot") == "ATR_HEAT=hot"
    assert outcome_bucket_for_tag("ENTRY_SCORE=84.2") == ""
    assert outcome_bucket_for_tag("INDUSTRY_ROTATION_SCORE=78.1") == ""
    assert outcome_bucket_for_tag("CUSTOM_SIGNAL=x") is None


def test_radar_outcome_bucket_ignores_reliability_scoring_tags() -> None:
    assert radar_outcome_bucket_for_tag("OUTCOME_RELIABILITY_BOOST=1.2") == ""
    assert radar_outcome_bucket_for_tag("OUTCOME_RELIABILITY_HORIZONS=5d:0.60") == ""
    assert radar_outcome_bucket_for_tag("TRIGGER_REASON=breakout") == "TRIGGER_REASON=breakout"
