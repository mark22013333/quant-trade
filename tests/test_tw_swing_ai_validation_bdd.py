"""已核准的收盤研究行為案例；全部外部來源均由固定離線資料提供。

新服務延遲到公開建立實驗入口成功後才匯入，讓 RED 呈現產品缺少功能，
而非模組載入失敗。金額單位為新台幣萬分之一元（e4）。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from importlib import import_module
from pathlib import Path
import socket
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.finmind_client import FinMindClient
from app.db.models import Base, ShortlistItem, ShortlistRun
from app.db.repository import TradingRepository
from app.pipeline.shortlist import ShortlistService
import app.db.session as db_session
from web.control_panel_app import app

SLUG = "tw-swing-ai-validation"
CUTOFF = "2026-07-27T15:00:00+08:00"


def _config(key="bdd"):
    return {
        "experiment_key": key,
        "mode": "historical_exploratory",
        "etf_symbol": "0050",
        "stock_pool": ["2330", "2317"],
        "start_date": "2026-07-27",
        "end_date": "2026-07-31",
        "initial_cash_e4": 100_000_000,
        "stop_equity_e4": 80_000_000,
        "strategy": {"version": "swing-v1", "hold_days": 20, "parameters": {}, "stop_loss_pct": "0.10"},
        "risk": {"max_position_e4": 90_000_000, "max_total_exposure_e4": 90_000_000, "max_single_risk_e4": 10_000_000},
        "fees": {
            "stock": {"commission_rate": "0", "minimum_fee_e4": 0, "sell_tax_rate": "0"},
            "etf": {"commission_rate": "0", "minimum_fee_e4": 0, "sell_tax_rate": "0"},
        },
        "execution": {
            "slippage_bps": "0", "settlement_sessions": 2,
            "price_basis": "raw", "corporate_actions": "cash_and_split",
            "odd_lot_model": "daily_proxy", "rounding": "ROUND_HALF_UP",
        },
        "data_policy": {"warmup_bars": 1, "missing_required": "block"},
        "observation_sessions": 20,
        "code_version": "bdd-fixture-v1",
    }


def _blocked_external(*args, **kwargs):
    raise AssertionError("BDD 必須使用離線資料，不得呼叫網路、AI CLI 或券商")


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(socket, "create_connection", _blocked_external)
    monkeypatch.setattr(socket.socket, "connect", _blocked_external)
    monkeypatch.setattr(subprocess, "run", _blocked_external)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_constraints(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=ON")

    Base.metadata.create_all(engine)
    ddl = (Path(__file__).resolve().parents[1] / ".spec" / SLUG / "deploy.sql").read_text(encoding="utf-8")
    with engine.connect() as connection:
        connection.connection.driver_connection.executescript(ddl)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_session, "_ENGINE", engine)
    monkeypatch.setattr(db_session, "_SESSION_FACTORY", factory)
    # Fixture 已用 canonical DDL 部署獨立記憶體 DB；API 不得自行偷偷部署。
    monkeypatch.setattr(db_session, "init_db", lambda: None)
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def _create(isolated, key="bdd"):
    client, factory = isolated
    response = client.post("/api/swing/experiments", json={
        "config": _config(key), "confirmed_by": "BDD 使用者", "confirmation_note": "確認這份離線研究設定",
    })
    assert response.status_code == 200, f"完整且人工確認的研究設定應可建立實驗：{response.text}"
    payload = response.json()
    assert payload["status"] == "ok"
    data = payload["data"]
    assert data["experiment_id"]
    assert len(data["config_hash"]) == 64
    assert {row["arm"] for row in data["accounts"]} == {"A", "B", "C"}
    return data, factory


def _service(session):
    return import_module("app.pipeline.swing_research").SwingResearchService(session)


def _ledger(session, experiment_id):
    return import_module("app.paper.portfolio").PortfolioLedger(session, experiment_id)


def _market(*, price="100", volume=2000, prior_volume=2000):
    return [
        {"date": day, "open": price, "close": price, "volume": volume, "prior_volume": prior_volume,
         "available_at": f"{day}T09:00:00+08:00", "session_open_at": f"{day}T09:00:00+08:00"}
        for day in ["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31", "2026-08-03", "2026-08-04"]
    ]


def _order(ledger, key, *, arm="B", symbol="2330", quantity=10, side="buy", market=None, signal_at=CUTOFF, stop_loss_price="90", decision_id=None):
    return ledger.submit_order(
        arm=arm, symbol=symbol, quantity=quantity, side=side, signal_at=signal_at,
        market=_market() if market is None else market, event_key=key, stop_loss_price=stop_loss_price,
        **({"decision_id": decision_id} if decision_id is not None else {}),
    )



def _approved_decision(session, experiment_id, symbol="2330", quantity=80):
    """資料 fixture：已封存的共同量化意圖及取得原文的 allow 決策。

    直接建立真實 schema 的輸入，不要求產品為了測試憑空核准 AI。
    只供首次截止的已知合格 C 部位，故障與晚回應不新增 allow。
    """
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def digest(value):
        return hashlib.sha256(canonical(value).encode()).hexdigest()

    timestamp = int(datetime.fromisoformat(CUTOFF).timestamp() * 1_000_000)
    article = {
        "schema_version": 1, "symbol": symbol,
        "sources": [{"url": f"https://example.com/official/{symbol}",
                     "original_text": "公司公告營運狀況正常，已核對之本次事件未出現新增重大風險。",
                     "published_at": "2026-07-27T10:00:00+08:00",
                     "obtained_at": "2026-07-27T14:00:00+08:00",
                     "verified_at": "2026-07-27T14:01:00+08:00"}],
        "decision_version": "event-filter-v1", "outcome": "allow",
    }
    source_id = session.execute(text("""
        INSERT INTO swing_source_versions
        (version_key,source,kind,source_key,symbol,data_period,public_at_us,obtained_at_us,
         verified_at_us,quality,payload_json,payload_hash,created_at_us)
        VALUES (:version_key,'offline-official','ai_evidence',:key,:symbol,'2026-07-27',
                :public,:obtained,:verified,'verified',:payload,:hash,:created)
    """), {"version_key": digest(article), "key": f"allow:{experiment_id}:{symbol}", "symbol": symbol,
            "public": timestamp - 5 * 3_600_000_000, "obtained": timestamp - 3_600_000_000,
            "verified": timestamp - 59 * 60_000_000, "payload": canonical(article),
            "hash": digest(article), "created": timestamp}).lastrowid
    batch_id = session.execute(text("SELECT COALESCE(MAX(id),0)+1 FROM swing_input_batches")).scalar_one()
    session.execute(text("""
        INSERT INTO swing_batch_sources(batch_id,experiment_id,source_version_id,purpose)
        VALUES (:batch,:experiment,:source,'strategy')
    """), {"batch": batch_id, "experiment": experiment_id, "source": source_id})
    quant = {"schema_version": 1, "symbol": symbol, "strategy_version": "swing-v1", "signal_at": CUTOFF,
             "side": "buy", "quantity": quantity, "stop_loss_price": "90", "rank": 1}
    manifest = {"schema_version": 1, "candidates": [quant], "sources": [{"id": source_id, "payload_hash": digest(article)}]}
    session.execute(text("""
        INSERT INTO swing_input_batches
        (id,experiment_id,run_key,cutoff_at_us,decision_at_us,coverage_status,coverage_json,
         source_count,manifest_json,input_hash,research_elapsed_ms,created_at_us)
        VALUES (:batch,:experiment,:run,:at,:at,'complete',:coverage,1,:manifest,:hash,1,:at)
    """), {"batch": batch_id, "experiment": experiment_id, "run": f"fixture:{symbol}", "at": timestamp,
            "coverage": canonical({"schema_version": 1, "status": "complete"}),
            "manifest": canonical(manifest), "hash": digest(manifest)})
    decision_id = session.execute(text("""
        INSERT INTO swing_candidate_decisions
        (experiment_id,batch_id,symbol,quant_rank,quant_json,ai_outcome,evidence_version_id,ai_json,payload_hash,created_at_us)
        VALUES (:experiment,:batch,:symbol,1,:quant,'allow',:source,:ai,:hash,:at)
    """), {"experiment": experiment_id, "batch": batch_id, "symbol": symbol, "quant": canonical(quant),
            "source": source_id, "ai": canonical(article), "hash": digest({"quant": quant, "ai": article}),
            "at": timestamp}).lastrowid
    session.commit()
    return decision_id

def _source(*, key="2330:revenue:2026-06", public_at="2026-07-10T10:00:00+08:00", revenue=100,
            obtained_at="2026-07-10T10:01:00+08:00", revised_at=None, quality="verified", supersedes_id=None):
    result = {
        "source": "offline-official", "kind": "monthly_revenue", "source_key": key,
        "symbol": "2330", "data_period": "2026-06", "public_at": public_at,
        "obtained_at": obtained_at, "revised_at": revised_at,
        "verified_at": obtained_at if quality == "verified" else None,
        "quality": quality, "payload": {"schema_version": 1, "revenue": revenue, "original_text": f"當月營收 {revenue} 元"},
    }
    if supersedes_id is not None:
        result["supersedes_id"] = supersedes_id
    return result


def _evidence():
    return {
        "symbol": "2330", "name": "台積電", "event_type": "重大裁罰", "sentiment": "negative",
        "relevance": 0.9, "confidence": 0.99, "impact_horizon": "1 至 3 個月",
        "summary": "公司公告遭主管機關重大裁罰，可能影響近期營運及財務表現，因此本次先排除新增部位，保留後續觀察與既有部位風險管理。",
        "risk_tags": ["重大裁罰"], "exclude_recommended": True,
        "decision_version": "event-filter-v1", "model": "offline-model", "prompt_version": "offline-prompt-v1",
        "sources": [{"title": "公司重大裁罰公告", "url": "https://example.com/official/2330", "published_at": "2026-07-27T10:00:00+08:00"}],
    }


def _fetched():
    return {
        "url": "https://example.com/official/2330", "title": "公司重大裁罰公告",
        "published_at": "2026-07-27T10:00:00+08:00", "obtained_at": "2026-07-27T14:00:00+08:00",
        "verified_at": "2026-07-27T14:01:00+08:00",
        "original_text": "公司公告：本公司遭主管機關重大裁罰新台幣一億元，預期影響本季營運及財務表現。",
        "excerpt": "本公司遭主管機關重大裁罰新台幣一億元",
    }


class _NoExternalResearcher:
    def analyze_batch(self, candidates, cutoff_at):
        return {"status": "timeout", "items": [], "error": "離線模擬逾時"}


def _shortlist(session, *, allowed=0, status="normal", suffix="zero"):
    run_id = f"bdd-shortlist-{suffix}"
    session.add(ShortlistRun(run_id=run_id, run_date=date(2026, 7, 27), source_radar_run_id=f"radar-{suffix}",
                             cutoff_at=datetime(2026, 7, 27, 15), status="ai_enriched", ai_status=status,
                             candidate_count=12, degraded=status != "normal"))
    for i in range(12):
        session.add(ShortlistItem(run_id=run_id, symbol=str(2300 + i), name=f"股票 {i}", quant_rank=i + 1,
                                  quant_score=90 - i, price=100, ai_status="normal" if status == "normal" else status,
                                  excluded_by_ai_risk=i >= allowed, blocker_tags_json="[]"))
    session.commit()
    service = ShortlistService(TradingRepository(session), researcher=_NoExternalResearcher())
    return service, run_id


@pytest.mark.parametrize("scenario", ["S01", "S02"], ids=[f"{SLUG}#AC-1#S01", f"{SLUG}#AC-1#S02"])
def test_confirmed_configuration(isolated, scenario):
    if scenario == "S01":
        data, factory = _create(isolated, "config-complete")
        assert len(data["accounts"]) == 3
        assert all(row["initial_cash_e4"] == 100_000_000 for row in data["accounts"])
        assert all(row["cash_available_e4"] == 100_000_000 for row in data["accounts"])
        with factory() as session:
            result = _service(session).create_experiment(_config("config-complete"), "BDD 使用者", "確認這份離線研究設定")
            assert result["experiment_id"] == data["experiment_id"], "相同設定重試不建立第二份本金"
            assert result["config"]["strategy"]["hold_days"] == 20
            assert result["config"]["initial_cash_e4"] != 1_000_000 * 10_000
    else:
        client, _factory = isolated
        for missing in ["fees", "etf_symbol", "stock_pool", "strategy", "risk", "execution"]:
            config = _config(f"missing-{missing}")
            config.pop(missing)
            response = client.post("/api/swing/experiments", json={"config": config, "confirmed_by": "BDD 使用者", "confirmation_note": "確認"})
            assert response.status_code == 422, f"缺 {missing} 不得建立合格實驗：{response.text}"
            assert missing in response.json()["detail"]["missing_fields"]
        response = client.post("/api/swing/experiments", json={"config": _config("unconfirmed"), "confirmed_by": "", "confirmation_note": ""})
        assert response.status_code == 422, "沒有人工確認不得啟動"
        assert "confirmed_by" in response.json()["detail"]["missing_fields"]


@pytest.mark.parametrize("scenario", ["S01", "S02"], ids=[f"{SLUG}#AC-2#S01", f"{SLUG}#AC-2#S02"])
def test_monthly_revenue_source_semantics(monkeypatch, scenario):
    client = FinMindClient(api_key="dummy")
    rows = [{"date": "2019-04-01", "revenue_year": 2019, "revenue_month": 3, "revenue": 120}]
    if scenario == "S01":
        rows += [{"date": "2018-04-01", "revenue_year": 2018, "revenue_month": 3, "revenue": 100},
                 {"date": "2019-03-01", "revenue_year": 2019, "revenue_month": 2, "revenue": 80}]
    monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: rows)
    parsed = client.fetch_monthly_revenue("2330", "2018-01-01", "2019-04-30")
    assert any(row["period"] == "2019-03" for row in parsed), "營收所屬月應由 revenue_year/month 決定，不能使用來源月份標籤"
    march = next(row for row in parsed if row["period"] == "2019-03")
    if scenario == "S01":
        assert march["revenue_yoy_pct"] == pytest.approx(20)
        assert march["revenue_mom_pct"] == pytest.approx(50)
    else:
        assert march["revenue_yoy_pct"] is None, "缺去年同期不得把 2019 年誤當 2019%"
        assert march["revenue_mom_pct"] is None, "缺上月不得虛構零成長"
        assert march["announce_date"] is None, "來源月份標籤不等於正式公告時間"


@pytest.mark.parametrize("scenario", ["S01", "S02"], ids=[f"{SLUG}#AC-3#S01", f"{SLUG}#AC-3#S02"])
def test_point_in_time_versions(isolated, monkeypatch, scenario):
    _data, factory = _create(isolated, f"pit-{scenario}")
    with factory() as session:
        service = _service(session)
        original = service.append_source(_source())
        session.commit()
        eligible = service.eligible_sources(kind="monthly_revenue", symbol="2330", cutoff_at=CUTOFF)
        assert [row["id"] for row in eligible] == [original["id"]]
        assert eligible[0]["payload"]["revenue"] == 100
        if scenario == "S01":
            assert eligible[0]["public_at"]
            assert eligible[0]["obtained_at"]
            assert eligible[0]["verified_at"]
        else:
            service.append_source(_source(key="2330:unknown", public_at=None, quality="unknown_publication"))
            service.append_source(_source(revenue=999, revised_at="2026-07-28T09:00:00+08:00",
                                          obtained_at="2026-07-28T09:01:00+08:00", supersedes_id=original["id"]))
            session.commit()
            historical = service.eligible_sources(kind="monthly_revenue", symbol="2330", cutoff_at=CUTOFF)
            assert [row["id"] for row in historical] == [original["id"]], "截止後修訂不得覆蓋舊回放，公告未知不得合格"
            assert historical[0]["payload"]["revenue"] == 100
            client = FinMindClient(api_key="dummy")
            monkeypatch.setattr(client, "_request_dataset", lambda dataset, **kwargs: [{"date": "2026-03-31", "type": "EPS", "value": 5}])
            financials = client.fetch_financial_statement_summary("2330", "2026-01-01", "2026-07-31")
            assert financials and financials[0]["announce_date"] is None, "季末不得被冒充成實際公告"


@pytest.mark.parametrize("scenario", ["S01", "S02"], ids=[f"{SLUG}#AC-7#S01", f"{SLUG}#AC-7#S02"])
def test_next_session_execution(isolated, scenario):
    data, factory = _create(isolated, f"execution-{scenario}")
    with factory() as session:
        ledger = _ledger(session, data["experiment_id"])
        if scenario == "S01":
            result = _order(ledger, "next-open")
            assert result["status"] == "filled"
            assert result["trade_date"] == "2026-07-28"
            assert result["price_e4"] == 1_000_000
            assert result["settlement_date"] == "2026-07-30", "交割依交易日曆推進兩個交易日"
            assert result["execution_assumption"] == "daily_proxy", "日線零股近似必須揭露"
            state = ledger.snapshot("B")
            assert state["payable_e4"] == 10_000_000
            assert state["cash_available_e4"] == 90_000_000
        else:
            missing = _order(ledger, "missing-next", market=_market()[:1])
            assert missing["status"] == "rejected", "缺下一合格行情不得假成交"
            assert ledger.snapshot("B")["positions"] == {}
            assert ledger.snapshot("B")["cash_available_e4"] == 100_000_000
            before = _order(ledger, "no-prior-liquidity", market=_market(prior_volume=None))
            assert before["status"] == "rejected", "不能以撮合後的全日量替代撮合時可得流動性"
            low_full_day = _order(ledger, "ignore-later-zero-volume", market=_market(volume=0, prior_volume=2000))
            assert low_full_day["status"] == "filled", "開盤決策不得由事後全日量改寫"
            assert low_full_day["trade_date"] != "2026-07-27"
            events = ledger.snapshot("B")["events"]
            assert {"missing-next", "no-prior-liquidity"} <= {row["event_key"] for row in events if row["event_kind"] == "reject"}


@pytest.mark.parametrize("scenario", ["S01", "S02"], ids=[f"{SLUG}#AC-8#S01", f"{SLUG}#AC-8#S02"])
def test_portfolio_shared_cash_and_replay(isolated, scenario):
    data, factory = _create(isolated, f"cash-{scenario}")
    with factory() as session:
        ledger = _ledger(session, data["experiment_id"])
        first = _order(ledger, "first-stock", quantity=60)
        assert first["status"] == "filled"
        session.commit()
        if scenario == "S01":
            second = _order(ledger, "second-stock", symbol="2317", quantity=20)
            assert second["status"] == "filled"
            state = ledger.snapshot("B")
            assert state["cash_available_e4"] == 20_000_000
            assert state["payable_e4"] == 80_000_000
            assert state["reserved_cash_e4"] == 0
            assert state["receivable_e4"] == 0
            assert {symbol: row["quantity"] for symbol, row in state["positions"].items()} == {"2330": 60, "2317": 20}
            assert ledger.snapshot("A")["cash_available_e4"] == ledger.snapshot("C")["cash_available_e4"] == 100_000_000
            session.commit()
        else:
            second = _order(ledger, "insufficient-second", symbol="2317", quantity=60)
            assert second["status"] == "rejected", "第二檔不得重新配置獨立一萬元"
            assert ledger.snapshot("B")["cash_available_e4"] == 40_000_000
            assert "2317" not in ledger.snapshot("B")["positions"]
            session.commit()
        frozen = deepcopy(ledger.snapshot("B"))
    with factory() as restarted:
        ledger = _ledger(restarted, data["experiment_id"])
        retry = _order(ledger, "first-stock", quantity=60)
        assert retry == first, "中斷後相同事件重跑應回傳既有結果"
        assert ledger.snapshot("B") == frozen, "重播不得重複扣款、交割或增持"
        with pytest.raises(ValueError, match="idempotency_conflict"):
            _order(ledger, "first-stock", quantity=59)


@pytest.mark.parametrize("scenario", ["S01", "S02"], ids=[f"{SLUG}#AC-10#S01", f"{SLUG}#AC-10#S02"])
def test_event_original_evidence(isolated, scenario):
    _data, factory = _create(isolated, f"evidence-{scenario}")
    with factory() as session:
        service = _service(session)
        if scenario == "S01":
            result = service.verify_evidence(item=_evidence(), fetched_sources=[_fetched()], cutoff_at=CUTOFF)
            assert result["accepted"] is True
            assert result["errors"] == []
            assert result["sources"][0]["original_text"] == _fetched()["original_text"]
            for field in ["url", "published_at", "obtained_at", "verified_at"]:
                assert result["sources"][0][field] == _fetched()[field]
            assert result["decision_version"] == "event-filter-v1"
        else:
            only_url = service.verify_evidence(item=_evidence(), fetched_sources=[], cutoff_at=CUTOFF)
            assert only_url["accepted"] is False, "合法網址及 0.99 信心度不等於已查證"
            for mutation in [
                {"original_text": "公司公告本月無重大裁罰，營運正常。", "excerpt": "本月無重大裁罰"},
                {"published_at": None},
                {"published_at": "2026-07-28T10:00:00+08:00"},
                {"verified_at": None},
            ]:
                source = {**_fetched(), **mutation}
                rejected = service.verify_evidence(item=_evidence(), fetched_sources=[source], cutoff_at=CUTOFF)
                assert rejected["accepted"] is False, f"不支持、時間未知或晚於截止的證據不得接受：{mutation}"
                assert rejected["errors"]


@pytest.mark.parametrize("scenario", ["S01", "S02"], ids=[f"{SLUG}#AC-11#S01", f"{SLUG}#AC-11#S02"])
def test_risk_exclusions_and_ai_failure(isolated, scenario):
    _client, factory = isolated
    with factory() as session:
        service, run_id = _shortlist(session, allowed=3 if scenario == "S01" else 0, suffix=scenario)
        final = service.finalize(run_id)
        expected = {"2300", "2301", "2302"} if scenario == "S01" else set()
        assert {row["symbol"] for row in final["items"]} == expected, "風險排除不可為湊十檔補回，零檔也應回傳空清單"
        assert final["selected_count"] == len(expected)
        if scenario == "S02":
            timeout_service, timeout_run = _shortlist(session, allowed=12, status="timeout", suffix="timeout")
            timeout = timeout_service.finalize(timeout_run)
            assert timeout["items"] == [], "AI 逾時不得默認全部無風險"
            assert timeout["degraded"] is True
            assert timeout["ai_status"] == "timeout"
    if scenario == "S02":
        data, factory = _create(isolated, "ai-control")
        with factory() as session:
            ledger = _ledger(session, data["experiment_id"])
            decision_id = _approved_decision(session, data["experiment_id"], quantity=10)
            assert _order(ledger, "c-prior-position", arm="C", quantity=10, decision_id=decision_id)["status"] == "filled"
            ledger.set_ai_status(status="timeout", at="2026-07-28T15:00:00+08:00", event_key="ai-timeout")
            paused = _order(ledger, "missed-c-decision", arm="C", symbol="2317", signal_at="2026-07-28T15:00:00+08:00")
            assert paused["status"] == "rejected"
            assert ledger.snapshot("C")["ai_blocked"] is True
            assert _order(ledger, "b-continues", signal_at="2026-07-28T15:00:00+08:00")["status"] == "filled"
            exit_result = _order(ledger, "c-risk-exit", arm="C", quantity=10, side="sell", signal_at="2026-07-28T15:00:00+08:00")
            assert exit_result["status"] == "filled", "AI 故障仍應管理既有退出"
            ledger.set_ai_status(status="normal", at="2026-07-29T15:00:00+08:00", event_key="ai-recovered")
            assert _order(ledger, "missed-c-decision", arm="C", symbol="2317", signal_at="2026-07-28T15:00:00+08:00") == paused, "恢復不得回填錯過的決策"


@pytest.mark.parametrize("scenario", ["S01", "S02"], ids=[f"{SLUG}#AC-13#S01", f"{SLUG}#AC-13#S02"])
def test_loss_boundary_and_human_resume(isolated, scenario):
    data, factory = _create(isolated, f"risk-{scenario}")
    with factory() as session:
        ledger = _ledger(session, data["experiment_id"])
        for arm in ["A", "B", "C"]:
            symbol = "0050" if arm == "A" else "2330"
            decision_id = _approved_decision(session, data["experiment_id"], symbol) if arm == "C" else None
            assert _order(ledger, f"position-{arm}", arm=arm, symbol=symbol, quantity=80, decision_id=decision_id)["status"] == "filled"
        if scenario == "S01":
            state = ledger.value(arm="B", prices={"2330": "75.0125"}, at="2026-07-28T13:30:00+08:00", event_key="8001")
            assert state["equity_e4"] == 80_010_000
            assert state["risk_paused"] is False
            too_risky = _order(ledger, "risk-per-trade", quantity=20, symbol="2317", stop_loss_price="0", signal_at="2026-07-28T15:00:00+08:00")
            assert too_risky["status"] == "rejected", "8,001 元仍必須通過單筆風險限制"
            assert "risk" in too_risky["reason"]
            assert "2317" not in ledger.snapshot("B")["positions"]
        else:
            for arm in ["A", "B", "C"]:
                symbol = "0050" if arm == "A" else "2330"
                state = ledger.value(arm=arm, prices={symbol: "75"}, at="2026-07-28T13:30:00+08:00", event_key=f"8000-{arm}")
                assert state["equity_e4"] == 80_000_000
                assert state["positions"][symbol]["quantity"] == 80, "觸線不得自動清倉"
                assert bool(state["risk_paused"]) == (arm in {"B", "C"})
            for arm in ["B", "C"]:
                assert _order(ledger, f"paused-{arm}", arm=arm, quantity=1, symbol="2317", signal_at="2026-07-28T15:00:00+08:00")["status"] == "rejected"
                recovered = ledger.value(arm=arm, prices={"2330": "100"}, at="2026-07-29T13:30:00+08:00", event_key=f"rebound-{arm}")
                assert recovered["risk_paused"] is True, "淨值回升不得自動恢復"
                assert ledger.snapshot(arm)["positions"]["2330"]["quantity"] == 80
            with pytest.raises(ValueError, match="human"):
                ledger.resume(arm="B", actor="", note="", at="2026-07-29T14:00:00+08:00", event_key="invalid-resume")
            ledger.resume(arm="B", actor="BDD 使用者", note="重新檢視組合後人工恢復", at="2026-07-29T14:00:00+08:00", event_key="human-resume")
            assert ledger.snapshot("B")["risk_paused"] is False
            assert ledger.snapshot("C")["risk_paused"] is True
            assert any(row["event_kind"] == "human_resume" and row["actor"] == "BDD 使用者" for row in ledger.snapshot("B")["events"])
            assert any(row["event_kind"] == "risk_breach" for row in ledger.snapshot("A")["events"])
            assert _order(ledger, "paused-risk-exit", arm="C", quantity=1, side="sell", signal_at="2026-07-29T15:00:00+08:00")["status"] == "filled", "觸線後既有退出仍須執行"
