"""W24 ADM-05 — the project export's build → sign → write → re-read + verify (contract §2.2 E-3 / E-4).

A fake session serves rows per table and RECORDS every statement (the source session may only SELECT);
a fake object store keeps bytes in a dict. The archive is the real ``build_archive`` output, signed by a
real Ed25519 key and read back with the real verifier.
"""
from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from app.services import compliance_signing
from app.services import project_export_service as svc
from app.services import project_export_vocab as vocab
from app.services.compliance_pack_verify import verify_archive_file
from app.services.project_export_entities import EXPORTED_TABLES, exported_columns, plan, spec_for
from app.services.project_export_scrub import scrub_row


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


class RecordingSession:
    """Serves ``rows[table]`` (cut to the selected columns, like the database) and records statements."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]], users: list[tuple] = ()) -> None:
        self.rows = rows
        self.users = list(users)
        self.statements: list[Any] = []
        self.results: list[_Result] = []

    def _table(self, stmt: Any) -> str:
        return stmt.get_final_froms()[0].name

    async def stream(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        names = [column.name for column in stmt.selected_columns]
        rows = [{name: row.get(name) for name in names} for row in self.rows.get(self._table(stmt), [])]
        result = _Result(rows)
        self.results.append(result)
        return result

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(stmt)
        if self._table(stmt) == "users":
            return _Result([{"id": u[0], "username": u[1], "full_name": u[2]} for u in self.users])
        return _Result([])

    def add(self, *_: Any) -> None:  # pragma: no cover - a write on the source session is the bug
        raise AssertionError("the source session must not stage a row")

    async def flush(self) -> None:  # pragma: no cover
        raise AssertionError("the source session must not flush")

    async def commit(self) -> None:  # pragma: no cover
        raise AssertionError("the source session must not commit")

    async def rollback(self) -> None:
        """Ending the read-only snapshot (after the build) is the one thing a READ session may do."""
        self.rollbacks = getattr(self, "rollbacks", 0) + 1


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

    async def list_objects(self, prefix: str, bucket: str | None = None) -> list[dict]:
        return [{"Key": key, "LastModified": 0.0} for key in self.objects if key.startswith(prefix)]


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

def _seed() -> tuple[Any, dict[str, list[dict[str, Any]]], list[tuple]]:
    project_id = uuid.uuid4()
    author, assignee, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    created = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    suites = [
        {"id": uuid.uuid4(), "project_id": project_id, "name": f"Suite {n}", "description": f"desc {n}",
         "tags": ["smoke"], "created_at": created}
        for n in range(5)
    ]
    token = "ghp" + "_" + "SENTINELghpTokenXyzzyQwertyPlughWxyz"
    rows = {
        "projects": [{"id": project_id, "name": "Payment Service", "slug": "payment-service",
                      "description": "uses " + token, "manager_user_id": assignee, "tags": ["payments"]}],
        "test_suites": suites,
        "managed_test_cases": [{
            "id": uuid.uuid4(), "project_id": project_id, "title": "Login works", "test_suite_id": suites[0]["id"],
            "author_id": author, "assignee_id": assignee, "status": "draft", "version": 2,
            "generation_batch_id": uuid.uuid4(), "faithfulness_score": 0.5, "steps": [{"action": "open"}],
        }],
    }
    users = [(author, "alice", "Alice A"), (assignee, "bob", None)]
    project = SimpleNamespace(id=project_id, name="Payment Service", slug="payment-service", stranger=stranger)
    return project, rows, users


def _zip(storage: FakeStorage) -> zipfile.ZipFile:
    (data,) = storage.objects.values()
    return zipfile.ZipFile(io.BytesIO(data))


def _run(db: Any, project: Any, storage: FakeStorage, **kwargs: Any) -> svc.ProjectExportOutcome:
    async def no_sleep(_seconds: float) -> None:
        return None

    return asyncio.run(svc.run_project_export(
        db, job_id=kwargs.pop("job_id", uuid.uuid4()), project=project, storage=storage,
        now=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc), sleep=no_sleep, **kwargs,
    ))


# ── the statements ──────────────────────────────────────────────────────────

def test_a_parent_scoped_table_is_selected_through_a_subquery_never_an_id_list() -> None:
    project_id = uuid.uuid4()
    sql = str(svc.rows_statement(spec_for("test_case_versions"), project_id).compile(dialect=postgresql.dialect()))
    assert "IN (SELECT managed_test_cases.id" in sql
    assert "managed_test_cases.project_id = " in sql
    assert "ORDER BY test_case_versions.id" in sql


def test_dropped_columns_are_never_selected() -> None:
    for spec in plan():
        selected = {column.name for column in svc.rows_statement(spec, uuid.uuid4()).selected_columns}
        assert not selected & set(spec.drop), spec.table
        assert selected, spec.table


def test_the_root_row_is_the_project_itself() -> None:
    project_id = uuid.uuid4()
    sql = str(svc.rows_statement(spec_for("projects"), project_id).compile(dialect=postgresql.dialect()))
    assert "WHERE projects.id = " in sql
    assert "projects.name" not in sql.split("FROM")[0]


# ── the archive ─────────────────────────────────────────────────────────────

def test_the_archive_holds_parts_directory_readme_manifest_and_signature(signing: Any) -> None:
    project, rows, users = _seed()
    db, sign_db, storage = RecordingSession(rows, users), RecordingSession({}), FakeStorage()
    progress: list[tuple[int, int]] = []

    async def on_table(done: int, total: int) -> None:
        progress.append((done, total))

    written: list[str] = []

    async def on_written(key: str) -> None:
        written.append(key)

    outcome = _run(db, project, storage, sign_db=sign_db, on_table=on_table, on_written=on_written)
    archive = _zip(storage)
    names = archive.namelist()
    assert names[-2:] == ["manifest.json", "signature.json"]
    assert names[-3] == "README.txt" and names[-4] == "directory/users.jsonl"
    assert "entities/projects/part-0001.jsonl" in names and "entities/test_suites/part-0001.jsonl" in names
    assert all(n.startswith(("entities/", "directory/")) or n in ("README.txt", "manifest.json", "signature.json")
               for n in names)
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["scope"] == "project" and manifest["format"] == vocab.ARCHIVE_FORMAT
    assert manifest["schema_version"] == vocab.SCHEMA_VERSION
    assert manifest["project"] == {"name": "Payment Service", "slug": "payment-service"}
    assert manifest["project_id"] == str(project.id)
    assert set(manifest["entities"]) == set(EXPORTED_TABLES)
    assert manifest["entities"]["test_suites"]["rows"] == 5 and manifest["entities"]["test_suites"]["parts"] == 1
    assert manifest["entities"]["release_phases"] == {"rows": 0, "parts": 0,
                                                       "columns": manifest["entities"]["release_phases"]["columns"]}
    assert "test_runs" in manifest["excluded"]["run_history"]
    assert manifest["directory"] == {"users": 2}
    assert outcome.counts["test_suites"] == 5 and outcome.counts["managed_test_cases"] == 1
    assert outcome.signature == "signed" and outcome.signing_key_id == signing.key_id
    assert written == [outcome.key] and outcome.key.startswith("project-exports/2026/10/30/")
    assert progress == [(n, len(plan())) for n in range(1, len(plan()) + 1)]
    # the stored bytes verify with the real verifier (chain intact, signature valid)
    result = verify_archive_file(io.BytesIO(storage.objects[outcome.key]), recorded_manifest_sha256=None,
                                 public_keys={signing.key_id: signing.public_key_pem})
    assert result["chain"] == "intact" and result["signature"] == "valid"
    # every stream was closed
    assert all(r.closed for r in db.results)


def test_rows_are_scrubbed_dropped_and_users_listed_without_email(signing: Any) -> None:
    project, rows, users = _seed()
    storage = FakeStorage()
    _run(RecordingSession(rows, users), project, storage, sign_db=RecordingSession({}))
    archive = _zip(storage)
    case = json.loads(archive.read("entities/managed_test_cases/part-0001.jsonl").decode().splitlines()[0])
    assert "generation_batch_id" not in case and "faithfulness_score" not in case
    assert case["author_id"] == str(users[0][0]) and case["version"] == 2
    root = json.loads(archive.read("entities/projects/part-0001.jsonl"))
    assert "name" not in root and "slug" not in root and "id" not in root
    assert "SENTINEL" not in root["description"] and "[REDACTED]" in root["description"]
    directory = [json.loads(line) for line in archive.read("directory/users.jsonl").decode().splitlines()]
    assert {entry["username"] for entry in directory} == {"alice", "bob"}
    assert all(set(entry) == {"id", "username", "full_name"} for entry in directory)
    assert str(project.stranger) not in archive.read("directory/users.jsonl").decode()


def test_the_source_session_executes_only_selects(signing: Any) -> None:
    project, rows, users = _seed()
    db = RecordingSession(rows, users)
    _run(db, project, FakeStorage(), sign_db=RecordingSession({}))
    assert db.statements, "the export read nothing"
    for stmt in db.statements:
        assert isinstance(stmt, Select), type(stmt).__name__
        assert str(stmt.compile(dialect=postgresql.dialect())).lstrip().upper().startswith("SELECT")
    assert len(db.statements) == len(plan()) + 1     # one streamed SELECT per table + the directory


def test_the_snapshot_is_ended_before_the_archive_is_written(signing: Any) -> None:
    """The worker's pool holds 2 connections: the snapshot must hand its back before the write (on_written and the
    storage config each need one) — the homelab's first export held it and failed export_write (TimeoutError)."""
    project, rows, users = _seed()
    db = RecordingSession(rows, users)
    storage = FakeStorage()
    seen: list[int] = []
    real_put = storage.put_object_stream

    async def put(*args: Any, **kwargs: Any) -> None:
        seen.append(getattr(db, "rollbacks", 0))
        await real_put(*args, **kwargs)

    storage.put_object_stream = put  # type: ignore[method-assign]
    _run(db, project, storage, sign_db=RecordingSession({}))
    assert seen == [1], "the read-only snapshot was still open when the archive was written"


# ── parts ───────────────────────────────────────────────────────────────────

def test_parts_are_cut_at_the_row_limit(signing: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vocab, "PART_ROWS_MAX", 2)
    project, rows, users = _seed()
    storage = FakeStorage()
    _run(RecordingSession(rows, users), project, storage, sign_db=RecordingSession({}))
    archive = _zip(storage)
    parts = sorted(n for n in archive.namelist() if n.startswith("entities/test_suites/"))
    assert parts == [f"entities/test_suites/part-000{n}.jsonl" for n in (1, 2, 3)]
    assert [len(archive.read(p).splitlines()) for p in parts] == [2, 2, 1]
    assert json.loads(archive.read("manifest.json"))["entities"]["test_suites"]["parts"] == 3


def test_parts_are_cut_at_the_byte_limit(signing: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    project, rows, users = _seed()
    spec = spec_for("test_suites")
    one_row = max(
        len(svc.encode_row(scrub_row(spec, {c: row.get(c) for c in exported_columns(spec)})))
        for row in rows["test_suites"]
    )
    monkeypatch.setattr(vocab, "PART_BYTES_MAX", one_row * 2 + 1)
    storage = FakeStorage()
    _run(RecordingSession(rows, users), project, storage, sign_db=RecordingSession({}))
    archive = _zip(storage)
    parts = sorted(n for n in archive.namelist() if n.startswith("entities/test_suites/"))
    assert len(parts) == 3
    assert all(len(archive.read(p)) <= vocab.PART_BYTES_MAX for p in parts)
    assert sum(len(archive.read(p).splitlines()) for p in parts) == 5


# ── refusals ────────────────────────────────────────────────────────────────

def test_a_row_over_the_cap_fails_the_export_naming_the_entity_and_count(
    signing: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, rows, users = _seed()
    rows["test_suites"][1]["description"] = "x " * 3000
    rows["test_suites"][3]["description"] = "y " * 3000
    monkeypatch.setattr(vocab, "ROW_BYTES_MAX", 4000)
    storage = FakeStorage()
    db = RecordingSession(rows, users)
    with pytest.raises(svc.ProjectExportFailed) as caught:
        _run(db, project, storage, sign_db=RecordingSession({}))
    assert caught.value.code == "row_too_large"
    assert caught.value.message == "test_suites has 2 rows larger than 4 MB; they cannot be exported."
    assert "x x" not in caught.value.message
    assert storage.objects == {} and storage.deleted == []
    assert all(r.closed for r in db.results)


def test_an_archive_over_the_cap_fails_export_too_large(signing: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vocab, "EXPORT_MAX_BYTES", 600)
    project, rows, users = _seed()
    storage = FakeStorage()
    db = RecordingSession(rows, users)
    with pytest.raises(svc.ProjectExportFailed) as caught:
        _run(db, project, storage, sign_db=RecordingSession({}))
    assert caught.value.code == "export_too_large"
    assert caught.value.message == vocab.ERROR_SENTENCES["export_too_large"]
    assert storage.objects == {}
    assert all(r.closed for r in db.results), "a refusal mid-table left a cursor open"


def test_a_verification_failure_removes_the_object(signing: Any) -> None:
    project, rows, users = _seed()
    storage = FakeStorage(corrupt=True)
    written: list[str] = []

    async def on_written(key: str) -> None:
        written.append(key)

    with pytest.raises(svc.ProjectExportFailed) as caught:
        _run(RecordingSession(rows, users), project, storage, sign_db=RecordingSession({}), on_written=on_written)
    assert caught.value.code == "export_unverified"
    assert caught.value.message == "The stored export could not be verified (digest mismatch)."
    assert storage.deleted == written and storage.objects == {}


def test_a_write_failure_names_the_error_type_only(signing: Any) -> None:
    project, rows, users = _seed()
    storage = FakeStorage(fail_put=True)
    with pytest.raises(svc.ProjectExportFailed) as caught:
        _run(RecordingSession(rows, users), project, storage, sign_db=RecordingSession({}))
    assert caught.value.code == "export_write"
    assert caught.value.message == "The export could not be written to storage (ConnectionRefusedError)."


def test_without_a_usable_key_the_export_is_unsigned_and_still_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_key(db: Any, *, actor: Any, create: bool) -> Any:
        return None

    async def no_keys(db: Any) -> dict[str, str]:
        return {}

    monkeypatch.setattr(compliance_signing, "active_signing_key", no_key)
    monkeypatch.setattr(compliance_signing, "public_keys", no_keys)
    project, rows, users = _seed()
    storage = FakeStorage()
    outcome = _run(RecordingSession(rows, users), project, storage)
    assert outcome.signature == "unsigned" and outcome.signing_key_id is None
    assert "signature.json" not in _zip(storage).namelist()


def test_the_row_encoding_follows_the_archive_format() -> None:
    """§1.3: keys sorted, UTF-8 (not ASCII-escaped), ISO 8601 times (a TIME column too: the seed's cadence
    window failed the first live export), UUID as text, Decimal as a string, no NaN."""
    from datetime import date, time
    from decimal import Decimal

    row_id = uuid.uuid4()
    line = svc.encode_row({
        "z": "Zürich", "at": datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc), "day": date(2026, 10, 30),
        "window": time(9, 30), "id": row_id, "pct": Decimal("80.50"), "none": None,
    })
    assert line == (
        '{"at":"2026-10-30T12:00:00+00:00","day":"2026-10-30","id":"' + str(row_id)
        + '","none":null,"pct":"80.50","window":"09:30:00","z":"Zürich"}\n'
    ).encode("utf-8")
    with pytest.raises(ValueError):
        svc.encode_row({"x": float("nan")})
    with pytest.raises(TypeError):
        svc.encode_row({"blob": b"bytes never travel"})

