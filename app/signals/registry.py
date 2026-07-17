from __future__ import annotations

from app.signals.base import SignalDefinition, SignalRegistry


_DEFINITIONS = (
    SignalDefinition(
        signal_id="profile",
        label="風險檔位",
        category="configuration",
        description="每日雷達使用的風險檔位，例如 balanced 或 aggressive。",
        tag_prefix="PROFILE=",
        value_type="enum",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="industry_group",
        label="正式產業族群",
        category="market_context",
        description="標的所屬產業，用於族群分散與同族群比較。",
        tag_prefix="INDUSTRY_GROUP=",
        value_type="string",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="industry_rotation_score",
        label="族群輪動分數",
        category="market_context",
        description="結合標的分數與族群趨勢後的排序輔助分數。",
        tag_prefix="INDUSTRY_ROTATION_SCORE=",
        value_type="number_pct",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="industry_trend",
        label="族群趨勢",
        category="market_context",
        description="族群背景順風、逆風或混合，用於判斷標的是否站在較好的市場背景。",
        tag_prefix="INDUSTRY_TREND=",
        value_type="enum",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="industry_trend_score",
        label="族群趨勢分數",
        category="market_context",
        description="族群短中期報酬與上漲家數比例形成的分數。",
        tag_prefix="INDUSTRY_TREND_SCORE=",
        value_type="number_pct",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="industry_short_return",
        label="族群短期報酬",
        category="market_context",
        description="族群近 5 根日線報酬。",
        tag_prefix="INDUSTRY_SHORT_RETURN=",
        value_type="number_pct",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="industry_medium_return",
        label="族群中期報酬",
        category="market_context",
        description="族群近 20 根日線報酬。",
        tag_prefix="INDUSTRY_MEDIUM_RETURN=",
        value_type="number_pct",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="industry_breadth",
        label="族群上漲家數比例",
        category="market_context",
        description="族群樣本中上漲標的比例。",
        tag_prefix="INDUSTRY_BREADTH=",
        value_type="number_pct",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="atr_pct",
        label="ATR 波動百分比",
        category="risk",
        description="以 ATR 衡量近期波動幅度。",
        tag_prefix="ATR_PCT=",
        value_type="number_pct",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="atr_heat",
        label="ATR 波動熱度",
        category="risk",
        description="將 ATR 分成 normal、elevated、hot，協助辨識高分但過熱的標的。",
        tag_prefix="ATR_HEAT=",
        value_type="enum",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="trend_ok",
        label="趨勢條件",
        category="technical",
        description="收盤價是否站上主要均線趨勢條件。",
        tag_prefix="TREND_OK=",
        value_type="bool",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="vol_ok",
        label="量能條件",
        category="technical",
        description="成交量是否放大到短期均量門檻之上。",
        tag_prefix="VOL_OK=",
        value_type="bool",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="trigger",
        label="觸發條件",
        category="technical",
        description="短線觸發條件是否成立，例如 RSI3 低檔或 KD 低檔黃金交叉。",
        tag_prefix="TRIGGER=",
        value_type="bool",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="trigger_reason",
        label="觸發原因",
        category="technical",
        description="觸發條件的具體來源。",
        tag_prefix="TRIGGER_REASON=",
        value_type="enum",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="chip_up3",
        label="籌碼連三日增強",
        category="chip",
        description="籌碼集中度是否連續三日改善。",
        tag_prefix="CHIP_UP3=",
        value_type="bool",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="fundamental_quality",
        label="基本面資料品質",
        category="fundamental",
        description="基本面資料完整度狀態。",
        tag_prefix="FUNDAMENTAL_QUALITY=",
        value_type="enum",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="revenue_score",
        label="營收分數",
        category="fundamental",
        description="營收動能分數。",
        tag_prefix="REVENUE_SCORE=",
        value_type="number",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="quality_score",
        label="財務品質分數",
        category="fundamental",
        description="財務品質分數。",
        tag_prefix="QUALITY_SCORE=",
        value_type="number",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="news_risk_score",
        label="新聞風險分數",
        category="risk",
        description="新聞或事件風險分數。",
        tag_prefix="NEWS_RISK_SCORE=",
        value_type="number",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="entry_score",
        label="進場總分",
        category="scoring",
        description="每日雷達綜合後的 Entry Score。",
        tag_prefix="ENTRY_SCORE=",
        value_type="number",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="outcome_reliability_score",
        label="Outcome 可靠度分數",
        category="scoring",
        description="依近期成熟雷達候選後續表現，回饋到排序的訊號可靠度分數。",
        tag_prefix="OUTCOME_RELIABILITY_SCORE=",
        value_type="number",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="outcome_reliability_boost",
        label="Outcome 可靠度加權",
        category="scoring",
        description="由訊號可靠度換算出的 Entry Score 微調分數，樣本不足時為 0。",
        tag_prefix="OUTCOME_RELIABILITY_BOOST=",
        value_type="number",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="outcome_reliability_samples",
        label="Outcome 可靠度樣本數",
        category="scoring",
        description="本次可靠度加權參考到的成熟 outcome 樣本數。",
        tag_prefix="OUTCOME_RELIABILITY_SAMPLES=",
        value_type="number",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="outcome_reliability_horizons",
        label="Outcome 多週期摘要",
        category="scoring",
        description="本次可靠度加權的 5/10/20 日正報酬率摘要。",
        tag_prefix="OUTCOME_RELIABILITY_HORIZONS=",
        value_type="string",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="symbol_adoption_feedback_boost",
        label="標的採用回饋加權",
        category="scoring",
        description="同一標的在採用推薦設定後的成熟 outcome 回饋，經信心權重調整後微調每日雷達排序。",
        tag_prefix="SYMBOL_ADOPTION_FEEDBACK_BOOST=",
        value_type="number",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="symbol_adoption_feedback_confidence",
        label="標的採用回饋信心",
        category="scoring",
        description="標的採用回饋依成熟樣本數與採用 run 數推估的信心等級。",
        tag_prefix="SYMBOL_ADOPTION_FEEDBACK_CONFIDENCE=",
        value_type="string",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="symbol_adoption_feedback_samples",
        label="標的採用回饋樣本數",
        category="scoring",
        description="標的採用回饋參考到的成熟 outcome 樣本數。",
        tag_prefix="SYMBOL_ADOPTION_FEEDBACK_SAMPLES=",
        value_type="number",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="symbol_adoption_feedback_horizons",
        label="標的採用回饋多週期摘要",
        category="scoring",
        description="標的採用後 5/10/20 日 outcome 的正報酬率摘要。",
        tag_prefix="SYMBOL_ADOPTION_FEEDBACK_HORIZONS=",
        value_type="string",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="excluded_reason",
        label="不碰清單原因",
        category="governance",
        description="標的被人工不碰清單擋下的原因。",
        tag_prefix="EXCLUDED_REASON=",
        value_type="string",
        outcome_bucket=True,
    ),
    SignalDefinition(
        signal_id="blocker_data_stale",
        label="資料過期阻擋",
        category="blocker",
        description="資料不是最新交易日，禁止進場。",
        exact_tags=("B_DATA_STALE",),
        value_type="flag",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="blocker_disposition_active",
        label="處置股阻擋",
        category="blocker",
        description="標的仍在處置期間，禁止進場。",
        exact_tags=("B_DISPOSITION_ACTIVE",),
        value_type="flag",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="blocker_attention_active",
        label="注意股阻擋",
        category="blocker",
        description="標的仍在注意交易清單中，禁止進場。",
        exact_tags=("B_ATTENTION_ACTIVE",),
        value_type="flag",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="blocker_low_liquidity",
        label="低流動性阻擋",
        category="blocker",
        description="流動性低於風險檔位門檻，禁止進場。",
        exact_tags=("B_LOW_LIQUIDITY",),
        value_type="flag",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="blocker_limit_lock_risk",
        label="漲跌停風險阻擋",
        category="blocker",
        description="價格與量能狀態接近鎖漲跌停風險，禁止進場。",
        exact_tags=("B_LIMIT_LOCK_RISK",),
        value_type="flag",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="blocker_missing_critical_chip",
        label="關鍵籌碼缺漏阻擋",
        category="blocker",
        description="缺少關鍵籌碼資料，禁止進場。",
        exact_tags=("B_MISSING_CRITICAL_CHIP",),
        value_type="flag",
        outcome_bucket=False,
    ),
    SignalDefinition(
        signal_id="blocker_excluded_symbol",
        label="不碰清單阻擋",
        category="blocker",
        description="標的在人工不碰清單內，禁止進場。",
        exact_tags=("B_EXCLUDED_SYMBOL",),
        value_type="flag",
        outcome_bucket=False,
    ),
)


DEFAULT_SIGNAL_REGISTRY = SignalRegistry(_DEFINITIONS)


def list_registered_signals(*, category: str | None = None, source: str | None = None) -> list[dict]:
    return DEFAULT_SIGNAL_REGISTRY.to_dicts(category=category, source=source)


def match_signal_tag(tag: str) -> SignalDefinition | None:
    return DEFAULT_SIGNAL_REGISTRY.match_tag(tag)


def outcome_bucket_for_tag(tag: str) -> str | None:
    definition = match_signal_tag(tag)
    if definition is None:
        return None
    return definition.bucket_for_tag(tag)


def radar_outcome_bucket_for_tag(tag: str) -> str:
    text = str(tag or "").strip()
    if not text:
        return ""
    registered_bucket = outcome_bucket_for_tag(text)
    if registered_bucket is not None:
        return registered_bucket
    noisy_prefixes = (
        "ENTRY_SCORE=",
        "INDUSTRY_ROTATION_SCORE=",
        "INDUSTRY_TREND_SCORE=",
        "INDUSTRY_SHORT_RETURN=",
        "INDUSTRY_MEDIUM_RETURN=",
        "INDUSTRY_BREADTH=",
        "ATR_PCT=",
        "OUTCOME_RELIABILITY_SCORE=",
        "OUTCOME_RELIABILITY_BOOST=",
        "OUTCOME_RELIABILITY_SAMPLES=",
        "OUTCOME_RELIABILITY_HORIZONS=",
    )
    if text.startswith(noisy_prefixes):
        return ""
    stable_prefixes = (
        "PROFILE=",
        "INDUSTRY_GROUP=",
        "INDUSTRY_TREND=",
        "ATR_HEAT=",
        "TRIGGER_REASON=",
        "EXCLUDED_REASON=",
        "TREND_OK=",
        "VOL_OK=",
        "TRIGGER=",
        "CHIP_UP3=",
    )
    if text.startswith(stable_prefixes):
        return text
    if "=" in text:
        return text.split("=", 1)[0].strip()
    return text
