from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass(frozen=True)
class DataQualityStatus:
    freshness_status: str = "unknown"
    latest_bar_date: date | None = None
    missing_ohlcv_count: int = 0
    chip_data_status: str = "unknown"
    source: str = "unknown"
    insecure_transport: bool = False
    partial_failure: bool = False
    degraded_reasons: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.insecure_transport:
            return "insecure_transport"
        if self.partial_failure:
            return "partial_failure"
        if self.missing_ohlcv_count > 0:
            return "missing_ohlcv"
        if self.freshness_status not in {"ok", "unknown"}:
            return self.freshness_status
        return "ok" if not self.degraded_reasons else "degraded"

    def blocks_live_order(self, *, require_chip: bool = False) -> bool:
        if self.insecure_transport or self.partial_failure or self.missing_ohlcv_count > 0:
            return True
        if self.freshness_status not in {"ok", "unknown"}:
            return True
        if require_chip and self.chip_data_status not in {"ok", "complete"}:
            return True
        return False

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["latest_bar_date"] = self.latest_bar_date.isoformat() if self.latest_bar_date else None
        payload["status"] = self.status
        return payload
