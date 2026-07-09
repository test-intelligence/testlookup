"""
Regression tests for the CI-context contract (PMF backlog US-4.3a,
migration 0101): repo/PR/actor/run-URL metadata on runs — the anchor for
PR summary comments and commit attribution.

Covers: schema acceptance + bounds on both ingest paths, the
extra_metadata["ci_context"] fold for live sessions (explicit metadata
keys win), the fill-if-null backfill in create_run_from_payload's reuse
path, and the migration's column/index contract.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.models.schemas import IngestPayload, LiveSessionCreate

_CI_FIELDS = {
    "ci_provider": "github_actions",
    "ci_repo": "acme/webapp",
    "pr_number": 421,
    "ci_actor": "octocat",
    "ci_run_url": "https://github.com/acme/webapp/actions/runs/99",
}


def _ingest_payload(**overrides) -> dict:
    base = {
        "project_id": str(uuid.uuid4()),
        "build_number": "ci-1234",
        "results": [{"test_name": "t", "suite_name": "s", "status": "PASSED"}],
    }
    base.update(overrides)
    return base


# ── Schema contract ────────────────────────────────────────────────────────


def test_ingest_payload_accepts_ci_context():
    payload = IngestPayload(**_ingest_payload(**_CI_FIELDS))
    for field, value in _CI_FIELDS.items():
        assert getattr(payload, field) == value


def test_ingest_payload_ci_context_is_optional():
    payload = IngestPayload(**_ingest_payload())
    for field in _CI_FIELDS:
        assert getattr(payload, field) is None


def test_ingest_payload_rejects_out_of_bounds_ci_context():
    with pytest.raises(ValidationError):
        IngestPayload(**_ingest_payload(pr_number=0))
    with pytest.raises(ValidationError):
        IngestPayload(**_ingest_payload(ci_provider="x" * 31))
    with pytest.raises(ValidationError):
        IngestPayload(**_ingest_payload(ci_run_url="h" * 1001))


def test_live_session_create_accepts_ci_context():
    payload = LiveSessionCreate(
        project_id="demo", client_name="pytest-runner", **_CI_FIELDS,
    )
    assert payload.pr_number == 421
    assert payload.ci_repo == "acme/webapp"


# ── Live-session extra_metadata fold ───────────────────────────────────────


def test_with_ci_context_folds_fields_into_metadata():
    from app.services.stream_service import _with_ci_context

    payload = SimpleNamespace(**_CI_FIELDS)
    merged = _with_ci_context({"team": "payments"}, payload)
    assert merged["team"] == "payments"
    assert merged["ci_context"] == _CI_FIELDS


def test_with_ci_context_noop_without_fields_and_explicit_keys_win():
    from app.services.stream_service import _with_ci_context

    empty_payload = SimpleNamespace(
        ci_provider=None, ci_repo=None, pr_number=None, ci_actor=None, ci_run_url=None,
    )
    original = {"team": "payments"}
    assert _with_ci_context(original, empty_payload) is original

    # A caller-supplied metadata.ci_context key beats the typed field.
    payload = SimpleNamespace(**_CI_FIELDS)
    merged = _with_ci_context({"ci_context": {"pr_number": 7}}, payload)
    assert merged["ci_context"]["pr_number"] == 7
    assert merged["ci_context"]["ci_repo"] == "acme/webapp"


# ── create_run_from_payload: reuse path backfills NULLs only ───────────────


@pytest.mark.asyncio
async def test_reuse_existing_run_backfills_null_ci_context_only():
    from app.services.ingestion_pipeline import create_run_from_payload

    project_id = str(uuid.uuid4())
    existing_run = SimpleNamespace(
        id=uuid.uuid4(),
        ci_provider=None,
        ci_repo="already/set",   # must NOT be overwritten
        pr_number=None,
        ci_actor=None,
        ci_run_url=None,
    )
    project = SimpleNamespace(id=uuid.UUID(project_id))

    project_result = MagicMock()
    project_result.scalar_one_or_none.return_value = project
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = existing_run

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[project_result, run_result])

    run = await create_run_from_payload(
        db,
        project_id=project_id,
        build_number="ci-1234",
        ci_provider="github_actions",
        ci_repo="acme/webapp",
        pr_number=421,
        reuse_existing=True,
    )

    assert run is existing_run
    assert run.ci_provider == "github_actions"   # NULL → filled
    assert run.pr_number == 421                  # NULL → filled
    assert run.ci_repo == "already/set"          # non-NULL → preserved


# ── Migration 0101 contract ────────────────────────────────────────────────

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations" / "versions" / "0101_add_ci_context_to_test_runs.py"
)


def test_migration_0101_contract():
    spec = importlib.util.spec_from_file_location("migration_0101", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0101"
    assert module.down_revision == "0100"
    import inspect
    up = inspect.getsource(module.upgrade)
    down = inspect.getsource(module.downgrade)
    for column in _CI_FIELDS:
        assert column in up, f"upgrade() missing column {column}"
        assert column in down, f"downgrade() missing column {column}"
    assert "ix_test_runs_project_pr" in up and "ix_test_runs_project_pr" in down
