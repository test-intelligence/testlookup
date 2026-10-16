"""W08 TRND-04 on real PostgreSQL: timeline notes, end to end through the app.

* CRUD: a QA engineer pins, lists, changes and removes a note; one ledger row
  per write (``timeline_note.created`` / ``updated`` naming the fields /
  ``deleted``), none for a PATCH that changes nothing, none for a refusal;
* the rules and their texts: blank text, 281 characters, ``ends_on`` before
  ``starts_on``, a 367-day range, a 367-day list window;
* the tenant: a note of project Q under ``/projects/P/...`` is the same 404 as
  an unknown id (PATCH and DELETE); a non-member reads nothing;
* who may change a note: its author, a QA lead; another QA engineer gets 403;
  VIEWER and TESTER cannot pin;
* the window: a note overlapping an edge is listed, one wholly outside is not;
  a suite note only under that suite (any case), a project-wide note always;
* the table: both CHECKs refuse a bad row; the window query uses
  ``ix_timeline_notes_project_starts``.

Uses ``tests/integration/defect_w06_world.py`` (P and Q, a QA-engineer member
of P, an outsider of Q, the app over ASGI). Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0218) and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOT_FOUND = "Timeline note not found in this project."
NOT_YOURS = "Only the note's author or a QA lead can change this note."
TEXT_INVALID = "A note is 1 to 280 characters."
RANGE_INVALID = "A note ends on or after the day it starts, at most 366 days later."
WINDOW_INVALID = "from and to are UTC dates, from on or before to, at most 366 days apart."


@pytest.fixture
async def world():
    from app.models.postgres import ProjectMember, UserRole

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.engineer2_id = await w.user(db, role=UserRole.QA_ENGINEER.value, full_name="Second Engineer")
            w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Priya Nair")
            w.viewer_id = await w.user(db, role=UserRole.VIEWER.value)
            w.tester_id = await w.user(db, role=UserRole.TESTER.value)
            w.lead_q_id = await w.user(db, role=UserRole.QA_LEAD.value)
            for uid, pid, role in (
                (w.engineer2_id, w.P, UserRole.QA_ENGINEER), (w.lead_id, w.P, UserRole.QA_LEAD),
                (w.viewer_id, w.P, UserRole.VIEWER), (w.tester_id, w.P, UserRole.TESTER),
                (w.lead_q_id, w.Q, UserRole.QA_LEAD),
            ):
                db.add(ProjectMember(project_id=pid, user_id=uid, role=role.value))
        w.engineer2, w.lead = w.jwt(w.engineer2_id), w.jwt(w.lead_id)
        w.viewer, w.tester, w.lead_q = w.jwt(w.viewer_id), w.jwt(w.tester_id), w.jwt(w.lead_q_id)
        yield w


def _base(pid) -> str:
    return f"/api/v1/projects/{pid}/timeline-notes"


async def _pin(w, pid=None, headers=None, **body):
    payload = {"starts_on": "2026-09-10", "ends_on": "2026-09-12", "text": "release freeze", **body}
    return await w.client.post(_base(pid or w.P), json=payload, headers=headers or w.member)


async def _list(w, frm="2026-09-05", to="2026-09-18", headers=None, suite=None, pid=None):
    params = {"from": frm, "to": to, **({"suite": suite} if suite is not None else {})}
    return await w.client.get(_base(pid or w.P), params=params, headers=headers or w.viewer)


async def _events(w, pid, event_type=None):
    from app.models.postgres import ProjectActivityEvent

    async with w.sessions() as db:
        stmt = select(ProjectActivityEvent).where(
            ProjectActivityEvent.project_id == pid, ProjectActivityEvent.entity_type == "timeline_note",
        ).order_by(ProjectActivityEvent.recorded_at)
        if event_type:
            stmt = stmt.where(ProjectActivityEvent.event_type == event_type)
        return list((await db.execute(stmt)).scalars().all())


async def test_a_qa_engineer_pins_lists_changes_and_removes_with_one_ledger_row_each(world):
    w = world
    created = await _pin(w, text="  release freeze  ", suite_name="  ")
    assert created.status_code == 201, created.text
    note = created.json()
    assert (note["starts_on"], note["ends_on"], note["text"], note["suite_name"]) == (
        "2026-09-10", "2026-09-12", "release freeze", None,
    )
    assert note["created_by_name"] == "W06A member" and note["can_edit"] is True
    assert note["project_id"] == str(w.P) and note["created_at"] and note["updated_at"]

    listed = await _list(w)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert (body["total"], body["truncated"], body["start"], body["end"]) == (1, False, "2026-09-05", "2026-09-18")
    assert body["items"][0]["id"] == note["id"] and body["items"][0]["can_edit"] is False, "a viewer cannot edit"

    [row] = await _events(w, w.P, "timeline_note.created")
    assert row.category == "quality" and row.entity_id == note["id"]
    assert row.summary == 'Note "release freeze" was pinned'
    assert str(w.member_id) not in repr(row.diff), "no user id in the ledger values"

    patched = await w.client.patch(f"{_base(w.P)}/{note['id']}",
                                   json={"text": "infra outage", "ends_on": None}, headers=w.member)
    assert patched.status_code == 200, patched.text
    assert (patched.json()["text"], patched.json()["ends_on"]) == ("infra outage", "2026-09-10")
    [upd] = await _events(w, w.P, "timeline_note.updated")
    assert upd.summary == 'Note "infra outage" changed: text, dates'
    assert upd.diff["before"]["text"] == "release freeze" and upd.diff["after"]["ends_on"] == "2026-09-10"

    for same in ({"text": "infra outage"}, {}):
        unchanged = await w.client.patch(f"{_base(w.P)}/{note['id']}", json=same, headers=w.member)
        assert unchanged.status_code == 200, unchanged.text
    assert len(await _events(w, w.P, "timeline_note.updated")) == 1, "no change, no row"

    gone = await w.client.delete(f"{_base(w.P)}/{note['id']}", headers=w.member)
    assert gone.status_code == 204, gone.text
    [deleted] = await _events(w, w.P, "timeline_note.deleted")
    assert deleted.summary == 'Note "infra outage" was removed'
    assert (await _list(w)).json()["total"] == 0


@pytest.mark.parametrize("body,detail", [
    ({"text": "   "}, TEXT_INVALID),
    ({"ends_on": "2026-09-09"}, RANGE_INVALID),
    ({"starts_on": "2026-01-01", "ends_on": "2027-01-03"}, RANGE_INVALID),  # 367 days later
])
async def test_each_refusal_has_its_text_and_leaves_no_row(world, body, detail):
    w = world
    refused = await _pin(w, **body)
    assert refused.status_code == 422 and refused.json()["detail"] == detail, refused.text
    assert await _events(w, w.P) == []


async def test_the_edges_281_characters_and_a_367_day_window(world):
    w = world
    assert (await _pin(w, text="x" * 281)).status_code == 422
    assert (await _pin(w, text="x" * 280)).status_code == 201
    assert (await _pin(w, starts_on="2026-01-01", ends_on="2027-01-02")).status_code == 201, "366 days later"
    wide = await _list(w, frm="2026-01-01", to="2027-01-03")
    assert wide.status_code == 422 and wide.json()["detail"] == WINDOW_INVALID
    backwards = await _list(w, frm="2026-09-18", to="2026-09-05")
    assert backwards.status_code == 422 and backwards.json()["detail"] == WINDOW_INVALID
    assert (await _list(w, frm="2026-01-01", to="2027-01-02")).json()["total"] == 2


async def test_another_projects_note_is_the_unknown_id_404(world):
    w = world
    foreign = await _pin(w, pid=w.Q, headers=w.lead_q)
    assert foreign.status_code == 201, foreign.text
    fid = foreign.json()["id"]
    for method, body in (("PATCH", {"text": "hijack"}), ("DELETE", None)):
        for note_id in (fid, str(uuid.uuid4())):
            response = await w.client.request(method, f"{_base(w.P)}/{note_id}", json=body, headers=w.lead)
            assert response.status_code == 404 and response.json()["detail"] == NOT_FOUND, (method, response.text)
    # Q's note is untouched, and P's list never shows it.
    q_list = await _list(w, pid=w.Q, headers=w.lead_q)
    assert q_list.json()["items"][0]["text"] == "release freeze"
    assert (await _list(w)).json()["total"] == 0
    # A member of Q only reads nothing of P.
    assert (await _list(w, headers=w.outsider)).status_code == 403


async def test_the_author_or_a_qa_lead_may_change_a_note(world):
    w = world
    nid = (await _pin(w)).json()["id"]
    other = await w.client.patch(f"{_base(w.P)}/{nid}", json={"text": "mine now"}, headers=w.engineer2)
    assert other.status_code == 403 and other.json()["detail"] == NOT_YOURS
    assert (await w.client.delete(f"{_base(w.P)}/{nid}", headers=w.engineer2)).status_code == 403
    assert (await w.client.patch(f"{_base(w.P)}/{nid}", json={"text": "by the author"}, headers=w.member)).status_code == 200
    lead = await w.client.patch(f"{_base(w.P)}/{nid}", json={"suite_name": "Checkout"}, headers=w.lead)
    assert lead.status_code == 200 and lead.json()["suite_name"] == "Checkout"
    [_, by_lead] = await _events(w, w.P, "timeline_note.updated")
    assert by_lead.summary == 'Note "by the author" changed: suite' and by_lead.actor_name == "Priya Nair"
    for headers in (w.viewer, w.tester):
        refused = await _pin(w, headers=headers)
        assert refused.status_code == 403 and "Requires at least QA_ENGINEER" in refused.text
    assert (await w.client.delete(f"{_base(w.P)}/{nid}", headers=w.lead)).status_code == 204


async def test_the_window_and_the_suite_filter(world):
    w = world
    for body in (
        {"starts_on": "2026-09-01", "ends_on": "2026-09-05", "text": "edge, overlaps from"},
        {"starts_on": "2026-08-01", "ends_on": "2026-09-04", "text": "wholly before"},
        {"starts_on": "2026-09-18", "ends_on": "2026-09-30", "text": "edge, overlaps to"},
        {"starts_on": "2026-09-19", "ends_on": "2026-09-19", "text": "wholly after"},
        {"starts_on": "2026-09-10", "text": "checkout only", "suite_name": "Checkout"},
        {"starts_on": "2026-09-11", "text": "payments only", "suite_name": "payments"},
    ):
        assert (await _pin(w, **{"ends_on": None, **body})).status_code == 201

    def texts(response):
        return [item["text"] for item in response.json()["items"]]

    everything = await _list(w)
    assert texts(everything) == ["edge, overlaps from", "checkout only", "payments only", "edge, overlaps to"]
    assert texts(await _list(w, suite="  CHECKOUT ")) == ["edge, overlaps from", "checkout only", "edge, overlaps to"]
    assert texts(await _list(w, suite="smoke")) == ["edge, overlaps from", "edge, overlaps to"]
    assert texts(await _list(w, suite="")) == texts(everything)


async def test_the_table_refuses_bad_rows_and_the_window_read_uses_its_index(world):
    w = world
    insert = text(
        "INSERT INTO timeline_notes (id, project_id, starts_on, ends_on, text) "
        "VALUES (:id, :pid, :s, :e, :t)"
    )
    from datetime import date

    for values, constraint in (
        ({"s": date(2026, 9, 10), "e": date(2026, 9, 9), "t": "backwards"}, "ck_timeline_notes_range"),
        ({"s": date(2026, 9, 10), "e": date(2026, 9, 10), "t": ""}, "ck_timeline_notes_text"),
    ):
        with pytest.raises(IntegrityError) as caught:
            async with w.sessions.begin() as db:
                await db.execute(insert, {"id": uuid.uuid4(), "pid": w.P, **values})
        assert constraint in str(caught.value)
    with pytest.raises(Exception):
        async with w.sessions.begin() as db:
            await db.execute(insert, {"id": uuid.uuid4(), "pid": w.P, "s": date(2026, 9, 10),
                                      "e": date(2026, 9, 10), "t": "x" * 281})

    async with w.sessions() as db:
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in (await db.execute(text(
            "EXPLAIN SELECT id FROM timeline_notes WHERE project_id = :pid "
            "AND starts_on <= :to AND ends_on >= :frm ORDER BY starts_on, created_at, id LIMIT 200"
        ), {"pid": w.P, "to": date(2026, 9, 18), "frm": date(2026, 9, 5)})).all())
    assert "ix_timeline_notes_project_starts" in plan, plan
