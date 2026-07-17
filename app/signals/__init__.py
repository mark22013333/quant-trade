"""Signal generation boundary; strategies must emit signals, not broker orders."""

from app.signals.base import SignalDefinition, SignalRegistry
from app.signals.registry import (
    DEFAULT_SIGNAL_REGISTRY,
    list_registered_signals,
    match_signal_tag,
    outcome_bucket_for_tag,
    radar_outcome_bucket_for_tag,
)

__all__ = [
    "DEFAULT_SIGNAL_REGISTRY",
    "SignalDefinition",
    "SignalRegistry",
    "list_registered_signals",
    "match_signal_tag",
    "outcome_bucket_for_tag",
    "radar_outcome_bucket_for_tag",
]
