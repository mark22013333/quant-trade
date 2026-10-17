from __future__ import annotations

import json
import csv
from io import StringIO
from datetime import date

from app.pipeline.daily_radar import DailyRadarService
from app.pipeline.invest_pipeline import InvestPipelineService
from app.reports.daily_decision_summary import build_diversified_shortlist, export_daily_decision_summary_report
from app.reports.daily_radar_export import export_daily_radar_files
from app.security.reporting import csv_safe_value, sanitize_report_payload


class _Repo:
    pass


class _Sync:
    pass


def test_report_payload_redacts_sensitive_keys():
    payload = sanitize_report_payload(
        {
            "api_token": "secret-token-value",
            "nested": {"account_id": "1234567890"},
            "safe": "2330",
        }
    )

    assert payload["api_token"] != "secret-token-value"
    assert payload["nested"]["account_id"] != "1234567890"
    assert payload["safe"] == "2330"


def test_csv_safe_value_prevents_formula_injection():
    assert csv_safe_value("=HYPERLINK(\"https://example.com\")").startswith("'=")
    assert csv_safe_value("+SUM(1,2)").startswith("'+")


def test_diversified_shortlist_prefers_cross_industry_no_blockers():
    picks = build_diversified_shortlist(
        [
            {
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "action_label": "ENTRY_READY",
                "entry_score": 88.0,
                "industry_group": "半導體業",
                "blocker_tags": [],
            },
            {
                "rank": 2,
                "symbol": "2303",
                "name": "聯電",
                "action_label": "ENTRY_READY",
                "entry_score": 84.0,
                "industry_group": "半導體業",
                "blocker_tags": [],
            },
            {
                "rank": 3,
                "symbol": "2881",
                "name": "富邦金",
                "action_label": "WATCH_WAIT_TRIGGER",
                "entry_score": 76.0,
                "industry_group": "金融保險業",
                "blocker_tags": [],
            },
            {
                "rank": 4,
                "symbol": "2603",
                "name": "長榮",
                "action_label": "ENTRY_READY",
                "entry_score": 72.0,
                "industry_group": "航運業",
                "blocker_tags": ["B_DATA_STALE"],
            },
        ],
        limit=3,
    )

    assert [item["symbol"] for item in picks] == ["2330", "2881", "2303"]
    assert [item["shortlist_rank"] for item in picks] == [1, 2, 3]
    assert picks[0]["investment_action"] == "可進場"
    assert picks[1]["investment_action"] == "觀察中"


def test_invest_candidate_export_redacts_json_and_csv(tmp_path):
    service = InvestPipelineService(repo=_Repo(), sync_service=_Sync())
    service.reports_dir = tmp_path

    export = service._export_candidates(
        run_id="run_test",
        trade_date=date(2026, 6, 5),
        payload=[
            {
                "rank": 1,
                "symbol": "2330",
                "name": "=FORMULA",
                "score": 0.9,
                "api_token": "secret-token-value",
                "account_id": "1234567890",
                "reason_codes": ["momentum"],
                "risk_flags": ["none"],
            }
        ],
    )

    json_text = (tmp_path / export["files"]["json"]).read_text(encoding="utf-8")
    csv_text = (tmp_path / export["files"]["csv"]).read_text(encoding="utf-8")
    payload = json.loads(json_text)

    assert "secret-token-value" not in json_text
    assert payload["candidates"][0]["api_token"] != "secret-token-value"
    assert "1234567890" not in json_text
    assert "'=FORMULA" in csv_text


def test_daily_radar_export_redacts_json_and_csv(tmp_path):
    service = DailyRadarService(repo=_Repo(), sync_service=_Sync())
    service.reports_dir = tmp_path

    export = service._export(
        run_id="radar_test",
        trade_date=date(2026, 6, 5),
        payload=[
            {
                "rank": 1,
                "symbol": "2330",
                "name": "@FORMULA",
                "action_label": "ENTRY_READY",
                "entry_score": 0.8,
                "ca_path": "/Users/cheng/certs/my.pfx",
                "reason_tags": ["trend"],
                "blocker_tags": [],
            },
            {
                "rank": 2,
                "symbol": "2317",
                "name": "鴻海",
                "action_label": "NOT_RECOMMENDED",
                "entry_score": 0.4,
                "reason_tags": ["vol"],
                "blocker_tags": ["B_DATA_STALE"],
            },
        ],
    )

    json_text = (tmp_path / export["files"]["json"]).read_text(encoding="utf-8")
    csv_text = (tmp_path / export["files"]["csv"]).read_text(encoding="utf-8")

    assert "/Users/cheng/certs/my.pfx" not in json_text
    payload = json.loads(json_text)
    assert payload["investment_actions"]["entry"]["count"] == 1
    assert payload["investment_actions"]["entry"]["representatives"][0]["symbol"] == "2330"
    assert payload["investment_actions"]["avoid"]["count"] == 1
    assert payload["investment_actions"]["avoid"]["representatives"][0]["symbol"] == "2317"
    assert payload["diversified_shortlist"][0]["symbol"] == "2330"
    assert payload["diversified_shortlist"][0]["shortlist_rank"] == 1
    assert payload["action_queue"]["entry"]["items"][0]["symbol"] == "2330"
    assert payload["action_queue"]["risk"]["items"][0]["symbol"] == "2317"
    assert "diversified_shortlist_rank" in csv_text
    assert "investment_action" in csv_text
    assert "可進場" in csv_text
    assert "不要碰" in csv_text
    assert "action_queue_lanes" in csv_text
    assert "先看進場" in csv_text
    assert "先處理風險" in csv_text
    assert "'@FORMULA" in csv_text
    assert (tmp_path / export["files"]["html_summary"]).exists()
    html_text = (tmp_path / export["files"]["html_summary"]).read_text(encoding="utf-8")
    assert "分散首選" in html_text
    assert export["urls"]["html_summary"].endswith(".html")


def test_daily_radar_export_persists_allocation_evidence(tmp_path):
    export = export_daily_radar_files(
        [
            {
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "action_label": "ENTRY_READY",
                "price": 620.0,
                "entry_score": 88.5,
                "risk_adjusted_score": 91.2,
                "risk_score": 0.18,
                "atr_heat": "normal",
                "last_bar_date": "2026-06-25",
                "data_quality": "fresh",
                "industry_group": "半導體業",
                "reason_tags": ["TREND_OK"],
                "blocker_tags": [],
            }
        ],
        output_dir=tmp_path,
        report_date=date(2026, 6, 25),
        run_id="radar_alloc",
        allocation_plan={
            "status": "ok",
            "research_budget": 350000.0,
            "allocated_value": 76000.0,
            "allocation_count": 1,
            "calibration_profile": {"key": "standard", "label": "標準", "enabled": True},
            "allocation_diagnostics": {
                "risk_level": "watch",
                "verdict": "可小額研究，先補檢查",
                "alerts": [
                    {
                        "type": "low_utilization",
                        "label": "研究額度使用偏低",
                        "severity": "watch",
                        "detail": "目前只使用 22% 研究預算。",
                    }
                ],
                "review_queue": {
                    "lanes": {
                        "preview": {
                            "label": "可先建立 Preview",
                            "hint": "配置、動作與阻擋條件相對完整",
                            "count": 1,
                            "alert_plan": {
                                "rule": "清單內標的可優先建立 Preview，但仍需逐檔通過交易 gate。",
                                "checklist": ["先檢查即時價格", "再查部位與可賣股數"],
                                "checklist_text": "先檢查即時價格 | 再查部位與可賣股數",
                            },
                            "items": [
                                {
                                    "symbol": "2330",
                                    "name": "台積電",
                                    "reason": "可先檢查價格",
                                    "alert_rule": "即時價格、部位與 promotion gate 都通過後建立 Preview",
                                    "premarket_multiplier": 1.06,
                                    "premarket_reasons": [
                                        {"type": "adoption_feedback", "label": "採用回饋加權"},
                                        {"type": "no_blocker", "label": "無阻擋"},
                                    ],
                                    "checklist": ["確認即時價格與參考價偏離", "查詢目前持股"],
                                    "checklist_text": "確認即時價格與參考價偏離 | 查詢目前持股",
                                }
                            ],
                        },
                        "trigger": {
                            "label": "設觸發觀察",
                            "hint": "等待條件",
                            "count": 0,
                            "alert_plan": {
                                "rule": "清單內標的先放 watchlist，等價格或資料條件到位再升級。",
                                "checklist_text": "設定觸發條件",
                            },
                            "items": [],
                        },
                        "risk": {
                            "label": "先處理風險",
                            "hint": "先確認阻擋",
                            "count": 0,
                            "alert_plan": {
                                "rule": "清單內標的暫不建立 Preview，先把風險待辦清乾淨。",
                                "checklist_text": "先處理阻擋或跳過原因",
                            },
                            "items": [],
                        },
                    },
                    "summary": {"preview_count": 1, "trigger_count": 0, "risk_count": 0},
                    "handoff": {
                        "primary_lane": "preview",
                        "primary_label": "可先建立 Preview",
                        "primary_action": "先檢查可建立 Preview",
                        "next_session_note": "可先建立 Preview 1 檔：2330",
                        "top_symbols": ["2330"],
                        "total_review_count": 1,
                    },
                },
                "summary_metrics": {"budget_utilization": 0.22, "max_group_budget_share": 0.22},
            },
            "allocation_items": [
                {
                    "symbol": "2330",
                    "name": "台積電",
                    "industry_group": "半導體業",
                    "suggested_value": 76000.0,
                    "suggested_odd_lot_shares": 100,
                    "suggested_board_lots": 0,
                    "allocation_weight": 0.91,
                    "calibration_multiplier": 1.08,
                    "premarket_multiplier": 1.06,
                    "premarket_reasons": [
                        {"type": "adoption_feedback", "label": "採用回饋加權"},
                        {"type": "no_blocker", "label": "無阻擋"},
                    ],
                    "next_step": "可建立 paper/Preview，仍需通過 promotion 與 live gate",
                }
            ],
            "calibration": {"status": "ok", "top_positive": [{"label": "半導體業", "multiplier": 1.08}]},
        },
        allocation_outcomes={
            "status": "ok",
            "verdict": "配置優於排名基準",
            "average_alpha": 0.012,
            "allocation_positive_rate": 0.75,
            "evaluated_run_count": 4,
        },
        allocation_calibration={
            "status": "ok",
            "profile": {"key": "standard", "label": "標準", "enabled": True},
            "top_positive": [{"label": "半導體業", "multiplier": 1.08}],
            "top_negative": [{"label": "過熱波動", "multiplier": 0.94}],
        },
        trading_feedback={
            "status": "ok",
            "run_id": "radar_alloc",
            "run_date": "2026-06-25",
            "candidate_count": 1,
            "verdict": "交易回饋需留意",
            "risk_level": "warn",
            "primary_action": "先清理待決策 Preview，再建立新單。",
            "metrics": {"affected_symbol_count": 1, "pending_preview_count": 1, "execution_rejected_count": 0},
            "alerts": [
                {
                    "code": "pending_preview",
                    "label": "候選標的有待決策 Preview",
                    "severity": "warn",
                    "detail": "1 筆 Preview 尚未決策。",
                    "action": "先清理待決策 Preview。",
                }
            ],
            "symbols": [
                {
                    "symbol": "2330",
                    "name": "台積電",
                    "risk_level": "warn",
                    "pending_preview_count": 1,
                    "execution_rejected_count": 0,
                    "execution_error_count": 0,
                    "issues": [{"label": "Preview 待決策", "detail": "2330 仍有 Preview 停在 created。"}],
                }
            ],
        },
    )

    payload = json.loads((tmp_path / export["files"]["json"]).read_text(encoding="utf-8"))
    csv_text = (tmp_path / export["files"]["csv"]).read_text(encoding="utf-8")
    quality_research_csv = (tmp_path / export["files"]["quality_lane_research_now_csv"]).read_text(encoding="utf-8")
    quality_research_symbols_txt = (tmp_path / export["files"]["quality_lane_research_now_symbols_txt"]).read_text(encoding="utf-8")
    preview_lane_csv = (tmp_path / export["files"]["allocation_review_preview_csv"]).read_text(encoding="utf-8")
    preview_symbols_txt = (tmp_path / export["files"]["allocation_review_preview_symbols_txt"]).read_text(encoding="utf-8")
    trigger_lane_csv = (tmp_path / export["files"]["allocation_review_trigger_csv"]).read_text(encoding="utf-8")
    trigger_symbols_txt = (tmp_path / export["files"]["allocation_review_trigger_symbols_txt"]).read_text(encoding="utf-8")
    brief_text = (tmp_path / export["files"]["decision_brief_md"]).read_text(encoding="utf-8")
    html_text = (tmp_path / export["files"]["html_summary"]).read_text(encoding="utf-8")

    assert payload["allocation_plan"]["calibration_profile"]["key"] == "standard"
    assert payload["allocation_plan"]["allocation_items"][0]["symbol"] == "2330"
    assert payload["allocation_plan"]["allocation_diagnostics"]["risk_level"] == "watch"
    assert payload["allocation_plan"]["allocation_diagnostics"]["review_queue"]["lanes"]["preview"]["count"] == 1
    assert payload["quality_summary"]["status"] == "ok"
    assert payload["quality_summary"]["metrics"]["entry_count"] == 1
    assert payload["quality_summary"]["top_picks"][0]["symbol"] == "2330"
    assert payload["quality_summary"]["quality_lanes"][0]["key"] == "research_now"
    assert payload["threshold_sweep"]["status"] == "ok"
    assert payload["threshold_sweep"]["base_profile"] == "balanced"
    assert payload["threshold_sweep"]["stable_entry_symbols"] == ["2330"]
    assert payload["trading_feedback"]["risk_level"] == "warn"
    assert payload["trading_feedback"]["symbols"][0]["symbol"] == "2330"
    assert payload["decision_brief"]["handoff"]["primary_action"] == "先檢查可建立 Preview"
    assert payload["decision_brief"]["quality"]["verdict"] == payload["quality_summary"]["verdict"]
    assert payload["decision_brief"]["quality"]["top_symbols"] == ["2330"]
    assert payload["decision_brief"]["quality"]["lanes"][0]["key"] == "research_now"
    assert payload["decision_brief"]["quality"]["lanes"][0]["symbols"] == ["2330"]
    assert payload["decision_brief"]["threshold_sweep"]["stable_symbols"] == ["2330"]
    assert payload["decision_brief"]["trading_feedback"]["affected_symbols"] == ["2330"]
    assert payload["decision_brief"]["lanes"][0]["items"][0]["symbol"] == "2330"
    assert payload["decision_brief"]["lanes"][0]["alert_plan"]["checklist"][0] == "先檢查即時價格"
    assert export["data"]["decision_brief"]["handoff"]["primary_action"] == "先檢查可建立 Preview"
    assert export["data"]["decision_brief"]["quality"]["top_symbols"] == ["2330"]
    assert export["data"]["decision_brief"]["quality"]["lanes"][0]["symbols"] == ["2330"]
    assert export["data"]["decision_brief"]["trading_feedback"]["affected_symbols"] == ["2330"]
    assert export["data"]["decision_brief"]["lanes"][0]["items"][0]["symbol"] == "2330"
    assert payload["allocation_outcomes"]["verdict"] == "配置優於排名基準"
    assert payload["allocation_calibration"]["top_positive"][0]["label"] == "半導體業"
    assert "allocation_suggested_value" in csv_text
    assert "candidate_quality_score" in quality_research_csv
    assert "2330" in quality_research_csv
    assert quality_research_symbols_txt.strip() == "2330"
    assert "allocation_calibration_multiplier" in csv_text
    assert "allocation_review_lane" in csv_text
    assert "allocation_review_label" in csv_text
    assert "allocation_review_alert_rule" in csv_text
    assert "allocation_review_checklist" in csv_text
    assert "allocation_review_monitor_only" in csv_text
    assert "allocation_review_allocation_lane" in csv_text
    assert "allocation_review_status" in csv_text
    assert "allocation_review_trigger_plan" in csv_text
    assert "allocation_review_trigger_target_entry_score" in csv_text
    assert "allocation_review_trigger_breakout_price" in csv_text
    assert "allocation_review_trigger_readiness_score" in csv_text
    assert "allocation_review_trigger_pullback_price" in csv_text
    assert "allocation_review_trading_feedback_risk" in csv_text
    assert "allocation_review_trading_feedback" in csv_text
    assert "allocation_review_focus" in csv_text
    assert "allocation_review_primary_action" in csv_text
    assert "allocation_review_next_session_note" in csv_text
    assert "risk_adjusted_score" in csv_text
    assert "先檢查可建立 Preview" in csv_text
    assert "allocation_review_preview_count" in csv_text
    assert "可先建立 Preview 1 檔" in csv_text
    assert "preview" in csv_text
    assert "可先建立 Preview" in csv_text
    assert "76000.0" in csv_text
    assert "1.08" in csv_text
    assert export["files"]["decision_brief_md"].endswith("_brief.md")
    assert export["files"]["allocation_review_preview_csv"].endswith("_review_preview.csv")
    assert export["files"]["allocation_review_preview_symbols_txt"].endswith("_review_preview_symbols.txt")
    assert export["urls"]["allocation_review_risk_csv"].endswith("_review_risk.csv")
    assert export["urls"]["allocation_review_risk_symbols_txt"].endswith("_review_risk_symbols.txt")
    assert "review_rank,lane,lane_label,symbol" in preview_lane_csv
    assert "preview,可先建立 Preview,2330" in preview_lane_csv
    preview_header = preview_lane_csv.splitlines()[0].split(",")
    for column in (
        "radar_rank",
        "price",
        "entry_score",
        "risk_adjusted_score",
        "risk_score",
        "risk_quality_score",
        "risk_quality_label",
        "risk_quality_reason",
        "atr_heat",
        "last_bar_date",
        "data_quality",
        "reason_tags",
        "blocker_tags",
    ):
        assert column in preview_header
    preview_rows = list(csv.DictReader(StringIO(preview_lane_csv)))
    assert preview_rows[0]["radar_rank"] == "1"
    assert preview_rows[0]["price"] == "620.0"
    assert preview_rows[0]["entry_score"] == "88.5"
    assert preview_rows[0]["risk_adjusted_score"] == "91.2"
    assert preview_rows[0]["risk_score"] == "0.18"
    assert preview_rows[0]["risk_quality_score"]
    assert preview_rows[0]["risk_quality_label"]
    assert preview_rows[0]["atr_heat"] == "normal"
    assert preview_rows[0]["last_bar_date"] == "2026-06-25"
    assert preview_rows[0]["data_quality"] == "fresh"
    assert preview_rows[0]["reason_tags"] == "TREND_OK"
    assert "TREND_OK" in preview_lane_csv
    assert "alert_rule,checklist" in preview_lane_csv
    assert "trigger_watchlist_note" in preview_lane_csv
    assert "trading_feedback_risk,trading_feedback" in preview_lane_csv
    assert "monitor_only,allocation_lane,allocation_status" in preview_lane_csv
    assert "allocation_weight,premarket_multiplier,premarket_reasons" in preview_lane_csv
    assert "symbol_adoption_feedback_boost,symbol_adoption_feedback_confidence_weight" in preview_lane_csv
    assert "allocation_premarket_multiplier" in csv_text
    assert "allocation_premarket_reasons" in csv_text
    assert "即時價格、部位與 promotion gate 都通過後建立 Preview" in preview_lane_csv
    assert "確認即時價格與參考價偏離 | 查詢目前持股" in preview_lane_csv
    assert "allocation_review_focus" in preview_lane_csv
    assert "可先建立 Preview 1 檔" in preview_lane_csv
    assert preview_symbols_txt.strip() == "2330"
    trigger_lines = trigger_lane_csv.strip().splitlines()
    assert len(trigger_lines) == 1
    trigger_header = trigger_lines[0].split(",")
    for column in (
        "review_rank",
        "lane",
        "symbol",
        "trigger_plan",
        "radar_rank",
        "price",
        "entry_score",
        "risk_adjusted_score",
        "risk_score",
        "risk_quality_score",
        "risk_quality_label",
        "risk_quality_reason",
        "atr_heat",
        "allocation_review_focus",
    ):
        assert column in trigger_header
    assert trigger_symbols_txt == ""
    assert "## 候選品質" in brief_text
    assert "品質首選: 2330" in brief_text
    assert "### 品質行動線" in brief_text
    assert "可先研究: 1 檔；2330" in brief_text
    assert "## 交易回饋" in brief_text
    assert "交易警示: 候選標的有待決策 Preview - 1 筆 Preview 尚未決策。" in brief_text
    assert "## 門檻穩健性" in brief_text
    assert "穩定標的: 2330" in brief_text
    assert "THRESHOLD ROBUSTNESS" in html_text
    assert "門檻穩健性" in html_text
    assert "研究配置與驗證" in html_text
    assert "配置診斷" in html_text
    assert "盤前 x1.06" in html_text
    assert "採用回饋加權、無阻擋" in html_text
    assert "行動線摘要" in html_text
    assert "盤前交接" in html_text
    assert "可先建立 Preview 1 檔：2330" in html_text
    assert "行動線下載" in html_text
    assert "盤前 Brief MD" in html_text
    assert "提醒計畫" in html_text
    assert "先檢查即時價格" in html_text
    assert "可先建立 Preview CSV" in html_text
    assert "Preview 代號 TXT" in html_text
    assert "_review_preview.csv" in html_text
    assert "_review_preview_symbols.txt" in html_text
    assert "_review_trigger.csv" in html_text
    assert "_review_risk.csv" in html_text
    assert "review_lane_exports" in html_text
    assert "主線：可先建立 Preview 1 檔" in html_text
    assert "可先建立 Preview" in html_text
    assert "研究額度使用偏低" in html_text
    assert "配置校準：加權因素" in html_text
    assert "配置優於排名基準" in html_text
    assert "# 每日雷達盤前 Brief" in brief_text
    assert "- 主線: 先檢查可建立 Preview" in brief_text
    assert "### 可先建立 Preview (1 檔)" in brief_text
    assert "- 2330 台積電 [已配置]: 可先檢查價格；即時價格、部位與 promotion gate 都通過後建立 Preview" in brief_text


def test_daily_radar_export_includes_data_health_gap_scopes_in_brief(tmp_path):
    export = export_daily_radar_files(
        [
            {
                "rank": 1,
                "symbol": "2330",
                "name": "台積電",
                "action_label": "ENTRY_READY",
                "price": 620.0,
                "entry_score": 88.5,
                "risk_adjusted_score": 91.2,
                "risk_score": 0.18,
                "atr_heat": "normal",
                "last_bar_date": "2026-07-01",
                "data_quality": "stale",
                "industry_group": "半導體業",
                "reason_tags": ["TREND_OK"],
                "blocker_tags": [],
            }
        ],
        output_dir=tmp_path,
        report_date=date(2026, 7, 1),
        run_id="radar_data_gap",
        data_health_overview={
            "status": "danger",
            "score": 0,
            "sync_jobs": [
                {
                    "job_name": "sync_market_bundle",
                    "status": "partial",
                    "failure_summary": {
                        "reason_codes": ["upstream_forbidden"],
                        "scope_failures": [
                            {
                                "scope": "daily_bars",
                                "failed_count": 1,
                                "reason_codes": ["upstream_forbidden"],
                                "symbols": ["2330"],
                            },
                            {
                                "scope": "institutional_chip",
                                "failed_count": 1,
                                "reason_codes": ["upstream_forbidden"],
                                "symbols": ["2330"],
                            },
                        ],
                        "symbol_failures": [
                            {
                                "symbol": "2330",
                                "failed_count": 2,
                                "scopes": ["daily_bars", "institutional_chip"],
                                "reason_codes": ["upstream_forbidden"],
                            }
                        ],
                    },
                }
            ],
        },
    )

    payload = json.loads((tmp_path / export["files"]["json"]).read_text(encoding="utf-8"))
    brief_text = (tmp_path / export["files"]["decision_brief_md"]).read_text(encoding="utf-8")
    repair_symbols_txt = (tmp_path / export["files"]["quality_lane_repair_first_symbols_txt"]).read_text(encoding="utf-8")
    quality_lanes = {lane["key"]: lane for lane in payload["decision_brief"]["quality"]["lanes"]}

    assert payload["quality_summary"]["data_readiness"]["status"] == "danger"
    assert payload["quality_summary"]["verdict"] == "先修資料再選股"
    assert quality_lanes["repair_first"]["symbols"] == ["2330"]
    assert quality_lanes["repair_first"]["data_gap_scopes"] == ["daily_bars", "institutional_chip"]
    assert quality_lanes["repair_first"]["data_gap_scope_summary"] == ["daily_bars 1 檔", "institutional_chip 1 檔"]
    assert repair_symbols_txt.strip() == "2330"
    assert "修資料後再看: 1 檔；2330" in brief_text
    assert "缺口 daily_bars 1 檔、institutional_chip 1 檔" in brief_text


def test_daily_radar_export_builds_actionable_review_queue_without_allocation_plan(tmp_path):
    export = export_daily_radar_files(
        [
            {
                "rank": 1,
                "symbol": "2881",
                "name": "富邦金",
                "action_label": "WATCH_WAIT_TRIGGER",
                "price": 86.2,
                "entry_score": 71.4,
                "risk_adjusted_score": 78.6,
                "risk_score": 0.24,
                "atr_heat": "normal",
                "last_bar_date": "2026-07-01",
                "data_quality": "fresh",
                "industry_group": "金融保險業",
                "reason_tags": ["INDUSTRY_TREND"],
                "blocker_tags": [],
            },
            {
                "rank": 2,
                "symbol": "9904",
                "name": "寶成",
                "action_label": "NOT_RECOMMENDED",
                "price": 35.1,
                "entry_score": 42.0,
                "risk_adjusted_score": 39.5,
                "risk_score": 0.35,
                "atr_heat": "normal",
                "last_bar_date": "2026-07-01",
                "data_quality": "fresh",
                "industry_group": "其他",
                "reason_tags": ["WEAK_SIGNAL"],
                "blocker_tags": [],
            },
        ],
        output_dir=tmp_path,
        report_date=date(2026, 7, 1),
        run_id="radar_action_fallback",
    )

    payload = json.loads((tmp_path / export["files"]["json"]).read_text(encoding="utf-8"))
    csv_text = (tmp_path / export["files"]["csv"]).read_text(encoding="utf-8")
    brief_text = (tmp_path / export["files"]["decision_brief_md"]).read_text(encoding="utf-8")
    trigger_symbols_txt = (tmp_path / export["files"]["allocation_review_trigger_symbols_txt"]).read_text(encoding="utf-8")
    risk_symbols_txt = (tmp_path / export["files"]["allocation_review_risk_symbols_txt"]).read_text(encoding="utf-8")

    review_queue = payload["allocation_plan"]["allocation_diagnostics"]["review_queue"]
    lane_by_key = {lane["lane"]: lane for lane in payload["decision_brief"]["lanes"]}

    assert payload["allocation_plan"]["status"] == "action_queue_fallback"
    assert review_queue["source"] == "investment_actions_fallback"
    assert review_queue["lanes"]["trigger"]["count"] == 1
    assert review_queue["lanes"]["trigger"]["items"][0]["symbol"] == "2881"
    assert review_queue["lanes"]["trigger"]["items"][0]["trigger_plan"]["target_entry_score"] == 74.4
    assert review_queue["lanes"]["trigger"]["items"][0]["trigger_plan"]["breakout_price"] == 87.06
    assert review_queue["lanes"]["trigger"]["items"][0]["trigger_plan"]["pullback_price"] == 84.48
    assert review_queue["lanes"]["risk"]["count"] == 0
    assert payload["decision_brief"]["handoff"]["primary_action"] == "設觸發觀察 1 檔"
    assert payload["decision_brief"]["handoff"]["next_session_note"] == "設觸發觀察 1 檔：2881"
    assert lane_by_key["trigger"]["count"] == 1
    assert lane_by_key["trigger"]["items"][0]["symbol"] == "2881"
    assert lane_by_key["trigger"]["items"][0]["trigger_plan"]["target_entry_score"] == 74.4
    assert "Entry >= 74" in lane_by_key["trigger"]["items"][0]["trigger_plan_text"]
    assert "突破 87.06" in lane_by_key["trigger"]["items"][0]["trigger_plan_text"]
    assert lane_by_key["trigger"]["items"][0]["trigger_plan_short_text"] == "Entry>=74 / 突破87.06 / 回測84.48"
    assert trigger_symbols_txt.strip() == "2881"
    assert risk_symbols_txt == ""
    assert "allocation_review_trigger_count" in csv_text
    assert "allocation_review_trigger_plan" in csv_text
    assert "設觸發觀察 1 檔" in csv_text
    trigger_lane_csv = (tmp_path / export["files"]["allocation_review_trigger_csv"]).read_text(encoding="utf-8")
    assert "trigger,設觸發觀察,2881" in trigger_lane_csv
    assert "trigger_target_entry_score" in trigger_lane_csv
    assert "Entry Score 重新整理後站上 74 分" in trigger_lane_csv
    assert "87.06" in trigger_lane_csv
    assert "84.48" in trigger_lane_csv
    assert "- 主線: 設觸發觀察 1 檔" in brief_text
    assert "### 設觸發觀察 (1 檔)" in brief_text
    assert "- 2881 富邦金 [未配置]: 觀察中，等待觸發條件確認；價格、量能或觸發訊號到位後，再重新檢查是否可升級。" in brief_text
    assert "觸發: Entry>=74 / 突破87.06 / 回測84.48" in brief_text
    assert "觸發條件: Entry >= 74 / 突破 87.06 / 回測 84.48" not in brief_text
    assert "盯盤: 2881 富邦金 | 未配置 | Entry>=74 | 突破 87.06 | 回測 84.48" not in brief_text


def test_daily_radar_export_excludes_prior_run_symbols_from_current_handoff(tmp_path):
    export = export_daily_radar_files(
        [
            {
                "rank": 1,
                "symbol": "1216",
                "name": "統一",
                "action_label": "WATCH_WAIT_TRIGGER",
                "price": 70.0,
                "entry_score": 70.2,
                "risk_adjusted_score": 56.2,
                "risk_score": 0.87,
                "atr_heat": "normal",
                "data_quality": "fresh",
                "blocker_tags": [],
            }
        ],
        output_dir=tmp_path,
        report_date=date(2026, 7, 22),
        run_id="radar_current_scope",
        allocation_plan={
            "status": "ok",
            "allocation_diagnostics": {
                "review_queue": {
                    "lanes": {
                        "preview": {"label": "可先建立 Preview", "count": 0, "items": []},
                        "trigger": {
                            "label": "設觸發觀察",
                            "count": 3,
                            "items": [
                                {"symbol": symbol, "lane": "trigger", "name": "前次候選"}
                                for symbol in ("3481", "2379", "3711")
                            ],
                        },
                        "risk": {"label": "先處理風險", "count": 0, "items": []},
                    },
                    "summary": {"preview_count": 0, "trigger_count": 3, "risk_count": 0},
                    "handoff": {
                        "primary_lane": "trigger",
                        "next_session_note": "設觸發觀察 3 檔：3481、2379、3711",
                    },
                }
            },
        },
    )

    payload = json.loads((tmp_path / export["files"]["json"]).read_text(encoding="utf-8"))
    brief_text = (tmp_path / export["files"]["decision_brief_md"]).read_text(encoding="utf-8")
    review_queue = payload["allocation_plan"]["allocation_diagnostics"]["review_queue"]

    assert review_queue["source"] == "investment_actions_fallback"
    assert review_queue["lanes"]["trigger"]["count"] == 1
    assert review_queue["lanes"]["trigger"]["items"][0]["symbol"] == "1216"
    assert payload["decision_brief"]["handoff"]["next_session_note"] == "設觸發觀察 1 檔：1216"
    assert "1216" in brief_text
    assert "3481" not in brief_text
    assert "2379" not in brief_text
    assert "3711" not in brief_text


def test_daily_radar_export_excludes_stale_lane_when_current_action_changed(tmp_path):
    export = export_daily_radar_files(
        [
            {
                "rank": 1,
                "symbol": "1216",
                "name": "統一",
                "action_label": "NOT_RECOMMENDED",
                "price": 76.8,
                "entry_score": 52.0,
                "risk_adjusted_score": 25.5,
                "risk_score": 0.5,
                "atr_heat": "normal",
                "data_quality": "fresh",
                "blocker_tags": [],
            }
        ],
        output_dir=tmp_path,
        report_date=date(2026, 7, 24),
        run_id="radar_current_action_changed",
        allocation_plan={
            "status": "ok",
            "allocation_diagnostics": {
                "review_queue": {
                    "lanes": {
                        "preview": {"label": "可先建立 Preview", "count": 0, "items": []},
                        "trigger": {
                            "label": "設觸發觀察",
                            "count": 1,
                            "items": [{"symbol": "1216", "lane": "trigger", "name": "統一"}],
                        },
                        "risk": {"label": "先處理風險", "count": 0, "items": []},
                    },
                    "summary": {"preview_count": 0, "trigger_count": 1, "risk_count": 0},
                    "handoff": {
                        "primary_lane": "trigger",
                        "next_session_note": "設觸發觀察 1 檔：1216",
                    },
                }
            },
        },
    )

    payload = json.loads((tmp_path / export["files"]["json"]).read_text(encoding="utf-8"))
    review_queue = payload["allocation_plan"]["allocation_diagnostics"]["review_queue"]

    assert review_queue["lanes"]["trigger"]["count"] == 0
    assert review_queue["lanes"]["trigger"]["items"] == []
    assert payload["decision_brief"]["handoff"]["primary_lane"] == ""
    assert payload["decision_brief"]["handoff"]["next_session_note"] == "等待配置行動線"


def test_daily_radar_export_rebuilds_redacted_trigger_plan(tmp_path):
    export = export_daily_radar_files(
        [
            {
                "rank": 1,
                "symbol": "2881",
                "name": "富邦金",
                "action_label": "WATCH_WAIT_TRIGGER",
                "price": 86.2,
                "entry_score": 71.4,
                "risk_adjusted_score": 78.6,
                "risk_score": 0.24,
                "atr_heat": "normal",
                "last_bar_date": "2026-07-01",
                "data_quality": "fresh",
                "industry_group": "金融保險業",
                "reason_tags": ["INDUSTRY_TREND"],
                "blocker_tags": [],
            }
        ],
        output_dir=tmp_path,
        report_date=date(2026, 7, 1),
        run_id="radar_redacted_trigger",
        allocation_plan={
            "status": "ok",
            "allocation_diagnostics": {
                "review_queue": {
                    "lanes": {
                        "preview": {"label": "可先建立 Preview", "count": 0, "items": []},
                        "trigger": {
                            "label": "設觸發觀察",
                            "count": 1,
                            "items": [
                                {
                                    "lane": "trigger",
                                    "symbol": "2881",
                                    "name": "富邦金",
                                    "action_label": "WATCH_WAIT_TRIGGER",
                                    "price": 86.2,
                                    "entry_score": 71.4,
                                    "risk_score": 0.24,
                                    "risk_adjusted_score": 78.6,
                                    "reason": "等待觸發條件後再建立 Preview",
                                    "alert_rule": "被遮罩前的舊提醒",
                                    "trigger_plan": {
                                        "summary": "<redacted-depth-limit>",
                                        "target_entry_score": "<redacted-depth-limit>",
                                    },
                                }
                            ],
                        },
                        "risk": {"label": "先處理風險", "count": 0, "items": []},
                    },
                    "summary": {"preview_count": 0, "trigger_count": 1, "risk_count": 0},
                    "handoff": {"primary_lane": "trigger", "primary_action": "先設定觸發觀察"},
                }
            },
        },
    )

    payload = json.loads((tmp_path / export["files"]["json"]).read_text(encoding="utf-8"))
    brief_text = (tmp_path / export["files"]["decision_brief_md"]).read_text(encoding="utf-8")
    trigger_csv = (tmp_path / export["files"]["allocation_review_trigger_csv"]).read_text(encoding="utf-8")
    review_trigger_item = payload["allocation_plan"]["allocation_diagnostics"]["review_queue"]["lanes"]["trigger"]["items"][0]
    trigger_item = {lane["lane"]: lane for lane in payload["decision_brief"]["lanes"]}["trigger"]["items"][0]

    assert review_trigger_item["trigger_plan"]["target_entry_score"] == 74.4
    assert review_trigger_item["trigger_plan"]["breakout_price"] == 87.06
    assert trigger_item["trigger_plan"]["target_entry_score"] == 74.4
    assert trigger_item["trigger_plan"]["breakout_price"] == 87.06
    assert trigger_item["trigger_plan_text"].startswith("Entry >= 74 / 突破 87.06 / 回測 84.48")
    assert trigger_item["trigger_plan_short_text"] == "Entry>=74 / 突破87.06 / 回測84.48"
    assert "<redacted-depth-limit>" not in brief_text
    assert "觸發: Entry>=74 / 突破87.06 / 回測84.48" in brief_text
    assert "觸發條件: Entry >= 74 / 突破 87.06 / 回測 84.48" not in brief_text
    assert "盯盤: 2881 富邦金 | 已配置 | Entry>=74 | 突破 87.06 | 回測 84.48" not in brief_text
    assert "Entry Score 重新整理後站上 74 分" in trigger_csv


def test_daily_decision_summary_report_redacts_and_escapes_html(tmp_path):
    export = export_daily_decision_summary_report(
        [
            {
                "rank": 1,
                "symbol": "2330",
                "name": "<script>alert(1)</script>",
                "action_label": "ENTRY_READY",
                "entry_score": 88.5,
                "risk_score": 0.72,
                "industry_group": "半導體業",
                "industry_rotation_score": 0.91,
                "atr_pct": 0.035,
                "blocker_tags": ["B_EXCLUDED_SYMBOL"],
                "api_token": "secret-token-value",
            }
        ],
        output_dir=tmp_path,
        report_date=date(2026, 6, 25),
        run_id="radar_test",
        allocation_plan={
            "status": "ok",
            "research_budget": 350000.0,
            "allocated_value": 80000.0,
            "allocation_count": 1,
            "calibration_profile": {"key": "standard", "label": "標準"},
            "allocation_items": [
                {
                    "symbol": "2330",
                    "name": "台積電",
                    "industry_group": "半導體業",
                    "suggested_value": 80000.0,
                    "suggested_odd_lot_shares": 100,
                    "calibration_multiplier": 1.05,
                    "next_step": "先檢查正式 gate",
                }
            ],
        },
        allocation_outcomes={"status": "ok", "verdict": "配置接近排名基準", "average_alpha": 0.001},
        allocation_calibration={"status": "ok", "top_positive": [{"label": "半導體業", "multiplier": 1.05}]},
    )

    html_text = (tmp_path / export["files"]["html_summary"]).read_text(encoding="utf-8")

    assert "secret-token-value" not in html_text
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_text
    assert "每日決策摘要" in html_text
    assert "今日投資行動" in html_text
    assert "分散首選" in html_text
    assert "今日動作佇列" in html_text
    assert "研究配置與驗證" in html_text
    assert "配置接近排名基準" in html_text
    assert "配置校準：加權因素" in html_text
    assert "先看進場" in html_text
    assert "先處理風險" in html_text
    assert "排除清單" in html_text
