"""``services/workflow_default_service`` (W14 AGT-08) on real PostgreSQL.

The real service over real ``workflow_definitions`` rows (published and draft
versions of forks in two projects) and the real ``project_workflow_defaults``
table (migration 0230):

* resolution: no row -> builtin; an unpinned default -> the newest PUBLISHED
  version (a newer draft ignored); a pinned one -> that version; a planted row
  whose workflow has only a draft -> ``default_unavailable`` with the sentence.
* ``set_default``: the ``INSERT … ON CONFLICT (project_id, base) DO UPDATE``
  keeps ONE row per base and names the previous ref; another project's
  workflow -> 404; a draft-only fork -> 422; a deep fork as the offline
  default -> 422 (as the deep default -> stored).
* ``read_defaults`` answers both bases in ≤ 4 statements (counted on the engine).
* ``clear_default`` deletes (None the second time); deleting the project
  deletes its defaults (the cascade).

The service never commits: the test commits where a router would. The app's
own engine is disposed before and after (the W13 event-loop rule).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import Project, ProjectWorkflowDefault, User, UserRole, WorkflowDefinition
from app.models.schemas import WorkflowDefaultUpdate
from app.services import workflow_default_service as svc
from app.services import workflow_definition_service as definition_svc

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_engine():
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _definition(project_id, workflow_id, version, *, base="offline", status="published") -> WorkflowDefinition:
    document = dict(definition_svc.builtin(base)["definition"])
    document.update({"workflow_id": workflow_id, "version": version, "project_id": str(project_id)})
    return WorkflowDefinition(
        id=uuid.uuid4(), project_id=project_id, workflow_id=workflow_id, version=version,
        name=f"{workflow_id} v{version}", base=base, definition=document, status=status,
        published_at=datetime(2026, 10, 1, tzinfo=timezone.utc) if status == "published" else None,
    )


@pytest.fixture
async def world(pg_engine):
    token = uuid.uuid4().hex[:10]
    lead = User(id=uuid.uuid4(), email=f"w14a-{token}@example.invalid", username=f"w14a-{token}",
                full_name="Lea Lead", hashed_password="not-a-real-password", role=UserRole.QA_LEAD, is_active=True)
    p = Project(id=uuid.uuid4(), name=f"W14 defaults P {token}", slug=f"w14-defaults-p-{token}")
    q = Project(id=uuid.uuid4(), name=f"W14 defaults Q {token}", slug=f"w14-defaults-q-{token}")
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([lead, p, q])
        await s.flush()
        s.add_all([
            _definition(p.id, "wf.my-fork", 1), _definition(p.id, "wf.my-fork", 2),
            _definition(p.id, "wf.my-fork", 3, status="draft"),
            _definition(p.id, "wf.deep-fork", 1, base="deep"),
            _definition(p.id, "wf.draft", 1, status="draft"),
            _definition(q.id, "wf.theirs", 1),
        ])
        await s.commit()
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(pg_engine.sync_engine, "before_cursor_execute", _count)
    try:
        yield SimpleNamespace(factory=factory, lead=lead, p=p.id, q=q.id, statements=statements)
    finally:
        event.remove(pg_engine.sync_engine, "before_cursor_execute", _count)
        async with factory() as s:
            await s.execute(delete(Project).where(Project.id.in_([p.id, q.id])))
            await s.execute(delete(User).where(User.id == lead.id))
            await s.commit()


async def _rows(world) -> list[tuple]:
    async with world.factory() as s:
        return [
            (r.project_id, r.base, r.workflow_id, r.workflow_version)
            for r in (await s.execute(
                select(ProjectWorkflowDefault).where(ProjectWorkflowDefault.project_id.in_([world.p, world.q]))
                .order_by(ProjectWorkflowDefault.base)
            )).scalars().all()
        ]


async def _set(world, base, workflow_id, version=None):
    async with world.factory() as s:
        result = await svc.set_default(
            s, world.p, base, WorkflowDefaultUpdate(workflow_id=workflow_id, workflow_version=version), world.lead,
        )
        await s.commit()          # where the router commits
        return result


async def _resolve(world, base="offline"):
    async with world.factory() as s:
        return await svc.resolve_run_workflow(s, world.p, base)


async def test_resolution_follows_the_published_versions_and_the_pin(world):
    assert await _resolve(world) == svc.DefaultResolution("offline", None, "builtin", None)

    row, definition = await _set(world, "offline", "wf.my-fork")
    assert (row.workflow_id, row.workflow_version, row.updated_by) == ("wf.my-fork", None, world.lead.id)
    assert definition["previous_default"] == "builtin" and definition["version"] == 2
    assert await _resolve(world) == svc.DefaultResolution("wf.my-fork", 2, "project_default", None), (
        "unpinned: the newest PUBLISHED version; the v3 draft never runs"
    )

    _, definition = await _set(world, "offline", "wf.my-fork", 1)
    assert definition["previous_default"] == "wf.my-fork"
    assert await _resolve(world) == svc.DefaultResolution("wf.my-fork", 1, "project_default", None)
    assert await _rows(world) == [(world.p, "offline", "wf.my-fork", 1)], "the upsert keeps one row per base"

    _, definition = await _set(world, "deep", "wf.deep-fork")
    assert await _resolve(world, "deep") == svc.DefaultResolution("wf.deep-fork", 1, "project_default", None)
    assert await _rows(world) == [(world.p, "deep", "wf.deep-fork", None), (world.p, "offline", "wf.my-fork", 1)]


async def test_a_planted_row_without_a_published_version_falls_back(world):
    async with world.factory() as s:
        await s.execute(text(
            "INSERT INTO project_workflow_defaults (project_id, base, workflow_id) VALUES (:p, 'offline', 'wf.draft')"
        ), {"p": world.p})
        await s.commit()
    resolution = await _resolve(world)
    assert resolution == svc.DefaultResolution(
        "offline", None, "default_unavailable",
        "The default wf.draft no longer resolves in this project. The built-in offline workflow ran instead.",
    )


@pytest.mark.parametrize(
    ("base", "workflow_id", "status", "detail"),
    [
        ("offline", "wf.theirs", 404, "Workflow definition not found"),
        ("offline", "wf.draft", 422, "Only a published workflow can be the default: publish wf.draft first."),
        ("offline", "wf.deep-fork", 422, "wf.deep-fork is built on deep: it can only be the deep default."),
        ("offline", "wf.missing", 404, "Workflow definition not found"),
        ("live", "wf.my-fork", 422, "base must be offline or deep."),
    ],
)
async def test_set_default_refuses_and_stores_nothing(world, base, workflow_id, status, detail):
    with pytest.raises(svc.DefaultRejected) as caught:
        await _set(world, base, workflow_id)
    assert (caught.value.status_code, caught.value.detail) == (status, detail)
    assert await _rows(world) == []


async def test_read_defaults_answers_both_bases_in_at_most_four_statements(world):
    await _set(world, "offline", "wf.my-fork")
    async with world.factory() as s:
        await s.execute(text(
            "INSERT INTO project_workflow_defaults (project_id, base, workflow_id, workflow_version) "
            "VALUES (:p, 'deep', 'wf.deep-fork', 7)"
        ), {"p": world.p})
        await s.commit()
    world.statements.clear()
    async with world.factory() as s:
        offline, deep = await svc.read_defaults(s, world.p)
    issued = [sql for sql in world.statements if not sql.lstrip().upper().startswith(("BEGIN", "ROLLBACK", "COMMIT"))]
    assert len(issued) <= 4, issued
    assert (offline["source"], offline["state"], offline["resolved_ref"], offline["updated_by"]) == (
        "project_default", "ok", "wf.my-fork@2", "Lea Lead",
    )
    assert offline["model_stages"] == ["root_cause_analysis", "summary", "triage"]
    assert (deep["state"], deep["resolved_ref"], deep["reason"]) == (
        "unavailable", None, "The default wf.deep-fork@7 no longer resolves in this project.",
    )
    async with world.factory() as s:
        assert [d["source"] for d in await svc.read_defaults(s, world.q)] == ["builtin", "builtin"]


async def test_clear_deletes_once_and_the_project_cascade_removes_defaults(world):
    await _set(world, "offline", "wf.my-fork")
    async with world.factory() as s:
        cleared = await svc.clear_default(s, world.p, "offline")
        await s.commit()
    assert cleared is not None and cleared.workflow_id == "wf.my-fork"
    assert await _rows(world) == []
    async with world.factory() as s:
        assert await svc.clear_default(s, world.p, "offline") is None

    await _set(world, "deep", "wf.deep-fork")
    async with world.factory() as s:
        await s.execute(delete(Project).where(Project.id == world.p))
        await s.commit()
    assert await _rows(world) == [], "deleting the project deletes its defaults"
