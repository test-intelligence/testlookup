"""Immutable durable evidence snapshots for terminal decision verification."""
from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError
from sqlalchemy import select

from app.core.config import settings
from app.db.mongo import Collections, get_mongo_db
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AgentPipelineRun, TestRun
from app.services.canonical_json import canonical_json_bytes as _canonical_json_bytes
from app.services.canonical_json import stable_json_sha256 as _stable_json_sha256
from app.services.run_evidence_bundle import (
    build_run_evidence_bundle,
    validate_run_evidence_bundle,
)
from app.services.evidence_sanitizer import sanitize_persistence_payload

SNAPSHOT_SCHEMA_VERSION = 3
SNAPSHOT_SIGNATURE_VERSION = 1
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceSnapshotConflict(RuntimeError):
    """Raised when an existing immutable snapshot differs from a new write."""


def canonical_json_bytes(value: Any) -> bytes:
    return _canonical_json_bytes(value, max_bytes=MAX_SNAPSHOT_BYTES)


def stable_json_sha256(value: Any) -> str:
    return _stable_json_sha256(value, max_bytes=MAX_SNAPSHOT_BYTES)


def _signature(content_sha256: str, key: str) -> str:
    return hmac.new(
        key.encode("utf-8"),
        content_sha256.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _signature_fields(content_sha256: str) -> dict[str, Any]:
    key = settings.APP_SECRET_KEY or ""
    if not key:
        raise RuntimeError("APP_SECRET_KEY is required to sign decision evidence snapshots")
    return {
        "signature_version": SNAPSHOT_SIGNATURE_VERSION,
        "signature_key_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
        "signature_hmac_sha256": _signature(content_sha256, key),
    }


def _snapshot_id(project_id: str, test_run_id: str, pipeline_run_id: str) -> str:
    return stable_json_sha256({
        "project_id": project_id,
        "test_run_id": test_run_id,
        "pipeline_run_id": pipeline_run_id,
    })


def build_decision_evidence_snapshot(
    state: dict[str, Any], canonical_decision: dict[str, Any]
) -> dict[str, Any]:
    """Build the minimal durable authority used by the terminal critic."""
    release = state.get("release_decision")
    release = release if isinstance(release, dict) else {}
    run_evidence_bundle = build_run_evidence_bundle(state)
    known_ids = list(run_evidence_bundle["failed_test_ids"])
    safe_decision, decision_stats = sanitize_persistence_payload(canonical_decision)
    if (
        decision_stats.omitted_items
        or decision_stats.truncated_strings
        or safe_decision != canonical_decision
    ):
        raise ValueError(
            "canonical decision exceeds persistence bounds or is not fully sanitized"
        )
    safe_contracts, contract_stats = sanitize_persistence_payload(
        state.get("agent_contracts") or {}
    )
    if contract_stats.omitted_items or contract_stats.truncated_strings:
        raise ValueError("agent contracts exceed snapshot persistence bounds")
    if isinstance(safe_contracts, dict):
        for contract in safe_contracts.values():
            if isinstance(contract, dict):
                # Wall-clock metadata is deliberately excluded so a retry of
                # the same pipeline produces identical signed authority.
                contract.pop("generated_at", None)
    release_policy_inputs, release_stats = sanitize_persistence_payload({
        "recommendation": release.get("recommendation"),
        "risk_score": release.get("risk_score"),
        "composite_risk": release.get("composite_risk"),
        "dimension_scores": deepcopy(release.get("dimension_scores") or {}),
        "score_model_version": release.get("score_model_version"),
        "input_snapshot": deepcopy(release.get("input_snapshot") or {}),
        "policy_id": str(release.get("policy_id")) if release.get("policy_id") else None,
        "policy_evaluation": deepcopy(release.get("policy_evaluation") or {}),
    })
    if release_stats.omitted_items or release_stats.truncated_strings:
        raise ValueError("release policy inputs exceed snapshot persistence bounds")
    body = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "test_run_id": str(state["test_run_id"]),
        "pipeline_run_id": str(state["pipeline_run_id"]),
        "project_id": str(state["project_id"]),
        "canonical_decision": safe_decision,
        "run_evidence_bundle": run_evidence_bundle,
        "verification_context": {
            "known_test_ids": known_ids,
            "completed_stages": [str(item) for item in (state.get("completed_stages") or [])],
            "skipped_stages": [str(item) for item in (state.get("skipped_stages") or [])],
            "agent_contracts": safe_contracts,
        },
        "release_policy_inputs": release_policy_inputs,
    }
    content_sha256 = stable_json_sha256(body)
    return {
        "_id": _snapshot_id(body["project_id"], body["test_run_id"], body["pipeline_run_id"]),
        **body,
        "content_sha256": content_sha256,
        **_signature_fields(content_sha256),
        "created_at": datetime.now(timezone.utc),
    }


def validate_decision_evidence_snapshot(snapshot: dict[str, Any]) -> list[str]:
    """Return validation failures without mutating the durable document."""
    failures: list[str] = []
    if snapshot.get("schema_version") not in {2, SNAPSHOT_SCHEMA_VERSION}:
        failures.append("snapshot_schema_unsupported")
    body = {
        key: deepcopy(value)
        for key, value in snapshot.items()
        if key not in {
            "_id",
            "content_sha256",
            "signature_version",
            "signature_key_id",
            "signature_hmac_sha256",
            "created_at",
        }
    }
    content_sha256 = snapshot.get("content_sha256")
    content_hash_valid = (
        isinstance(content_sha256, str) and _SHA256_RE.fullmatch(content_sha256) is not None
    )
    if not content_hash_valid:
        failures.append("snapshot_content_hash_invalid")
    try:
        if content_sha256 != stable_json_sha256(body):
            failures.append("snapshot_content_hash_mismatch")
    except (TypeError, ValueError):
        failures.append("snapshot_body_not_canonical")
    if snapshot.get("_id") != _snapshot_id(
        str(snapshot.get("project_id")),
        str(snapshot.get("test_run_id")),
        str(snapshot.get("pipeline_run_id")),
    ):
        failures.append("snapshot_identity_mismatch")
    if snapshot.get("signature_version") != SNAPSHOT_SIGNATURE_VERSION:
        failures.append("snapshot_signature_unsupported")
    signatures_by_key_id = (
        {
            hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]: _signature(
                content_sha256, key
            )
            for key in (settings.APP_SECRET_KEY, settings.APP_SECRET_KEY_PREVIOUS)
            if key
        }
        if content_hash_valid
        else {}
    )
    signature_key_id = str(snapshot.get("signature_key_id") or "")
    expected_signature = signatures_by_key_id.get(signature_key_id)
    if expected_signature is None:
        failures.append("snapshot_signature_key_unknown")
    elif not hmac.compare_digest(
        str(snapshot.get("signature_hmac_sha256") or ""), expected_signature
    ):
        failures.append("snapshot_signature_mismatch")
    if not isinstance(snapshot.get("canonical_decision"), dict):
        failures.append("snapshot_canonical_decision_missing")
    else:
        try:
            safe_decision, _ = sanitize_persistence_payload(snapshot["canonical_decision"])
            if safe_decision != snapshot["canonical_decision"]:
                failures.append("snapshot_canonical_decision_not_sanitized")
        except (TypeError, ValueError):
            failures.append("snapshot_canonical_decision_not_sanitized")
    run_evidence_bundle = snapshot.get("run_evidence_bundle")
    if not isinstance(run_evidence_bundle, dict):
        failures.append("snapshot_run_evidence_bundle_missing")
    else:
        failures.extend(validate_run_evidence_bundle(run_evidence_bundle))
    if not isinstance(snapshot.get("verification_context"), dict):
        failures.append("snapshot_verification_context_missing")
    return failures


async def _verify_snapshot_identity(
    *, project_id: str, test_run_id: str, pipeline_run_id: str
) -> None:
    """Prove the project, test run, and pipeline IDs belong together."""
    try:
        project_uuid = uuid.UUID(str(project_id))
        run_uuid = uuid.UUID(str(test_run_id))
        pipeline_uuid = uuid.UUID(str(pipeline_run_id))
    except ValueError as exc:
        raise LookupError("invalid snapshot identity") from exc
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentPipelineRun.id)
            .join(TestRun, TestRun.id == AgentPipelineRun.test_run_id)
            .where(
                AgentPipelineRun.id == pipeline_uuid,
                AgentPipelineRun.test_run_id == run_uuid,
                TestRun.project_id == project_uuid,
            )
        )
        if result.scalar_one_or_none() is None:
            raise LookupError("snapshot identity does not match a project-owned pipeline")


async def persist_decision_evidence_snapshot(
    state: dict[str, Any], canonical_decision: dict[str, Any]
) -> dict[str, Any]:
    """Create once and reject any later attempt to change the same snapshot key."""
    snapshot = build_decision_evidence_snapshot(state, canonical_decision)
    await _verify_snapshot_identity(
        project_id=snapshot["project_id"],
        test_run_id=snapshot["test_run_id"],
        pipeline_run_id=snapshot["pipeline_run_id"],
    )
    query = {
        "project_id": snapshot["project_id"],
        "test_run_id": snapshot["test_run_id"],
        "pipeline_run_id": snapshot["pipeline_run_id"],
    }
    db = get_mongo_db()
    try:
        await db[Collections.DECISION_EVIDENCE_SNAPSHOTS].insert_one(snapshot)
    except DuplicateKeyError:
        pass
    stored = await db[Collections.DECISION_EVIDENCE_SNAPSHOTS].find_one(query)
    if not stored or stored.get("content_sha256") != snapshot["content_sha256"]:
        raise EvidenceSnapshotConflict(
            "immutable decision evidence snapshot already exists with different content"
        )
    stored_failures = validate_decision_evidence_snapshot(stored)
    if stored_failures:
        raise EvidenceSnapshotConflict(
            "stored decision evidence snapshot failed validation: "
            + ",".join(stored_failures)
        )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "test_run_id": snapshot["test_run_id"],
        "pipeline_run_id": snapshot["pipeline_run_id"],
        "content_sha256": snapshot["content_sha256"],
        "status": "persisted",
    }


async def load_decision_evidence_snapshot(
    *, test_run_id: str, pipeline_run_id: str, project_id: str
) -> dict[str, Any]:
    """Load a snapshot using all tenant/run identity fields."""
    await _verify_snapshot_identity(
        project_id=project_id,
        test_run_id=test_run_id,
        pipeline_run_id=pipeline_run_id,
    )
    db = get_mongo_db()
    snapshot = await db[Collections.DECISION_EVIDENCE_SNAPSHOTS].find_one({
        "test_run_id": str(test_run_id),
        "pipeline_run_id": str(pipeline_run_id),
        "project_id": str(project_id),
    })
    if not snapshot:
        raise LookupError("decision evidence snapshot not found")
    failures = validate_decision_evidence_snapshot(snapshot)
    if failures:
        raise ValueError(",".join(failures))
    return snapshot
