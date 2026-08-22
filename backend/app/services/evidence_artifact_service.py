"""Capture and independently verify tenant-bound evidence artifacts."""
from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.postgres import AsyncSessionLocal
from app.core.config import settings
from app.models.postgres import (
    AgentPipelineRun,
    EvidenceArtifact,
    TestCase,
    TestRun,
)
from app.services.canonical_json import canonical_json_bytes, stable_json_sha256
from app.services.evidence_sanitizer import sanitize_reference_text

MAX_CAPTURED_ARTIFACTS = 500
MAX_CAPTURE_SCAN = 2000
# Local classifier provenance (F-17). Not an attested tool observation and not
# a candidate for the signed evidence store -- see the skip in the capture loop.
_CLASSIFIER_EVIDENCE_KIND = "classifier_input"

_AUTHORIZED_TOOLS = frozenset({
    "fetch_allure_stacktrace",
    "fetch_rest_api_payload",
    "query_splunk_logs",
    "check_test_flakiness",
    "analyze_openshift_pod_events",
    "recall_similar_failures",
})


class EvidenceAuthorizationError(RuntimeError):
    """The supplied workflow identity cannot authorize evidence."""


def tool_observation_attestation(
    *, project_id: str, run_id: str, pipeline_id: str, test_case_id: str,
    source: str, kind: str, excerpt: str,
) -> str:
    """HMAC-bind an executor observation to its original workflow identity."""
    key = settings.APP_SECRET_KEY or ""
    if not key:
        raise EvidenceAuthorizationError("APP_SECRET_KEY is required for evidence attestation")
    content_checksum = stable_json_sha256(
        _canonical_content(source=source, kind=kind, excerpt=excerpt)
    )
    message = stable_json_sha256({
        "project_id": str(project_id),
        "run_id": str(run_id),
        "pipeline_id": str(pipeline_id),
        "test_case_id": str(test_case_id),
        "source": source,
        "kind": kind,
        "content_sha256": content_checksum,
    })
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def _uuid(value: Any, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise EvidenceAuthorizationError(f"invalid {label}") from exc


def _canonical_content(*, source: str, kind: str, excerpt: str) -> dict[str, Any]:
    return {
        "source": source,
        "kind": kind,
        "uri_or_ref": None,
        "excerpt": excerpt,
    }


async def capture_authorized_evidence_artifacts(
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Persist bounded tool observations after proving full ownership scope.

    The captured row is the authority. Model-provided IDs and URIs are never
    accepted, and only the known server tool names emitted by the executor are
    eligible for capture.
    """
    analyses = state.get("analyses") if isinstance(state.get("analyses"), dict) else {}

    candidates: list[tuple[uuid.UUID, dict[str, Any]]] = []
    errors: list[str] = []
    scanned = 0
    for raw_test_id in sorted(analyses, key=str):
        analysis = analyses[raw_test_id]
        references = analysis.get("evidence_references") if isinstance(analysis, dict) else None
        if not references:
            continue
        if not isinstance(references, list):
            errors.append("evidence_reference_collection_invalid")
            continue
        try:
            test_id = _uuid(raw_test_id, "test_case_id")
        except EvidenceAuthorizationError:
            errors.append("evidence_test_identity_invalid")
            continue
        for raw in references:
            if scanned >= MAX_CAPTURE_SCAN:
                errors.append("evidence_capture_scan_limit_reached")
                break
            scanned += 1
            if not isinstance(raw, dict):
                errors.append("evidence_reference_invalid")
                continue
            source, _, _ = sanitize_reference_text(str(raw.get("source") or ""), limit=100)
            kind, _, _ = sanitize_reference_text(str(raw.get("kind") or ""), limit=100)
            excerpt, _, _ = sanitize_reference_text(str(raw.get("excerpt") or ""), limit=500)
            if kind == _CLASSIFIER_EVIDENCE_KIND:
                # F-17: local classifier provenance -- the error text a rule
                # matched on. It does NOT claim to be an attested artifact, so
                # skipping it is correct; flagging it would fill the decision
                # report with authorization errors for evidence that never
                # applied for authorization. The boundary this preserves is the
                # point: only HMAC-attested tool observations become signed
                # evidence, and nothing here relaxes that.
                continue
            if source not in _AUTHORIZED_TOOLS or kind != "tool_observation" or not excerpt:
                errors.append("evidence_producer_unverified")
                continue
            if not all(state.get(field) for field in (
                "project_id", "test_run_id", "pipeline_run_id"
            )):
                errors.append("evidence_producer_attestation_missing")
                continue
            expected_attestation = tool_observation_attestation(
                project_id=str(state["project_id"]),
                run_id=str(state["test_run_id"]),
                pipeline_id=str(state["pipeline_run_id"]),
                test_case_id=str(test_id),
                source=source,
                kind=kind,
                excerpt=excerpt,
            )
            if not hmac.compare_digest(
                str(raw.get("producer_attestation") or ""), expected_attestation
            ):
                errors.append("evidence_producer_attestation_invalid")
                continue
            candidates.append((test_id, {
                "source": source,
                "kind": kind,
                "excerpt": excerpt,
                "freshness": "current_run",
                "sensitivity": "restricted",
            }))
        if scanned >= MAX_CAPTURE_SCAN:
            break

    if len(candidates) > MAX_CAPTURED_ARTIFACTS:
        errors.append("evidence_artifact_limit_reached")
        candidates = candidates[:MAX_CAPTURED_ARTIFACTS]
    if not candidates:
        return [], sorted(set(errors))

    project_id = _uuid(state.get("project_id"), "project_id")
    run_id = _uuid(state.get("test_run_id"), "test_run_id")
    pipeline_id = _uuid(state.get("pipeline_run_id"), "pipeline_run_id")

    test_ids = {test_id for test_id, _ in candidates}
    async with AsyncSessionLocal() as db:
        identity = await db.execute(
            select(AgentPipelineRun.id)
            .join(TestRun, TestRun.id == AgentPipelineRun.test_run_id)
            .where(
                AgentPipelineRun.id == pipeline_id,
                AgentPipelineRun.test_run_id == run_id,
                TestRun.project_id == project_id,
            )
        )
        if identity.scalar_one_or_none() is None:
            raise EvidenceAuthorizationError(
                "evidence identity does not match a project-owned pipeline"
            )
        owned = await db.execute(
            select(TestCase.id).where(
                TestCase.test_run_id == run_id,
                TestCase.id.in_(test_ids),
            )
        )
        owned_ids = set(owned.scalars().all())
        if owned_ids != test_ids:
            raise EvidenceAuthorizationError("evidence test does not belong to the run")

        now = datetime.now(timezone.utc)
        rows: list[dict[str, Any]] = []
        expected_by_key: dict[str, dict[str, Any]] = {}
        for test_id, item in candidates:
            content = _canonical_content(
                source=item["source"], kind=item["kind"], excerpt=item["excerpt"]
            )
            checksum = stable_json_sha256(content)
            idempotency_key = stable_json_sha256({
                "project_id": str(project_id),
                "run_id": str(run_id),
                "pipeline_id": str(pipeline_id),
                "test_case_id": str(test_id),
                "content_sha256": checksum,
            })
            artifact_id = uuid.uuid5(uuid.NAMESPACE_URL, f"evidence:{idempotency_key}")
            row = {
                "id": artifact_id,
                "project_id": project_id,
                "run_id": run_id,
                "producer_pipeline_run_id": pipeline_id,
                "test_case_id": test_id,
                "cluster_id": None,
                "artifact_type": item["kind"],
                "source_system": item["source"],
                "source_version": "react_tool_observation_v1",
                "uri_or_ref": None,
                "summary_excerpt": item["excerpt"],
                "relevance_score": None,
                "schema_version": 2,
                "content_sha256": checksum,
                "content_size_bytes": len(canonical_json_bytes(content)),
                "media_type": "application/json",
                "sensitivity": item["sensitivity"],
                "freshness": item["freshness"],
                "observed_at": now,
                "integrity_status": "verified",
                "retention_class": "artifacts",
                "idempotency_key": idempotency_key,
            }
            rows.append(row)
            expected_by_key[idempotency_key] = row
        await db.execute(
            insert(EvidenceArtifact)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        await db.commit()
        stored_result = await db.execute(
            select(EvidenceArtifact).where(
                EvidenceArtifact.idempotency_key.in_(expected_by_key)
            )
        )
        stored = list(stored_result.scalars().all())

    if len(stored) != len(expected_by_key):
        raise EvidenceAuthorizationError("not all evidence artifacts were durably captured")
    projections: list[dict[str, Any]] = []
    for artifact in stored:
        expected = expected_by_key[str(artifact.idempotency_key)]
        immutable_match = all(
            getattr(artifact, field) == expected[field]
            for field in (
                "project_id", "run_id", "producer_pipeline_run_id", "test_case_id",
                "artifact_type", "source_system", "summary_excerpt", "content_sha256",
                "content_size_bytes", "sensitivity", "freshness", "integrity_status",
            )
        )
        if not immutable_match or artifact.uri_or_ref is not None:
            raise EvidenceAuthorizationError("stored evidence artifact does not match capture")
        evidence_id = stable_json_sha256({
            "project_id": str(project_id),
            "test_run_id": str(run_id),
            "test_case_id": str(artifact.test_case_id),
            "checksum": artifact.content_sha256,
        })
        projections.append({
            "schema_version": 2,
            "artifact_id": str(artifact.id),
            "evidence_id": evidence_id,
            "kind": artifact.artifact_type,
            "source": artifact.source_system,
            "scope": {
                "project_id": str(project_id),
                "test_run_id": str(run_id),
                "test_case_id": str(artifact.test_case_id),
            },
            "producer_pipeline_run_id": str(pipeline_id),
            "checksum_sha256": artifact.content_sha256,
            "freshness": artifact.freshness,
            "sensitivity": artifact.sensitivity,
            "authorization_status": "tenant_run_pipeline_verified",
            "uri_or_ref": None,
            "excerpt": artifact.summary_excerpt or "",
        })
    projections.sort(key=lambda item: (item["scope"]["test_case_id"], item["artifact_id"]))
    return projections, sorted(set(errors))


async def verify_bundle_evidence_artifacts(bundle: dict[str, Any]) -> dict[str, Any]:
    """Re-resolve every signed artifact using exact tenant/run/pipeline scope."""
    references = bundle.get("evidence_refs") if isinstance(bundle, dict) else None
    if not references:
        return {"status": "passed", "failures": [], "verified_count": 0}
    if not isinstance(references, list):
        return {"status": "failed", "failures": ["artifact_reference_set_invalid"]}
    try:
        project_id = _uuid(bundle.get("project_id"), "project_id")
        run_id = _uuid(bundle.get("test_run_id"), "test_run_id")
        pipeline_id = _uuid(bundle.get("pipeline_run_id"), "pipeline_run_id")
        artifact_ids = [_uuid(item.get("artifact_id"), "artifact_id") for item in references]
    except (EvidenceAuthorizationError, AttributeError):
        return {"status": "failed", "failures": ["artifact_reference_identity_invalid"]}
    if len(set(artifact_ids)) != len(artifact_ids):
        return {"status": "failed", "failures": ["artifact_reference_duplicate"]}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(EvidenceArtifact).where(
                EvidenceArtifact.id.in_(artifact_ids),
                EvidenceArtifact.project_id == project_id,
                EvidenceArtifact.run_id == run_id,
                EvidenceArtifact.producer_pipeline_run_id == pipeline_id,
                EvidenceArtifact.integrity_status == "verified",
            )
        )
        rows = {row.id: row for row in result.scalars().all()}
    failures: list[str] = []
    for reference, artifact_id in zip(references, artifact_ids):
        row = rows.get(artifact_id)
        if row is None:
            failures.append("artifact_unresolved")
            continue
        content = _canonical_content(
            source=row.source_system,
            kind=row.artifact_type,
            excerpt=row.summary_excerpt or "",
        )
        checksum = stable_json_sha256(content)
        if (
            row.uri_or_ref is not None
            or checksum != row.content_sha256
            or checksum != reference.get("checksum_sha256")
            or row.source_system != reference.get("source")
            or row.artifact_type != reference.get("kind")
            or (row.summary_excerpt or "") != reference.get("excerpt")
            or row.freshness != reference.get("freshness")
            or row.sensitivity != reference.get("sensitivity")
            or reference.get("authorization_status")
            != "tenant_run_pipeline_verified"
            or (reference.get("scope") or {}).get("project_id") != str(project_id)
            or (reference.get("scope") or {}).get("test_run_id") != str(run_id)
            or str(row.test_case_id) != (reference.get("scope") or {}).get("test_case_id")
            or str(row.producer_pipeline_run_id)
            != reference.get("producer_pipeline_run_id")
        ):
            failures.append("artifact_integrity_mismatch")
            continue
        expected_evidence_id = stable_json_sha256({
            "project_id": str(project_id),
            "test_run_id": str(run_id),
            "test_case_id": str(row.test_case_id),
            "checksum": checksum,
        })
        if reference.get("evidence_id") != expected_evidence_id:
            failures.append("artifact_integrity_mismatch")
    return {
        "status": "passed" if not failures else "failed",
        "failures": sorted(set(failures)),
        "verified_count": len(rows),
    }
