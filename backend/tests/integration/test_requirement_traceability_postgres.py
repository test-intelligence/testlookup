"""W13 TCL-06 against real PostgreSQL: requirement traceability "lite".

5. Forward (case -> links) and backward (key -> cases, ``GET /cases?requirement=``)
   are both project-scoped.
6. A "related"-only key IS a gap (``related_only``), a partly-verified one
   ``partly_verified``, and a "verifies" link from a DEPRECATED case verifies nothing.
7. A batch id and a chunk id ``[REQ-12]`` are known and equal a typed ``req-12``;
   another project's links, batches and chunks are never counted; a JSON-null
   claim list counts 0 (never an error).
8. The unique index refuses a second ``REQ-12`` on one case: 409 through the
   route under a forced race; deleting the case cascades its links.
9. ``EXPLAIN`` of the links statement reads an index of the new table, no OR across tables.
10. Offline, no integration: every route answers (no LLM / Jira call is made).
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.deps import get_current_active_user, get_db
from app.models.postgres import (
    GenerationBatch,
    KnowledgeChunk,
    KnowledgeSource,
    ManagedTestCase,
    Project,
    ProjectMember,
    RequirementCoverage,
    User,
    UserRole,
)
from app.models.postgres import TestCaseRequirementLink as Link
from app.routers import test_management as tm_router
from app.services import feature_flags, jira_client, llm_factory
from app.services import requirement_traceability_service as svc

asyncpg = pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_engine():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


def _case(project_id, title, status="active"):
    return ManagedTestCase(id=uuid.uuid4(), project_id=project_id, title=title, status=status, version=1,
                           priority="medium", severity="major", test_type="functional", is_automated=False,
                           automation_status="not_automated", ai_generated=False)


async def _knowledge(session, project_id, ids, token):
    source = KnowledgeSource(id=uuid.uuid4(), project_id=project_id, source_type="confluence", title="Spec",
                             canonical_url=f"https://kb.example.invalid/{token}/{project_id}")
    session.add(source)
    await session.flush()
    session.add_all([
        KnowledgeChunk(source_id=source.id, project_id=project_id, vector_id=f"w13r-{uuid.uuid4().hex}",
                       chunk_index=n, requirement_id=rid, is_active=active)
        for n, (rid, active) in enumerate(ids)
    ])
    await session.flush()


@pytest.fixture
async def world(pg_engine, monkeypatch):
    async def _flag_on(key, **kwargs):
        return True

    monkeypatch.setattr(feature_flags, "is_enabled", _flag_on)
    token = uuid.uuid4().hex[:10]
    tess = User(id=uuid.uuid4(), email=f"w13r-{token}@example.invalid", username=f"w13r-{token}",
                full_name="W13 Tess", hashed_password="not-a-real-password", role=UserRole.TESTER, is_active=True)
    a = Project(id=uuid.uuid4(), name=f"W13 req A {token}", slug=f"w13-req-a-{token}")
    b = Project(id=uuid.uuid4(), name=f"W13 req B {token}", slug=f"w13-req-b-{token}")
    c = {k: _case(a.id, k, status) for k, status in (
        ("verify", "active"), ("related", "approved"), ("partial", "active"), ("dep", "deprecated"),
        ("nolink", "active"), ("draft", "draft"),
    )}
    b_case = _case(b.id, "b-verify")
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([tess, a, b])
        await s.flush()
        s.add(ProjectMember(user_id=tess.id, project_id=a.id, role=UserRole.TESTER.value))
        s.add_all([*c.values(), b_case])
        await s.flush()
        for case, key, link_type in (
            (c["verify"], "REQ-PAY-07", "verifies"), (c["related"], "REQ-REL-1", "related"),
            (c["partial"], "REQ-PART-1", "partially_verifies"), (c["dep"], "REQ-DEP-1", "verifies"),
            (c["partial"], "req-12", "verifies"),
            (b_case, "REQ-B-ONLY", "verifies"), (b_case, "REQ-PAY-07", "verifies"),
        ):
            s.add(Link(project_id=case.project_id, test_case_id=case.id, requirement_key=key,
                       requirement_key_norm=key.upper(), link_type=link_type, created_by_id=tess.id))
        for project_id, claims in ((a.id, (("REQ-12", [1, 2]), ("REQ-BATCH-ONLY", [1, 2, 3]), ("REQ-NULL", None))),
                                   (b.id, (("REQ-B-BATCH", [1]),))):
            batch = GenerationBatch(id=uuid.uuid4(), project_id=project_id)
            s.add(batch)
            await s.flush()
            for rid, covered in claims:
                s.add(RequirementCoverage(batch_id=batch.id, project_id=project_id, requirement_id=rid,
                                          covered_by_case_ids=covered))
        await _knowledge(s, a.id, [("[REQ-12]", True), ("[REQ-KS-9]", True), ("REQ-INACTIVE", False)], token)
        await _knowledge(s, b.id, [("[REQ-B-KS]", True)], token)
        await s.commit()
    try:
        yield SimpleNamespace(factory=factory, engine=pg_engine, a=a, b=b, c=c, b_case=b_case, tess=tess)
    finally:
        async with factory() as s:
            await s.execute(delete(Project).where(Project.id.in_([a.id, b.id])))
            await s.execute(delete(User).where(User.id == tess.id))
            await s.commit()


def _client(world, user):
    app = FastAPI()
    app.include_router(tm_router.router)                 # the composed /api/v1/test-management router

    async def _db():
        async with world.factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _tess(world):
    async with world.factory() as s:
        return await s.get(User, world.tess.id)


async def test_forward_and_backward_are_both_project_scoped(world):
    async with _client(world, await _tess(world)) as client:
        forward = await client.get(f"/api/v1/test-management/cases/{world.c['verify'].id}/requirements")
        backward = await client.get("/api/v1/test-management/cases",
                                    params={"project_id": str(world.a.id), "requirement": "[req-pay-07]"})
        b_forward = await client.get(f"/api/v1/test-management/cases/{world.b_case.id}/requirements")
    assert forward.status_code == 200, forward.text
    assert [(i["normalized_key"], i["created_by_name"]) for i in forward.json()["items"]] == [("REQ-PAY-07", "W13 Tess")]
    assert forward.json()["total"] == 1
    assert backward.status_code == 200, backward.text
    assert [i["id"] for i in backward.json()["items"]] == [str(world.c["verify"].id)]   # never B's case
    assert b_forward.status_code == 403


async def test_another_projects_link_is_never_reached_and_never_confirmed(world):
    async with world.factory() as s:
        b_link = (await s.execute(select(Link.id).where(Link.test_case_id == world.b_case.id))).scalars().first()
        a_link = (await s.execute(select(Link.id).where(Link.test_case_id == world.c["related"].id))).scalar()
    mine = f"/api/v1/test-management/cases/{world.c['verify'].id}/requirements"
    async with _client(world, await _tess(world)) as client:
        via_my_case = await client.delete(f"{mine}/{b_link}")              # B's link under A's case
        sibling = await client.delete(f"{mine}/{a_link}")                  # another A case's link
        unknown = await client.delete(f"{mine}/{uuid.uuid4()}")
        via_b_case = await client.delete(f"/api/v1/test-management/cases/{world.b_case.id}/requirements/{b_link}")
    assert [(r.status_code, r.json()["detail"]) for r in (via_my_case, sibling, unknown)] == [
        (404, "Requirement link not found")] * 3                            # one sentence: no oracle
    assert (via_b_case.status_code, via_b_case.json()["detail"]) == (403, "You do not have access to this project")
    async with world.factory() as s:
        left = (await s.execute(select(func.count()).select_from(Link).where(Link.id.in_([b_link, a_link]))))
        assert left.scalar() == 2                                          # nothing was deleted


async def test_the_summary_and_the_gaps_measure_only_this_project(world):
    async with world.factory() as s:
        summary = await svc.requirement_summary(s, project_id=world.a.id, top_gaps=5)
        page = await svc.traceability_gaps(s, project_id=world.a.id, page=1, size=4)
        page2 = await svc.traceability_gaps(s, project_id=world.a.id, page=2, size=4)
    assert (summary["known"], summary["verified"], summary["partly_verified"], summary["gaps"]) == (8, 2, 1, 6)
    assert summary["measured"] is True and summary["truncated"] is False
    reasons = [(g["normalized_key"], g["reason"]) for g in page["gaps"] + page2["gaps"]]
    assert reasons == [
        ("REQ-BATCH-ONLY", "no_link"), ("REQ-DEP-1", "no_link"), ("REQ-KS-9", "no_link"), ("REQ-NULL", "no_link"),
        ("REQ-REL-1", "related_only"), ("REQ-PART-1", "partly_verified"),
    ]
    assert page["gaps_total"] == 6 and len(page["gaps"]) == 4
    gaps = {g["normalized_key"]: g for g in summary["top_gaps"]}
    assert gaps["REQ-BATCH-ONLY"]["batch_claimed_cases"] == 3 and gaps["REQ-BATCH-ONLY"]["sources"] == ["generation_batch"]
    assert gaps["REQ-KS-9"]["key"] == "[REQ-KS-9]" and gaps["REQ-KS-9"]["sources"] == ["knowledge_source"]
    assert gaps["REQ-NULL"]["batch_claimed_cases"] == 0             # a JSON-null claim list counts 0
    assert gaps["REQ-DEP-1"]["linked_cases"] == 0                   # only a deprecated case links it
    assert len(summary["top_gaps"]) == 5
    assert page["cases_without_requirements_total"] == 1
    assert [r["id"] for r in page["cases_without_requirements"]] == [world.c["nolink"].id]
    assert page["summary"]["top_gaps"] == []


async def test_a_batch_id_and_a_chunk_id_equal_a_typed_key(world):
    async with world.factory() as s:
        known, _ = await svc._known_keys(s, world.a.id)
    assert known["REQ-12"]["sources"] == {"link", "generation_batch", "knowledge_source"}
    assert known["REQ-12"]["key"] == "req-12" and known["REQ-12"]["verified"] is True
    assert known["REQ-12"]["batch_claimed_cases"] == 2
    assert not {"REQ-B-ONLY", "REQ-B-BATCH", "REQ-B-KS", "REQ-INACTIVE"} & set(known)


async def test_the_unique_index_answers_a_race_with_the_same_409(world, monkeypatch):
    async def _no_precheck(db, case_id, normalized):
        return None                                         # the race: the pre-check saw nothing

    url = f"/api/v1/test-management/cases/{world.c['verify'].id}/requirements"
    async with _client(world, await _tess(world)) as client:
        first = await client.post(url, json={"requirement_key": "req-77"})
        monkeypatch.setattr(svc, "_existing_link_id", _no_precheck)
        second = await client.post(url, json={"requirement_key": "REQ-77", "link_type": "related"})
    assert first.status_code == 201, first.text
    assert (second.status_code, second.json()["detail"]) == (409, "This case already links REQ-77.")
    async with world.factory() as s:
        count = (await s.execute(select(func.count()).select_from(Link).where(
            Link.test_case_id == world.c["verify"].id, Link.requirement_key_norm == "REQ-77"))).scalar()
        assert count == 1
        await s.execute(delete(ManagedTestCase).where(ManagedTestCase.id == world.c["verify"].id))
        await s.commit()
        left = (await s.execute(select(func.count()).select_from(Link).where(
            Link.test_case_id == world.c["verify"].id))).scalar()
    assert left == 0                                            # the case's links cascade with it


async def test_the_links_statement_reads_an_index_of_its_own_table(world):
    links_stmt, _batches, _chunks = svc.known_key_statements(world.a.id)
    sql = str(links_stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert " OR " not in sql.upper()
    async with world.factory() as s:
        await s.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(r[0] for r in (await s.execute(text(f"EXPLAIN {sql}"))).all())
    assert "ix_tcrl_project_key" in plan or "uq_tcrl_case_key" in plan, plan


async def test_offline_with_no_integration_every_route_answers(world, monkeypatch):
    def _refuse(*args, **kwargs):
        raise AssertionError("no LLM or Jira call on the traceability path")

    monkeypatch.setattr(llm_factory, "get_llm", _refuse)
    monkeypatch.setattr(jira_client, "create_jira_issue", _refuse)
    monkeypatch.setattr(jira_client, "env_jira_config", _refuse)
    case_url = f"/api/v1/test-management/cases/{world.c['nolink'].id}/requirements"
    async with _client(world, await _tess(world)) as client:
        added = await client.post(case_url, json={"requirement_key": "REQ-OFF-1", "requirement_title": "Offline"})
        listed = await client.get(case_url)
        removed = await client.delete(f"{case_url}/{added.json()['id']}")
        gaps = await client.get("/api/v1/test-management/traceability/gaps", params={"project_id": str(world.a.id)})
        health = await client.get("/api/v1/test-management/health", params={"project_id": str(world.a.id)})
    assert [r.status_code for r in (added, listed, removed, gaps, health)] == [201, 200, 204, 200, 200]
    assert listed.json()["items"][0]["requirement_title"] == "Offline"
    assert health.json()["requirements"]["known"] == 8                # the flag on: the summary rides health
    assert [g["normalized_key"] for g in health.json()["requirements"]["top_gaps"]][:2] == ["REQ-BATCH-ONLY",
                                                                                         "REQ-DEP-1"]
