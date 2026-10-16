"""W08 TRND-04: the timeline-note rules, without a database.

* the two copies of the text limit agree (``schemas`` must not import services);
* each rule has its one 422 text, at its exact edge: blank / 281 characters,
  ``ends_on`` before ``starts_on``, a range or a list window past 366 days;
* who may change a note: its author, or an instance QA lead or above by ROLE
  ORDER (a QA engineer may not touch another's note; an unknown role may not);
* the response shape, the ledger values (no user ids) and the 60-character label.

Every route and the ledger rows over real Postgres:
``tests/integration/test_timeline_notes_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models import schemas
from app.models.postgres import TimelineNote, UserRole
from app.services import timeline_note_service as svc

D = date(2026, 9, 10)


def _detail(fn, *args):
    with pytest.raises(HTTPException) as caught:
        fn(*args)
    assert caught.value.status_code == 422
    return caught.value.detail


def test_the_schemas_and_the_service_agree_on_280():
    assert schemas.TIMELINE_NOTE_TEXT_MAX == svc.NOTE_TEXT_MAX == 280
    assert schemas.TimelineNoteCreate.model_fields["text"].metadata[0].max_length == svc.NOTE_TEXT_MAX
    assert TimelineNote.__table__.c.text.type.length == svc.NOTE_TEXT_MAX


def test_the_text_is_stripped_and_one_to_280_characters():
    assert svc.clean_text("  release freeze  ") == "release freeze"
    assert svc.clean_text("x" * 280) == "x" * 280
    for bad in ("", "   ", "\n\t", None, 3):
        assert _detail(svc.clean_text, bad) == "A note is 1 to 280 characters."
    assert _detail(svc.clean_text, "x" * 281) == "A note is 1 to 280 characters."


def test_a_281_character_body_is_refused_before_the_service():
    with pytest.raises(ValidationError):
        schemas.TimelineNoteCreate(starts_on=D, text="x" * 281)
    with pytest.raises(ValidationError):
        schemas.TimelineNoteCreate(starts_on=D, text="ok", project_id=str(uuid.uuid4()))  # extra="forbid"


def test_the_range_ends_on_or_after_its_start_and_at_most_366_days_later():
    svc.check_range(D, D)
    svc.check_range(D, D + timedelta(days=366))
    text = "A note ends on or after the day it starts, at most 366 days later."
    assert _detail(svc.check_range, D, D - timedelta(days=1)) == text
    assert _detail(svc.check_range, D, D + timedelta(days=367)) == text


def test_the_list_window_is_at_most_366_days():
    svc.check_window(D, D)
    svc.check_window(D, D + timedelta(days=366))
    text = "from and to are UTC dates, from on or before to, at most 366 days apart."
    assert _detail(svc.check_window, D, D - timedelta(days=1)) == text
    assert _detail(svc.check_window, D, D + timedelta(days=367)) == text


def test_a_blank_suite_is_every_suite():
    assert svc.clean_suite("  checkout ") == "checkout"
    assert svc.clean_suite("   ") is None and svc.clean_suite(None) is None


AUTHOR = uuid.uuid4()


def _note(**kw) -> TimelineNote:
    values = dict(id=uuid.uuid4(), project_id=uuid.uuid4(), suite_name=None, starts_on=D, ends_on=D,
                  text="release freeze", created_by_id=AUTHOR,
                  created_at=datetime(2026, 9, 10, 8, tzinfo=timezone.utc), updated_at=None)
    values.update(kw)
    return TimelineNote(**values)


def _user(role, uid=None):
    return SimpleNamespace(id=uid or uuid.uuid4(), role=role)


@pytest.mark.parametrize("role,allowed", [
    (UserRole.VIEWER, False), (UserRole.TESTER, False), (UserRole.QA_ENGINEER, False),
    (UserRole.QA_LEAD, True), (UserRole.ADMIN, True),
    ("QA_LEAD", True), ("ADMIN", True), ("QA_ENGINEER", False), ("UserRole.QA_LEAD", True), ("OWNER", False),
])
def test_another_users_note_is_editable_from_qa_lead_up_by_role_order(role, allowed):
    assert svc.can_edit(_note(), _user(role)) is allowed


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.QA_ENGINEER, "QA_ENGINEER"])
def test_the_author_may_always_edit_their_own_note(role):
    assert svc.can_edit(_note(), _user(role, AUTHOR)) is True


def test_a_note_whose_author_was_deleted_is_editable_by_a_qa_lead_only():
    orphan = _note(created_by_id=None)
    assert svc.can_edit(orphan, _user(UserRole.QA_ENGINEER)) is False
    assert svc.can_edit(orphan, SimpleNamespace(id=None, role="QA_ENGINEER")) is False
    assert svc.can_edit(orphan, _user(UserRole.QA_LEAD)) is True


def test_the_response_shape_and_can_edit_are_for_this_reader():
    note = _note(suite_name="checkout", ends_on=D + timedelta(days=2))
    note.created_by_name = "Priya Nair"
    body = svc.note_response(note, _user(UserRole.VIEWER))
    assert set(body) == set(schemas.TimelineNoteResponse.model_fields)
    assert body["starts_on"] == "2026-09-10" and body["ends_on"] == "2026-09-12"
    assert body["created_by_name"] == "Priya Nair" and body["can_edit"] is False
    assert body["created_at"] == "2026-09-10T08:00:00+00:00" and body["updated_at"] is None
    assert svc.note_response(note, _user(UserRole.VIEWER, AUTHOR))["can_edit"] is True
    schemas.TimelineNoteResponse(**body)


def test_the_ledger_values_carry_no_user_id_and_the_label_is_60_characters():
    note = _note(text="y" * 100)
    assert svc.note_values(note) == {"text": "y" * 100, "starts_on": "2026-09-10", "ends_on": "2026-09-10",
                                     "suite_name": None}
    assert svc.note_label(note.text) == "y" * 60


def test_the_orm_checks_are_the_migrations():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0218_timeline_notes.py"
    spec = importlib.util.spec_from_file_location("m0218_unit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checks = {c.name: str(c.sqltext) for c in TimelineNote.__table__.constraints if c.name and c.name.startswith("ck_")}
    assert checks == {"ck_timeline_notes_range": module.RANGE_CHECK, "ck_timeline_notes_text": module.TEXT_CHECK}
