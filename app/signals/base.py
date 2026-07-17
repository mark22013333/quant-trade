from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class SignalDefinition:
    signal_id: str
    label: str
    category: str
    description: str
    tag_prefix: str | None = None
    exact_tags: tuple[str, ...] = ()
    value_type: str = "flag"
    source: str = "radar"
    required_data: tuple[str, ...] = ()
    outcome_bucket: bool = True
    outcome_bucket_mode: str = "exact"
    metadata: dict[str, str] = field(default_factory=dict)

    def matches_tag(self, tag: str) -> bool:
        text = str(tag or "").strip()
        if not text:
            return False
        if text in self.exact_tags:
            return True
        return bool(self.tag_prefix and text.startswith(self.tag_prefix))

    def bucket_for_tag(self, tag: str) -> str:
        if not self.outcome_bucket:
            return ""
        text = str(tag or "").strip()
        if self.outcome_bucket_mode == "prefix":
            return self.signal_id
        return text

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "tag_prefix": self.tag_prefix,
            "exact_tags": list(self.exact_tags),
            "value_type": self.value_type,
            "source": self.source,
            "required_data": list(self.required_data),
            "outcome_bucket": self.outcome_bucket,
            "outcome_bucket_mode": self.outcome_bucket_mode,
            "metadata": dict(self.metadata),
        }


class SignalRegistry:
    def __init__(self, definitions: Iterable[SignalDefinition]):
        self._definitions = tuple(definitions)
        self._by_id = {item.signal_id: item for item in self._definitions}
        if len(self._by_id) != len(self._definitions):
            raise ValueError("duplicate signal_id in SignalRegistry")

    def get(self, signal_id: str) -> SignalDefinition | None:
        return self._by_id.get(str(signal_id or "").strip())

    def require(self, signal_id: str) -> SignalDefinition:
        definition = self.get(signal_id)
        if definition is None:
            raise KeyError(f"unknown signal_id: {signal_id}")
        return definition

    def match_tag(self, tag: str) -> SignalDefinition | None:
        text = str(tag or "").strip()
        for definition in self._definitions:
            if definition.matches_tag(text):
                return definition
        return None

    def list(self, *, category: str | None = None, source: str | None = None) -> list[SignalDefinition]:
        category_key = str(category or "").strip()
        source_key = str(source or "").strip()
        items = list(self._definitions)
        if category_key:
            items = [item for item in items if item.category == category_key]
        if source_key:
            items = [item for item in items if item.source == source_key]
        return items

    def categories(self) -> list[str]:
        return sorted({item.category for item in self._definitions})

    def to_dicts(self, *, category: str | None = None, source: str | None = None) -> list[dict]:
        return [item.to_dict() for item in self.list(category=category, source=source)]
