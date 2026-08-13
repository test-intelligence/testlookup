from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

from app.services.decision_evidence_snapshot import (
    EvidenceSnapshotConflict,
    build_decision_evidence_snapshot,
    canonical_json_bytes,
    load_decision_evidence_snapshot,
    persist_decision_evidence_snapshot,
    stable_json_sha256,
    validate_decision_evidence_snapshot,
    _verify_snapshot_identity,
)
from app.core.config import settings


def _state():
    return {
        "project_id": str(uuid.uuid4()),
        "test_run_id": str(uuid.uuid4()),
        "pipeline_run_id": str(uuid.uuid4()),
        "failed_test_ids": ["a"],
        "analyses": {"a": {}},
        "completed_stages": ["release_risk"],
        "skipped_stages": [],
        "agent_contracts": {"release_risk": {"schema_version": 1}},
        "release_decision": {
            "recommendation": "NO_GO",
            "risk_score": 80,
            "composite_risk": 80.0,
            "dimension_scores": {"user_impact": 80.0},
            "score_model_version": 1,
            "input_snapshot": {"pass_rate": 50.0},
            "policy_evaluation": {"recommendation": "NO_GO"},
        },
    }


def test_snapshot_hash_and_hmac_validate_after_round_trip():
    snapshot = build_decision_evidence_snapshot(_state(), {"status": "complete"})
    round_tripped = deepcopy(snapshot)

    assert validate_decision_evidence_snapshot(round_tripped) == []
    assert snapshot["schema_version"] == 3
    assert len(snapshot["content_sha256"]) == 64
    assert len(snapshot["signature_hmac_sha256"]) == 64
    assert snapshot["run_evidence_bundle"]["metric_snapshot"]["values"][
        "failure_cluster_count"
    ] == 0


def test_snapshot_binds_decision_contract_without_volatile_timestamp():
    state = _state()
    state["agent_contracts"]["decision_report"] = {
        "schema_version": 1,
        "agent_name": "decision_report",
        "confidence_score": 95,
        "fallback_used": False,
        "decision_reason": "terminal_synthesis_complete",
        "output_keys": ["decision_intelligence"],
        "evidence_refs": [],
        "generated_at": "2026-08-11T00:00:00+00:00",
    }
    snapshot = build_decision_evidence_snapshot(state, {"status": "complete"})
    contract = snapshot["verification_context"]["agent_contracts"]["decision_report"]
    assert contract["agent_name"] == "decision_report"
    assert "generated_at" not in contract


def test_snapshot_redacts_structured_secret_keys_across_decision_and_policy():
    state = _state()
    state["release_decision"]["input_snapshot"] = {
        "api_key": "short-secret",
        "authorization": "Basic abc",
    }
    snapshot = build_decision_evidence_snapshot(
        state,
        {"release_decision": {"password": "[REDACTED]"}},
    )
    serialized = str(snapshot)
    assert "short-secret" not in serialized
    assert "Basic abc" not in serialized
    assert snapshot["release_policy_inputs"]["input_snapshot"]["api_key"] == "[REDACTED]"


def test_snapshot_fails_closed_instead_of_truncating_aggregate_authority():
    with pytest.raises(ValueError, match="exceeds persistence bounds"):
        build_decision_evidence_snapshot(
            _state(),
            {"deep_findings": list(range(20_001))},
        )


def test_snapshot_rejects_unsanitized_structured_secret_in_canonical_decision():
    with pytest.raises(ValueError, match="not fully sanitized"):
        build_decision_evidence_snapshot(
            _state(),
            {"release_decision": {"password": "hunter2"}},
        )


def test_snapshot_detects_content_signature_and_identity_tampering():
    snapshot = build_decision_evidence_snapshot(_state(), {"status": "complete"})

    content_tampered = deepcopy(snapshot)
    content_tampered["canonical_decision"]["status"] = "degraded"
    assert "snapshot_content_hash_mismatch" in validate_decision_evidence_snapshot(
        content_tampered
    )

    signature_tampered = deepcopy(snapshot)
    signature_tampered["signature_hmac_sha256"] = "0" * 64
    assert "snapshot_signature_mismatch" in validate_decision_evidence_snapshot(
        signature_tampered
    )

    identity_tampered = deepcopy(snapshot)
    identity_tampered["project_id"] = str(uuid.uuid4())
    assert "snapshot_identity_mismatch" in validate_decision_evidence_snapshot(
        identity_tampered
    )

    key_id_tampered = deepcopy(snapshot)
    key_id_tampered["signature_key_id"] = "0" * 16
    assert "snapshot_signature_key_unknown" in validate_decision_evidence_snapshot(
        key_id_tampered
    )

    bundle_tampered = deepcopy(snapshot)
    bundle_tampered["run_evidence_bundle"]["metric_snapshot"]["values"][
        "total_tests"
    ] = 999
    bundle_failures = validate_decision_evidence_snapshot(bundle_tampered)
    assert "run_evidence_bundle_schema_invalid" in bundle_failures


def test_malformed_snapshot_hash_returns_named_failure_without_raising():
    snapshot = build_decision_evidence_snapshot(_state(), {"status": "complete"})
    snapshot["content_sha256"] = "☃"
    failures = validate_decision_evidence_snapshot(snapshot)
    assert "snapshot_content_hash_invalid" in failures
    assert "snapshot_signature_key_unknown" in failures


def test_previous_key_validates_snapshot_during_rotation(monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "old-snapshot-key")
    monkeypatch.setattr(settings, "APP_SECRET_KEY_PREVIOUS", None)
    snapshot = build_decision_evidence_snapshot(_state(), {"status": "complete"})

    monkeypatch.setattr(settings, "APP_SECRET_KEY", "new-snapshot-key")
    monkeypatch.setattr(settings, "APP_SECRET_KEY_PREVIOUS", "old-snapshot-key")
    assert validate_decision_evidence_snapshot(snapshot) == []


@pytest.mark.asyncio
async def test_real_loader_accepts_valid_v2_and_rejects_missing_bundle(monkeypatch):
    state = _state()
    snapshot = build_decision_evidence_snapshot(state, {"status": "complete"})
    collection = _Collection()
    collection.docs[snapshot["_id"]] = deepcopy(snapshot)
    monkeypatch.setattr(
        "app.services.decision_evidence_snapshot._verify_snapshot_identity", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.decision_evidence_snapshot.get_mongo_db",
        lambda: _Mongo(collection),
    )

    loaded = await load_decision_evidence_snapshot(
        test_run_id=state["test_run_id"],
        pipeline_run_id=state["pipeline_run_id"],
        project_id=state["project_id"],
    )
    assert loaded["schema_version"] == 3

    collection.docs[snapshot["_id"]].pop("run_evidence_bundle")
    with pytest.raises(ValueError, match="snapshot_run_evidence_bundle_missing"):
        await load_decision_evidence_snapshot(
            test_run_id=state["test_run_id"],
            pipeline_run_id=state["pipeline_run_id"],
            project_id=state["project_id"],
        )


def test_canonical_json_rejects_non_finite_and_unsupported_values():
    with pytest.raises(ValueError, match="NaN"):
        canonical_json_bytes({"value": float("nan")})
    with pytest.raises(TypeError, match="unsupported"):
        canonical_json_bytes({"value": object()})


def test_canonical_json_normalizes_uuid_and_datetime_stably():
    value = {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "at": datetime(2026, 8, 11, tzinfo=timezone.utc),
    }
    normalized = {
        "id": "00000000-0000-0000-0000-000000000001",
        "at": "2026-08-11T00:00:00+00:00",
    }
    assert stable_json_sha256(value) == stable_json_sha256(normalized)


class _Collection:
    def __init__(self):
        self.docs = {}

    async def insert_one(self, document):
        if document["_id"] in self.docs:
            raise DuplicateKeyError("duplicate")
        self.docs[document["_id"]] = deepcopy(document)

    async def find_one(self, query):
        return next(
            (
                deepcopy(doc)
                for doc in self.docs.values()
                if all(doc.get(key) == value for key, value in query.items())
            ),
            None,
        )


class _Mongo:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, _name):
        return self.collection


@pytest.mark.asyncio
async def test_equal_retry_is_idempotent_and_divergent_retry_fails_closed(monkeypatch):
    state = _state()
    collection = _Collection()
    monkeypatch.setattr(
        "app.services.decision_evidence_snapshot._verify_snapshot_identity",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.decision_evidence_snapshot.get_mongo_db",
        lambda: _Mongo(collection),
    )

    first = await persist_decision_evidence_snapshot(state, {"status": "complete"})
    second = await persist_decision_evidence_snapshot(state, {"status": "complete"})
    assert first["content_sha256"] == second["content_sha256"]

    with pytest.raises(EvidenceSnapshotConflict):
        await persist_decision_evidence_snapshot(state, {"status": "degraded"})


class _IdentityResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _IdentitySession:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return _IdentityResult(self.value)


@pytest.mark.asyncio
async def test_snapshot_identity_fails_closed_for_wrong_project_or_pipeline(monkeypatch):
    state = _state()
    monkeypatch.setattr(
        "app.services.decision_evidence_snapshot.AsyncSessionLocal",
        lambda: _IdentitySession(None),
    )
    with pytest.raises(LookupError, match="project-owned"):
        await _verify_snapshot_identity(
            project_id=state["project_id"],
            test_run_id=state["test_run_id"],
            pipeline_run_id=state["pipeline_run_id"],
        )

    with pytest.raises(LookupError, match="invalid"):
        await _verify_snapshot_identity(
            project_id="wrong-project",
            test_run_id=state["test_run_id"],
            pipeline_run_id=state["pipeline_run_id"],
        )
