from __future__ import annotations

from datetime import date

from app.reports.daily_radar_decision_gate import DailyRadarDecisionGateService


def _low_market_coverage_payload() -> dict:
    return {
        "status": "danger",
        "universe": {"coverage": 0.461},
        "market_data_coverage": {
            "status": "warn",
            "datasets": [
                {
                    "key": "daily_bars",
                    "label": "日線資料",
                    "status": "warn",
                    "coverage_pct": 58.7,
                    "missing_count": 874,
                    "missing_symbols": ["1258", "1591"],
                },
                {
                    "key": "institutional_chip",
                    "label": "法人買賣超",
                    "status": "warn",
                    "coverage_pct": 56.4,
                    "missing_count": 922,
                    "missing_symbols": ["2067", "2073"],
                },
            ],
        },
    }


def test_decision_gate_downgrades_market_gap_when_latest_radar_scope_is_clean() -> None:
    payload = DailyRadarDecisionGateService(freshness_days=1).evaluate_payloads(
        data_health=_low_market_coverage_payload(),
        radar_health={
            "status": "warn",
            "latest_run_date": "2026-07-08",
            "row_count": 20,
            "entry_ready_count": 0,
            "latest_sync_job": {
                "job_name": "daily_radar",
                "status": "ok",
                "health_status": "ok",
                "rows_upserted": 20,
                "run_at": "2026-07-09T00:33:10",
            },
            "sync_issues": [],
        },
        quality={
            "status": "ok",
            "risk_level": "warn",
            "quality_score": 48,
            "candidate_count": 20,
            "verdict": "偏觀察",
            "metrics": {"entry_count": 0, "data_gap_candidate_count": 0},
            "data_readiness": {"status": "warn", "affected_candidate_symbols": []},
        },
        as_of=date(2026, 7, 9),
    )

    assert payload["status"] == "warn"
    assert payload["title"] == "今天偏觀察，不急著進場"
    assert payload["primary_action"]["key"] == "inspect_quality"
    assert payload["metrics"]["candidate_scope_ready"] is True
    assert payload["metrics"]["data_coverage_warn_only"] is True
    assert payload["samples"]["data_gap_candidate_symbols"] == []
    assert any(item["key"] == "repair_market_data" and item["priority"] == "secondary" for item in payload["actions"])
    assert "本輪候選未命中資料缺口" in payload["evidence"][0]["detail"]


def test_decision_gate_still_blocks_when_candidates_hit_data_gaps() -> None:
    payload = DailyRadarDecisionGateService(freshness_days=1).evaluate_payloads(
        data_health=_low_market_coverage_payload(),
        radar_health={
            "status": "warn",
            "latest_run_date": "2026-07-08",
            "row_count": 20,
            "entry_ready_count": 0,
            "latest_sync_job": {
                "job_name": "daily_radar",
                "status": "ok",
                "health_status": "ok",
                "rows_upserted": 20,
                "run_at": "2026-07-09T00:33:10",
            },
            "sync_issues": [],
        },
        quality={
            "status": "ok",
            "risk_level": "warn",
            "quality_score": 55,
            "candidate_count": 20,
            "verdict": "先修資料再看",
            "metrics": {"entry_count": 0, "data_gap_candidate_count": 1},
            "data_readiness": {
                "status": "danger",
                "affected_candidate_symbols": [{"symbol": "1258"}],
            },
        },
        as_of=date(2026, 7, 9),
    )

    assert payload["status"] == "danger"
    assert payload["primary_action"]["key"] == "repair_market_data"
    assert payload["metrics"]["candidate_scope_ready"] is False
    assert payload["metrics"]["data_coverage_warn_only"] is False
    assert payload["samples"]["data_gap_candidate_symbols"] == ["1258"]
