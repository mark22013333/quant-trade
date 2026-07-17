from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parent
PRODUCTION_DATABASE_URL = f"sqlite:///{(PROJECT_ROOT / 'data' / 'quant_trade.db').as_posix()}"

_pytest_database_root: Path | None = None
_configured_test_database_url = os.getenv("PYTEST_DATABASE_URL", "").strip()

if _configured_test_database_url:
    if _configured_test_database_url == PRODUCTION_DATABASE_URL:
        raise RuntimeError("PYTEST_DATABASE_URL 不得指向正式本機資料庫 data/quant_trade.db")
    TEST_DATABASE_URL = _configured_test_database_url
else:
    _pytest_database_root = Path(tempfile.mkdtemp(prefix="quant-trade-pytest-"))
    TEST_DATABASE_URL = f"sqlite:///{(_pytest_database_root / 'pytest.sqlite').as_posix()}"

# conftest 會在測試模組收集前載入，必須在任何 app/web import 建立 engine 前完成覆寫。
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["QUANT_TRADE_PYTEST_DATABASE_ISOLATED"] = "1"


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    if _pytest_database_root is not None:
        shutil.rmtree(_pytest_database_root, ignore_errors=True)
