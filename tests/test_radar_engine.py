from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pytest

from app.alerts import radar_engine
from app.services.market_context import classify_tw_symbol_group


@dataclass
class _Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class _Shareholding:
    major_holder_ratio: float
    retail_holder_ratio: float


@dataclass
class _Holding:
    concentration_proxy: float
    dispersion_proxy: float


@dataclass
class _Chip:
    foreign_net_buy: float
    investment_trust_net_buy: float


class _Repo:
    def __init__(
        self,
        bars: dict[str, list[_Bar]],
        disposition_symbols: set[str],
        reliability: dict | None = None,
        symbol_feedback: dict | None = None,
        attention_symbols: set[str] | None = None,
    ):
        self._bars = bars
        self._disposition_symbols = disposition_symbols
        self._attention_symbols = attention_symbols or set()
        self._reliability = reliability or {}
        self._symbol_feedback = symbol_feedback or {}

    def get_daily_bars(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        return self._bars.get(symbol, [])

    def get_active_disposition(self, symbol: str, on_date):  # noqa: ANN001, ARG002
        return {"active": symbol in self._disposition_symbols}

    def get_active_attention(self, symbol: str, on_date):  # noqa: ANN001, ARG002
        return {"active": symbol in self._attention_symbols}

    def get_institutional_chip_rollup(self, symbol: str, end_date, lookback_days=5):  # noqa: ANN001, ARG002
        if symbol == "AAA":
            return {"institutional_net_buy_sum": 120_000_000.0}
        if symbol == "BBB":
            return {"institutional_net_buy_sum": 55_000_000.0}
        return {"institutional_net_buy_sum": 20_000_000.0}

    def get_institutional_chip(self, symbol: str, start_date=None, end_date=None):  # noqa: ANN001, ARG002
        return [_Chip(1.0, 1.0) for _ in range(30)]

    def is_chip_concentration_up(self, symbol: str, end_date, days=3):  # noqa: ANN001, ARG002
        return symbol in {"AAA", "BBB"}

    def get_latest_shareholding(self, symbol: str, on_or_before=None):  # noqa: ANN001, ARG002
        return _Shareholding(major_holder_ratio=35.0, retail_holder_ratio=12.0)

    def get_latest_holding_shares_per(self, symbol: str, on_or_before=None):  # noqa: ANN001, ARG002
        return _Holding(concentration_proxy=40.0, dispersion_proxy=10.0)

    def get_instrument_name(self, symbol: str) -> str:
        return symbol

    def get_radar_signal_reliability(self, **kwargs):  # noqa: ANN003, ARG002
        return self._reliability

    def get_daily_radar_symbol_adoption_feedback(self, **kwargs):  # noqa: ANN003, ARG002
        return self._symbol_feedback


@pytest.fixture(autouse=True)
def _empty_symbol_exclusions(monkeypatch):
    monkeypatch.setattr(radar_engine, "load_symbol_exclusions", lambda: {})


def _make_bars(base_close: float) -> list[_Bar]:
    rows: list[_Bar] = []
    start = date(2025, 12, 1)
    for idx in range(65):
        close = base_close + idx * 0.1
        rows.append(
            _Bar(
                date=date.fromordinal(start.toordinal() + idx),
                open=close * 0.99,
                high=close * 1.01,
                low=close * 0.98,
                close=close,
                volume=2000.0,
            )
        )
    return rows


def test_generate_daily_radar_emits_action_labels(monkeypatch):
    bars = {
        "AAA": _make_bars(100.0),
        "BBB": _make_bars(80.0),
        "CCC": _make_bars(60.0),
        "DDD": _make_bars(80.0),
    }
    repo = _Repo(bars=bars, disposition_symbols={"CCC"}, attention_symbols={"BBB"})

    def fake_signal(df):  # noqa: ANN001
        close = float(df["close"].iloc[-1])
        if close >= 106:
            return {
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": "rsi3<20",
                "close": close,
                "ma60": close * 0.9,
                "volume": 3000.0,
                "volume_ma5": 1200.0,
                "score": 0.9,
                "risk_score": 0.8,
            }
        if close >= 86:
            return {
                "entry": False,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": False,
                "trigger_reason": "none",
                "close": close,
                "ma60": close * 0.92,
                "volume": 2600.0,
                "volume_ma5": 1300.0,
                "score": 0.75,
                "risk_score": 0.6,
            }
        return {
            "entry": False,
            "trend_ok": True,
            "vol_ok": False,
            "trigger": False,
            "trigger_reason": "none",
            "close": close,
            "ma60": close * 0.95,
            "volume": 800.0,
            "volume_ma5": 1500.0,
            "score": 0.45,
            "risk_score": 0.3,
        }

    monkeypatch.setattr(radar_engine, "evaluate_entry_signal", fake_signal)

    rows = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["AAA", "BBB", "CCC", "DDD"],
        trade_date=bars["AAA"][-1].date,
        target_count=15,
        data_freshness_required=True,
    )

    assert len(rows) == 4
    label_map = {item["symbol"]: item["action_label"] for item in rows}
    assert label_map["AAA"] == "ENTRY_READY"
    assert label_map["DDD"] == "WATCH_WAIT_TRIGGER"
    assert label_map["BBB"] == "NOT_RECOMMENDED"
    assert label_map["CCC"] == "NOT_RECOMMENDED"
    ccc = next(item for item in rows if item["symbol"] == "CCC")
    bbb = next(item for item in rows if item["symbol"] == "BBB")
    assert "B_DISPOSITION_ACTIVE" in ccc["blocker_tags"]
    assert "B_ATTENTION_ACTIVE" in bbb["blocker_tags"]


def test_generate_daily_radar_aggressive_allows_soft_entry(monkeypatch):
    bars = {
        "AAA": _make_bars(100.0),
        "BBB": _make_bars(80.0),
    }
    repo = _Repo(bars=bars, disposition_symbols=set())

    def fake_signal(df):  # noqa: ANN001
        close = float(df["close"].iloc[-1])
        if close >= 106:
            return {
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": "rsi3<20",
                "close": close,
                "ma60": close * 0.9,
                "volume": 3000.0,
                "volume_ma5": 1200.0,
                "score": 0.9,
                "risk_score": 0.8,
            }
        return {
            "entry": False,
            "trend_ok": True,
            "vol_ok": True,
            "trigger": False,
            "trigger_reason": "none",
            "close": close,
            "ma60": close * 0.92,
            "volume": 2600.0,
            "volume_ma5": 1300.0,
            "score": 0.75,
            "risk_score": 0.6,
        }

    monkeypatch.setattr(radar_engine, "evaluate_entry_signal", fake_signal)

    balanced = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["AAA", "BBB"],
        trade_date=bars["AAA"][-1].date,
        target_count=15,
        data_freshness_required=True,
        risk_profile="balanced",
    )
    aggressive = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["AAA", "BBB"],
        trade_date=bars["AAA"][-1].date,
        target_count=15,
        data_freshness_required=True,
        risk_profile="aggressive",
    )

    b_balanced = next(item for item in balanced if item["symbol"] == "BBB")
    b_aggressive = next(item for item in aggressive if item["symbol"] == "BBB")
    assert b_balanced["action_label"] == "WATCH_WAIT_TRIGGER"
    assert b_aggressive["action_label"] == "ENTRY_READY"


def test_generate_daily_radar_conservative_tightens_entry_and_liquidity(monkeypatch):
    def fake_score_symbol(repo, symbol, trade_date, risk_profile, signal_evaluator):  # noqa: ANN001, ARG001
        return SimpleNamespace(
            symbol=symbol,
            name=f"Name{symbol}",
            price=100.0,
            signal={
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": "rsi3<20",
                "close": 100.0,
                "ma60": 95.0,
                "volume": 2200.0,
                "volume_ma5": 1300.0,
                "risk_score": 0.5,
            },
            last_bar_date="2024-04-01",
            data_quality="fresh",
            disposition_active=False,
            attention_active=False,
            liquidity_score=0.30,
            chip_available=True,
            chip_up3=True,
            industry_group="電子/科技",
            atr_pct=0.045,
            atr_heat="elevated",
            total_score=0.74,
            risk_score=0.5,
            fundamental_data_quality="fresh",
            revenue_score=0.6,
            quality_score=0.6,
            valuation_or_growth_score=0.6,
            news_risk_score=0.4,
            fundamental_summary={},
            news_summary={},
        )

    monkeypatch.setattr(radar_engine, "score_symbol", fake_score_symbol)
    monkeypatch.setattr(radar_engine, "compute_group_trend_metrics", lambda *args, **kwargs: {})
    repo = _Repo(bars={}, disposition_symbols=set())

    balanced = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["2330"],
        trade_date=date(2024, 4, 1),
        risk_profile="balanced",
    )
    conservative = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["2330"],
        trade_date=date(2024, 4, 1),
        risk_profile="conservative",
    )

    assert balanced[0]["action_label"] == "ENTRY_READY"
    assert "B_LOW_LIQUIDITY" not in balanced[0]["blocker_tags"]
    assert conservative[0]["action_label"] == "NOT_RECOMMENDED"
    assert "B_LOW_LIQUIDITY" in conservative[0]["blocker_tags"]
    assert "PROFILE=conservative" in conservative[0]["reason_tags"]


def test_generate_daily_radar_applies_threshold_offset(monkeypatch):
    def fake_score_symbol(repo, symbol, trade_date, risk_profile, signal_evaluator):  # noqa: ANN001, ARG001
        return SimpleNamespace(
            symbol=symbol,
            name=f"Name{symbol}",
            price=100.0,
            signal={
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": "rsi3<20",
                "close": 100.0,
                "ma60": 95.0,
                "volume": 2200.0,
                "volume_ma5": 1300.0,
                "risk_score": 0.5,
            },
            last_bar_date="2024-04-01",
            data_quality="fresh",
            disposition_active=False,
            attention_active=False,
            liquidity_score=0.50,
            chip_available=True,
            chip_up3=True,
            industry_group="電子/科技",
            atr_pct=0.035,
            atr_heat="normal",
            total_score=0.74,
            risk_score=0.5,
            fundamental_data_quality="fresh",
            revenue_score=0.6,
            quality_score=0.6,
            valuation_or_growth_score=0.6,
            news_risk_score=0.4,
            fundamental_summary={},
            news_summary={},
        )

    monkeypatch.setattr(radar_engine, "score_symbol", fake_score_symbol)
    monkeypatch.setattr(radar_engine, "compute_group_trend_metrics", lambda *args, **kwargs: {})
    repo = _Repo(bars={}, disposition_symbols=set())

    baseline = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["2330"],
        trade_date=date(2024, 4, 1),
        risk_profile="balanced",
    )
    stricter = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["2330"],
        trade_date=date(2024, 4, 1),
        risk_profile="balanced",
        threshold_offset=4.0,
    )

    assert baseline[0]["action_label"] == "ENTRY_READY"
    assert stricter[0]["action_label"] == "WATCH_WAIT_TRIGGER"
    assert "THRESHOLD_OFFSET=4.0" in stricter[0]["reason_tags"]
    assert "ENTRY_SCORE_MIN=76.0" in stricter[0]["reason_tags"]


def test_generate_daily_radar_orders_entry_by_risk_adjusted_score(monkeypatch):
    def fake_score_symbol(repo, symbol, trade_date, risk_profile, signal_evaluator):  # noqa: ANN001, ARG001
        is_clean = symbol == "CLEAN"
        return SimpleNamespace(
            symbol=symbol,
            name=symbol,
            price=100.0,
            signal={
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": "rsi3<20",
                "close": 100.0,
                "ma60": 95.0,
                "volume": 2200.0,
                "volume_ma5": 1300.0,
                "risk_score": 0.2 if is_clean else 0.9,
            },
            last_bar_date="2024-04-01",
            data_quality="fresh",
            disposition_active=False,
            attention_active=False,
            liquidity_score=0.90,
            chip_available=True,
            chip_up3=True,
            industry_group="電子/科技",
            atr_pct=0.02 if is_clean else 0.07,
            atr_heat="normal" if is_clean else "hot",
            total_score=0.85 if is_clean else 0.92,
            risk_score=0.2 if is_clean else 0.9,
            fundamental_data_quality="fresh",
            revenue_score=0.6,
            quality_score=0.6,
            valuation_or_growth_score=0.6,
            news_risk_score=0.4,
            fundamental_summary={},
            news_summary={},
        )

    monkeypatch.setattr(radar_engine, "score_symbol", fake_score_symbol)
    monkeypatch.setattr(radar_engine, "compute_group_trend_metrics", lambda *args, **kwargs: {})
    repo = _Repo(bars={}, disposition_symbols=set())

    rows = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["HOT", "CLEAN"],
        trade_date=date(2024, 4, 1),
        risk_profile="balanced",
    )

    assert rows[0]["symbol"] == "CLEAN"
    assert rows[0]["risk_adjusted_score"] > rows[1]["risk_adjusted_score"]
    assert rows[0]["entry_score"] < rows[1]["entry_score"]
    assert "CHIP_AVAILABLE=True" in rows[0]["reason_tags"]
    assert any(tag.startswith("RISK_ADJUSTED_SCORE=") for tag in rows[0]["reason_tags"])


def test_generate_daily_radar_applies_group_diversity(monkeypatch):
    symbols = [
        "2301",
        "2302",
        "2303",
        "2304",
        "2305",
        "2306",
        "2307",
        "2308",
        "2801",
        "2802",
        "2803",
        "2804",
        "1201",
        "1202",
        "1203",
        "1204",
    ]
    repo = _Repo(bars={}, disposition_symbols=set())

    def fake_score_symbol(repo, symbol, trade_date, risk_profile, signal_evaluator):  # noqa: ANN001, ARG001
        group = classify_tw_symbol_group(symbol)
        base_score = 0.95 if group == "電子/科技" else (0.82 if group == "金融/保險" else 0.78)
        atr_pct = 0.065 if symbol == "2301" else 0.025
        atr_heat = "hot" if atr_pct >= 0.06 else "normal"
        return SimpleNamespace(
            symbol=symbol,
            name=symbol,
            price=100.0,
            signal={
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": "rsi3<20",
                "close": 100.0,
                "ma60": 90.0,
                "volume": 3000.0,
                "volume_ma5": 1200.0,
            },
            last_bar_date=trade_date.isoformat(),
            data_quality="fresh",
            disposition_active=False,
            attention_active=False,
            chip_available=True,
            chip_up3=True,
            liquidity_score=0.9,
            total_score=base_score,
            risk_score=0.3 if symbol == "2301" else 0.7,
            industry_group=group,
            atr_pct=atr_pct,
            atr_heat=atr_heat,
            fundamental_data_quality="fresh",
            revenue_score=0.8,
            quality_score=0.8,
            valuation_or_growth_score=0.8,
            news_risk_score=0.8,
            fundamental_summary={},
            news_summary={},
        )

    monkeypatch.setattr(radar_engine, "score_symbol", fake_score_symbol)

    rows = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=symbols,
        trade_date=date(2026, 1, 15),
        target_count=10,
        data_freshness_required=True,
        risk_profile="balanced",
    )

    groups = [item["industry_group"] for item in rows]
    electronics_count = sum(1 for group in groups if group == "電子/科技")
    hot_item = next(item for item in rows if item["symbol"] == "2301")
    assert len(rows) == 10
    assert electronics_count <= 4
    assert hot_item["industry_rotation_score"] > 0
    assert hot_item["industry_trend_label"] == "neutral"
    assert "industry_trend_score" in hot_item
    assert any(tag.startswith("INDUSTRY_ROTATION_SCORE=") for tag in hot_item["reason_tags"])
    assert any(tag.startswith("INDUSTRY_TREND=") for tag in hot_item["reason_tags"])
    assert "ATR_HEAT=hot" in hot_item["reason_tags"]
    assert any(group == "金融/保險" for group in groups)
    assert any(group == "食品" for group in groups)


def test_generate_daily_radar_marks_excluded_symbols(monkeypatch):
    bars = {"AAA": _make_bars(100.0), "BBB": _make_bars(80.0)}
    repo = _Repo(bars=bars, disposition_symbols=set())
    monkeypatch.setattr(radar_engine, "load_symbol_exclusions", lambda: {"AAA": "event risk"})

    def fake_signal(df):  # noqa: ANN001
        close = float(df["close"].iloc[-1])
        return {
            "entry": True,
            "trend_ok": True,
            "vol_ok": True,
            "trigger": True,
            "trigger_reason": "rsi3<20",
            "close": close,
            "ma60": close * 0.9,
            "volume": 3000.0,
            "volume_ma5": 1200.0,
            "score": 0.9,
            "risk_score": 0.8,
        }

    monkeypatch.setattr(radar_engine, "evaluate_entry_signal", fake_signal)

    rows = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["AAA", "BBB"],
        trade_date=bars["AAA"][-1].date,
        target_count=10,
        data_freshness_required=True,
    )

    aaa = next(item for item in rows if item["symbol"] == "AAA")
    assert aaa["action_label"] == "NOT_RECOMMENDED"
    assert "B_EXCLUDED_SYMBOL" in aaa["blocker_tags"]
    assert "EXCLUDED_REASON=event risk" in aaa["reason_tags"]


def test_generate_daily_radar_uses_outcome_reliability_in_sort(monkeypatch):
    reliability = {
        "status": "ok",
        "baseline_positive_rate": 0.5,
        "baseline_average_return": 0.0,
        "buckets": {
            "TRIGGER_REASON=breakout": {
                "evaluated_count": 20,
                "positive_rate": 0.72,
                "average_return": 0.012,
                "horizons": [
                    {"lookahead_bars": 5, "evaluated_count": 10, "positive_count": 7, "positive_rate": 0.7, "average_return": 0.01},
                    {"lookahead_bars": 10, "evaluated_count": 10, "positive_count": 8, "positive_rate": 0.8, "average_return": 0.014},
                ],
            },
            "TRIGGER_REASON=rsi3<20": {
                "evaluated_count": 20,
                "positive_rate": 0.36,
                "average_return": -0.008,
                "horizons": [
                    {"lookahead_bars": 5, "evaluated_count": 10, "positive_count": 4, "positive_rate": 0.4, "average_return": -0.004},
                    {"lookahead_bars": 10, "evaluated_count": 10, "positive_count": 3, "positive_rate": 0.3, "average_return": -0.012},
                ],
            },
        },
    }
    repo = _Repo(bars={}, disposition_symbols=set(), reliability=reliability)

    def fake_score_symbol(repo, symbol, trade_date, risk_profile, signal_evaluator):  # noqa: ANN001, ARG001
        trigger_reason = "breakout" if symbol == "BBB" else "rsi3<20"
        return SimpleNamespace(
            symbol=symbol,
            name=symbol,
            price=100.0,
            signal={
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": trigger_reason,
                "close": 100.0,
                "ma60": 90.0,
                "volume": 3000.0,
                "volume_ma5": 1200.0,
            },
            last_bar_date=trade_date.isoformat(),
            data_quality="fresh",
            disposition_active=False,
            attention_active=False,
            chip_available=True,
            chip_up3=True,
            liquidity_score=0.9,
            total_score=0.8,
            risk_score=0.6,
            industry_group="測試族群",
            atr_pct=0.02,
            atr_heat="normal",
            fundamental_data_quality="fresh",
            revenue_score=0.8,
            quality_score=0.8,
            valuation_or_growth_score=0.8,
            news_risk_score=0.8,
            fundamental_summary={},
            news_summary={},
        )

    monkeypatch.setattr(radar_engine, "score_symbol", fake_score_symbol)

    rows = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["AAA", "BBB"],
        trade_date=date(2026, 1, 15),
        target_count=10,
        data_freshness_required=True,
    )

    assert rows[0]["symbol"] == "BBB"
    assert rows[0]["outcome_reliability_boost"] > 0
    assert rows[1]["outcome_reliability_boost"] < 0
    assert rows[0]["outcome_reliability_horizons"][0]["lookahead_bars"] == 5
    assert any(tag.startswith("OUTCOME_RELIABILITY_SCORE=") for tag in rows[0]["reason_tags"])
    assert any(tag.startswith("OUTCOME_RELIABILITY_HORIZONS=") for tag in rows[0]["reason_tags"])


def test_generate_daily_radar_uses_symbol_adoption_feedback_in_sort(monkeypatch):
    symbol_feedback = {
        "status": "ok",
        "symbols": {
            "BBB": {
                "symbol": "BBB",
                "evaluated_count": 4,
                "positive_rate": 1.0,
                "average_return": 0.035,
                "score": 0.9,
                "boost": 3.6,
                "horizons": [{"lookahead_bars": 5, "evaluated_count": 2, "positive_rate": 1.0, "average_return": 0.03}],
            },
            "AAA": {
                "symbol": "AAA",
                "evaluated_count": 4,
                "positive_rate": 0.25,
                "average_return": -0.02,
                "score": -0.8,
                "boost": -3.2,
                "horizons": [{"lookahead_bars": 5, "evaluated_count": 2, "positive_rate": 0.0, "average_return": -0.02}],
            },
        },
    }
    repo = _Repo(bars={}, disposition_symbols=set(), symbol_feedback=symbol_feedback)

    def fake_score_symbol(repo, symbol, trade_date, risk_profile, signal_evaluator):  # noqa: ANN001, ARG001
        return SimpleNamespace(
            symbol=symbol,
            name=symbol,
            price=100.0,
            signal={
                "entry": True,
                "trend_ok": True,
                "vol_ok": True,
                "trigger": True,
                "trigger_reason": "breakout",
                "close": 100.0,
                "ma60": 90.0,
                "volume": 3000.0,
                "volume_ma5": 1200.0,
            },
            last_bar_date=trade_date.isoformat(),
            data_quality="fresh",
            disposition_active=False,
            attention_active=False,
            chip_available=True,
            chip_up3=True,
            liquidity_score=0.9,
            total_score=0.8,
            risk_score=0.55,
            industry_group="測試族群",
            atr_pct=0.02,
            atr_heat="normal",
            fundamental_data_quality="fresh",
            revenue_score=0.8,
            quality_score=0.8,
            valuation_or_growth_score=0.8,
            news_risk_score=0.8,
            fundamental_summary={},
            news_summary={},
        )

    monkeypatch.setattr(radar_engine, "score_symbol", fake_score_symbol)

    rows = radar_engine.generate_daily_radar(
        repo=repo,
        symbols=["AAA", "BBB"],
        trade_date=date(2026, 1, 15),
        target_count=10,
        data_freshness_required=True,
    )

    assert rows[0]["symbol"] == "BBB"
    assert rows[0]["symbol_adoption_feedback_boost"] > 0
    assert rows[0]["symbol_adoption_feedback_raw_boost"] > rows[0]["symbol_adoption_feedback_boost"]
    assert rows[0]["symbol_adoption_feedback_confidence_level"] == "medium"
    assert rows[0]["symbol_adoption_feedback_confidence_weight"] > 0
    assert rows[1]["symbol_adoption_feedback_boost"] < 0
    assert rows[0]["symbol_adoption_feedback_horizons"][0]["lookahead_bars"] == 5
    assert any(tag.startswith("SYMBOL_ADOPTION_FEEDBACK_RAW_BOOST=") for tag in rows[0]["reason_tags"])
    assert any(tag.startswith("SYMBOL_ADOPTION_FEEDBACK_BOOST=") for tag in rows[0]["reason_tags"])
    assert any(tag.startswith("SYMBOL_ADOPTION_FEEDBACK_CONFIDENCE=") for tag in rows[0]["reason_tags"])
