"""Sync Sinotrade Shioaji AI assistant docs (llms.txt / llms-full.txt)."""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple
import urllib.error
import urllib.request


LLMS_URLS = {
    "llms.txt": "https://sinotrade.github.io/llms.txt",
    "llms-full.txt": "https://sinotrade.github.io/llms-full.txt",
}
CACHE_DAYS = 7


@dataclass
class SyncResult:
    path: Path
    url: str
    status: str
    bytes_written: int = 0
    error: str | None = None


def _is_fresh(path: Path, cache_days: int) -> bool:
    if not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < cache_days * 86400


def _download_with_urllib(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read()


def _download_with_requests(url: str) -> bytes:
    import certifi
    import requests

    response = requests.get(url, timeout=20, verify=certifi.where())
    response.raise_for_status()
    return response.content


def _download(url: str) -> bytes:
    try:
        return _download_with_urllib(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return _download_with_requests(url)


def sync_ai_docs(dest_dir: str | Path = "docs/shioaji", force: bool = False) -> Dict[str, SyncResult]:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    results: Dict[str, SyncResult] = {}
    for filename, url in LLMS_URLS.items():
        out_path = dest / filename
        if not force and _is_fresh(out_path, CACHE_DAYS):
            results[filename] = SyncResult(
                path=out_path,
                url=url,
                status="cached",
            )
            continue

        try:
            content = _download(url)
            out_path.write_bytes(content)
            results[filename] = SyncResult(
                path=out_path,
                url=url,
                status="downloaded",
                bytes_written=len(content),
            )
        except Exception as exc:  # noqa: BLE001
            results[filename] = SyncResult(
                path=out_path,
                url=url,
                status="error",
                error=str(exc),
            )

    return results


def _format_result(name: str, result: SyncResult) -> str:
    if result.status == "cached":
        mtime = datetime.fromtimestamp(result.path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        size = result.path.stat().st_size / 1024
        return f"{name}: cached ({mtime}, {size:.0f} KB)"
    if result.status == "downloaded":
        size = result.bytes_written / 1024
        return f"{name}: downloaded ({size:.0f} KB)"
    return f"{name}: error ({result.error})"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Shioaji AI assistant docs")
    parser.add_argument("--force", action="store_true", help="Ignore cache and force download")
    args = parser.parse_args()

    results = sync_ai_docs(force=args.force)
    for name, result in results.items():
        print(_format_result(name, result))


if __name__ == "__main__":
    main()
