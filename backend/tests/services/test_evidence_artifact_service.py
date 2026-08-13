from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.canonical_json import canonical_json_bytes, stable_json_sha256
from app.services.evidence_artifact_service import (
    EvidenceAuthorizationError,
    capture_authorized_evidence_artifacts,
    tool_observation_attestation,
    verify_bundle_evidence_artifacts,
)


class _Result:
    def __init__(self, *, scalar=None, scalars=None):
        self._scalar = scalar
        self._scalars = scalars or []

    def scalar_one_or_none(self): return self._scalar
    def scalars(self): return self
    def all(self): return self._scalars


class _Insert:
    def __init__(self, _model): self.rows = []
    def values(self, rows):
        self.rows = rows
        return self
    def on_conflict_do_nothing(self, **_kwargs): return self


class _CaptureSession:
    def __init__(self, *, own_tests=True):
        self.calls = 0
        self.inserted = []
        self.own_tests = own_tests
        self.committed = False

    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return None

    async def execute(self, statement):
        self.calls += 1
        if self.calls == 1:
            return _Result(scalar=uuid.uuid4())
        if self.calls == 2:
            return _Result(scalars=[_TEST] if self.own_tests else [])
        if isinstance(statement, _Insert):
            self.inserted = statement.rows
            return _Result()
        return _Result(scalars=[SimpleNamespace(**row) for row in self.inserted])

    async def commit(self): self.committed = True


_PROJECT = uuid.uuid4()
_RUN = uuid.uuid4()
_PIPELINE = uuid.uuid4()
_TEST = uuid.uuid4()


def _state():
    excerpt = "password=[REDACTED] assertion failed"
    return {
        "project_id": str(_PROJECT),
        "test_run_id": str(_RUN),
        "pipeline_run_id": str(_PIPELINE),
        "analyses": {str(_TEST): {"evidence_references": [{
            "source": "fetch_allure_stacktrace",
            "kind": "tool_observation",
            "excerpt": "password=hunter2 assertion failed",
            "producer_attestation": tool_observation_attestation(
                project_id=str(_PROJECT), run_id=str(_RUN),
                pipeline_id=str(_PIPELINE), test_case_id=str(_TEST),
                source="fetch_allure_stacktrace", kind="tool_observation",
                excerpt=excerpt,
            ),
        }]}},
    }


@pytest.mark.asyncio
async def test_capture_is_tenant_bound_redacted_idempotent_and_non_actionable(monkeypatch):
    session = _CaptureSession()
    monkeypatch.setattr(
        "app.services.evidence_artifact_service.AsyncSessionLocal", lambda: session
    )
    monkeypatch.setattr(
        "app.services.evidence_artifact_service.insert", lambda model: _Insert(model)
    )

    references, errors = await capture_authorized_evidence_artifacts(_state())

    assert errors == []
    assert session.committed is True
    assert len(references) == 1
    reference = references[0]
    assert reference["authorization_status"] == "tenant_run_pipeline_verified"
    assert reference["producer_pipeline_run_id"] == str(_PIPELINE)
    assert reference["scope"] == {
        "project_id": str(_PROJECT),
        "test_run_id": str(_RUN),
        "test_case_id": str(_TEST),
    }
    assert reference["uri_or_ref"] is None
    assert "hunter2" not in reference["excerpt"]
    inserted = session.inserted[0]
    assert inserted["id"] == uuid.uuid5(
        uuid.NAMESPACE_URL, f"evidence:{inserted['idempotency_key']}"
    )
    assert inserted["content_size_bytes"] == len(canonical_json_bytes({
        "source": inserted["source_system"],
        "kind": inserted["artifact_type"],
        "uri_or_ref": None,
        "excerpt": inserted["summary_excerpt"],
    }))


@pytest.mark.asyncio
async def test_capture_rejects_foreign_test_ownership(monkeypatch):
    session = _CaptureSession(own_tests=False)
    monkeypatch.setattr(
        "app.services.evidence_artifact_service.AsyncSessionLocal", lambda: session
    )
    with pytest.raises(EvidenceAuthorizationError, match="does not belong"):
        await capture_authorized_evidence_artifacts(_state())


@pytest.mark.asyncio
async def test_model_fabricated_reference_is_not_captured():
    state = _state()
    state["analyses"][str(_TEST)]["evidence_references"] = [{
        "source": "splunk",
        "reference_id": str(uuid.uuid4()),
        "excerpt": "invented",
    }]
    references, errors = await capture_authorized_evidence_artifacts(state)
    assert references == []
    assert errors == ["evidence_producer_unverified"]


@pytest.mark.asyncio
async def test_allowlisted_forged_observation_without_valid_attestation_is_rejected():
    state = _state()
    state["analyses"][str(_TEST)]["evidence_references"][0][
        "producer_attestation"
    ] = "0" * 64
    references, errors = await capture_authorized_evidence_artifacts(state)
    assert references == []
    assert errors == ["evidence_producer_attestation_invalid"]


@pytest.mark.asyncio
async def test_independent_resolver_rejects_content_tamper(monkeypatch):
    content = {
        "source": "fetch_allure_stacktrace",
        "kind": "tool_observation",
        "uri_or_ref": None,
        "excerpt": "original",
    }
    checksum = stable_json_sha256(content)
    artifact_id = uuid.uuid4()
    row = SimpleNamespace(
        id=artifact_id,
        project_id=_PROJECT,
        run_id=_RUN,
        producer_pipeline_run_id=_PIPELINE,
        test_case_id=_TEST,
        integrity_status="verified",
        source_system=content["source"],
        artifact_type=content["kind"],
        summary_excerpt="tampered",
        uri_or_ref=None,
        content_sha256=checksum,
    )

    class _VerifySession:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def execute(self, _statement): return _Result(scalars=[row])

    monkeypatch.setattr(
        "app.services.evidence_artifact_service.AsyncSessionLocal",
        lambda: _VerifySession(),
    )
    result = await verify_bundle_evidence_artifacts({
        "project_id": str(_PROJECT),
        "test_run_id": str(_RUN),
        "pipeline_run_id": str(_PIPELINE),
        "evidence_refs": [{
            "artifact_id": str(artifact_id),
            "checksum_sha256": checksum,
            "producer_pipeline_run_id": str(_PIPELINE),
            "scope": {"test_case_id": str(_TEST)},
        }],
    })
    assert result["status"] == "failed"
    assert result["failures"] == ["artifact_integrity_mismatch"]
