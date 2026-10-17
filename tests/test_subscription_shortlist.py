from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Base, DailyRadarSnapshot, NewsEvent, ResearchEvidence, ShortlistItem
from app.db.repository import TradingRepository
from app.pipeline.shortlist import ShortlistService
from app.services.subscription_research import validate_research_item


class FakeResearcher:
    def analyze_batch(self, candidates, cutoff_at):  # noqa: ANN001
        return {
            "status": "normal",
            "items": [
                {
                    "symbol": item["symbol"],
                    "name": item["name"],
                    "event_type": "營運",
                    "sentiment": "positive",
                    "relevance": 0.9,
                    "confidence": 0.9,
                    "impact_horizon": "1～3 個月",
                    "risk_tags": [],
                    "summary": "公司近期營運訊號穩定，量價與基本面資料同步改善；目前引用來源未顯示重大治理或財務風險，適合列入未來一至三個月的持續研究名單。",
                    "sources": [
                        {
                            "title": "公司營運新聞",
                            "url": f"https://example.com/{item['symbol']}",
                            "published_at": cutoff_at.date().isoformat(),
                        }
                    ],
                    "exclude_recommended": False,
                }
                for item in candidates
            ],
        }


def _repo() -> tuple[Session, TradingRepository]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = Session(engine)
    return session, TradingRepository(session)


def test_shortlist_pipeline_persists_ai_evidence_and_final_top_ten():
    session, repo = _repo()
    try:
        for index in range(12):
            symbol = f"{2300 + index}"
            session.add(
                DailyRadarSnapshot(
                    run_id="radar_test",
                    run_date=date(2026, 7, 29),
                    symbol=symbol,
                    name=f"股票 {index}",
                    rank=index + 1,
                    price=100 + index,
                    entry_score=90 - index,
                    risk_adjusted_score=88 - index,
                    reason_tags_json='["趨勢向上"]',
                    blocker_tags_json="[]",
                )
            )
            session.add(
                NewsEvent(
                    symbol=symbol,
                    news_date=date(2026, 7, 29),
                    title=f"{symbol} 營運更新",
                    source_name="測試來源",
                    url=f"https://example.com/news/{symbol}",
                )
            )
        session.commit()

        service = ShortlistService(repo, researcher=FakeResearcher())
        prepared = service.prepare(radar_run_id="radar_test", cutoff_at=datetime(2026, 7, 29, 15, 0))
        enriched = service.enrich(prepared["run_id"])
        final = service.finalize(prepared["run_id"])
        session.commit()

        assert enriched["accepted"] == 12
        assert final["status"] == "completed"
        assert len(final["items"]) == 10
        assert final["items"][0]["sources"][0]["url"].startswith("https://example.com/")
        assert session.query(ResearchEvidence).filter_by(status="accepted").count() == 12
    finally:
        session.close()


def test_research_validation_rejects_future_or_missing_sources():
    clean, errors = validate_research_item(
        {
            "symbol": "2330",
            "name": "台積電",
            "event_type": "法說",
            "sentiment": "neutral",
            "relevance": 0.8,
            "confidence": 0.8,
            "impact_horizon": "1～3 個月",
            "risk_tags": [],
            "summary": "這是一段足夠長度的繁體中文研究摘要，用來確認來源截止時間與引用驗證會正確拒絕未來資訊，避免回測時產生資料穿越問題。",
            "sources": [
                {
                    "title": "未來新聞",
                    "url": "https://example.com/future",
                    "published_at": "2026-07-30T09:00:00",
                }
            ],
            "exclude_recommended": False,
        },
        candidate_symbols={"2330"},
        cutoff_at=datetime(2026, 7, 29, 15, 0),
    )

    assert clean["sources"] == []
    assert "source_after_cutoff" in errors
    assert "missing_valid_sources" in errors


def test_premarket_research_is_an_overlay_and_does_not_rewrite_frozen_item():
    session, repo = _repo()
    try:
        for index in range(10):
            session.add(
                DailyRadarSnapshot(
                    run_id="radar_premarket",
                    run_date=date(2026, 7, 29),
                    symbol=f"{2400 + index}",
                    name=f"股票 {index}",
                    rank=index + 1,
                    price=80 + index,
                    entry_score=85 - index,
                    risk_adjusted_score=82 - index,
                    reason_tags_json='["量價同步"]',
                    blocker_tags_json="[]",
                )
            )
        session.commit()

        service = ShortlistService(repo, researcher=FakeResearcher())
        prepared = service.prepare(
            radar_run_id="radar_premarket",
            cutoff_at=datetime(2026, 7, 29, 15, 20),
        )
        service.enrich(prepared["run_id"])
        service.finalize(prepared["run_id"])
        frozen = session.query(ShortlistItem).filter_by(run_id=prepared["run_id"]).first()
        original_evidence_id = frozen.evidence_id
        original_summary = frozen.ai_summary

        result = service.refresh_premarket(
            prepared["run_id"],
            cutoff_at=datetime(2026, 7, 30, 7, 30),
        )
        session.flush()
        session.refresh(frozen)
        payload = service.get_run(prepared["run_id"], limit=1)["items"][0]

        assert result["accepted"] == 10
        assert frozen.evidence_id == original_evidence_id
        assert frozen.ai_summary == original_summary
        assert payload["ai_status"] == "premarket"
        assert payload["ai_scope"] == "premarket_20260730"
        assert (
            session.query(ResearchEvidence)
            .filter(ResearchEvidence.research_scope == "premarket_20260730")
            .count()
            == 10
        )
    finally:
        session.close()
