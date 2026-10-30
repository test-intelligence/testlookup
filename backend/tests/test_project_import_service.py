"""W24 ADM-05 — the project import (``project_import_service``), without a database.

* Every refusal happens BEFORE the first write: a recording session sees only
  SELECTs (no INSERT / UPDATE / DELETE, no ``add`` / ``flush``) and the
  progress callbacks never run.
* The name / slug ladder, users by USERNAME only, the I-7 active-release
  cases, ``REVIEW_RESET_STATES`` → draft, the inert values, the FK remap.
* Open point 7 (imported workflow definitions) is pinned at the bottom.

The live round trip is ``tests/integration/test_project_import_postgres.py``.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import uuid
import zipfile
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.sql.selectable import Select

from app.services import compliance_signing
from app.services import project_export_vocab as vocab
from app.services import project_import_service as svc
from app.services.project_export_entities import spec_for
from app.services.project_import_service import ProjectImportFailed

# ── archive builder ──────────────────────────────────────────────────────────


def _project_row(**overrides: Any) -> dict[str, Any]:
    row = {"description": "Payments catalogue", "tags": ["payments"], "manager_user_id": None,
           "default_qa_lead_user_id": None, "require_case_review": False}
    row.update(overrides)
    return row


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8") + b"\n" for r in rows
    )


def build_archive(
    entities: Optional[dict[str, list[dict[str, Any]]]] = None,
    *,
    directory: Optional[list[dict[str, Any]]] = None,
    manifest_patch: Optional[dict[str, Any]] = None,
    entity_patch: Optional[dict[str, dict[str, Any]]] = None,
    extra_members: Optional[dict[str, bytes]] = None,
    sign_with: Optional[Ed25519PrivateKey] = None,
    after_sign: Optional[Callable[[dict[str, bytes]], None]] = None,
) -> bytes:
    """A project export shaped like contract §1.3 (members, manifest, optional signature)."""
    entities = {"projects": [_project_row()], **(entities or {})}
    members: dict[str, bytes] = {}
    listed: dict[str, dict[str, Any]] = {}
    for table, rows in entities.items():
        if rows:
            members[f"entities/{table}/part-0001.jsonl"] = _jsonl(rows)
        listed[table] = {"rows": len(rows), "parts": 1 if rows else 0}
    for table, patch in (entity_patch or {}).items():
        listed.setdefault(table, {}).update(patch)
    members["directory/users.jsonl"] = _jsonl(directory or [])
    members["README.txt"] = b"Settings and catalog of one project.\n"
    members.update(extra_members or {})
    manifest: dict[str, Any] = {
        "format_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "project_id": str(uuid.uuid4()),
        "files": [
            {"name": n, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in sorted(members.items())
        ],
        "scope": "project",
        "format": vocab.ARCHIVE_FORMAT,
        "schema_version": vocab.SCHEMA_VERSION,
        "source": {"app_version": "9.9.9", "host": None},
        "project": {"name": "Payment Service", "slug": "payment-service"},
        "entities": listed,
    }
    manifest.update(manifest_patch or {})
    members["manifest.json"] = json.dumps(manifest, sort_keys=True).encode("utf-8")
    if sign_with is not None:
        public = sign_with.public_key()
        key_id = compliance_signing.key_id_for(public)
        digest = hashlib.sha256(members["manifest.json"]).hexdigest()
        pack_id = str(uuid.uuid4())
        statement = compliance_signing.statement_bytes(pack_id=pack_id, manifest_sha256=digest, key_id=key_id)
        members["signature.json"] = json.dumps({
            "format_version": 1, "algorithm": compliance_signing.SIGNING_ALGORITHM,
            "statement": compliance_signing.STATEMENT_LABEL, "pack_id": pack_id, "manifest_sha256": digest,
            "key_id": key_id, "signed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "signature": base64.b64encode(sign_with.sign(statement)).decode("ascii"),
        }).encode("utf-8")
    if after_sign is not None:
        after_sign(members)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _public_keys(key: Ed25519PrivateKey) -> dict[str, str]:
    public = key.public_key()
    return {compliance_signing.key_id_for(public): compliance_signing.public_key_pem(public)}


# ── a recording session, a storage double ────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self) -> "_Result":
        return _Result([r[0] if isinstance(r, tuple) else r for r in self._rows])

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any:
        return self.scalars().first()


class RecordingSession:
    """Answers SELECTs from ``answers(sql_text)``; records every statement and every write."""

    def __init__(self, answers: Optional[Callable[[str], list[Any]]] = None) -> None:
        self.answers = answers or (lambda _sql: [])
        self.statements: list[str] = []
        self.writes: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if not isinstance(statement, Select):
            self.writes.append(sql.split()[0].upper())
        return _Result(self.answers(sql))

    def add(self, obj: Any) -> None:
        self.writes.append(f"add {type(obj).__name__}")

    async def flush(self) -> None:
        self.writes.append("flush")

    async def get(self, *_args: Any, **_kwargs: Any) -> Any:
        return None


class FakeStorage:
    def __init__(self, blob: Optional[bytes] = None) -> None:
        self.blob = blob
        self.puts: list[tuple[str, int, Optional[str]]] = []

    async def stream_object(self, key: str, bucket: Optional[str] = None):
        if self.blob is None:
            raise FileNotFoundError(key)
        for start in range(0, len(self.blob), 7000):
            yield self.blob[start:start + 7000]

    async def put_object_stream(self, key: str, fileobj: Any, *, size: int, content_type: str = "", bucket: Optional[str] = None) -> None:
        fileobj.seek(0)
        assert len(fileobj.read()) == size
        self.puts.append((key, size, bucket))


# ── run_import refusals: everything is refused before the first write ────────


def _answers(*, keys: Optional[dict[str, str]] = None, taken: tuple[str, ...] = ()) -> Callable[[str], list[Any]]:
    def answer(sql: str) -> list[Any]:
        if "compliance_signing_keys" in sql:
            return list((keys or {}).items())
        if "FROM projects" in sql and "lower(projects.name)" in sql:
            return [(name.lower(),) for name in taken]
        return []
    return answer


async def _refused(
    blob: Optional[bytes], *, keys: Optional[dict[str, str]] = None, taken: tuple[str, ...] = (),
    project_name: Optional[str] = None,
) -> tuple[ProjectImportFailed, RecordingSession, list[Any]]:
    session = RecordingSession(_answers(keys=keys, taken=taken))
    calls: list[Any] = []

    async def on_table(done: int, total: int) -> None:
        calls.append(("table", done, total))

    async def on_manifest(name: str, total: int) -> None:
        calls.append(("manifest", total))

    job = SimpleNamespace(id=uuid.uuid4(), project_name=project_name, storage_key="project-imports/x.zip")
    actor = SimpleNamespace(id=uuid.uuid4(), role="admin", username="importer")
    with pytest.raises(ProjectImportFailed) as ei:
        await svc.run_import(
            session, job=job, storage=FakeStorage(blob), actor=actor,
            now=dt.datetime.now(dt.timezone.utc), on_table=on_table, on_manifest=on_manifest,
        )
    return ei.value, session, calls


def _assert_nothing_written(session: RecordingSession, calls: list[Any]) -> None:
    assert session.writes == [], f"a write ran before the refusal: {session.writes}"
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in session.statements)
    assert calls == []


def _suite(**overrides: Any) -> dict[str, Any]:
    row = {"id": str(uuid.uuid4()), "name": "Checkout", "description": "Checkout flows"}
    row.update(overrides)
    return row


def _flip_part(members: dict[str, bytes]) -> None:
    members["entities/test_suites/part-0001.jsonl"] = members["entities/test_suites/part-0001.jsonl"].replace(
        b"Checkout flows", b"Checkout flowz"
    )


def _edit_manifest(members: dict[str, bytes]) -> None:
    manifest = json.loads(members["manifest.json"])
    manifest["project"]["name"] = "Someone Else's Service"
    members["manifest.json"] = json.dumps(manifest, sort_keys=True).encode("utf-8")


def _drop_manifest(members: dict[str, bytes]) -> None:
    del members["manifest.json"]


_KEY = Ed25519PrivateKey.generate()

# (id, archive builder, the code, a fragment of the sentence)
_REFUSALS: list[tuple[str, Callable[[], bytes], str, str]] = [
    ("not-a-zip", lambda: b"PK\x03\x04 this is not a zip", "not_a_zip", "not a valid ZIP"),
    ("byte-flipped-part", lambda: build_archive({"test_suites": [_suite()]}, after_sign=_flip_part),
     "tampered", "a member was changed"),
    ("manifest-edited-after-signing",
     lambda: build_archive({"test_suites": [_suite()]}, sign_with=_KEY, after_sign=_edit_manifest),
     "signature_invalid", "signature is not valid"),
    ("zip-slip-member", lambda: build_archive(extra_members={"../evil": b"x"}), "unsafe_archive", "unsafe path"),
    ("ratio-bomb", lambda: build_archive(extra_members={"bomb.bin": b"\x00" * (2 * 1024 * 1024)}),
     "unsafe_archive", "compression ratio"),
    ("schema-newer", lambda: build_archive(manifest_patch={"schema_version": 2}), "schema_newer", "(schema 2)"),
    ("schema-not-an-int", lambda: build_archive(manifest_patch={"schema_version": "1"}), "schema_unknown", "schema"),
    ("retention-archive", lambda: build_archive(manifest_patch={"scope": "runs", "format": None}),
     "not_a_project_export", "not a TestLookup project export"),
    ("no-manifest", lambda: build_archive(after_sign=_drop_manifest), "not_a_project_export", "manifest.json"),
    ("unexpected-member", lambda: build_archive(extra_members={"notes.txt": b"hello"}),
     "tampered", "an unexpected member"),
    ("unknown-entity", lambda: build_archive({"test_runs": [{"id": str(uuid.uuid4())}]}),
     "unknown_entity", "contains test_runs"),
    ("unknown-column-listed", lambda: build_archive(
        {"test_suites": [_suite()]}, entity_patch={"test_suites": {"columns": ["name", "added_later"]}}),
     "unknown_column", "test_suites.added_later"),
    ("unknown-column-in-row", lambda: build_archive({"test_suites": [_suite(added_later=1)]}),
     "unknown_column", "test_suites.added_later"),
    ("duplicate-natural-key", lambda: build_archive({"test_suites": [_suite(), _suite()]}),
     "invalid_row", "test_suites row 2 in entities/test_suites/part-0001.jsonl is not valid (duplicate name)"),
    ("row-count-differs", lambda: build_archive({"test_suites": [_suite()]}, entity_patch={"test_suites": {"rows": 5}}),
     "tampered", "a row count differs"),
    ("listed-part-missing",
     lambda: build_archive({"test_suites": [_suite()]}, entity_patch={"test_suites": {"parts": 2}}),
     "tampered", "a listed part is missing"),
    ("two-project-rows", lambda: build_archive({"projects": [_project_row(), _project_row()]}),
     "invalid_row", "projects row 2"),
    ("directory-carries-an-email", lambda: build_archive(directory=[
        {"id": str(uuid.uuid4()), "username": "ana", "full_name": None, "email": "ana@example.test"}]),
     "invalid_row", "users row 1 in directory/users.jsonl is not valid (email)"),
]


@pytest.mark.parametrize(("builder", "code", "fragment"), [r[1:] for r in _REFUSALS], ids=[r[0] for r in _REFUSALS])
async def test_every_refusal_happens_before_any_write(builder, code, fragment):
    failure, session, calls = await _refused(builder(), keys=_public_keys(_KEY))
    assert failure.code == code
    assert fragment in failure.message
    assert str(failure) == code                      # a logged exception carries the code only
    _assert_nothing_written(session, calls)


async def test_a_wrong_type_names_entity_line_file_and_field_never_the_value():
    sentinel = "SENTINELnotAnIntegerXyzzy"
    blob = build_archive({"managed_test_cases": [
        {"id": str(uuid.uuid4()), "title": "Pays by card"},
        {"id": str(uuid.uuid4()), "title": "Refunds", "version": sentinel},
    ]})
    failure, session, calls = await _refused(blob)
    assert failure.code == "invalid_row"
    assert failure.message == vocab.ERROR_SENTENCES["invalid_row"].format(
        entity="managed_test_cases", line=2, file="entities/managed_test_cases/part-0001.jsonl", field="version",
    )
    assert sentinel not in failure.message and sentinel not in repr(failure.fmt)
    _assert_nothing_written(session, calls)


async def test_schema_newer_names_both_versions():
    failure, _session, _calls = await _refused(build_archive(manifest_patch={"schema_version": 7}))
    assert failure.message == vocab.ERROR_SENTENCES["schema_newer"].format(found=7, supported="1")


async def test_a_supplied_name_already_taken_is_refused_by_selects_only():
    failure, session, calls = await _refused(build_archive(), taken=("Payments Copy",), project_name="payments copy")
    assert failure.code == "name_taken"
    assert failure.message == vocab.ERROR_SENTENCES["name_taken"].format(name="payments copy")
    _assert_nothing_written(session, calls)


async def test_a_missing_upload_object_is_interrupted():
    failure, session, calls = await _refused(None)
    assert failure.code == "interrupted"
    _assert_nothing_written(session, calls)


# ── what I-2 accepts ─────────────────────────────────────────────────────────


def _preflight(blob: bytes, keys: Optional[dict[str, str]] = None) -> Any:
    spool = io.BytesIO(blob)
    return svc._preflight_sync(spool, keys or {}, svc._scan(spool))


@pytest.mark.parametrize(
    ("signer", "known", "expected"),
    [(True, True, "valid"), (True, False, "unknown_key"), (False, False, "unsigned")],
    ids=["this-deployment", "another-deployment", "unsigned"],
)
def test_signature_rule_accepts_valid_unknown_key_and_unsigned(signer, known, expected):
    blob = build_archive({"test_suites": [_suite()]}, sign_with=_KEY if signer else None)
    pre = _preflight(blob, _public_keys(_KEY) if known else {})
    assert pre.signature == expected
    assert (pre.source_name, pre.source_slug, pre.rows["test_suites"]) == ("Payment Service", "payment-service", 1)


def test_the_directory_maps_source_ids_to_lower_case_usernames_only():
    ana, bo = uuid.uuid4(), uuid.uuid4()
    pre = _preflight(build_archive(directory=[
        {"id": str(ana), "username": "Ana.Lima", "full_name": "Ana Lima"},
        {"id": str(bo), "username": "bo", "full_name": None},
    ]))
    assert pre.directory == {ana: "ana.lima", bo: "bo"}


async def test_users_map_by_username_to_active_users_only():
    ana, gone = uuid.uuid4(), uuid.uuid4()
    target = uuid.uuid4()
    seen: list[str] = []

    def answer(sql: str) -> list[Any]:
        seen.append(sql)
        return [(target, "ana.lima")]

    mapped = await svc._map_users(RecordingSession(answer), {ana: "ana.lima", gone: "left.the.company"})
    assert mapped == {ana: target}
    assert len(seen) == 1 and "lower(users.username) IN" in seen[0] and "users.is_active IS true" in seen[0]
    assert "email" not in seen[0]


# ── I-4: the name and slug ladders ───────────────────────────────────────────


def test_name_ladder_starts_with_imported_then_numbers():
    candidates = svc.name_candidates("Payment Service")
    assert candidates[:3] == ["Payment Service (imported)", "Payment Service (imported 2)", "Payment Service (imported 3)"]
    assert candidates[-1] == "Payment Service (imported 50)" and len(candidates) == 50
    assert svc.pick_free(candidates, {"payment service (imported)"}) == "Payment Service (imported 2)"
    assert svc.pick_free(candidates, {c.lower() for c in candidates}) is None


def test_a_long_source_name_is_trimmed_to_fit():
    candidates = svc.name_candidates("N" * 255)
    assert all(len(c) <= 255 for c in candidates)
    assert candidates[0].endswith(" (imported)") and candidates[49].endswith(" (imported 50)")


async def test_choose_name_walks_the_ladder_and_refuses_after_fifty():
    taken = ("Payment Service (imported)", "payment service (imported 2)")
    session = RecordingSession(_answers(taken=taken))
    assert await svc.choose_name(session, requested=None, source_name="Payment Service") == "Payment Service (imported 3)"
    everything = tuple(svc.name_candidates("Payment Service"))
    with pytest.raises(ProjectImportFailed) as ei:
        await svc.choose_name(RecordingSession(_answers(taken=everything)), requested=None, source_name="Payment Service")
    assert ei.value.code == "name_taken"
    assert await svc.choose_name(RecordingSession(), requested="My Copy", source_name="Payment Service") == "My Copy"


def test_slug_ladder_and_reslugify():
    assert svc.slug_candidates("payment-service")[:2] == ["payment-service-imported", "payment-service-imported-2"]
    assert svc.slug_candidates("Payment Service!")[0] == "payment-service-imported"
    assert svc.slug_candidates("!!!")[0] == "project-imported"
    long = svc.slug_candidates("a" * 100)
    assert all(len(s) <= 100 and svc._SLUG.match(s) for s in long)


async def test_choose_slug_skips_slugs_any_project_holds():
    session = RecordingSession(lambda sql: [("payment-service-imported",)] if "projects.slug IN" in sql else [])
    assert await svc.choose_slug(session, source_slug="payment-service") == "payment-service-imported-2"


@pytest.mark.parametrize(("raw", "expected"), [(None, None), ("   ", None), ("  My Copy ", "My Copy")])
def test_clean_name(raw, expected):
    assert svc.clean_name(raw) == expected


@pytest.mark.parametrize("raw", ["x", "y" * 256])
def test_clean_name_refuses_out_of_bounds(raw):
    with pytest.raises(ProjectImportFailed) as ei:
        svc.clean_name(raw)
    assert ei.value.code == "name_invalid"


def test_client_token_must_be_a_uuid():
    token = uuid.uuid4()
    assert svc.parse_client_token(str(token)) == token
    with pytest.raises(ProjectImportFailed) as ei:
        svc.parse_client_token("not-a-token")
    assert ei.value.code == "client_token_invalid"


# ── I-5: one row through the remap ───────────────────────────────────────────


def _ctx(**overrides: Any) -> Any:
    fields = {"project_id": uuid.uuid4(), "actor_id": uuid.uuid4(), "now": dt.datetime.now(dt.timezone.utc),
              "user_map": {}, "release_keeper": None}
    fields.update(overrides)
    return svc._Context(**fields)


def test_fks_resolve_through_the_remap_and_unresolved_ones_are_counted():
    ctx = _ctx()
    suite_src, suite_new = uuid.uuid4(), uuid.uuid4()
    ctx.remap["test_suites"] = {suite_src: suite_new}
    case = svc.transform_row(ctx, spec_for("managed_test_cases"), {"id": uuid.uuid4(), "title": "T", "test_suite_id": suite_src})
    assert case["test_suite_id"] == suite_new and case["project_id"] == ctx.project_id
    orphan = svc.transform_row(ctx, spec_for("managed_test_cases"), {"id": uuid.uuid4(), "title": "T", "test_suite_id": uuid.uuid4()})
    assert orphan["test_suite_id"] is None                       # optional fk → NULL
    version_src = uuid.uuid4()
    skipped = svc.transform_row(ctx, spec_for("test_case_versions"), {
        "id": version_src, "test_case_id": uuid.uuid4(), "version": 1, "title": "T", "status": "draft"})
    assert skipped is None and version_src not in ctx.remap.get("test_case_versions", {})   # its children skip too
    assert ctx.cleared[("managed_test_cases", "test_suite_id", "row_unresolved")] == 1
    assert ctx.skipped[("test_case_versions", "test_case_id", "row_unresolved")] == 1


def test_ids_are_fresh_and_remembered_and_project_id_is_the_new_project():
    ctx = _ctx()
    source = uuid.uuid4()
    row = svc.transform_row(ctx, spec_for("test_suites"), {"id": source, "name": "S", "project_id": uuid.uuid4()})
    assert row["id"] != source and ctx.remap["test_suites"][source] == row["id"]
    assert row["project_id"] == ctx.project_id


def test_authorship_maps_by_username_and_assignments_are_cleared():
    author_src, author_new, gone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ctx = _ctx(user_map={author_src: author_new})
    row = svc.transform_row(ctx, spec_for("managed_test_cases"), {
        "id": uuid.uuid4(), "title": "T", "author_id": author_src, "approved_by_id": gone,
        "assignee_id": author_src, "reviewer_id": None,
    })
    assert (row["author_id"], row["approved_by_id"], row["assignee_id"], row["reviewer_id"]) == (author_new, None, None, None)
    assert ctx.cleared[("managed_test_cases", "approved_by_id", "user_not_found")] == 1
    assert ctx.cleared[("managed_test_cases", "assignee_id", "assignment")] == 1
    assert ("managed_test_cases", "reviewer_id", "assignment") not in ctx.cleared


def test_a_not_null_author_falls_back_to_the_importer():
    ctx = _ctx()
    row = svc.transform_row(ctx, spec_for("release_gate_policies"), {
        "id": uuid.uuid4(), "version": 1, "name": "P", "rules": {}, "created_by": uuid.uuid4()})
    assert row["created_by"] == ctx.actor_id
    assert ctx.cleared[("release_gate_policies", "created_by", "user_not_found")] == 1


def test_senders_arrive_switched_off_and_cadences_paused():
    from app.services.project_export_entities import WEBHOOK_PAUSED_BY_PERSON

    ctx = _ctx()
    hook = svc.transform_row(ctx, spec_for("webhook_subscriptions"), {
        "id": uuid.uuid4(), "name": "ops", "target_url": "https://hooks.example.test/in", "enabled": True,
        "has_secret": True})
    assert (hook["enabled"], hook["has_secret"], hook["disabled_reason"], hook["disabled_at"]) == (
        False, False, WEBHOOK_PAUSED_BY_PERSON, ctx.now)
    channel = svc.transform_row(ctx, spec_for("team_notification_channels"), {
        "id": uuid.uuid4(), "team_name": "payments", "channel_type": "slack", "target": "[REDACTED]", "is_active": True})
    assert channel["is_active"] is False
    cadence = svc.transform_row(ctx, spec_for("run_cadences"), {
        "id": uuid.uuid4(), "mode": "daily", "timezone": "UTC", "effective_from": ctx.now - dt.timedelta(days=30),
        "checked_through": ctx.now - dt.timedelta(days=1)})
    assert cadence["paused_at"] == ctx.now and cadence["paused_reason"] == vocab.IMPORTED_PAUSE_REASON
    assert cadence["paused_until"] == ctx.now + dt.timedelta(days=vocab.IMPORTED_PAUSE_DAYS)
    assert cadence["paused_by_id"] == ctx.actor_id and cadence["checked_through"] == ctx.now
    for spec, row in (("quarantine_lifecycle_policies", {"auto_create_defect": True}),
                      ("notification_transition_policies", {"transitions_enabled": True, "per_run_events_enabled": True}),
                      ("github_integrations", {"repo_owner": "o", "repo_name": "r", "enabled": True, "has_pat": True}),
                      ("gitlab_integrations", {"enabled": True, "has_pat": True, "commit_status_enabled": True})):
        out = svc.transform_row(ctx, spec_for(spec), {"id": uuid.uuid4(), **row})
        assert all(out[column] is False for column in spec_for(spec).inert), spec
    assert ctx.switched_off == {
        "webhook_subscriptions": 1, "team_notification_channels": 1, "run_cadences": 1,
        "quarantine_lifecycle_policies": 1, "notification_transition_policies": 1,
        "github_integrations": 1, "gitlab_integrations": 1,
    }


@pytest.mark.parametrize("status", vocab.REVIEW_RESET_STATES)
def test_cases_in_review_arrive_as_draft(status):
    ctx = _ctx()
    row = svc.transform_row(ctx, spec_for("managed_test_cases"), {"id": uuid.uuid4(), "title": "T", "status": status})
    assert row["status"] == "draft" and ctx.switched_off["managed_test_cases.review_reset"] == 1


def test_an_approved_case_keeps_its_status_and_version():
    ctx = _ctx()
    row = svc.transform_row(ctx, spec_for("managed_test_cases"), {
        "id": uuid.uuid4(), "title": "T", "status": "approved", "version": 4})
    assert (row["status"], row["version"]) == ("approved", 4) and not ctx.switched_off


def test_dismissed_pairs_are_reordered_on_the_new_ids():
    ctx = _ctx()
    a, b = uuid.uuid4(), uuid.uuid4()
    low, high = sorted([uuid.uuid4(), uuid.uuid4()])
    ctx.remap["managed_test_cases"] = {a: high, b: low}
    row = svc.transform_row(ctx, spec_for("dismissed_duplicate_pairs"), {"id": uuid.uuid4(), "case_a_id": a, "case_b_id": b})
    assert (row["case_a_id"], row["case_b_id"]) == (low, high)


# ── I-6 / I-7: self references and the one active release ────────────────────


def _release(name: str, *, active: bool, activated: Optional[dt.datetime] = None, **extra: Any) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "name": name, "is_active": active,
            "activated_at": activated.isoformat() if activated else None, **extra}


def test_the_latest_activated_release_is_the_keeper():
    now = dt.datetime.now(dt.timezone.utc)
    old, new, idle = (_release("R1", active=True, activated=now - dt.timedelta(days=90)),
                      _release("R2", active=True, activated=now - dt.timedelta(days=2)),
                      _release("R3", active=False))
    pre = _preflight(build_archive({"releases": [old, new, idle]}))
    assert pre.release_keeper == uuid.UUID(new["id"])


@pytest.mark.parametrize(
    ("actives", "kept", "deactivated"),
    [(1, True, 0), (3, True, 2), (0, False, 0)],
    ids=["exactly-one", "a-crafted-archive-with-three", "none-so-provisioning-adds-one"],
)
def test_one_active_release_after_the_import(actives, kept, deactivated):
    sources = [uuid.uuid4() for _ in range(max(actives, 1))]
    ctx = _ctx(release_keeper=sources[-1] if actives else None)
    rows = [
        svc.transform_row(ctx, spec_for("releases"), {"id": src, "name": f"R{i}", "is_active": i < actives})
        for i, src in enumerate(sources)
    ]
    assert sum(1 for r in rows if r["is_active"]) == (1 if actives else 0)
    assert ctx.active_release_kept is kept               # False → run_import calls ensure_active_release_for_new_project
    assert ctx.switched_off.get("releases.deactivated", 0) == deactivated


async def test_self_references_are_set_in_a_second_pass():
    ctx = _ctx()
    base_src, child_src = uuid.uuid4(), uuid.uuid4()
    base = svc.transform_row(ctx, spec_for("releases"), {"id": base_src, "name": "R1"})
    child = svc.transform_row(ctx, spec_for("releases"), {"id": child_src, "name": "R2", "baseline_release_id": base_src})
    lost = svc.transform_row(ctx, spec_for("releases"), {"id": uuid.uuid4(), "name": "R3", "baseline_release_id": uuid.uuid4()})
    assert child["baseline_release_id"] is None and lost["baseline_release_id"] is None   # inserted NULL first
    session = RecordingSession()
    await svc._second_pass(session, ctx)
    assert session.writes == ["UPDATE"]
    assert ctx.cleared[("releases", "baseline_release_id", "row_unresolved")] == 1
    assert ctx.self_pending[("releases", "baseline_release_id")][0] == (child["id"], base_src)
    assert ctx.remap["releases"][base_src] == base["id"]


async def test_batches_insert_once_per_column_set_and_json_none_is_sql_null():
    from app.models.postgres import Base

    table = Base.metadata.tables["managed_test_cases"]
    assert svc._bind_type(table.c.steps).none_as_null is True
    session = RecordingSession()
    await svc._insert_batch(session, table, [
        {"id": uuid.uuid4(), "title": "a"}, {"id": uuid.uuid4(), "title": "b"}, {"id": uuid.uuid4(), "title": "c", "steps": None},
    ])
    assert session.writes == ["INSERT", "INSERT"]


def test_every_user_reference_is_nullable_but_the_one_with_a_fallback():
    from app.models.postgres import Base
    from app.services.project_export_entities import plan

    not_null = sorted(
        f"{spec.table}.{column}"
        for spec in plan() for column in (*spec.user_refs, *spec.cleared)
        if not Base.metadata.tables[spec.table].c[column].nullable
    )
    assert not_null == ["release_gate_policies.created_by"]   # transform_row falls back to the importer


# ── open point 7: imported workflow definitions stay published ───────────────


def test_open_point_7_a_published_definition_without_eval_evidence_is_selectable_and_runnable():
    """Evidence (read 2026-10-30): the run path never reads eval evidence.

    * ``agents/workflow.py:3574-3591`` — a run that names no workflow calls
      ``workflow_default_service.resolve_run_workflow`` then
      ``workflow_definition_service.get_published_definition``;
    * ``workflow_definition_service.py:321-356`` — ``get_published_definition``
      filters ``status == 'published'`` only;
    * ``workflow_default_service.py:83-99, 113-147`` — the default resolves when
      the published definition exists, is built on the base and parses;
    * ``routers/agents.py:385-393`` — a NAMED workflow resolves the same way;
    * ``workflow_evaluation_service.py:957-977`` — the publish gate refuses only a
      measured ``fail``; NULL evidence ("insufficient evidence remains visible") passes.

    So the contract's rule stands: ``published`` with the eval columns dropped
    (NULL), and ``project_workflow_defaults`` copied verbatim.
    """
    import inspect

    from app.services import workflow_default_service, workflow_definition_service, workflow_evaluation_service

    for fn in (workflow_definition_service.get_published_definition,
               workflow_default_service.resolve_run_workflow,
               workflow_default_service._unavailable_reason):
        assert "eval_" not in inspect.getsource(fn), f"{fn.__name__} now reads eval evidence: revisit the import"
    workflow_evaluation_service.enforce_publish_gate(
        SimpleNamespace(eval_verdict=None), accept_regression=False, reason=None, accepted_by=None,
    )

    ctx = _ctx()
    published_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)
    source_project = str(uuid.uuid4())
    row = svc.transform_row(ctx, spec_for("workflow_definitions"), {
        "id": uuid.uuid4(), "workflow_id": "wf.payments", "version": 2, "name": "Payments", "base": "offline",
        "definition": {"workflow_id": "wf.payments", "project_id": source_project, "steps": []},
        "status": "published", "published_at": published_at,
    })
    assert (row["status"], row["published_at"]) == ("published", published_at)
    assert row["definition"]["project_id"] == str(ctx.project_id)       # no source id survives in the document
    assert not any(column.startswith("eval_") or column == "evaluated_at" for column in row)
    default = svc.transform_row(ctx, spec_for("project_workflow_defaults"), {
        "base": "offline", "workflow_id": "wf.payments", "workflow_version": 2})
    assert (default["workflow_id"], default["project_id"]) == ("wf.payments", ctx.project_id)


# ── I-0: accept_upload (the route's half) ────────────────────────────────────


class FakeUpload:
    def __init__(self, data: bytes, chunk: int = 4096) -> None:
        self.data, self.chunk, self.reads, self.offset = data, chunk, 0, 0

    async def read(self, size: int = -1) -> bytes:
        self.reads += 1
        step = self.chunk if size < 0 else min(size, self.chunk)
        out = self.data[self.offset:self.offset + step]
        self.offset += len(out)
        return out


@pytest.fixture
def created_jobs(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def fake_create_import_job(db, *, actor, client_token, project_name):
        calls.append({"client_token": client_token, "project_name": project_name})
        return SimpleNamespace(id=uuid.uuid4(), storage_key=None, bytes=None), True

    monkeypatch.setattr("app.services.project_data_job_service.create_import_job", fake_create_import_job)
    return calls


def _actor() -> Any:
    return SimpleNamespace(id=uuid.uuid4(), role="admin", username="importer")


@pytest.mark.parametrize(
    ("payload", "code"),
    [(b"PK\x03\x04" + b"z" * 2000, "upload_too_large"), (b"", "upload_empty"), (b"<xml>not a zip</xml>", "not_a_zip")],
    ids=["over-the-cap", "empty", "no-zip-magic"],
)
async def test_an_upload_is_refused_before_a_job_row_exists(monkeypatch, created_jobs, payload, code):
    monkeypatch.setattr(svc.vocab, "IMPORT_UPLOAD_MAX_BYTES", 1000)
    storage, session = FakeStorage(), RecordingSession()
    with pytest.raises(ProjectImportFailed) as ei:
        await svc.accept_upload(session, storage, file=FakeUpload(payload), name=None,
                                client_token=uuid.uuid4(), actor=_actor())
    assert ei.value.code == code and svc.ROUTE_STATUS[code] in (413, 422)
    assert created_jobs == [] and storage.puts == [] and session.writes == []


async def test_the_same_token_returns_its_job_and_reads_nothing(created_jobs):
    existing = SimpleNamespace(id=uuid.uuid4(), status="succeeded")
    session = RecordingSession(lambda sql: [existing] if "FROM project_data_jobs" in sql else [])
    upload, storage = FakeUpload(b"PK\x03\x04data"), FakeStorage()
    job, created = await svc.accept_upload(session, storage, file=upload, name=None,
                                           client_token=uuid.uuid4(), actor=_actor())
    assert (job, created) == (existing, False)
    assert upload.reads == 0 and storage.puts == [] and created_jobs == [] and session.writes == []


async def test_the_same_token_wins_over_the_name_its_own_import_took(created_jobs):
    """The CLI re-sends `--name My Copy` with the same token after the first import created "My Copy":
    the answer is that job ("already imported"), never name_taken (the token is checked FIRST)."""
    existing = SimpleNamespace(id=uuid.uuid4(), status="succeeded")
    names = _answers(taken=("my copy",))
    session = RecordingSession(lambda sql: [existing] if "FROM project_data_jobs" in sql else names(sql))
    upload = FakeUpload(b"PK\x03\x04data")
    job, created = await svc.accept_upload(session, FakeStorage(), file=upload, name="My Copy",
                                           client_token=uuid.uuid4(), actor=_actor())
    assert (job, created) == (existing, False)
    assert upload.reads == 0 and created_jobs == [] and session.writes == []


@pytest.mark.parametrize(("name", "taken", "code"), [("x", (), "name_invalid"), ("Taken", ("taken",), "name_taken")])
async def test_a_bad_or_taken_name_is_refused_before_reading(created_jobs, name, taken, code):
    upload = FakeUpload(b"PK\x03\x04data")
    with pytest.raises(ProjectImportFailed) as ei:
        await svc.accept_upload(RecordingSession(_answers(taken=taken)), FakeStorage(), file=upload, name=name,
                                client_token=uuid.uuid4(), actor=_actor())
    assert ei.value.code == code and upload.reads == 0 and created_jobs == []


async def test_an_accepted_upload_is_stored_under_project_imports(created_jobs):
    blob = build_archive()
    storage, session = FakeStorage(), RecordingSession()
    token = uuid.uuid4()
    job, created = await svc.accept_upload(session, storage, file=FakeUpload(blob), name="  My Copy ",
                                           client_token=token, actor=_actor())
    assert created is True and created_jobs == [{"client_token": token, "project_name": "My Copy"}]
    [(key, size, bucket)] = storage.puts
    assert key.startswith(vocab.STORAGE_PREFIX_IMPORT) and key.endswith(f"/{job.id}.zip")
    assert (size, bucket, job.storage_key, job.bytes) == (len(blob), "compliance-packs", key, len(blob))
    assert session.writes == ["flush"]
