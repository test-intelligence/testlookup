"""W31 STRAT-12 — the per-person export builder (``services/subject_export_service.py``; contract §1.3, §2.2).

* Every ``export_as != "none"`` inventory location has a projection (a unit of the plan, a statement that
  filters on the subject's id, or ``profile.json``); ``metadata`` emits ONLY its columns; ``full`` drops its
  ``drop`` set and every hash column and redacts the two webhook URLs.
* The real ``build_archive`` → ``_sign_pack`` (a real Ed25519 key) → a fake store → the real verifier, over a
  fake READ session that serves the seeded rows cut to the selected columns (like the database) and refuses
  any write: no member carries a hash / secret sentinel; parts split at ``PART_ROWS_MAX`` / ``PART_BYTES_MAX``;
  the README carries the §4.5 retention paragraph.
* The audit rule: another person is an id (+ display where the subject's own row names them), never an e-mail.
Filter semantics on real rows (a bystander's rows never selected) are proven on Postgres by
``tests/integration/test_subject_export_postgres.py``.
"""
from __future__ import annotations

import ast
import asyncio
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.services import compliance_signing
from app.services import subject_export_service as svc
from app.services.compliance_pack_verify import verify_archive_file
from app.services.subject_data_inventory import INVENTORY, PROFILE_CATEGORY, by_key

SERVICE_PY = Path(__file__).resolve().parents[1] / "app" / "services" / "subject_export_service.py"
#: Non-hex sentinels (a short hex literal collides with uuids), one per secret / hash column.
SENTINELS = {
    "key_hash": "zqxwKEYHASHsentinel", "jti_hash": "zqxwJTIHASHsentinel", "code_hash": "zqxwCODEHASHsentinel",
    "hashed_password": "zqxwPWHASHsentinel", "token_hash": "zqxwTOKENHASHsentinel", "token": "zqxwTOKENsentinel",
    "encrypted_value": "zqxwCIPHERsentinel",
}


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.closed = False

    def mappings(self) -> "_Result":
        return self

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for row in self._rows:
                yield row
        return gen()

    async def close(self) -> None:
        self.closed = True

    def all(self) -> list[tuple]:
        return [tuple(row.values()) for row in self._rows]

    def one(self) -> Any:
        assert len(self._rows) == 1
        return self._rows[0] if self._mapping else tuple(self._rows[0].values())

    _mapping = False


class _MappingResult(_Result):
    _mapping = True


def _base(stmt: Any) -> str:
    for column in stmt.selected_columns:
        table = getattr(column, "table", None)
        if table is not None and getattr(table, "name", "") and not table.name.startswith("x_"):
            return str(table.name)
    return str(stmt.get_final_froms()[0].name)


class ReadSession:
    """Serves ``rows[table]`` cut to the selected columns; the users table answers the profile read (the subject)
    or the display read (everyone else); refuses every write."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]], subject_id: uuid.UUID) -> None:
        self.rows = rows
        self.subject_id = subject_id
        self.statements: list[Any] = []
        self.rollbacks = 0

    def _cut(self, stmt: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = [column.name for column in stmt.selected_columns]
        return [{name: row.get(name) for name in names} for row in rows]

    async def stream(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result(self._cut(stmt, self.rows.get(_base(stmt), [])))

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(stmt)
        base = _base(stmt)
        if base == "mfa_recovery_codes":
            codes = self.rows.get(base, [])
            return _Result([{"total": len(codes), "unused": sum(1 for c in codes if c.get("used_at") is None)}])
        if base == "users":
            names = [column.name for column in stmt.selected_columns]
            subject = [u for u in self.rows["users"] if u["id"] == self.subject_id]
            others = [u for u in self.rows["users"] if u["id"] != self.subject_id]
            if names == ["id", "full_name", "username", "erased_at"]:
                return _Result(self._cut(stmt, others))
            return _MappingResult(self._cut(stmt, subject))
        return _Result(self._cut(stmt, self.rows.get(base, [])))

    def add(self, *_: Any) -> None:  # pragma: no cover - a write on the source session is the bug
        raise AssertionError("the source session must not stage a row")

    async def flush(self) -> None:  # pragma: no cover
        raise AssertionError("the source session must not flush")

    async def commit(self) -> None:  # pragma: no cover
        raise AssertionError("the source session must not commit")

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeStorage:
    def __init__(self, *, corrupt: bool = False, fail_put: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.corrupt = corrupt
        self.fail_put = fail_put

    async def put_object_stream(self, key: str, fileobj: Any, *, size: int, content_type: str, bucket: str) -> None:
        if self.fail_put:
            raise ConnectionRefusedError("storage down")
        fileobj.seek(0)
        data = fileobj.read()
        assert len(data) == size
        if self.corrupt:
            data = data[:-3] + bytes(b ^ 0x5A for b in data[-3:])
        self.objects[key] = data

    async def stream_object(self, key: str, bucket: str | None = None) -> Any:
        data = self.objects[key]
        for start in range(0, len(data), 4096):
            yield data[start:start + 4096]

    async def delete_object(self, key: str, bucket: str | None = None) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


def _signing_key() -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    return compliance_signing.SigningKey(
        key_id=compliance_signing.key_id_for(public),
        public_key_pem=compliance_signing.public_key_pem(public),
        private_key=private,
    )


@pytest.fixture()
def signing(monkeypatch: pytest.MonkeyPatch) -> Any:
    key = _signing_key()

    async def active(db: Any, *, actor: Any, create: bool) -> Any:
        return key

    async def keys(db: Any) -> dict[str, str]:
        return {key.key_id: key.public_key_pem}

    monkeypatch.setattr(compliance_signing, "active_signing_key", active)
    monkeypatch.setattr(compliance_signing, "public_keys", keys)
    return key


# ── the seeded source ───────────────────────────────────────────────────────

def _seed() -> tuple[Any, dict[str, list[dict[str, Any]]]]:
    at = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    subject_id, bystander_id, requester_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    webhook = "https://hooks.slack.com/services/T000/B000/" + "zqxwWEBHOOKsecret"

    def user(uid: uuid.UUID, name: str, full: str) -> dict[str, Any]:
        return {"id": uid, "email": f"{name}@example.org", "username": name, "full_name": full,
                "hashed_password": SENTINELS["hashed_password"], "role": "tester", "is_active": True,
                "is_service_account": False, "is_synthetic": False, "must_change_password": False,
                "avatar_color": "#336699", "mfa_enabled": True, "mfa_enrolled_at": at, "mfa_last_used_step": 4711,
                "last_login_at": at, "failed_login_attempts": 0, "locked_until": None, "created_at": at,
                "updated_at": at, "erased_at": None}

    subject = user(subject_id, "zqxw.subj", "Zqxw Subjperson")
    rows: dict[str, list[dict[str, Any]]] = {
        "users": [subject, user(bystander_id, "yvkq.bystand", "Yvkq Bystander"),
                  user(requester_id, "yvkq.lead", "Yvkq Leadperson")],
        "api_keys": [{"id": uuid.uuid4(), "user_id": subject_id, "name": "ci key", "key_hint": "tl_…ab12",
                      "scopes": ["project:read"], "project_id": None, "is_active": True, "expires_at": None,
                      "last_used_at": at, "created_at": at, "key_hash": SENTINELS["key_hash"],
                      "minted_by_key_id": uuid.uuid4()}],
        "refresh_token_records": [{"id": uuid.uuid4(), "user_id": subject_id, "issued_at": at, "expires_at": at,
                                   "revoked_at": None, "replay_detected": False, "jti_hash": SENTINELS["jti_hash"],
                                   "rotated_to_id": None}],
        "mfa_recovery_codes": [{"id": uuid.uuid4(), "user_id": subject_id, "code_hash": SENTINELS["code_hash"],
                                "used_at": None, "created_at": at},
                               {"id": uuid.uuid4(), "user_id": subject_id, "code_hash": SENTINELS["code_hash"],
                                "used_at": at, "created_at": at}],
        "notification_preferences": [{"id": uuid.uuid4(), "user_id": subject_id, "project_id": None,
                                      "channel": "slack", "enabled": True, "events": {"api_token": "zqxwJSONsecret"},
                                      "failure_rate_threshold": None, "email_override": "zqxw.alt@example.org",
                                      "slack_webhook_url": webhook, "teams_webhook_url": None,
                                      "created_at": at, "updated_at": at}],
        "user_invitations": [{"id": uuid.uuid4(), "email": "zqxw.subj@example.org", "role": "tester",
                              "token": SENTINELS["token"], "token_hash": SENTINELS["token_hash"],
                              "accepted_user_id": subject_id, "invited_by_id": requester_id, "created_at": at,
                              "used_at": at, "expires_at": at, "project_memberships": []}],
        "federated_identities": [{"id": uuid.uuid4(), "user_id": subject_id, "sso_config_id": uuid.uuid4(),
                                  "external_id": "ext-1", "external_email": "zqxw.subj.sso@example.org",
                                  "external_display_name": "Zqxw S", "external_groups": ["qa"],
                                  "last_login_at": at, "created_at": at, "updated_at": at,
                                  "sso_display_name": "Corp IdP"}],
        "test_case_comments": [{"id": uuid.uuid4(), "test_case_id": uuid.uuid4(), "author_id": subject_id,
                                "content": "looks good @yvkq.lead", "comment_type": "general", "parent_id": None,
                                "step_number": None, "is_resolved": False, "created_at": at, "updated_at": at,
                                "test_case_title": "Login works"}],
        "test_case_reviews": [{"id": uuid.uuid4(), "test_case_id": uuid.uuid4(), "reviewer_id": subject_id,
                               "requested_by_id": requester_id, "status": "pending", "human_notes": "ok",
                               "created_at": at, "updated_at": at}],
        "access_audit_logs": [
            {"id": uuid.uuid4(), "actor_user_id": requester_id, "actor_name": "yvkq.lead",
             "target_user_id": subject_id, "project_id": project_id, "action": "role_changed",
             "before_value": {"role": "viewer"},
             "after_value": {"role": "tester", "cc": "yvkq.bystand@example.org", "to": "zqxw.subj@example.org"},
             "created_at": at},
            {"id": uuid.uuid4(), "actor_user_id": subject_id, "actor_name": "zqxw.subj",
             "target_user_id": bystander_id, "project_id": project_id, "action": "member_added",
             "before_value": None, "after_value": {"role": "viewer"}, "created_at": at},
        ],
        "chat_sessions": [{"id": uuid.uuid4(), "user_id": subject_id, "project_id": project_id, "title": "why red",
                           "created_at": at, "updated_at": at}],
        "chat_messages": [{"id": uuid.uuid4(), "session_id": uuid.uuid4(), "role": "user", "content": "why?",
                           "sources": [], "created_at": at}],
        "test_cases": [{"id": uuid.uuid4(), "project_id": project_id, "assigned_to_user_id": subject_id,
                        "triage_updated_by_user_id": subject_id}],
        "projects": [{"id": project_id, "manager_user_id": subject_id}],
    }
    return subject, rows


def _members(storage: FakeStorage) -> dict[str, bytes]:
    (data,) = storage.objects.values()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _run(db: Any, subject: dict[str, Any], storage: FakeStorage, **kwargs: Any) -> svc.SubjectExportOutcome:
    async def no_sleep(_seconds: float) -> None:
        return None

    who = type("Subject", (), {"id": subject["id"], "username": subject["username"], "email": subject["email"]})()
    return asyncio.run(svc.run_subject_export(
        db, job_id=kwargs.pop("job_id", uuid.uuid4()), subject=who, storage=storage,
        now=datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc), sleep=no_sleep, **kwargs,
    ))


def _jsonl(data: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line]


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ── every exported location has a projection ────────────────────────────────

def test_every_exported_location_has_a_projection_filtered_on_the_subject() -> None:
    subject_id = uuid.uuid4()
    planned = [location for _category, units in svc.export_plan() for unit in units for location in unit]
    exported = [location for location in INVENTORY if location.export_as != "none"]
    assert len(exported) >= 120                       # fail-open guard: the inventory is read, not stubbed
    for location in exported:
        if location.category == PROFILE_CATEGORY:
            assert location.key in ("users.*", "mfa_recovery_codes.user_id"), location.key
            continue
        assert planned.count(location) == 1, location.key
        unit = next(unit for _c, units in svc.export_plan() for unit in units if location in unit)
        sql = _sql(svc.unit_statement(unit, subject_id=subject_id, email="zqxw.subj@example.org"))
        if location.match == "fk":
            assert f"{location.table}.{location.column} = '{subject_id}'" in sql, location.key
        else:
            assert "lower(user_invitations.email) = 'zqxw.subj@example.org'" in sql
            assert "user_invitations.accepted_user_id IS NULL" in sql
        assert f"ORDER BY {location.table}." in sql
        for child in location.child_tables:
            child_sql = _sql(svc.child_statement(location, child, subject_id=subject_id, email=None))
            assert f"IN (SELECT {location.table}.id" in child_sql and f"= '{subject_id}'" in child_sql


def test_an_exported_location_without_a_filter_is_refused() -> None:
    import dataclasses

    stray = dataclasses.replace(by_key("agent_investigations.cancelled_by"), export_as="metadata", category="usage")
    with pytest.raises(ValueError, match="no export filter"):
        svc.unit_statement([stray], subject_id=uuid.uuid4(), email="x@example.org")


def test_the_two_shared_members_read_each_row_once() -> None:
    shared = {tuple(loc.key for loc in unit) for _c, units in svc.export_plan() for unit in units if len(unit) > 1}
    assert shared == {
        ("access_audit_logs.actor_user_id", "access_audit_logs.target_user_id"),
        ("identity_events.user_id", "identity_events.actor_id"),
    }


def test_a_metadata_projection_emits_only_its_columns() -> None:
    subject_id = uuid.uuid4()
    context = svc.SubjectContext(id=subject_id)
    for location in INVENTORY:
        if location.export_as != "metadata" or location.category == PROFILE_CATEGORY:
            continue
        stmt = svc.unit_statement([location], subject_id=subject_id, email="a@example.org")
        names = [column.name for column in stmt.selected_columns]
        assert names == [*location.columns, *location.extra], location.key
        row = svc.project_row(location, {name: "v" for name in names}, subject=context)
        assert set(row) == {*location.columns, *location.extra, svc.LOCATION_KEY}, location.key
        assert not set(names) & set(location.drop)


def test_a_full_projection_drops_its_drop_set_and_every_hash_column() -> None:
    from app.models.postgres import Base

    for location in INVENTORY:
        if location.export_as != "full" or location.category == PROFILE_CATEGORY:
            continue
        names = set(svc.exported_columns(location))
        table = Base.metadata.tables[location.table]
        assert not names & (set(location.drop) | svc.HASH_COLUMNS), location.key
        assert names == {c.name for c in table.columns} - set(location.drop) - svc.HASH_COLUMNS, location.key
    users = svc.exported_columns(by_key("users.*"))
    assert "hashed_password" not in users and "mfa_last_used_step" not in users


def test_the_two_webhook_urls_are_redacted_when_set() -> None:
    location = by_key("notification_preferences.user_id")
    context = svc.SubjectContext(id=uuid.uuid4())
    url = "https://hooks.slack.com/services/T000/B000/" + "zqxwWEBHOOKsecret"
    row = svc.project_row(location, {"slack_webhook_url": url, "teams_webhook_url": None,
                                     "email_override": "zqxw.alt@example.org", "events": {"secret": "s"}},
                          subject=context)
    assert row["slack_webhook_url"] == "[REDACTED]"
    assert row["teams_webhook_url"] is None                 # "when set"
    assert row["email_override"] == "zqxw.alt@example.org"  # the person's own address
    assert row["events"] == {"secret": "[REDACTED]"}
    row = svc.project_row(location, {"slack_webhook_url": None, "teams_webhook_url": url}, subject=context)
    assert row == {"slack_webhook_url": None, "teams_webhook_url": "[REDACTED]", "_location": location.key}


def test_a_reference_row_is_a_pointer_only() -> None:
    project_id, row_id = uuid.uuid4(), uuid.uuid4()
    location = by_key("test_cases.assigned_to_user_id")
    row = svc.project_row(location, {"id": row_id, "assigned_to_user_id": uuid.uuid4(), "project_id": project_id},
                          subject=svc.SubjectContext(id=uuid.uuid4()))
    assert row == {"table": "test_cases", "column": "assigned_to_user_id", "row_id": row_id,
                   "project_id": project_id, "label": location.label}
    project = svc.reference_row(by_key("projects.manager_user_id"), {"id": project_id, "manager_user_id": None})
    assert project["project_id"] == project_id


# ── the archive ─────────────────────────────────────────────────────────────

def test_a_signed_archive_verifies_and_carries_no_hash_or_secret(signing: Any) -> None:
    subject, rows = _seed()
    db, storage, job_id = ReadSession(rows, subject["id"]), FakeStorage(), uuid.uuid4()
    outcome = _run(db, subject, storage, job_id=job_id, source="admin")
    assert outcome.key == f"subject-exports/2026/11/04/{job_id}.zip"
    assert outcome.signature == "signed" and outcome.signing_key_id == signing.key_id
    (data,) = storage.objects.values()
    verified = verify_archive_file(io.BytesIO(data), recorded_manifest_sha256=outcome.manifest_sha256,
                                   public_keys={signing.key_id: signing.public_key_pem})
    assert verified["result"] == "verified" and verified["chain"] == "intact"
    assert verified["signature"] == "valid"
    for name, sentinel in SENTINELS.items():
        assert sentinel.encode() not in data and all(sentinel.encode() not in body for body in
                                                     _members(storage).values()), name
    for secret in (b"zqxwWEBHOOKsecret", b"zqxwJSONsecret", b"yvkq.bystand@example.org"):
        assert all(secret not in body for body in _members(storage).values()), secret
    assert db.rollbacks == 1                        # the snapshot ended before sign / write


def test_the_members_and_the_manifest(signing: Any) -> None:
    subject, rows = _seed()
    storage = FakeStorage()
    outcome = _run(ReadSession(rows, subject["id"]), subject, storage, source="self")
    members = _members(storage)
    names = list(members)
    assert names[:2] == ["README.txt", "profile.json"] and names[-2:] == ["manifest.json", "signature.json"]
    manifest = json.loads(members["manifest.json"])
    assert manifest["scope"] == "subject" and manifest["project_id"] is None
    assert manifest["format"] == "testlookup.subject-export" and manifest["schema_version"] == 1
    assert manifest["subject"] == {"id": str(subject["id"]), "username": "zqxw.subj"}
    assert manifest["requested"]["source"] == "self"
    assert manifest["categories"]["comments"] == {"rows": 1, "parts": 1}
    assert manifest["categories"]["chat_messages"] == {"rows": 1, "parts": 1}
    assert outcome.counts["profile"] == 1 and outcome.counts["comments"] == 1
    assert outcome.counts["audit_access"] == 2
    assert outcome.locations["access_audit_logs.actor_user_id"] == 1
    assert outcome.locations["access_audit_logs.target_user_id"] == 1
    assert outcome.locations["mfa_recovery_codes.user_id"] == 2
    exported = {loc.key for loc in INVENTORY if loc.export_as != "none"}
    assert set(outcome.locations) == exported              # every exported location reports a count, 0 included
    comment, = _jsonl(members["comments/part-0001.jsonl"])
    assert comment["content"] == "looks good @yvkq.lead" and comment["test_case_title"] == "Login works"
    assert comment["_location"] == "test_case_comments.author_id"
    key, = _jsonl(members["api_keys/part-0001.jsonl"])
    assert set(key) == {"name", "key_hint", "scopes", "project_id", "is_active", "expires_at", "last_used_at",
                        "created_at", "_location"}


def test_profile_json_carries_the_account_and_the_mfa_facts(signing: Any) -> None:
    subject, rows = _seed()
    storage = FakeStorage()
    _run(ReadSession(rows, subject["id"]), subject, storage)
    profile = json.loads(_members(storage)["profile.json"])
    assert profile["id"] == str(subject["id"]) and profile["email"] == "zqxw.subj@example.org"
    assert profile["username"] == "zqxw.subj" and profile["full_name"] == "Zqxw Subjperson"
    assert profile["mfa"] == {"enabled": True, "enrolled_at": "2026-10-01T09:30:00+00:00", "recovery_codes_left": 1}
    assert "hashed_password" not in profile and "mfa_last_used_step" not in profile
    assert not [key for key in profile if key.startswith("mfa_")]


def test_the_audit_rule_and_other_people(signing: Any) -> None:
    subject, rows = _seed()
    storage = FakeStorage()
    _run(ReadSession(rows, subject["id"]), subject, storage)
    members = _members(storage)
    about_me, by_me = sorted(_jsonl(members["audit_access/part-0001.jsonl"]), key=lambda r: r["action"] != "role_changed")
    # a row ABOUT the subject: the actor as {id, display}; another person's e-mail replaced, theirs kept
    assert about_me["actor_user_id_display"] == "Yvkq Leadperson" and about_me["actor_name"] == "Yvkq Leadperson"
    assert about_me["after_value"] == {"role": "tester", "cc": "[REDACTED_EMAIL]", "to": "zqxw.subj@example.org"}
    # a row the subject ACTED in: the target's id only
    assert by_me["target_user_id"] == str(rows["users"][1]["id"]) and "target_user_id_display" not in by_me
    assert by_me["actor_name"] == "zqxw.subj"
    # each row of the shared member names the location it matched
    assert about_me["_location"] == "access_audit_logs.target_user_id"
    assert by_me["_location"] == "access_audit_logs.actor_user_id"
    review, = _jsonl(members["reviews/part-0001.jsonl"])
    assert review["requested_by_id_display"] == "Yvkq Leadperson" and "reviewer_id_display" not in review
    for invitation in _jsonl(members["invitations/part-0001.jsonl"]):     # accepted + addressed: the same row here
        assert "email" not in invitation and "invited_by_id" not in invitation


def test_a_display_is_never_an_email() -> None:
    assert svc.display_of(None, "yvkq@example.org", None) == "[REDACTED_EMAIL]"
    assert svc.display_of("Yvkq B", "yvkq", None) == "Yvkq B"
    assert svc.display_of("Yvkq B", "yvkq", datetime(2026, 11, 1, tzinfo=timezone.utc)) == "Erased user"
    assert svc.redact_foreign_emails({"a": ["x@example.org", "ME@example.org"]}, frozenset({"me@example.org"})) == {
        "a": ["[REDACTED_EMAIL]", "ME@example.org"]}


def _comments(rows: dict[str, list[dict[str, Any]]], subject_id: uuid.UUID, count: int, text: str) -> None:
    at = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
    rows["test_case_comments"] = [
        {"id": uuid.uuid4(), "test_case_id": uuid.uuid4(), "author_id": subject_id, "content": f"{text} {n}",
         "comment_type": "general", "created_at": at, "updated_at": at, "test_case_title": "T"}
        for n in range(count)
    ]


def test_parts_split_at_part_rows_max(signing: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "PART_ROWS_MAX", 3)
    subject, rows = _seed()
    _comments(rows, subject["id"], 7, "zqxw comment")
    storage = FakeStorage()
    outcome = _run(ReadSession(rows, subject["id"]), subject, storage)
    members = _members(storage)
    parts = sorted(name for name in members if name.startswith("comments/"))
    assert parts == ["comments/part-0001.jsonl", "comments/part-0002.jsonl", "comments/part-0003.jsonl"]
    assert [len(_jsonl(members[name])) for name in parts] == [3, 3, 1]
    assert json.loads(members["manifest.json"])["categories"]["comments"] == {"rows": 7, "parts": 3}
    assert outcome.counts["comments"] == 7


def test_parts_split_before_part_bytes_max(signing: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    subject, rows = _seed()
    _comments(rows, subject["id"], 4, "x" * 200)
    location = by_key("test_case_comments.author_id")
    names = [c.name for c in svc.unit_statement([location], subject_id=subject["id"], email=None).selected_columns]
    raw = {name: rows["test_case_comments"][0].get(name) for name in names}       # cut like the database
    one_row = len(svc.encode_row(svc.project_row(location, raw, subject=svc.SubjectContext(id=subject["id"]))))
    monkeypatch.setattr(svc, "PART_BYTES_MAX", one_row * 2 + 1)
    storage = FakeStorage()
    _run(ReadSession(rows, subject["id"]), subject, storage)
    members = _members(storage)
    parts = sorted(name for name in members if name.startswith("comments/"))
    assert [len(_jsonl(members[name])) for name in parts] == [2, 2]
    assert all(len(members[name]) <= one_row * 2 + 1 for name in parts)


def test_the_readme_carries_the_retention_paragraph(signing: Any) -> None:
    subject, rows = _seed()
    storage = FakeStorage()
    _run(ReadSession(rows, subject["id"]), subject, storage)
    readme = _members(storage)["README.txt"].decode("utf-8")
    assert svc.RETENTION_PARAGRAPH in readme
    for phrase in ("Audit records keep the actor's name after an erasure.", "Art. 17(3)(b) and (e) GDPR",
                   "for as long as their own retention clock allows", 'shown as "Erased user"',
                   "visible only on the audit surfaces, to administrators."):
        assert phrase in svc.RETENTION_PARAGRAPH
    assert "testlookup compliance-pack verify" in readme


def test_an_export_past_the_cap_fails_and_leaves_no_object(signing: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "EXPORT_MAX_BYTES", 600)
    subject, rows = _seed()
    storage = FakeStorage()
    with pytest.raises(svc.SubjectExportFailed) as caught:
        _run(ReadSession(rows, subject["id"]), subject, storage)
    assert caught.value.code == "export_too_large"
    assert caught.value.message == "This person's export is larger than 256 MB."
    assert storage.objects == {}


def test_an_unverified_upload_is_removed(signing: Any) -> None:
    subject, rows = _seed()
    storage = FakeStorage(corrupt=True)
    written: list[str] = []

    async def on_written(key: str) -> None:
        written.append(key)

    with pytest.raises(svc.SubjectExportFailed) as caught:
        _run(ReadSession(rows, subject["id"]), subject, storage, on_written=on_written)
    assert caught.value.code == "export_unverified"
    assert storage.objects == {} and storage.deleted == written and len(written) == 1


def test_a_write_failure_is_export_write(signing: Any) -> None:
    subject, rows = _seed()
    with pytest.raises(svc.SubjectExportFailed) as caught:
        _run(ReadSession(rows, subject["id"]), subject, FakeStorage(fail_put=True))
    assert caught.value.code == "export_write"
    assert caught.value.message == "The export could not be written to storage (ConnectionRefusedError)."


def test_the_module_never_commits_and_logs_no_person() -> None:
    tree = ast.parse(SERVICE_PY.read_text(encoding="utf-8"))
    banned = {"username", "email", "full_name", "key", "storage_key", "name", "display", "manifest_sha256"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "commit":
            raise AssertionError(f"commit at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and node.func.value.id == "_slog":
            assert not node.args[1:], f"positional log args at line {node.lineno}"
            assert not {kw.arg for kw in node.keywords} & banned, f"a person / key logged at line {node.lineno}"



