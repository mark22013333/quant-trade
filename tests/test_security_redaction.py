from __future__ import annotations

from types import SimpleNamespace

from app.security import redact_sensitive


def test_redact_sensitive_masks_nested_credentials():
    payload = {
        "SHIOAJI_APIKEY": "dummy",
        "nested": {
            "account_id": "123456789",
            "public": "2330",
        },
        "items": [SimpleNamespace(secret="super-secret-value", qty=1)],
    }

    redacted = redact_sensitive(payload)

    assert redacted["SHIOAJI_APIKEY"] != "dummy"
    assert redacted["nested"]["account_id"] != "123456789"
    assert redacted["nested"]["public"] == "2330"
    assert redacted["items"][0]["secret"] != "super-secret-value"
    assert redacted["items"][0]["qty"] == 1
