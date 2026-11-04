"""W30 ADM-05 phase 2 — the run history of a project export (contract §2.4, ``project_export_history``).

Pure unit tests over recording doubles (the real Postgres / Mongo / storage round trip is
``tests/integration/test_project_export_postgres.py``): the window statement and its ONE bound array, the
skips counted, documents ITERATED in ``$in`` chunks (a cursor that raises on ``to_list``), objects as
``StreamedMember``s (STORED for media), the precheck before any member, the estimate's ``measured: false``,
the peak memory, and a source session that never sees an INSERT / UPDATE / DELETE.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Mapping, Optional

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Delete, Insert, Update

from app.services import project_export_history as hx
from app.services import project_export_service as svc
from app.services import project_export_vocab as vocab
from app.services.project_export_entities import HISTORY, spec_for

PG = postgresql.dialect()


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=PG))


def _window(run_ids: list[uuid.UUID], project_id: Optional[uuid.UUID] = None) -> hx.RunWindow:
    return hx.RunWindow(project_id=project_id or uuid.uuid4(), run_ids=run_ids, days=90, max_runs=2000)


# ── doubles ─────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows: list[Any], scalar: Any = None) -> None:
        self._rows = rows
        self._scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for row in self._rows:
                yield row
        return gen()

    async def close(self) -> None:
        return None

    def all(self) -> list[Any]:
        return [tuple(row.values()) if isinstance(row, dict) else row for row in self._rows]

    def one(self) -> Any:
        return self.all()[0]

    def scalar(self) -> Any:
        return self._scalar


class RecordingSession:
    """A READ-ONLY snapshot double: answers the history statements from ``rows[table]`` and records every
    statement; a write of any kind (an INSERT / UPDATE / DELETE statement, add / flush / commit) is the bug."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]], *, users: list[tuple] = (),
                 counts: tuple[int, int] = (0, 0), skipped: int = 0) -> None:
        self.rows = rows
        self.users = list(users)
        self.counts = counts
        self.skipped = skipped
        self.statements: list[Any] = []

    @staticmethod
    def _table(stmt: Any) -> str:
        return stmt.get_final_froms()[0].name

    def _check(self, stmt: Any) -> None:
        assert not isinstance(stmt, (Insert, Update, Delete)), f"a write reached the source: {stmt}"
        self.statements.append(stmt)

    async def stream(self, stmt: Any) -> _Result:
        self._check(stmt)
        names = [column.name for column in stmt.selected_columns]
        rows = self.rows.get(self._table(stmt), [])
        if names == ["id"] or names == ["run_id"]:
            return _Result([(row.get(names[0]),) for row in rows])
        return _Result([{name: row.get(name) for name in names} for row in rows])

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _Result:
        self._check(stmt)
        table = self._table(stmt)
        names = [getattr(column, "name", None) for column in stmt.selected_columns]
        if table == "users":
            return _Result([{"id": u[0], "username": u[1], "full_name": u[2]} for u in self.users])
        if table == "test_runs" and names == ["id", "created_at"]:
            return _Result([(row["id"], row["created_at"]) for row in self.rows.get("test_runs", [])])
        if table == "test_runs" and names == ["id", "minio_prefix"]:
            return _Result([(row["id"], row.get("minio_prefix")) for row in self.rows.get("test_runs", [])])
        if table == "test_runs" and len(names) == 2:
            return _Result([self.counts])
        return _Result([], scalar=self.skipped if table in ("agent_pipeline_runs", "agent_stage_results")
                       else len(self.rows.get(table, [])))

    def add(self, *_: Any) -> None:  # pragma: no cover
        raise AssertionError("the source session must not stage a row")

    async def flush(self) -> None:  # pragma: no cover
        raise AssertionError("the source session must not flush")

    async def commit(self) -> None:  # pragma: no cover
        raise AssertionError("the source session must not commit")

    async def rollback(self) -> None:
        self.rollbacks = getattr(self, "rollbacks", 0) + 1


class NoToListCursor:
    """A Motor-shaped cursor that can only be ITERATED: ``to_list`` is the bug the contract forbids."""

    def __init__(self, docs: list[dict[str, Any]], calls: list[dict[str, Any]], query: dict[str, Any]) -> None:
        self._docs = docs
        self.calls = calls
        self.call = {"query": query, "sort": None, "batch_size": None}
        calls.append(self.call)

    def sort(self, key: str, direction: int) -> "NoToListCursor":
        self.call["sort"] = (key, direction)
        return self

    def batch_size(self, size: int) -> "NoToListCursor":
        self.call["batch_size"] = size
        return self

    async def to_list(self, *_: Any, **__: Any) -> list[Any]:  # pragma: no cover - the bug
        raise AssertionError("documents must be iterated, never loaded with to_list")

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for doc in sorted(self._docs, key=lambda d: str(d.get("_id"))):
                yield doc
        return gen()


class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]], calls: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.calls = calls

    def _match(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        ((key, cond),) = query.items()
        wanted = set(cond["$in"])
        assert len(wanted) <= vocab.MONGO_BATCH_DOCS, "one $in chunk holds at most MONGO_BATCH_DOCS keys"
        return [doc for doc in self.docs if doc.get(key) in wanted]

    def find(self, query: dict[str, Any]) -> NoToListCursor:
        return NoToListCursor(self._match(query), self.calls, query)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return len(self._match(query))

    # a write on the source is the bug
    async def insert_one(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        raise AssertionError("the export must not write a document")

    insert_many = update_one = update_many = delete_one = delete_many = insert_one


class FakeMongo:
    def __init__(self, collections: Mapping[str, list[dict[str, Any]]], *, down: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.down = down
        self._collections = {name: FakeCollection(list(docs), self.calls) for name, docs in collections.items()}

    def __getitem__(self, name: str) -> FakeCollection:
        if self.down:
            raise ConnectionError("mongo unreachable")
        return self._collections.setdefault(name, FakeCollection([], self.calls))


@dataclass(frozen=True)
class Spec:
    """The ``MongoSpec`` fields the export reads (G-A's dataclass carries the same names)."""

    collection: str
    key: str
    key_map: str
    id_fields: Mapping[str, str] = field(default_factory=dict)
    project_field: Optional[str] = None
    slug_key: bool = False


class FakeObjectStore:
    """``list_objects`` / ``stream_object`` only (the export reads); a write is the bug."""

    def __init__(self, objects: Mapping[str, bytes], *, down: bool = False) -> None:
        self.objects = dict(objects)
        self.down = down
        self.streamed: list[str] = []

    async def list_objects(self, prefix: str, bucket: Optional[str] = None) -> list[dict]:
        if self.down:
            raise ConnectionError("storage unreachable")
        return [{"Key": k, "Size": len(v)} for k, v in self.objects.items() if k.startswith(prefix)]

    async def stream_object(self, key: str, bucket: Optional[str] = None) -> Any:
        self.streamed.append(key)
        data = self.objects[key]
        for start in range(0, len(data), 65536):
            yield data[start:start + 65536]

    async def put_object(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        raise AssertionError("the plan must not write an object")


# ── E-H1: the window statement and the ONE bound array ──────────────────────

def test_the_window_is_one_select_newest_first_over_the_terminal_statuses() -> None:
    project_id = uuid.uuid4()
    since = datetime(2026, 8, 5, tzinfo=timezone.utc)
    statement = hx.window_statement(project_id, since=since, max_runs=2000)
    sql = _sql(statement)
    assert sql.startswith("SELECT test_runs.id, test_runs.created_at \nFROM test_runs")
    assert "test_runs.project_id = %(project_id_1)s" in sql
    assert "test_runs.created_at >= %(created_at_1)s" in sql
    assert "test_runs.status IN (__[POSTCOMPILE_status_1])" in sql
    assert sql.endswith("ORDER BY test_runs.created_at DESC, test_runs.id DESC \n LIMIT %(param_1)s")
    params = statement.compile(dialect=PG).params
    assert params["status_1"] == list(vocab.HISTORY_RUN_STATUSES) and params["param_1"] == 2000
    # "All history": no time bound at all.
    assert "created_at >=" not in _sql(hx.window_statement(project_id, since=None, max_runs=10))


def test_window_bounds_read_the_options() -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    assert hx.window_bounds({"history_days": 30, "max_runs": 5}, now=now) == (30, 5, now - timedelta(days=30))
    assert hx.window_bounds({"history_days": None}, now=now) == (None, vocab.HISTORY_MAX_RUNS, None)
    days, runs, _since = hx.window_bounds(SimpleNamespace(history_days=365, max_runs=99_999), now=now)
    assert (days, runs) == (365, vocab.HISTORY_MAX_RUNS)


def test_run_window_counts_in_progress_and_over_cap_runs() -> None:
    project_id = uuid.uuid4()
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    runs = [{"id": uuid.uuid4(), "created_at": base - timedelta(days=n)} for n in range(3)]
    db = RecordingSession({"test_runs": runs}, counts=(7, 2))
    window = asyncio.run(hx.run_window(db, project_id=project_id, options={"history_days": 90, "max_runs": 3},
                                       now=base))
    assert window.run_ids == [r["id"] for r in runs]
    assert (window.in_progress, window.over_cap, window.max_runs, window.days) == (2, 4, 3, 90)
    assert window.oldest_run_at == base - timedelta(days=2) and window.newest_run_at == base
    assert len(db.statements) == 2     # the window SELECT + ONE aggregate of the counts


def test_every_history_statement_is_scoped_by_the_one_bound_array_or_a_subquery() -> None:
    window = _window([uuid.uuid4() for _ in range(3)])
    for spec in HISTORY:
        compiled = hx.rows_statement(spec, window).compile(dialect=PG)
        sql = str(compiled)
        kind = spec.window_parts[0]
        assert "= ANY (%(run_window)s::UUID[])" in sql or kind in ("project", "release", "canonical"), spec.table
        # the only list-valued parameter is the bound window (or a status vocabulary) — never a list of case ids
        for name, value in compiled.params.items():
            if isinstance(value, (list, tuple)) and name != "run_window":
                assert set(value) <= set(vocab.HISTORY_RUN_STATUSES) | set(vocab.TERMINAL_PIPELINE_STATUSES), (
                    spec.table, name)
        for column in spec.null_unless:
            assert f"CASE WHEN ({spec.table}.{column} " in sql and f"END AS {column}" in sql, (spec.table, column)


def test_the_windows_follow_the_contract() -> None:
    window = _window([uuid.uuid4()])
    sql = {spec.table: _sql(hx.rows_statement(spec, window)) for spec in HISTORY}
    assert "WHERE test_runs.id = ANY (%(run_window)s::UUID[]) AND test_runs.status IN" in sql["test_runs"]
    assert ("WHERE agent_pipeline_runs.test_run_id = ANY (%(run_window)s::UUID[]) "
            "AND agent_pipeline_runs.status IN") in sql["agent_pipeline_runs"]
    assert "WHERE test_cases.test_run_id = ANY (%(run_window)s::UUID[])" in sql["test_cases"]
    # a case-keyed table goes through a subquery on the window's cases
    assert ("ai_analysis.test_case_id IN (SELECT test_cases.id \nFROM test_cases \n"
            "WHERE test_cases.test_run_id = ANY") in sql["ai_analysis"]
    # stage results of EXPORTED (terminal) pipelines only
    assert "agent_stage_results.pipeline_run_id IN (SELECT agent_pipeline_runs.id" in sql["agent_stage_results"]
    assert "agent_pipeline_runs.status IN" in sql["agent_stage_results"]
    # release history: every row of an exported release; canonical: of an exported canonical case
    assert "release_outcomes.release_id IN (SELECT releases.id \nFROM releases \nWHERE releases.project_id" in (
        sql["release_outcomes"])
    assert "test_steps.canonical_test_case_id IN (SELECT canonical_test_cases.id" in sql["test_steps"]
    assert "WHERE timeline_notes.project_id = %(project_id_1)s" in sql["timeline_notes"]
    # a pointer outside the archive is written NULL in SQL
    assert "CASE WHEN (run_baselines.baseline_run_id = ANY (%(run_window)s::UUID[])) " in sql["run_baselines"]


def test_dropped_columns_are_never_selected_and_c1_pointers_are_null_unless_in_the_window() -> None:
    window = _window([uuid.uuid4()])
    assert "event_archive" not in _sql(hx.rows_statement(spec_for("test_runs"), window))
    selected = _sql(hx.rows_statement(spec_for("agent_pipeline_runs"), window)).split("FROM")[0]
    for lease in ("lease_owner", "lease_expires_at", "fencing_token", "heartbeat_at", "next_retry_at"):
        assert lease not in selected
    canonical = _sql(hx.rows_statement(spec_for("canonical_test_cases"), window))
    assert "CASE WHEN (canonical_test_cases.first_seen_run_id = ANY (%(run_window)s::UUID[]))" in canonical
    # schema 1 (the catalog export) still drops them
    assert "first_seen_run_id" not in _sql(svc.rows_statement(spec_for("canonical_test_cases"), window.project_id))


def test_skipped_pipelines_and_their_stage_results_are_counted() -> None:
    window = _window([uuid.uuid4()])
    sqls = {key: _sql(stmt) for key, stmt in hx.skipped_statements(window).items()}
    assert "agent_pipeline_runs.status NOT IN" in sqls[("agent_pipeline_runs", "pipeline_not_terminal")]
    assert "agent_stage_results.pipeline_run_id IN (SELECT agent_pipeline_runs.id" in (
        sqls[("agent_stage_results", "pipeline_not_terminal")])
    assert asyncio.run(hx.count_skipped(RecordingSession({}, skipped=2), window)) == [
        {"entity": "agent_pipeline_runs", "reason": "pipeline_not_terminal", "count": 2},
        {"entity": "agent_stage_results", "reason": "pipeline_not_terminal", "count": 2},
    ]
    assert asyncio.run(hx.count_skipped(RecordingSession({}, skipped=0), window)) == []


# ── E-H3: documents (S-9) ───────────────────────────────────────────────────

RAW = Spec("raw_allure_json", "test_run_id", "test_runs", id_fields={"test_case_id": "test_cases"})
LIVE = Spec("live_execution_events", "run_id", "test_runs", slug_key=True)
PAYLOADS = Spec("ai_analysis_payloads", "test_case_id", "test_cases")
PIPE_LOG = Spec("pipeline_event_log", "pipeline_run_id", "agent_pipeline_runs")


def _collect(gen: Any) -> list[tuple[str, Any]]:
    async def run() -> list[tuple[str, Any]]:
        return [item async for item in gen]
    return asyncio.run(run())


def _lines(members: list[tuple[str, bytes]], prefix: str) -> list[dict[str, Any]]:
    from bson import json_util

    out = []
    for name, data in members:
        if name.startswith(prefix):
            out.extend(json_util.loads(line) for line in data.decode("utf-8").splitlines())
    return out


def _sentinels() -> dict[str, str]:
    """REAL-LOOKING, non-hex secrets, built at run time (never a literal token in the file)."""
    return {
        "bearer": "glpat" + "-" + "SentinelGitlabTokenQwXyz",
        "url_token": "sk" + "-or-v1-" + "SentinelOpenRouterKeyPlughXyzzyWq",
        "password": "Hunter" + "SentinelPasswordZq!",
    }


def test_documents_are_iterated_in_in_chunks_and_never_loaded_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hx, "_mongo_specs", lambda: (RAW,))
    run_ids = [uuid.uuid4() for _ in range(1001)]
    when = datetime(2026, 10, 2, 8, 0)
    docs = [{"_id": f"oid-{n:05d}", "test_run_id": str(run_id), "test_case_id": str(uuid.uuid4()),
             "status": "failed", "stop": when} for n, run_id in enumerate(run_ids)]
    mongo = FakeMongo({"raw_allure_json": docs})
    stats: dict[str, Any] = {}
    members = _collect(hx.iter_documents(RecordingSession({}), mongo, window=_window(run_ids), stats=stats))
    assert [call["sort"] for call in mongo.calls] == [("_id", 1)] * 3          # 1001 keys → chunks of 500
    assert {call["batch_size"] for call in mongo.calls} == {vocab.MONGO_BATCH_DOCS}
    assert [len(call["query"]["test_run_id"]["$in"]) for call in mongo.calls] == [500, 500, 1]
    lines = _lines(members, "documents/raw_allure_json/")
    assert len(lines) == 1001 and all("_id" not in line for line in lines)
    assert lines[0]["stop"] == when                       # relaxed Extended JSON: the stored type round-trips
    raw_text = members[0][1].decode("utf-8")
    assert '"$date"' in raw_text and "oid-00000" not in raw_text
    assert stats["history"]["documents"] == {"raw_allure_json": {"docs": 1001, "parts": 1}}


def test_a_document_is_scrubbed_and_its_ids_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hx, "_mongo_specs", lambda: (Spec(
        "rest_api_payloads", "test_run_id", "test_runs", id_fields={"test_case_id": "test_cases"},
        project_field="project_id"),))
    secret = _sentinels()
    run_id, case_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    doc = {"_id": "x1", "test_run_id": str(run_id), "test_case_id": str(case_id), "project_id": str(project_id),
           "request": {"headers": {"Authorization": "Bearer " + secret["bearer"]},
                       "url": "https://api.example.test/v1/items?token=" + secret["url_token"]},
           "params": [{"name": "login", "password": secret["password"]}]}
    members = _collect(hx.iter_documents(RecordingSession({}), FakeMongo({"rest_api_payloads": [doc]}),
                                         window=_window([run_id], project_id), stats={}))
    text = b"".join(data for _name, data in members).decode("utf-8")
    for value in secret.values():
        assert value not in text
    assert "[REDACTED]" in text
    (line,) = _lines(members, "documents/rest_api_payloads/")
    assert (line["test_run_id"], line["test_case_id"], line["project_id"]) == (
        str(run_id), str(case_id), str(project_id))


def test_a_live_event_keyed_by_a_session_slug_travels_under_its_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hx, "_mongo_specs", lambda: (LIVE,))
    slug = "local-" + "slugRunAlpha"
    run_id = uuid.uuid5(uuid.NAMESPACE_DNS, slug)          # stream_service.canonical_test_run_uuid
    other_run = uuid.uuid4()
    db = RecordingSession({"live_sessions": [{"run_id": slug}, {"run_id": "local-" + "notInWindow"}]})
    docs = [{"_id": "a", "run_id": slug, "session_id": slug, "kind": "step"},
            {"_id": "b", "run_id": str(other_run), "kind": "step"},
            {"_id": "c", "run_id": "local-" + "notInWindow", "kind": "step"}]
    mongo = FakeMongo({"live_execution_events": docs})
    members = _collect(hx.iter_documents(db, mongo, window=_window([run_id, other_run]), stats={}))
    lines = _lines(members, "documents/live_execution_events/")
    # a slug-keyed event is written under its run's UUID string (the archive's key is always a run of the
    # archive — G-C refuses a non-UUID key); the session outside the window does not travel at all
    assert sorted(line["run_id"] for line in lines) == sorted([str(run_id), str(other_run)])
    (call,) = mongo.calls
    assert sorted(call["query"]["run_id"]["$in"]) == sorted([str(run_id), str(other_run), slug])
    assert "FROM live_sessions" in _sql(db.statements[0])


def test_the_real_mongo_specs_are_all_selectable() -> None:
    """Every G-A ``MongoSpec`` has a key map the export knows and a key it selects by."""
    from app.services.project_export_documents import MONGO_COLLECTIONS

    assert len(MONGO_COLLECTIONS) == 10
    for spec in MONGO_COLLECTIONS:
        assert spec.key_map in ("test_runs", "test_cases", "agent_pipeline_runs"), spec.collection
        assert spec.key in ("test_run_id", "run_id", "test_case_id", "pipeline_run_id"), spec.collection
    from app.services.project_export_objects import ROOT_MINIO_PREFIX

    assert hx.OBJECT_ROOT == ROOT_MINIO_PREFIX


def test_case_and_pipeline_keyed_documents_use_ids_streamed_from_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hx, "_mongo_specs", lambda: (PAYLOADS, PIPE_LOG))
    cases = [uuid.uuid4() for _ in range(3)]
    pipeline = uuid.uuid4()
    db = RecordingSession({"test_cases": [{"id": c} for c in cases], "agent_pipeline_runs": [{"id": pipeline}]})
    mongo = FakeMongo({
        "ai_analysis_payloads": [{"_id": n, "test_case_id": str(c), "analysis": "ok"} for n, c in enumerate(cases)]
        + [{"_id": 9, "test_case_id": str(uuid.uuid4()), "analysis": "another run"}],
        "pipeline_event_log": [{"_id": 1, "pipeline_run_id": str(pipeline), "event": "done"}],
    })
    stats: dict[str, Any] = {}
    _collect(hx.iter_documents(db, mongo, window=_window([uuid.uuid4()]), stats=stats))
    assert stats["history"]["documents"] == {
        "ai_analysis_payloads": {"docs": 3, "parts": 1}, "pipeline_event_log": {"docs": 1, "parts": 1},
    }
    streamed = [_sql(s) for s in db.statements]
    assert any("SELECT test_cases.id \nFROM test_cases \nWHERE test_cases.test_run_id = ANY" in s for s in streamed)
    assert any("FROM agent_pipeline_runs" in s and "agent_pipeline_runs.status IN" in s for s in streamed)


def test_an_oversized_document_fails_the_export_naming_only_the_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hx, "_mongo_specs", lambda: (RAW,))
    monkeypatch.setattr(vocab, "MONGO_DOC_MAX_BYTES", 200)
    run_id = uuid.uuid4()
    mongo = FakeMongo({"raw_allure_json": [{"_id": 1, "test_run_id": str(run_id), "body": "step ok " * 60}]})
    with pytest.raises(svc.ProjectExportFailed) as caught:
        _collect(hx.iter_documents(RecordingSession({}), mongo, window=_window([run_id]), stats={}))
    assert caught.value.code == "row_too_large" and caught.value.params == {"entity": "raw_allure_json", "count": 1}


def test_a_type_extended_json_would_not_round_trip_refuses_the_export(monkeypatch: pytest.MonkeyPatch) -> None:
    from bson import Binary

    monkeypatch.setattr(hx, "_mongo_specs", lambda: (RAW,))
    run_id = uuid.uuid4()
    mongo = FakeMongo({"raw_allure_json": [{"_id": 1, "test_run_id": str(run_id), "blob": Binary(b"\x00\x01")}]})
    with pytest.raises(svc.ProjectExportFailed) as caught:
        _collect(hx.iter_documents(RecordingSession({}), mongo, window=_window([run_id]), stats={}))
    assert caught.value.code == "export_build"
    assert caught.value.params == {"error_type": "UnsupportedDocumentType"}


# ── R-1: objects as StreamedMembers ─────────────────────────────────────────

def _object_world(monkeypatch: pytest.MonkeyPatch) -> tuple[uuid.UUID, list[uuid.UUID], dict[str, bytes], dict]:
    monkeypatch.setattr(vocab, "OBJECT_MAX_BYTES", 50_000)
    project_id = uuid.uuid4()
    newest, refused, uploaded = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    root = f"{project_id}/builds/build-2001/"
    objects = {
        root + "allure/abc-result.json": b'{"status": "failed", "name": "login"}',
        root + "shots/failure.png": b"\x89PNG\r\n\x1a\n" + b"pixel" * 2000,
        root + "video/huge.webm": b"v" * 60_000,                                   # > OBJECT_MAX_BYTES
        root + "bad\\name.txt": b"backslash suffix",                               # not a safe suffix
        f"uploads/{project_id}/{newest}/queued/raw.json": b"ingest input",         # O-2: never travels
        f"uploads/{project_id}/{uploaded}/result.json": b"ingest input too",
    }
    prefixes = {newest: root, refused: f"{uuid.uuid4()}/builds/b2/", uploaded: f"uploads/{project_id}/{uploaded}/"}
    return project_id, [newest, refused, uploaded], objects, prefixes


def test_the_plan_copies_only_the_runs_minio_prefix_range_and_counts_what_it_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, runs, objects, prefixes = _object_world(monkeypatch)
    listing = asyncio.run(FakeObjectStore(objects).list_objects(f"{project_id}/"))
    listing += asyncio.run(FakeObjectStore(objects).list_objects(f"uploads/{project_id}/"))
    listing.sort(key=lambda entry: entry["Key"])
    plan = hx.plan_from_listing(listing, project_id=project_id, window=_window(runs, project_id), prefixes=prefixes)
    assert [(item.run_id, item.suffix) for item in plan.objects] == [
        (runs[0], "allure/abc-result.json"), (runs[0], "shots/failure.png"),
    ]
    assert plan.skipped == {("test_runs", "unsafe_key"): 1, ("objects", "unsafe_key"): 1,
                            ("objects", "too_large"): 1}
    assert plan.count == 2 and plan.bytes == sum(len(objects[i.key]) for i in plan.objects)


def test_objects_stream_into_the_archive_as_stored_bytes_with_an_index(monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import io
    import json
    import zipfile

    from app.services import compliance_pack_service as packs

    project_id, runs, objects, prefixes = _object_world(monkeypatch)
    store = FakeObjectStore(objects)
    db = RecordingSession({"test_runs": [{"id": r, "minio_prefix": prefixes[r]} for r in runs]})
    window = _window(runs, project_id)
    stats: dict[str, Any] = {}
    progress: list[tuple[str, int, int]] = []

    async def on_progress(phase: str, done: int, total: int) -> None:
        progress.append((phase, done, total))

    async def build() -> bytes:
        plan = await hx.plan_objects(db, store, project_id=project_id, window=window)
        sink = io.BytesIO()
        scope = packs.ExportScope.for_project(project_id=project_id, manifest_extra={})
        await packs.build_archive(scope, sink=sink, generated_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
                                  members=hx.iter_objects(db, store, project_id=project_id, window=window,
                                                          stats=stats, on_progress=on_progress, plan=plan))
        return sink.getvalue()

    archive = zipfile.ZipFile(io.BytesIO(asyncio.run(build())))
    root = prefixes[runs[0]]
    json_member, png_member = f"objects/{runs[0]}/000001.bin", f"objects/{runs[0]}/000002.bin"
    assert archive.read(json_member) == objects[root + "allure/abc-result.json"]
    assert archive.read(png_member) == objects[root + "shots/failure.png"]
    assert archive.getinfo(png_member).compress_type == zipfile.ZIP_STORED
    assert archive.getinfo(json_member).compress_type == zipfile.ZIP_DEFLATED
    index = [json.loads(line) for line in archive.read("objects/index/part-0001.jsonl").splitlines()]
    assert index[1] == {
        "member": png_member, "run_id": str(runs[0]), "root": "minio_prefix", "suffix": "shots/failure.png",
        "bytes": len(objects[root + "shots/failure.png"]), "content_type": "image/png",
        "sha256": hashlib.sha256(objects[root + "shots/failure.png"]).hexdigest(),
    }
    # no member name is a stored key, and nothing of the ingest input / refused / skipped objects travelled
    assert not any(name.startswith(("uploads", str(project_id))) for name in archive.namelist())
    assert store.streamed == [root + "allure/abc-result.json", root + "shots/failure.png"]
    assert stats["history"]["objects"] == {"count": 2, "bytes": index[0]["bytes"] + index[1]["bytes"], "parts": 1}
    assert progress[0] == ("objects", 0, 2) and progress[-1] == ("objects", 2, 2)


@pytest.mark.parametrize("count,size", [(vocab.HISTORY_MAX_MEMBERS - hx.MEMBER_MARGIN + 1, 1),
                                        (1, vocab.HISTORY_EXPORT_MAX_BYTES + 1)])
def test_the_precheck_refuses_over_the_member_or_byte_cap(count: int, size: int) -> None:
    run_id = uuid.uuid4()
    plan = hx.ObjectPlan(objects=[hx.PlannedObject(run_id=run_id, key=f"k{n}", suffix=f"s{n}", size=size)
                                  for n in range(count)])
    with pytest.raises(svc.ProjectExportFailed) as caught:
        hx.precheck(plan)
    assert caught.value.code == "history_too_large"
    hx.precheck(hx.ObjectPlan(objects=plan.objects[:1] if size == 1 else []))   # under both caps: no refusal


# ── the whole history export on a recording snapshot ────────────────────────

class ArchiveStore(FakeObjectStore):
    """The source objects (read) + the compliance bucket (the archive is written there, then re-read)."""

    def __init__(self, objects: Mapping[str, bytes]) -> None:
        super().__init__(objects)
        self.archives: dict[str, bytes] = {}

    async def put_object_stream(self, key: str, fileobj: Any, *, size: int, content_type: str, bucket: str) -> None:
        fileobj.seek(0)
        self.archives[key] = fileobj.read()

    async def stream_object(self, key: str, bucket: Optional[str] = None) -> Any:
        source = self.archives if bucket else self.objects
        if not bucket:
            self.streamed.append(key)
        data = source[key]
        for start in range(0, len(data), 65536):
            yield data[start:start + 65536]

    async def delete_object(self, key: str, bucket: Optional[str] = None) -> None:
        self.archives.pop(key, None)


@pytest.fixture()
def signing(monkeypatch: pytest.MonkeyPatch) -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.services import compliance_signing

    private = Ed25519PrivateKey.generate()
    key = compliance_signing.SigningKey(
        key_id=compliance_signing.key_id_for(private.public_key()),
        public_key_pem=compliance_signing.public_key_pem(private.public_key()), private_key=private,
    )

    async def active(db: Any, *, actor: Any, create: bool) -> Any:
        return key

    async def keys(db: Any) -> dict[str, str]:
        return {key.key_id: key.public_key_pem}

    monkeypatch.setattr(compliance_signing, "active_signing_key", active)
    monkeypatch.setattr(compliance_signing, "public_keys", keys)
    return key


def _history_world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(hx, "_mongo_specs", lambda: (RAW, LIVE))
    project_id, run_id, case_id, author = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    created = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    root = f"{project_id}/builds/build-77/"
    rows = {
        "projects": [{"id": project_id, "name": "Payment Service", "slug": "payment-service"}],
        "test_runs": [{"id": run_id, "project_id": project_id, "status": "FAILED", "created_at": created,
                       "minio_prefix": root, "build_number": "77"}],
        "test_cases": [{"id": case_id, "test_run_id": run_id, "test_name": "test_login", "status": "failed",
                        "triage_updated_by_user_id": author}],
        "ai_analysis": [{"id": uuid.uuid4(), "test_case_id": case_id, "root_cause_summary": "timeout"}],
    }
    mongo = FakeMongo({"raw_allure_json": [{"_id": 1, "test_run_id": str(run_id), "name": "test_login"}]})
    store = ArchiveStore({root + "allure/a-result.json": b'{"ok": false}'})
    project = SimpleNamespace(id=project_id, name="Payment Service", slug="payment-service")
    return {"rows": rows, "mongo": mongo, "store": store, "project": project, "run_id": run_id,
            "users": [(author, "alice", "Alice A")]}


def _export(world: dict[str, Any], db: Any, **kwargs: Any) -> svc.ProjectExportOutcome:
    async def no_sleep(_seconds: float) -> None:
        return None

    return asyncio.run(svc.run_project_export(
        db, job_id=uuid.uuid4(), project=world["project"], storage=world["store"],
        now=datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc), sleep=no_sleep, mongo=world["mongo"],
        options={"include_history": True, "history_days": 90, "max_runs": 2000}, **kwargs,
    ))


def test_a_history_export_reads_only_and_writes_a_schema_2_archive(
    monkeypatch: pytest.MonkeyPatch, signing: Any,
) -> None:
    import io
    import json
    import zipfile

    world = _history_world(monkeypatch)
    db = RecordingSession(world["rows"], users=world["users"], counts=(1, 1), skipped=0)
    progress: list[dict[str, Any]] = []

    async def on_progress(entry: dict[str, Any]) -> None:
        progress.append(entry)

    outcome = _export(world, db, sign_db=RecordingSession({}), on_progress=on_progress)
    # the snapshot saw SELECTs only (RecordingSession asserts no INSERT / UPDATE / DELETE, add, flush or commit)
    assert db.statements and all(type(s).__name__ == "Select" for s in db.statements)
    assert db.rollbacks == 1          # released before sign / write / verify
    (data,) = world["store"].archives.values()
    archive = zipfile.ZipFile(io.BytesIO(data))
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["schema_version"] == vocab.SCHEMA_VERSION == 2
    history = manifest["history"]
    assert (history["runs"], history["runs_in_progress_skipped"], history["runs_over_cap"]) == (1, 1, 0)
    assert history["documents"]["raw_allure_json"] == {"docs": 1, "parts": 1}
    assert history["objects"]["count"] == 1
    assert "entities/test_runs/part-0001.jsonl" in archive.namelist()
    assert f"objects/{world['run_id']}/000001.bin" in archive.namelist()
    assert "Run history (schema_version 2):" in archive.read("README.txt").decode("utf-8")
    assert outcome.counts["test_runs"] == 1 and outcome.counts["documents.raw_allure_json"] == 1
    assert outcome.counts["objects"] == 1
    phases = [entry["phase"] for entry in progress]
    assert phases[0] == "catalog" and "runs" in phases and "documents" in phases and "objects" in phases
    assert phases[-1] == "verify"
    assert set(manifest["entities"]) == {spec.table for spec in svc.plan(include_history=True)}


def test_a_history_export_over_the_caps_fails_before_any_member(
    monkeypatch: pytest.MonkeyPatch, signing: Any,
) -> None:
    world = _history_world(monkeypatch)
    monkeypatch.setattr(vocab, "HISTORY_EXPORT_MAX_BYTES", 5)       # the one object (13 bytes) passes it
    db = RecordingSession(world["rows"], users=world["users"], counts=(1, 0))
    with pytest.raises(svc.ProjectExportFailed) as caught:
        _export(world, db, sign_db=RecordingSession({}))
    assert caught.value.code == "history_too_large"
    assert world["store"].archives == {} and world["store"].streamed == []
    assert world["mongo"].calls == []
    assert not any(stmt.get_final_froms()[0].name == "projects" for stmt in db.statements)   # no member read


def test_a_body_less_export_is_still_phase_1(monkeypatch: pytest.MonkeyPatch, signing: Any) -> None:
    import io
    import json
    import zipfile

    world = _history_world(monkeypatch)
    db = RecordingSession(world["rows"], users=world["users"])

    async def no_sleep(_seconds: float) -> None:
        return None

    asyncio.run(svc.run_project_export(db, job_id=uuid.uuid4(), project=world["project"], storage=world["store"],
                                       now=datetime(2026, 11, 3, tzinfo=timezone.utc), sleep=no_sleep,
                                       sign_db=RecordingSession({})))
    (data,) = world["store"].archives.values()
    archive = zipfile.ZipFile(io.BytesIO(data))
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["schema_version"] == 1 and "history" not in manifest
    assert not any(n.startswith(("documents/", "objects/", "entities/test_runs/")) for n in archive.namelist())
    assert world["mongo"].calls == [] and world["store"].streamed == []


# ── E-H7: the estimate (a GET: counts only) ─────────────────────────────────

def _estimate(world: dict[str, Any], db: Any, *, mongo: Any = None, store: Any = None) -> Any:
    return asyncio.run(hx.estimate(
        db, mongo if mongo is not None else world["mongo"], store if store is not None else world["store"],
        project_id=world["project"].id, options={"include_history": True, "history_days": 90, "max_runs": 2000},
        now=datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc),
    ))


def test_the_estimate_counts_what_the_export_would_write(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _history_world(monkeypatch)
    db = RecordingSession(world["rows"], counts=(1, 1))
    result = _estimate(world, db)
    assert result.measured == {"postgres": True, "documents": True, "objects": True}
    assert (result.runs, result.runs_in_progress, result.runs_over_cap, result.test_cases) == (1, 1, 0, 1)
    assert result.documents == {"raw_allure_json": 1, "live_execution_events": 0}
    assert (result.objects, result.object_bytes) == (1, len(b'{"ok": false}'))
    assert result.estimated_bytes and result.over_limit is False
    assert all(type(s).__name__ == "Select" for s in db.statements)     # a GET writes nothing
    assert world["store"].streamed == [] and world["store"].archives == {}


@pytest.mark.parametrize("down", ["documents", "objects"])
def test_an_unreachable_store_is_unmeasured_never_zero(monkeypatch: pytest.MonkeyPatch, down: str) -> None:
    world = _history_world(monkeypatch)
    db = RecordingSession(world["rows"], counts=(1, 0))
    mongo = FakeMongo({}, down=True) if down == "documents" else world["mongo"]
    store = FakeObjectStore({}, down=True) if down == "objects" else world["store"]
    result = _estimate(world, db, mongo=mongo, store=store)
    assert result.measured[down] is False and result.measured["postgres"] is True
    if down == "documents":
        assert result.documents is None and result.objects == 1
    else:
        assert result.objects is None and result.object_bytes is None and result.documents is not None
    assert result.estimated_bytes is None and result.over_limit is None
    assert result.runs == 1     # Postgres counts still answer


def test_a_catalog_estimate_needs_no_other_store(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _history_world(monkeypatch)
    db = RecordingSession(world["rows"])
    result = asyncio.run(hx.estimate(db, FakeMongo({}, down=True), FakeObjectStore({}, down=True),
                                     project_id=world["project"].id, options={"include_history": False}))
    assert result.include_history is False and result.runs is None
    assert result.estimated_bytes == hx.ROW_BYTES_ESTIMATE * 1     # the one projects row
    assert not any(s.get_final_froms()[0].name == "test_runs" for s in db.statements)


# ── E-H4: bounded memory ────────────────────────────────────────────────────

def test_peak_memory_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """300 runs / 30 000 documents / 2 000 objects (one of them 80 MiB) through the REAL builder: the export
    holds one part or one chunk at a time, so Python's peak allocation stays far under the 400 MiB job budget
    (< 64 MiB) — an object read whole would alone pass it. The source data is built before tracing starts."""
    import tempfile
    import tracemalloc

    from app.services import compliance_pack_service as packs

    monkeypatch.setattr(hx, "_mongo_specs", lambda: (RAW,))
    project_id = uuid.uuid4()
    runs = [uuid.uuid4() for _ in range(300)]
    docs = [{"_id": n, "test_run_id": str(runs[n % 300]), "n": n, "status": "failed", "duration_ms": 1500 + n}
            for n in range(30_000)]
    mongo = FakeMongo({"raw_allure_json": docs})
    objects = {f"{project_id}/builds/b{n % 300}/f{n:05d}.json": (b'{"n": %d, "text": "' % n) + b"x " * 2000 + b'"}'
               for n in range(1_999)}
    objects[f"{project_id}/builds/b0/video/failure.png"] = b"\x89PNG\r\n\x1a\n" + bytes(80 * 1024 * 1024)
    prefixes = {run: f"{project_id}/builds/b{i}/" for i, run in enumerate(runs)}
    store = FakeObjectStore(objects)
    db = RecordingSession({"test_runs": [{"id": r, "minio_prefix": prefixes[r]} for r in runs]})
    window = _window(runs, project_id)

    async def members() -> Any:
        stats: dict[str, Any] = {}
        async for item in hx.iter_documents(db, mongo, window=window, stats=stats):
            yield item
        async for item in hx.iter_objects(db, store, project_id=project_id, window=window, stats=stats):
            yield item

    async def build(sink: Any) -> Any:
        scope = packs.ExportScope.for_project(project_id=project_id, manifest_extra={})
        return await packs.build_archive(scope, members=members(), sink=sink,
                                         generated_at=datetime(2026, 11, 3, tzinfo=timezone.utc))

    with tempfile.TemporaryFile() as sink:
        tracemalloc.start()
        try:
            result = asyncio.run(build(sink))
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert result.file_count > 2_000          # every object + the document / index parts + the manifest
    assert peak < 64 * 1024 * 1024, f"peak {peak / 1024 / 1024:.1f} MiB"


# ── id lists into test_runs: narrowed to the archive's runs ────────────────

def test_a_run_id_list_names_only_runs_of_the_archive() -> None:
    inside, outside = uuid.uuid4(), uuid.uuid4()
    keep = frozenset({str(inside)})
    assert hx.filter_run_ids([str(inside), str(outside), "not-a-run"], keep) == ([str(inside)], 2)
    assert hx.filter_run_ids({str(inside): 3, str(outside): 1}, keep) == ({str(inside): 3}, 1)   # production's dict
    assert hx.filter_run_ids(None, keep) == (None, 0)
    assert hx.filter_run_ids([str(inside)], keep) == ([str(inside)], 0)


def test_the_row_hook_narrows_gate_decisions_and_counts_pipeline_scratch() -> None:
    inside, outside = uuid.uuid4(), uuid.uuid4()
    window = _window([inside])
    skips: dict[tuple[str, str], int] = {}
    gate = hx.row_hook(spec_for("release_gate_decisions"), window, skips)
    row = {"run_ids": [str(inside), str(outside)], "incomplete_runs": {str(outside): 2}}
    gate(row)
    assert row == {"run_ids": [str(inside)], "incomplete_runs": {}}
    evidence = hx.row_hook(spec_for("evidence_artifacts"), window, skips)
    evidence({"uri_or_ref": "pipeline-artifacts/pipeline/2026/10/01/p/s/a.json"})
    evidence({"uri_or_ref": "https://jira.example.test/browse/PAY-1"})
    assert skips == {("release_gate_decisions", "run_not_exported"): 2, ("evidence_artifacts", "pipeline_scratch"): 1}
    assert hx.row_hook(spec_for("test_runs"), window, skips) is None
    # failure_clusters.member_test_ids point at test_cases — complete by construction, never narrowed here
    assert hx.row_hook(spec_for("failure_clusters"), window, skips) is None
