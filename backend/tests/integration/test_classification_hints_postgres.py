"""FAIL-10 on real PostgreSQL: project classification hints, end to end through the app.

* the routes: QA lead writes; TESTER / VIEWER / QA engineer cannot; a
  non-member reads and writes nothing; a hint of project Q under
  ``/projects/P/...`` is the same 404 as a random id; an API key bound to Q
  cannot write P's hints; an extra body field is a 422;
* the rules: the case-insensitive unique index answers a duplicate with 409,
  the 51st hint with 422, every refusal with its one text, and no refusal
  leaves a ledger row;
* the ledger: ``classification_hint.created`` / ``updated`` (only on a real
  change, naming the fields and their old and new values) / ``deleted``;
* the preview: the project's newest 200 FAILED/BROKEN rows of 30 days, the
  same matcher as the pipeline, nothing written;
* the tenant: ``load_enabled_hints(P)`` never returns Q's hints (nor P's
  disabled ones), in ``(created_at, id)`` order;
* ``POST /analyze`` honours a hint and its provenance carries ``hint_*``.

Uses ``tests/integration/defect_w06_world.py`` (P and Q, a QA-engineer member
of P, an outsider of Q, the app over ASGI). Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0216) and ``REDIS_URL``.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOT_FOUND = "Classification hint not found in this project."
BODY = {"match_field": "error_message", "pattern": "ECONNREFUSED", "category": "INFRASTRUCTURE"}


@pytest.fixture
async def world():
    from app.models.postgres import ApiKey, ProjectMember, UserRole

    async with defect_world() as w:
        raw_key = f"qai_{secrets.token_urlsafe(32)}"
        async with w.sessions.begin() as db:
            w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Priya Nair")
            w.tester_id = await w.user(db, role=UserRole.TESTER.value)
            w.viewer_id = await w.user(db, role=UserRole.VIEWER.value)
            w.lead_q_id = await w.user(db, role=UserRole.QA_LEAD.value)
            w.key_owner_id = await w.user(db, role=UserRole.ADMIN.value)
            for uid, pid, role in (
                (w.lead_id, w.P, UserRole.QA_LEAD), (w.tester_id, w.P, UserRole.TESTER),
                (w.viewer_id, w.P, UserRole.VIEWER), (w.lead_q_id, w.Q, UserRole.QA_LEAD),
                (w.lead_id, w.Q, UserRole.QA_LEAD),
            ):
                db.add(ProjectMember(project_id=pid, user_id=uid, role=role.value))
            # A legacy (full-access) CI key bound to Q, owned by an admin.
            db.add(ApiKey(id=uuid.uuid4(), user_id=w.key_owner_id, name=f"fail10 {w.tag}",
                          key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                          key_hint=raw_key[:8] + "...", scopes=[], project_id=w.Q, expires_at=None))
        w.lead, w.tester, w.viewer = w.jwt(w.lead_id), w.jwt(w.tester_id), w.jwt(w.viewer_id)
        w.lead_q = w.jwt(w.lead_q_id)
        w.key_q = {"X-API-Key": raw_key}
        yield w


def _base(pid) -> str:
    return f"/api/v1/projects/{pid}/classification-hints"


async def _events(w, pid, event_type=None):
    from app.models.postgres import ProjectActivityEvent

    async with w.sessions() as db:
        stmt = select(ProjectActivityEvent).where(
            ProjectActivityEvent.project_id == pid, ProjectActivityEvent.entity_type == "classification_hint",
        ).order_by(ProjectActivityEvent.recorded_at)
        if event_type:
            stmt = stmt.where(ProjectActivityEvent.event_type == event_type)
        return list((await db.execute(stmt)).scalars().all())


async def _create(w, pid=None, headers=None, **body):
    resp = await w.client.post(_base(pid or w.P), json={**BODY, **body}, headers=headers or w.lead)
    return resp


# ── CRUD and the ledger ─────────────────────────────────────────────────────


async def test_a_qa_lead_creates_lists_updates_and_deletes_with_one_ledger_row_each(world):
    w = world
    created = await _create(w, note="  payments sandbox  ")
    assert created.status_code == 201, created.text
    hint = created.json()
    assert (hint["match_field"], hint["pattern"], hint["category"], hint["note"], hint["enabled"]) == (
        "error_message", "ECONNREFUSED", "INFRASTRUCTURE", "payments sandbox", True,
    )
    assert hint["project_id"] == str(w.P) and hint["created_by_name"] == "Priya Nair"
    assert hint["created_at"] and hint["updated_at"]

    listed = await w.client.get(_base(w.P), headers=w.viewer)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1 and listed.json()["max_hints"] == 50
    assert listed.json()["items"][0]["id"] == hint["id"]

    [row] = await _events(w, w.P, "classification_hint.created")
    assert row.category == "configuration" and row.entity_id == hint["id"]
    assert row.summary == 'Classification hint error message contains "ECONNREFUSED" → Infrastructure was created'

    patched = await w.client.patch(f"{_base(w.P)}/{hint['id']}", json={"category": "test_data"}, headers=w.lead)
    assert patched.status_code == 200, patched.text
    assert patched.json()["category"] == "TEST_DATA"
    [upd] = await _events(w, w.P, "classification_hint.updated")
    assert upd.summary == 'Classification hint error message contains "ECONNREFUSED" → Test data changed: category'
    assert upd.diff["before"] == {"category": "INFRASTRUCTURE"} and upd.diff["after"] == {"category": "TEST_DATA"}

    same = await w.client.patch(f"{_base(w.P)}/{hint['id']}", json={"category": "TEST_DATA"}, headers=w.lead)
    assert same.status_code == 200, same.text
    assert len(await _events(w, w.P, "classification_hint.updated")) == 1, "no change, no row"
    empty = await w.client.patch(f"{_base(w.P)}/{hint['id']}", json={}, headers=w.lead)
    assert empty.status_code == 422 and empty.json()["detail"] == "Nothing to change."

    gone = await w.client.delete(f"{_base(w.P)}/{hint['id']}", headers=w.lead)
    assert gone.status_code == 204, gone.text
    assert len(await _events(w, w.P, "classification_hint.deleted")) == 1
    assert (await w.client.get(_base(w.P), headers=w.lead)).json()["total"] == 0


# ── the rules and their texts ───────────────────────────────────────────────


async def test_a_case_insensitive_duplicate_is_409_and_the_51st_hint_is_422(world):
    w = world
    assert (await _create(w, pattern="econnrefused")).status_code == 201
    dup = await _create(w, pattern="  ECONNREFUSED ")
    assert dup.status_code == 409 and dup.json()["detail"] == "A hint with this field and text already exists."
    # The same text on another field is another rule.
    assert (await _create(w, match_field="test_name", pattern="econnrefused")).status_code == 201
    for i in range(48):
        assert (await _create(w, pattern=f"pattern-{i:02d}")).status_code == 201
    over = await _create(w, pattern="one too many")
    assert over.status_code == 422 and over.json()["detail"] == "This project already has 50 classification hints."
    assert len(await _events(w, w.P, "classification_hint.created")) == 50


@pytest.mark.parametrize(("body", "detail"), [
    ({"match_field": "stack_trace"}, "match_field must be one of error_message, test_name, suite_name, class_name."),
    ({"category": "UNKNOWN"}, "category must be one of PRODUCT_BUG, INFRASTRUCTURE, TEST_DATA, AUTOMATION_DEFECT, FLAKY."),
    ({"pattern": "  ab  "}, "pattern must be 3-200 characters."),
])
async def test_each_refusal_has_its_text_and_leaves_no_ledger_row(world, body, detail):
    resp = await _create(world, **body)
    assert resp.status_code == 422 and resp.json()["detail"] == detail
    assert await _events(world, world.P) == []


async def test_an_extra_body_field_is_422(world):
    resp = await _create(world, project_id=str(world.Q))
    assert resp.status_code == 422, resp.text
    assert await _events(world, world.P) == []


# ── who may ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("who", ["tester", "viewer", "member"])
async def test_below_qa_lead_cannot_write(world, who):
    assert (await _create(world, headers=getattr(world, who))).status_code == 403


async def test_a_non_member_reads_and_writes_nothing(world):
    w = world
    assert (await w.client.get(_base(w.P), headers=w.outsider)).status_code == 403
    assert (await w.client.get(f"{_base(w.P)}/preview", params={"match_field": "error_message", "pattern": "boom"},
                               headers=w.outsider)).status_code == 403
    # A QA lead of Q only.
    assert (await _create(w, headers=w.lead_q)).status_code == 403


async def test_a_key_bound_to_q_writes_q_only(world):
    w = world
    assert (await _create(w, headers=w.key_q)).status_code == 403
    assert (await _create(w, pid=w.Q, headers=w.key_q)).status_code == 201


async def test_another_projects_hint_is_the_same_404_as_a_random_id(world):
    w = world
    q_hint = (await _create(w, pid=w.Q)).json()
    for hid in (q_hint["id"], str(uuid.uuid4())):
        patched = await w.client.patch(f"{_base(w.P)}/{hid}", json={"enabled": False}, headers=w.lead)
        deleted = await w.client.delete(f"{_base(w.P)}/{hid}", headers=w.lead)
        assert (patched.status_code, patched.json()) == (404, {"detail": NOT_FOUND})
        assert (deleted.status_code, deleted.json()) == (404, {"detail": NOT_FOUND})
    # Q's hint is untouched.
    [still] = (await w.client.get(_base(w.Q), headers=w.lead)).json()["items"]
    assert still["enabled"] is True


# ── preview ─────────────────────────────────────────────────────────────────


async def test_the_preview_counts_the_newest_failures_and_writes_nothing(world):
    from app.models.postgres import ClassificationHint, LaunchStatus, TestRun

    w = world
    now = datetime.now(timezone.utc)
    async with w.sessions.begin() as db:
        old_run, q_run = uuid.uuid4(), uuid.uuid4()
        db.add(TestRun(id=old_run, project_id=w.P, build_number=f"fail10-old-{w.tag}", status=LaunchStatus.FAILED))
        db.add(TestRun(id=q_run, project_id=w.Q, build_number=f"fail10-q-{w.tag}", status=LaunchStatus.FAILED))
        await db.flush()
        for i in range(7):
            await w.case(db, error_message=f"requests: connect econnrefused host-{i}", created_at=now - timedelta(hours=i))
        for i in range(3):
            await w.case(db, error_message="AssertionError: 3 != 4", created_at=now - timedelta(hours=i))
        await w.case(db, status="PASSED", error_message="ECONNREFUSED but passed", created_at=now)
        await w.case(db, run_id=q_run, error_message="ECONNREFUSED in Q", created_at=now)
        await w.case(db, run_id=old_run, error_message="ECONNREFUSED long ago", created_at=now - timedelta(days=40))
    resp = await w.client.get(f"{_base(w.P)}/preview", params={"match_field": "error_message", "pattern": "ECONNREFUSED"},
                              headers=w.viewer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["matched"], body["sampled"], body["lookback_days"]) == (7, 10, 30)
    assert len(body["examples"]) == 5
    assert all("econnrefused" in ex["value"].lower() for ex in body["examples"])
    bad = await w.client.get(f"{_base(w.P)}/preview", params={"match_field": "error_message", "pattern": "ab"},
                             headers=w.viewer)
    assert bad.status_code == 422 and bad.json()["detail"] == "pattern must be 3-200 characters."
    async with w.sessions() as db:
        assert (await db.execute(select(func.count()).select_from(ClassificationHint).where(
            ClassificationHint.project_id == w.P))).scalar_one() == 0
    assert await _events(w, w.P) == []


# ── the tenant ──────────────────────────────────────────────────────────────


async def test_load_enabled_hints_reads_one_projects_enabled_hints_in_order(world):
    from app.services.classification_hint_service import load_enabled_hints

    w = world
    first = (await _create(w, pattern="first")).json()
    second = (await _create(w, pattern="second")).json()
    off = (await _create(w, pattern="disabled", enabled=False)).json()
    q_only = (await _create(w, pid=w.Q, pattern="q-only")).json()
    async with w.sessions() as db:
        loaded = await load_enabled_hints(db, w.P)
    ids = [h["id"] for h in loaded]
    assert ids == [first["id"], second["id"]]
    assert off["id"] not in ids and q_only["id"] not in ids
    assert loaded[0]["created_by_name"] == "Priya Nair"
    assert {h["project_id"] for h in loaded} == {str(w.P)}


# ── /analyze ────────────────────────────────────────────────────────────────


async def test_analyze_honours_a_hint_and_its_provenance_says_so(world):
    from app.models.postgres import AIAnalysis

    w = world
    hint = (await _create(w, note="the payments sandbox is down")).json()
    async with w.sessions.begin() as db:
        tc = await w.case(db, error_message="requests: connect ECONNREFUSED payments-sandbox:443",
                          class_name="CheckoutTest")
    try:
        resp = await w.client.post("/api/v1/analyze", json={"test_case_id": str(tc)}, headers=w.member)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["failure_category"] == "INFRASTRUCTURE"
        prov = body["provenance"]
        assert prov["mode_used"] == "hint"
        assert (prov["hint_id"], prov["hint_match_field"], prov["hint_pattern"], prov["hint_set_by"]) == (
            hint["id"], "error_message", "ECONNREFUSED", "Priya Nair",
        )
        stored = await w.client.get(f"/api/v1/analyze/{tc}", headers=w.member)
        assert stored.status_code == 200, stored.text
        again = stored.json()
        assert again["provenance"]["hint_id"] == hint["id"]
        assert again["provenance"]["confidence_basis"] == "project_hint"
        assert again["confidence_why"]["confidence_basis"] == "project_hint"
        assert again["failure_category"] == "INFRASTRUCTURE"
    finally:
        async with w.sessions.begin() as db:
            await db.execute(delete(AIAnalysis).where(AIAnalysis.test_case_id == tc))
