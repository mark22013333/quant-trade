"""Quick health check for Shioaji environment and connectivity."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List


REQUIRED_ENV = ["SHIOAJI_APIKEY", "SHIOAJI_SECRET"]
OPTIONAL_ENV = ["SHIOAJI_CA_PATH", "SHIOAJI_CA_PASSWORD", "SHIOAJI_CA_PERSON_ID"]


@dataclass
class CheckResult:
    status: str
    missing_env: List[str]
    simulation: bool | None
    contracts_ok: bool
    snapshot_ok: bool
    message: str


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass


def run_check(do_login: bool = True) -> CheckResult:
    _load_env()
    missing = [key for key in REQUIRED_ENV if not os.getenv(key)]
    if missing:
        return CheckResult(
            status="missing_env",
            missing_env=missing,
            simulation=None,
            contracts_ok=False,
            snapshot_ok=False,
            message="Missing required SHIOAJI env vars.",
        )

    try:
        import shioaji as sj
    except Exception:
        return CheckResult(
            status="missing_shioaji",
            missing_env=[],
            simulation=None,
            contracts_ok=False,
            snapshot_ok=False,
            message="shioaji not installed.",
        )

    if not do_login:
        return CheckResult(
            status="env_ok",
            missing_env=[],
            simulation=None,
            contracts_ok=False,
            snapshot_ok=False,
            message="Env loaded. Login skipped.",
        )

    api_key = os.getenv("SHIOAJI_APIKEY")
    secret_key = os.getenv("SHIOAJI_SECRET")

    api = sj.Shioaji(simulation=True)
    try:
        api.login(api_key, secret_key)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            status="login_failed",
            missing_env=[],
            simulation=getattr(api, "simulation", None),
            contracts_ok=False,
            snapshot_ok=False,
            message=str(exc),
        )

    contracts_ok = False
    snapshot_ok = False
    try:
        contract = api.Contracts.Stocks.get("2330") or api.Contracts.Stocks.get("2330.TW")
        if contract:
            contracts_ok = True
            try:
                api.snapshots([contract])
                snapshot_ok = True
            except Exception:
                snapshot_ok = False
    except Exception:
        contracts_ok = False

    try:
        api.logout()
    except Exception:
        pass

    return CheckResult(
        status="ok",
        missing_env=[],
        simulation=getattr(api, "simulation", None),
        contracts_ok=contracts_ok,
        snapshot_ok=snapshot_ok,
        message="Login ok." if contracts_ok else "Login ok, but contract lookup failed.",
    )


def _print_result(result: CheckResult) -> None:
    if result.status == "missing_env":
        print("Missing env:", ", ".join(result.missing_env))
        return
    if result.status == "missing_shioaji":
        print("shioaji not installed.")
        return
    if result.status == "login_failed":
        print("Login failed:", result.message)
        return

    print("Status:", result.status)
    print("Simulation:", result.simulation)
    print("Contracts OK:", result.contracts_ok)
    print("Snapshot OK:", result.snapshot_ok)
    print("Message:", result.message)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Shioaji environment and login")
    parser.add_argument("--skip-login", action="store_true", help="Skip login and live checks")
    args = parser.parse_args()

    result = run_check(do_login=not args.skip_login)
    _print_result(result)


if __name__ == "__main__":
    main()
