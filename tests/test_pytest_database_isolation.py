from __future__ import annotations

from pathlib import Path

from app.config import PROJECT_ROOT, load_config


def test_pytest_database_is_not_the_production_local_database() -> None:
    config = load_config()
    production_url = f"sqlite:///{(Path(PROJECT_ROOT) / 'data' / 'quant_trade.db').as_posix()}"

    assert config.database_url != production_url
    assert config.database_url.startswith("sqlite:///")
