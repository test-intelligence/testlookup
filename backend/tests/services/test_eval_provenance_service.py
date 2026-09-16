from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.agents.investigator import workflow as investigator_workflow
from app.models.postgres import AgentPipelineRun
from app.routers import ai_evaluation as router
from app.services import eval_provenance_service as provenance


class _ScalarResult:
    def __init__(self, values):
        self.values = list(values)

    def scalars(self):
        return self

    def all(self):
        return self.values

    def first(self):
        return self.values[0] if self.values else None


def _manifest() -> dict:
    return {
        "schema_version": 1,
        "change_id": "E9.7-test",
        "verdict": "pass",
        "prompt_versions": {"summary": "v1:abc"},
        "watched_sources": {"model_router.py": "123"},
        "attested_at": "2026-09-14T00:00:00+00:00",
    }


def test_manifest_checksum_is_canonical_and_archive_is_tamper_evident(tmp_path):
    current = tmp_path / "current.json"
    archive = tmp_path / "archive"
    first = _manifest()
    second = dict(reversed(list(first.items())))

    assert provenance.eval_manifest_checksum(first) == provenance.eval_manifest_checksum(second)
    stamped = provenance.stamp_and_archive_eval_manifest(
        first, current_path=current, archive_dir=archive
    )
    checksum = stamped["eval_manifest_checksum"]
    assert provenance.current_eval_manifest(
        current_path=current, archive_dir=archive
    ) == stamped

    archive_path = archive / f"{checksum}.json"
    corrupted = json.loads(archive_path.read_text(encoding="utf-8"))
    corrupted["change_id"] = "tampered"
    archive_path.write_text(json.dumps(corrupted), encoding="utf-8")
    with pytest.raises(provenance.EvalManifestError, match="content"):
        provenance.load_bundled_eval_manifest(checksum, archive_dir=archive)


@pytest.mark.asyncio
async def test_health_flags_missing_and_unknown_recent_run_manifests():
    checksum = provenance.current_eval_manifest_checksum()
    unknown = "f" * 64
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarResult([
                    {"eval_manifest_checksum": checksum},
                    {"eval_manifest_checksum": checksum},
                    {"eval_manifest_checksum": unknown},
                    {},
                ]),
                _ScalarResult([]),
            ]
        )
    )

    result = await provenance.eval_provenance_health(
        db, now=datetime(2026, 9, 14, tzinfo=timezone.utc)
    )

    assert result["resolved_runs"] == 2
    assert result["missing_checksum_count"] == 1
    assert result["unresolvable_run_count"] == 2
    assert result["unresolvable_checksums"] == [unknown]
    assert result["has_unresolvable_checksums"] is True


@pytest.mark.asyncio
async def test_manifest_route_resolves_the_bundled_attestation_and_404s_unknown():
    checksum = provenance.current_eval_manifest_checksum()
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult([])))

    result = await router.get_eval_manifest(
        checksum, current_user=SimpleNamespace(id=uuid.uuid4()), db=db
    )
    assert result["eval_manifest_checksum"] == checksum
    assert result["source"] == "bundled_attestation"
    assert result["status"] == "pass"

    with pytest.raises(HTTPException) as exc:
        await router.get_eval_manifest(
            "0" * 64, current_user=SimpleNamespace(id=uuid.uuid4()), db=db
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_investigator_pipeline_creation_freezes_the_eval_manifest(monkeypatch):
    added = []
    investigation = SimpleNamespace(
        parent_pipeline_run_id=None,
        parent_task_id=None,
        spawn_depth=0,
        failure_cluster_id=None,
        cluster_scope_sha256="scope",
        requested_by=uuid.uuid4(),
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalar_one(self):
            return self.value

    class _Session:
        def __init__(self):
            self.results = iter([None, investigation])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement):
            return _Result(next(self.results))

        def add(self, row):
            added.append(row)

        async def commit(self):
            return None

    monkeypatch.setattr(investigator_workflow, "AsyncSessionLocal", _Session)
    await investigator_workflow._create_stage_rows(
        str(uuid.uuid4()), str(uuid.uuid4()), {}, str(uuid.uuid4())
    )

    pipeline = next(row for row in added if isinstance(row, AgentPipelineRun))
    assert pipeline.execution_metadata["eval_manifest_checksum"] == (
        provenance.current_eval_manifest_checksum()
    )
    assert pipeline.requested_by == investigation.requested_by
