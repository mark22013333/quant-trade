from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

import web.control_panel_app as control_panel_app
from web.control_panel_app import Tw0050SyncRequest, app


REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


@pytest.fixture()
def report_file():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "security_test_report.txt"
    path.write_text("report-ok", encoding="utf-8")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_control_panel_token_protects_api(monkeypatch):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.delenv("CONTROL_PANEL_TRUST_PROXY_AUTH", raising=False)
    with TestClient(app) as client:
        blocked = client.get("/api/ping")
        allowed = client.get("/api/ping", headers={"Authorization": "Bearer secret-token"})

    assert blocked.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "ok"


def test_control_panel_accepts_trusted_proxy_auth(monkeypatch):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("CONTROL_PANEL_TRUST_PROXY_AUTH", "1")
    with TestClient(app) as client:
        response = client.get("/api/ping", headers={"X-Authenticated-User": "quanttrade"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_control_panel_home_exposes_browser_token_storage(monkeypatch):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "control-panel-token" in response.text
    assert "quant_trade_control_panel_token" in response.text
    assert "X-Control-Panel-Token" in response.text


def test_control_panel_serves_linked_css(monkeypatch):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        response = client.get("/control_panel.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert ".screener-gate-panel" in response.text


def test_control_panel_home_exposes_daily_radar_screener(monkeypatch):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'id="radar-filter-search"' in response.text
    assert 'id="radar-filter-action"' in response.text
    assert 'id="radar-filter-group"' in response.text
    assert 'id="radar-filter-heat"' in response.text
    assert 'id="radar-filter-blocker"' in response.text
    assert 'id="radar-filter-reliability"' in response.text
    assert 'id="radar-filter-adoption-feedback"' in response.text
    assert 'id="radar-filter-sort"' in response.text
    assert 'option value="reliability_desc"' in response.text
    assert 'option value="reliability_samples_desc"' in response.text
    assert 'option value="adoption_feedback_desc"' in response.text
    assert 'option value="adoption_feedback_samples_desc"' in response.text
    assert 'option value="positive_confident"' in response.text
    assert 'option value="trigger_readiness_desc"' in response.text
    assert 'data-radar-filter-preset="quality_entry"' in response.text
    assert 'data-radar-filter-preset="steady_entry"' in response.text
    assert 'data-radar-filter-preset="quality_watch"' in response.text
    assert 'data-radar-filter-preset="risk_cooling_watch"' in response.text
    assert 'data-radar-filter-preset="reliability_risk"' in response.text
    assert 'data-radar-filter-preset="adoption_feedback_winners"' in response.text
    assert 'data-radar-preset-count="quality_entry"' in response.text
    assert 'data-radar-preset-count="risk_cooling_watch"' in response.text
    assert 'data-radar-preset-count="reliability_risk"' in response.text
    assert 'data-radar-preset-count="adoption_feedback_winners"' in response.text
    assert 'id="save-radar-filter-preset"' in response.text
    assert 'id="apply-radar-filter-preset"' in response.text
    assert 'id="copy-radar-filtered-symbols"' in response.text
    assert 'id="apply-radar-filtered-universe"' in response.text
    assert 'id="copy-radar-filtered-csv"' in response.text
    assert 'id="download-radar-filtered-csv"' in response.text
    assert 'id="radar-filter-preset-status"' in response.text
    assert 'id="radar-filter-insights"' in response.text
    assert 'id="radar-premarket-shortlist"' in response.text
    assert 'id="radar-diversified-shortlist"' in response.text
    assert "applyDailyRadarFilters" in response.text
    assert "radarReliabilityMatches" in response.text
    assert "radarAdoptionFeedbackMatches" in response.text
    assert "applyRadarFilterQuickPreset" in response.text
    assert "data-apply-radar-quick-preset" in response.text
    assert "updateRadarQuickPresetCounts" in response.text
    assert "filterDailyRadarRows" in response.text
    assert "selectRadarPremarketRows" in response.text
    assert "renderRadarPremarketShortlist" in response.text
    assert "copyRadarPremarketSymbols" in response.text
    assert "applyRadarPremarketUniverse" in response.text
    assert "盤前首選摘要" in response.text
    assert "renderRadarTriggerPlan" in response.text
    assert "radarTriggerReadinessScore" in response.text
    assert "radarTriggerQueueSummary" in response.text
    assert "radar-trigger-strip" in response.text
    assert "action-queue-trigger" in response.text
    assert "buildRadarFilteredCsv" in response.text
    assert "copyRadarFilteredCsv" in response.text
    assert "downloadRadarFilteredCsv" in response.text
    assert 'id="copy-radar-priority-symbols"' in response.text
    assert 'id="download-radar-priority-csv"' in response.text
    assert 'id="apply-radar-priority-universe"' in response.text
    assert "buildRadarPriorityCsv" in response.text
    assert "copyRadarPrioritySymbols" in response.text
    assert "downloadRadarPriorityCsv" in response.text
    assert "applyRadarPriorityUniverse" in response.text
    assert "radar-priority-next" in response.text
    assert "radar-priority-components" in response.text
    assert "radar-allocation-trigger-plan" in response.text
    assert "trigger_target_entry_score" in response.text
    assert "trigger_breakout_price" in response.text
    assert "trigger_readiness_score" in response.text
    assert "radarPriorityComponentText" in response.text
    assert "priority_next_action" in response.text
    assert "priority_score_components" in response.text
    assert "next_action_counts" in response.text
    assert "diversified_priority_rank" in response.text
    assert "radar-priority-diversified" in response.text
    assert "跨族群研究首選" in response.text
    assert "top_industry_group_share" in response.text
    assert "priority_concentration_level" in response.text
    assert "可建 Preview" in response.text
    assert "觸發觀察" in response.text
    assert "待處理" in response.text
    assert "priority_score_raw" in response.text
    assert "component_trading_feedback" in response.text
    assert "premarket_multiplier" in response.text
    assert "premarket_reasons" in response.text
    assert "allocation_weight" in response.text
    assert "symbol_adoption_feedback_confidence_level" in response.text
    assert "trading_feedback_latest_at" in response.text
    assert "exportLinkLabel" in response.text
    assert "Preview 清單 CSV" in response.text
    assert "觸發觀察 CSV" in response.text
    assert "風險處理 CSV" in response.text
    assert "Preview 代號 TXT" in response.text
    assert "觸發代號 TXT" in response.text
    assert "風險代號 TXT" in response.text
    assert "trading_feedback_risk" in response.text
    assert "trading_feedback_issue_count" in response.text
    assert "trading_feedback_pending_preview_count" in response.text
    assert "trading_feedback_execution_rejected_count" in response.text
    assert "trading_feedback_execution_error_count" in response.text
    assert "盤前 Brief MD" in response.text
    assert 'id="radar-brief-notify-channel"' in response.text
    assert 'id="radar-brief-notification-channel-status"' in response.text
    assert 'id="preview-radar-brief-notification"' in response.text
    assert 'id="copy-radar-brief-notification"' in response.text
    assert 'id="send-radar-brief-notification"' in response.text
    assert 'id="preview-radar-alert-plan"' in response.text
    assert 'id="send-radar-alert-plan"' in response.text
    assert "requestRadarBriefNotification" in response.text
    assert "copyRadarBriefNotificationText" in response.text
    assert "requestRadarAlertPlan" in response.text
    assert "refreshNotificationChannelStatus" in response.text
    assert "提醒計畫" in response.text
    assert 'id="dashboard-radar-threshold-status"' in response.text
    assert 'id="dashboard-radar-threshold-summary"' in response.text
    assert 'id="dashboard-radar-threshold-symbols"' in response.text
    assert 'id="refresh-radar-threshold"' in response.text
    assert 'id="dashboard-radar-threshold-backtest-status"' in response.text
    assert 'id="dashboard-radar-threshold-backtest-summary"' in response.text
    assert 'id="dashboard-radar-threshold-backtest-configs"' in response.text
    assert 'id="dashboard-radar-threshold-optimization-plan"' in response.text
    assert 'id="dashboard-radar-threshold-optimization-result"' in response.text
    assert 'id="dashboard-radar-threshold-adoptions"' in response.text
    assert 'id="dashboard-radar-threshold-backtest-segments"' in response.text
    assert 'id="refresh-radar-threshold-backtest"' in response.text
    assert 'id="dry-run-radar-threshold-optimization"' in response.text
    assert 'id="dry-run-grid-radar-threshold-optimization"' in response.text
    assert 'id="dry-run-exploration-radar-threshold-optimization"' in response.text
    assert 'id="execute-radar-threshold-optimization"' in response.text
    assert 'id="execute-grid-radar-threshold-optimization"' in response.text
    assert 'id="execute-exploration-radar-threshold-optimization"' in response.text
    assert "maxRunsOverride" in response.text
    assert "runRadarThresholdOptimizationBatch(false, 'grid', 6)" in response.text
    assert "runRadarThresholdOptimizationBatch(false, 'exploration', 6)" in response.text
    assert "runRadarThresholdOptimizationBatch(true, 'exploration')" in response.text
    assert "candidate_source" in response.text
    assert "radarThresholdOptimizationGridRequest" in response.text
    assert "radarThresholdCandidateSourceLabel" in response.text
    assert "radar-threshold-run-source-strip" in response.text
    assert "renderRadarThresholdSourceComparison" in response.text
    assert "radar-threshold-source-comparison" in response.text
    assert "radar-threshold-source-best-list" in response.text
    assert "data-threshold-source-execute" in response.text
    assert "data-threshold-source-copy-command" in response.text
    assert "copyRadarThresholdSourceCommand" in response.text
    assert "data-threshold-run-copy-command" in response.text
    assert "copyRadarThresholdRunCommand" in response.text
    assert "radarThresholdRunCommand" in response.text
    assert "renderRadarThresholdExplorationQueue" in response.text
    assert "exploration_queue" in response.text
    assert "data-threshold-exploration-copy-command" in response.text
    assert "copyRadarThresholdExplorationCommand" in response.text
    assert "renderRadarThresholdWalkForwardPlan" in response.text
    assert "walk_forward_plan" in response.text
    assert "data-threshold-walk-forward-copy-command" in response.text
    assert "copyRadarThresholdWalkForwardCommand" in response.text
    assert "renderRadarThresholdWalkForwardWindows" in response.text
    assert "walk_forward_windows" in response.text
    assert "renderRadarThresholdWalkForwardHeatmap" in response.text
    assert "walk_forward_heatmap" in response.text
    assert "Rolling 熱區" in response.text
    assert "data-threshold-walk-forward-apply-best" in response.text
    assert "applyRadarThresholdWalkForwardBest" in response.text
    assert "control_panel_walk_forward_heatmap" in response.text
    assert "Rolling Walk-forward" in response.text
    assert "walk_forward_adjustment" in response.text
    assert "walk_forward_label" in response.text
    assert "pool_walk_forward_adjustment" in response.text
    assert "pool_walk_forward_label" in response.text
    assert "renderRadarThresholdPoolRecommendation" in response.text
    assert "pool_recommendation" in response.text
    assert "下一輪市場池建議" in response.text
    assert "row-actions" in response.text
    assert "primaryAction" in response.text
    assert "confidence_label" in response.text
    assert "radar-threshold-source-guards" in response.text
    assert "coverage" in response.text
    assert "覆蓋" in response.text
    assert "建議操作" in response.text
    assert "radar-threshold-run-action compact" in response.text
    assert "跨市場池 Grid" in response.text
    assert "renderRadarThresholdSweep" in response.text
    assert "refreshRadarThresholdSweep" in response.text
    assert "renderRadarThresholdOptimizationConfigs" in response.text
    assert "renderRadarThresholdOptimizationPlan" in response.text
    assert "renderRadarThresholdVariantHeatmap" in response.text
    assert "renderRadarThresholdRecommendedAction" in response.text
    assert "renderRadarThresholdAdoptionSummary" in response.text
    assert "refreshRadarThresholdAdoptions" in response.text
    assert "feedback_adjustment" in response.text
    assert "historical_score" in response.text
    assert "radarSymbolAdoptionBoost" in response.text
    assert "radarSymbolAdoptionRawBoost" in response.text
    assert "radarSymbolAdoptionSamples" in response.text
    assert "radarSymbolAdoptionConfidenceWeight" in response.text
    assert "radarSymbolAdoptionConfidenceLabel" in response.text
    assert "adoption_feedback_raw_boost" in response.text
    assert "adoption_feedback_boost" in response.text
    assert "adoption_feedback_samples" in response.text
    assert "adoption_feedback_confidence_weight" in response.text
    assert "adoption_feedback_confidence_level" in response.text
    assert "採用後表現佳" in response.text
    assert "門檻熱區" in response.text
    assert "採用建議" in response.text
    assert "採用追蹤" in response.text
    assert "採用回饋" in response.text
    assert "尚無採用回饋" in response.text
    assert "radar-threshold-heatmap" in response.text
    assert "radar-threshold-optimization-grid" in response.text
    assert "radar-threshold-adoption" in response.text
    assert "radar-threshold-adoptions" in response.text
    assert "radar-threshold-config-feedback" in response.text
    assert "renderRadarThresholdOptimizationRunResult" in response.text
    assert "runRadarThresholdOptimizationBatch" in response.text
    assert "代表代號" in response.text
    assert "優先檢查" in response.text
    assert "top_items" in response.text
    assert "複製代號" in response.text
    assert "套用觀察池" in response.text
    assert "data-threshold-run-copy-symbols" in response.text
    assert "data-threshold-run-apply-symbols" in response.text
    assert "copyRadarThresholdOptimizationSymbols" in response.text
    assert "applyRadarThresholdOptimizationUniverse" in response.text
    assert "applyRadarThresholdOptimizationConfig" in response.text
    assert "data-threshold-config-apply" in response.text
    assert "data-threshold-config-copy-symbols" in response.text
    assert "copyRadarThresholdConfigSymbols" in response.text
    assert "套用到雷達表單" in response.text
    assert "套用推薦設定" in response.text
    assert "執行第 1 組" in response.text
    assert "run_params" in response.text
    assert "renderRadarThresholdBacktest" in response.text
    assert "refreshRadarThresholdBacktest" in response.text
    assert "/api/radar/threshold-sweep" in response.text
    assert "/api/radar/threshold-backtest" in response.text
    assert "/api/radar/threshold-optimization-runs" in response.text
    assert "/api/radar/threshold-adoptions" in response.text
    assert "THRESHOLD ROBUSTNESS" in response.text
    assert "HISTORICAL RUNS" in response.text
    assert "門檻穩健性" in response.text
    assert "radarFilteredExportRows" in response.text
    assert "copyRadarFilteredSymbols" in response.text
    assert "applyRadarFilteredUniverse" in response.text
    assert "radarFilteredSymbolsText" in response.text
    assert "downloadCsvText" in response.text
    assert "renderRadarFilterInsights" in response.text
    assert "averageRadarMetric" in response.text
    assert "topRadarIndustryGroup" in response.text
    assert "radarFilterInsightVerdict" in response.text
    assert "族群集中" in response.text
    assert "阻擋偏多" in response.text
    assert "selectDiversifiedRadarRows" in response.text
    assert "renderRadarDiversifiedShortlist" in response.text
    assert "radarDiversifiedSymbolsText" in response.text
    assert "copyRadarDiversifiedSymbols" in response.text
    assert "applyRadarDiversifiedUniverse" in response.text
    assert "copy-radar-diversified-symbols" in response.text
    assert "apply-radar-diversified-universe" in response.text
    assert "RADAR_FILTER_PRESET_STORAGE_KEY" in response.text
    assert "saveRadarFilterPreset" in response.text
    assert "applyRadarFilterPreset" in response.text
    assert 'option value="dynamic_balanced"' in response.text
    assert 'option value="dynamic_liquid"' in response.text
    assert 'option value="tw_mid100"' in response.text
    assert 'option value="index:custom"' in response.text
    assert 'option value="conservative"' in response.text
    assert "保守型會提高 Entry / Watch 門檻" in response.text
    assert "風格：保守型" in response.text
    assert "data/cache/index_constituents/custom.csv" in response.text
    assert 'id="index-constituents-key"' in response.text
    assert 'id="index-constituents-source"' in response.text
    assert 'id="index-constituents-url"' in response.text
    assert 'id="index-constituents-content"' in response.text
    assert 'id="import-index-constituents"' in response.text
    assert 'id="refresh-index-constituents"' in response.text
    assert 'id="index-constituents-list"' in response.text
    assert "refreshIndexConstituents" in response.text
    assert "importIndexConstituentsFromForm" in response.text
    assert 'data-action="0050-sync"' in response.text
    assert 'data-result="0050-sync"' in response.text
    assert "同步台灣 50" in response.text
    assert "'0050-sync': '台灣 50 同步'" in response.text
    assert 'data-action="mid100-sync"' in response.text
    assert 'data-result="mid100-sync"' in response.text
    assert "同步中型 100" in response.text
    assert 'data-action="industry-index-sync"' in response.text
    assert 'id="sync-industry-indices"' in response.text
    assert 'id="sync-attention"' in response.text
    assert 'id="pipeline-include-attention"' in response.text
    assert 'id="pipeline-include-extended-chip"' in response.text
    assert 'id="radar-threshold-offset"' in response.text
    assert "include_attention" in response.text
    assert "include_extended_chip" in response.text
    assert "include_extended_data" in response.text
    assert "threshold_offset" in response.text
    assert "外部指數同步" in response.text
    assert 'id="dashboard-workbench-mode"' in response.text
    assert 'id="dashboard-today-decision"' in response.text
    assert 'id="dashboard-today-decision-action"' in response.text
    assert "renderTodayDecision" in response.text
    assert "today-decision-panel" in response.text
    assert 'data-workbench-mode="daily"' in response.text
    assert 'data-workbench-mode="ops"' in response.text
    assert 'data-open-pipeline-ops="true"' in response.text
    assert 'id="pipeline-ops-details"' in response.text
    assert 'id="pipeline-universe-preview"' in response.text
    assert '<option value="research_ready" selected>' in response.text
    assert "研究就緒池（建議：歷史 + 法人 + 流動性 + 分散）" in response.text
    assert 'id="refresh-universe-preview"' in response.text
    assert "/api/universe/preview" in response.text
    assert "renderUniversePreview" in response.text
    assert "refreshUniversePreview" in response.text
    assert "UNIVERSE PREVIEW" in response.text
    assert "分類來源" in response.text
    assert "WORKBENCH_MODE_STORAGE_KEY" in response.text
    assert "setWorkbenchMode" in response.text
    assert "openPipelineOpsTools" in response.text
    assert 'id="dashboard-ops-readiness-status"' in response.text
    assert 'id="refresh-ops-readiness"' in response.text
    assert "/api/ops/readiness" in response.text
    assert "renderOpsReadiness" in response.text
    assert 'id="dashboard-phase1-status"' in response.text
    assert 'id="refresh-phase1-acceptance"' in response.text
    assert "/api/radar/phase1-acceptance" in response.text
    assert "renderPhase1Acceptance" in response.text
    assert "refreshPhase1Acceptance" in response.text
    assert "PHASE 1 ACCEPTANCE" in response.text
    assert "handleOpsReadinessAction" in response.text
    assert "intraday_quote_service" in response.text
    assert "intraday-quote-service" in response.text
    assert "quote_disconnected" in response.text
    assert "opsReadinessPostSessionReviewHtml" in response.text
    assert "post_session_review" in response.text
    assert "盤後回顧" in response.text
    assert "ops-readiness-review" in response.text
    assert "ops-readiness-issue" in response.text
    assert "data-ops-action" in response.text
    assert 'id="dashboard-data-overview"' in response.text
    assert 'id="refresh-data-overview"' in response.text
    assert 'id="dashboard-data-repair-queue"' in response.text
    assert "renderDataRepairQueue" in response.text
    assert "handleDataRepairAction" in response.text
    assert "data-data-repair-action" in response.text
    assert "retry-data-sync" in response.text
    assert "market-data-repair" in response.text
    assert "latestDataRepairItems" in response.text
    assert "applyDataRepairRetryScope" in response.text
    assert "applyMarketDataRepairScope" in response.text
    assert "scope_failures" in response.text
    assert "symbol_failures" in response.text
    assert "dataRepairComparisonBaseline" in response.text
    assert "compareDataRepairSnapshots" in response.text
    assert "REPAIR DELTA" in response.text
    assert "data-repair-comparison" in response.text
    assert 'id="dashboard-screener-gate-status"' in response.text
    assert 'id="refresh-screener-gate"' in response.text
    assert 'id="dashboard-screener-gate-metrics"' in response.text
    assert "/api/radar/decision-gate" in response.text
    assert "renderScreenerDecisionGate" in response.text
    assert "handleScreenerGateAction" in response.text
    assert "findScreenerGateRepairItem" in response.text
    assert "applyScreenerGateRepairAction" in response.text
    assert "screenerGateRepairAutoRerunRadar" in response.text
    assert "完成後會重跑每日雷達" in response.text
    assert "資料修復完成，接著重跑每日雷達" in response.text
    assert 'id="dashboard-market-context"' in response.text
    assert "market_context_coverage" in response.text
    assert 'id="dashboard-market-data-coverage"' in response.text
    assert "market_data_coverage" in response.text
    assert "buildMarketDataCoverageRepairItems" in response.text
    assert "market_data_coverage_gap" in response.text
    assert "market-context-chip" in response.text
    assert "component.optional" in response.text
    assert 'id="dashboard-radar-health-status"' in response.text
    assert 'id="refresh-radar-health"' in response.text
    assert 'id="dashboard-radar-sync-issues"' in response.text
    assert "/api/radar/deployment-health" in response.text
    assert "renderRadarDeploymentHealth" in response.text
    assert "data?.sync_issues" in response.text
    assert "entry_ready_diagnosis" in response.text
    assert "無可進場診斷" in response.text
    assert "sync-issue-card" in response.text
    assert 'id="dashboard-radar-trigger-monitor-status"' in response.text
    assert 'id="refresh-radar-trigger-monitor"' in response.text
    assert 'id="radar-trigger-price-source"' in response.text
    assert 'id="radar-trigger-streaming-seconds"' in response.text
    assert 'id="radar-trigger-max-price-age"' in response.text
    assert 'id="radar-trigger-shioaji-live"' in response.text
    assert 'id="intraday-quote-symbols"' in response.text
    assert 'id="start-intraday-quote-service"' in response.text
    assert 'id="stop-intraday-quote-service"' in response.text
    assert "/api/radar/trigger-monitor" in response.text
    assert "/api/quotes/intraday/status" in response.text
    assert "/api/quotes/intraday/start" in response.text
    assert "/api/quotes/intraday/stop" in response.text
    assert "radarTriggerMonitorRequestParams" in response.text
    assert "radarQuoteConnectionLabel" in response.text
    assert "renderIntradayQuoteService" in response.text
    assert "intradayQuoteHealthTone" in response.text
    assert "startIntradayQuoteService" in response.text
    assert "shioaji_streaming" in response.text
    assert "renderRadarTriggerMonitor" in response.text
    assert "觸發觀察監控" in response.text
    assert 'id="dashboard-radar-quality-status"' in response.text
    assert 'id="dashboard-radar-quality-summary"' in response.text
    assert 'id="dashboard-radar-quality-picks"' in response.text
    assert "data_readiness" in response.text
    assert "資料可信度" in response.text
    assert "QUALITY DELTA" in response.text
    assert "radarQualityComparisonBaseline" in response.text
    assert "compareRadarQualitySnapshots" in response.text
    assert "open-radar-quality-repair" in response.text
    assert "openRadarQualityRepairQueue" in response.text
    assert "QUALITY LANES" in response.text
    assert "quality_lanes" in response.text
    assert "radar-quality-lane" in response.text
    assert "data-quality-lane-action" in response.text
    assert "copyRadarQualityLaneSymbols" in response.text
    assert "applyRadarQualityLaneUniverse" in response.text
    assert "handleRadarQualityLaneAction" in response.text
    assert "套用觀察池" in response.text
    assert "查看修復" in response.text
    assert 'id="refresh-radar-quality"' in response.text
    assert "/api/radar/quality-summary" in response.text
    assert "renderRadarQualitySummary" in response.text
    assert "radarQualityTopPickSymbolsText" in response.text
    assert "copyRadarQualityTopPicks" in response.text
    assert "applyRadarQualityTopPicksUniverse" in response.text
    assert 'id="copy-radar-quality-picks"' in response.text
    assert 'id="apply-radar-quality-picks"' in response.text
    assert "候選品質總覽" in response.text
    assert 'id="dashboard-radar-feedback-status"' in response.text
    assert 'id="dashboard-radar-feedback-summary"' in response.text
    assert 'id="dashboard-radar-feedback-list"' in response.text
    assert 'id="refresh-radar-feedback"' in response.text
    assert "/api/radar/trading-feedback" in response.text
    assert "renderRadarTradingFeedback" in response.text
    assert "交易回饋摘要" in response.text
    assert 'id="dashboard-radar-change-summary"' in response.text
    assert 'id="dashboard-radar-change-lists"' in response.text
    assert 'id="refresh-radar-changes"' in response.text
    assert "/api/radar/changes" in response.text
    assert "renderRadarChanges" in response.text
    assert "每個交易日只採最新 run" in response.text
    assert "renderRadarChangeLane" in response.text
    assert 'id="dashboard-radar-stability-summary"' in response.text
    assert 'id="dashboard-radar-stability-list"' in response.text
    assert 'id="refresh-radar-stability"' in response.text
    assert "/api/radar/stability" in response.text
    assert "renderRadarStability" in response.text
    assert "renderRadarStabilityItem" in response.text
    assert "連續 3 日+" in response.text
    assert 'id="dashboard-radar-priority-summary"' in response.text
    assert 'id="dashboard-radar-priority-list"' in response.text
    assert 'id="refresh-radar-priority"' in response.text
    assert "/api/radar/research-priority" in response.text
    assert "renderRadarResearchPriority" in response.text
    assert "renderRadarPriorityItem" in response.text
    assert "radar-priority-feedback" in response.text
    assert 'id="dashboard-radar-allocation-summary"' in response.text
    assert 'id="dashboard-radar-allocation-list"' in response.text
    assert 'id="refresh-radar-allocation"' in response.text
    assert "data-calibration-profile" in response.text
    assert "/api/radar/allocation-plan" in response.text
    assert "renderRadarAllocationPlan" in response.text
    assert "renderRadarAllocationItem" in response.text
    assert "renderRadarAllocationDiagnostics" in response.text
    assert "renderRadarAllocationReviewQueue" in response.text
    assert "renderRadarAllocationReviewSummary" in response.text
    assert "copyRadarAllocationReviewSymbols" in response.text
    assert "downloadRadarAllocationReviewCsv" in response.text
    assert "applyRadarAllocationReviewUniverse" in response.text
    assert "applySymbolsToCustomUniverse" in response.text
    assert "data-allocation-review-copy" in response.text
    assert "data-allocation-review-download" in response.text
    assert "data-allocation-review-apply" in response.text
    assert "行動線摘要" in response.text
    assert "配置診斷" in response.text
    assert 'id="dashboard-radar-allocation-outcome-summary"' in response.text
    assert 'id="dashboard-radar-allocation-outcome-list"' in response.text
    assert 'id="refresh-radar-allocation-outcomes"' in response.text
    assert "/api/radar/allocation-outcomes" in response.text
    assert "renderRadarAllocationOutcomes" in response.text
    assert "每個交易日只計一次" in response.text
    assert "renderRadarAllocationOutcomeRun" in response.text
    assert 'id="dashboard-radar-calibration-summary"' in response.text
    assert "每檔每交易日只計一次" in response.text
    assert 'id="dashboard-radar-calibration-list"' in response.text
    assert 'id="refresh-radar-calibration"' in response.text
    assert "/api/radar/allocation-calibration" in response.text
    assert "renderRadarAllocationCalibration" in response.text
    assert "renderRadarCalibrationBucket" in response.text
    assert 'id="dashboard-investment-actions"' in response.text
    assert "renderDashboardInvestmentActions" in response.text
    assert "applyDashboardActionFilter" in response.text
    assert "investment-symbol-chip" in response.text
    assert "data-dashboard-action-filter" in response.text
    assert 'id="dashboard-action-queue"' in response.text
    assert "renderDashboardActionQueue" in response.text
    assert "data-queue-preview" in response.text
    assert "prepareTwPreviewFromRadarSymbol" in response.text
    assert 'id="radar-compare-panel"' in response.text
    assert 'id="radar-compare-content"' in response.text
    assert 'id="copy-radar-compare-csv"' in response.text
    assert 'id="download-radar-compare-csv"' in response.text
    assert 'id="clear-radar-compare"' in response.text
    assert "data-radar-compare" in response.text
    assert "renderRadarComparePanel" in response.text
    assert "toggleRadarCompare" in response.text
    assert "buildRadarCompareCsv" in response.text
    assert "copyRadarCompareCsv" in response.text
    assert "downloadRadarCompareCsv" in response.text
    assert "radarCompareDecisionScore" in response.text
    assert "radarCompareConclusion" in response.text
    assert "radarComparePortfolioVerdict" in response.text
    assert "compare_score" in response.text
    assert "比較結論" in response.text
    assert "組合判讀" in response.text
    assert "radar-compare-decision" in response.text
    assert 'id="strategy-upgrade-decision"' in response.text
    assert "策略升級決策" in response.text
    assert "renderStrategyUpgradeDecision" in response.text
    assert "strategyUpgradeMeta" in response.text
    assert 'id="tw-preview-strategy-name"' in response.text
    assert 'id="tw-preview-strategy-version"' in response.text
    assert 'id="tw-preview-signal-id"' in response.text
    assert "twPreviewStrategyTracking" in response.text
    assert "daily_radar" in response.text
    assert "data-queue-exclude" in response.text
    assert "每日雷達卡片" in response.text
    assert "upsertSymbolExclusion(symbol" in response.text
    assert "資料健康度" in response.text
    assert 'id="tw-preview-side"' in response.text
    assert 'id="tw-preview-order-lot"' in response.text
    assert 'id="tw-preview-reference-price"' in response.text
    assert 'id="tw-preview-limit-up"' in response.text
    assert 'id="tw-preview-limit-down"' in response.text
    assert 'id="tw-preview-reference-status"' in response.text
    assert 'id="tw-preview-position"' in response.text
    assert 'id="tw-preview-sellable-position"' in response.text
    assert 'id="tw-preview-position-value"' in response.text
    assert 'id="tw-preview-portfolio-equity"' in response.text
    assert 'id="tw-preview-portfolio-exposure"' in response.text
    assert 'id="tw-preview-max-single-position-pct"' in response.text
    assert 'id="tw-preview-max-exposure-pct"' in response.text
    assert 'id="tw-preview-daily-pnl"' in response.text
    assert 'id="tw-preview-max-daily-loss-pct"' in response.text
    assert 'id="tw-preview-position-status"' in response.text
    assert 'id="tw-preview-portfolio-status"' in response.text
    assert 'id="tw-preview-gate-summary"' in response.text
    assert "preview-gate-summary" in response.text
    assert "latestTwPositionBySymbol" in response.text
    assert "latestTwPositionValueBySymbol" in response.text
    assert "latestTwPendingSellBySymbol" in response.text
    assert "latestTwSellableBySymbol" in response.text
    assert "latestTwSellableSourceBySymbol" in response.text
    assert "latestTwPortfolioRisk" in response.text
    assert "isSellSide" in response.text
    assert "isBuySide" in response.text
    assert "snapshotPositionPnl" in response.text
    assert "twPnlSourceLabel" in response.text
    assert "twPnlBreakdownText" in response.text
    assert "realized_pnl" in response.text
    assert "unrealized_pnl" in response.text
    assert "pnl_source" in response.text
    assert "sellable_quantity" in response.text
    assert "sellableSourceFromSnapshotItem" in response.text
    assert "twSellableSourceLabel" in response.text
    assert "券商可賣欄位" in response.text
    assert "昨日庫存" in response.text
    assert "current_portfolio_exposure" in response.text
    assert "max_portfolio_exposure_pct" in response.text
    assert "share_quantity" in response.text
    assert "twOrderShareQuantity" in response.text
    assert "daily_pnl" in response.text
    assert "max_daily_loss_pct" in response.text
    assert "applyTwPreviewPortfolioRiskFromSnapshot" in response.text
    assert "renderTwPreviewGateSummary" in response.text
    assert "twPreviewPriceGateState" in response.text
    assert "twPreviewPositionGateState" in response.text
    assert "twPreviewSinglePositionGateState" in response.text
    assert "twPreviewExposureGateState" in response.text
    assert "twPreviewDailyLossGateState" in response.text
    assert "正式檢查以 Preview metadata 為準" in response.text
    assert "正式 Preview 建立前帳戶快照查詢失敗" in response.text
    assert "正式 Preview 建立前參考價查詢失敗" in response.text
    assert "refreshTwReferencePrice" in response.text
    assert "twPriceGuardDefaults" in response.text
    assert "updateTwPriceGuardHint" in response.text
    assert "limit_up_price" in response.text
    assert "limit_down_price" in response.text
    assert "applyTwPreviewPositionFromSnapshot" in response.text
    assert 'id="tw-readiness-card"' in response.text
    assert 'id="tw-check-readiness-paper"' in response.text
    assert 'id="tw-check-readiness-live"' in response.text
    assert 'id="tw-eod-summary-card"' in response.text
    assert 'id="tw-refresh-eod-summary"' in response.text
    assert "/api/tw-live/eod-summary" in response.text
    assert "renderEodSummary" in response.text
    assert 'id="tw-eod-preview-queue"' in response.text
    assert "eod-preview-queue" in response.text
    assert "/api/tw-live/promotion-readiness" in response.text
    assert "renderPromotionReadiness" in response.text
    assert "promotionReadinessMeta" in response.text
    assert 'id="symbol-detail-panel"' in response.text
    assert "data-symbol-detail" in response.text
    assert "標的詳情" in response.text
    assert 'id="radar-outcome-summary"' in response.text
    assert 'id="radar-outcome-horizons"' in response.text
    assert 'id="refresh-radar-outcomes"' in response.text
    assert 'id="radar-outcome-industry-table"' in response.text
    assert 'id="radar-outcome-reason-table"' in response.text
    assert 'id="radar-outcome-heat-table"' in response.text
    assert 'id="radar-signal-catalog-table"' in response.text
    assert 'id="radar-signal-catalog-meta"' in response.text
    assert "候選命中率" in response.text
    assert "族群命中率" in response.text
    assert "理由命中率" in response.text
    assert "波動熱度命中率" in response.text
    assert "訊號說明與可靠度" in response.text
    assert "可靠度加權" in response.text
    assert "候選命中率（後續 5 / 10 / 20 根日線）" in response.text
    assert "radarOutcomeReliabilityHorizonText" in response.text
    assert "radarOutcomeReliabilityBoost" in response.text
    assert "OUTCOME_RELIABILITY_BOOST=" in response.text
    assert "refreshSignalCatalog" in response.text
    assert "industry-trend-row" in response.text
    assert "INDUSTRY_TREND=" in response.text
    assert "族群趨勢" in response.text
    assert "風險品質優先" in response.text
    assert "radarRiskQualityScore" in response.text
    assert "radarRiskLayerMeta" in response.text
    assert "風險層" in response.text
    assert "市場狀態" in response.text
    assert "requested_risk_profile" in response.text
    assert "Entry Score 高到低" in response.text
    assert "風險調整分高到低" in response.text
    assert "風險分數高到低" in response.text
    assert "risk_adjusted_score" in response.text
    assert "RISK_ADJUSTED_SCORE=" in response.text
    assert "風險調整分：" in response.text
    assert "StrategyResearchSpec" in response.text
    assert "Walk-forward" in response.text
    assert "研究假說" in response.text
    assert "跨標的敏感度" in response.text
    assert "跨族群壓力" in response.text
    assert "參數穩健性摘要" in response.text
    assert "parameterRobustnessMeta" in response.text
    assert "策略比較視圖：Threshold" in response.text
    assert "策略比較視圖：族群壓力" in response.text
    assert "匯出 HTML/CSV/JSON" in response.text
    assert 'id="validation-symbols"' in response.text
    assert 'id="validation-max-symbols"' in response.text
    assert 'id="validation-min-rows"' in response.text
    assert 'id="validation-per-industry"' in response.text
    assert 'id="validation-lookback-days"' in response.text
    assert "正式產業" in response.text


def test_notification_status_api_reports_configured_channels_without_secrets(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("LINE_NOTIFY_TOKEN", "line-secret-token")

    with TestClient(app) as client:
        response = client.get("/api/notifications/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["channels"]["telegram"]["configured"] is False
    assert payload["data"]["channels"]["telegram"]["missing"] == ["TELEGRAM_CHAT_ID"]
    assert payload["data"]["channels"]["line"]["configured"] is True
    assert payload["data"]["channels"]["line"]["missing"] == []
    assert "telegram-secret-token" not in response.text
    assert "line-secret-token" not in response.text


def test_control_panel_home_exposes_symbol_exclusion_manager(monkeypatch):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'id="symbol-exclusion-symbol"' in response.text
    assert 'id="symbol-exclusion-reason"' in response.text
    assert 'id="add-symbol-exclusion"' in response.text
    assert 'id="symbol-exclusion-list"' in response.text
    assert "refreshSymbolExclusions" in response.text
    assert "upsertSymbolExclusionFromForm" in response.text


def test_index_constituents_api_imports_and_lists_cache(monkeypatch, tmp_path):
    import app.data.index_constituents as index_constituents

    class _Response:
        url = "https://backend.taiwanindex.com.tw/files/tip.csv"
        headers = {"content-type": "text/csv"}
        content = "證券代號,證券名稱\n1101,台泥\n2303,聯電\n".encode("utf-8")

        def raise_for_status(self):
            return None

    def fake_get(url, timeout):  # noqa: ANN001
        assert url == "https://backend.taiwanindex.com.tw/files/tip.csv"
        assert timeout == 15
        return _Response()

    monkeypatch.setattr(index_constituents, "DEFAULT_INDEX_CONSTITUENTS_DIR", tmp_path)
    monkeypatch.setattr(index_constituents.requests, "get", fake_get)
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        empty = client.get("/api/index-constituents")
        added = client.post(
            "/api/index-constituents",
            json={
                "index_key": "custom",
                "source": "official_text",
                "content": "證券代號,證券名稱\n2330,台積電\n2454,聯發科\n",
            },
        )
        url_added = client.post(
            "/api/index-constituents",
            json={
                "index_key": "tip",
                "source": "tip_official_url",
                "url": "https://backend.taiwanindex.com.tw/files/tip.csv",
            },
        )
        bad_url = client.post(
            "/api/index-constituents",
            json={
                "index_key": "tip",
                "source": "tip_official_url",
                "url": "http://backend.taiwanindex.com.tw/files/tip.csv",
            },
        )
        fetched = client.get("/api/index-constituents")

    assert empty.status_code == 200
    assert empty.json()["data"]["items"] == []
    assert added.status_code == 200
    imported = added.json()["data"]["imported"]
    assert imported["index_key"] == "custom"
    assert imported["symbols"] == ["2330", "2454"]
    assert imported["count"] == 2
    assert url_added.status_code == 200
    url_imported = url_added.json()["data"]["imported"]
    assert url_imported["index_key"] == "tip"
    assert url_imported["symbols"] == ["1101", "2303"]
    assert url_imported["url"] == "https://backend.taiwanindex.com.tw/files/tip.csv"
    assert bad_url.status_code == 400
    assert bad_url.json()["error"] == "official_url_https_required"
    assert fetched.status_code == 200
    items = {item["index_key"]: item for item in fetched.json()["data"]["items"]}
    assert items["custom"]["count"] == 2
    assert items["tip"]["count"] == 2


def test_symbol_exclusion_api_roundtrip(monkeypatch, tmp_path):
    exclusions = tmp_path / "excluded_symbols.txt"
    monkeypatch.setattr(control_panel_app, "SYMBOL_EXCLUSIONS_FILE", exclusions)
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        empty = client.get("/api/symbol-exclusions")
        added = client.post("/api/symbol-exclusions", json={"symbol": "2330.TW", "reason": "valuation"})
        fetched = client.get("/api/symbol-exclusions")
        removed = client.delete("/api/symbol-exclusions/2330.TW")

    assert empty.status_code == 200
    assert empty.json()["data"]["items"] == []
    assert added.status_code == 200
    assert added.json()["data"]["items"] == [{"symbol": "2330", "reason": "valuation"}]
    assert fetched.json()["data"]["items"] == [{"symbol": "2330", "reason": "valuation"}]
    assert removed.status_code == 200
    assert removed.json()["data"]["items"] == []


def test_latest_radar_export_includes_html_csv_json(monkeypatch, tmp_path):
    from app.db.repository import TradingRepository as RealTradingRepository

    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _RadarRepo:
        _daily_radar_quality_summary = staticmethod(RealTradingRepository._daily_radar_quality_summary)

        def __init__(self, _session):
            pass

        def get_latest_daily_radar(self, count=50):  # noqa: ARG002
            return [
                {
                    "run_id": "radar_test",
                    "run_date": "2026-06-25",
                    "rank": 1,
                    "symbol": "2330",
                    "name": "=FORMULA",
                    "entry_score": 82.5,
                    "risk_adjusted_score": 85.4,
                    "risk_score": 0.2,
                    "industry_group": "半導體業",
                    "industry_rotation_score": 0.62,
                    "action_label": "ENTRY_READY",
                    "reason_tags": ["trend"],
                    "blocker_tags": [],
                    "data_quality": "fresh",
                    "fundamental_data_quality": "fresh",
                }
            ]

        def get_daily_radar_allocation_plan(self, **kwargs):  # noqa: ANN001
            assert kwargs["calibration_profile"] == "standard"
            return {
                "status": "ok",
                "research_budget": 350000.0,
                "allocated_value": 76000.0,
                "allocation_count": 1,
                "calibration_profile": {"key": "standard", "label": "標準", "enabled": True},
                "allocation_diagnostics": {
                    "risk_level": "watch",
                    "verdict": "可小額研究，先補檢查",
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
                                        "industry_group": "半導體業",
                                        "action_label": "ENTRY_READY",
                                        "suggested_value": 76000.0,
                                        "calibration_multiplier": 1.08,
                                        "allocated": True,
                                        "reason": "可先檢查價格",
                                        "alert_rule": "即時價格、部位與 promotion gate 都通過後建立 Preview",
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
                        "next_step": "可建立 paper/Preview，仍需通過 promotion 與 live gate",
                    }
                ],
            }

        def get_daily_radar_allocation_outcome_summary(self, **kwargs):  # noqa: ANN001, ARG002
            return {
                "status": "ok",
                "verdict": "配置優於排名基準",
                "average_alpha": 0.012,
                "allocation_positive_rate": 0.75,
                "evaluated_run_count": 4,
            }

        def get_daily_radar_allocation_calibration(self, **kwargs):  # noqa: ANN001
            assert kwargs["calibration_profile"] == "standard"
            return {
                "status": "ok",
                "profile": {"key": "standard", "label": "標準", "enabled": True},
                "top_positive": [{"label": "半導體業", "multiplier": 1.08}],
                "top_negative": [{"label": "過熱波動", "multiplier": 0.94}],
            }

        def get_daily_radar_trading_feedback_summary(self, **kwargs):  # noqa: ANN001
            assert kwargs["count"] == 50
            assert kwargs["limit"] == 100
            return {
                "status": "ok",
                "run_id": "radar_latest",
                "run_date": "2026-06-25",
                "candidate_count": 1,
                "verdict": "交易回饋需留意",
                "risk_level": "warn",
                "primary_action": "先清理待決策 Preview，再建立新單。",
                "metrics": {"affected_symbol_count": 1, "pending_preview_count": 1},
                "alerts": [
                    {
                        "code": "pending_preview",
                        "label": "候選標的有待決策 Preview",
                        "severity": "warn",
                        "detail": "1 筆 Preview 尚未決策。",
                        "action": "先清理待決策 Preview。",
                    }
                ],
                "symbols": [{"symbol": "2330", "name": "台積電", "risk_level": "warn", "pending_preview_count": 1}],
            }

        def get_data_health_overview(self, **kwargs):  # noqa: ANN001
            assert kwargs["freshness_days"] == 3
            return {"status": "ok", "score": 100, "sync_jobs": []}

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(control_panel_app, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _RadarRepo)
    with TestClient(app) as client:
        response = client.post("/api/radar/latest/export-summary")

    assert response.status_code == 200
    payload = response.json()
    export = payload["data"]["export"]
    assert set(export["files"]) == {
        "json",
        "csv",
        "decision_brief_md",
        "quality_lane_research_now_csv",
        "quality_lane_research_now_symbols_txt",
        "quality_lane_observe_only_csv",
        "quality_lane_observe_only_symbols_txt",
        "quality_lane_repair_first_csv",
        "quality_lane_repair_first_symbols_txt",
        "quality_lane_skip_for_now_csv",
        "quality_lane_skip_for_now_symbols_txt",
        "allocation_review_preview_csv",
        "allocation_review_trigger_csv",
        "allocation_review_risk_csv",
        "allocation_review_preview_symbols_txt",
        "allocation_review_trigger_symbols_txt",
        "allocation_review_risk_symbols_txt",
        "html_summary",
    }
    assert set(export["urls"]) == {
        "json",
        "csv",
        "decision_brief_md",
        "quality_lane_research_now_csv",
        "quality_lane_research_now_symbols_txt",
        "quality_lane_observe_only_csv",
        "quality_lane_observe_only_symbols_txt",
        "quality_lane_repair_first_csv",
        "quality_lane_repair_first_symbols_txt",
        "quality_lane_skip_for_now_csv",
        "quality_lane_skip_for_now_symbols_txt",
        "allocation_review_preview_csv",
        "allocation_review_trigger_csv",
        "allocation_review_risk_csv",
        "allocation_review_preview_symbols_txt",
        "allocation_review_trigger_symbols_txt",
        "allocation_review_risk_symbols_txt",
        "html_summary",
    }
    for filename in export["files"].values():
        assert (tmp_path / filename).exists()
    csv_text = (tmp_path / export["files"]["csv"]).read_text(encoding="utf-8")
    quality_research_csv = (tmp_path / export["files"]["quality_lane_research_now_csv"]).read_text(encoding="utf-8")
    quality_research_symbols_txt = (tmp_path / export["files"]["quality_lane_research_now_symbols_txt"]).read_text(encoding="utf-8")
    preview_lane_csv = (tmp_path / export["files"]["allocation_review_preview_csv"]).read_text(encoding="utf-8")
    preview_symbols_txt = (tmp_path / export["files"]["allocation_review_preview_symbols_txt"]).read_text(encoding="utf-8")
    brief_text = (tmp_path / export["files"]["decision_brief_md"]).read_text(encoding="utf-8")
    json_text = (tmp_path / export["files"]["json"]).read_text(encoding="utf-8")
    html_text = (tmp_path / export["files"]["html_summary"]).read_text(encoding="utf-8")
    assert "'=FORMULA" in csv_text
    assert "diversified_shortlist_rank" in csv_text
    assert "investment_action" in csv_text
    assert "可進場" in csv_text
    assert "action_queue_lanes" in csv_text
    assert "先看進場" in csv_text
    assert "allocation_suggested_value" in csv_text
    assert "allocation_calibration_multiplier" in csv_text
    assert "allocation_review_alert_rule" in csv_text
    assert "allocation_review_monitor_only" in csv_text
    assert "allocation_review_allocation_lane" in csv_text
    assert "allocation_review_status" in csv_text
    assert "candidate_quality_score" in quality_research_csv
    assert "2330" in quality_research_csv
    assert quality_research_symbols_txt.strip() == "2330"
    assert "allocation_review_checklist" in csv_text
    assert "allocation_review_trading_feedback_risk" in csv_text
    assert "allocation_review_trading_feedback" in csv_text
    assert "allocation_review_focus" in csv_text
    assert "allocation_review_primary_action" in csv_text
    assert "allocation_review_next_session_note" in csv_text
    assert "risk_adjusted_score" in csv_text
    assert "risk_quality_score" in csv_text
    assert "82.5,85.4,0.2" in csv_text
    assert "先檢查可建立 Preview" in csv_text
    assert "review_rank,lane,lane_label,symbol" in preview_lane_csv
    assert "entry_score,risk_adjusted_score,risk_score" in preview_lane_csv
    assert "risk_quality_score,risk_quality_label,risk_quality_reason" in preview_lane_csv
    assert "alert_rule,checklist" in preview_lane_csv
    assert "trading_feedback_risk,trading_feedback" in preview_lane_csv
    assert "monitor_only,allocation_lane,allocation_status" in preview_lane_csv
    assert "即時價格、部位與 promotion gate 都通過後建立 Preview" in preview_lane_csv
    assert "確認即時價格與參考價偏離 | 查詢目前持股" in preview_lane_csv
    assert preview_symbols_txt.strip() == "2330"
    assert "76000.0" in csv_text
    assert '"count": 1' in json_text
    assert '"investment_actions"' in json_text
    assert '"entry_count": 1' in json_text
    assert '"action_queue"' in json_text
    assert '"entry_focus": 1' in json_text
    assert '"diversified_shortlist"' in json_text
    assert '"allocation_plan"' in json_text
    assert '"allocation_outcomes"' in json_text
    assert '"allocation_calibration"' in json_text
    assert '"decision_brief"' in json_text
    assert '"quality_summary"' in json_text
    assert '"trading_feedback"' in json_text
    assert payload["data"]["export"]["urls"]["decision_brief_md"].endswith("_brief.md")
    assert payload["data"]["allocation_plan"]["allocation_items"][0]["symbol"] == "2330"
    assert payload["data"]["trading_feedback"]["risk_level"] == "warn"
    assert payload["data"]["decision_brief"]["handoff"]["primary_action"] == "先檢查可建立 Preview"
    assert payload["data"]["decision_brief"]["quality"]["top_symbols"] == ["2330"]
    assert payload["data"]["decision_brief"]["trading_feedback"]["affected_symbols"] == ["2330"]
    assert payload["data"]["decision_brief"]["lanes"][0]["items"][0]["symbol"] == "2330"
    assert payload["data"]["export"]["data"]["decision_brief"]["handoff"]["primary_action"] == "先檢查可建立 Preview"
    assert payload["data"]["export"]["data"]["decision_brief"]["quality"]["top_symbols"] == ["2330"]
    assert payload["data"]["export"]["data"]["decision_brief"]["trading_feedback"]["affected_symbols"] == ["2330"]
    persisted_payload = json.loads(json_text)
    assert persisted_payload["quality_summary"]["status"] == "ok"
    assert persisted_payload["quality_summary"]["top_picks"][0]["symbol"] == "2330"
    assert persisted_payload["trading_feedback"]["symbols"][0]["symbol"] == "2330"
    assert persisted_payload["decision_brief"]["handoff"]["primary_action"] == "先檢查可建立 Preview"
    assert persisted_payload["decision_brief"]["quality"]["top_symbols"] == ["2330"]
    assert persisted_payload["decision_brief"]["trading_feedback"]["affected_symbols"] == ["2330"]
    assert persisted_payload["decision_brief"]["lanes"][0]["items"][0]["symbol"] == "2330"
    assert payload["data"]["allocation_outcomes"]["verdict"] == "配置優於排名基準"
    assert payload["data"]["allocation_calibration"]["top_positive"][0]["label"] == "半導體業"

    sent_messages: list[str] = []

    class _BriefNotifier:
        def send(self, message: str) -> None:
            sent_messages.append(message)

    monkeypatch.setattr("app.scheduler.jobs.build_notifiers", lambda channels: [_BriefNotifier()])
    with TestClient(app) as client:
        preview_response = client.post(
            "/api/radar/latest/brief-notification",
            json={"notify": "none", "send": False},
        )
        alert_preview_response = client.post(
            "/api/radar/latest/alert-plan",
            json={"notify": "none", "send": False},
        )
        invalid_response = client.post(
            "/api/radar/latest/brief-notification",
            json={"notify": "sms", "send": False},
        )
        alert_invalid_response = client.post(
            "/api/radar/latest/alert-plan",
            json={"notify": "sms", "send": False},
        )

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert "主線: 先檢查可建立 Preview" in preview_payload["data"]["message"]
    assert "telegram" in preview_payload["data"]["channel_status"]
    assert "line" in preview_payload["data"]["channel_status"]
    assert preview_payload["data"]["notification"]["skipped_reason"] == "preview_only"
    assert alert_preview_response.status_code == 200
    alert_preview_payload = alert_preview_response.json()
    assert "[每日雷達] 2026-06-25 提醒檢查" in alert_preview_payload["data"]["message"]
    assert "可先建立 Preview: 1 檔" in alert_preview_payload["data"]["message"]
    assert "清單內標的可優先建立 Preview" in alert_preview_payload["data"]["message"]
    assert alert_preview_payload["data"]["alert_plan"]["summary"]["preview_count"] == 1
    assert alert_preview_payload["data"]["alert_plan"]["lanes"][0]["items"][0]["symbol"] == "2330"
    assert alert_preview_payload["data"]["notification"]["skipped_reason"] == "preview_only"
    assert sent_messages == []
    with TestClient(app) as client:
        send_response = client.post(
            "/api/radar/latest/brief-notification",
            json={"notify": "telegram", "send": True},
        )
        alert_send_response = client.post(
            "/api/radar/latest/alert-plan",
            json={"notify": "telegram", "send": True},
        )
    assert send_response.status_code == 200
    send_payload = send_response.json()
    assert send_payload["data"]["notification"]["sent"] == 1
    assert send_payload["data"]["notification"]["triggered"] is True
    assert "主線: 先檢查可建立 Preview" in sent_messages[0]
    assert alert_send_response.status_code == 200
    alert_send_payload = alert_send_response.json()
    assert alert_send_payload["data"]["notification"]["sent"] == 1
    assert alert_send_payload["data"]["notification"]["triggered"] is True
    assert "提醒檢查" in sent_messages[1]
    assert "規則: 清單內標的可優先建立 Preview" in sent_messages[1]
    assert invalid_response.status_code == 400
    assert alert_invalid_response.status_code == 400
    assert "分散首選" in html_text
    assert "## 候選品質" in brief_text
    assert "品質首選: 2330" in brief_text
    assert "## 交易回饋" in brief_text
    assert "交易警示: 候選標的有待決策 Preview - 1 筆 Preview 尚未決策。" in brief_text
    assert "研究配置與驗證" in html_text
    assert "風險品質" in html_text
    assert "核心候選" in html_text
    assert "盤前交接" in html_text
    assert "可先建立 Preview 1 檔：2330" in html_text
    assert "盤前 Brief MD" in html_text
    assert "提醒計畫" in html_text
    assert "先檢查即時價格" in html_text
    assert "配置優於排名基準" in html_text
    assert "# 每日雷達盤前 Brief" in brief_text
    assert "- 主線: 先檢查可建立 Preview" in brief_text
    assert "### 可先建立 Preview (1 檔)" in brief_text


def test_data_overview_api_returns_workflow_health(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_data_health_overview(self):
            return {
                "status": "ok",
                "score": 100,
                "components": [{"key": "daily_bars", "label": "日線資料", "status": "ok", "value": "2026-06-25"}],
                "market_data_coverage": {
                    "status": "warn",
                    "datasets": [{"key": "daily_bars", "missing_symbols": ["3105"], "coverage_pct": 50.0}],
                },
                "sync_jobs": [{"job_name": "daily_radar", "status": "ok", "rows_upserted": 2, "run_at": "2026-06-25T08:00:00"}],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/data/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["score"] == 100
    assert payload["data"]["components"][0]["key"] == "daily_bars"
    assert payload["data"]["market_data_coverage"]["datasets"][0]["missing_symbols"] == ["3105"]


def test_ops_readiness_api_returns_aggregate_summary(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

    class _OpsReadinessService:
        def __init__(self, **kwargs):  # noqa: ANN003
            assert kwargs["min_rows"] == 8
            assert kwargs["eod_limit"] == 50

        def build(self, _repo, *, target_phase="paper"):  # noqa: ANN001
            assert target_phase == "small_live"
            return {
                "status": "danger",
                "status_text": "需處理",
                "target_phase": target_phase,
                "components": [{"key": "daily_radar", "label": "盤前每日雷達", "status": "warn", "value": "需留意"}],
                "metrics": {"daily_radar_rows": 15, "pending_preview_count": 3, "target_readiness": "live_blocked"},
                "next_steps": ["先清理待決策 Preview。"],
                "decision_brief": {
                    "state": "blocked",
                    "title": "今天先暫停建立新部位",
                    "primary_action": {"key": "ops-readiness", "label": "查看阻擋項目"},
                },
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    monkeypatch.setattr("app.reports.ops_readiness.OpsReadinessService", _OpsReadinessService)
    with TestClient(app) as client:
        response = client.get("/api/ops/readiness?target_phase=small_live&min_rows=8&eod_limit=50")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["status"] == "danger"
    assert payload["data"]["target_phase"] == "small_live"
    assert payload["data"]["next_steps"][0] == "先清理待決策 Preview。"
    assert payload["data"]["decision_brief"]["state"] == "blocked"


def test_radar_phase1_acceptance_api_returns_user_test_gate(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    class _Repo:
        def __init__(self, session):  # noqa: ANN001
            self.session = session

    class _Phase1Service:
        def __init__(self, **kwargs):  # noqa: ANN003
            assert kwargs["min_rows"] == 8
            assert kwargs["min_entry_ready"] == 1

        def build(self, _repo):  # noqa: ANN001
            return {
                "phase": "decision_radar_phase1",
                "status": "ok",
                "ready_for_user_testing": True,
                "requires_user_confirmation": True,
                "action_grade": {"counts": {"可下手": 1, "只觀察": 2, "不碰": 3}},
                "blocking_reasons": [],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    monkeypatch.setattr("app.reports.decision_radar_phase1_acceptance.DecisionRadarPhase1AcceptanceService", _Phase1Service)
    with TestClient(app) as client:
        response = client.get("/api/radar/phase1-acceptance?min_rows=8&min_entry_ready=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["ready_for_user_testing"] is True
    assert payload["data"]["requires_user_confirmation"] is True
    assert payload["data"]["action_grade"]["counts"]["可下手"] == 1


def test_ops_readiness_api_includes_intraday_quote_service_health(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

    class _OpsReadinessService:
        def __init__(self, **kwargs):  # noqa: ANN003
            assert kwargs["min_rows"] == 5

        def build(self, _repo, *, target_phase="paper"):  # noqa: ANN001
            return {
                "status": "ok",
                "status_text": "正常",
                "target_phase": target_phase,
                "components": [{"key": "daily_radar", "label": "盤前每日雷達", "status": "ok", "value": "正常"}],
                "blocking_reasons": [],
                "warnings": [],
                "metrics": {"daily_radar_rows": 12, "pending_preview_count": 0, "target_readiness": "paper_ready"},
                "next_steps": ["營運總覽正常；可依今日雷達與人工 gate 繼續日常流程。"],
                "decision_brief": {
                    "state": "review_ready",
                    "title": "有 2 檔可進入人工檢查",
                    "summary": "原本 readiness 正常。",
                    "primary_action": {"key": "daily-radar", "label": "檢查可進場標的"},
                },
            }

    class _QuoteService:
        def status(self):
            return {
                "state": "running",
                "running": True,
                "symbols": ["2330", "2317"],
                "symbol_count": 2,
                "simulation": True,
                "include_bidask": True,
                "market_session": {"status": "open", "is_open": True, "checked_at": "2026-07-13T09:30:00"},
                "health": {
                    "status": "danger",
                    "label": "異常",
                    "summary": "盤中行情服務需要處理。",
                    "issues": [
                        {"code": "quote_disconnected", "severity": "danger", "message": "Shioaji 行情連線已斷線。"},
                        {"code": "no_quotes_received", "severity": "warn", "message": "尚未收到任何串流報價。"},
                    ],
                    "next_actions": ["停止並重新啟動行情服務；若持續斷線，檢查網路與 Shioaji 登入狀態。"],
                },
                "manager": {
                    "connection_status": "disconnected",
                    "subscription_count": 4,
                    "subscribed_symbols": ["2330", "2317"],
                    "buffer": {"latest_count": 0, "tick_count": 0, "bidask_count": 0},
                },
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    monkeypatch.setattr("app.reports.ops_readiness.OpsReadinessService", _OpsReadinessService)
    monkeypatch.setattr(control_panel_app, "INTRADAY_QUOTE_SERVICE", _QuoteService())
    monkeypatch.setattr(
        control_panel_app,
        "build_intraday_quote_service_review",
        lambda *args, **kwargs: {
            "status": "warn",
            "label": "盤後留意",
            "summary": "盤中行情服務可用但仍有提醒。",
            "issues": [{"code": "session_had_warn", "severity": "warn", "message": "本輪行情服務曾出現 warn。"}],
            "next_actions": ["隔日開盤前再檢查狀態檔 freshness。"],
            "evidence": {
                "heartbeat_count": 12,
                "max_quote_coverage": 0.5,
                "max_tick_count": 1,
                "max_bidask_count": 0,
                "snapshot_freshness": "fresh",
            },
        },
    )
    with TestClient(app) as client:
        response = client.get("/api/ops/readiness?target_phase=paper")

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]
    quote_component = next(item for item in data["components"] if item["key"] == "intraday_quote_service")
    assert data["status"] == "danger"
    assert data["status_text"] == "需處理"
    assert "intraday_quote_service" in data["blocking_reasons"]
    assert quote_component["status"] == "danger"
    assert quote_component["details"]["blocking_reasons"] == ["quote_disconnected"]
    assert quote_component["details"]["warnings"] == ["no_quotes_received"]
    assert data["metrics"]["intraday_quote_health"] == "danger"
    assert data["metrics"]["intraday_quote_symbol_count"] == 2
    assert data["metrics"]["intraday_quote_review_status"] == "warn"
    assert data["metrics"]["intraday_quote_review_coverage"] == 0.5
    assert data["decision_brief"]["state"] == "blocked"
    assert data["decision_brief"]["primary_action"]["key"] == "intraday_quote_service"
    assert "停止並重新啟動行情服務" in data["next_steps"][-1]
    assert data["intraday_quote_service"]["manager"]["connection_status"] == "disconnected"
    assert quote_component["details"]["post_session_review"]["label"] == "盤後留意"
    assert quote_component["details"]["post_session_review"]["evidence"]["heartbeat_count"] == 12
    assert "session_had_warn" in quote_component["details"]["post_session_review"]["issues"][0]["code"]


def test_radar_deployment_health_api_returns_monitor_summary(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_data_health_overview(self, *, as_of, freshness_days):  # noqa: ANN001
            assert freshness_days == 3
            return {
                "latest_radar": {
                    "run_id": "radar_ok",
                    "run_date": as_of.isoformat(),
                    "row_count": 6,
                    "entry_ready_count": 2,
                    "age_days": 0,
                },
                "sync_jobs": [
                    {
                        "job_name": "daily_radar",
                        "status": "ok",
                        "health_status": "ok",
                        "rows_upserted": 6,
                        "run_at": f"{as_of.isoformat()}T08:45:00",
                    }
                ],
            }

        def get_latest_daily_radar(self, count=50):  # noqa: ANN001
            return [{"run_id": "radar_ok", "run_date": "2026-06-25", "action_label": "ENTRY_READY"}]

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/deployment-health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["latest_run_id"] == "radar_ok"
    assert payload["data"]["entry_ready_diagnosis"]["reason"] == "entry_ready_available"
    assert payload["data"]["blocking_reasons"] == []


def test_radar_trigger_monitor_api_returns_watch_conditions(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_latest_daily_radar(self, count=50):  # noqa: ANN001
            assert count == 20
            return [
                {
                    "run_id": "radar_trigger",
                    "run_date": "2026-07-13",
                    "rank": 1,
                    "symbol": "2303",
                    "name": "聯電",
                    "action_label": "WATCH_WAIT_TRIGGER",
                    "price": 98.5,
                    "entry_score": 73.5,
                    "risk_score": 0.24,
                    "risk_adjusted_score": 76.5,
                    "data_quality": "fresh",
                    "atr_heat": "elevated",
                    "trigger_plan": {
                        "target_entry_score": 74.0,
                        "breakout_price": 100.0,
                        "pullback_price": 95.0,
                        "trigger_readiness_score": 68.0,
                        "trigger_readiness_label": "接近觸發",
                        "atr_heat": "elevated",
                    },
                }
            ]

        def get_daily_radar_allocation_plan(self, limit=12):  # noqa: ANN001
            assert limit == 12
            return {
                "allocation_diagnostics": {
                    "review_queue": {
                        "lanes": {
                            "trigger": {
                                "items": [
                                    {
                                        "symbol": "2303",
                                        "monitor_only": True,
                                        "allocation_lane": "monitor_only",
                                        "trigger_watchlist_note": "2303 聯電 | 盯盤備選 | Entry>=74 | 突破 100",
                                    }
                                ]
                            }
                        }
                    }
                }
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/trigger-monitor?count=20&limit=4&near_entry_gap=1&near_price_gap_pct=0.02")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["status"] == "near"
    assert payload["data"]["summary"]["near_count"] == 1
    assert payload["data"]["request"]["price_source"] == "radar_snapshot"
    assert payload["data"]["request"]["streaming_seconds"] == 3.0
    assert payload["data"]["request"]["max_price_age_seconds"] == 180
    assert payload["data"]["request"]["shioaji_live"] is False
    assert payload["data"]["quote_status"]["enabled"] is False
    assert payload["data"]["quote_status"]["source"] == "radar_snapshot"
    assert payload["data"]["quote_status"]["freshness_status"] == "radar_snapshot"
    assert payload["data"]["items"][0]["symbol"] == "2303"
    assert payload["data"]["items"][0]["allocation_status"] == "盯盤備選"
    assert payload["data"]["items"][0]["trigger_watchlist_note"].startswith("2303 聯電 | 盯盤備選")


def test_radar_trigger_monitor_api_supports_shioaji_streaming_price_source(monkeypatch):
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    captured: dict[str, object] = {}

    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_latest_daily_radar(self, count=50):  # noqa: ANN001
            assert count == 20
            return [
                {
                    "run_id": "radar_trigger",
                    "run_date": "2026-07-13",
                    "rank": 1,
                    "symbol": "2303",
                    "name": "聯電",
                    "action_label": "WATCH_WAIT_TRIGGER",
                    "price": 98.5,
                    "entry_score": 73.5,
                    "risk_score": 0.24,
                    "risk_adjusted_score": 76.5,
                    "data_quality": "fresh",
                    "atr_heat": "elevated",
                    "trigger_plan": {
                        "target_entry_score": 74.0,
                        "breakout_price": 100.0,
                        "pullback_price": 95.0,
                        "trigger_readiness_score": 68.0,
                        "trigger_readiness_label": "接近觸發",
                        "atr_heat": "elevated",
                    },
                }
            ]

        def get_daily_radar_allocation_plan(self, limit=12):  # noqa: ANN001
            assert limit == 12
            return {
                "allocation_diagnostics": {
                    "review_queue": {
                        "lanes": {
                            "trigger": {
                                "items": [
                                    {
                                        "symbol": "2303",
                                        "monitor_only": True,
                                        "allocation_lane": "monitor_only",
                                        "trigger_watchlist_note": "2303 聯電 | 盯盤備選 | Entry>=74 | 突破 100",
                                    }
                                ]
                            }
                        }
                    }
                }
            }

    def _provider_factory(*, price_source, shioaji_simulation, streaming_seconds):  # noqa: ANN001
        captured["price_source"] = price_source
        captured["shioaji_simulation"] = shioaji_simulation
        captured["streaming_seconds"] = streaming_seconds

        def _provider(symbols):  # noqa: ANN001
            captured["symbols"] = list(symbols)
            return {
                "2303": {
                    "price": 100.5,
                    "source": "shioaji_streaming_tick",
                    "checked_at": checked_at,
                    "quote_buffer_status": "streaming_tick",
                    "quote_buffer_tick_count": 2,
                    "quote_buffer_bidask_count": 1,
                    "quote_buffer_retained_tick_count": 2,
                    "quote_buffer_retained_bidask_count": 1,
                    "quote_buffer_event_count": 1,
                    "quote_buffer_last_event": {
                        "event_code": 16,
                        "info": "subscribe 2303",
                        "checked_at": checked_at,
                    },
                    "quote_buffer": {
                        "latest_count": 1,
                        "tick_count": 2,
                        "bidask_count": 1,
                        "retained_tick_count": 2,
                        "retained_bidask_count": 1,
                        "event_count": 1,
                    },
                }
            }

        return _provider

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    monkeypatch.setattr("app.scheduler.jobs.build_daily_radar_trigger_price_provider", _provider_factory)
    with TestClient(app) as client:
        response = client.get(
            "/api/radar/trigger-monitor"
            "?count=20&limit=4&near_entry_gap=1&near_price_gap_pct=0.02"
            "&price_source=shioaji_streaming&streaming_seconds=0.25"
            "&max_price_age_seconds=180&shioaji_live=true"
        )

    assert response.status_code == 200
    assert captured["price_source"] == "shioaji_streaming"
    assert captured["shioaji_simulation"] is False
    assert captured["streaming_seconds"] == 0.25
    assert captured["symbols"] == ["2303"]
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["request"]["price_source"] == "shioaji_streaming"
    assert payload["data"]["request"]["streaming_seconds"] == 0.25
    assert payload["data"]["request"]["max_price_age_seconds"] == 180
    assert payload["data"]["request"]["shioaji_live"] is True
    assert payload["data"]["quote_status"]["enabled"] is True
    assert payload["data"]["quote_status"]["source"] == "shioaji_streaming_tick"
    assert payload["data"]["quote_status"]["streaming_count"] == 1
    assert payload["data"]["quote_status"]["tick_count"] == 2
    assert payload["data"]["quote_status"]["bidask_count"] == 1
    assert payload["data"]["quote_status"]["last_event"]["event_code"] == 16
    assert payload["data"]["items"][0]["price_source"] == "shioaji_streaming_tick"
    assert payload["data"]["items"][0]["quote_buffer_status"] == "streaming_tick"


def test_intraday_quote_service_api_controls_process_local_service(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _QuoteService:
        def status(self):
            calls.append(("status", None))
            return {
                "state": "stopped",
                "running": False,
                "symbols": [],
                "symbol_count": 0,
                "simulation": True,
                "include_bidask": True,
                "market_session": {"status": "closed", "is_open": False},
                "health": {"status": "idle", "label": "未啟動", "issues": [], "next_actions": []},
                "manager": None,
            }

        def snapshot(self):
            calls.append(("snapshot", None))
            return {"status": self.status(), "items": {}}

        def start(self, symbols, *, simulation, include_bidask, allow_outside_session):  # noqa: ANN001
            calls.append(
                (
                    "start",
                    {
                        "symbols": list(symbols),
                        "simulation": simulation,
                        "include_bidask": include_bidask,
                        "allow_outside_session": allow_outside_session,
                    },
                )
            )
            return {
                "state": "running",
                "running": True,
                "symbols": list(symbols),
                "symbol_count": len(symbols),
                "simulation": simulation,
                "include_bidask": include_bidask,
                "market_session": {"status": "open", "is_open": True},
                "health": {"status": "ok", "label": "正常", "issues": [], "next_actions": []},
                "manager": {"connection_status": "connected", "subscription_count": len(symbols) * 2},
            }

        def stop(self):
            calls.append(("stop", None))
            return {
                "state": "stopped",
                "running": False,
                "symbols": ["2330"],
                "symbol_count": 1,
                "simulation": False,
                "include_bidask": True,
                "market_session": {"status": "open", "is_open": True},
                "health": {"status": "idle", "label": "未啟動", "issues": [], "next_actions": []},
                "manager": None,
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(control_panel_app, "INTRADAY_QUOTE_SERVICE", _QuoteService())
    with TestClient(app) as client:
        status_response = client.get("/api/quotes/intraday/status")
        start_response = client.post(
            "/api/quotes/intraday/start",
            json={
                "symbols_csv": "2330.TW, 2317 2454",
                "simulation": False,
                "include_bidask": True,
                "allow_outside_session": True,
            },
        )
        stop_response = client.post("/api/quotes/intraday/stop")

    assert status_response.status_code == 200
    assert status_response.json()["data"]["state"] == "stopped"
    assert status_response.json()["data"]["health"]["status"] == "idle"
    assert start_response.status_code == 200
    assert start_response.json()["data"]["state"] == "running"
    assert stop_response.status_code == 200
    assert stop_response.json()["data"]["state"] == "stopped"
    assert calls[1] == (
        "start",
        {
            "symbols": ["2330", "2317", "2454"],
            "simulation": False,
            "include_bidask": True,
            "allow_outside_session": True,
        },
    )
    assert calls[-1] == ("stop", None)


def test_radar_quality_summary_api_returns_candidate_pool_quality(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_quality_summary(self, count=50):  # noqa: ANN001
            assert count == 50
            return {
                "status": "ok",
                "run_id": "radar_quality",
                "run_date": "2026-06-26",
                "candidate_count": 4,
                "quality_score": 72,
                "verdict": "可研究，先分散",
                "risk_level": "watch",
                "metrics": {"entry_count": 2, "no_blocker_ratio": 0.75, "top_group": "半導體業"},
                "alerts": [{"code": "atr_hot", "label": "存在過熱波動"}],
                "top_picks": [{"symbol": "2330", "name": "台積電", "candidate_quality_score": 88.0}],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/quality-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["quality_score"] == 72
    assert payload["data"]["top_picks"][0]["symbol"] == "2330"


def test_radar_threshold_sweep_api_returns_robustness_summary(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_latest_daily_radar(self, count=50):  # noqa: ANN001
            assert count == 4
            return [
                {
                    "symbol": "2330",
                    "entry_score": 88.0,
                    "action_label": "ENTRY_READY",
                    "reason_tags": ["PROFILE=balanced"],
                    "blocker_tags": [],
                },
                {
                    "symbol": "2454",
                    "entry_score": 76.0,
                    "action_label": "WATCH_WAIT_TRIGGER",
                    "reason_tags": ["TREND_OK=True", "VOL_OK=True"],
                    "blocker_tags": [],
                },
                {
                    "symbol": "2881",
                    "entry_score": 61.0,
                    "action_label": "WATCH_WAIT_TRIGGER",
                    "reason_tags": ["TREND_OK=True"],
                    "blocker_tags": [],
                },
                {
                    "symbol": "2603",
                    "entry_score": 82.0,
                    "action_label": "ENTRY_READY",
                    "reason_tags": ["PROFILE=balanced"],
                    "blocker_tags": ["ATR_HOT"],
                },
            ]

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/threshold-sweep?count=4&base_profile=balanced")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    data = payload["data"]
    assert data["status"] == "ok"
    assert data["base_profile"] == "balanced"
    assert data["metrics"]["candidate_count"] == 4
    assert data["stable_symbols"] == ["2330", "2454"]
    assert data["stable_entry_symbols"] == ["2330"]
    assert data["fragile_symbols"] == ["2881"]


def test_radar_threshold_backtest_api_returns_historical_robustness(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_threshold_backtest(self, **kwargs):  # noqa: ANN001
            assert kwargs["lookback_runs"] == 6
            assert kwargs["lookahead_bars_list"] == (5, 10)
            assert kwargs["count"] == 40
            assert kwargs["base_profile"] == "balanced"
            return {
                "status": "ok",
                "verdict": "門檻有歷史支撐",
                "risk_level": "ok",
                "robustness_score": 78,
                "best_variant": {"profile": "balanced", "profile_label": "平衡", "entry_offset": 2.0},
                "baseline_variant": {"profile": "balanced", "profile_label": "平衡", "entry_offset": 0.0},
                "recommended_config": {
                    "rank": 1,
                    "pool_key": "all",
                    "pool_label": "全部候選",
                    "pool_type": "all",
                    "profile": "balanced",
                    "profile_label": "平衡",
                    "entry_offset": 2.0,
                    "score": 82,
                    "evaluated_count": 18,
                    "readiness": "preferred",
                    "run_params": {
                        "risk_profile": "balanced",
                        "threshold_offset": 2.0,
                        "universe": "research_ready",
                        "symbols_csv": "",
                        "target_count": 15,
                    },
                },
                "optimization_candidates": [
                    {
                        "rank": 1,
                        "pool_key": "all",
                        "pool_label": "全部候選",
                        "pool_type": "all",
                        "profile": "balanced",
                        "profile_label": "平衡",
                        "entry_offset": 2.0,
                        "score": 82,
                        "evaluated_count": 18,
                        "readiness": "preferred",
                        "run_params": {
                            "risk_profile": "balanced",
                            "threshold_offset": 2.0,
                            "universe": "research_ready",
                            "symbols_csv": "",
                            "target_count": 15,
                        },
                    }
                ],
                "optimization_plan": {
                    "status": "ok",
                    "run_count": 1,
                    "run_items": [
                        {
                            "rank": 1,
                            "label": "全部候選 / 平衡 / offset 2.0",
                            "request_payload": {
                                "risk_profile": "balanced",
                                "threshold_offset": 2.0,
                                "universe": "research_ready",
                                "target_count": 15,
                                "symbols_csv": "",
                            },
                            "cli_args": [".venv/bin/python", "-m", "app.cli", "run-daily-radar-job", "--threshold-offset", "2.0"],
                            "cli_command": ".venv/bin/python -m app.cli run-daily-radar-job --threshold-offset 2.0",
                        }
                    ],
                },
                "segments": {"market": [{"label": "上市", "score": 82}], "profile": [], "industry": []},
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/threshold-backtest?lookback_runs=6&horizons=5,10&count=40&base_profile=balanced")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["robustness_score"] == 78
    assert payload["data"]["best_variant"]["entry_offset"] == 2.0
    assert payload["data"]["recommended_config"]["pool_key"] == "all"
    assert payload["data"]["recommended_config"]["run_params"]["threshold_offset"] == 2.0
    assert payload["data"]["optimization_plan"]["run_items"][0]["request_payload"]["threshold_offset"] == 2.0
    assert payload["data"]["optimization_candidates"][0]["readiness"] == "preferred"
    assert payload["data"]["segments"]["market"][0]["label"] == "上市"


def test_radar_threshold_optimization_runs_api_builds_dry_run_batch(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_threshold_backtest(self, **kwargs):  # noqa: ANN001
            assert kwargs["lookback_runs"] == 6
            assert kwargs["lookahead_bars_list"] == (5, 10)
            assert kwargs["count"] == 40
            return {
                "status": "ok",
                "verdict": "門檻有歷史支撐",
                "robustness_score": 78,
                "recommended_config": {"pool_key": "all"},
                "optimization_plan": {
                    "status": "ok",
                    "candidate_count": 1,
                    "run_items": [
                        {
                            "rank": 1,
                            "label": "全部候選 / 平衡 / offset 2.0",
                            "pool_key": "all",
                            "readiness": "preferred",
                            "score": 82,
                            "request_payload": {
                                "risk_profile": "balanced",
                                "threshold_offset": 2.0,
                                "universe": "research_ready",
                                "target_count": 15,
                                "symbols_csv": "",
                            },
                        }
                    ],
                },
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.post(
            "/api/radar/threshold-optimization-runs",
            json={
                "execute": False,
                "max_runs": 3,
                "lookback_runs": 6,
                "horizons": "5,10",
                "count": 40,
                "grid_universes": "dynamic_balanced,tw0050",
                "grid_profiles": "balanced,conservative",
                "grid_offsets": "-2,0",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["status"] == "planned"
    assert payload["data"]["summary"]["planned_count"] == 3
    assert payload["data"]["summary"]["best_decision_score"] >= 82.0
    assert payload["data"]["grid_candidate_count"] > 0
    assert payload["data"]["selected_grid_count"] == 2
    assert payload["data"]["source_comparison"]["status"] == "ok"
    assert payload["data"]["source_comparison"]["leader_source"] in {"grid", "plan", "tie"}
    assert payload["data"]["recommended_run"]["decision_label"] == "待執行"
    assert payload["data"]["run_items"][0]["status"] == "planned"
    assert payload["data"]["run_items"][0]["decision_score"] == 82.0
    assert payload["data"]["run_items"][0]["request_payload"]["threshold_offset"] == 2.0
    assert payload["data"]["run_items"][1]["candidate_source"] == "grid"
    assert payload["data"]["run_items"][1]["request_payload"]["universe"] == "dynamic_balanced"


def test_radar_threshold_optimization_runs_api_can_select_grid_source(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_threshold_backtest(self, **kwargs):  # noqa: ANN001, ARG002
            return {
                "status": "ok",
                "verdict": "門檻有歷史支撐",
                "robustness_score": 78,
                "recommended_config": {"pool_key": "all"},
                "optimization_plan": {
                    "status": "ok",
                    "candidate_count": 1,
                    "run_items": [
                        {
                            "rank": 1,
                            "label": "全部候選 / 平衡 / offset 2.0",
                            "pool_key": "all",
                            "readiness": "preferred",
                            "score": 82,
                            "request_payload": {
                                "risk_profile": "balanced",
                                "threshold_offset": 2.0,
                                "universe": "research_ready",
                                "target_count": 15,
                                "symbols_csv": "",
                            },
                        }
                    ],
                },
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.post(
            "/api/radar/threshold-optimization-runs",
            json={
                "execute": False,
                "max_runs": 1,
                "grid_universes": "dynamic_balanced,tw0050",
                "grid_profiles": "balanced,conservative",
                "grid_offsets": "-2,0",
                "candidate_source": "grid",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["candidate_source"] == "grid"
    assert payload["data"]["selected_grid_count"] == 1
    assert payload["data"]["candidate_sources"] == {"grid": 1}
    assert payload["data"]["source_comparison"]["verdict"] == "grid_only"
    assert payload["data"]["run_items"][0]["candidate_source"] == "grid"
    assert payload["data"]["run_items"][0]["pool_key"].startswith("grid:")


def test_radar_threshold_optimization_runs_api_can_select_exploration_source(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_threshold_backtest(self, **kwargs):  # noqa: ANN001, ARG002
            return {
                "status": "ok",
                "verdict": "門檻有歷史支撐",
                "robustness_score": 78,
                "recommended_config": {"pool_key": "all"},
                "optimization_plan": {
                    "status": "ok",
                    "candidate_count": 1,
                    "run_items": [
                        {
                            "rank": 1,
                            "label": "全部候選 / 平衡 / offset 2.0",
                            "pool_key": "all",
                            "readiness": "preferred",
                            "score": 82,
                            "request_payload": {
                                "risk_profile": "balanced",
                                "threshold_offset": 2.0,
                                "universe": "research_ready",
                                "target_count": 15,
                                "symbols_csv": "",
                            },
                        }
                    ],
                },
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.post(
            "/api/radar/threshold-optimization-runs",
            json={
                "execute": False,
                "max_runs": 1,
                "grid_universes": "dynamic_balanced,tw0050",
                "grid_profiles": "balanced,conservative",
                "grid_offsets": "-2,0",
                "candidate_source": "exploration",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["candidate_source"] == "exploration"
    assert payload["data"]["selected_grid_count"] == 1
    assert payload["data"]["candidate_sources"] == {"grid": 1}
    assert payload["data"]["run_items"][0]["candidate_source"] == "grid"
    assert payload["data"]["run_items"][0]["request_payload"]["universe"] in {"dynamic_balanced", "tw0050"}
    assert payload["data"]["exploration_queue"]["candidate_source"] == "exploration"
    assert payload["data"]["walk_forward_plan"]["next_candidate_source"] == "exploration"
    assert payload["data"]["walk_forward_plan"]["execute_probe_command"]


def test_radar_threshold_adoptions_api_returns_tracking_summary(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_threshold_adoption_summary(self, **kwargs):  # noqa: ANN001
            assert kwargs["limit"] == 4
            assert kwargs["lookahead_bars_list"] == (5, 10)
            return {
                "status": "ok",
                "verdict": "已有採用追蹤",
                "tracked_count": 1,
                "matured_run_count": 1,
                "runs": [
                    {
                        "run_id": "radar_1",
                        "trade_date": "2026-07-13",
                        "adoption": {"pool_label": "全部候選", "threshold_offset": 2.0},
                        "horizon_summary": [{"lookahead_bars": 5, "matured_count": 2}],
                    }
                ],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/threshold-adoptions?limit=4&horizons=5,10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["tracked_count"] == 1
    assert payload["data"]["runs"][0]["adoption"]["pool_label"] == "全部候選"


def test_universe_preview_api_returns_pool_diagnostics(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            self.recent_liquid_by_industry_args = None

        def get_recent_liquid_symbols_by_industry(self, **kwargs):  # noqa: ANN003
            self.recent_liquid_by_industry_args = kwargs
            return [
                {"symbol": "1101", "industry": "水泥工業", "avg_turnover": 100.0},
                {"symbol": "2881", "industry": "金融保險業", "avg_turnover": 90.0},
            ]

        def count_symbols_with_recent_bars(self, *, symbols, min_date):  # noqa: ANN001
            assert symbols == ["1101", "2881"]
            assert min_date.isoformat() == "2026-07-07"
            return 1

    class _SyncService:
        def __init__(self, repo):  # noqa: ANN001
            self.repo = repo

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    monkeypatch.setattr("app.data.sync_service.SyncService", _SyncService)
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {"9999": "event risk"})
    with TestClient(app) as client:
        response = client.get("/api/universe/preview?universe=dynamic_balanced&trade_date=2026-07-10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["source"] == "dynamic_balanced"
    assert payload["data"]["symbol_count"] == 2
    assert payload["data"]["freshness"]["coverage_pct"] == 50.0
    assert payload["data"]["industry_counts"][0]["industry"] == "水泥工業"


def test_radar_decision_gate_api_blocks_research_when_core_data_coverage_is_low(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_data_health_overview(self, *args, **kwargs):  # noqa: ANN001
            return {
                "status": "danger",
                "universe": {"coverage": 0.31},
                "latest_radar": {
                    "run_id": "radar_low_coverage",
                    "run_date": "2026-07-08",
                    "row_count": 20,
                    "entry_ready_count": 0,
                    "age_days": 0,
                },
                "market_data_coverage": {
                    "status": "danger",
                    "datasets": [
                        {
                            "key": "daily_bars",
                            "label": "日線資料",
                            "status": "danger",
                            "coverage_pct": 39.0,
                            "missing_count": 1292,
                            "missing_symbols": ["1258", "1591"],
                        },
                        {
                            "key": "institutional_chip",
                            "label": "法人買賣超",
                            "status": "danger",
                            "coverage_pct": 38.0,
                            "missing_count": 1315,
                            "missing_symbols": ["2067", "2073"],
                        },
                    ],
                },
                "sync_jobs": [
                    {
                        "job_name": "daily_radar",
                        "status": "ok",
                        "health_status": "ok",
                        "rows_upserted": 20,
                        "run_at": "2026-07-08T08:45:00",
                    }
                ],
            }

        def get_latest_daily_radar(self, count=50):  # noqa: ANN001
            return [{"run_id": "radar_low_coverage", "run_date": "2026-07-08", "action_label": "WATCH_WAIT_TRIGGER"}]

        def get_daily_radar_quality_summary(self, count=50):  # noqa: ANN001
            return {
                "status": "ok",
                "risk_level": "warn",
                "quality_score": 55,
                "candidate_count": 20,
                "verdict": "先修資料再看",
                "metrics": {"entry_count": 0, "data_gap_candidate_count": 4},
                "data_readiness": {
                    "status": "danger",
                    "label": "候選命中資料缺口",
                    "affected_candidate_symbols": [{"symbol": "1258"}],
                },
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/decision-gate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    data = payload["data"]
    assert data["status"] == "danger"
    assert data["primary_action"]["key"] == "repair_market_data"
    assert data["metrics"]["core_coverage_pct"] == 38.0
    assert data["samples"]["daily_bars_missing_symbols"] == ["1258", "1591"]
    assert "先修資料" in data["title"]


def test_radar_trading_feedback_api_returns_candidate_feedback(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_trading_feedback_summary(self, count=50, limit=100):  # noqa: ANN001
            assert count == 20
            assert limit == 80
            return {
                "status": "ok",
                "run_id": "radar_feedback",
                "run_date": "2026-06-26",
                "candidate_count": 2,
                "verdict": "交易回饋需留意",
                "risk_level": "warn",
                "primary_action": "先清理 gate 阻擋。",
                "metrics": {"affected_symbol_count": 1, "execution_rejected_count": 1},
                "alerts": [{"code": "execution_rejected", "label": "候選標的曾被 gate 擋下"}],
                "symbols": [{"symbol": "2330", "name": "台積電", "risk_level": "warn"}],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/trading-feedback?count=20&limit=80")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["risk_level"] == "warn"
    assert payload["data"]["symbols"][0]["symbol"] == "2330"


def test_radar_changes_api_returns_cross_run_summary(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_change_summary(self, limit=8):  # noqa: ANN001
            return {
                "status": "ok",
                "latest_run_id": "radar_latest",
                "previous_run_id": "radar_prev",
                "new_count": 1,
                "recurring_count": 2,
                "dropped_count": 1,
                "new_items": [{"symbol": "2881", "rank": 2, "entry_score": 76.0}],
                "recurring_items": [{"symbol": "2330", "rank": 1, "previous_rank": 2, "entry_score_delta": 13.0}],
                "dropped_items": [{"symbol": "2317", "previous_rank": 3, "previous_entry_score": 50.0}],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/changes?limit=6")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["latest_run_id"] == "radar_latest"
    assert payload["data"]["new_items"][0]["symbol"] == "2881"
    assert payload["data"]["dropped_items"][0]["symbol"] == "2317"


def test_radar_stability_api_returns_recurring_summary(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_stability_summary(self, lookback_runs=5, limit=8):  # noqa: ANN001
            return {
                "status": "ok",
                "lookback_runs": lookback_runs,
                "available_runs": 3,
                "stable_count": 1,
                "stable_items": [
	                    {
	                        "symbol": "2330",
	                        "streak_count": 3,
	                        "seen_count": 3,
	                        "entry_score": 84.0,
	                        "stability_score": 92.4,
	                        "risk_quality_score": 78.5,
	                        "risk_quality_label": "核心候選",
	                        "risk_quality_trend": "improving",
	                    }
	                ],
	                "average_risk_quality_score": 78.5,
	                "improving_risk_quality_count": 1,
	                "weakening_risk_quality_count": 0,
	            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/stability?lookback_runs=3&limit=6")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["lookback_runs"] == 3
    assert payload["data"]["stable_items"][0]["symbol"] == "2330"
    assert payload["data"]["stable_items"][0]["streak_count"] == 3
    assert payload["data"]["stable_items"][0]["risk_quality_label"] == "核心候選"
    assert payload["data"]["improving_risk_quality_count"] == 1


def test_radar_research_priority_api_returns_ranked_summary(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_research_priority_summary(self, lookback_runs=5, limit=8):  # noqa: ANN001
            return {
                "status": "ok",
                "lookback_runs": lookback_runs,
                "candidate_count": 2,
                "trading_feedback_risk_count": 1,
                "next_action_counts": {"先清交易回饋": 1, "可建立 Preview": 1},
                "primary_next_action": "先清交易回饋",
                "top_industry_group": "半導體業",
                "top_industry_group_count": 1,
                "top_industry_group_share": 1.0,
                "priority_group_count": 1,
                "priority_concentration_level": "ok",
                "diversified_priority_count": 1,
                "diversified_priority_group_count": 1,
                "diversified_priority_items": [
                    {
                        "symbol": "2330",
                        "industry_group": "半導體業",
                        "diversified_priority_rank": 1,
                    }
                ],
                "priority_items": [
                    {
                        "symbol": "2330",
                        "priority_score": 82.4,
                        "priority_score_raw": 94.4,
                        "priority_score_components": {
                            "entry": 35.28,
                            "stability": 16.63,
                            "action": 12.0,
                            "change": 7.0,
                            "blocker": 8.0,
                            "reliability": 5.0,
                            "risk": -1.4,
                            "trading_feedback": -12.0,
                        },
                        "priority_score_component_summary": ["Entry +35.3", "穩定度 +16.6", "動作 +12.0", "回饋 -12.0"],
                        "priority_tier": "優先研究",
                        "priority_next_action": "先清交易回饋",
                        "reason_summary": ["Entry 84.0", "轉強"],
                        "trading_feedback_risk_level": "warn",
                        "trading_feedback_issue_count": 1,
                    }
                ],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/research-priority?lookback_runs=3&limit=6")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["lookback_runs"] == 3
    assert payload["data"]["next_action_counts"]["先清交易回饋"] == 1
    assert payload["data"]["primary_next_action"] == "先清交易回饋"
    assert payload["data"]["top_industry_group_share"] == 1.0
    assert payload["data"]["priority_concentration_level"] == "ok"
    assert payload["data"]["diversified_priority_items"][0]["symbol"] == "2330"
    assert payload["data"]["priority_items"][0]["symbol"] == "2330"
    assert payload["data"]["priority_items"][0]["priority_tier"] == "優先研究"
    assert payload["data"]["priority_items"][0]["priority_next_action"] == "先清交易回饋"
    assert payload["data"]["priority_items"][0]["trading_feedback_issue_count"] == 1
    assert payload["data"]["priority_items"][0]["priority_score_components"]["entry"] == 35.28
    assert "回饋 -12.0" in payload["data"]["priority_items"][0]["priority_score_component_summary"]


def test_radar_allocation_plan_api_returns_research_budget(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_allocation_plan(  # noqa: ANN001
            self,
            portfolio_equity=1_000_000.0,
            max_exposure_pct=0.35,
            max_single_pct=0.08,
            max_group_pct=0.18,
            lookback_runs=5,
            limit=6,
            calibration_profile="standard",
        ):
            return {
                "status": "ok",
                "portfolio_equity": portfolio_equity,
                "max_exposure_pct": max_exposure_pct,
                "max_single_pct": max_single_pct,
                "max_group_pct": max_group_pct,
                "lookback_runs": lookback_runs,
                "calibration_profile": {"key": calibration_profile},
                "research_budget": portfolio_equity * max_exposure_pct,
                "allocation_diagnostics": {
                    "risk_level": "watch",
                    "verdict": "可小額研究，先補檢查",
                    "alerts": [{"type": "low_utilization", "label": "研究額度使用偏低"}],
                    "review_queue": {
                        "lanes": {
                            "preview": {"count": 1, "items": [{"symbol": "2330"}]},
                            "trigger": {"count": 0, "items": []},
                            "risk": {"count": 0, "items": []},
                        },
                        "handoff": {
                            "primary_lane": "preview",
                            "primary_action": "先檢查可建立 Preview",
                            "next_session_note": "可先建立 Preview 1 檔：2330",
                            "top_symbols": ["2330"],
                            "total_review_count": 1,
                        },
                    },
                    "summary_metrics": {"budget_utilization": 0.16},
                },
                "allocation_items": [
                    {
                        "symbol": "2330",
                        "suggested_value": 80_000,
                        "suggested_odd_lot_shares": 133,
                        "next_step": "可建立 paper/Preview，仍需通過 promotion 與 live gate",
                    }
                ][:limit],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get(
            "/api/radar/allocation-plan?portfolio_equity=2000000&max_exposure_pct=0.25&max_single_pct=0.05&max_group_pct=0.12&lookback_runs=3&limit=4&calibration_profile=conservative"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["portfolio_equity"] == 2_000_000
    assert payload["data"]["max_exposure_pct"] == 0.25
    assert payload["data"]["calibration_profile"]["key"] == "conservative"
    assert payload["data"]["research_budget"] == 500_000
    assert payload["data"]["allocation_diagnostics"]["risk_level"] == "watch"
    assert payload["data"]["allocation_diagnostics"]["review_queue"]["lanes"]["preview"]["count"] == 1
    assert payload["data"]["allocation_diagnostics"]["review_queue"]["handoff"]["primary_action"] == "先檢查可建立 Preview"
    assert payload["data"]["allocation_items"][0]["symbol"] == "2330"


def test_radar_allocation_outcomes_api_returns_backtest_summary(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_allocation_outcome_summary(  # noqa: ANN001
            self,
            portfolio_equity=1_000_000.0,
            max_exposure_pct=0.35,
            max_single_pct=0.08,
            max_group_pct=0.18,
            lookahead_bars=5,
            max_runs=8,
            limit=6,
        ):
            return {
                "status": "ok",
                "portfolio_equity": portfolio_equity,
                "max_exposure_pct": max_exposure_pct,
                "max_single_pct": max_single_pct,
                "max_group_pct": max_group_pct,
                "lookahead_bars": lookahead_bars,
                "max_runs": max_runs,
                "research_budget": portfolio_equity * max_exposure_pct,
                "evaluated_run_count": 2,
                "allocation_average_return": 0.04,
                "baseline_average_return": 0.01,
                "average_alpha": 0.03,
                "verdict": "配置優於排名基準",
                "recent_runs": [{"run_id": "radar_1", "allocation_return": 0.05, "baseline_return": 0.01}][:limit],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get(
            "/api/radar/allocation-outcomes?portfolio_equity=2000000&max_exposure_pct=0.25&max_single_pct=0.05&max_group_pct=0.12&lookahead_bars=10&max_runs=4&limit=3"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["portfolio_equity"] == 2_000_000
    assert payload["data"]["lookahead_bars"] == 10
    assert payload["data"]["max_runs"] == 4
    assert payload["data"]["average_alpha"] == 0.03
    assert payload["data"]["verdict"] == "配置優於排名基準"


def test_radar_allocation_calibration_api_returns_multiplier_buckets(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_daily_radar_allocation_calibration(  # noqa: ANN001
            self,
            lookahead_bars=5,
            max_snapshots=300,
            min_evaluated=4,
            calibration_profile="standard",
        ):
            return {
                "status": "ok",
                "lookahead_bars": lookahead_bars,
                "max_snapshots": max_snapshots,
                "min_evaluated": min_evaluated,
                "profile": {"key": calibration_profile},
                "evaluated_count": 12,
                "baseline_positive_rate": 0.58,
                "baseline_average_return": 0.012,
                "top_positive": [{"group": "industry", "key": "半導體業", "multiplier": 1.08}],
                "top_negative": [{"group": "atr_heat", "key": "hot", "multiplier": 0.92}],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get(
            "/api/radar/allocation-calibration?lookahead_bars=10&max_snapshots=120&min_evaluated=6&calibration_profile=aggressive"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["lookahead_bars"] == 10
    assert payload["data"]["max_snapshots"] == 120
    assert payload["data"]["min_evaluated"] == 6
    assert payload["data"]["profile"]["key"] == "aggressive"
    assert payload["data"]["top_positive"][0]["multiplier"] == 1.08


def test_eod_summary_api_returns_after_close_status(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def list_recent_trading_execution_records(self, *, limit=20):  # noqa: ANN001
            return [
                {
                    "environment": "simulation",
                    "accepted": True,
                    "executed": True,
                    "status": "submitted",
                    "created_at": "2026-06-25T13:30:00",
                }
            ]

        def list_recent_order_preview_records(self, *, limit=20):  # noqa: ANN001
            return [{"status": "accepted", "created_at": "2026-06-25T13:20:00"}]

        def list_recent_promotion_gate_records(self, *, limit=20):  # noqa: ANN001
            return [{"accepted": True, "reason": "ok", "created_at": "2026-06-25T13:10:00"}]

        def list_recent_reconciliation_records(self, *, limit=20):  # noqa: ANN001
            return [{"matched": True, "blocking_reasons": [], "checked_at": "2026-06-25T15:10:00"}]

        def get_data_health_overview(self, *args, **kwargs):  # noqa: ANN001
            return {"status": "ok", "score": 100, "freshness_alerts": []}

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/tw-live/eod-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "components" in payload["data"]
    assert "metrics" in payload["data"]
    assert "reconciliation_runbook" in payload["data"]
    assert "reconcile --expected-cash" in payload["data"]["reconciliation_runbook"]["command_template"]


def test_radar_symbol_detail_api_returns_symbol_drilldown(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_symbol_decision_detail(self, symbol):  # noqa: ANN001
            return {
                "status": "ok",
                "symbol": symbol,
                "instrument": {"symbol": symbol, "name": "台積電", "industry": "半導體業"},
                "radar": {"symbol": symbol, "action_label": "ENTRY_READY", "entry_score": 82.5},
                "peer_radar": [],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/symbol/2330.TW")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["symbol"] == "2330"
    assert payload["data"]["instrument"]["name"] == "台積電"


def test_radar_outcomes_api_returns_forward_performance(monkeypatch):
    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class _Repo:
        def __init__(self, _session):
            pass

        def get_latest_radar_outcomes(self, lookahead_bars=5, count=20):  # noqa: ANN001, ARG002
            return {
                "status": "ok",
                "lookahead_bars": lookahead_bars,
                "positive_rate": 0.5,
                "by_industry": [{"key": "半導體業", "positive_rate": 0.5}],
                "by_reason": [{"key": "TREND_OK=true", "positive_rate": 1.0}],
                "by_atr_heat": [{"key": "normal", "positive_rate": 1.0}],
                "rows": [{"symbol": "2330", "lookahead_return": 0.03}],
            }

    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    with TestClient(app) as client:
        response = client.get("/api/radar/outcomes?lookahead_bars=3&count=12")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["lookahead_bars"] == 3
    assert payload["data"]["rows"][0]["symbol"] == "2330"


def test_signal_catalog_api_returns_registry(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        response = client.get("/api/signals/catalog?category=technical")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    signal_ids = {item["signal_id"] for item in payload["data"]["signals"]}
    assert "trend_ok" in signal_ids
    assert "trigger_reason" in signal_ids
    assert "technical" in payload["data"]["categories"]
    assert "blocker" in payload["data"]["categories"]


def test_control_panel_token_protects_reports(monkeypatch, report_file):
    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        blocked_static = client.get(f"/reports/{report_file.name}")
        blocked_list = client.get("/api/reports")
        allowed_static = client.get(f"/reports/{report_file.name}", headers={"X-Control-Panel-Token": "secret-token"})
        allowed_list = client.get("/api/reports", headers={"X-Control-Panel-Token": "secret-token"})

    assert blocked_static.status_code == 401
    assert blocked_list.status_code == 401
    assert allowed_static.status_code == 200
    assert allowed_static.text == "report-ok"
    assert allowed_list.status_code == 200
    assert any(item["name"] == report_file.name for item in allowed_list.json()["reports"])


def test_control_panel_allows_local_reports_without_token(monkeypatch, report_file):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        response = client.get(f"/reports/{report_file.name}")

    assert response.status_code == 200
    assert response.text == "report-ok"


def test_control_panel_rejects_external_bind_without_token(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "0.0.0.0")
    with TestClient(app) as client:
        response = client.get("/api/ping")

    assert response.status_code == 403
    assert response.json()["error"] == "control panel requires CONTROL_PANEL_TOKEN outside localhost"


def test_control_panel_echo_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_DEBUG", raising=False)
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        response = client.post("/api/echo", json={"token": "secret-token-value"})

    assert response.status_code == 404
    assert response.json()["error"] == "debug endpoint disabled"


def test_tw_live_api_is_token_protected(monkeypatch):
    class _HealthResult:
        def to_dict(self):
            return {"status": "ok"}

    class _AccountService:
        def run_health_check(self, simulation=True):  # noqa: ARG002
            return _HealthResult()

    monkeypatch.setenv("CONTROL_PANEL_TOKEN", "secret-token")
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(control_panel_app, "SHIOAJI_ACCOUNT_SERVICE", _AccountService())
    with TestClient(app) as client:
        blocked = client.get("/api/tw-live/health?simulation=true")
        blocked_audit = client.get("/api/tw-live/audit")
        allowed = client.get("/api/tw-live/health?simulation=true", headers={"X-Control-Panel-Token": "secret-token"})

    assert blocked.status_code == 401
    assert blocked_audit.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "ok"


def test_run_0050_sync_endpoint_starts_background_action(monkeypatch):
    calls = []

    def fake_start_action(action, target, req, busy_error, accepted_message):  # noqa: ANN001
        calls.append(
            {
                "action": action,
                "target": target,
                "req": req,
                "busy_error": busy_error,
                "accepted_message": accepted_message,
            }
        )
        return JSONResponse({"status": "ok"})

    monkeypatch.setattr(control_panel_app, "_start_action", fake_start_action)

    response = control_panel_app.run_0050_sync(Tw0050SyncRequest())

    assert response.status_code == 200
    assert calls[0]["action"] == "0050-sync"
    assert calls[0]["target"] is control_panel_app._run_0050_sync
    assert calls[0]["busy_error"] == "台灣 50 同步正在執行中"
    assert calls[0]["accepted_message"] == "台灣 50 同步開始執行"


def test_run_market_data_repair_endpoint_starts_background_action(monkeypatch):
    calls = []

    def fake_start_action(action, target, req, busy_error, accepted_message):  # noqa: ANN001
        calls.append(
            {
                "action": action,
                "target": target,
                "req": req,
                "busy_error": busy_error,
                "accepted_message": accepted_message,
            }
        )
        return JSONResponse({"status": "ok"})

    monkeypatch.setattr(control_panel_app, "_start_action", fake_start_action)

    response = control_panel_app.run_market_data_repair(
        control_panel_app.MarketDataRepairRequest(
            datasets="daily_bars",
            markets="TPEX",
            symbols="3105,6488",
            max_symbols=8,
        )
    )

    assert response.status_code == 200
    assert calls[0]["action"] == "market-data-repair"
    assert calls[0]["target"] is control_panel_app._run_market_data_repair
    assert calls[0]["req"].datasets == "daily_bars"
    assert calls[0]["req"].markets == "TPEX"
    assert calls[0]["req"].symbols == "3105,6488"
    assert calls[0]["req"].max_symbols == 8
    assert calls[0]["busy_error"] == "市場資料覆蓋修復正在執行中"
    assert calls[0]["accepted_message"] == "市場資料覆蓋修復開始執行"


def test_market_data_repair_uses_official_bulk_sync(monkeypatch):
    sync_calls = []
    updates = []

    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def commit(self):
            return None

    class _Repo:
        def __init__(self, _session):
            pass

        def get_data_health_overview(self, *args, **kwargs):  # noqa: ANN001
            return {
                "status": "danger",
                "score": 0,
                "market_data_coverage": {
                    "datasets": [
                        {
                            "key": "daily_bars",
                            "status": "danger",
                            "missing_symbols": ["3105"],
                        }
                    ]
                },
            }

    class _SyncService:
        def __init__(self, repo):  # noqa: ANN001
            self.repo = repo

        def sync_market_bundle(self, **kwargs):  # noqa: ANN001
            sync_calls.append(kwargs)
            return {"rows_upserted_total": 3, "partial_failure": False}

    def fake_plan(*args, **kwargs):  # noqa: ANN001
        return {
            "status": "ready",
            "start_date": "2026-07-07",
            "end_date": "2026-07-08",
            "symbols": ["3105"],
            "include_bars": True,
            "include_institutional": True,
            "include_broker_agg": False,
            "selected_datasets": ["daily_bars", "institutional_chip"],
        }

    monkeypatch.setattr(control_panel_app, "_update", lambda *args, **kwargs: updates.append((args, kwargs)))
    monkeypatch.setattr("app.db.session.init_db", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _SessionFactory())
    monkeypatch.setattr("app.db.repository.TradingRepository", _Repo)
    monkeypatch.setattr("app.data.sync_service.SyncService", _SyncService)
    monkeypatch.setattr("app.cli.build_market_data_repair_plan", fake_plan)

    control_panel_app._run_market_data_repair(
        control_panel_app.MarketDataRepairRequest(
            as_of="2026-07-08",
            datasets="daily_bars,institutional_chip",
            max_symbols=1,
            execute=True,
        )
    )

    assert sync_calls
    assert sync_calls[0]["prefer_official_bulk"] is True
    assert sync_calls[0]["symbols"] == ["3105"]
    assert sync_calls[0]["include_bars"] is True
    assert sync_calls[0]["include_institutional"] is True
    assert any(kwargs.get("state") == "done" for _args, kwargs in updates)


def test_tw_live_order_preview_endpoint(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        response = client.post(
            "/api/tw-live/order-preview",
            json={
                "symbol": "2330",
                "side": "buy",
                "price": 100,
                "quantity": 1,
                "strategy_name": "daily_radar",
                "strategy_version": "2026-06-28",
                "signal_id": "daily_radar:2330:ENTRY_READY",
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["symbol"] == "2330"
    assert data["strategy_name"] == "daily_radar"
    assert data["strategy_version"] == "2026-06-28"
    assert data["signal_id"] == "daily_radar:2330:ENTRY_READY"
    assert data["preview_id"]


def test_tw_live_order_preview_common_lot_uses_share_quantity(monkeypatch):
    monkeypatch.delenv("CONTROL_PANEL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    with TestClient(app) as client:
        response = client.post(
            "/api/tw-live/order-preview",
            json={
                "symbol": "2330",
                "side": "buy",
                "price": 100,
                "quantity": 1,
                "order_lot": "Common",
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["quantity"] == 1
    assert data["order_lot"] == "Common"
    assert data["estimated_total_cost"] == 100_000.0
