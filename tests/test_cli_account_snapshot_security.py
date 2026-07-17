from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import cli
from app.broker.shioaji_account_service import ShioajiAccountService


def test_account_snapshot_cli_masks_broker_login_exception(monkeypatch, capsys) -> None:
    sensitive_error = (
        "login rejected: person_id=SECRET_PERSON account_id=SECRET_ACCOUNT "
        "ip_address=192.0.2.10"
    )

    def fail_snapshot(self, simulation: bool):  # noqa: ARG001
        raise Exception(sensitive_error)

    monkeypatch.setattr(ShioajiAccountService, "get_account_snapshot", fail_snapshot)

    with pytest.raises(SystemExit) as caught:
        cli.cmd_account_snapshot(SimpleNamespace(simulation=False))

    output = capsys.readouterr()
    assert caught.value.code == 2
    assert output.out == ""
    assert '"error": "account_snapshot_failed"' in output.err
    assert '"error_type": "Exception"' in output.err
    assert "SECRET_PERSON" not in output.err
    assert "SECRET_ACCOUNT" not in output.err
    assert "192.0.2.10" not in output.err
