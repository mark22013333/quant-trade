from __future__ import annotations

import json
from datetime import date

from app.pipeline.daily_radar import DailyRadarService
from app.pipeline.invest_pipeline import InvestPipelineService
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
                "entry_score": 0.8,
                "ca_path": "/Users/cheng/certs/my.pfx",
                "reason_tags": ["trend"],
                "blocker_tags": [],
            }
        ],
    )

    json_text = (tmp_path / export["files"]["json"]).read_text(encoding="utf-8")
    csv_text = (tmp_path / export["files"]["csv"]).read_text(encoding="utf-8")

    assert "/Users/cheng/certs/my.pfx" not in json_text
    assert "'@FORMULA" in csv_text
