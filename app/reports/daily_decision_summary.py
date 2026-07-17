from __future__ import annotations

import html
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.security.reporting import sanitize_report_payload


ACTION_LABELS = {
    "ENTRY_READY": "可進場",
    "WATCH_WAIT_TRIGGER": "觀察中",
    "NOT_RECOMMENDED": "不建議",
}

BLOCKER_LABELS = {
    "B_DATA_STALE": "資料過舊",
    "B_DISPOSITION_ACTIVE": "處置期間中",
    "B_ATTENTION_ACTIVE": "注意股期間中",
    "B_LOW_LIQUIDITY": "流動性不足",
    "B_LIMIT_LOCK_RISK": "鎖單風險偏高",
    "B_MISSING_CRITICAL_CHIP": "籌碼資料不足",
    "B_EXCLUDED_SYMBOL": "排除清單",
}

ACTION_QUEUE_LANE_ORDER = ("entry", "watch", "risk")
ACTION_QUEUE_LANE_META = {
    "entry": {
        "label": "先看進場",
        "hint": "分數與條件已到位",
        "empty": "目前沒有可進場標的。",
    },
    "watch": {
        "label": "等觸發",
        "hint": "接近條件，先放觀察",
        "empty": "目前沒有觀察中標的。",
    },
    "risk": {
        "label": "先處理風險",
        "hint": "資料、處置或流動性要先確認",
        "empty": "目前沒有明顯阻擋。",
    },
}

INVESTMENT_ACTION_ORDER = ("entry", "watch", "avoid")
INVESTMENT_ACTION_META = {
    "entry": {
        "label": "可進場",
        "hint": "條件相對完整，先看價格、部位與正式 gate。",
        "empty": "目前沒有可進場標的。",
    },
    "watch": {
        "label": "觀察中",
        "hint": "分數接近，但仍要等觸發或資料補齊。",
        "empty": "目前沒有觀察中標的。",
    },
    "avoid": {
        "label": "不要碰",
        "hint": "有阻擋、資料疑慮或風險條件，先避開。",
        "empty": "目前沒有明確不要碰標的。",
    },
}


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _pct(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except Exception:
        return "-"


def _signed_num(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
        return f"{number:+.{digits}f}"
    except Exception:
        return "-"


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except Exception:
        return "-"


def _allocation_premarket_reason_text(item: dict[str, Any]) -> str:
    reasons = item.get("premarket_reasons")
    if not isinstance(reasons, list):
        return ""
    labels: list[str] = []
    for reason in reasons:
        if not isinstance(reason, dict):
            continue
        label = str(reason.get("label") or reason.get("type") or "").strip()
        if label:
            labels.append(label)
    return "、".join(labels[:3])


def _allocation_premarket_html(item: dict[str, Any], *, empty: str = "-") -> str:
    reason_text = _allocation_premarket_reason_text(item)
    multiplier = item.get("premarket_multiplier")
    has_multiplier = False
    try:
        multiplier_value = float(multiplier)
        has_multiplier = True
    except Exception:
        multiplier_value = 1.0
    if not has_multiplier and not reason_text:
        return _esc(empty)
    multiplier_text = _num(multiplier_value, 2) if has_multiplier else "-"
    tone = "positive" if multiplier_value > 1 else "negative" if multiplier_value < 1 else "neutral"
    return (
        f'<span class="allocation-premarket {tone}">'
        f"<strong>盤前 x{_esc(multiplier_text)}</strong>"
        f"{f'<em>{_esc(reason_text)}</em>' if reason_text else ''}"
        "</span>"
    )


def _risk_quality_tone(label: Any) -> str:
    text = str(label or "")
    if text in {"核心候選", "可小量觀察"}:
        return "ok"
    if text == "等待確認":
        return "watch"
    if text in {"先降溫", "先跳過"}:
        return "danger"
    return "neutral"


def _risk_quality_html(item: dict[str, Any], *, compact: bool = False) -> str:
    label = str(item.get("risk_quality_label") or "").strip()
    score = item.get("risk_quality_score")
    reason = str(item.get("risk_quality_reason") or "").strip()
    if not label and score in (None, ""):
        return ""
    reason_html = "" if compact or not reason else f"<em>{_esc(reason)}</em>"
    return (
        f'<span class="risk-quality-chip {_risk_quality_tone(label)}">'
        f"<strong>{_esc(label or '風險品質')}</strong>"
        f"<b>{_esc(_num(score, 1))}</b>"
        f"{reason_html}"
        "</span>"
    )


def _reliability_horizon_text(item: dict) -> str:
    rows = item.get("outcome_reliability_horizons")
    if not isinstance(rows, list) or not rows:
        return "樣本不足"
    parts = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        horizon = int(row.get("lookahead_bars", 0) or 0)
        positive_rate = row.get("positive_rate")
        if horizon <= 0 or positive_rate is None:
            continue
        parts.append(f"{horizon}日 {_pct(positive_rate, 0)}")
    return " / ".join(parts) if parts else "樣本不足"


def _action_label(value: Any) -> str:
    return ACTION_LABELS.get(str(value or ""), str(value or "-"))


def _blocker_label(value: Any) -> str:
    return BLOCKER_LABELS.get(str(value or ""), str(value or "-"))


def _count_groups(items: list[dict]) -> list[tuple[str, int]]:
    counts = Counter(str(item.get("industry_group") or "其他") for item in items)
    return counts.most_common(8)


def _count_blockers(items: list[dict]) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for item in items:
        for tag in item.get("blocker_tags", []) if isinstance(item.get("blocker_tags"), list) else []:
            counts[_blocker_label(tag)] += 1
    return counts.most_common(8)


def _rank_value(item: dict) -> int:
    try:
        return int(item.get("rank") or 9999)
    except Exception:
        return 9999


def _tags(item: dict, key: str) -> list[Any]:
    rows = item.get(key)
    return rows if isinstance(rows, list) else []


def _has_blockers(item: dict) -> bool:
    return len(_tags(item, "blocker_tags")) > 0


def _queue_note(item: dict) -> str:
    blockers = [_blocker_label(tag) for tag in _tags(item, "blocker_tags")[:2] if tag]
    if blockers:
        return f"阻擋：{' / '.join(blockers)}"
    reasons = [str(tag) for tag in _tags(item, "reason_tags")[:2] if tag]
    if reasons:
        return f"理由：{' / '.join(reasons)}"
    return _action_label(item.get("action_label"))


def _queue_item(item: dict, lane: str) -> dict[str, Any]:
    blocker_tags = [str(tag) for tag in _tags(item, "blocker_tags") if tag]
    reason_tags = [str(tag) for tag in _tags(item, "reason_tags") if tag]
    return {
        "lane": lane,
        "lane_label": ACTION_QUEUE_LANE_META[lane]["label"],
        "rank": item.get("rank", ""),
        "symbol": item.get("symbol", ""),
        "name": item.get("name", ""),
        "action_label": item.get("action_label", ""),
        "action_text": _action_label(item.get("action_label")),
        "entry_score": item.get("entry_score", ""),
        "risk_score": item.get("risk_score", ""),
        "industry_group": item.get("industry_group", ""),
        "reason_tags": reason_tags,
        "blocker_tags": blocker_tags,
        "note": _queue_note(item),
    }


def _investment_action_key(item: dict) -> str:
    action = str(item.get("action_label") or "")
    if action == "ENTRY_READY":
        return "entry"
    if action == "WATCH_WAIT_TRIGGER":
        return "watch"
    return "avoid"


def investment_action_label_for_item(item: dict) -> str:
    return INVESTMENT_ACTION_META[_investment_action_key(item)]["label"]


def _investment_representative(item: dict, action_key: str) -> dict[str, Any]:
    return {
        "action": action_key,
        "action_label": INVESTMENT_ACTION_META[action_key]["label"],
        "rank": item.get("rank", ""),
        "symbol": item.get("symbol", ""),
        "name": item.get("name", ""),
        "entry_score": item.get("entry_score", ""),
        "risk_score": item.get("risk_score", ""),
        "industry_group": item.get("industry_group", ""),
        "note": _queue_note(item),
    }


def build_investment_actions(items: list[dict]) -> dict[str, Any]:
    rows = items if isinstance(items, list) else []
    action_rows = {
        "entry": sorted((item for item in rows if _investment_action_key(item) == "entry"), key=_rank_value),
        "watch": sorted((item for item in rows if _investment_action_key(item) == "watch"), key=_rank_value),
        "avoid": sorted(
            (item for item in rows if _investment_action_key(item) == "avoid"),
            key=lambda item: (-len(_tags(item, "blocker_tags")), _rank_value(item)),
        ),
    }
    output = {
        key: {
            **INVESTMENT_ACTION_META[key],
            "count": len(action_rows[key]),
            "representatives": [_investment_representative(item, key) for item in action_rows[key][:3]],
        }
        for key in INVESTMENT_ACTION_ORDER
    }
    output["summary"] = {
        "entry_count": len(action_rows["entry"]),
        "watch_count": len(action_rows["watch"]),
        "avoid_count": len(action_rows["avoid"]),
    }
    return output


def _diversified_shortlist_item(item: dict, shortlist_rank: int) -> dict[str, Any]:
    blocker_tags = [str(tag) for tag in _tags(item, "blocker_tags") if tag]
    reason_tags = [str(tag) for tag in _tags(item, "reason_tags") if tag]
    try:
        samples = int(item.get("outcome_reliability_samples", 0) or 0)
    except Exception:
        samples = 0
    return {
        "shortlist_rank": shortlist_rank,
        "rank": item.get("rank", ""),
        "symbol": item.get("symbol", ""),
        "name": item.get("name", ""),
        "action_label": item.get("action_label", ""),
        "investment_action": investment_action_label_for_item(item),
        "entry_score": item.get("entry_score", ""),
        "risk_score": item.get("risk_score", ""),
        "industry_group": item.get("industry_group") or "其他",
        "outcome_reliability_boost": item.get("outcome_reliability_boost", ""),
        "outcome_reliability_samples": samples,
        "reason_tags": reason_tags[:5],
        "blocker_tags": blocker_tags,
    }


def build_diversified_shortlist(items: list[dict], limit: int = 5) -> list[dict[str, Any]]:
    rows = sorted((item for item in items if isinstance(item, dict)), key=_rank_value) if isinstance(items, list) else []
    try:
        max_items = max(0, int(limit))
    except Exception:
        max_items = 5
    selected: list[dict] = []
    selected_symbols: set[str] = set()
    group_counts: Counter[str] = Counter()

    def add(item: dict, *, require_no_blockers: bool, max_per_group: int | None) -> None:
        if len(selected) >= max_items:
            return
        symbol = str(item.get("symbol") or "").strip()
        if not symbol or symbol in selected_symbols:
            return
        if require_no_blockers and _has_blockers(item):
            return
        group = str(item.get("industry_group") or "其他")
        if max_per_group is not None and group_counts[group] >= max_per_group:
            return
        selected.append(item)
        selected_symbols.add(symbol)
        group_counts[group] += 1

    for options in (
        {"require_no_blockers": True, "max_per_group": 1},
        {"require_no_blockers": True, "max_per_group": 2},
        {"require_no_blockers": False, "max_per_group": 2},
        {"require_no_blockers": False, "max_per_group": None},
    ):
        for item in rows:
            add(item, **options)
            if len(selected) >= max_items:
                break

    return [_diversified_shortlist_item(item, index + 1) for index, item in enumerate(selected)]


def action_queue_lanes_for_item(item: dict) -> list[str]:
    lanes: list[str] = []
    action = str(item.get("action_label") or "")
    if action == "ENTRY_READY":
        lanes.append("先看進場")
    if action == "WATCH_WAIT_TRIGGER":
        lanes.append("等觸發")
    if _has_blockers(item) or action == "NOT_RECOMMENDED":
        lanes.append("先處理風險")
    return lanes


def build_action_queue(items: list[dict]) -> dict[str, Any]:
    rows = items if isinstance(items, list) else []
    by_rank = lambda item: _rank_value(item)
    entry_rows = sorted((item for item in rows if item.get("action_label") == "ENTRY_READY"), key=by_rank)[:3]
    watch_rows = sorted((item for item in rows if item.get("action_label") == "WATCH_WAIT_TRIGGER"), key=by_rank)[:3]
    risk_rows = sorted(
        (item for item in rows if _has_blockers(item) or item.get("action_label") == "NOT_RECOMMENDED"),
        key=lambda item: (-len(_tags(item, "blocker_tags")), _rank_value(item)),
    )[:3]
    lane_rows = {"entry": entry_rows, "watch": watch_rows, "risk": risk_rows}
    queue = {
        lane: {
            **ACTION_QUEUE_LANE_META[lane],
            "count": len(lane_rows[lane]),
            "items": [_queue_item(item, lane) for item in lane_rows[lane]],
        }
        for lane in ACTION_QUEUE_LANE_ORDER
    }
    queue["summary"] = {
        "entry_focus": len(entry_rows),
        "watch_triggers": len(watch_rows),
        "risk_total": sum(1 for item in rows if _has_blockers(item) or item.get("action_label") == "NOT_RECOMMENDED"),
    }
    return queue


def _priority_items(items: list[dict]) -> list[dict]:
    actionable = {"ENTRY_READY", "WATCH_WAIT_TRIGGER"}
    return [item for item in items if str(item.get("action_label")) in actionable][:8]


def _bars(title: str, rows: list[tuple[str, int]], empty: str) -> str:
    if not rows:
        return f'<section class="panel"><h2>{_esc(title)}</h2><p class="empty">{_esc(empty)}</p></section>'
    max_count = max(count for _, count in rows) or 1
    body = "".join(
        f"""
        <div class="bar-row">
          <div class="bar-head"><span>{_esc(label)}</span><strong>{count}</strong></div>
          <div class="bar-track"><span style="width:{max(4, round(count / max_count * 100))}%"></span></div>
        </div>
        """
        for label, count in rows
    )
    return f'<section class="panel"><h2>{_esc(title)}</h2>{body}</section>'


def _priority_section(items: list[dict]) -> str:
    rows = _priority_items(items)
    if not rows:
        return '<section class="panel priority"><h2>優先觀察</h2><p class="empty">目前沒有可進場或觀察中標的。</p></section>'
    cards = "".join(
        f"""
        <article class="pick">
          <div class="rank">#{_esc(item.get("rank", "-"))}</div>
          <div>
            <h3>{_esc(item.get("symbol", "-"))} <span>{_esc(item.get("name", ""))}</span></h3>
            <p>{_esc(item.get("industry_group", "其他"))} · 產業輪動 {_pct(item.get("industry_rotation_score"), 1)} · 可靠度 {_signed_num(item.get("outcome_reliability_boost"), 2)}（{_esc(_reliability_horizon_text(item))}） · ATR {_pct(item.get("atr_pct"), 2)}</p>
          </div>
          <strong class="badge">{_esc(_action_label(item.get("action_label")))}</strong>
        </article>
        """
        for item in rows
    )
    return f'<section class="panel priority"><h2>優先觀察</h2><div class="pick-grid">{cards}</div></section>'


def _investment_actions_section(items: list[dict]) -> str:
    actions = build_investment_actions(items)
    cards = []
    for key in INVESTMENT_ACTION_ORDER:
        meta = actions[key]
        representatives = meta["representatives"]
        chips = "".join(
            f"""
            <span class="action-chip">
              <strong>{_esc(item.get("symbol", "-"))}</strong>
              <em>{_num(item.get("entry_score"), 1)}</em>
              <small>{_esc(item.get("note", ""))}</small>
            </span>
            """
            for item in representatives
        ) or f'<p class="empty">{_esc(meta["empty"])}</p>'
        cards.append(
            f"""
            <article class="action-card {key}">
              <div class="action-card-top">
                <span>{_esc(meta["label"])}</span>
                <strong>{_esc(meta["count"])}</strong>
              </div>
              <p>{_esc(meta["hint"])}</p>
              <div class="action-chip-grid">{chips}</div>
            </article>
            """
        )
    return f"""
    <section class="panel investment-actions">
      <div class="kicker">INVESTMENT ACTIONS</div>
      <h2>今日投資行動</h2>
      <div class="action-card-grid">{''.join(cards)}</div>
    </section>
    """


def _diversified_shortlist_section(shortlist: list[dict[str, Any]]) -> str:
    if not shortlist:
        return """
        <section class="panel diversified-shortlist">
          <div class="kicker">DIVERSIFIED SHORTLIST</div>
          <h2>分散首選</h2>
          <p class="empty">目前沒有足夠標的產生分散首選。</p>
        </section>
        """
    cards = "".join(
        f"""
        <article class="diversified-card">
          <div class="diversified-top">
            <span>#{_esc(item.get("shortlist_rank", "-"))}</span>
            <strong>{_esc(item.get("symbol", "-"))}</strong>
            <em>{_esc(item.get("investment_action", "-"))}</em>
          </div>
          <h3>{_esc(item.get("name") or item.get("industry_group") or "未命名")}</h3>
          <p>{_esc(item.get("industry_group") or "其他")} · Entry {_num(item.get("entry_score"), 1)} · Risk {_pct(item.get("risk_score"), 0)}</p>
          {_risk_quality_html(item, compact=True)}
          <small>來源 Rank #{_esc(item.get("rank", "-"))} · 可靠度 {_signed_num(item.get("outcome_reliability_boost"), 2)} · 樣本 {_esc(item.get("outcome_reliability_samples", 0))} · 阻擋 {len(_tags(item, "blocker_tags"))}</small>
        </article>
        """
        for item in shortlist
    )
    return f"""
    <section class="panel diversified-shortlist">
      <div class="kicker">DIVERSIFIED SHORTLIST</div>
      <div class="diversified-head">
        <h2>分散首選</h2>
        <span>先跨族群，再放寬同族群與阻擋條件</span>
      </div>
      <div class="diversified-grid">{cards}</div>
    </section>
    """


def _action_queue_section(items: list[dict]) -> str:
    queue = build_action_queue(items)
    lanes = []
    for lane in ACTION_QUEUE_LANE_ORDER:
        meta = queue[lane]
        cards = "".join(
            f"""
            <article class="queue-card">
              <div class="queue-top">
                <strong>#{_esc(item.get("rank", "-"))} {_esc(item.get("symbol", "-"))}</strong>
                <span>{_esc(item.get("action_text", "-"))}</span>
              </div>
              <h3>{_esc(item.get("name", ""))}</h3>
              <p>{_esc(item.get("industry_group") or "其他")} · Entry {_num(item.get("entry_score"), 1)} · Risk {_pct(item.get("risk_score"), 0)}</p>
              {_risk_quality_html(item, compact=True)}
              <small>{_esc(item.get("note", ""))}</small>
            </article>
            """
            for item in meta["items"]
        )
        body = cards or f'<p class="empty">{_esc(meta["empty"])}</p>'
        lanes.append(
            f"""
            <section class="queue-lane">
              <div class="queue-lane-head">
                <h3>{_esc(meta["label"])}</h3>
                <span>{_esc(meta["hint"])}</span>
              </div>
              {body}
            </section>
            """
        )
    summary = queue["summary"]
    summary_text = (
        f"{summary['entry_focus']} 進場焦點 · "
        f"{summary['watch_triggers']} 觀察觸發 · "
        f"{summary['risk_total']} 風險待確認"
    )
    return f"""
    <section class="panel queue">
      <div class="queue-head">
        <div>
          <div class="kicker">ACTION QUEUE</div>
          <h2>今日動作佇列</h2>
        </div>
        <strong>{_esc(summary_text)}</strong>
      </div>
      <div class="queue-grid">{''.join(lanes)}</div>
    </section>
    """


def _allocation_evidence_section(
    allocation_plan: dict[str, Any],
    allocation_outcomes: dict[str, Any],
    allocation_calibration: dict[str, Any],
    review_lane_exports: dict[str, Any] | None = None,
) -> str:
    if not any(isinstance(value, dict) and value for value in (allocation_plan, allocation_outcomes, allocation_calibration)):
        return ""

    profile = allocation_plan.get("calibration_profile") if isinstance(allocation_plan, dict) else {}
    if not isinstance(profile, dict):
        profile = allocation_calibration.get("profile") if isinstance(allocation_calibration, dict) else {}
    if not isinstance(profile, dict):
        profile = {}
    plan_items = allocation_plan.get("allocation_items") if isinstance(allocation_plan.get("allocation_items"), list) else []
    allocation_rows = "".join(
        f"""
        <tr>
          <td><strong>{_esc(item.get("symbol", "-"))}</strong><br><span>{_esc(item.get("name", ""))}</span></td>
          <td>{_esc(item.get("industry_group") or "其他")}</td>
          <td>{_money(item.get("suggested_value"))}</td>
          <td>{_esc(item.get("suggested_odd_lot_shares", "-"))}</td>
          <td>{_num(item.get("calibration_multiplier"), 2)}</td>
          <td>{_allocation_premarket_html(item)}</td>
          <td>{_esc(item.get("next_step") or "-")}</td>
        </tr>
        """
        for item in plan_items[:6]
        if isinstance(item, dict)
    )
    allocation_table = (
        f"""
        <div class="allocation-table-wrap">
          <table class="allocation-table">
            <thead>
              <tr><th>標的</th><th>族群</th><th>建議金額</th><th>零股股數</th><th>校準</th><th>盤前</th><th>下一步</th></tr>
            </thead>
            <tbody>{allocation_rows}</tbody>
          </table>
        </div>
        """
        if allocation_rows
        else '<p class="empty">目前沒有配置草稿項目。</p>'
    )

    calibration_summary = allocation_plan.get("calibration") if isinstance(allocation_plan.get("calibration"), dict) else {}
    top_positive = allocation_calibration.get("top_positive") or calibration_summary.get("top_positive") or []
    top_negative = allocation_calibration.get("top_negative") or calibration_summary.get("top_negative") or []
    diagnostics = (
        allocation_plan.get("allocation_diagnostics")
        if isinstance(allocation_plan.get("allocation_diagnostics"), dict)
        else {}
    )
    diagnostic_alerts = diagnostics.get("alerts") if isinstance(diagnostics.get("alerts"), list) else []
    diagnostic_metrics = diagnostics.get("summary_metrics") if isinstance(diagnostics.get("summary_metrics"), dict) else {}
    diagnostic_alert_html = "".join(
        f"""
        <span class="allocation-diagnostic-chip {str(alert.get("severity") or "")}">
          <strong>{_esc(alert.get("label") or "-")}</strong>
          <em>{_esc(alert.get("detail") or "")}</em>
        </span>
        """
        for alert in diagnostic_alerts[:4]
        if isinstance(alert, dict)
    ) or '<span class="allocation-diagnostic-chip ok"><strong>無明顯配置警示</strong><em>仍需檢查價格、部位與正式 gate。</em></span>'
    review_queue = diagnostics.get("review_queue") if isinstance(diagnostics.get("review_queue"), dict) else {}
    review_lanes = review_queue.get("lanes") if isinstance(review_queue.get("lanes"), dict) else {}
    review_summary = review_queue.get("summary") if isinstance(review_queue.get("summary"), dict) else {}
    review_handoff = review_queue.get("handoff") if isinstance(review_queue.get("handoff"), dict) else {}

    def review_lane_count(key: str, summary_key: str) -> int:
        lane = review_lanes.get(key) if isinstance(review_lanes.get(key), dict) else {}
        items = lane.get("items") if isinstance(lane.get("items"), list) else []
        raw_count = review_summary.get(summary_key, lane.get("count", len(items)))
        try:
            return int(raw_count)
        except Exception:
            return len(items)

    review_stats = [
        ("preview", "preview_count", "可先建立 Preview"),
        ("trigger", "trigger_count", "設觸發觀察"),
        ("risk", "risk_count", "先處理風險"),
    ]
    review_counts = [(key, label, review_lane_count(key, summary_key)) for key, summary_key, label in review_stats]
    review_focus = next(((label, count) for key, label, count in review_counts if key == "risk" and count > 0), None)
    if review_focus is None:
        review_focus = next(((label, count) for key, label, count in review_counts if key == "preview" and count > 0), None)
    if review_focus is None:
        review_focus = next(((label, count) for key, label, count in review_counts if key == "trigger" and count > 0), None)
    review_focus_text = f"{review_focus[0]} {review_focus[1]} 檔" if review_focus else "等待配置行動線"
    review_summary_html = (
        f"""
        <div class="allocation-review-summary">
          <div><span>行動線摘要</span><strong>主線：{_esc(review_focus_text)}</strong></div>
          <div>
            {''.join(f'<span class="{_esc(key)}"><em>{_esc(label)}</em><strong>{_esc(count)}</strong></span>' for key, label, count in review_counts)}
          </div>
        </div>
        """
        if review_lanes
        else ""
    )
    review_handoff_html = (
        f"""
        <div class="allocation-review-handoff">
          <span>盤前交接</span>
          <strong>{_esc(review_handoff.get("primary_action") or "等待配置行動線")}</strong>
          <small>{_esc(review_handoff.get("next_session_note") or "")}</small>
        </div>
        """
        if review_handoff
        else ""
    )
    export_urls = review_lane_exports if isinstance(review_lane_exports, dict) else {}
    export_link_rows = [
        ("decision_brief_md", "盤前 Brief MD", "preview"),
        ("quality_lane_research_now_csv", "可先研究 CSV", "preview"),
        ("quality_lane_research_now_symbols_txt", "可先研究 TXT", "preview"),
        ("quality_lane_observe_only_csv", "只觀察 CSV", "trigger"),
        ("quality_lane_observe_only_symbols_txt", "觀察代號 TXT", "trigger"),
        ("quality_lane_repair_first_csv", "修資料後再看 CSV", "risk"),
        ("quality_lane_repair_first_symbols_txt", "修資料代號 TXT", "risk"),
        ("quality_lane_skip_for_now_csv", "先略過 CSV", "risk"),
        ("quality_lane_skip_for_now_symbols_txt", "略過代號 TXT", "risk"),
        ("allocation_review_preview_csv", "可先建立 Preview CSV", "preview"),
        ("allocation_review_preview_symbols_txt", "Preview 代號 TXT", "preview"),
        ("allocation_review_trigger_csv", "設觸發觀察 CSV", "trigger"),
        ("allocation_review_trigger_symbols_txt", "觸發代號 TXT", "trigger"),
        ("allocation_review_risk_csv", "先處理風險 CSV", "risk"),
        ("allocation_review_risk_symbols_txt", "風險代號 TXT", "risk"),
    ]
    export_links = "".join(
        f'<a class="{_esc(css)}" href="{_esc(export_urls[key])}">{_esc(label)}</a>'
        for key, label, css in export_link_rows
        if export_urls.get(key)
    )
    review_export_html = (
        f"""
        <div class="allocation-review-exports">
          <strong>行動線下載</strong>
          <div>{export_links}</div>
        </div>
        """
        if export_links
        else ""
    )

    def review_checklist_items(source: dict[str, Any]) -> list[str]:
        text = source.get("checklist_text")
        if text:
            return [part.strip() for part in str(text).split("|") if part.strip()]
        checks = source.get("checklist") if isinstance(source.get("checklist"), list) else []
        return [str(check) for check in checks if str(check) != "<redacted-depth-limit>"]

    def review_trigger_plan_html(source: dict[str, Any]) -> str:
        plan = source.get("trigger_plan") if isinstance(source.get("trigger_plan"), dict) else {}
        if not plan:
            return ""

        def metric(label: str, value: Any, digits: int) -> str:
            try:
                text = f"{float(value):.{digits}f}"
            except Exception:
                return ""
            return f"<span><em>{_esc(label)}</em><strong>{_esc(text)}</strong></span>"

        metric_html = "".join(
            part
            for part in [
                metric("Entry >=", plan.get("target_entry_score"), 0),
                metric("突破", plan.get("breakout_price"), 2),
                metric("回測", plan.get("pullback_price"), 2),
            ]
            if part
        )
        summary = str(plan.get("summary") or "").strip()
        rules = plan.get("rules") if isinstance(plan.get("rules"), list) else []
        rule_text = summary or " / ".join(str(rule) for rule in rules[:2] if str(rule).strip())
        return f"""
        <span class="allocation-review-trigger-plan">
          <b>觸發條件</b>
          {f'<span class="allocation-review-trigger-metrics">{metric_html}</span>' if metric_html else ''}
          {f'<i>{_esc(rule_text)}</i>' if rule_text else ''}
        </span>
        """

    def review_allocation_status(source: dict[str, Any]) -> str:
        if source.get("monitor_only") is True or str(source.get("allocation_lane") or "") == "monitor_only":
            return "盯盤備選"
        if source.get("allocated") is False:
            return "未配置"
        if str(source.get("allocated") or "").strip().lower() == "false":
            return "未配置"
        return "已配置"

    def review_lane_html(key: str) -> str:
        lane = review_lanes.get(key) if isinstance(review_lanes.get(key), dict) else {}
        items = lane.get("items") if isinstance(lane.get("items"), list) else []
        alert_plan = lane.get("alert_plan") if isinstance(lane.get("alert_plan"), dict) else {}
        plan_checks = review_checklist_items(alert_plan)
        plan_html = (
            f"""
            <div class="allocation-review-alert-plan">
              <strong>提醒計畫</strong>
              <em>{_esc(alert_plan.get("rule") or "")}</em>
              <ul>{''.join(f'<li>{_esc(check)}</li>' for check in plan_checks[:3])}</ul>
            </div>
            """
            if alert_plan
            else ""
        )
        body = "".join(
            f"""
            <span class="allocation-review-item">
              <strong>{_esc(item.get("symbol") or "-")} {_esc(item.get("name") or "")}</strong>
              <b class="allocation-review-status">{_esc(review_allocation_status(item))}</b>
              <em>{_esc(item.get("reason") or item.get("next_step") or "-")}</em>
              {_risk_quality_html(item, compact=True)}
              {f'<small class="allocation-review-premarket">{_allocation_premarket_html(item, empty="")}</small>' if item.get("premarket_multiplier") or item.get("premarket_reasons") else ''}
              <small>{_esc(item.get("alert_rule") or "")}</small>
              {f'<small class="allocation-review-watchlist-note">{_esc(item.get("trigger_watchlist_note") or "")}</small>' if item.get("trigger_watchlist_note") else ''}
              {review_trigger_plan_html(item)}
            </span>
            """
            for item in items[:3]
            if isinstance(item, dict)
        ) or '<span class="allocation-review-empty">目前沒有項目</span>'
        return f"""
        <section class="allocation-review-lane {key}">
          <div><strong>{_esc(lane.get("label") or key)}</strong><span>{_esc(lane.get("count", len(items)))} 件</span></div>
          <small>{_esc(lane.get("hint") or "")}</small>
          {plan_html}
          {body}
        </section>
        """

    review_queue_html = (
        f"""
        <div class="allocation-review-queue">
          {review_lane_html("preview")}
          {review_lane_html("trigger")}
          {review_lane_html("risk")}
        </div>
        """
        if review_lanes
        else ""
    )
    diagnostic_html = (
        f"""
        <div class="allocation-diagnostics">
          <div>
            <span>配置診斷</span>
            <strong>{_esc(diagnostics.get("verdict") or "等待診斷")}</strong>
            <small>預算使用 {_pct(diagnostic_metrics.get("budget_utilization"), 0)} · 最大族群 {_pct(diagnostic_metrics.get("max_group_budget_share"), 0)}</small>
          </div>
          <div>{diagnostic_alert_html}</div>
          {review_handoff_html}
          {review_summary_html}
          {review_export_html}
          {review_queue_html}
        </div>
        """
        if diagnostics
        else ""
    )

    def chips(rows: Any, tone: str) -> str:
        if not isinstance(rows, list) or not rows:
            return '<span class="allocation-chip muted">暫無樣本</span>'
        return "".join(
            f"""
            <span class="allocation-chip {tone}">
              <strong>{_esc(row.get("label") or row.get("key") or "-")}</strong>
              <em>{_num(row.get("multiplier"), 2)}x</em>
            </span>
            """
            for row in rows[:3]
            if isinstance(row, dict)
        )

    outcomes_status = str(allocation_outcomes.get("status") or "")
    outcomes_text = allocation_outcomes.get("verdict") or allocation_outcomes.get("reason") or "等待成熟樣本"
    if outcomes_status == "ok":
        outcomes_detail = (
            f"平均 alpha {_signed_num(allocation_outcomes.get('average_alpha'), 4)} · "
            f"配置正報酬率 {_pct(allocation_outcomes.get('allocation_positive_rate'), 0)} · "
            f"驗證 run {allocation_outcomes.get('evaluated_run_count', 0)}"
        )
    else:
        outcomes_detail = "成熟雷達 run 或後續日線樣本仍不足。"

    return f"""
    <section class="panel allocation-evidence">
      <div class="kicker">ALLOCATION EVIDENCE</div>
      <div class="allocation-head">
        <div>
          <h2>研究配置與驗證</h2>
          <p>配置 profile：{_esc(profile.get("label") or profile.get("key") or "-")} · 校準狀態：{_esc((allocation_calibration or calibration_summary).get("status") or "-")}</p>
        </div>
        <strong>{_esc(outcomes_text)}</strong>
      </div>
      <div class="allocation-stats">
        <span><em>研究預算</em><strong>{_money(allocation_plan.get("research_budget"))}</strong></span>
        <span><em>已配置</em><strong>{_money(allocation_plan.get("allocated_value"))}</strong></span>
        <span><em>配置檔數</em><strong>{_esc(allocation_plan.get("allocation_count", 0))}</strong></span>
        <span><em>配置驗證</em><strong>{_esc(outcomes_detail)}</strong></span>
      </div>
      {diagnostic_html}
      {allocation_table}
      <div class="allocation-calibration-grid">
        <section>
          <h3>配置校準：加權因素</h3>
          <div>{chips(top_positive, "positive")}</div>
        </section>
        <section>
          <h3>配置校準：降權因素</h3>
          <div>{chips(top_negative, "negative")}</div>
        </section>
      </div>
    </section>
    """


def _threshold_sweep_section(threshold_sweep: dict[str, Any]) -> str:
    if not isinstance(threshold_sweep, dict) or threshold_sweep.get("status") != "ok":
        return ""
    metrics = threshold_sweep.get("metrics") if isinstance(threshold_sweep.get("metrics"), dict) else {}
    stable_symbols = threshold_sweep.get("stable_symbols") if isinstance(threshold_sweep.get("stable_symbols"), list) else []
    fragile_symbols = threshold_sweep.get("fragile_symbols") if isinstance(threshold_sweep.get("fragile_symbols"), list) else []
    variants = threshold_sweep.get("threshold_variants") if isinstance(threshold_sweep.get("threshold_variants"), list) else []
    stable_text = "、".join(str(symbol) for symbol in stable_symbols[:8] if str(symbol).strip()) or "暫無"
    fragile_text = "、".join(str(symbol) for symbol in fragile_symbols[:8] if str(symbol).strip()) or "暫無"
    variant_chips = "".join(
        f"""
        <span class="threshold-chip">
          <em>{_signed_num(item.get("entry_offset"), 0)}</em>
          <strong>{_esc(item.get("actionable_count", 0))}</strong>
          <small>可行動</small>
        </span>
        """
        for item in variants[:5]
        if isinstance(item, dict)
    )
    return f"""
    <section class="panel threshold-sweep">
      <div class="kicker">THRESHOLD ROBUSTNESS</div>
      <div class="threshold-head">
        <div>
          <h2>門檻穩健性</h2>
          <p>{_esc(threshold_sweep.get("next_step") or "")}</p>
        </div>
        <strong>{_esc(threshold_sweep.get("verdict") or "-")} · {_esc(threshold_sweep.get("robustness_score", 0))}/100</strong>
      </div>
      <div class="threshold-grid">
        <span><em>基準風格</em><strong>{_esc(threshold_sweep.get("base_profile_label") or "-")}</strong></span>
        <span><em>基準可行動</em><strong>{_esc(metrics.get("baseline_actionable_count", 0))}</strong></span>
        <span><em>穩定標的</em><strong>{_esc(metrics.get("stable_count", 0))}</strong></span>
        <span><em>門檻邊緣</em><strong>{_esc(metrics.get("fragile_count", 0))}</strong></span>
      </div>
      <div class="threshold-symbols">
        <span><em>穩定</em><strong>{_esc(stable_text)}</strong></span>
        <span><em>邊緣</em><strong>{_esc(fragile_text)}</strong></span>
      </div>
      <div class="threshold-variants">{variant_chips}</div>
    </section>
    """


def _table(items: list[dict]) -> str:
    if not items:
        return '<section class="panel"><h2>完整雷達清單</h2><p class="empty">尚無每日雷達資料。</p></section>'
    body = "".join(
        f"""
        <tr>
          <td>{_esc(item.get("rank", "-"))}</td>
          <td><strong>{_esc(item.get("symbol", "-"))}</strong><br><span>{_esc(item.get("name", ""))}</span></td>
          <td>{_esc(_action_label(item.get("action_label")))}</td>
          <td>{_num(item.get("entry_score"), 2)}</td>
          <td>{_pct(item.get("risk_score"), 1)}</td>
          <td>{_risk_quality_html(item, compact=True) or "-"}</td>
          <td>{_esc(item.get("industry_group", "其他"))}</td>
          <td>{_pct(item.get("industry_rotation_score"), 1)}</td>
          <td>{_signed_num(item.get("outcome_reliability_boost"), 2)}<br><span>{_esc(_reliability_horizon_text(item))}</span></td>
          <td>{_esc("｜".join(_blocker_label(tag) for tag in item.get("blocker_tags", []) if tag) or "-")}</td>
        </tr>
        """
        for item in items
    )
    return f"""
    <section class="panel full">
      <h2>完整雷達清單</h2>
      <table>
        <thead>
          <tr>
            <th>Rank</th><th>標的</th><th>動作</th><th>Entry</th><th>Risk</th><th>風險品質</th><th>族群</th><th>輪動</th><th>可靠度</th><th>阻擋</th>
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </section>
    """


def render_daily_decision_summary_html(
    items: list[dict],
    *,
    report_date: date | str,
    generated_at: datetime | None = None,
    allocation_plan: dict[str, Any] | None = None,
    allocation_outcomes: dict[str, Any] | None = None,
    allocation_calibration: dict[str, Any] | None = None,
    review_lane_exports: dict[str, Any] | None = None,
    threshold_sweep: dict[str, Any] | None = None,
) -> str:
    safe_items = sanitize_report_payload(items)
    safe_allocation_plan = sanitize_report_payload(allocation_plan) if isinstance(allocation_plan, dict) else {}
    safe_allocation_outcomes = sanitize_report_payload(allocation_outcomes) if isinstance(allocation_outcomes, dict) else {}
    safe_allocation_calibration = sanitize_report_payload(allocation_calibration) if isinstance(allocation_calibration, dict) else {}
    safe_review_lane_exports = sanitize_report_payload(review_lane_exports) if isinstance(review_lane_exports, dict) else {}
    safe_threshold_sweep = sanitize_report_payload(threshold_sweep) if isinstance(threshold_sweep, dict) else {}
    generated = generated_at or datetime.now()
    entry_count = sum(1 for item in safe_items if item.get("action_label") == "ENTRY_READY")
    watch_count = sum(1 for item in safe_items if item.get("action_label") == "WATCH_WAIT_TRIGGER")
    blocker_count = sum(len(item.get("blocker_tags", [])) for item in safe_items if isinstance(item.get("blocker_tags"), list))
    title = f"{report_date} 每日決策摘要"
    action_queue = build_action_queue(safe_items)
    investment_actions = build_investment_actions(safe_items)
    diversified_shortlist = build_diversified_shortlist(safe_items)
    payload_json = _esc(
        json.dumps(
            {
                "report_date": str(report_date),
                "investment_actions": investment_actions,
                "action_queue": action_queue,
                "diversified_shortlist": diversified_shortlist,
                "allocation_plan": safe_allocation_plan,
                "allocation_outcomes": safe_allocation_outcomes,
                "allocation_calibration": safe_allocation_calibration,
                "review_lane_exports": safe_review_lane_exports,
                "threshold_sweep": safe_threshold_sweep,
                "items": safe_items,
            },
            ensure_ascii=False,
        )
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(title)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#101828; --muted:#667085; --line:#dce7e1; --accent:#12866d; --soft:#f6faf8; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:#eef4f1; color:var(--ink); font-family:"Noto Sans TC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ max-width:1180px; margin:0 auto; padding:36px 20px 52px; }}
    .hero {{ border:1px solid rgba(16,24,40,.12); border-radius:12px; background:linear-gradient(135deg,#ffffff,#f8fbf9 65%,#e7f5ef); padding:26px; box-shadow:0 18px 42px rgba(16,24,40,.08); }}
    .kicker {{ color:var(--accent); font:800 12px ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing:.14em; }}
    h1 {{ margin:8px 0 4px; font-size:34px; line-height:1.12; }}
    .meta {{ color:var(--muted); font-size:13px; }}
    .stats {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:22px; }}
    .stat {{ border:1px solid var(--line); border-radius:10px; background:#fff; padding:14px; }}
    .stat span {{ display:block; color:var(--muted); font:800 11px ui-monospace, SFMono-Regular, Menlo, monospace; text-transform:uppercase; }}
    .stat strong {{ display:block; margin-top:6px; font-size:28px; }}
    .grid {{ display:grid; grid-template-columns:1.2fr .8fr .8fr; gap:14px; margin-top:16px; }}
    .panel {{ border:1px solid var(--line); border-radius:12px; background:#fff; padding:16px; box-shadow:0 10px 26px rgba(16,24,40,.05); }}
    .panel h2 {{ margin:0 0 12px; font-size:17px; }}
    .pick-grid {{ display:grid; gap:10px; }}
    .pick {{ display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:10px; align-items:center; border:1px solid #e4eee9; border-radius:10px; padding:10px; }}
    .rank {{ color:var(--accent); font:900 13px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .pick h3 {{ margin:0; font-size:15px; }}
    .pick h3 span {{ color:var(--muted); font-weight:500; }}
    .pick p {{ margin:4px 0 0; color:var(--muted); font-size:12px; }}
    .badge {{ border-radius:999px; background:#e8f7f1; color:var(--accent); padding:5px 9px; font-size:12px; }}
    .bar-row {{ display:grid; gap:6px; margin-bottom:10px; }}
    .bar-head {{ display:flex; justify-content:space-between; gap:8px; color:#344054; font-size:12px; font-weight:800; }}
    .bar-track {{ height:8px; border-radius:999px; background:rgba(16,24,40,.08); overflow:hidden; }}
    .bar-track span {{ display:block; height:100%; border-radius:inherit; background:linear-gradient(90deg,#18a384,#0f6f5f); }}
    .investment-actions {{ margin-top:16px; }}
    .action-card-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:10px; }}
    .action-card {{ border:1px solid #d9e8e1; border-radius:10px; background:var(--soft); padding:12px; }}
    .action-card.entry {{ background:linear-gradient(180deg,#fff,#ecfdf3); }}
    .action-card.watch {{ background:linear-gradient(180deg,#fff,#fffaeb); }}
    .action-card.avoid {{ background:linear-gradient(180deg,#fff,#fef3f2); }}
    .action-card-top {{ display:flex; justify-content:space-between; gap:10px; align-items:center; }}
    .action-card-top span {{ color:var(--muted); font:900 11px ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing:.08em; }}
    .action-card-top strong {{ font:900 28px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .action-card p {{ color:var(--muted); font-size:12px; line-height:1.5; }}
    .action-chip-grid {{ display:grid; gap:6px; }}
    .action-chip {{ display:grid; grid-template-columns:auto auto minmax(0,1fr); gap:7px; align-items:center; border:1px solid #d9e8e1; border-radius:8px; background:#fff; padding:7px 8px; }}
    .action-chip strong {{ color:var(--accent); font:900 12px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .action-chip em {{ font-style:normal; color:#344054; font:900 11px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .action-chip small {{ color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .diversified-shortlist {{ margin-top:16px; }}
    .diversified-head {{ display:flex; justify-content:space-between; align-items:flex-end; gap:12px; margin-bottom:12px; }}
    .diversified-head h2 {{ margin:0; }}
    .diversified-head span {{ color:var(--muted); font-size:12px; text-align:right; }}
    .diversified-grid {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; }}
    .diversified-card {{ border:1px solid #d9e8e1; border-radius:10px; background:linear-gradient(180deg,#fff,#f7fbf9); padding:10px; min-width:0; }}
    .diversified-top {{ display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:7px; align-items:center; }}
    .diversified-top span {{ color:var(--accent); font:900 12px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .diversified-top strong {{ overflow:hidden; text-overflow:ellipsis; color:#101828; font:900 13px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .diversified-top em {{ border-radius:999px; background:#e8f7f1; color:var(--accent); padding:3px 7px; font-style:normal; font-size:10px; font-weight:900; white-space:nowrap; }}
    .diversified-card h3 {{ margin:9px 0 4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }}
    .diversified-card p, .diversified-card small {{ margin:0; color:var(--muted); font-size:12px; line-height:1.45; }}
    .queue {{ margin-top:16px; }}
    .queue-head {{ display:flex; align-items:flex-end; justify-content:space-between; gap:14px; margin-bottom:14px; }}
    .queue-head h2 {{ margin:3px 0 0; }}
    .queue-head strong {{ color:var(--muted); font-size:13px; }}
    .queue-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
    .queue-lane {{ border:1px solid #e4eee9; border-radius:10px; padding:12px; background:var(--soft); }}
    .queue-lane-head {{ display:flex; justify-content:space-between; gap:10px; margin-bottom:10px; }}
    .queue-lane-head h3 {{ margin:0; font-size:14px; }}
    .queue-lane-head span {{ color:var(--muted); font-size:11px; text-align:right; }}
    .queue-card {{ display:grid; gap:5px; border:1px solid #d9e8e1; border-radius:10px; background:#fff; padding:10px; margin-top:8px; }}
    .queue-top {{ display:flex; align-items:center; justify-content:space-between; gap:8px; }}
    .queue-top strong {{ color:var(--accent); font:900 13px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .queue-top span {{ border-radius:999px; background:#e8f7f1; color:var(--accent); padding:4px 8px; font-size:11px; font-weight:800; }}
    .queue-card h3 {{ margin:0; font-size:15px; }}
    .queue-card p, .queue-card small {{ margin:0; color:var(--muted); font-size:12px; }}
    .risk-quality-chip {{ display:inline-grid; grid-template-columns:auto auto; gap:3px 6px; align-items:center; width:fit-content; max-width:100%; border:1px solid #d9e8e1; border-radius:8px; background:#fff; color:#344054; padding:4px 6px; margin:3px 0; }}
    .risk-quality-chip strong {{ color:#101828; font:900 10px ui-monospace, SFMono-Regular, Menlo, monospace; white-space:nowrap; }}
    .risk-quality-chip b {{ color:#101828; font:900 10px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .risk-quality-chip em {{ grid-column:1 / -1; color:#667085; font-style:normal; font-size:10px; line-height:1.25; }}
    .risk-quality-chip.ok {{ border-color:#abefc6; background:#f6fef9; }}
    .risk-quality-chip.ok strong,.risk-quality-chip.ok b {{ color:#067647; }}
    .risk-quality-chip.watch {{ border-color:#fedf89; background:#fffcf5; }}
    .risk-quality-chip.watch strong,.risk-quality-chip.watch b {{ color:#b54708; }}
    .risk-quality-chip.danger {{ border-color:#fecdca; background:#fff8f7; }}
    .risk-quality-chip.danger strong,.risk-quality-chip.danger b {{ color:#b42318; }}
    .allocation-evidence {{ margin-top:16px; }}
    .allocation-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    .allocation-head h2 {{ margin:4px 0 4px; }}
    .allocation-head p {{ margin:0; color:var(--muted); font-size:12px; }}
    .allocation-head strong {{ border-radius:999px; background:#e8f7f1; color:var(--accent); padding:6px 10px; font-size:12px; white-space:nowrap; }}
    .allocation-stats {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:14px 0; }}
    .allocation-stats span {{ border:1px solid #d9e8e1; border-radius:10px; background:var(--soft); padding:10px; }}
    .allocation-stats em {{ display:block; color:var(--muted); font-style:normal; font-size:11px; font-weight:800; }}
    .allocation-stats strong {{ display:block; margin-top:5px; font-size:14px; }}
    .allocation-diagnostics {{ border:1px solid #d9e8e1; border-radius:10px; background:var(--soft); padding:12px; margin-bottom:12px; display:grid; grid-template-columns:.72fr 1.28fr; gap:10px; }}
    .allocation-diagnostics span:first-child {{ display:block; color:var(--muted); font:900 11px ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing:.08em; }}
    .allocation-diagnostics strong {{ display:block; margin-top:4px; color:#101828; font-size:15px; }}
    .allocation-diagnostics small {{ display:block; margin-top:4px; color:var(--muted); font-size:12px; }}
    .allocation-diagnostic-chip {{ display:block; border:1px solid #d9e8e1; border-radius:8px; background:#fff; padding:8px; margin-bottom:7px; }}
    .allocation-diagnostic-chip.warn {{ border-color:#fecdca; background:#fff8f7; }}
    .allocation-diagnostic-chip.watch {{ border-color:#fedf89; background:#fffcf5; }}
    .allocation-diagnostic-chip.ok {{ border-color:#abefc6; background:#f6fef9; }}
    .allocation-diagnostic-chip strong {{ margin:0; font-size:12px; }}
    .allocation-diagnostic-chip em {{ display:block; margin-top:3px; color:var(--muted); font-style:normal; font-size:11px; line-height:1.35; }}
    .allocation-review-summary {{ grid-column:1 / -1; border:1px solid #d9e8e1; border-radius:8px; background:#fff; padding:10px; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:center; }}
    .allocation-review-summary > div:first-child span {{ display:block; color:var(--muted); font:900 10px ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing:.06em; }}
    .allocation-review-summary > div:first-child strong {{ margin-top:3px; color:#101828; font-size:13px; }}
    .allocation-review-summary > div:last-child {{ display:flex; justify-content:flex-end; flex-wrap:wrap; gap:6px; }}
    .allocation-review-summary > div:last-child span {{ border:1px solid #e4e9f2; border-radius:999px; background:#f9fbfa; padding:4px 7px; display:inline-flex; align-items:center; gap:5px; }}
    .allocation-review-summary span.preview {{ border-color:#abefc6; }}
    .allocation-review-summary span.trigger {{ border-color:#b2ddff; }}
    .allocation-review-summary span.risk {{ border-color:#fecdca; }}
    .allocation-review-summary em {{ color:var(--muted); font-style:normal; font-size:10px; font-weight:800; }}
    .allocation-review-summary > div:last-child strong {{ margin:0; color:#101828; font:900 11px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .allocation-review-handoff {{ grid-column:1 / -1; border:1px solid #d9e8e1; border-radius:8px; background:#f6fefc; padding:10px; }}
    .allocation-review-handoff span {{ display:block; color:var(--muted); font:900 10px ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing:.06em; }}
    .allocation-review-handoff strong {{ margin-top:3px; color:#101828; font-size:14px; }}
    .allocation-review-handoff small {{ margin-top:4px; color:#475467; font-size:11px; }}
    .allocation-review-exports {{ grid-column:1 / -1; border:1px solid #d9e8e1; border-radius:8px; background:#fff; padding:10px; display:flex; align-items:center; justify-content:space-between; gap:10px; }}
    .allocation-review-exports > strong {{ margin:0; color:#101828; font-size:13px; }}
    .allocation-review-exports > div {{ display:flex; flex-wrap:wrap; justify-content:flex-end; gap:7px; }}
    .allocation-review-exports a {{ border:1px solid #d9e8e1; border-radius:999px; background:#f9fbfa; color:#344054; padding:5px 9px; text-decoration:none; font-size:11px; font-weight:900; }}
    .allocation-review-exports a.preview {{ border-color:#abefc6; color:#067647; }}
    .allocation-review-exports a.trigger {{ border-color:#b2ddff; color:#175cd3; }}
    .allocation-review-exports a.risk {{ border-color:#fecdca; color:#b42318; }}
    .allocation-review-queue {{ grid-column:1 / -1; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }}
    .allocation-review-lane {{ border:1px solid #d9e8e1; border-radius:8px; background:#fff; padding:10px; display:grid; gap:7px; }}
    .allocation-review-lane div {{ display:flex; align-items:center; justify-content:space-between; gap:8px; }}
    .allocation-review-lane div strong {{ margin:0; font-size:12px; }}
    .allocation-review-lane div span {{ border-radius:999px; background:#f2f4f7; color:#344054; padding:3px 7px; font:900 10px ui-monospace, SFMono-Regular, Menlo, monospace; white-space:nowrap; }}
    .allocation-review-lane small {{ color:var(--muted); font-size:10px; line-height:1.35; }}
    .allocation-review-item {{ border:1px solid #edf2ef; border-radius:8px; background:#f9fbfa; padding:7px; }}
    .allocation-review-item strong {{ margin:0; font:900 11px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .allocation-review-status {{ display:inline-block; margin:4px 0 0; border:1px solid #d1e9ff; border-radius:999px; background:#eff8ff; color:#175cd3; padding:2px 6px; font:900 9px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .allocation-review-alert-plan {{ border:1px solid #edf2ef; border-radius:8px; background:#f8fcfb; padding:7px; display:block; }}
    .allocation-review-alert-plan strong {{ margin:0; color:#101828; font-size:11px; }}
    .allocation-review-alert-plan em {{ display:block; margin-top:3px; color:#475467; font-style:normal; font-size:10px; line-height:1.35; }}
    .allocation-review-alert-plan ul {{ margin:5px 0 0; padding-left:16px; color:#667085; font-size:10px; line-height:1.4; }}
    .allocation-review-item em {{ display:block; margin-top:3px; color:var(--muted); font-style:normal; font-size:10px; line-height:1.35; }}
    .allocation-review-item small {{ display:block; margin-top:3px; color:#344054; font-size:10px; line-height:1.35; }}
    .allocation-review-premarket {{ margin-top:6px !important; }}
    .allocation-review-watchlist-note {{ border-left:2px solid #84caff; padding-left:6px; color:#175cd3 !important; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .allocation-review-trigger-plan {{ display:block; margin-top:6px; border:1px solid #d1e9ff; border-radius:7px; background:#f5fbff; padding:6px; color:#175cd3; }}
    .allocation-review-trigger-plan b {{ display:block; color:#1849a9; font-size:10px; }}
    .allocation-review-trigger-metrics {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:5px; }}
    .allocation-review-trigger-metrics span {{ border:1px solid #b2ddff; border-radius:999px; background:#eff8ff; padding:3px 6px; display:inline-flex; gap:4px; align-items:center; }}
    .allocation-review-trigger-metrics span em {{ display:inline; margin:0; color:#175cd3; font-style:normal; font-size:9px; }}
    .allocation-review-trigger-metrics span strong {{ margin:0; color:#101828; font:900 10px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .allocation-review-trigger-plan i {{ display:block; margin-top:5px; color:#475467; font-style:normal; font-size:10px; line-height:1.35; }}
    .allocation-review-empty {{ color:var(--muted); font-size:11px; }}
    .allocation-table-wrap {{ overflow:auto; }}
    .allocation-table {{ min-width:980px; }}
    .allocation-premarket {{ display:inline-flex; flex-direction:column; align-items:flex-start; gap:2px; border:1px solid #d9e8e1; border-radius:8px; background:#fff; padding:4px 6px; color:#344054; }}
    .allocation-premarket strong {{ color:#101828; font:900 10px ui-monospace, SFMono-Regular, Menlo, monospace; white-space:nowrap; }}
    .allocation-premarket em {{ color:#667085; font-style:normal; font-size:10px; line-height:1.25; }}
    .allocation-premarket.positive {{ border-color:#abefc6; background:#f6fef9; }}
    .allocation-premarket.positive strong {{ color:#067647; }}
    .allocation-premarket.negative {{ border-color:#fecdca; background:#fff8f7; }}
    .allocation-premarket.negative strong {{ color:#b42318; }}
    .allocation-premarket.neutral {{ border-color:#d1e9ff; background:#f5fbff; }}
    .allocation-calibration-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px; }}
    .allocation-calibration-grid section {{ border:1px solid #e4eee9; border-radius:10px; padding:12px; background:var(--soft); }}
    .allocation-calibration-grid h3 {{ margin:0 0 8px; font-size:13px; }}
    .allocation-chip {{ display:inline-flex; align-items:center; gap:6px; border:1px solid #d9e8e1; border-radius:999px; background:#fff; padding:5px 8px; margin:0 6px 6px 0; font-size:12px; }}
    .allocation-chip.positive em {{ color:#067647; }}
    .allocation-chip.negative em {{ color:#b42318; }}
    .allocation-chip.muted {{ color:var(--muted); }}
    .allocation-chip em {{ font-style:normal; font-weight:900; }}
    .threshold-sweep {{ margin-top:16px; }}
    .threshold-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    .threshold-head h2 {{ margin:4px 0 4px; }}
    .threshold-head p {{ margin:0; color:var(--muted); font-size:12px; }}
    .threshold-head strong {{ border-radius:999px; background:#eff8ff; color:#175cd3; padding:6px 10px; font-size:12px; white-space:nowrap; }}
    .threshold-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-top:14px; }}
    .threshold-grid span,.threshold-symbols span {{ border:1px solid #d9e8e1; border-radius:10px; background:var(--soft); padding:10px; }}
    .threshold-grid em,.threshold-symbols em {{ display:block; color:var(--muted); font-style:normal; font-size:11px; font-weight:800; }}
    .threshold-grid strong,.threshold-symbols strong {{ display:block; margin-top:5px; font-size:14px; }}
    .threshold-symbols {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px; }}
    .threshold-variants {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }}
    .threshold-chip {{ border:1px solid #d1e9ff; border-radius:999px; background:#f5fbff; color:#175cd3; padding:6px 9px; display:inline-flex; gap:6px; align-items:center; }}
    .threshold-chip em {{ font-style:normal; font:900 11px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .threshold-chip strong {{ margin:0; color:#101828; font:900 12px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .threshold-chip small {{ color:#175cd3; font-size:10px; }}
    .full {{ margin-top:16px; overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; min-width:860px; }}
    th, td {{ border-bottom:1px solid #edf2ef; padding:10px; text-align:left; font-size:13px; vertical-align:top; }}
    th {{ color:var(--muted); font-size:11px; text-transform:uppercase; }}
    td span {{ color:var(--muted); }}
    .empty {{ color:var(--muted); margin:0; }}
    details {{ margin-top:16px; color:var(--muted); }}
    pre {{ white-space:pre-wrap; word-break:break-word; background:#101828; color:#d7fff4; border-radius:10px; padding:14px; }}
    @media (max-width:900px) {{ .stats,.grid,.queue-grid,.action-card-grid,.diversified-grid,.allocation-stats,.allocation-calibration-grid,.allocation-diagnostics,.allocation-review-summary,.allocation-review-queue,.threshold-grid,.threshold-symbols {{ grid-template-columns:1fr; }} .queue-head,.diversified-head,.allocation-head,.allocation-review-exports,.threshold-head {{ align-items:flex-start; flex-direction:column; }} .allocation-review-summary > div:last-child,.allocation-review-exports > div {{ justify-content:flex-start; }} h1 {{ font-size:28px; }} }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="kicker">DAILY DECISION BRIEF</div>
      <h1>{_esc(title)}</h1>
      <div class="meta">產生時間：{_esc(generated.strftime("%Y-%m-%d %H:%M:%S"))}</div>
      <div class="stats">
        <div class="stat"><span>雷達檔數</span><strong>{len(safe_items)}</strong></div>
        <div class="stat"><span>可進場</span><strong>{entry_count}</strong></div>
        <div class="stat"><span>觀察中</span><strong>{watch_count}</strong></div>
        <div class="stat"><span>阻擋標籤</span><strong>{blocker_count}</strong></div>
      </div>
    </section>
    {_investment_actions_section(safe_items)}
    {_diversified_shortlist_section(diversified_shortlist)}
    {_action_queue_section(safe_items)}
    {_threshold_sweep_section(safe_threshold_sweep)}
    {_allocation_evidence_section(safe_allocation_plan, safe_allocation_outcomes, safe_allocation_calibration, safe_review_lane_exports)}
    <section class="grid">
      {_priority_section(safe_items)}
      {_bars("族群分布", _count_groups(safe_items), "尚無族群資料。")}
      {_bars("阻擋原因", _count_blockers(safe_items), "目前無阻擋原因。")}
    </section>
    {_table(safe_items)}
    <details><summary>原始摘要 JSON</summary><pre>{payload_json}</pre></details>
  </main>
</body>
</html>
"""


def export_daily_decision_summary_report(
    items: list[dict],
    *,
    output_dir: Path,
    report_date: date | str,
    run_id: str = "latest",
    generated_at: datetime | None = None,
    allocation_plan: dict[str, Any] | None = None,
    allocation_outcomes: dict[str, Any] | None = None,
    allocation_calibration: dict[str, Any] | None = None,
    review_lane_exports: dict[str, Any] | None = None,
    threshold_sweep: dict[str, Any] | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_run_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(run_id or "latest"))
    date_text = str(report_date)
    filename = f"daily_decision_summary_{date_text}_{safe_run_id}.html"
    path = output_dir / filename
    path.write_text(
        render_daily_decision_summary_html(
            items,
            report_date=report_date,
            generated_at=generated_at,
            allocation_plan=allocation_plan,
            allocation_outcomes=allocation_outcomes,
            allocation_calibration=allocation_calibration,
            review_lane_exports=review_lane_exports,
            threshold_sweep=threshold_sweep,
        ),
        encoding="utf-8",
    )
    return {
        "files": {"html_summary": filename},
        "urls": {"html_summary": f"/reports/{filename}"},
    }
