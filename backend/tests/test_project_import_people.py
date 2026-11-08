"""W37 F-2 / F-7 — Add members (matched by e-mail) and Keep reviews on import, without a database
(contract §1 People / Reviews, §2.2, §2.3, D-5, D-6).

A recording session answers the people SELECT, the copy's members and the username map, records every
statement WITH its params and every staged object. The archives are built like ``test_project_import_service``'s.
"""
from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from app.services import project_import_service as svc
from tests.test_project_import_service import (
    FakeStorage,
    _answers,
    _assert_nothing_written,
    _ImportSession,
    _jsonl,
    _refused,
    _suite,
    build_archive,
)


@pytest.fixture
def import_world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """``run_import`` end to end on a recording session (``test_project_import_service``'s world, narrowed):
    provisioning and the initial release faked, the legacy membership mirror off (after the HYG-04 cutover)."""
    from app.services import suite_membership_cutover as cutover

    async def provision(db: Any, *, project: Any, creator: Any, provision_release: bool = True,
                        activity_context: Any = None) -> list[Any]:
        return []

    async def ensure_release(db: Any, project: Any) -> None:
        return None

    async def legacy_model_active(db: Any, *, fresh: bool = False) -> bool:
        return False

    monkeypatch.setattr("app.services.project_provisioning.provision_new_project", provision)
    monkeypatch.setattr("app.services.release_lifecycle_service.ensure_active_release_for_new_project", ensure_release)
    monkeypatch.setattr(cutover, "legacy_model_active", legacy_model_active)
    return SimpleNamespace()

IMPORTER = uuid.uuid4()
SYNTHETIC_LEAD = uuid.uuid4()


def _ids(*names: str) -> SimpleNamespace:
    return SimpleNamespace(**{name: uuid.uuid4() for name in names})


SRC = _ids("alice", "bob", "carol", "dave", "imp", "erin", "frank", "gina", "rev", "out")      # source users
TGT = _ids("alice", "bob1", "bob2", "erin", "frank", "gina", "rev", "out")                     # accounts here

DIRECTORY = [
    {"id": str(SRC.alice), "username": "alice", "full_name": "Alice", "email": "Alice@Example.test"},
    {"id": str(SRC.bob), "username": "bob", "full_name": None, "email": "bob@example.test"},
    {"id": str(SRC.carol), "username": "carol", "full_name": None, "email": "carol@example.test"},
    {"id": str(SRC.dave), "username": "dave", "full_name": None, "email": "dave@example.test"},
    {"id": str(SRC.imp), "username": "imp", "full_name": None, "email": "importer@example.test"},
    {"id": str(SRC.erin), "username": "erin", "full_name": None, "email": "erin@example.test"},
    {"id": str(SRC.frank), "username": "frank", "full_name": None, "email": "frank@example.test"},
    {"id": str(SRC.gina), "username": "gina", "full_name": None, "email": "gina@example.test"},
    {"id": str(SRC.rev), "username": "reviewer", "full_name": None, "email": "rev@example.test"},
    {"id": str(SRC.out), "username": "outsider", "full_name": None},          # an author only: no e-mail
]
MEMBERS = [
    {"user_id": str(SRC.alice), "role": "QA_LEAD"},        # → alice, whose own role is QA_ENGINEER: capped
    {"user_id": str(SRC.bob), "role": "QA_ENGINEER"},      # two accounts share the address (case variants)
    {"user_id": str(SRC.carol), "role": "ADMIN"},          # nobody here has the address
    {"user_id": str(SRC.dave), "role": "SUPERUSER"},       # not a role this deployment knows
    {"user_id": str(SRC.imp), "role": "VIEWER"},           # the importer: already a member, skipped silently
    {"user_id": str(SRC.erin), "role": "VIEWER"},          # erin is an ADMIN here: VIEWER is kept (never raised)
    {"user_id": str(SRC.gina), "role": "QA_LEAD"},
    {"user_id": str(SRC.rev), "role": "QA_ENGINEER"},
]
OWNERS = [
    {"suite_name": "Suite A", "user_id": str(SRC.alice)},  # a QA_ENGINEER member → owner_not_lead
    {"suite_name": "Suite B", "user_id": str(SRC.frank)},  # a QA lead here, but not a member → owner_not_lead
    {"suite_name": "Suite C", "user_id": str(SRC.gina)},   # a QA_LEAD member → kept
    {"suite_name": "Suite D", "user_id": str(SRC.imp)},    # an instance ADMIN → kept
    {"suite_name": "Suite E", "user_id": str(SRC.bob)},    # ambiguous
]
#: the people SELECT's answer: (id, lower(email), global role) — the SQL already kept the eligible accounts
ACCOUNTS = [
    (TGT.alice, "alice@example.test", "QA_ENGINEER"), (TGT.bob1, "bob@example.test", "QA_LEAD"),
    (TGT.bob2, "bob@example.test", "VIEWER"), (IMPORTER, "importer@example.test", "ADMIN"),
    (TGT.erin, "erin@example.test", "ADMIN"), (TGT.frank, "frank@example.test", "QA_LEAD"),
    (TGT.gina, "gina@example.test", "QA_LEAD"), (TGT.rev, "rev@example.test", "QA_LEAD"),
]
COPY = [(IMPORTER, "ADMIN", "ADMIN", False), (SYNTHETIC_LEAD, "QA_LEAD", "QA_LEAD", True)]
USERNAMES = [(TGT.rev, "reviewer"), (TGT.out, "outsider")]


class PeopleSession(_ImportSession):
    """Answers the people / copy-members / username SELECTs; records params and staged objects."""

    def __init__(self) -> None:
        base = _answers()

        def answer(sql: str) -> list[Any]:
            if "lower(users.email)" in sql:
                return list(ACCOUNTS)
            if "FROM project_members JOIN users" in sql:
                return list(COPY)
            if "lower(users.username)" in sql:
                return list(USERNAMES)
            return base(sql)

        super().__init__(answer)
        self.params: list[tuple[str, Any]] = []
        self.added: list[Any] = []
        self.job_row = SimpleNamespace(options={})

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.params.append((str(statement), params))
        return await super().execute(statement, params)

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        super().add(obj)

    def rows_of(self, table: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for sql, params in self.params:
            if sql.startswith(f"INSERT INTO {table} "):
                out += [{key[2:]: value for key, value in row.items()} for row in params]
        return out

    def first(self, prefix: str) -> int:
        return next(i for i, sql in enumerate(self.statements) if sql.startswith(prefix))


def _people_archive(entities: Optional[dict[str, list[dict[str, Any]]]] = None, **patch: Any) -> bytes:
    return build_archive(
        entities or {"test_suites": [_suite()]}, directory=DIRECTORY, manifest_patch={"schema_version": 3, **patch},
        extra_members={"directory/members.jsonl": _jsonl(MEMBERS), "directory/suite_owners.jsonl": _jsonl(OWNERS)},
    )


async def _run(blob: bytes, monkeypatch: pytest.MonkeyPatch, **options: Any) -> tuple[Any, PeopleSession, dict, dict]:
    session = PeopleSession()
    activity: dict[str, Any] = {}

    async def record(db: Any, **kwargs: Any) -> None:
        activity.update(kwargs.get("context") or {})

    monkeypatch.setattr("app.services.activity.service.record", record)
    outcome: dict[str, Any] = {}

    async def on_table(done: int, total: int) -> None:
        return None

    report = await svc.run_import(
        session, job=SimpleNamespace(id=uuid.uuid4(), project_name=None, storage_key="project-imports/x.zip"),
        storage=FakeStorage(blob), actor=SimpleNamespace(id=IMPORTER, role="ADMIN", username="importer"),
        now=dt.datetime.now(dt.timezone.utc), on_table=on_table, outcome=outcome, **options,
    )
    return report, session, outcome, activity


def _skips(report: Any, entity: str) -> dict[str, int]:
    return {s.reason: s.count for s in report.skipped if s.entity == entity}


# ── I-2: people only at schema 3, every line proven before the first write ──

_PEOPLE_REFUSALS = [
    ("members-in-schema-1", lambda: build_archive(extra_members={"directory/members.jsonl": _jsonl(MEMBERS[:1])},
                                                  directory=DIRECTORY[:1]),
     "tampered", "an unexpected member"),
    ("suite-owners-in-schema-2", lambda: build_archive(
        extra_members={"directory/suite_owners.jsonl": b""}, manifest_patch={"schema_version": 2, "history": {}}),
     "tampered", "an unexpected member"),
    ("email-in-a-schema-1-directory", lambda: build_archive(directory=DIRECTORY[:1]), "invalid_row", "(email)"),
    ("reviews-in-schema-1", lambda: build_archive({"test_case_reviews": [{"id": str(uuid.uuid4())}]}),
     "unknown_entity", "test_case_reviews"),
    ("member-not-in-the-directory", lambda: build_archive(
        directory=DIRECTORY[:1], manifest_patch={"schema_version": 3},
        extra_members={"directory/members.jsonl": _jsonl([MEMBERS[1]])}), "invalid_row", "(user_id)"),
    ("member-listed-twice", lambda: build_archive(
        directory=DIRECTORY[:1], manifest_patch={"schema_version": 3},
        extra_members={"directory/members.jsonl": _jsonl([MEMBERS[0], MEMBERS[0]])}), "invalid_row", "duplicate user_id"),
    ("suite-owned-twice", lambda: build_archive(
        directory=DIRECTORY[:1], manifest_patch={"schema_version": 3},
        extra_members={"directory/suite_owners.jsonl": _jsonl([OWNERS[0], OWNERS[0]])}), "invalid_row",
     "duplicate suite_name"),
    ("a-member-line-with-an-extra-key", lambda: build_archive(
        directory=DIRECTORY[:1], manifest_patch={"schema_version": 3},
        extra_members={"directory/members.jsonl": _jsonl([{**MEMBERS[0], "is_admin": True}])}), "invalid_row", ""),
]


@pytest.mark.parametrize(("builder", "code", "fragment"), [r[1:] for r in _PEOPLE_REFUSALS],
                         ids=[r[0] for r in _PEOPLE_REFUSALS])
async def test_people_and_reviews_are_read_only_at_schema_3_and_refused_before_any_write(builder, code, fragment):
    failure, session, calls = await _refused(builder())
    assert failure.code == code and fragment in failure.message, failure.message
    _assert_nothing_written(session, calls)


async def test_two_open_reviews_of_one_case_are_tampered_before_any_write():
    suite, case = _suite(), str(uuid.uuid4())
    reviews = [{"id": str(uuid.uuid4()), "test_case_id": case, "status": status} for status in ("pending", "in_progress")]
    failure, session, calls = await _refused(build_archive(
        {"test_suites": [suite], "managed_test_cases": [{"id": case, "title": "x", "test_suite_id": suite["id"]}],
         "test_case_reviews": reviews}, manifest_patch={"schema_version": 3}))
    assert failure.code == "tampered" and "two open reviews" in failure.message
    _assert_nothing_written(session, calls)


def test_the_people_select_matches_lower_email_among_eligible_accounts_only():
    sql = str(svc.people_statement(["a@x.test"]))
    assert sql.startswith("SELECT users.id, lower(users.email)")
    for rule in ("lower(users.email) IN", "users.is_active IS true", "users.erased_at IS NULL",
                 "users.is_synthetic IS false", "users.is_service_account IS false"):
        assert rule in sql, rule


def test_a_claimant_needs_the_lifecycles_reviewer_roles():
    from app.services.test_case_lifecycle_service import REVIEWER_ROLES

    assert svc._REVIEWER_ROLES == frozenset(REVIEWER_ROLES)


# ── Add members ─────────────────────────────────────────────────────────────

async def test_add_members_stages_matched_members_capped_and_audited_before_the_catalog(import_world, monkeypatch):
    report, session, outcome, activity = await _run(_people_archive(), monkeypatch, add_members=True)
    members = {row["user_id"]: row["role"] for row in session.rows_of("project_members")}
    # D-5: the archived role, never above the person's own (alice QA_LEAD → QA_ENGINEER; erin stays VIEWER)
    assert members == {TGT.alice: "QA_ENGINEER", TGT.erin: "VIEWER", TGT.gina: "QA_LEAD", TGT.rev: "QA_ENGINEER"}
    assert all(row["project_id"] == report.project_id and row["source"] is None
               for row in session.rows_of("project_members"))
    assert _skips(report, "project_members") == {"email_ambiguous": 1, "email_not_found": 1, "role_unknown": 1}
    owners = {row["suite_name"]: row["owner_user_id"] for row in session.rows_of("test_suite_owners")}
    assert owners == {"Suite C": TGT.gina, "Suite D": IMPORTER}
    assert _skips(report, "test_suite_owners") == {"owner_not_lead": 2, "email_ambiguous": 1}
    assert report.inserted["project_members"] == 4 and report.inserted["test_suite_owners"] == 2
    assert report.options_ignored == []
    # the access audit, one per added member, in this transaction
    audits = [obj for obj in session.added if type(obj).__name__ == "AccessAuditLog"]
    assert sorted((a.action, a.target_user_id, a.after_value["role"]) for a in audits) == sorted(
        ("member_added", user, role) for user, role in members.items())
    assert {a.project_id for a in audits} == {report.project_id} and {a.actor_user_id for a in audits} == {IMPORTER}
    # ONE people SELECT (I-3, before the first write); members + owners before the first catalog row
    people = [i for i, sql in enumerate(session.statements) if "lower(users.email)" in sql]
    assert len(people) == 1
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in session.statements[:people[0] + 1])
    assert session.first("INSERT INTO project_members") < session.first("INSERT INTO test_suite_owners") \
        < session.first("INSERT INTO test_suites")
    assert outcome["added_member_ids"] == list(members)
    assert activity["members_added"] == 4 and "reviews_kept" not in activity


async def test_the_default_import_adds_nobody_and_says_the_option_was_not_chosen(import_world, monkeypatch):
    report, session, outcome, activity = await _run(_people_archive(), monkeypatch)
    assert not any("lower(users.email)" in sql for sql in session.statements)      # no people SELECT
    assert not any(sql.startswith(("INSERT INTO project_members", "INSERT INTO test_suite_owners"))
                   for sql in session.statements)
    assert not [obj for obj in session.added if type(obj).__name__ == "AccessAuditLog"]
    assert _skips(report, "project_members") == {"not_chosen": len(MEMBERS)}
    assert _skips(report, "test_suite_owners") == {"not_chosen": len(OWNERS)}
    assert "project_members" not in report.inserted and report.options_ignored == []
    assert "added_member_ids" not in outcome and "members_added" not in activity


@pytest.mark.parametrize(("options", "ignored"), [
    ({"add_members": True}, ["add_members"]), ({"keep_reviews": True}, ["keep_reviews"]),
    ({"add_members": True, "keep_reviews": True}, ["add_members", "keep_reviews"]),
])
async def test_an_option_the_archive_cannot_honour_is_reported_never_refused(import_world, monkeypatch, options, ignored):
    report, session, outcome, activity = await _run(build_archive({"test_suites": [_suite()]}), monkeypatch, **options)
    assert report.options_ignored == ignored
    assert not any("lower(users.email)" in sql for sql in session.statements)
    assert set(report.inserted) & {"project_members", "test_suite_owners", "test_case_reviews"} == set()
    assert "members_added" not in activity and "reviews_kept" not in activity


# ── Keep reviews ────────────────────────────────────────────────────────────

def _review_world() -> tuple[dict[str, list[dict[str, Any]]], SimpleNamespace]:
    suite = _suite()
    c = _ids("requested", "claimed", "stray_claim", "no_row", "approved", "draft")
    r = _ids("requested", "claimed", "stray_claim", "approved", "orphan")
    k = _ids("first", "reply")

    def case(cid: uuid.UUID, status: str, reviewer: Optional[uuid.UUID] = None) -> dict[str, Any]:
        return {"id": str(cid), "title": status, "test_suite_id": suite["id"], "status": status,
                "author_id": str(SRC.out), "reviewer_id": str(reviewer) if reviewer else None}

    def review(rid: uuid.UUID, cid: uuid.UUID, status: str, reviewer: Optional[uuid.UUID] = None) -> dict[str, Any]:
        return {"id": str(rid), "test_case_id": str(cid), "status": status, "requested_by_id": str(SRC.out),
                "reviewer_id": str(reviewer) if reviewer else None, "human_notes": "notes"}

    entities = {
        "test_suites": [suite],
        "managed_test_cases": [
            case(c.requested, "review_requested"), case(c.claimed, "under_review", SRC.rev),
            case(c.stray_claim, "under_review", SRC.out), case(c.no_row, "under_review", SRC.rev),
            case(c.approved, "approved"), case(c.draft, "draft"),
        ],
        "test_case_reviews": [
            review(r.requested, c.requested, "pending"), review(r.claimed, c.claimed, "in_progress", SRC.rev),
            review(r.stray_claim, c.stray_claim, "in_progress", SRC.out),     # outsider: not a member here
            review(r.approved, c.approved, "approved", SRC.rev), review(r.orphan, c.draft, "pending"),
        ],
        "test_case_comments": [
            {"id": str(k.first), "test_case_id": str(c.requested), "author_id": str(SRC.rev), "content": "why?"},
            {"id": str(k.reply), "test_case_id": str(c.requested), "author_id": str(SRC.out), "content": "because",
             "parent_id": str(k.first)},
        ],
    }
    return entities, SimpleNamespace(c=c, r=r, k=k)


async def test_keep_reviews_keeps_what_can_still_be_acted_on_and_unclaims_the_rest(import_world, monkeypatch):
    entities, ids = _review_world()
    report, session, _outcome, activity = await _run(_people_archive(entities), monkeypatch,
                                                     add_members=True, keep_reviews=True)
    statuses = sorted(((row["title"], row["status"], row["reviewer_id"]) for row in session.rows_of("managed_test_cases")),
                      key=str)
    assert statuses == sorted([
        ("review_requested", "review_requested", None),       # its pending review travelled: kept
        ("under_review", "under_review", TGT.rev),             # claimed by a reviewer-role member: kept, re-pointed
        ("under_review", "review_requested", None),            # claimed by someone not here: unclaimed
        ("under_review", "draft", None),                       # no open review in the archive: today's rule
        ("approved", "approved", None), ("draft", "draft", None),
    ], key=str), statuses
    assert report.switched_off["managed_test_cases.review_unclaimed"] == 1
    assert report.switched_off["managed_test_cases.review_reset"] == 1
    rows = sorted(((row["status"], row["reviewer_id"]) for row in session.rows_of("test_case_reviews")), key=str)
    assert rows == sorted([("pending", None), ("in_progress", TGT.rev), ("pending", None), ("approved", TGT.rev)],
                          key=str), rows
    # the orphan open review (its case is a draft) is not inserted, and says so
    assert {(s.entity, s.column, s.reason): s.count for s in report.skipped if s.entity == "test_case_reviews"} == {
        ("test_case_reviews", "status", "row_unresolved"): 1}
    # the claimant of the kept claim is NOT reported cleared; the unclaimed case's source claimant is
    cleared = {(s.entity, s.column, s.reason): s.count for s in report.cleared}
    assert cleared[("managed_test_cases", "reviewer_id", "assignment")] == 2       # stray_claim + no_row
    assert report.inserted["test_case_reviews"] == 4 and report.inserted["test_case_comments"] == 2
    # a reply's parent is set in the second pass
    assert any(sql.startswith("UPDATE test_case_comments SET parent_id") for sql in session.statements)
    assert activity["reviews_kept"] == 4 and activity["members_added"] == 4
    assert session.first("INSERT INTO managed_test_cases") < session.first("INSERT INTO test_case_reviews")


async def test_without_keep_reviews_the_reviews_stay_behind_and_in_review_cases_arrive_draft(import_world, monkeypatch):
    entities, _ids_ = _review_world()
    report, session, _outcome, activity = await _run(_people_archive(entities), monkeypatch)
    assert not any(sql.startswith(("INSERT INTO test_case_reviews", "INSERT INTO test_case_comments"))
                   for sql in session.statements)
    assert sorted(row["status"] for row in session.rows_of("managed_test_cases")) == [
        "approved", "draft", "draft", "draft", "draft", "draft"]
    assert report.switched_off["managed_test_cases.review_reset"] == 4
    assert "managed_test_cases.review_unclaimed" not in report.switched_off
    assert _skips(report, "test_case_reviews") == {"not_chosen": 5}
    assert _skips(report, "test_case_comments") == {"not_chosen": 2}
    assert not {"test_case_reviews", "test_case_comments"} & set(report.inserted)
    assert "reviews_kept" not in activity


async def test_keep_reviews_without_add_members_keeps_a_claim_only_for_a_member_already_here(import_world, monkeypatch):
    entities, _ids_ = _review_world()
    report, session, _outcome, _activity = await _run(_people_archive(entities), monkeypatch, keep_reviews=True)
    assert not any(sql.startswith("INSERT INTO project_members") for sql in session.statements)
    statuses = sorted(row["status"] for row in session.rows_of("managed_test_cases"))
    # the reviewer is no member of the copy (members were not added): both claims are unclaimed
    assert statuses == ["approved", "draft", "draft", "review_requested", "review_requested", "review_requested"]
    assert report.switched_off["managed_test_cases.review_unclaimed"] == 2
    assert _skips(report, "project_members") == {"not_chosen": len(MEMBERS)}
