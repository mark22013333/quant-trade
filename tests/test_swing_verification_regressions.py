"""現場驗收發現的研究回歸；只用合成資料、記憶體 SQLite 與 ASGI。"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.swing_source_import import import_source_bundle, preview_source_bundle
from app.db.swing_repository import canonical_json, content_hash, now_us, to_us
from app.paper.portfolio import PortfolioLedger, freeze_batch, insert_row
from app.pipeline.swing_research import SwingResearchService


CUTOFF = "2026-07-27T15:00:00+08:00"
OPENING = "2026-07-28T09:00:00+08:00"
SYMBOLS = ["0050", "2330", "2317"]


def configuration(key, mode="historical_exploratory"):
    # 費用及滑價明示為合成零值，不代表正式交易成本。
    return {
        "experiment_key": key, "mode": mode, "etf_symbol": "0050", "stock_pool": ["2330", "2317"],
        "start_date": "2026-07-27", "end_date": "2026-07-31",
        "initial_cash_e4": 100_000_000, "stop_equity_e4": 80_000_000,
        "strategy": {"version": "swing-v1", "hold_days": 2, "stop_loss_pct": "0.10",
                     "parameters": {"enabled": {"momentum_trend": True, "mean_reversion": False, "chip_flow": False},
                                    "threshold": .6, "params": {"momentum_trend": {"volume_multiplier": 1.5}}}},
        "risk": {"max_position_e4": 40_000_000, "max_total_exposure_e4": 80_000_000, "max_single_risk_e4": 5_000_000},
        "fees": {kind: {"commission_rate": "0", "minimum_fee_e4": 0, "sell_tax_rate": "0"} for kind in ("stock", "etf")},
        "execution": {"slippage_bps": "0", "settlement_sessions": 2, "price_basis": "raw",
                      "corporate_actions": "cash_and_split", "odd_lot_model": "daily_proxy", "rounding": "ROUND_HALF_UP"},
        "data_policy": {"warmup_bars": 60, "missing_required": "block"},
        "observation_sessions": 2, "code_version": "offline-verification-regression",
    }


@pytest.fixture
def database():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def constraints(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=ON")

    ddl = Path(__file__).resolve().parents[1] / ".spec/tw-swing-ai-validation/deploy.sql"
    with engine.connect() as connection:
        connection.connection.driver_connection.executescript(ddl.read_text(encoding="utf-8"))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def market_bundle(*, strings=False, late_acquisition=True):
    warmup = [day.date().isoformat() for day in pd.bdate_range(end="2026-07-24", periods=60)]
    days = warmup + ["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31"]
    calendar = days + ["2026-08-03", "2026-08-04"]
    acquired = datetime.now(timezone.utc).isoformat()

    def source(kind, key, day, payload, public, symbol=None):
        return {"kind": kind, "source_key": key, "symbol": symbol, "data_period": day,
                "url": "https://www.twse.com.tw/offline-synthetic", "raw": deepcopy(payload),
                "public_at": public, "obtained_at": acquired if late_acquisition else public,
                "verified_at": acquired if late_acquisition else public, "quality": "verified",
                "attestation": {"confirmed": True, "statement": "僅供離線回歸的合成原始資料，已核對欄位及時間。"},
                "payload": payload}

    original = {"fixture_only": True, "result": "合成日曆與公司行動完整性聲明"}
    calendar_payload = {
        "schema_version": 1, "sessions": calendar, "symbols": SYMBOLS,
        "period_start": calendar[0], "period_end": calendar[-1], "corporate_actions_complete": True,
        "corporate_action_evidence": [{"url": "https://www.twse.com.tw/offline-synthetic", "raw": original,
                                       "raw_hash": content_hash(original), "symbols": SYMBOLS,
                                       "period_start": calendar[0], "period_end": calendar[-1]}],
    }
    sources = [source("calendar", "calendar", calendar[0], calendar_payload, "2026-01-01T00:00:00+08:00")]
    for symbol in SYMBOLS:
        for index, day in enumerate(days):
            opening = 100.0 if symbol == "0050" or index >= 60 else 40 + index * .5
            close = opening if index < 60 or symbol == "0050" else 100 + index - 60
            for phase in ("open", "close"):
                at = day + ("T09:00:00+08:00" if phase == "open" else "T13:30:00+08:00")
                payload = {"schema_version": 1, "phase": phase, "open": opening,
                           "available_at": at, "session_open_at": day + "T09:00:00+08:00"}
                if phase == "close":
                    payload.update(high=close + 1, low=opening - 1, close=close, volume=100000 if index == 60 else 10000)
                if strings:
                    for field in ("open", "high", "low", "close", "volume"):
                        if field in payload:
                            payload[field] = str(payload[field])
                sources.append(source("market_bar", f"{symbol}:{day}:{phase}", day, payload, at, symbol))
    return {"schema_version": 1, "bundle_key": "synthetic-market", "sources": sources}


def seed(session, *, strings=False, late_acquisition=True, malformed=None):
    bundle = market_bundle(strings=strings, late_acquisition=late_acquisition)
    if malformed:
        field, value = malformed
        target = next(row for row in bundle["sources"] if row["source_key"] == "2330:2026-07-27:close")
        target["payload"][field] = value
        # 官方匯入已拒絕非法數值；仍須防禦既有低階來源，並保留它的原始內容。
        assert not preview_source_bundle(bundle)["valid"]
        service = SwingResearchService(session)
        for row in bundle["sources"]:
            service.append_source({**row, "source": "legacy-synthetic"})
        session.commit()
        return bundle
    preview = preview_source_bundle(bundle)
    assert preview["valid"], preview["missing_fields"]
    result = import_source_bundle(session, bundle, expected_hash=content_hash(bundle),
                                  actor="離線回歸", note="合成數據，不代表正式來源或公司事件")
    session.commit()
    assert len(result["source_ids"]) == 391
    return bundle


def source_snapshot(session):
    return [tuple(row) for row in session.execute(text(
        "SELECT id,payload_json,payload_hash,public_at_us,obtained_at_us,verified_at_us FROM swing_source_versions ORDER BY id"))]


def candidates_or_fail(service, experiment):
    coverage = service.preflight(experiment["experiment_id"], cutoff_at=CUTOFF)
    assert coverage["qualified"], coverage["missing_fields"]
    assert coverage["bar_counts"] == {symbol: 61 for symbol in SYMBOLS}
    try:
        candidates, missing = service._candidates(experiment["config"], coverage["inputs"], CUTOFF)
    except (TypeError, ValueError, ArithmeticError) as exc:
        pytest.fail(f"合法來源 OHLCV 應可進入實際策略計算，卻發生 {type(exc).__name__}: {exc}")
    assert not missing, missing
    assert {candidate["symbol"] for candidate in candidates} == {"2330", "2317"}
    assert all(candidate["quantity"] == 40 and candidate["score"] == 1 for candidate in candidates)
    return candidates


class Researcher:
    def analyze_batch(self, candidates, cutoff_at):
        assert to_us(cutoff_at) == to_us(CUTOFF)
        return {"status": "normal", "items": [
            {"symbol": candidate["symbol"], "decision_version": "synthetic-event-v1",
             "event_type": "重大裁罰" if candidate["symbol"] == "2330" else "營運正常",
             "exclude_recommended": candidate["symbol"] == "2330",
             "sources": [{"url": "https://www.twse.com.tw/offline/" + candidate["symbol"],
                          "published_at": "2026-07-27T10:00:00+08:00"}]}
            for candidate in candidates]}


class Fetcher:
    def fetch(self, url, timeout):
        symbol = url.rsplit("/", 1)[-1]
        original = f"公司代碼 {symbol}。公司公告：" + (
            "本公司遭主管機關重大裁罰新台幣一億元。" if symbol == "2330" else "本公司本月無重大裁罰。")
        # 公布時間早於 cutoff；實際取得、核驗時間保留今天，歷史研究不得假造過去的 AI 決策。
        at = datetime.now(timezone.utc).isoformat()
        return {"url": url, "original_text": original, "excerpt": original,
                "published_at": "2026-07-27T10:00:00+08:00", "obtained_at": at, "verified_at": at}


def run_numeric(session, key):
    service = SwingResearchService(session)
    seed(session)
    experiment = service.create_experiment(configuration(key), "離線回歸", "確認合成零成本設定")
    candidates_or_fail(service, experiment)
    before = now_us()
    result = service.run(experiment["experiment_id"], "research-27", CUTOFF,
                         researcher=Researcher(), fetcher=Fetcher())
    assert result["status"] == "frozen", result
    rows = session.execute(text(
        "SELECT d.*,b.cutoff_at_us,b.decision_at_us FROM swing_candidate_decisions d "
        "JOIN swing_input_batches b ON b.id=d.batch_id ORDER BY d.quant_rank")).mappings().all()
    assert {row["symbol"]: row["ai_outcome"] for row in rows} == {"2330": "veto", "2317": "allow"}
    assert all(row["cutoff_at_us"] == to_us(CUTOFF) and row["decision_at_us"] >= before for row in rows)
    assert all(row["decision_at_us"] > to_us(OPENING) for row in rows)
    return service, experiment, result, rows


def test_imported_string_ohlcv_matches_actual_numeric_candidates(database):
    with database() as session:
        bundle = seed(session, strings=True)
        service = SwingResearchService(session)
        experiment = service.create_experiment(configuration("string-candidates"), "離線回歸", "確認")
        before = source_snapshot(session)
        actual = candidates_or_fail(service, experiment)
        inputs = service.preflight(experiment["experiment_id"], cutoff_at=CUTOFF)["inputs"]
        numeric = deepcopy(inputs)
        for bars in numeric["market"].values():
            for bar in bars:
                for field in ("open", "high", "low", "close", "volume"):
                    bar[field] = float(bar[field])
        expected, missing = service._candidates(experiment["config"], numeric, CUTOFF)
        assert not missing and actual == expected
        assert source_snapshot(session) == before
        assert all(isinstance(row["payload"]["close"], str) for row in bundle["sources"] if row["kind"] == "market_bar" and row["payload"]["phase"] == "close")


@pytest.fixture
def api(database, monkeypatch, tmp_path):
    import app.db.session as db_session
    import app.services.subscription_research as subscription
    import web.control_panel_app as panel

    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "synthetic-regression-token")
    monkeypatch.setenv("CONTROL_PANEL_TRUST_PROXY_AUTH", "0")
    monkeypatch.setattr(db_session, "get_session_factory", lambda: database)
    monkeypatch.setattr(panel, "LOG_PATH", tmp_path / "control-panel.log")
    monkeypatch.setattr(subscription, "build_swing_research_dependencies", lambda: {"researcher": Researcher(), "fetcher": Fetcher()})
    # 不進入 context manager，不執行 lifespan，也不啟動任何監聽或背景服務。
    client = TestClient(panel.app)
    client.headers["X-Control-Panel-Token"] = "synthetic-regression-token"
    yield client
    client.close()


def test_historical_api_accepts_imported_string_ohlcv(database, api):
    with database() as session:
        seed(session, strings=True)
        service = SwingResearchService(session)
        experiment = service.create_experiment(configuration("string-api"), "離線回歸", "確認")
        session.commit()
        before = source_snapshot(session)
    response = api.post(f"/api/swing/experiments/{experiment['experiment_id']}/run", json={
        "run_key": "string-api", "cutoff_at": CUTOFF, "config_hash": experiment["config_hash"]})
    assert response.status_code == 200, f"合法字串 OHLCV 應回傳 200，實際：{response.status_code} {response.text}"
    assert response.json()["data"]["status"] == "frozen", response.text
    with database() as session:
        # 研究可追加 ai_evidence；原始來源及其原文必須逐位元保持相同。
        assert source_snapshot(session)[:len(before)] == before


@pytest.mark.parametrize("field,value", [
    ("close", "not-a-price"), ("open", "NaN"), ("high", "Infinity"),
    ("low", "-Infinity"), ("close", 0), ("open", -1), ("volume", -1),
])
def test_invalid_ohlcv_is_missing_without_mutating_source(database, api, field, value):
    with database() as session:
        seed(session, malformed=(field, value))
        service = SwingResearchService(session)
        experiment = service.create_experiment(configuration("invalid-" + field + str(value)), "離線回歸", "確認")
        session.commit()
        before = source_snapshot(session)
        coverage = service.preflight(experiment["experiment_id"], cutoff_at=CUTOFF)
        inputs = coverage["inputs"]
        assert inputs["market"]["2330"][-1][field] == value
        try:
            candidates, missing = service._candidates(experiment["config"], inputs, CUTOFF)
        except (TypeError, ValueError, ArithmeticError) as exc:
            pytest.fail(f"非法 OHLCV 應分類為 missing，不能丟出 {type(exc).__name__}: {exc}")
        assert any("2330" in item and "missing" in item for item in missing), missing
        assert all(candidate["symbol"] != "2330" for candidate in candidates)
        assert source_snapshot(session) == before
    response = api.post(f"/api/swing/experiments/{experiment['experiment_id']}/run", json={
        "run_key": "invalid-ohlcv", "cutoff_at": CUTOFF, "config_hash": experiment["config_hash"]})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "incomplete", response.text
    with database() as session:
        assert source_snapshot(session)[:len(before)] == before


@pytest.mark.parametrize("fixture_case", ["numeric-real-candidates"], ids=["tw-swing-ai-validation#AC-7#S01"])
def test_historical_research_replays_next_open_with_real_audit(database, fixture_case):
    with database() as session:
        service, experiment, frozen, _decisions = run_numeric(session, fixture_case)
        original_batches = [tuple(row) for row in session.execute(text("SELECT id,manifest_json,input_hash,decision_at_us FROM swing_input_batches ORDER BY id"))]
        original_sources = source_snapshot(session)
        observed = service.observe(experiment["experiment_id"], "2026-07-31T15:00:00+08:00", "observe-31")
        assert observed["status"] == "observed", observed
        buys = session.execute(text(
            "SELECT a.arm,l.symbol,l.trade_date FROM swing_ledger_events l JOIN swing_paper_accounts a ON a.id=l.account_id "
            "WHERE l.event_kind='buy' ORDER BY a.arm,l.symbol")).all()
        assert [tuple(row) for row in buys] == [
            ("A", "0050", "2026-07-28"), ("B", "2317", "2026-07-28"),
            ("B", "2330", "2026-07-28"), ("C", "2317", "2026-07-28"),
        ], f"歷史研究必須在次日開盤執行 B 兩筆／C 一筆；實際：{buys}"
        sells = session.execute(text("SELECT l.trade_date FROM swing_ledger_events l WHERE l.event_kind='sell'")).scalars().all()
        assert sells == ["2026-07-31"] * 3
        ledger = PortfolioLedger(session, experiment["experiment_id"])
        assert not ledger.snapshot("B")["positions"] and not ledger.snapshot("C")["positions"]
        outcomes = service.compare(experiment["experiment_id"])["ai_outcomes"]
        assert outcomes["allow"] == 1 and outcomes["veto"] == 1
        before_ledger = [tuple(row) for row in session.execute(text("SELECT * FROM swing_ledger_events ORDER BY id"))]
        replay = service.run(experiment["experiment_id"], "research-27", CUTOFF, researcher=Researcher(), fetcher=Fetcher())
        assert replay["batch_id"] == frozen["batch_id"] and replay["replayed"] is True
        assert service.observe(experiment["experiment_id"], "2026-07-31T15:00:00+08:00", "observe-31") == observed
        assert [tuple(row) for row in session.execute(text("SELECT * FROM swing_ledger_events ORDER BY id"))] == before_ledger
        assert [tuple(row) for row in session.execute(text("SELECT id,manifest_json,input_hash,decision_at_us FROM swing_input_batches ORDER BY id"))][:len(original_batches)] == original_batches
        assert source_snapshot(session) == original_sources


def test_historical_ledger_accepts_cutoff_before_fill_and_preserves_late_audit(database):
    with database() as session:
        service, experiment, _frozen, decisions = run_numeric(session, "historical-ledger")
        allowed = next(row for row in decisions if row["symbol"] == "2317")
        quant = json.loads(allowed["quant_json"])
        config = experiment["config"]
        calendars = service._load_inputs(config, CUTOFF)["calendar"]
        market, _source_ids = service._execution_market(config, "2317", "2026-07-28", calendars)
        result = PortfolioLedger(session, experiment["experiment_id"]).submit_order(
            "B", "2317", quant["quantity"], "buy", CUTOFF, market, "historical-late-audit",
            quant["stop_loss_price"], allowed["id"], calendar=calendars)
        assert result["status"] == "filled", f"歷史有效 cutoff 早於開盤，即使真實 audit 較晚仍應成交：{result}"
        assert session.execute(text("SELECT decision_at_us FROM swing_input_batches WHERE id=:id"), {"id": allowed["batch_id"]}).scalar_one() == allowed["decision_at_us"]


def manual_decision(session, mode, cutoff, decision_at):
    service = SwingResearchService(session)
    experiment = service.create_experiment(configuration("chronology-" + mode, mode), "離線回歸", "確認")
    batch = freeze_batch(session, experiment["experiment_id"], "manual-negative", to_us(cutoff),
                         {"schema_version": 1, "candidates": []}, decision_at_us=to_us(decision_at))
    quant = {"schema_version": 1, "symbol": "2317", "side": "buy", "quantity": 1, "signal_at": cutoff}
    decision = insert_row(session, "swing_candidate_decisions", {
        "experiment_id": experiment["experiment_id"], "batch_id": batch, "symbol": "2317", "quant_rank": 1,
        "quant_json": canonical_json(quant), "ai_outcome": "not_requested", "ai_json": "{}",
        "payload_hash": content_hash(quant), "created_at_us": max(now_us(), to_us(decision_at))})
    session.commit()
    return experiment, batch, decision


@pytest.mark.parametrize("signal,decision_at", [(CUTOFF, "2026-07-28T09:01:00+08:00"), (CUTOFF, CUTOFF)])
def test_prospective_late_or_same_session_decision_cannot_retrofill(database, signal, decision_at):
    with database() as session:
        experiment, _batch, decision = manual_decision(session, "prospective", signal, decision_at)
        # 第一個案例：次日開盤已過；第二個案例：只給同日開盤，收盤信號不得偷用。
        day = "2026-07-28" if decision_at != signal else "2026-07-27"
        market = [{"date": day, "open": 100, "prior_volume": 10000,
                   "available_at": day + "T09:00:00+08:00", "session_open_at": day + "T09:00:00+08:00"}]
        result = PortfolioLedger(session, experiment["experiment_id"]).submit_order(
            "B", "2317", 1, "buy", signal, market, "prospective-negative", "90", decision,
            calendar=["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"])
        assert result["status"] == "rejected", result
        assert result["reason"] == ("invalid_common_decision" if decision_at != signal else "missing_next_session")
        assert not PortfolioLedger(session, experiment["experiment_id"]).snapshot("B")["positions"]


@pytest.mark.parametrize("mode,cutoff,decision_at", [
    ("prospective", CUTOFF, "2026-07-28T09:01:00+08:00"),
    ("historical_exploratory", OPENING, "2026-07-28T09:01:00+08:00"),
    ("historical_exploratory", "2026-07-28T09:01:00+08:00", "2026-07-28T09:02:00+08:00"),
])
def test_sql_guard_rejects_invalid_mode_specific_chronology(database, mode, cutoff, decision_at):
    with database() as session:
        experiment, batch, decision = manual_decision(session, mode, cutoff, decision_at)
        ledger = PortfolioLedger(session, experiment["experiment_id"])
        previous = dict(ledger._latest("B"))
        previous.pop("id")
        previous.update(seq=previous["seq"] + 1, event_key="invalid-direct-sql", event_kind="buy",
                        prev_state_hash=previous["state_hash"], effective_at_us=to_us(OPENING),
                        batch_id=batch, decision_id=decision, decision_batch_id=batch, symbol="2317",
                        quantity=1, price_e4=1_000_000, trade_date="2026-07-28", settlement_date="2026-07-30")
        # 執行輸入 batch 另以合法開盤時間封存，確保觸發的是決策時間防線。
        previous["batch_id"] = freeze_batch(session, experiment["experiment_id"], "sql-execution", to_us(OPENING), {"schema_version": 1})
        with pytest.raises(IntegrityError, match="decision_time_or_symbol_mismatch"):
            with session.begin_nested():
                insert_row(session, "swing_ledger_events", previous)
