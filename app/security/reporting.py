from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.security.redaction import redact_sensitive


CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def sanitize_report_payload(value: Any) -> Any:
    """Return a report-safe payload with credentials and account identifiers masked."""
    return redact_sensitive(value)


def csv_safe_value(value: Any) -> str:
    text = "" if value is None else str(redact_sensitive(value))
    if text.startswith(CSV_FORMULA_PREFIXES):
        return f"'{text}"
    return text.replace("\r", " ").replace("\n", " ")


def csv_join_row(values: Sequence[Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow([csv_safe_value(value) for value in values])
    return buffer.getvalue()


def csv_join_pipe(values: Iterable[Any]) -> str:
    return "|".join(csv_safe_value(value) for value in values)


def pick_report_values(row: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    return [csv_safe_value(row.get(key, "")) for key in keys]
