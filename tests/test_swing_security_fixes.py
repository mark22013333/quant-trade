"""安全掃描發現的離線回歸：不連網、不改位址或代理設定。"""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from app.pipeline.swing_research import SwingResearchService
import app.services.subscription_research as research


CUTOFF = "2026-07-27T15:00:00+08:00"


def evidence():
    return {
        "symbol": "2330", "name": "測試公司", "event_type": "營運正常",
        "sentiment": "neutral", "relevance": .9, "confidence": .9,
        "impact_horizon": "數週至數月", "risk_tags": [],
        "summary": "此為完全離線的研究測試摘要，僅用來確認原文主體、陳述內容與資料型別都會接受服務端核對，不代表實際投資建議。",
        "exclude_recommended": False, "decision_version": "offline-v1",
        "sources": [{"title": "離線公告", "url": "https://www.twse.com.tw/offline", "published_at": "2026-07-27T10:00:00+08:00"}],
    }


def original(body):
    return {"url": evidence()["sources"][0]["url"], "original_text": body,
            "excerpt": body, "published_at": "2026-07-27T10:00:00+08:00",
            "obtained_at": "2026-07-27T14:00:00+08:00", "verified_at": "2026-07-27T14:01:00+08:00"}


@pytest.mark.parametrize("body", [
    "公告公司：公司代碼 2317。公司公告：本公司本月無重大裁罰。相關市場參考清單：公司代碼 2330。",
    "公司代碼 2330。本公司否認「本次事件未出現新增重大風險」之不實說法，實際新增重大風險。",
    "公司代碼 2330。媒體引述『本公司本月無重大裁罰』，本公司尚未證實。",
    "公司代碼 2330。公司公告：本公司本月無重大裁罰。本公司遭主管機關重大裁罰新台幣一億元。",
    "公司代碼 2330。本公司本月無重大裁罰。本公司於今日遭主管機關重大裁罰新台幣一億元。",
    "公司代碼 2330。本公司本月無重大裁罰。本公司營運狀況異常，已發生新增重大財務風險。",
    "公司代碼 2330。以下公告主體為另一家公司。本公司本月無重大裁罰。",
])
def test_wrong_issuer_denial_quote_or_conflict_is_not_verified(body):
    # 低階驗證不需要資料庫，仍須拒絕有歧義的官方原文。
    service = object.__new__(SwingResearchService)
    result = service.verify_evidence(item=evidence(), fetched_sources=[original(body)], cutoff_at=CUTOFF)
    assert result["accepted"] is False
    assert result["errors"]


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None, [], {}])
def test_non_boolean_decision_is_rejected(bad):
    item = evidence(); item["exclude_recommended"] = bad
    service = object.__new__(SwingResearchService)
    result = service.verify_evidence(item=item, fetched_sources=[original("公司代碼 2330。公司公告：本公司本月無重大裁罰。")], cutoff_at=CUTOFF)
    assert result["accepted"] is False
    clean, errors = research.validate_research_item(item, candidate_symbols={"2330"}, cutoff_at=datetime.fromisoformat(CUTOFF).replace(tzinfo=None))
    assert "invalid_exclude_recommended" in errors
    assert clean["exclude_recommended"] is False


def test_supported_fact_with_matching_primary_issuer_is_accepted():
    service = object.__new__(SwingResearchService)
    result = service.verify_evidence(item=evidence(), fetched_sources=[original("公司代碼 2330。公司公告：本公司本月無重大裁罰。")], cutoff_at=CUTOFF)
    assert result["accepted"] is True


def test_research_runner_has_isolated_directory_and_filtered_environment(monkeypatch):
    monkeypatch.setenv("SHIOAJI_API_SECRET", "offline-secret-must-not-inherit")
    seen = {}
    def runner(command, **kwargs):
        seen.update(kwargs)
        assert "--skip-git-repo-check" in command
        payload = evidence(); payload.pop("decision_version")
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps({"items": [payload]}))
        return subprocess.CompletedProcess(command, 0, "", "")
    researcher = research.CodexSubscriptionResearcher(runner=runner)
    monkeypatch.setattr(researcher, "status", lambda **kwargs: research.CodexSubscriptionStatus("normal", True, True, "offline", "offline-codex"))
    assert researcher.analyze_batch([{"symbol": "2330"}], datetime.fromisoformat(CUTOFF))["status"] == "normal"
    assert Path(seen["cwd"]) != research.PROJECT_ROOT
    assert "env" in seen
    assert "SHIOAJI_API_SECRET" not in seen["env"]
    assert not Path(seen["cwd"]).exists(), "研究完成應清理獨立暫存目錄"


def test_real_local_child_is_terminated_at_deadline(tmp_path):
    helper = getattr(research, "run_managed_research_process", None)
    assert callable(helper), "研究程序需要可終止的管理入口"
    processes = []
    real_popen = subprocess.Popen
    def popen(*args, **kwargs):
        p = real_popen(*args, **kwargs); processes.append(p); return p
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        helper([sys.executable, "-c", "import time; time.sleep(30)"],
               cwd=tmp_path, input="", timeout=.15, env=research.research_environment(), popen=popen)
    assert time.monotonic() - start < 3
    assert processes and processes[0].poll() is not None


def test_single_flight_and_global_limit_are_released_after_error():
    slot = getattr(research, "research_execution_slot", None)
    assert callable(slot)
    with slot(("offline-db", 1)):
        with pytest.raises(ValueError, match="in_progress"):
            with slot(("offline-db", 1)): pytest.fail("同實驗不得重複研究")
        with slot(("offline-db", 2)):
            with pytest.raises(ValueError, match="busy"):
                with slot(("offline-db", 3)): pytest.fail("超過全域研究額度")
    with pytest.raises(RuntimeError):
        with slot(("offline-db", 1)): raise RuntimeError("offline failure")
    with slot(("offline-db", 1)): pass


def test_model_output_schema_is_checked_after_runner(monkeypatch):
    def runner(command, **kwargs):
        payload = evidence(); payload.pop("decision_version"); payload["exclude_recommended"] = "false"
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps({"items": [payload]}))
        return subprocess.CompletedProcess(command, 0, "", "")
    researcher = research.CodexSubscriptionResearcher(runner=runner)
    monkeypatch.setattr(researcher, "status", lambda **kwargs: research.CodexSubscriptionStatus("normal", True, True, "offline", "offline-codex"))
    result = researcher.analyze_batch([{"symbol": "2330"}], datetime.fromisoformat(CUTOFF))
    assert result["status"] != "normal"
    assert result["items"] == []


def test_status_check_uses_same_process_manager(monkeypatch):
    seen = {}
    monkeypatch.setattr(research.shutil, "which", lambda name: "offline-codex")
    def managed(command, **kwargs):
        seen.update(kwargs)
        assert command == ["offline-codex", "login", "status"]
        return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")
    monkeypatch.setattr(research, "run_managed_research_process", managed)
    monkeypatch.setenv("SHIOAJI_API_SECRET", "offline-secret-must-not-inherit")
    assert research.inspect_codex_subscription(timeout_seconds=.2).authenticated
    assert seen["capture_stdout"] is True and seen["timeout"] == .2
    assert "SHIOAJI_API_SECRET" not in seen["env"]


def test_timed_out_generic_io_keeps_bounded_capacity_until_finished():
    release = threading.Event()
    ended = [threading.Event() for _ in range(4)]
    def blocked(index):
        try: release.wait(2)
        finally: ended[index].set()
    try:
        for index in range(4):
            with pytest.raises(TimeoutError):
                research.bounded_external_call(lambda i=index: blocked(i), .005)
        with pytest.raises(TimeoutError, match="busy"):
            research.bounded_external_call(lambda: pytest.fail("不得再啟動工作"), .01)
    finally:
        release.set()
        for event in ended: assert event.wait(1)
    assert research.bounded_external_call(lambda: "recovered", .5) == "recovered"


def test_excessive_process_output_is_stopped_and_capacity_recovers(tmp_path):
    helper = research.run_managed_research_process
    with pytest.raises(ValueError, match="output_too_large"):
        helper([sys.executable, "-c", "import sys,time; sys.stderr.write('x'*1100000); sys.stderr.flush(); time.sleep(30)"],
               cwd=tmp_path, input="", timeout=2, env=research.research_environment())
    result = helper([sys.executable, "-c", "print('offline-ok')"], cwd=tmp_path,
                    input="", timeout=2, env=research.research_environment(), capture_stdout=True)
    assert result.returncode == 0 and result.stdout.strip() == "offline-ok"
