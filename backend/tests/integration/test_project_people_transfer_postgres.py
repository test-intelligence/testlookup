"""W37 F-2 / F-7 — members by e-mail and kept reviews, round trip on a real PostgreSQL (contract §1, §2, D-5, D-6).

ONE small source project (its own seed, removed at the end — CI shares ONE database across modules) is exported
through the REAL runner WITH Include members + Include reviews, then imported through the ASGI route (``POST
/api/v1/projects/import`` as an ADMIN) and the REAL runner. Proven:

1. The export lists the HUMAN members only (never the synthetic QA lead, a service account, an erased or an
   inactive member), the suite owners, and the e-mail of exactly the people it names; schema 3, no history block.
2. Add members + Keep reviews: members added by e-mail with roles CAPPED at their own (QA_LEAD → QA_ENGINEER), the
   importer skipped silently, one archived e-mail matching two accounts (case variants) → ``email_ambiguous``, an
   account erased after the export → ``email_not_found``; a suite owner kept (a QA lead) and one refused
   (``owner_not_lead``); one ``member_added`` audit row per member; reviews kept (pending / claimed by a reviewer
   member), the claim of someone NOT added → unclaimed; comments with their parent; the activity counts.
3. A crafted (unsigned) variant naming the synthetic QA lead and a service account → both ``email_not_found``.
4. The DEFAULT import of the same archive → nobody added, reviews ``not_chosen``, in-review cases draft.

Fixture rule (§6.3): ``dispose_engine_for_loop()`` before the engine is built and after it is disposed; storage =
``LocalStorageProvider`` on a temporary directory; no Mongo. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` at head.
"""
from __future__ import annotations

import inspect
import io
import json
import os
import uuid
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest
import pytest_asyncio
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_TAG_LETTERS = str.maketrans("0123456789abcdef", "ghjkmnpqrstvwxyz")


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _members(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {info.filename: zf.read(info) for info in zf.infolist()}


def _lines(blob: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in blob.decode("utf-8").splitlines() if line.strip()]


def _crafted(archive: bytes, extra_people: list[tuple[uuid.UUID, str, str]]) -> bytes:
    """The real archive + members naming ``(id, username, e-mail)`` people, re-listed and UNSIGNED."""
    import hashlib

    members = _members(archive)
    users = _lines(members["directory/users.jsonl"])
    roster = _lines(members["directory/members.jsonl"])
    by_id = {line["id"]: line for line in users}
    for user_id, username, email in extra_people:
        line = by_id.get(str(user_id))           # e.g. the synthetic lead: already listed as a cleared reference
        if line is None:
            users.append({"id": str(user_id), "username": username, "full_name": None, "email": email})
        else:
            line["email"] = email
        roster.append({"user_id": str(user_id), "role": "QA_ENGINEER"})
    members["directory/users.jsonl"] = b"".join(json.dumps(u, sort_keys=True).encode() + b"\n" for u in users)
    members["directory/members.jsonl"] = b"".join(json.dumps(r, sort_keys=True).encode() + b"\n" for r in roster)
    members.pop("signature.json", None)
    manifest = json.loads(members["manifest.json"])
    manifest["directory"]["users"], manifest["directory"]["members"] = len(users), len(roster)
    manifest["files"] = [{"name": n, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                         for n, b in sorted(members.items()) if n != "manifest.json"]
    members["manifest.json"] = json.dumps(manifest, sort_keys=True).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _person(tag: str, label: str, role: str, **extra: Any) -> Any:
    from app.models.postgres import User

    username = f"w37p-{label}-{tag}"
    values = {"email": f"{username}@people.example.test", "is_active": True, **extra}
    return User(id=uuid.uuid4(), username=username, full_name=None, hashed_password="!integration", role=role,
                **values)


async def _seed(db: Any, tag: str) -> SimpleNamespace:
    """The source project, its people, two suites with owners, four cases in every review shape, their reviews and a
    two-comment thread. Commits."""
    from app.models.postgres import (
        ManagedTestCase,
        Project,
        ProjectMember,
        TestCaseComment,
        TestCaseReview,
        TestSuiteOwner,
    )
    from app.models.schemas import ManagedTestCaseCreate
    from app.services.project_provisioning import provision_new_project
    from app.services.test_case_lifecycle_service import transition
    from app.services.test_management_service import create_managed_test_case

    p = SimpleNamespace(
        admin=_person(tag, "admin", "ADMIN"), author=_person(tag, "author", "QA_ENGINEER"),
        lead_cap=_person(tag, "leadcap", "QA_ENGINEER"),      # archived QA_LEAD, own role QA_ENGINEER → capped
        eng=_person(tag, "eng", "QA_LEAD"), owner=_person(tag, "owner", "QA_LEAD"),
        viewer=_person(tag, "viewer", "QA_ENGINEER"), amb=_person(tag, "amb", "QA_ENGINEER"),
        gone=_person(tag, "gone", "QA_ENGINEER"),              # erased AFTER the export
        erased=_person(tag, "erased", "QA_ENGINEER", erased_at=datetime.now(timezone.utc)),
        off=_person(tag, "off", "QA_ENGINEER", is_active=False),
        service=_person(tag, "svc", "QA_ENGINEER", is_service_account=True),
    )
    p.amb.email = p.amb.email.replace("w37p-amb", "W37P-Amb")                 # the twin below differs in case only
    p.twin = _person(tag, "twin", "VIEWER", email=p.amb.email.lower())
    db.add_all(list(vars(p).values()))
    await db.flush()
    project = Project(id=uuid.uuid4(), name=f"People Service {tag}", slug=f"people-service-{tag}",
                      require_case_review=True)
    db.add(project)
    await db.flush()
    await provision_new_project(db, project=project, creator=p.admin)        # admin + synthetic lead + service
    for person, role in ((p.author, "QA_ENGINEER"), (p.lead_cap, "QA_LEAD"), (p.eng, "QA_ENGINEER"),
                         (p.owner, "QA_LEAD"), (p.viewer, "VIEWER"), (p.amb, "QA_ENGINEER"), (p.gone, "QA_ENGINEER"),
                         (p.erased, "QA_ENGINEER"), (p.off, "QA_ENGINEER")):
        db.add(ProjectMember(user_id=person.id, project_id=project.id, role=role))
    await db.flush()
    cases = {}
    for key, suite in (("req", "Checkout"), ("claimed", "Checkout"), ("stray", "Ledger"), ("approved", "Ledger")):
        case = await create_managed_test_case(db, ManagedTestCaseCreate(
            project_id=project.id, title=f"{key} case {tag}", suite_name=suite), p.author)
        cases[key] = case.id
    await db.flush()
    await transition(db, cases["req"], "request_review", p.author)          # review_requested + a pending review
    for key, claimant, status in (("claimed", p.eng, "under_review"), ("stray", p.amb, "under_review"),
                                  ("approved", None, "approved")):
        await db.execute(update(ManagedTestCase).where(ManagedTestCase.id == cases[key])
                         .values(status=status, reviewer_id=claimant.id if claimant else None))
    db.add_all([
        TestCaseReview(test_case_id=cases["claimed"], reviewer_id=p.eng.id, requested_by_id=p.author.id,
                       status="in_progress"),
        TestCaseReview(test_case_id=cases["stray"], reviewer_id=p.amb.id, requested_by_id=p.author.id,
                       status="in_progress"),
        TestCaseReview(test_case_id=cases["approved"], reviewer_id=p.eng.id, requested_by_id=p.author.id,
                       status="approved", human_notes="ship it", reviewed_at=datetime.now(timezone.utc)),
    ])
    first = TestCaseComment(id=uuid.uuid4(), test_case_id=cases["req"], author_id=p.author.id, content="why?")
    db.add(first)
    await db.flush()
    db.add(TestCaseComment(test_case_id=cases["req"], author_id=p.eng.id, content="because", parent_id=first.id))
    for suite, owner in (("Checkout", p.owner), ("Ledger", p.viewer)):     # the suites were seeded with the lead
        result = await db.execute(update(TestSuiteOwner).where(
            TestSuiteOwner.project_id == project.id, TestSuiteOwner.suite_name == suite).values(owner_user_id=owner.id))
        if not result.rowcount:
            db.add(TestSuiteOwner(project_id=project.id, suite_name=suite, owner_user_id=owner.id))
    await db.commit()
    return SimpleNamespace(project=project, people=p, cases=cases, lead=project.default_qa_lead_user_id)


async def _cleanup(sessions: Any, seeded: Optional[SimpleNamespace], people: list[Any]) -> None:
    """Everything this module wrote: the source, every copy (their synthetic QA leads), the jobs, the access-audit
    rows (SET NULL FKs — they would outlive their project), the activity rows and the people. Raises on a failure."""
    from app.models.postgres import AccessAuditLog, Project, ProjectActivityEvent, ProjectDataJob, User

    ids = [person.id for person in people]
    async with sessions() as db:
        projects = set((await db.execute(select(ProjectDataJob.project_id).where(
            ProjectDataJob.requested_by_id.in_(ids), ProjectDataJob.project_id.is_not(None)))).scalars().all())
        if seeded is not None:
            projects.add(seeded.project.id)
        leads = [lead for lead in (await db.execute(
            select(Project.default_qa_lead_user_id).where(Project.id.in_(projects)))).scalars().all() if lead]
        everyone = ids + leads
        await db.execute(delete(AccessAuditLog).where(or_(
            AccessAuditLog.project_id.in_(projects), AccessAuditLog.target_user_id.in_(everyone),
            AccessAuditLog.actor_user_id.in_(everyone))))
        await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id.in_(projects)))
        await db.execute(delete(ProjectDataJob).where(or_(
            ProjectDataJob.project_id.in_(projects), ProjectDataJob.requested_by_id.in_(ids))))
        await db.execute(delete(Project).where(Project.id.in_(projects)))
        await db.execute(delete(User).where(User.id.in_(everyone)))
        await db.commit()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def env(tmp_path_factory):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core import deps
    from app.db import postgres as app_postgres
    from app.db import redis_client
    from app.db import storage as storage_module
    from app.models.postgres import ProjectDataJob, User
    from app.models.schemas import ProjectDataOptions
    from app.routers import project_data
    from app.services import project_data_job_service as jobs
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET
    from app.worker import project_data_runner as runner
    from app.worker import tasks

    await app_postgres.dispose_engine_for_loop()
    redis_was_bound = redis_client._pool is not None
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=4)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    patcher = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("w37b-objects")
    patcher.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(root))
    storage = storage_module.LocalStorageProvider(default_bucket="artifacts")
    patcher.setattr(storage_module, "get_storage_provider", lambda: storage)
    dispatched: list[list[str]] = []

    def apply_async(*, args, **_kw):          # the REAL payload is bound, never only recorded
        inspect.signature(tasks.import_project_data.run).bind(*args)
        dispatched.append(list(args))

    patcher.setattr(tasks.import_project_data, "apply_async", apply_async)
    tag = uuid.uuid4().hex[:10].translate(_TAG_LETTERS)
    seeded: Optional[SimpleNamespace] = None
    people: list[Any] = []
    try:
        async with sessions() as db:
            seeded = await _seed(db, tag)
        people = list(vars(seeded.people).values())
        async with sessions() as db:
            export_job = await jobs.create_export_job(
                db, project=seeded.project, actor=seeded.people.admin,
                options=ProjectDataOptions(include_members=True, include_reviews=True))
            await db.commit()
        await runner.run_export_job(export_job.id, storage=storage, session_factory=sessions)
        async with sessions() as db:
            exported = await db.get(ProjectDataJob, export_job.id)
        assert exported.status == "succeeded", (exported.error_code, exported.error)
        archive = await storage.get_object_content(exported.storage_key, bucket=COMPLIANCE_BUCKET)
        async with sessions() as db:                     # erased AFTER the export: the archive still lists them
            await db.execute(update(User).where(User.id == seeded.people.gone.id)
                             .values(erased_at=datetime.now(timezone.utc)))
            await db.commit()
    except BaseException:                     # a failed SETUP must not leak its people / seed into the shared DB
        await _cleanup(sessions, seeded, people)
        patcher.undo()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        raise
    for person in people:
        deps._bind_credential_kind(person, deps.CREDENTIAL_KIND_JWT)
    state = SimpleNamespace(user=seeded.people.admin)
    app = FastAPI()
    app.include_router(project_data.router)
    app.dependency_overrides[deps.get_current_active_user] = lambda: state.user

    async def own_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[app_postgres.get_db] = own_db

    async def call(method: str, path: str, **kwargs: Any):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    try:
        yield SimpleNamespace(sessions=sessions, storage=storage, call=call, dispatched=dispatched, runner=runner,
                              seeded=seeded, p=seeded.people, archive=archive, export=exported)
    finally:
        patcher.undo()
        await _cleanup(sessions, seeded, people)
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        if not redis_was_bound and redis_client._pool is not None:   # close ONLY what this module bound
            await redis_client.close_redis()


# ── helpers ──────────────────────────────────────────────────────────────────

async def _import(env, archive: bytes, **fields: str) -> Any:
    """POST through the route (202), then the REAL runner on this engine; returns the job row."""
    from app.models.postgres import ProjectDataJob

    response = await env.call("POST", "/api/v1/projects/import", data={"client_token": str(uuid.uuid4()), **fields},
                              files={"file": ("export.zip", archive, "application/zip")})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert env.dispatched[-1] == [job_id]
    await env.runner.run_import_job(uuid.UUID(job_id), storage=env.storage, session_factory=env.sessions)
    async with env.sessions() as db:
        job = await db.get(ProjectDataJob, uuid.UUID(job_id))
    assert job.status == "succeeded", (job.error_code, job.error)
    return job


async def _rows(env, sql: str, **params: Any) -> list[Any]:
    async with env.sessions() as db:
        return list((await db.execute(text(sql), params)).all())


async def _copy(env, project_id: uuid.UUID) -> SimpleNamespace:
    members = {r.user_id: r.role for r in await _rows(
        env, "SELECT user_id, role FROM project_members WHERE project_id = :p", p=project_id)}
    owners = {r.suite_name: r.owner_user_id for r in await _rows(
        env, "SELECT suite_name, owner_user_id FROM test_suite_owners WHERE project_id = :p", p=project_id)}
    cases = {r.title.split(" ")[0]: r for r in await _rows(
        env, "SELECT id, title, status, reviewer_id FROM managed_test_cases WHERE project_id = :p", p=project_id)}
    reviews = await _rows(env, (
        "SELECT r.test_case_id, r.status, r.reviewer_id, r.requested_by_id FROM test_case_reviews r "
        "JOIN managed_test_cases c ON c.id = r.test_case_id WHERE c.project_id = :p"), p=project_id)
    comments = await _rows(env, (
        "SELECT k.id, k.parent_id, k.author_id, k.content FROM test_case_comments k "
        "JOIN managed_test_cases c ON c.id = k.test_case_id WHERE c.project_id = :p"), p=project_id)
    audits = await _rows(env, (
        "SELECT action, target_user_id, actor_user_id, after_value FROM access_audit_logs WHERE project_id = :p"),
        p=project_id)
    imported = await _rows(env, (
        "SELECT context FROM project_activity_events WHERE project_id = :p AND event_type = 'project.imported'"),
        p=project_id)
    return SimpleNamespace(members=members, owners=owners, cases=cases, reviews=reviews, comments=comments,
                           audits=audits, imported=imported[0].context)


def _skips(report: dict[str, Any], entity: str) -> dict[str, int]:
    return {s["reason"]: s["count"] for s in report["skipped"] if s["entity"] == entity}


# ── 1. the export ────────────────────────────────────────────────────────────

async def test_the_export_lists_the_human_members_the_owners_and_only_their_emails(env):
    p, members = env.p, _members(env.archive)
    manifest = json.loads(members["manifest.json"])
    assert manifest["schema_version"] == 3 and "history" not in manifest
    listed = {line["user_id"]: line["role"] for line in _lines(members["directory/members.jsonl"])}
    assert listed == {str(p.admin.id): "ADMIN", str(p.author.id): "QA_ENGINEER", str(p.lead_cap.id): "QA_LEAD",
                      str(p.eng.id): "QA_ENGINEER", str(p.owner.id): "QA_LEAD", str(p.viewer.id): "VIEWER",
                      str(p.amb.id): "QA_ENGINEER", str(p.gone.id): "QA_ENGINEER"}
    # never the synthetic QA lead, the service account, the erased or the inactive member
    assert not {str(env.seeded.lead), str(p.service.id), str(p.erased.id), str(p.off.id)} & set(listed)
    owners = {line["suite_name"]: line["user_id"] for line in _lines(members["directory/suite_owners.jsonl"])}
    assert owners == {"Checkout": str(p.owner.id), "Ledger": str(p.viewer.id)}
    directory = {line["id"]: line for line in _lines(members["directory/users.jsonl"])}
    assert {uid for uid, line in directory.items() if "email" in line} == set(listed)
    assert directory[str(p.amb.id)]["email"] == p.amb.email                     # as stored (case kept)
    assert manifest["directory"] == {"users": len(directory), "members": 8, "suite_owners": 2}
    assert manifest["entities"]["test_case_reviews"]["rows"] == 4
    assert manifest["entities"]["test_case_comments"]["rows"] == 2
    assert env.export.options == {"include_members": True, "include_reviews": True}


# ── 2. Add members + Keep reviews ────────────────────────────────────────────

async def test_add_members_and_keep_reviews_bring_the_people_and_reviews_that_can_still_act(env):
    p = env.p
    job = await _import(env, env.archive, add_members="true", keep_reviews="true")
    report, copy = job.report, await _copy(env, job.project_id)
    assert job.options == {"add_members": True, "keep_reviews": True, "schema_version": 3}
    # roles capped at the person's own; the importer (already a member) untouched
    assert {uid: copy.members.get(uid) for uid in (p.admin.id, p.author.id, p.lead_cap.id, p.eng.id, p.owner.id,
                                                   p.viewer.id, p.amb.id, p.twin.id, p.gone.id)} == {
        p.admin.id: "ADMIN", p.author.id: "QA_ENGINEER", p.lead_cap.id: "QA_ENGINEER", p.eng.id: "QA_ENGINEER",
        p.owner.id: "QA_LEAD", p.viewer.id: "VIEWER", p.amb.id: None, p.twin.id: None, p.gone.id: None}
    assert _skips(report, "project_members") == {"email_ambiguous": 1, "email_not_found": 1}
    assert copy.owners == {"Checkout": p.owner.id}
    assert _skips(report, "test_suite_owners") == {"owner_not_lead": 1}
    added = {p.author.id, p.lead_cap.id, p.eng.id, p.owner.id, p.viewer.id}
    assert sorted((a.action, a.target_user_id, a.actor_user_id) for a in copy.audits) == sorted(
        ("member_added", uid, p.admin.id) for uid in added)
    assert {a.target_user_id: a.after_value["role"] for a in copy.audits} == {
        uid: copy.members[uid] for uid in added}
    assert report["inserted"]["project_members"] == 5 and report["inserted"]["test_suite_owners"] == 1
    # reviews: the pending one kept, the claim of a reviewer member kept, the claim of someone not added unclaimed
    assert {key: (row.status, row.reviewer_id) for key, row in copy.cases.items()} == {
        "req": ("review_requested", None), "claimed": ("under_review", p.eng.id),
        "stray": ("review_requested", None), "approved": ("approved", None)}
    by_case = {row.test_case_id: (row.status, row.reviewer_id) for row in copy.reviews}
    assert by_case == {copy.cases["req"].id: ("pending", None), copy.cases["claimed"].id: ("in_progress", p.eng.id),
                       copy.cases["stray"].id: ("pending", None), copy.cases["approved"].id: ("approved", p.eng.id)}
    assert {row.requested_by_id for row in copy.reviews} == {p.author.id}       # authorship by username
    assert report["switched_off"].get("managed_test_cases.review_unclaimed") == 1
    assert "managed_test_cases.review_reset" not in report["switched_off"]
    first = next(k for k in copy.comments if k.parent_id is None)
    reply = next(k for k in copy.comments if k.parent_id is not None)
    assert reply.parent_id == first.id and (first.author_id, reply.author_id) == (p.author.id, p.eng.id)
    assert report["inserted"]["test_case_reviews"] == 4 and report["inserted"]["test_case_comments"] == 2
    assert copy.imported["members_added"] == 5 and copy.imported["reviews_kept"] == 4
    assert report["options_ignored"] == []


async def test_the_synthetic_lead_and_a_service_account_are_never_matched(env):
    from app.models.postgres import User

    async with env.sessions() as db:
        lead = await db.get(User, env.seeded.lead)
    crafted = _crafted(env.archive, [(lead.id, lead.username, lead.email),
                                     (env.p.service.id, env.p.service.username, env.p.service.email)])
    job = await _import(env, crafted, add_members="true")
    assert job.report["signature"] == "unsigned"
    assert _skips(job.report, "project_members") == {"email_ambiguous": 1, "email_not_found": 3}
    copy = await _copy(env, job.project_id)
    assert copy.members.get(lead.id) is None                     # the SOURCE's synthetic lead never joins the copy
    # the service account is a member of every new project through provisioning — never through the archive
    assert env.p.service.id not in {a.target_user_id for a in copy.audits}
    assert len(copy.audits) == 5 and job.report["inserted"]["project_members"] == 5
    assert job.report["options_ignored"] == [] and "test_case_reviews" not in job.report["inserted"]


# ── 3. the default import of the same archive ───────────────────────────────

async def test_the_default_import_adds_nobody_and_keeps_no_review(env):
    p = env.p
    job = await _import(env, env.archive)
    report, copy = job.report, await _copy(env, job.project_id)
    people = {person.id for person in vars(p).values()} - {p.admin.id, p.service.id}
    assert not set(copy.members) & people                                  # an archive never grants access
    assert copy.audits == [] and copy.owners == {}
    assert _skips(report, "project_members") == {"not_chosen": 8}
    assert _skips(report, "test_suite_owners") == {"not_chosen": 2}
    assert _skips(report, "test_case_reviews") == {"not_chosen": 4}
    assert _skips(report, "test_case_comments") == {"not_chosen": 2}
    assert not {"project_members", "test_suite_owners", "test_case_reviews", "test_case_comments"} & set(
        report["inserted"])
    assert {key: row.status for key, row in copy.cases.items()} == {
        "req": "draft", "claimed": "draft", "stray": "draft", "approved": "approved"}
    assert report["switched_off"]["managed_test_cases.review_reset"] == 3 and copy.reviews == [] == copy.comments
    assert "members_added" not in copy.imported and "reviews_kept" not in copy.imported
    assert job.options == {"schema_version": 3}
