from __future__ import annotations

import argparse
import faulthandler
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _is_loopback_host(host: str) -> bool:
    value = str(host or "").strip().lower()
    return value in {"127.0.0.1", "localhost", "::1"}


def validate_bind_security(host: str, token: str | None = None) -> None:
    if not _is_loopback_host(str(host)) and not str(token or os.getenv("CONTROL_PANEL_TOKEN", "")).strip():
        raise SystemExit("綁定非 localhost 時必須設定 CONTROL_PANEL_TOKEN，避免控制台與 reports 被外部直接存取。")


def ensure_runtime_cache_dirs() -> None:
    """
    Ensure native libs (matplotlib/fontconfig) use writable cache directories.
    """
    cache_root = PROJECT_ROOT / ".cache"
    mpl_cache = cache_root / "matplotlib"
    cache_root.mkdir(parents=True, exist_ok=True)
    mpl_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_cache))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start quant-trade web control panel")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.getenv("WEB_RELOAD", "0") == "1",
        help="Enable auto reload (or set WEB_RELOAD=1)",
    )
    parser.add_argument(
        "--reload-dir",
        action="append",
        dest="reload_dirs",
        help="Extra reload directory. Can be used multiple times.",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log level",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    os.chdir(PROJECT_ROOT)
    faulthandler.enable(all_threads=True)
    ensure_runtime_cache_dirs()
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    os.environ["CONTROL_PANEL_BIND_HOST"] = str(args.host)
    validate_bind_security(str(args.host))

    try:
        import uvicorn
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"找不到 uvicorn，請先安裝 requirements：{exc}")

    reload_dirs = args.reload_dirs or ["web", "tools", "analysis", "strategies", "backtest", "data", "app"]
    uvicorn.run(
        "web.control_panel_app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=reload_dirs if args.reload else None,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
