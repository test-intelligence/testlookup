"""W37 F-2 / F-7 — the export side of Include members / Include reviews (contract §1 People / Reviews, D-1, D-5, D-6).

The fake session of ``test_project_export_service`` serves rows per table; this one also answers the two people
SELECTs and cuts the users rows to the selected columns (an ``email`` only reaches the archive when it is
selected). The archive is the real ``build_archive`` output, signed and read back.
"""
from __future__ import annotations

import json
import uuid
import zipfile
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.models.schemas import ProjectDataOptions
from app.services import compliance_signing
from app.services import project_export_service as svc
from app.services import project_export_vocab as vocab
from app.services.project_export_entities import CATALOG_TABLES, OPTIONAL_TABLES, plan
from tests.test_project_export_service import FakeStorage, RecordingSession, _Result, _run, _seed, _signing_key, _zip


@pytest.fixture()
def signing(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real Ed25519 key as the deployment's signing key (``test_project_export_service``'s fixture, restated)."""
    key = _signing_key()

    async def active(db: Any, *, actor: Any, create: bool) -> Any:
        return key

    async def keys(db: Any) -> dict[str, str]:
        return {key.key_id: key.public_key_pem}

    monkeypatch.setattr(compliance_signing, "active_signing_key", active)
    monkeypatch.setattr(compliance_signing, "public_keys", keys)
    return key

TOKEN = "ghp" + "_" + "SENTINELghpTokenXyzzyQwertyPlughWxyz"
EMAILS = {"alice": "Alice.A@people.example.test", "bob": "bob@people.example.test",
          "carol": "carol@people.example.test", "dave": "dave@people.example.test"}


class PeopleSession(RecordingSession):
    """+ ``project_members`` / ``test_suite_owners`` answers; users cut to the selected columns and the IN list."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]], users: list[tuple], *,
                 members: list[tuple] = (), owners: list[tuple] = ()) -> None:
        super().__init__(rows, users)
        self.members, self.owners = list(members), list(owners)

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(stmt)
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        if "FROM project_members" in sql:
            return _Result([{"user_id": uid, "role": role} for uid, role in self.members])
        if "FROM test_suite_owners" in sql:
            return _Result([{"suite_name": name, "owner_user_id": uid} for name, uid in self.owners])
        if "FROM users" in sql:
            wanted = set(stmt.whereclause.right.value)
            names = [column.name for column in stmt.selected_columns]
            out = []
            for uid, username, full_name in self.users:
                if uid in wanted:
                    full = {"id": uid, "username": username, "full_name": full_name, "email": EMAILS[username]}
                    out.append({name: full[name] for name in names})
            return _Result(out)
        return _Result([])


def _people() -> tuple[Any, dict[str, list[dict[str, Any]]], list[tuple], list[tuple], list[tuple]]:
    project, rows, users = _seed()            # alice (author) + bob (assignee) are referenced by the catalog
    carol, dave = uuid.uuid4(), uuid.uuid4()
    users = [*users, (carol, "carol", "Carol C"), (dave, "dave", None)]
    alice = users[0][0]
    members = [(alice, "QA_LEAD"), (carol, "QA_ENGINEER")]       # the SQL already kept the human members only
    owners = [("Suite 0", carol), ("Suite 1", dave)]
    return project, rows, users, members, owners


def _lines(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in archive.read(name).decode().splitlines()]


def _everything(archive: zipfile.ZipFile) -> str:
    return "".join(archive.read(name).decode("utf-8", "replace") for name in archive.namelist())


# ── the statements ──────────────────────────────────────────────────────────

def test_the_members_select_keeps_the_projects_human_members_only() -> None:
    project_id = uuid.uuid4()
    sql = str(svc.members_statement(project_id).compile(dialect=postgresql.dialect()))
    assert sql.startswith("SELECT project_members.user_id, project_members.role")
    assert "JOIN users ON users.id = project_members.user_id" in sql
    for rule in ("project_members.project_id = ", "users.is_active IS true", "users.is_synthetic IS false",
                 "users.is_service_account IS false", "users.erased_at IS NULL"):
        assert rule in sql, rule
    assert "users.email" not in sql and "ORDER BY project_members.user_id" in sql


def test_the_suite_owners_select_keeps_human_owners_only() -> None:
    sql = str(svc.suite_owners_statement(uuid.uuid4()).compile(dialect=postgresql.dialect()))
    assert sql.startswith("SELECT test_suite_owners.suite_name, test_suite_owners.owner_user_id")
    assert "JOIN users ON users.id = test_suite_owners.owner_user_id" in sql
    for rule in ("test_suite_owners.project_id = ", "users.is_active IS true", "users.is_synthetic IS false",
                 "users.is_service_account IS false", "users.erased_at IS NULL"):
        assert rule in sql, rule


# ── catalog only: phase 1 exactly ───────────────────────────────────────────

@pytest.mark.parametrize("options", [None, ProjectDataOptions(), {"include_members": False, "include_reviews": False}])
def test_a_catalog_only_export_stays_schema_1_byte_for_byte_and_carries_no_email(signing: Any, options: Any) -> None:
    project, rows, users, members, owners = _people()
    rows["test_case_reviews"] = [{"id": uuid.uuid4(), "test_case_id": rows["managed_test_cases"][0]["id"]}]
    job_id = uuid.uuid4()
    baseline, storage = FakeStorage(), FakeStorage()
    _run(RecordingSession(rows, users[:2]), project, baseline, sign_db=RecordingSession({}), job_id=job_id)
    db = PeopleSession(rows, users, members=members, owners=owners)
    _run(db, project, storage, sign_db=RecordingSession({}), job_id=job_id, options=options)
    assert storage.objects[next(iter(storage.objects))] == baseline.objects[next(iter(baseline.objects))]
    archive = _zip(storage)
    assert not any(name.startswith("directory/") and name != "directory/users.jsonl" for name in archive.namelist())
    text = _everything(archive)
    assert "email" not in text and not any(address in text for address in EMAILS.values())
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["schema_version"] == 1 and manifest["directory"] == {"users": 2}
    assert set(manifest["entities"]) == set(CATALOG_TABLES)
    assert {"test_case_comments", "test_case_reviews"} <= set(manifest["excluded"]["per_user_state"])
    assert len(db.statements) == len(plan()) + 1      # no people SELECT


# ── Include members ─────────────────────────────────────────────────────────

def test_include_members_writes_the_members_the_suite_owners_and_only_their_emails(signing: Any) -> None:
    project, rows, users, members, owners = _people()
    storage = FakeStorage()
    _run(PeopleSession(rows, users, members=members, owners=owners), project, storage,
         sign_db=RecordingSession({}), options=ProjectDataOptions(include_members=True))
    archive = _zip(storage)
    names = archive.namelist()
    assert names[-6:] == ["directory/users.jsonl", "directory/members.jsonl", "directory/suite_owners.jsonl",
                          "README.txt", "manifest.json", "signature.json"]
    alice, bob, carol, dave = (user[0] for user in users)
    assert _lines(archive, "directory/members.jsonl") == [
        {"role": "QA_LEAD", "user_id": str(alice)}, {"role": "QA_ENGINEER", "user_id": str(carol)},
    ]
    assert _lines(archive, "directory/suite_owners.jsonl") == [
        {"suite_name": "Suite 0", "user_id": str(carol)}, {"suite_name": "Suite 1", "user_id": str(dave)},
    ]
    directory = {line["id"]: line for line in _lines(archive, "directory/users.jsonl")}
    # every person the two files name is in the directory WITH an e-mail; bob (only an assignee) has none
    assert directory[str(alice)]["email"] == EMAILS["alice"] and directory[str(carol)]["email"] == EMAILS["carol"]
    assert directory[str(dave)] == {"id": str(dave), "username": "dave", "full_name": None, "email": EMAILS["dave"]}
    assert directory[str(bob)] == {"id": str(bob), "username": "bob", "full_name": None}
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["schema_version"] == vocab.SCHEMA_VERSION == 3 and "history" not in manifest
    assert manifest["directory"] == {"users": 4, "members": 2, "suite_owners": 2}
    assert set(manifest["entities"]) == set(CATALOG_TABLES)          # members alone add no entity
    # D-3 (decided): no history → no plan execution result columns, even at schema 3
    assert "execution_status" not in manifest["entities"]["test_plan_items"]["columns"]
    readme = archive.read("README.txt").decode()
    assert "directory/members.jsonl" in readme and "Add members" in readme and "members, roles" not in readme


def test_an_export_with_members_but_none_to_list_still_writes_both_files(signing: Any) -> None:
    project, rows, users, _members, _owners = _people()
    storage = FakeStorage()
    _run(PeopleSession(rows, users), project, storage, sign_db=RecordingSession({}),
         options={"include_members": True})
    archive = _zip(storage)
    assert archive.read("directory/members.jsonl") == b"" and archive.read("directory/suite_owners.jsonl") == b""
    assert json.loads(archive.read("manifest.json"))["directory"] == {"users": 2, "members": 0, "suite_owners": 0}
    assert "email" not in archive.read("directory/users.jsonl").decode()


# ── Include reviews ─────────────────────────────────────────────────────────

def test_include_reviews_writes_the_reviews_and_comments_scrubbed(signing: Any) -> None:
    project, rows, users, _members, _owners = _people()
    case_id = rows["managed_test_cases"][0]["id"]
    alice, bob = users[0][0], users[1][0]
    rows["test_case_reviews"] = [{
        "id": uuid.uuid4(), "test_case_id": case_id, "reviewer_id": bob, "requested_by_id": alice,
        "status": "in_progress", "human_notes": "looks fine, used " + TOKEN, "ai_review_notes": {"issues": [TOKEN]},
    }]
    parent = uuid.uuid4()
    rows["test_case_comments"] = [
        {"id": parent, "test_case_id": case_id, "author_id": alice, "content": "token " + TOKEN, "parent_id": None},
        {"id": uuid.uuid4(), "test_case_id": case_id, "author_id": bob, "content": "reply", "parent_id": parent},
    ]
    storage = FakeStorage()
    _run(PeopleSession(rows, users), project, storage, sign_db=RecordingSession({}),
         options=ProjectDataOptions(include_reviews=True))
    archive = _zip(storage)
    names = archive.namelist()
    order = [names.index(n) for n in ("entities/managed_test_cases/part-0001.jsonl",
                                      "entities/test_case_reviews/part-0001.jsonl",
                                      "entities/test_case_comments/part-0001.jsonl", "directory/users.jsonl")]
    assert order == sorted(order)                                    # after the catalog, before the directory
    (review,) = _lines(archive, "entities/test_case_reviews/part-0001.jsonl")
    assert review["status"] == "in_progress" and review["reviewer_id"] == str(bob)
    assert "SENTINEL" not in review["human_notes"] and "[REDACTED]" in review["human_notes"]
    assert "SENTINEL" not in json.dumps(review["ai_review_notes"])
    comments = _lines(archive, "entities/test_case_comments/part-0001.jsonl")
    assert "SENTINEL" not in comments[0]["content"] and comments[1]["parent_id"] == str(parent)
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["schema_version"] == 3 and "history" not in manifest
    assert set(manifest["entities"]) == set(CATALOG_TABLES) | set(OPTIONAL_TABLES)
    assert manifest["entities"]["test_case_reviews"]["rows"] == 1
    assert not set(OPTIONAL_TABLES) & {t for tables in manifest["excluded"].values() for t in tables}
    assert manifest["directory"] == {"users": 2}                     # reviews alone list no members
    assert not any(n in names for n in ("directory/members.jsonl", "directory/suite_owners.jsonl"))
    assert "Keep reviews" in archive.read("README.txt").decode()


def test_the_readme_names_each_chosen_section_once() -> None:
    assert svc.readme_text(history=False) == svc.README_TEXT
    assert svc.readme_text(history=True) == svc.HISTORY_README_TEXT
    both = svc.readme_text(history=True, members=True, reviews=True)
    assert both.endswith(svc.MEMBERS_README_TEXT + svc.REVIEWS_README_TEXT)
    assert "members, roles" not in both and "members, roles" in svc.HISTORY_README_TEXT


def test_the_people_select_runs_only_when_members_are_chosen() -> None:
    project, rows, users, members, owners = _people()

    async def build(options: Any) -> list[str]:
        db = PeopleSession(rows, users, members=members, owners=owners)
        stats: dict[str, Any] = {}
        names = [name async for name, _ in svc.iter_members(db, project_id=project.id, stats=stats, **options)]
        return names

    import asyncio

    plain = asyncio.run(build({}))
    chosen = asyncio.run(build({"include_members": True}))
    assert "directory/members.jsonl" not in plain and "directory/members.jsonl" in chosen
    assert set(chosen) - set(plain) == {"directory/members.jsonl", "directory/suite_owners.jsonl"}
