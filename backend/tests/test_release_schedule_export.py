"""W11 REL-06: the release schedule as ICS and CSV.

1. The pure writers. ICS (RFC 5545): every ``BEGIN:`` closed in order; no
   physical line past 75 OCTETS, unfolding restores the logical line and no
   fold splits a UTF-8 sequence (a 200-character name of multi-byte
   characters); TEXT escaping of ``\\ ; ,`` and newlines; stable UIDs; all-day
   UTC dates with an exclusive end; nothing for a release with no date and
   phases with no window; a cancelled release (and its phases) absent. CSV:
   one row per phase, a BOM, CRLF rows, formula-leading cells neutralised, a
   cancelled release present with its status. The file name's slug.
2. The routes. The QUERY ``project_id`` is resolved FIRST: a foreign one is
   403 before any release statement, a malformed one 400; an ADMIN with no
   project gets every active project, a member theirs only; more than 500
   releases is a 422; ``to`` before ``from`` is a 422; and the static paths
   are reachable -- declared before ``GET /{release_id}``, which would
   otherwise capture ``schedule.ics`` as a release id.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.core import deps
from app.core.release_planning import SCHEDULE_TOO_MANY
from app.models.postgres import Project, Release, ReleasePhase, UserRole
from app.routers import releases as rel_router
from app.services import release_schedule_export as export
from app.services.release_schedule_export import (
    CSV_HEADER,
    ScheduleRelease,
    SchedulePhase,
    csv_safe,
    ics_escape,
    ics_fold,
    schedule_filename,
    write_csv,
    write_ics,
)

NOW = datetime(2026, 10, 23, 9, 30, tzinfo=timezone.utc)
BASE = "https://testlookup.example.test"


def _phase(name="Payments soak", *, start=None, end=None, status="pending", order=0, actual=None):
    return SchedulePhase(
        id=uuid.uuid4(), name=name, phase_type="qa_testing", status=status, order_index=order,
        planned_start=start, planned_end=end,
        actual_start=actual, actual_end=None,
        updated_at=datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc),
    )


def _release(name="2026.11", *, status="in_progress", planned=None, released=None, phases=(), project="Checkout"):
    return ScheduleRelease(
        id=uuid.uuid4(), project_id=uuid.uuid4(), project_name=project, name=name, version=None,
        status=status, planned_date=planned, released_at=released,
        updated_at=datetime(2026, 10, 21, 14, 5, tzinfo=timezone.utc), phases=tuple(phases),
    )


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _unfold(raw: bytes) -> list[str]:
    return raw.decode("utf-8").replace("\r\n ", "").split("\r\n")


def _ics(releases, name="TestLookup releases: Checkout") -> bytes:
    return write_ics(releases, calendar_name=name, now=NOW, base_url=BASE)


# ── 1. The writers ───────────────────────────────────────────────────────────


def test_the_calendar_is_well_formed_and_every_block_closes_in_order():
    rel = _release(planned=_utc(2026, 11, 20, 17), phases=[
        _phase(start=_utc(2026, 11, 3), end=_utc(2026, 11, 7, 23, 59)),
        _phase("Undated"),
    ])
    raw = _ics([rel])
    assert raw.startswith(b"BEGIN:VCALENDAR\r\n")
    assert raw.endswith(b"END:VCALENDAR\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")    # CRLF everywhere, no bare LF
    stack: list[str] = []
    for line in _unfold(raw):
        if line.startswith("BEGIN:"):
            stack.append(line[6:])
        elif line.startswith("END:"):
            assert stack and stack.pop() == line[4:], line
    assert stack == []
    lines = _unfold(raw)
    for header in ("VERSION:2.0", "PRODID:-//TestLookup//Release schedule//EN", "CALSCALE:GREGORIAN",
                   "METHOD:PUBLISH", "X-WR-CALNAME:TestLookup releases: Checkout"):
        assert header in lines
    assert lines.count("BEGIN:VEVENT") == 2      # the release and its dated phase


def test_the_release_and_phase_events_are_all_day_utc_with_an_exclusive_end():
    phase = _phase(start=_utc(2026, 11, 3), end=_utc(2026, 11, 7, 23, 59), actual=_utc(2026, 11, 4, 9))
    rel = _release(planned=_utc(2026, 11, 20, 23, 30), phases=[phase])
    lines = _unfold(_ics([rel]))
    release_at = lines.index(f"UID:release-{rel.id}@testlookup")
    block = lines[release_at:lines.index("END:VEVENT", release_at)]
    assert "DTSTAMP:20261023T093000Z" in block
    assert "LAST-MODIFIED:20261021T140500Z" in block
    assert "DTSTART;VALUE=DATE:20261120" in block
    assert "DTEND;VALUE=DATE:20261121" in block
    assert "SUMMARY:Release 2026.11 (planned)" in block
    assert "DESCRIPTION:Project: Checkout\\nStatus: in_progress\\nPhases: 1" in block
    assert f"URL:{BASE}/releases/{rel.id}" in block
    phase_at = lines.index(f"UID:phase-{phase.id}@testlookup")
    block = lines[phase_at:lines.index("END:VEVENT", phase_at)]
    assert "DTSTART;VALUE=DATE:20261103" in block
    assert "DTEND;VALUE=DATE:20261108" in block
    assert "SUMMARY:2026.11 · Payments soak" in block
    assert any(line.startswith("DESCRIPTION:Type: qa_testing\\nStatus: pending\\nActual: 2026-11-04 to ?")
               for line in block)


def test_a_released_release_sits_on_its_release_day():
    rel = _release(status="released", planned=_utc(2026, 9, 1), released=_utc(2026, 9, 4, 22))
    lines = _unfold(_ics([rel]))
    assert "DTSTART;VALUE=DATE:20260904" in lines
    assert "SUMMARY:Release 2026.11 (released)" in lines


def test_text_is_escaped_per_rfc_5545():
    rel = _release(name="A;B,C\nD\\E", planned=_utc(2026, 11, 3))
    lines = _unfold(_ics([rel]))
    assert "SUMMARY:Release A\\;B\\,C\\nD\\\\E (planned)" in lines
    assert ics_escape("tab\there\rcr\x00nul") == "tabherecrnul"
    assert ics_escape("line\r\nbreak") == "line\\nbreak"


def test_long_multibyte_lines_fold_at_75_octets_and_unfold_exactly():
    name = ("é日本" * 70)[:200]
    rel = _release(name=name, planned=_utc(2026, 11, 3))
    raw = _ics([rel])
    for physical in raw.split(b"\r\n"):
        assert len(physical) <= 75, physical
        physical.decode("utf-8")      # never split inside a UTF-8 sequence
    assert f"SUMMARY:Release {name} (planned)" in _unfold(raw)
    folded = ics_fold("X" * 200)
    assert all(len(p.encode()) <= 75 for p in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == "X" * 200
    assert ics_fold("short") == "short"


def test_uids_are_stable_across_exports():
    rel = _release(planned=_utc(2026, 11, 3), phases=[_phase(start=_utc(2026, 11, 3))])
    uids = lambda raw: [line for line in _unfold(raw) if line.startswith("UID:")]  # noqa: E731
    assert uids(_ics([rel])) == uids(write_ics([rel], calendar_name="x", now=_utc(2027, 1, 1), base_url=BASE))


def test_no_date_and_no_window_means_no_event():
    rel = _release(phases=[_phase("Smoke"), _phase("UAT")])
    assert b"BEGIN:VEVENT" not in _ics([rel])


def test_a_phase_dated_at_one_end_is_a_one_day_event():
    rel = _release(phases=[_phase(end=_utc(2026, 11, 9))])
    lines = _unfold(_ics([rel]))
    assert "DTSTART;VALUE=DATE:20261109" in lines and "DTEND;VALUE=DATE:20261110" in lines


def test_a_cancelled_release_is_off_the_calendar_but_in_the_spreadsheet():
    cancelled = _release("2026.09", status="cancelled", planned=_utc(2026, 11, 3),
                         phases=[_phase(start=_utc(2026, 11, 3))])
    assert b"BEGIN:VEVENT" not in _ics([cancelled])
    rows = list(csv.reader(io.StringIO(write_csv([cancelled]).decode("utf-8-sig"))))
    assert rows[1][1] == "2026.09" and rows[1][3] == "cancelled"


def test_no_url_unless_the_base_is_http():
    rel = _release(planned=_utc(2026, 11, 3))
    for base in ("", "javascript:alert(1)", "ftp://x.example.test", "https://x .example.test"):
        raw = write_ics([rel], calendar_name="x", now=NOW, base_url=base)
        assert b"\r\nURL:" not in raw


def test_the_csv_is_one_row_per_phase_with_a_bom_and_crlf():
    rel = _release(planned=_utc(2026, 11, 20), phases=[
        _phase("Smoke", start=_utc(2026, 11, 3), end=_utc(2026, 11, 4), order=0),
        _phase("UAT", start=_utc(2026, 11, 10), order=1),
    ])
    bare = _release("2026.12", phases=[])
    raw = write_csv([rel, bare])
    assert raw.startswith("﻿".encode("utf-8"))
    text = raw.decode("utf-8-sig")
    assert text.count("\r\n") == 4 and "\n" not in text.replace("\r\n", "")
    rows = list(csv.reader(io.StringIO(text)))
    assert tuple(rows[0]) == CSV_HEADER
    assert rows[1] == ["Checkout", "2026.11", "", "in_progress", "2026-11-20T00:00:00Z", "",
                       "0", "Smoke", "qa_testing", "pending", "2026-11-03T00:00:00Z", "2026-11-04T00:00:00Z", "", ""]
    assert rows[2][7] == "UAT" and rows[2][6] == "1"
    assert rows[3][:2] == ["Checkout", "2026.12"] and rows[3][6:] == [""] * 8


@pytest.mark.parametrize("value, expected", [
    ("=SUM(A1)", "'=SUM(A1)"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"),
    ("\tx", "'\tx"), ("\rx", "'\rx"), ("a=b", "a=b"), ("", ""), (None, ""), (3, "3"),
])
def test_cells_cannot_start_a_formula(value, expected):
    assert csv_safe(value) == expected


def test_a_hostile_release_name_is_text_in_the_csv():
    rel = _release(name='=HYPERLINK("http://evil.example.test")', phases=[])
    rows = list(csv.reader(io.StringIO(write_csv([rel]).decode("utf-8-sig"))))
    assert rows[1][1] == '\'=HYPERLINK("http://evil.example.test")'


def test_the_file_name_slug():
    today = date(2026, 10, 23)
    assert schedule_filename("Checkout Web", "ics", today) == "testlookup-releases-checkout-web-20261023.ics"
    assert schedule_filename("all-projects", "csv", today) == "testlookup-releases-all-projects-20261023.csv"
    assert schedule_filename('Pay"ments/../x', "ics", today) == "testlookup-releases-pay-ments-x-20261023.ics"
    assert schedule_filename("日本", "ics", today) == "testlookup-releases-project-20261023.ics"
    slug = schedule_filename("a" * 80, "ics", today)[len("testlookup-releases-"):-len("-20261023.ics")]
    assert len(slug) == 60


# ── 2. The routes ────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows


class _Session:
    def __init__(self, answers):
        self.answers = list(answers)
        self.sql: list[str] = []
        self.params: list[dict] = []

    async def execute(self, stmt, *a, **k):
        compiled = stmt.compile()
        self.sql.append(str(compiled))
        self.params.append(dict(compiled.params))
        if not self.answers:
            raise AssertionError(f"unexpected statement: {compiled}")
        return _Result(self.answers.pop(0))


def _orm_release(name="2026.11", project_id=None) -> Release:
    rel = Release(id=uuid.uuid4(), project_id=project_id or uuid.uuid4(), name=name, status="planning",
                  planned_date=_utc(2026, 11, 20), updated_at=_utc(2026, 10, 21))
    rel.phases = [ReleasePhase(id=uuid.uuid4(), release_id=rel.id, name="Smoke", phase_type="qa_testing",
                               status="pending", order_index=0, planned_start=_utc(2026, 11, 3))]
    return rel


def _member(*projects):
    async def accessible(db, user):
        return set(projects)
    return accessible


async def _admin_scope(db, user):
    return None


USER = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_ENGINEER)


async def test_a_foreign_project_is_403_before_any_release_statement(monkeypatch):
    monkeypatch.setattr(deps, "get_accessible_project_ids", _member(uuid.uuid4()))
    db = _Session([])
    for handler in (rel_router.export_release_schedule_ics, rel_router.export_release_schedule_csv):
        with pytest.raises(HTTPException) as exc:
            await handler(project_id=str(uuid.uuid4()), from_=None, to=None, db=db, current_user=USER)
        assert exc.value.status_code == 403
    assert db.sql == []


async def test_a_malformed_project_id_is_400(monkeypatch):
    monkeypatch.setattr(deps, "get_accessible_project_ids", _member())
    db = _Session([])
    with pytest.raises(HTTPException) as exc:
        await rel_router.export_release_schedule_ics(project_id="not-a-uuid", from_=None, to=None, db=db,
                                                     current_user=USER)
    assert exc.value.status_code == 400
    assert db.sql == []


async def test_an_admin_without_a_project_gets_every_active_project(monkeypatch):
    monkeypatch.setattr(deps, "get_accessible_project_ids", _admin_scope)
    rel = _orm_release()
    db = _Session([[(rel, "Checkout")]])
    response = await rel_router.export_release_schedule_ics(project_id=None, from_=None, to=None, db=db,
                                                            current_user=USER)
    assert len(db.sql) == 1
    assert "projects.is_active IS true" in db.sql[0]
    assert "releases.project_id IN" not in db.sql[0]
    assert response.media_type == "text/calendar; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="testlookup-releases-all-projects-'
    )
    assert response.body.startswith(b"BEGIN:VCALENDAR")
    assert f"UID:release-{rel.id}@testlookup".encode() in response.body


async def test_a_member_without_a_project_gets_their_projects_only(monkeypatch):
    mine = {uuid.uuid4(), uuid.uuid4()}
    monkeypatch.setattr(deps, "get_accessible_project_ids", _member(*mine))
    db = _Session([[]])
    response = await rel_router.export_release_schedule_csv(project_id=None, from_=None, to=None, db=db,
                                                            current_user=USER)
    assert "releases.project_id IN" in db.sql[0]
    scoped = next(v for k, v in db.params[0].items() if k.startswith("project_id"))
    assert set(scoped) == mine
    assert response.media_type == "text/csv; charset=utf-8"
    assert response.body.startswith("﻿".encode())


async def test_one_project_names_the_file_after_it(monkeypatch):
    pid = uuid.uuid4()
    monkeypatch.setattr(deps, "get_accessible_project_ids", _member(pid))
    db = _Session([[(_orm_release(project_id=pid), "Checkout Web")], "Checkout Web"])
    response = await rel_router.export_release_schedule_ics(project_id=str(pid), from_=None, to=None, db=db,
                                                            current_user=USER)
    assert 'filename="testlookup-releases-checkout-web-' in response.headers["content-disposition"]
    assert b"X-WR-CALNAME:TestLookup releases: Checkout Web" in response.body


async def test_more_than_500_releases_is_a_422(monkeypatch):
    monkeypatch.setattr(deps, "get_accessible_project_ids", _admin_scope)
    db = _Session([[(_orm_release(f"r{i}"), "Checkout") for i in range(501)]])
    with pytest.raises(HTTPException) as exc:
        await rel_router.export_release_schedule_ics(project_id=None, from_=None, to=None, db=db,
                                                     current_user=USER)
    assert (exc.value.status_code, exc.value.detail) == (422, SCHEDULE_TOO_MANY)
    assert "LIMIT" in db.sql[0]


async def test_to_before_from_is_a_422(monkeypatch):
    monkeypatch.setattr(deps, "get_accessible_project_ids", _admin_scope)
    with pytest.raises(HTTPException) as exc:
        await rel_router.export_release_schedule_csv(project_id=None, from_=date(2026, 11, 2), to=date(2026, 11, 1),
                                                     db=_Session([]), current_user=USER)
    assert (exc.value.status_code, exc.value.detail) == (422, "to is before from.")


async def test_a_window_filters_in_the_statement(monkeypatch):
    monkeypatch.setattr(deps, "get_accessible_project_ids", _admin_scope)
    db = _Session([[]])
    await rel_router.export_release_schedule_ics(project_id=None, from_=date(2026, 11, 1), to=date(2026, 11, 30),
                                                 db=db, current_user=USER)
    assert "EXISTS" in db.sql[0] and "release_phases" in db.sql[0]


async def test_the_static_paths_are_not_captured_as_a_release_id(monkeypatch):
    """A GET of ``/api/v1/releases/schedule.ics`` reaches its handler (the route-order pin)."""
    monkeypatch.setattr(deps, "get_accessible_project_ids", _admin_scope)

    async def no_releases(db, **kwargs):
        return []

    async def trend(db, **kwargs):
        return {"weeks": kwargs["weeks"], "points_at": [], "in_progress": [], "ready_to_ship": [],
                "blocked": [], "released_30d": [], "evaluated_in_week": [], "go_in_week": [],
                "totals": {"evaluated": 0, "go": 0, "conditional_go": 0, "no_go": 0, "not_evaluated": 0,
                           "released": 0, "released_with_pack": 0},
                "approximated": [], "generated_at": NOW}

    monkeypatch.setattr(export, "load_schedule", no_releases)
    monkeypatch.setattr(rel_router.release_portfolio_service, "portfolio_trend", trend)
    app = FastAPI()
    app.include_router(rel_router.router)

    async def db():
        yield _Session([])

    app.dependency_overrides[rel_router.get_db] = db
    app.dependency_overrides[rel_router.get_current_active_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(), role=UserRole.ADMIN)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        ics = await client.get("/api/v1/releases/schedule.ics")
        csv_ = await client.get("/api/v1/releases/schedule.csv")
        trend_ = await client.get("/api/v1/releases/portfolio-trend", params={"weeks": 4})
        bad = await client.get("/api/v1/releases/portfolio-trend", params={"weeks": 27})
    assert ics.status_code == 200 and ics.text.startswith("BEGIN:VCALENDAR")
    assert ics.headers["content-type"] == "text/calendar; charset=utf-8"
    assert csv_.status_code == 200 and csv_.content.startswith("﻿".encode())
    assert trend_.status_code == 200 and trend_.json()["weeks"] == 4
    assert bad.status_code == 422
    paths = [route.path for route in rel_router.router.routes]
    detail = paths.index("/api/v1/releases/{release_id}")
    for static in ("/api/v1/releases/schedule.ics", "/api/v1/releases/schedule.csv",
                   "/api/v1/releases/portfolio-trend"):
        assert paths.index(static) < detail


def test_the_handlers_resolve_the_scope_themselves():
    """The authorization ratchet reads the CALL in each handler (AST)."""
    import inspect

    for handler in (rel_router.export_release_schedule_ics, rel_router.export_release_schedule_csv,
                    rel_router.get_release_portfolio_trend):
        assert "resolve_project_scope(db, current_user, project_id)" in inspect.getsource(handler)
    assert Project.__tablename__ == "projects"
