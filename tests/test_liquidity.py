from __future__ import annotations

import json

import pytest


class _FakeHeaders:
    def __init__(self, content_type: str) -> None:
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self._body = body
        self.headers = _FakeHeaders(content_type)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_fetch_twse_daily_all_reads_openapi_json(monkeypatch):
    from data import liquidity

    records = [
        {
            "Date": "1150728",
            "Code": "2330",
            "TradeValue": "1000000",
            "TradeVolume": "20000",
        }
    ]

    def fake_urlopen(request, **kwargs):  # noqa: ANN001, ANN003
        assert request.full_url == liquidity.TWSE_DAILY_ALL_URL
        assert request.get_header("Accept") == "application/json"
        return _FakeResponse(json.dumps(records).encode())

    monkeypatch.setattr(liquidity.urllib.request, "urlopen", fake_urlopen)

    assert liquidity._fetch_twse_daily_all() == records


def test_fetch_twse_daily_all_rejects_non_json_response(monkeypatch):
    from data import liquidity

    monkeypatch.setattr(
        liquidity.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _FakeResponse(b"Date,Code", "text/csv"),
    )

    with pytest.raises(RuntimeError, match="unexpected content type: text/csv"):
        liquidity._fetch_twse_daily_all()


def test_get_top_liquid_stocks_normalizes_and_sorts_openapi_records(monkeypatch, tmp_path):
    from data import liquidity

    records = [
        {"Date": "1150728", "Code": "1101", "TradeValue": "1,000", "TradeVolume": "100"},
        {"Date": "1150728", "Code": "2330", "TradeValue": "9,000", "TradeVolume": "300"},
        {"Date": "1150728", "Code": "0050", "TradeValue": "8,000", "TradeVolume": "200"},
    ]
    monkeypatch.setattr(liquidity, "_fetch_twse_daily_all", lambda: records)
    monkeypatch.setattr(liquidity, "DEFAULT_CACHE_DIR", tmp_path)

    result = liquidity.get_top_liquid_stocks(top_n=2)

    assert result.df["code"].tolist() == ["2330", "0050"]
    assert result.df["turnover"].tolist() == [9000, 8000]
    assert result.source == liquidity.TWSE_DAILY_ALL_URL
    assert result.cached is False
    assert (tmp_path / "liquidity_1150728.csv").exists()
