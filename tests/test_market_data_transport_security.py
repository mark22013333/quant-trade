from __future__ import annotations

import pytest


def test_universe_download_rejects_insecure_fallback_by_default(monkeypatch):
    from data import universe

    def fake_urlopen(*args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("cert failed")

    monkeypatch.delenv("ALLOW_INSECURE_MARKET_DATA", raising=False)
    monkeypatch.setattr(universe.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="ALLOW_INSECURE_MARKET_DATA"):
        universe._download_bytes("https://example.test/universe.csv")


def test_liquidity_rejects_insecure_fallback_by_default(monkeypatch):
    from data import liquidity

    def fake_urlopen(*args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("cert failed")

    monkeypatch.delenv("ALLOW_INSECURE_MARKET_DATA", raising=False)
    monkeypatch.setattr(liquidity.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="ALLOW_INSECURE_MARKET_DATA"):
        liquidity._fetch_twse_daily_all()
