"""管理者本機來源匯入：人工核驗、不可變原始資料與整批原子追加。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db.swing_repository import SwingRepository, content_hash, immediate_transaction, now_us, to_us
from app.services.subscription_research import SafeEvidenceFetcher


class InvalidSourceBundle(ValueError):
    def __init__(self, fields):
        self.missing_fields = sorted(set(fields))
        super().__init__("來源包不完整或不合法：" + ", ".join(self.missing_fields))


def _official_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname in SafeEvidenceFetcher.DEFAULT_HOSTS
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.fragment)
    except ValueError:
        return False


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _number(value, *, positive=False):
    if isinstance(value, bool):
        return False
    try:
        number = Decimal(str(value))
        return number.is_finite() and (number > 0 if positive else number >= 0)
    except (ValueError, InvalidOperation):
        return False


def _validate_source(item, path, current, errors):
    if not isinstance(item, dict):
        errors.append(path)
        return None
    item = deepcopy(item)
    kind = item.get("kind")
    if kind not in ("market_bar", "calendar", "corporate_action"):
        errors.append(path + ".kind")
    if not isinstance(item.get("source_key"), str) or not item["source_key"].strip() or len(item["source_key"]) > 200:
        errors.append(path + ".source_key")
    if not _official_url(item.get("url")):
        errors.append(path + ".url")
    raw = item.get("raw")
    if not isinstance(raw, (str, dict, list)) or not raw:
        errors.append(path + ".raw")
    payload = item.get("payload")
    if not isinstance(payload, dict):
        errors.append(path + ".payload")
        return None
    if payload.get("schema_version") != 1:
        errors.append(path + ".payload.schema_version")
    times = {}
    for field in ("public_at", "obtained_at", "verified_at"):
        value = item.get(field)
        if field == "public_at" and value is None:
            times[field] = None
            continue
        try:
            # 原始輸入必須為明確含時區的時間字串，不收內部整數時間戳。
            if not isinstance(value, str):
                raise ValueError("timestamp")
            times[field] = to_us(value)
        except (ValueError, TypeError, AttributeError):
            errors.append(path + "." + field)
    if "obtained_at" in times and "verified_at" in times:
        public = times.get("public_at")
        if not ((public is None or public <= times["obtained_at"]) and times["obtained_at"] <= times["verified_at"] <= current):
            errors.append(path + ".time_order")
    quality = item.get("quality")
    if quality not in ("verified", "unknown_publication"):
        errors.append(path + ".quality")
    if times.get("public_at") is None:
        if quality != "unknown_publication":
            errors.append(path + ".quality_requires_unknown_publication")
    elif quality == "unknown_publication":
        errors.append(path + ".unknown_publication_requires_null_time")
    attestation = item.get("attestation")
    if not (isinstance(attestation, dict) and attestation.get("confirmed") is True
            and isinstance(attestation.get("statement"), str) and attestation["statement"].strip()):
        errors.append(path + ".attestation")
    if kind in ("market_bar", "corporate_action"):
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.isalnum() or not 4 <= len(symbol) <= 12:
            errors.append(path + ".symbol")
    if not _day(item.get("data_period")):
        errors.append(path + ".data_period")
    if kind == "market_bar":
        if payload.get("phase") not in ("open", "close"):
            errors.append(path + ".payload.phase")
        fields = ("open",) if payload.get("phase") == "open" else ("open", "high", "low", "close", "volume")
        for field in fields:
            if not _number(payload.get(field), positive=field != "volume"):
                errors.append(path + ".payload." + field)
        if payload.get("phase") == "open" and any(field in payload for field in ("close", "high", "low", "volume")):
            errors.append(path + ".payload.open_contains_future_data")
        if payload.get("phase") == "close" and all(_number(payload.get(f), positive=True) for f in ("open", "high", "low", "close")):
            high, low = Decimal(str(payload["high"])), Decimal(str(payload["low"]))
            if not low <= min(Decimal(str(payload["open"])), Decimal(str(payload["close"]))) <= max(Decimal(str(payload["open"])), Decimal(str(payload["close"]))) <= high:
                errors.append(path + ".payload.ohlc_range")
        for field in ("available_at", "session_open_at"):
            try:
                value = payload.get(field)
                if not isinstance(value, str):
                    raise ValueError("timestamp")
                at = to_us(value)
                local = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Taipei"))
                if at > times.get("verified_at", 0) or local.date().isoformat() != item.get("data_period"):
                    raise ValueError("availability")
                opening = to_us(item["data_period"] + "T09:00:00+08:00")
                if field == "session_open_at" and at != opening:
                    raise ValueError("session_open")
                earliest = opening if payload.get("phase") == "open" else to_us(item["data_period"] + "T13:30:00+08:00")
                if field == "available_at" and at < earliest:
                    raise ValueError("market_data_before_session")
                if field == "available_at" and times.get("public_at") is not None and at < times["public_at"]:
                    raise ValueError("premature_availability")
            except (ValueError, TypeError, AttributeError):
                errors.append(path + ".payload." + field)
    elif kind == "calendar":
        sessions = payload.get("sessions")
        if not isinstance(sessions, list) or not sessions or not all(_day(d) for d in sessions) or sessions != sorted(set(sessions)):
            errors.append(path + ".payload.sessions")
        symbols = payload.get("symbols")
        if not isinstance(symbols, list) or not symbols or any(not isinstance(s, str) or not s.strip() for s in symbols) or len(symbols) != len(set(symbols)):
            errors.append(path + ".payload.symbols")
        start, end = payload.get("period_start"), payload.get("period_end")
        if not _day(start) or not _day(end) or start > end:
            errors.append(path + ".payload.period")
        elif isinstance(sessions, list) and all(_day(d) for d in sessions) and any(not start <= d <= end for d in sessions):
            errors.append(path + ".payload.sessions_outside_period")
        if type(payload.get("corporate_actions_complete")) is not bool:
            errors.append(path + ".payload.corporate_actions_complete")
        evidence = payload.get("corporate_action_evidence")
        if payload.get("corporate_actions_complete") is True:
            if not isinstance(evidence, list) or not evidence:
                errors.append(path + ".payload.corporate_action_evidence")
            else:
                for index, entry in enumerate(evidence):
                    ep = f"{path}.payload.corporate_action_evidence[{index}]"
                    if (not isinstance(entry, dict) or not _official_url(entry.get("url"))
                            or not entry.get("raw") or entry.get("raw_hash") != content_hash(entry.get("raw"))
                            or entry.get("symbols") != symbols or entry.get("period_start") != start or entry.get("period_end") != end):
                        errors.append(ep)
    elif kind == "corporate_action":
        if not _day(payload.get("ex_date")) or payload.get("ex_date") != item.get("data_period"):
            errors.append(path + ".payload.ex_date")
        if payload.get("type") == "cash_dividend":
            if not _number(payload.get("cash_per_share")) or not _day(payload.get("payment_date")) or payload["payment_date"] < payload.get("ex_date", ""):
                errors.append(path + ".payload.dividend")
        elif payload.get("type") == "split":
            if not _number(payload.get("ratio"), positive=True):
                errors.append(path + ".payload.ratio")
        else:
            errors.append(path + ".payload.type")
    return item


def preview_source_bundle(bundle, *, at_us=None):
    """唯讀預覽；缺公布時間仍可隔離匯入，永不升格為可用來源。"""
    errors = []
    if not isinstance(bundle, dict):
        raise InvalidSourceBundle(["bundle"])
    if bundle.get("schema_version") != 1:
        errors.append("schema_version")
    key = bundle.get("bundle_key")
    if not isinstance(key, str) or not key.strip() or len(key) > 160:
        errors.append("bundle_key")
    sources = bundle.get("sources")
    if not isinstance(sources, list) or not sources or len(sources) > 10000:
        errors.append("sources")
        sources = []
    current = at_us if at_us is not None else now_us()
    rows = [_validate_source(item, f"sources[{index}]", current, errors) for index, item in enumerate(sources)]
    keys = [(r.get("kind"), r.get("source_key")) for r in rows if r and isinstance(r.get("kind"), str) and isinstance(r.get("source_key"), str)]
    if len(keys) != len(set(keys)):
        errors.append("sources.duplicate_source_key")
    market_keys = [(r.get("symbol"), r.get("data_period"), r["payload"].get("phase"))
                   for r in rows if r and r.get("kind") == "market_bar"
                   and all(isinstance(v, str) for v in (r.get("symbol"), r.get("data_period"), r["payload"].get("phase")))]
    if len(market_keys) != len(set(market_keys)):
        errors.append("sources.duplicate_market_identity_use_repair_preview_and_repair_apply")
    digest = content_hash(bundle)
    return {"bundle_key": key, "bundle_hash": digest, "valid": not errors, "missing_fields": sorted(set(errors)),
            "trust_model": "管理者人工核驗聲明；匯入本身不代表獨立外部查證",
            "source_count": len(sources), "qualified_source_count": sum(r.get("quality") == "verified" for r in rows if r),
            "unknown_publication_count": sum(r.get("quality") == "unknown_publication" for r in rows if r),
            "sources": [{"kind": r.get("kind"), "symbol": r.get("symbol"), "data_period": r.get("data_period"),
                         "source_key": r.get("source_key"), "url": r.get("url"), "quality": r.get("quality"),
                         "raw_hash": content_hash(r.get("raw")), "payload_hash": content_hash(r.get("payload"))} for r in rows if r]}


def import_source_bundle(session, bundle, *, expected_hash, actor, note):
    preview = preview_source_bundle(bundle)
    if not preview["valid"]:
        raise InvalidSourceBundle(preview["missing_fields"])
    if not actor or not actor.strip() or not note or not note.strip():
        raise InvalidSourceBundle(["actor", "note"])
    if preview["bundle_hash"] != expected_hash:
        raise ValueError("bundle_hash_mismatch")
    repo = SwingRepository(session)
    with immediate_transaction(session), session.begin_nested():
        repo._require_schema()
        old = session.execute(text("SELECT id,payload_json FROM swing_source_versions WHERE json_extract(payload_json,'$._import.bundle_key')=:key ORDER BY id"), {"key": bundle["bundle_key"]}).mappings().all()
        if old:
            import json
            if any(json.loads(row["payload_json"])["_import"]["bundle_hash"] != expected_hash for row in old):
                raise ValueError("bundle_key_content_conflict")
            if len(old) != len(bundle["sources"]):
                raise ValueError("bundle_incomplete_conflict")
            return {"bundle_key": bundle["bundle_key"], "bundle_hash": expected_hash, "source_ids": [r["id"] for r in old], "replayed": True}
        ids = []
        for item in bundle["sources"]:
            # 匯入不建立修訂：來源修訂必須經 repair-preview／repair-apply
            # 保留同一 source_key 與 supersedes 鏈，避免同日行情重複入選。
            exists = session.execute(text("SELECT id FROM swing_source_versions WHERE source=:source AND kind=:kind AND source_key=:key LIMIT 1"),
                {"source": "official-local-bundle", "kind": item["kind"], "key": item["source_key"]}).first()
            if exists:
                raise ValueError("source_key_content_conflict")
            if item["kind"] == "market_bar":
                existing_bar = session.execute(text("""SELECT id FROM swing_source_versions
                    WHERE kind='market_bar' AND symbol=:symbol AND data_period=:period
                    AND json_extract(payload_json,'$.phase')=:phase LIMIT 1"""),
                    {"symbol": item["symbol"], "period": item["data_period"], "phase": item["payload"]["phase"]}).first()
                if existing_bar:
                    raise ValueError("market_revision_requires_repair_preview_and_repair_apply")
            payload = deepcopy(item["payload"])
            payload["_import"] = {"schema_version": 1, "bundle_key": bundle["bundle_key"], "bundle_hash": expected_hash,
                "actor": actor.strip(), "note": note.strip(), "source_url": item["url"], "raw": deepcopy(item["raw"]),
                "verification_method": "trusted_administrator_attestation",
                "raw_hash": content_hash(item["raw"]), "attestation": deepcopy(item["attestation"]), "source_item_hash": content_hash(item)}
            source = repo.append_source({"source": "official-local-bundle", "source_key": item["source_key"], "kind": item["kind"],
                "symbol": item.get("symbol"), "data_period": item["data_period"], "public_at": item.get("public_at"),
                "obtained_at": item["obtained_at"], "verified_at": item["verified_at"], "quality": item["quality"], "payload": payload})
            ids.append(source["id"])
        return {"bundle_key": bundle["bundle_key"], "bundle_hash": expected_hash, "source_ids": ids, "replayed": False,
                "qualified_source_count": preview["qualified_source_count"], "unknown_publication_count": preview["unknown_publication_count"]}
