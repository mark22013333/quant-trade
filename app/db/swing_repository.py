"""不可變研究來源與污染處理；所有寫入保留呼叫端交易。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported_json_type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_json_default)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def to_us(value: str | datetime | int) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid_timestamp")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("invalid_timestamp")
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_requires_explicit_timezone_and_time")
    delta = parsed.astimezone(timezone.utc) - _EPOCH
    result = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    if result <= 0:
        raise ValueError("invalid_timestamp")
    return result


def from_us(value: int | None) -> str | None:
    if value is None:
        return None
    from datetime import timedelta
    return (_EPOCH + timedelta(microseconds=value)).isoformat()


def now_us() -> int:
    return to_us(datetime.now(timezone.utc))


@contextmanager
def immediate_transaction(session: Session):
    """BEGIN IMMEDIATE 只接管尚未開始的實體交易；從不提交別人的工作。"""
    owned = not session.in_transaction()
    connection = session.connection()
    raw = connection.connection.driver_connection
    if connection.dialect.name == "sqlite" and not raw.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield
        if owned:
            session.commit()
    except Exception:
        if owned:
            session.rollback()
        raise


class SwingRepository:
    def __init__(self, session: Session):
        self.session = session

    def _require_schema(self):
        if not inspect(self.session.connection()).has_table("swing_source_versions"):
            raise ValueError("swing_schema_not_migrated: 請先備份並執行 Alembic 遷移")

    @staticmethod
    def _source_result(row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item["payload_json"])
        for field in ("public_at", "obtained_at", "revised_at", "verified_at", "created_at"):
            item[field] = from_us(item[f"{field}_us"])
        return item

    def append_source(self, source: dict) -> dict:
        self._require_schema()
        payload = dict(source.get("payload") or {})
        payload.setdefault("schema_version", 1)
        obtained = to_us(source.get("obtained_at") or source.get("obtained_at_us") or now_us())
        item = {"source": str(source["source"]), "kind": str(source["kind"]),
                "source_key": str(source["source_key"]), "symbol": source.get("symbol"),
                "data_period": str(source.get("data_period") or payload.get("period") or ""),
                "obtained_at_us": obtained, "quality": source.get("quality", "unverified"),
                "supersedes_id": source.get("supersedes_id"), "payload_json": canonical_json(payload),
                "payload_hash": content_hash(payload)}
        for field in ("public_at", "revised_at", "verified_at"):
            val = source.get(field, source.get(f"{field}_us"))
            item[f"{field}_us"] = to_us(val) if val is not None else None
        if item["quality"] == "verified" and (item["public_at_us"] is None or item["verified_at_us"] is None):
            raise ValueError("verified_source_requires_publication_and_verification")
        if item["quality"] == "unknown_publication" and item["public_at_us"] is not None:
            raise ValueError("unknown_publication_requires_null_time")
        # Version identity excludes acquisition/verification time: repeated retrieval of identical
        # source facts must return the first immutable observation rather than manufacture revisions.
        identity = {key: value for key, value in item.items() if key not in {"obtained_at_us", "verified_at_us", "payload_json"}}
        item["version_key"] = content_hash(identity)
        existing = self.session.execute(text("SELECT * FROM swing_source_versions WHERE version_key=:key"), {"key": item["version_key"]}).mappings().first()
        if existing:
            return self._source_result(existing)
        item["created_at_us"] = max(now_us(), obtained, item["verified_at_us"] or 0)
        names = list(item)
        self.session.execute(text(f"INSERT INTO swing_source_versions ({','.join(names)}) VALUES ({','.join(':'+k for k in names)})"), item)
        row = self.session.execute(text("SELECT * FROM swing_source_versions WHERE version_key=:key"), {"key": item["version_key"]}).mappings().one()
        return self._source_result(row)

    def eligible_sources(self, kind: str, symbol: str | None, cutoff_at, mode: str = "historical_exploratory") -> list[dict]:
        self._require_schema()
        if mode not in {"historical_exploratory", "prospective"}:
            raise ValueError("invalid_research_mode")
        cutoff = to_us(cutoff_at)
        rows = self.session.execute(text("""
            SELECT s.* FROM swing_source_versions s
            WHERE s.kind=:kind AND (:symbol IS NULL OR s.symbol=:symbol)
              AND s.public_at_us<=:cutoff AND (s.revised_at_us IS NULL OR s.revised_at_us<=:cutoff)
              AND (:mode='historical_exploratory' OR (s.obtained_at_us<=:cutoff AND s.verified_at_us<=:cutoff))
            ORDER BY COALESCE(s.revised_at_us,s.public_at_us) DESC,s.id DESC
        """), {"kind": kind, "symbol": symbol, "cutoff": cutoff, "mode": mode}).mappings().all()
        chosen: dict[tuple, dict] = {}
        # Pick latest publicly effective revision *before* applying quality/repair filters;
        # an invalid revision cannot resurrect an old, seemingly verified version.
        for row in rows:
            key = (row["source"], row["source_key"])
            if key in chosen:
                continue
            chosen[key] = dict(row)
        result = []
        for row in chosen.values():
            if row["quality"] != "verified":
                continue
            repair = self.session.execute(text("SELECT action FROM swing_data_repairs WHERE old_version_id=:id AND stage='applied' ORDER BY created_at_us DESC,id DESC LIMIT 1"), {"id": row["id"]}).scalar()
            if repair in {"repair", "quarantine"}:
                continue
            if content_hash(json.loads(row["payload_json"])) != row["payload_hash"]:
                raise ValueError("source_payload_hash_mismatch")
            result.append(self._source_result(row))
        return sorted(result, key=lambda row: (row["data_period"], row["id"]))

    def _append_repair(self, item: dict) -> dict:
        digest = content_hash({k: v for k, v in item.items() if k != "created_at_us"})
        previous = self.session.execute(text("SELECT * FROM swing_data_repairs WHERE repair_key=:repair_key"), item).mappings().first()
        if previous:
            if previous["payload_hash"] != digest:
                raise ValueError("idempotency_conflict")
            return {**dict(previous), "audit": json.loads(previous["audit_json"])}
        item = {**item, "payload_hash": digest, "created_at_us": now_us()}
        self.session.execute(text(f"INSERT INTO swing_data_repairs ({','.join(item)}) VALUES ({','.join(':'+k for k in item)})"), item)
        row = self.session.execute(text("SELECT * FROM swing_data_repairs WHERE repair_key=:repair_key"), item).mappings().one()
        return {**dict(row), "audit": json.loads(row["audit_json"])}

    def preview_repair(self, *, repair_key: str, old_version_id: int, action: str, actor: str, replacement: dict | None = None, reason: str) -> dict:
        self._require_schema()
        if action not in {"repair", "quarantine", "release"} or not actor.strip() or not reason.strip():
            raise ValueError("invalid_repair_request")
        old = self.session.execute(text("SELECT * FROM swing_source_versions WHERE id=:id"), {"id": old_version_id}).mappings().one()
        audit = {"schema_version": 1, "before_hash": old["payload_hash"], "before": json.loads(old["payload_json"]),
                 "replacement": replacement, "reason": reason}
        return self._append_repair({"repair_key": repair_key, "preview_id": None, "stage": "preview", "action": action,
                                    "old_version_id": old_version_id, "new_version_id": None, "actor": actor, "audit_json": canonical_json(audit)})

    def apply_repair(self, *, preview_id: int, repair_key: str, actor: str, expected_hash: str) -> dict:
        # Savepoint protects a caller-owned transaction from a late idempotency
        # conflict after a replacement source was appended. Core writes only:
        # do not flush unrelated caller ORM changes to create this savepoint.
        with immediate_transaction(self.session), self.session.connection().begin_nested():
            preview = self.session.execute(text("SELECT * FROM swing_data_repairs WHERE id=:id AND stage='preview'"), {"id": preview_id}).mappings().one()
            audit = json.loads(preview["audit_json"])
            if not actor.strip() or expected_hash != audit["before_hash"]:
                raise ValueError("repair_before_hash_mismatch")
            old = self.session.execute(text("SELECT * FROM swing_source_versions WHERE id=:id"), {"id": preview["old_version_id"]}).mappings().one()
            if content_hash(json.loads(old["payload_json"])) != expected_hash:
                raise ValueError("repair_before_hash_mismatch")
            new_id = None
            if preview["action"] == "repair":
                replacement = audit.get("replacement")
                if not replacement:
                    raise ValueError("repair_requires_replacement")
                new_id = self.append_source({**replacement, "supersedes_id": old["id"]})["id"]
            item = {"repair_key": repair_key, "preview_id": preview_id, "stage": "applied", "action": preview["action"],
                    "old_version_id": old["id"], "new_version_id": new_id, "actor": actor, "audit_json": canonical_json(audit)}
            result = self._append_repair(item)
            # Frozen manifests remain untouched; append a diagnostic to every affected experiment.
            for experiment_id in self.session.execute(text("SELECT DISTINCT experiment_id FROM swing_batch_sources WHERE source_version_id=:id"), {"id": old["id"]}).scalars():
                key = f"repair:{result['id']}"
                detail = {"schema_version": 1, "repair_id": result["id"], "source_version_id": old["id"], "frozen_inputs_preserved": True}
                exists = self.session.execute(text("SELECT id FROM swing_experiment_events WHERE experiment_id=:e AND event_key=:k"), {"e": experiment_id, "k": key}).first()
                if not exists:
                    self.session.execute(text("INSERT INTO swing_experiment_events (experiment_id,event_key,kind,actor,detail_json,payload_hash,created_at_us) VALUES (:e,:k,'incomplete',:actor,:detail,:hash,:at)"), {"e": experiment_id, "k": key, "actor": actor, "detail": canonical_json(detail), "hash": content_hash(detail), "at": now_us()})
            return result

    def preview_legacy_contamination(self) -> list[dict]:
        """舊快取沒有可信公布時間；只預覽，不更新、刪除或推定可信。"""
        findings = []
        inspector = inspect(self.session.connection())
        for table, kind in (("monthly_revenues", "monthly_revenue"), ("financial_statement_summaries", "financial_summary")):
            if not inspector.has_table(table):
                continue
            for row in self.session.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings():
                before = dict(row)
                reasons = ["legacy_publication_not_verified"]
                yoy = before.get("revenue_yoy_pct")
                if yoy is not None and 1900 <= float(yoy) <= 2200:
                    reasons.append("year_misread_as_growth")
                findings.append({"table": table, "kind": kind, "id": before["id"], "before": before, "before_hash": content_hash(before), "reasons": reasons})
        return findings

    def quarantine_legacy(self, finding: dict, *, repair_key: str, actor: str) -> dict:
        if finding["table"] not in {"monthly_revenues", "financial_statement_summaries"}:
            raise ValueError("unsupported_legacy_table")
        with immediate_transaction(self.session), self.session.connection().begin_nested():
            current = self.session.execute(text(f"SELECT * FROM {finding['table']} WHERE id=:id"), {"id": finding["id"]}).mappings().one()
            if content_hash(dict(current)) != finding["before_hash"]:
                raise ValueError("repair_before_hash_mismatch")
            version = self.append_source({"source": "legacy_cache", "kind": finding["kind"], "source_key": f"{finding['table']}:{finding['id']}",
                                          "symbol": current["symbol"], "data_period": current["period"], "quality": "unknown_publication",
                                          "payload": {"schema_version": 1, "legacy": dict(current), "reasons": finding["reasons"]}})
            preview = self.preview_repair(repair_key=repair_key+":preview", old_version_id=version["id"], action="quarantine", actor=actor, reason=",".join(finding["reasons"]))
            return self.apply_repair(preview_id=preview["id"], repair_key=repair_key, actor=actor, expected_hash=version["payload_hash"])
