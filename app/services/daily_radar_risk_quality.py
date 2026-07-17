from __future__ import annotations

from typing import Any


def reason_tag_value(item: dict[str, Any], prefix: str) -> str:
    tags = item.get("reason_tags") if isinstance(item.get("reason_tags"), list) else []
    for tag in tags:
        text = str(tag or "")
        if text.startswith(prefix):
            return text[len(prefix):]
    return ""


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def risk_quality_group_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        group = str(item.get("industry_group") or reason_tag_value(item, "INDUSTRY_GROUP=") or "其他").strip() or "其他"
        counts[group] = counts.get(group, 0) + 1
    return counts


def risk_quality_score(item: dict[str, Any], *, group_counts: dict[str, int], total_count: int) -> float:
    risk_adjusted = item.get("risk_adjusted_score")
    try:
        score = float(risk_adjusted)
        if score <= 0:
            raise ValueError("missing risk adjusted score")
    except Exception:
        score = _num(item.get("entry_score")) - (min(1.0, max(0.0, _num(item.get("risk_score")))) * 20.0)
    risk = min(1.0, max(0.0, _num(item.get("risk_score"))))
    blockers = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
    heat = str(item.get("atr_heat") or reason_tag_value(item, "ATR_HEAT=") or "normal").strip().lower()
    action = str(item.get("action_label") or "").strip().upper()
    group = str(item.get("industry_group") or reason_tag_value(item, "INDUSTRY_GROUP=") or "其他").strip() or "其他"
    group_share = (group_counts.get(group, 0) / max(1, int(total_count))) if total_count else 0.0

    score -= risk * 10.0
    if heat == "hot":
        score -= 8.0
    elif heat == "elevated":
        score -= 3.0
    score -= len(blockers) * 12.0
    if action == "ENTRY_READY":
        score += 4.0
    elif action == "WATCH_WAIT_TRIGGER":
        score -= 1.0
    elif action == "NOT_RECOMMENDED":
        score -= 10.0
    if group_share >= 0.50:
        score -= 6.0
    elif group_share >= 0.38:
        score -= 3.0
    return round(max(0.0, min(100.0, score)), 2)


def risk_quality_label(
    item: dict[str, Any],
    *,
    score: float,
    group_counts: dict[str, int],
    total_count: int,
) -> tuple[str, str]:
    blockers = item.get("blocker_tags") if isinstance(item.get("blocker_tags"), list) else []
    heat = str(item.get("atr_heat") or reason_tag_value(item, "ATR_HEAT=") or "normal").strip().lower()
    action = str(item.get("action_label") or "").strip().upper()
    risk = min(1.0, max(0.0, _num(item.get("risk_score"))))
    group = str(item.get("industry_group") or reason_tag_value(item, "INDUSTRY_GROUP=") or "其他").strip() or "其他"
    group_share = (group_counts.get(group, 0) / max(1, int(total_count))) if total_count else 0.0
    reasons = []
    if action == "ENTRY_READY":
        reasons.append("可進場")
    elif action == "WATCH_WAIT_TRIGGER":
        reasons.append("待觸發")
    else:
        reasons.append("不建議")
    if blockers:
        reasons.append(f"{len(blockers)} 個阻擋")
    if heat == "hot":
        reasons.append("波動過熱")
    elif heat == "elevated":
        reasons.append("波動偏高")
    reasons.append(f"風險 {risk:.1%}")
    if group_share >= 0.38:
        reasons.append(f"族群占比 {group_share:.0%}")

    if blockers or heat == "hot" or action == "NOT_RECOMMENDED":
        label = "先降溫"
    elif score >= 76 and action == "ENTRY_READY":
        label = "核心候選"
    elif score >= 64:
        label = "可小量觀察"
    elif score >= 50:
        label = "等待確認"
    else:
        label = "先跳過"
    return label, " / ".join(reasons)


def enrich_risk_quality(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(item) for item in items if isinstance(item, dict)]
    group_counts = risk_quality_group_counts(rows)
    total_count = len(rows)
    output: list[dict[str, Any]] = []
    for item in rows:
        score = risk_quality_score(item, group_counts=group_counts, total_count=total_count)
        label, reason = risk_quality_label(item, score=score, group_counts=group_counts, total_count=total_count)
        item["risk_quality_score"] = score
        item["risk_quality_label"] = label
        item["risk_quality_reason"] = reason
        output.append(item)
    return output
