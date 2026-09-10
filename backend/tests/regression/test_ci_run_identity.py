"""H05 regressions for source-aware CI run identity and race recovery."""
from __future__ import annotations

import importlib.util
import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError, MultipleResultsFound

from app.models.postgres import TestRun as TestRunModel
from app.routers.ingest import _resolve_run_id
from app.services.ingestion_pipeline import build_ingestion_identity, create_run_from_payload


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ResultDB:
    def __init__(self, value):
        self.value = value

    async def execute(self, *_args, **_kwargs):
        return _Result(self.value)


@pytest.mark.parametrize("url", [
    "https://github.com/acme/app/actions/runs/42/",
    "https://github.com/acme/app/actions/runs/42",
])
def test_ci_url_normalization_is_idempotent(url):
    kwargs = dict(
        project_id=uuid.uuid4(),
        build_number="17",
        ingestion_source="sdk",
        ci_provider="GitHub_Actions",
        ci_repo="/Acme/App/",
        ci_run_url=url,
    )
    assert build_ingestion_identity(**kwargs) == build_ingestion_identity(
        **{**kwargs, "ci_run_url": url.rstrip("/")}
    )


def test_distinct_ci_runs_and_projects_do_not_share_identity():
    base = dict(
        project_id=uuid.uuid4(),
        build_number="17",
        ingestion_source="sdk",
        ci_provider="github_actions",
        ci_repo="acme/app",
    )
    first = build_ingestion_identity(**base, ci_run_url="https://ci/runs/1")
    second = build_ingestion_identity(**base, ci_run_url="https://ci/runs/2")
    other_project = build_ingestion_identity(
        **{**base, "project_id": uuid.uuid4()}, ci_run_url="https://ci/runs/1"
    )
    assert first != second
    assert first != other_project


def test_distinct_jenkins_jobs_do_not_share_identity_without_run_url():
    base = dict(
        project_id=uuid.uuid4(), build_number="17", ingestion_source="sdk",
        ci_provider="jenkins", ci_repo="acme/app",
    )
    first = build_ingestion_identity(**base, jenkins_job="linux")
    second = build_ingestion_identity(**base, jenkins_job="windows")
    assert first != second


def test_same_jenkins_job_with_distinct_run_urls_do_not_share_identity():
    base = dict(
        project_id=uuid.uuid4(), build_number="17", ingestion_source="sdk",
        ci_provider="jenkins", ci_repo="acme/app", jenkins_job="linux",
    )
    first = build_ingestion_identity(**base, ci_run_url="https://ci/runs/1")
    second = build_ingestion_identity(**base, ci_run_url="https://ci/runs/2")
    assert first != second


class _AmbiguousResult:
    def scalar_one_or_none(self):
        raise MultipleResultsFound()


@pytest.mark.asyncio
async def test_context_free_retry_does_not_choose_ambiguous_legacy_run():
    project_id = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_Result(None), _AmbiguousResult()])
    resolved = await _resolve_run_id(db, project_id, "17")
    assert uuid.UUID(resolved)
    assert resolved == str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"testlookup:{project_id}:{build_ingestion_identity(project_id=project_id, build_number='17', ingestion_source='sdk')}",
    ))


@pytest.mark.asyncio
async def test_context_free_sdk_ingest_never_promotes_manual_upload():
    """Legacy label fallback must exclude operator-created upload runs."""
    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id)
    manual = SimpleNamespace(
        id=uuid.uuid4(), ingestion_identity=None, ingestion_source="upload",
        build_number="17",
    )
    db = MagicMock()
    statements = []

    async def _execute(statement, *_args, **_kwargs):
        statements.append(str(statement))
        if len(statements) == 1:
            return _Result(project)
        if len(statements) == 2:
            return _Result(None)
        if "test_runs.ingestion_source !=" in statements[-1]:
            return _Result(None)
        return _Result(manual)

    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock()
    db.flush = AsyncMock()

    created = await create_run_from_payload(
        db,
        project_id=str(project_id),
        build_number="17",
        ingestion_source="sdk",
        reuse_existing=True,
    )

    assert created is not manual
    assert created.ingestion_source == "sdk"
    assert created.ingestion_identity is not None
    assert manual.ingestion_identity is None
    assert any("test_runs.ingestion_source !=" in sql for sql in statements)


@pytest.mark.asyncio
async def test_new_ci_identity_gets_same_deterministic_id_for_retries():
    project_id = uuid.uuid4()
    first = await _resolve_run_id(
        _ResultDB(None), project_id, "17",
        ci_provider="github_actions", ci_repo="acme/app",
        ci_run_url="https://ci/runs/1",
    )
    second = await _resolve_run_id(
        _ResultDB(None), project_id, "17",
        ci_provider="github_actions", ci_repo="acme/app",
        ci_run_url="https://ci/runs/1",
    )
    assert first == second


@pytest.mark.asyncio
async def test_identity_conflict_returns_database_winner():
    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id)
    winner = SimpleNamespace(id=uuid.uuid4(), ingestion_identity="winner", build_number="17")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_Result(project), _Result(None), _Result(None), _Result(None), _Result(winner)])
    db.add = MagicMock()
    db.flush = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate")))
    db.rollback = AsyncMock()

    result = await create_run_from_payload(
        db,
        project_id=str(project_id),
        build_number="17",
        run_id=str(uuid.uuid4()),
        ci_provider="github_actions",
        ci_repo="acme/app",
        ci_run_url="https://ci/runs/1",
    )
    assert result is winner
    db.rollback.assert_awaited_once()


def test_model_has_partial_unique_identity_index():
    indexes = {idx.name: idx for idx in TestRunModel.__table__.indexes}
    idx = indexes["uq_test_runs_project_ingestion_identity"]
    assert idx.unique is True
    assert "ingestion_identity IS NOT NULL" in str(
        idx.dialect_options["postgresql"]["where"]
    )


def test_model_replaces_legacy_build_constraint_with_compatibility_index():
    indexes = {idx.name: idx for idx in TestRunModel.__table__.indexes}
    assert "uq_test_run_build" not in {
        constraint.name for constraint in TestRunModel.__table__.constraints
        if constraint.name
    }
    idx = indexes["uq_test_runs_legacy_build"]
    assert idx.unique is True
    assert "ingestion_identity IS NULL" in str(
        idx.dialect_options["postgresql"]["where"]
    )


def test_batch_finalizes_canonical_run():
    from app.worker.tasks import ingest_uploaded_results
    source = inspect.getsource(ingest_uploaded_results)
    assert "run_id=str(run.id)" in source


def test_migration_0160_contract():
    path = Path(__file__).parents[2] / "migrations" / "versions" / "0160_ingestion_identity.py"
    spec = importlib.util.spec_from_file_location("migration_0160", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0160"
    assert module.down_revision == "0159"
    up = inspect.getsource(module.upgrade)
    down = inspect.getsource(module.downgrade)
    assert "ingestion_identity" in up and "ingestion_identity" in down
    assert "drop_constraint" in up
    assert "uq_test_runs_legacy_build" in up
    assert "unique=True" in up
