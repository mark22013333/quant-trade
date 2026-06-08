from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


SENSITIVE_KEY_RE = re.compile(
    r"(api.?key|secret|token|password|passwd|ca_path|ca_pass|person_id|account_id|account_no|idno)",
    re.IGNORECASE,
)


def _mask_text(value: str) -> str:
    text = str(value)
    if len(text) <= 4:
        return "***"
    if len(text) <= 10:
        return f"{text[:2]}***{text[-2:]}"
    return f"{text[:4]}***{text[-4:]}"


def _redact_value(key: str | None, value: Any, depth: int) -> Any:
    if depth <= 0:
        return "<redacted-depth-limit>"

    if key is not None and SENSITIVE_KEY_RE.search(str(key)):
        if value in (None, ""):
            return value
        if isinstance(value, bool):
            return value
        return _mask_text(str(value))

    if isinstance(value, Mapping):
        return {str(item_key): _redact_value(str(item_key), item_value, depth - 1) for item_key, item_value in value.items()}

    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        # Conservative fallback for accidentally embedded bearer tokens.
        return re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", r"\1***", text)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_value(None, item, depth - 1) for item in value]

    if hasattr(value, "_asdict"):
        try:
            return _redact_value(key, value._asdict(), depth - 1)
        except Exception:
            return str(value)

    if hasattr(value, "__dict__") and not isinstance(value, type):
        try:
            return _redact_value(key, vars(value), depth - 1)
        except Exception:
            return str(value)

    return value


def redact_sensitive(value: Any, *, max_depth: int = 8) -> Any:
    """Return a JSON-friendly copy with credentials and account identifiers masked."""
    return _redact_value(None, value, max_depth)
