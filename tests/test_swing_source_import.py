"""管理者來源包的離線驗證與整批不可變匯入。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.data.swing_source_import import InvalidSourceBundle, import_source_bundle, preview_source_bundle
from app.db.swing_repository import SwingRepository, content_hash


def bundle(key="official-test"):
    raw = {"date": "2026-07-27", "symbol": "2330", "open": "100", "high": "102", "low": "99", "close": "101", "volume": 2000}
    return {"schema_version": 1, "bundle_key": key, "sources": [{
        "kind": "market_bar", "source_key": "2330:2026-07-27:close", "symbol": "2330", "data_period": "2026-07-27",
        "url": "https://www.twse.com.tw/official-data", "raw": raw,
        "public_at": "2026-07-27T13:30:00+08:00", "obtained_at": "2026-07-27T14:00:00+08:00",
        "verified_at": "2026-07-27T14:05:00+08:00", "quality": "verified",
        "attestation": {"confirmed": True, "statement": "我已核對官方原始資料、數值及各時間欄位。"},
        "payload": {"schema_version": 1, "phase": "close", "open": "100", "high": "102", "low": "99", "close": "101", "volume": 2000,
                    "available_at": "2026-07-27T13:30:00+08:00", "session_open_at": "2026-07-27T09:00:00+08:00"},
    }]}


@pytest.fixture
def session():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def constraints(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=ON")

    with engine.connect() as connection:
        connection.connection.driver_connection.executescript((Path(__file__).parents[1] / ".spec/tw-swing-ai-validation/deploy.sql").read_text())
    with Session(engine) as db:
        yield db
    engine.dispose()


def apply(session, value):
    return import_source_bundle(session, value, expected_hash=content_hash(value), actor="來源管理者", note="已人工核驗此來源包")


def test_preview_is_read_only_and_import_preserves_audit(session):
    value = bundle()
    result = preview_source_bundle(value)
    assert result["valid"] and result["qualified_source_count"] == 1
    assert session.execute(text("SELECT COUNT(*) FROM swing_source_versions")).scalar_one() == 0
    imported = apply(session, value)
    session.commit()
    repeated = apply(session, value)
    assert repeated["replayed"] is True and repeated["source_ids"] == imported["source_ids"]
    row = session.execute(text("SELECT payload_json FROM swing_source_versions")).scalar_one()
    audit = json.loads(row)["_import"]
    assert audit["raw"] == value["sources"][0]["raw"]
    assert audit["raw_hash"] == content_hash(audit["raw"])
    assert audit["bundle_hash"] == content_hash(value)
    assert audit["actor"] == "來源管理者" and audit["attestation"]["confirmed"] is True


def test_unknown_publication_stays_ineligible(session):
    value = bundle()
    value["sources"][0].update(public_at=None, quality="unknown_publication")
    assert preview_source_bundle(value)["unknown_publication_count"] == 1
    apply(session, value)
    assert SwingRepository(session).eligible_sources("market_bar", "2330", "2026-07-27T15:00:00+08:00") == []


@pytest.mark.parametrize("url", ["http://www.twse.com.tw/data", "https://127.0.0.1/data", "https://www.twse.com.tw.evil.test/data", "https://www.twse.com.tw@127.0.0.1/data", "https://www.twse.com.tw:8080/data", "https://192.168.1.1/"])
def test_official_url_only(url):
    value = bundle()
    value["sources"][0]["url"] = url
    result = preview_source_bundle(value)
    assert not result["valid"] and "sources[0].url" in result["missing_fields"]


def test_no_attestation_or_no_publication_cannot_be_verified(session):
    value = bundle()
    del value["sources"][0]["attestation"]
    with pytest.raises(InvalidSourceBundle, match="attestation"):
        apply(session, value)
    value = bundle()
    value["sources"][0]["public_at"] = None
    with pytest.raises(InvalidSourceBundle, match="quality_requires_unknown_publication"):
        apply(session, value)
    assert session.execute(text("SELECT COUNT(*) FROM swing_source_versions")).scalar_one() == 0


def test_bundle_hash_and_key_content_conflicts(session):
    value = bundle()
    with pytest.raises(ValueError, match="bundle_hash_mismatch"):
        import_source_bundle(session, value, expected_hash="0" * 64, actor="管理者", note="確認")
    with pytest.raises(InvalidSourceBundle, match="actor"):
        import_source_bundle(session, value, expected_hash=content_hash(value), actor="", note="確認")
    apply(session, value)
    session.commit()
    changed = deepcopy(value)
    changed["sources"][0]["payload"]["volume"] = 1000
    with pytest.raises(ValueError, match="bundle_key_content_conflict"):
        apply(session, changed)


def test_failed_batch_rolls_back_with_existing_caller_transaction(session):
    apply(session, bundle("first"))
    session.commit()
    # 第一筆合法新來源先寫入，第二筆碰到已存在 source_key，整批仍須回滾。
    value = bundle("second")
    new = deepcopy(value["sources"][0])
    new["source_key"] = "new-first"
    new["symbol"] = "2317"
    new["raw"]["symbol"] = "2317"
    value["sources"].insert(0, new)
    session.execute(text("SELECT 1"))
    with pytest.raises(ValueError, match="source_key_content_conflict"):
        apply(session, value)
    session.commit()
    assert session.execute(text("SELECT COUNT(*) FROM swing_source_versions")).scalar_one() == 1


def test_new_source_key_cannot_bypass_market_revision_chain(session):
    original = bundle()
    apply(session, original)
    session.commit()
    revision = bundle("renamed-revision")
    revision["sources"][0]["source_key"] = "new-key-same-market-day"
    revision["sources"][0]["payload"]["volume"] = 1500
    with pytest.raises(ValueError, match="market_revision_requires_repair_preview_and_repair_apply"):
        apply(session, revision)
    assert session.execute(text("SELECT COUNT(*) FROM swing_source_versions")).scalar_one() == 1
    duplicate = bundle("same-bundle-duplicate")
    renamed = deepcopy(duplicate["sources"][0])
    renamed["source_key"] = "different-key-same-market"
    duplicate["sources"].append(renamed)
    result = preview_source_bundle(duplicate)
    assert not result["valid"]
    assert "sources.duplicate_market_identity_use_repair_preview_and_repair_apply" in result["missing_fields"]


def test_calendar_completeness_requires_scoped_original_evidence():
    value = bundle()
    source = value["sources"][0]
    source.update(kind="calendar", source_key="calendar-test", symbol=None)
    payload = {"schema_version": 1, "sessions": ["2026-07-27", "2026-07-28"], "symbols": ["0050", "2330"],
               "period_start": "2026-07-27", "period_end": "2026-07-28", "corporate_actions_complete": True}
    source["payload"] = payload
    assert not preview_source_bundle(value)["valid"]
    original = {"official_search": "已確認這兩個標的在指定期間的公司行動結果"}
    payload["corporate_action_evidence"] = [{"url": "https://mops.twse.com.tw/official-search", "raw": original,
        "raw_hash": content_hash(original), "symbols": payload["symbols"], "period_start": payload["period_start"], "period_end": payload["period_end"]}]
    assert preview_source_bundle(value)["valid"]
    payload["sessions"].reverse()
    assert not preview_source_bundle(value)["valid"]


def test_open_payload_cannot_contain_future_full_day_values():
    value = bundle()
    value["sources"][0]["payload"]["phase"] = "open"
    assert "sources[0].payload.open_contains_future_data" in preview_source_bundle(value)["missing_fields"]


def test_calendar_and_company_action_import_uses_existing_repository(session):
    value = bundle()
    calendar = deepcopy(value["sources"][0])
    calendar.update(kind="calendar", symbol=None, source_key="calendar:2026-07")
    original = {"official_result": "2330 在指定期間的公司行動明細"}
    calendar["payload"] = {"schema_version": 1, "sessions": ["2026-07-27", "2026-07-28"], "symbols": ["2330"],
        "period_start": "2026-07-27", "period_end": "2026-07-28", "corporate_actions_complete": True,
        "corporate_action_evidence": [{"url": "https://mops.twse.com.tw/official", "raw": original, "raw_hash": content_hash(original),
            "symbols": ["2330"], "period_start": "2026-07-27", "period_end": "2026-07-28"}]}
    action = deepcopy(value["sources"][0])
    action.update(kind="corporate_action", source_key="2330:cash:2026-07-27")
    action["payload"] = {"schema_version": 1, "type": "cash_dividend", "ex_date": "2026-07-27", "payment_date": "2026-08-10", "cash_per_share": "1.5"}
    value["sources"].extend([calendar, action])
    imported = apply(session, value)
    assert len(imported["source_ids"]) == 3
    repo = SwingRepository(session)
    assert repo.eligible_sources("calendar", None, "2026-07-27T15:00:00+08:00")[0]["payload"]["symbols"] == ["2330"]
    assert repo.eligible_sources("corporate_action", "2330", "2026-07-27T15:00:00+08:00")[0]["payload"]["cash_per_share"] == "1.5"


def test_cli_requires_human_bundle_hash():
    from app.cli import build_parser
    parser = build_parser()
    preview = parser.parse_args(["swing-research", "source-preview", "--bundle", "official.json"])
    assert preview.swing_action == "source-preview"
    with pytest.raises(SystemExit):
        parser.parse_args(["swing-research", "source-import", "--bundle", "official.json"])
