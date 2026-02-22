from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


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
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    try:
        import uvicorn
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("找不到 uvicorn，請先安裝 requirements：{exc}")

    reload_dirs = args.reload_dirs or ["web", "tools", "analysis"]
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
