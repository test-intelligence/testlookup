"""W30 ADM-05 phase 2 — the import of run history (``project_import_history``), without a database.

* ``assemble_parts`` streams parts 1…n IN ORDER into one spool; the digest / size are the start's or the
  upload is refused (``upload_digest_mismatch``); a missing part names itself (``upload_incomplete``).
* ``validate_history`` refuses every crafted document / object index line BEFORE any write: the recording
  session, the recording Mongo and the recording storage see NOTHING (``run_import`` end to end).
* History ids are ``uuid5(job_id, source)``: stable per job, distinct across jobs, never a source id.
* Every stored ref is rebuilt under the new project's root (``rebuild_prefix``); a foreign one → NULL, counted.
* Documents: every id field rewritten, ``ai_analysis_payloads`` gains ``test_run_id``, ``insert_many``
  ordered batches; objects written under the rebuilt root with the archive's bytes, re-hashed.
* ``cleanup_outside_postgres`` deletes by the ledger ONLY and is idempotent.

The live round trip is ``tests/integration/test_project_history_transfer_postgres_mongo.py``.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import io
import json
import uuid
import zipfile
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest

from app.services import project_export_vocab as vocab
from app.services import project_import_history as hist
from app.services import project_import_service as svc
from app.services.project_export_documents import encode_document
from app.services.project_import_service import ProjectImportFailed

# ── a schema-2 archive, built in memory ──────────────────────────────────────


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(r, sort_keys=True, default=str).encode("utf-8") + b"\n" for r in rows)


class World:
    """One source project with one FAILED run, one case, documents of four collections and one object."""

    def __init__(self) -> None:
        self.project = uuid.uuid4()
        self.slug = "live-session-north-" + uuid.uuid4().hex[:6].upper().replace("A", "G")
        self.run = uuid.uuid5(uuid.NAMESPACE_DNS, self.slug)       # a live run's id IS its slug's canonical uuid
        self.case = uuid.uuid4()
        self.created = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(days=3)
        self.prefix = f"{self.project}/builds/build-77/"
        self.object = b'{"name": "checkout pays", "status": "failed"}'

    def rows(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "projects": [{"description": "Payments", "tags": ["pay"]}],
            "test_runs": [{
                "id": str(self.run), "build_number": "build-77", "status": "FAILED",
                "minio_prefix": self.prefix, "created_at": self.created.isoformat(),
            }],
            "test_cases": [{
                "id": str(self.case), "test_run_id": str(self.run), "test_fingerprint": "fp-checkout",
                "test_name": "checkout pays", "minio_s3_prefix": self.prefix,
            }],
        }

    def documents(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "raw_allure_json": [{"test_run_id": str(self.run), "test_case_id": "allure-result-qwerty",
                                 "stored_at": self.created}],
            "live_execution_events": [{"run_id": self.slug, "session_id": self.slug, "type": "run_start",
                                       "project_id": str(self.project)}],
            "run_summaries": [{"test_run_id": str(self.run), "project_id": str(self.project), "summary": "one failed"}],
            "ai_analysis_payloads": [{"test_case_id": str(self.case), "analysis": {"root_cause": "timeout"}}],
        }


def build_history_archive(
    world: World,
    *,
    documents: Optional[dict[str, list[Any]]] = None,
    raw_documents: Optional[dict[str, bytes]] = None,
    index: Optional[list[dict[str, Any]]] = None,
    history_patch: Optional[dict[str, Any]] = None,
    extra_members: Optional[dict[str, bytes]] = None,
    schema_version: int = vocab.SCHEMA_VERSION,
) -> bytes:
    """A project export WITH history (contract §1.3): rows, documents, one object + its index line, manifest —
    schema 3 as W37 writes it, or ``schema_version=2`` as a W30 exporter did."""
    members: dict[str, bytes] = {}
    listed: dict[str, dict[str, Any]] = {}
    for table, rows in world.rows().items():
        members[f"entities/{table}/part-0001.jsonl"] = _jsonl(rows)
        listed[table] = {"rows": len(rows), "parts": 1}
    docs_block: dict[str, dict[str, int]] = {}
    for collection, docs in (documents if documents is not None else world.documents()).items():
        lines = (raw_documents or {}).get(collection)
        if lines is None:
            lines = b"".join(encode_document(d).encode("utf-8") + b"\n" for d in docs)
        members[f"documents/{collection}/part-0001.jsonl"] = lines
        docs_block[collection] = {"docs": len(docs), "parts": 1}
    member = f"objects/{world.run}/000001.bin"
    members[member] = world.object
    line = {"member": member, "run_id": str(world.run), "root": "minio_prefix", "suffix": "allure/r1-result.json",
            "bytes": len(world.object), "sha256": hashlib.sha256(world.object).hexdigest(),
            "content_type": "application/json"}
    lines_index = index if index is not None else [line]
    members["objects/index/part-0001.jsonl"] = _jsonl(lines_index)
    members["directory/users.jsonl"] = b""
    members["README.txt"] = b"Settings, catalog and run history of one project.\n"
    members.update(extra_members or {})
    history_block: dict[str, Any] = {
        "window_days": 90, "max_runs": 2000, "runs": 1, "documents": docs_block,
        "objects": {"count": len(lines_index), "bytes": sum(int(i.get("bytes") or 0) for i in lines_index), "parts": 1},
        "skipped": [],
    }
    history_block.update(history_patch or {})
    manifest = {
        "format_version": 1, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "project_id": str(world.project),
        "files": [{"name": n, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in sorted(members.items())],
        "scope": "project", "format": vocab.ARCHIVE_FORMAT, "schema_version": schema_version,
        "source": {"app_version": "9.9.9", "host": None},
        "project": {"name": "Payment Service", "slug": "payment-service"},
        "entities": listed, "history": history_block,
    }
    members["manifest.json"] = json.dumps(manifest, sort_keys=True).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ── recording doubles: a session, a Mongo database, a storage provider ───────


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self) -> "_Result":
        return _Result([r[0] if isinstance(r, tuple) else r for r in self._rows])

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class RecordingSession:
    """SELECTs answer ``[]``; every other statement, ``add`` and ``flush`` is a recorded WRITE."""

    def __init__(self) -> None:
        from sqlalchemy.sql.selectable import Select

        self._select = Select
        self.writes: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        if not isinstance(statement, self._select):
            self.writes.append(str(statement).split()[0].upper())
        return _Result([])

    def add(self, obj: Any) -> None:
        self.writes.append(f"add {type(obj).__name__}")

    async def flush(self) -> None:
        self.writes.append("flush")

    async def get(self, *_a: Any, **_k: Any) -> Any:
        return None


class FakeCollection:
    def __init__(self, db: "FakeMongo", name: str) -> None:
        self.db, self.name = db, name

    async def insert_many(self, docs: list[dict[str, Any]], ordered: bool = True) -> Any:
        self.db.writes.append(("insert_many", self.name, len(docs), ordered))
        if self.db.fail_insert is not None:
            self.db.fail_insert(self.name, len(self.db.writes))
        self.db.docs.setdefault(self.name, []).extend(copy.deepcopy(docs))
        return SimpleNamespace(inserted_ids=list(range(len(docs))))

    async def delete_many(self, flt: dict[str, Any]) -> Any:
        self.db.writes.append(("delete_many", self.name, json.dumps(flt, sort_keys=True, default=str)))
        (field, cond), = flt.items()
        wanted = set(cond["$in"])
        kept = [d for d in self.db.docs.get(self.name, []) if d.get(field) not in wanted]
        removed = len(self.db.docs.get(self.name, [])) - len(kept)
        self.db.docs[self.name] = kept
        return SimpleNamespace(deleted_count=removed)


class FakeMongo:
    def __init__(self) -> None:
        self.docs: dict[str, list[dict[str, Any]]] = {}
        self.writes: list[tuple[Any, ...]] = []
        self.fail_insert: Optional[Callable[[str, int], None]] = None

    def __getitem__(self, name: str) -> FakeCollection:
        return FakeCollection(self, name)


class FakeStorage:
    """Objects in a dict; the upload and the parts are served from ``blobs``; every put / delete recorded."""

    def __init__(self, blobs: Optional[dict[str, bytes]] = None) -> None:
        self.blobs = dict(blobs or {})
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.reads: list[str] = []
        self.deleted_prefixes: list[str] = []
        self.fail_put: Optional[Callable[[str], None]] = None

    async def stream_object(self, key: str, bucket: Optional[str] = None):
        self.reads.append(key)
        if key not in self.blobs:
            raise FileNotFoundError("missing")
        blob = self.blobs[key]
        for start in range(0, len(blob), 5):
            yield blob[start:start + 5]

    async def put_object_stream(self, key: str, fileobj: Any, *, size: int, content_type: str = "",
                                bucket: Optional[str] = None) -> None:
        self.puts.append(key)
        if self.fail_put is not None:
            self.fail_put(key)
        fileobj.seek(0)
        data = b""
        while True:
            chunk = fileobj.read(7)
            if not chunk:
                break
            data += chunk
        self.objects[key] = data

    async def delete_prefix(self, prefix: str, bucket: Optional[str] = None) -> int:
        self.deleted_prefixes.append(prefix)
        gone = [k for k in self.objects if k.startswith(prefix)]
        for key in gone:
            del self.objects[key]
        return len(gone)


# ── assemble_parts: order, digest, size ──────────────────────────────────────


def _parted(data: bytes, parts: int, *, sha: Optional[str] = None, size: Optional[int] = None) -> tuple[Any, FakeStorage]:
    # the job as the worker sees it: queued, ``upload`` NULL, the start's plan kept in ``options``
    job = SimpleNamespace(id=uuid.uuid4(), archive_sha256=None, upload=None, options={
        "parted": True, "parts": parts, "bytes": len(data) if size is None else size,
        "sha256": sha or hashlib.sha256(data).hexdigest(),
    })
    step = -(-len(data) // parts)
    blobs = {hist.part_key(job.id, n): data[(n - 1) * step:n * step] for n in range(1, parts + 1)}
    return job, FakeStorage(blobs)


async def test_parts_are_assembled_in_order_and_the_digest_is_the_whole_files():
    data = b"".join(f"segment-{n:03d};".encode() for n in range(40))
    job, storage = _parted(data, 4)
    spool = io.BytesIO()
    digest = await hist.assemble_parts(storage, job, spool=spool)
    assert spool.getvalue() == data and digest == hashlib.sha256(data).hexdigest()
    assert storage.reads == [f"project-imports/parts/{job.id}/part-{n:04d}" for n in (1, 2, 3, 4)]


@pytest.mark.parametrize("bad", ["sha", "size"])
async def test_an_assembled_file_that_is_not_the_starts_is_refused(bad):
    data = b"a project export of forty-two bytes, roughly"
    job, storage = _parted(data, 3, sha="0" * 64 if bad == "sha" else None, size=len(data) + 1 if bad == "size" else None)
    with pytest.raises(ProjectImportFailed) as ei:
        await hist.assemble_parts(storage, job, spool=io.BytesIO())
    assert ei.value.code == "upload_digest_mismatch"
    assert ei.value.message == vocab.ERROR_SENTENCES["upload_digest_mismatch"]


async def test_a_missing_part_names_itself():
    data = b"x" * 90
    job, storage = _parted(data, 3)
    del storage.blobs[hist.part_key(job.id, 2)]
    with pytest.raises(ProjectImportFailed) as ei:
        await hist.assemble_parts(storage, job, spool=io.BytesIO())
    assert ei.value.code == "upload_incomplete" and "parts 2" in ei.value.message


# ── ids: uuid5 of the job ────────────────────────────────────────────────────


def test_history_ids_are_stable_per_job_distinct_across_jobs_and_never_the_source():
    job_a, job_b, source = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    first, again = hist.history_id(job_a, source), hist.history_id(job_a, str(source).upper())
    other = hist.history_id(job_b, source)
    assert first == again and first != other and source not in (first, other)
    assert first == uuid.uuid5(job_a, str(source))


def test_the_remap_resolves_bounded_parents_only_when_they_are_in_the_archive():
    job, run, stranger, case = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    remap = hist.HistoryRemap(job, {"test_runs": frozenset({run}), "agent_pipeline_runs": frozenset()})
    assert remap.new_id("test_runs", str(run)) == hist.history_id(job, run)
    assert remap.new_id("test_runs", stranger) is None                       # a run outside the archive
    assert remap.new_id("test_cases", case) == hist.history_id(job, case)    # unbounded: Postgres enforces the FK
    assert remap.new_id("test_runs", "not-a-uuid") is None
    project = uuid.uuid4()
    assert remap.ledger(project) == {"project_id": str(project), "run_ids": [str(hist.history_id(job, run))],
                                     "pipeline_ids": []}


# ── every crafted document / index line is refused before ANY write, in every store ──

_SENTINEL = "SENTINELdocumentValueQuokka"
_OPERATOR = "$" + "where"                    # built at run time
_UP = ".." + "/"


def _nested(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"leaf": _SENTINEL}
    for _ in range(depth):
        node = {"n": node}
    return node


def _with_doc(world: World, collection: str, doc: dict[str, Any]) -> bytes:
    docs = world.documents()
    docs[collection] = docs.get(collection, []) + [doc]
    return build_history_archive(world, documents=docs)


def _index_line(world: World, **patch: Any) -> dict[str, Any]:
    line = {"member": f"objects/{world.run}/000001.bin", "run_id": str(world.run), "root": "minio_prefix",
            "suffix": "allure/r1-result.json", "bytes": len(world.object),
            "sha256": hashlib.sha256(world.object).hexdigest(), "content_type": "application/json"}
    line.update(patch)
    return line


def _counts_off(world: World) -> bytes:
    block = {c: {"docs": len(d), "parts": 1} for c, d in world.documents().items()}
    block["raw_allure_json"]["docs"] = 5
    return build_history_archive(world, history_patch={"documents": block})


# (id, builder(world), code, a fragment of the sentence)
_CRAFTED: list[tuple[str, Callable[[World], bytes], str, str]] = [
    ("operator-key", lambda w: _with_doc(w, "raw_allure_json", {"test_run_id": str(w.run), _OPERATOR: _SENTINEL}),
     "invalid_document", "raw_allure_json document 2 in documents/raw_allure_json/part-0001.jsonl"),
    ("dotted-key", lambda w: _with_doc(w, "run_summaries", {"test_run_id": str(uuid.uuid4()), "a.b": _SENTINEL}),
     "invalid_document", "a key containing a dot"),
    ("depth-33", lambda w: _with_doc(w, "raw_allure_json", {"test_run_id": str(w.run), **_nested(33)}),
     "invalid_document", "nested deeper than 32"),
    ("an-id-field", lambda w: _with_doc(w, "raw_allure_json", {"test_run_id": str(w.run), "_id": _SENTINEL}),
     "invalid_document", "an _id field"),
    ("missing-key", lambda w: _with_doc(w, "raw_allure_json", {"note": _SENTINEL}),
     "invalid_document", "key is missing or malformed"),
    ("repeated-unique-key", lambda w: _with_doc(w, "run_summaries", {"test_run_id": str(w.run), "summary": "again"}),
     "invalid_document", "a repeated unique key"),
    ("unknown-collection", lambda w: build_history_archive(w, documents={**w.documents(), "secret_stash": [{"x": 1}]}),
     "unknown_entity", "contains secret_stash"),
    ("document-count-differs", _counts_off, "tampered", "a document count differs"),
    ("unsafe-suffix", lambda w: build_history_archive(w, index=[_index_line(w, suffix=_UP + _UP + "outside/k.json")]),
     "tampered", "an unsafe object key"),
    ("absolute-suffix", lambda w: build_history_archive(w, index=[_index_line(w, suffix="/abs/key.json")]),
     "tampered", "an unsafe object key"),
    ("index-digest-differs", lambda w: build_history_archive(w, index=[_index_line(w, sha256="b" * 64)]),
     "tampered", "an object does not match its manifest entry"),
    ("index-run-not-in-archive", lambda w: build_history_archive(w, index=[_index_line(w, run_id=str(uuid.uuid4()))]),
     "tampered", "an object belongs to a run that is not in the archive"),
    ("index-line-extra-field", lambda w: build_history_archive(w, index=[_index_line(w, path="elsewhere")]),
     "tampered", "an object index line is not valid"),
    ("object-not-indexed", lambda w: build_history_archive(
        w, extra_members={f"objects/{w.run}/000002.bin": b"stray bytes"}),
     "tampered", "an unexpected member"),
    # stays inside the archive root (scan_zip passes it), but is no history member shape
    ("traversal-member", lambda w: build_history_archive(w, extra_members={"objects/" + _UP + "x.bin": b"x"}),
     "tampered", "an unexpected member"),
    ("escaping-member", lambda w: build_history_archive(w, extra_members={_UP + _UP + "x.bin": b"x"}),
     "unsafe_archive", "unsafe"),
]


async def _run_refused(blob: bytes) -> tuple[ProjectImportFailed, RecordingSession, FakeMongo, FakeStorage, list[Any]]:
    session, mongo = RecordingSession(), FakeMongo()
    storage = FakeStorage({"project-imports/x.zip": blob})
    calls: list[Any] = []

    async def on_table(done: int, total: int) -> None:
        calls.append(("table", done))

    async def on_ledger(ledger: dict[str, Any]) -> None:
        calls.append(("ledger", ledger))

    job = SimpleNamespace(id=uuid.uuid4(), project_name=None, storage_key="project-imports/x.zip")
    actor = SimpleNamespace(id=uuid.uuid4(), role="admin", username="importer")
    with pytest.raises(ProjectImportFailed) as ei:
        await svc.run_import(session, job=job, storage=storage, actor=actor, now=dt.datetime.now(dt.timezone.utc),
                             on_table=on_table, mongo=mongo, on_ledger=on_ledger)
    return ei.value, session, mongo, storage, calls


@pytest.mark.parametrize(("builder", "code", "fragment"), [c[1:] for c in _CRAFTED], ids=[c[0] for c in _CRAFTED])
async def test_every_crafted_history_member_is_refused_before_any_write(builder, code, fragment):
    failure, session, mongo, storage, calls = await _run_refused(builder(World()))
    assert failure.code == code, failure.message
    assert fragment in failure.message
    assert _SENTINEL not in failure.message and _SENTINEL not in repr(failure.fmt)   # never a value
    assert session.writes == [] and mongo.writes == [] and storage.puts == [] and calls == []


async def test_the_valid_world_passes_the_preflight():
    """The control: the same builder, nothing crafted → the preflight proves it (no refusal)."""
    world = World()
    pre = svc._preflight_sync(io.BytesIO(build_history_archive(world)), {})
    assert pre.has_history and pre.source_project_id == world.project
    assert pre.bounded_ids["test_runs"] == {world.run} and pre.run_prefixes == {world.run: world.prefix}
    assert pre.history_pre is not None and pre.history_pre.objects == 1
    assert pre.history_pre.documents == {"raw_allure_json": 1, "live_execution_events": 1, "run_summaries": 1,
                                         "ai_analysis_payloads": 1}


# ── documents and objects: every id rewritten, every key rebuilt ─────────────


def _proved(world: World, blob: Optional[bytes] = None) -> tuple[Any, zipfile.ZipFile, hist.HistoryRemap]:
    raw = blob or build_history_archive(world)
    pre = svc._preflight_sync(io.BytesIO(raw), {})
    remap = hist.HistoryRemap(uuid.uuid4(), {t: frozenset(v) for t, v in pre.bounded_ids.items()})
    return pre, zipfile.ZipFile(io.BytesIO(raw)), remap


async def test_documents_are_written_with_every_id_rewritten():
    world, mongo = World(), FakeMongo()
    pre, zf, remap = _proved(world)
    new_project, new_run = uuid.uuid4(), hist.history_id(remap.job_id, world.run)
    new_case = hist.history_id(remap.job_id, world.case)
    asked: list[list[uuid.UUID]] = []

    async def case_runs(ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
        asked.append(ids)
        return {new_case: new_run}

    skipped: dict[str, int] = {}
    written = await hist.import_documents(mongo, zf, pre=pre.history_pre, remaps=remap, new_project_id=new_project,
                                          case_runs=case_runs, skipped=skipped)
    assert written["raw_allure_json"] == written["run_summaries"] == written["ai_analysis_payloads"] == 1
    assert written["live_execution_events"] == 1 and written["decision_reports"] == 0 and skipped == {}
    run_text, project_text = str(new_run), str(new_project)
    assert mongo.docs["raw_allure_json"][0]["test_run_id"] == run_text
    assert mongo.docs["raw_allure_json"][0]["test_case_id"] == "allure-result-qwerty"     # an Allure uuid: kept
    assert isinstance(mongo.docs["raw_allure_json"][0]["stored_at"], dt.datetime)         # $date → a datetime
    event = mongo.docs["live_execution_events"][0]
    assert (event["run_id"], event["session_id"], event["project_id"]) == (run_text, run_text, project_text)
    assert mongo.docs["run_summaries"][0]["project_id"] == project_text
    assert mongo.docs["ai_analysis_payloads"][0] == {"test_case_id": str(new_case), "test_run_id": run_text,
                                                     "analysis": {"root_cause": "timeout"}}
    assert asked == [[new_case]]
    assert all("_id" not in d for docs in mongo.docs.values() for d in docs)
    assert all(w[3] is True for w in mongo.writes if w[0] == "insert_many")               # ordered=True


async def test_an_unresolved_document_key_is_skipped_and_counted():
    world, mongo = World(), FakeMongo()
    docs = world.documents()
    docs["run_summaries"].append({"test_run_id": str(uuid.uuid4()), "summary": "a run outside the archive"})
    pre, zf, remap = _proved(world, build_history_archive(world, documents=docs))

    async def no_cases(ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
        return {}

    skipped: dict[str, int] = {}
    written = await hist.import_documents(mongo, zf, pre=pre.history_pre, remaps=remap, new_project_id=uuid.uuid4(),
                                          case_runs=no_cases, skipped=skipped)
    assert written["run_summaries"] == 1 and written["ai_analysis_payloads"] == 0
    assert skipped == {"run_summaries": 1, "ai_analysis_payloads": 1}


async def test_documents_go_in_ordered_batches_of_the_cap(monkeypatch):
    monkeypatch.setattr(vocab, "MONGO_BATCH_DOCS", 2)
    world, mongo = World(), FakeMongo()
    docs = world.documents()
    docs["raw_allure_json"] = [{"test_run_id": str(world.run), "n": n} for n in range(5)]
    pre, zf, remap = _proved(world, build_history_archive(world, documents=docs))
    await hist.import_documents(mongo, zf, pre=pre.history_pre, remaps=remap, new_project_id=uuid.uuid4())
    assert [w[2] for w in mongo.writes if w[1] == "raw_allure_json"] == [2, 2, 1]


async def test_a_driver_error_reads_documents_unwritable_with_its_type_only():
    world, mongo = World(), FakeMongo()
    pre, zf, remap = _proved(world)

    class DuplicateKeyError(Exception):
        pass

    def fail(name: str, _n: int) -> None:
        raise DuplicateKeyError(_SENTINEL)

    mongo.fail_insert = fail
    with pytest.raises(ProjectImportFailed) as ei:
        await hist.import_documents(mongo, zf, pre=pre.history_pre, remaps=remap, new_project_id=uuid.uuid4())
    assert ei.value.code == "documents_unwritable" and "(DuplicateKeyError)" in ei.value.message
    assert _SENTINEL not in ei.value.message


async def test_objects_are_written_under_the_rebuilt_root_with_the_archives_bytes():
    world, storage = World(), FakeStorage()
    pre, zf, remap = _proved(world)
    new_project = uuid.uuid4()
    count, size = await hist.import_objects(storage, zf, pre=pre.history_pre, remaps=remap, new_project_id=new_project)
    key = f"{new_project}/builds/build-77/allure/r1-result.json"
    assert (count, size) == (1, len(world.object)) and storage.objects == {key: world.object}
    assert all(k.startswith(f"{new_project}/") for k in storage.puts)


async def test_a_refused_run_prefix_writes_no_object_and_is_counted():
    world, storage = World(), FakeStorage()
    pre, zf, remap = _proved(world)
    pre.history_pre.run_prefixes[world.run] = f"uploads/{world.project}/{world.run}/queued/"
    skipped: dict[str, int] = {}
    count, _ = await hist.import_objects(storage, zf, pre=pre.history_pre, remaps=remap, new_project_id=uuid.uuid4(),
                                         skipped=skipped)
    assert count == 0 and storage.puts == [] and skipped == {"objects": 1}


@pytest.mark.parametrize("fault", ["storage", "digest"])
async def test_an_object_that_cannot_be_written_reads_object_unreadable(fault, monkeypatch):
    world, storage = World(), FakeStorage()
    pre, zf, remap = _proved(world)
    if fault == "storage":
        def boom(_key: str) -> None:
            raise ConnectionError(_SENTINEL)
        storage.fail_put = boom
    else:                                       # the streamed member's digest is re-checked while writing
        original = hist._index_line

        def lying(raw: Optional[bytes]) -> Optional[dict[str, Any]]:
            line = original(raw)
            return {**line, "sha256": "c" * 64} if line else line

        monkeypatch.setattr(hist, "_index_line", lying)
    with pytest.raises(ProjectImportFailed) as ei:
        await hist.import_objects(storage, zf, pre=pre.history_pre, remaps=remap, new_project_id=uuid.uuid4())
    assert ei.value.code == "object_unreadable"
    assert ("(ConnectionError)" if fault == "storage" else "(DigestMismatch)") in ei.value.message


# ── I-H9: the cleanup removes what the ledger names, nothing else, any number of times ──


async def test_cleanup_deletes_by_the_ledger_only_and_is_idempotent():
    from app.services.project_export_documents import MONGO_COLLECTIONS

    new_project, other_project = uuid.uuid4(), uuid.uuid4()
    mine_run, mine_pipeline, foreign_run = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    mongo, storage = FakeMongo(), FakeStorage()
    for spec in MONGO_COLLECTIONS:
        field = "test_run_id" if spec.key_map == "test_cases" else spec.key
        mine = mine_pipeline if spec.key_map == "agent_pipeline_runs" else mine_run
        mongo.docs[spec.collection] = [{field: mine, "n": 1}, {field: foreign_run, "n": 2}]
    storage.objects = {f"{new_project}/builds/b/r.json": b"1", f"{new_project}/builds/b/s.png": b"2",
                       f"{other_project}/builds/b/r.json": b"3", "project-imports/parts/j/part-0001": b"4"}
    ledger = {"project_id": str(new_project), "run_ids": [mine_run], "pipeline_ids": [mine_pipeline]}

    first = await hist.cleanup_outside_postgres(mongo, storage, ledger)
    assert first == {**{spec.collection: 1 for spec in MONGO_COLLECTIONS}, "objects": 2}
    assert all(docs == [d for d in docs if d.get("n") == 2] and len(docs) == 1 for docs in mongo.docs.values())
    assert set(storage.objects) == {f"{other_project}/builds/b/r.json", "project-imports/parts/j/part-0001"}
    assert storage.deleted_prefixes == [f"{new_project}/"]
    second = await hist.cleanup_outside_postgres(mongo, storage, ledger)
    assert set(second.values()) == {0}


async def test_a_ledger_without_a_project_id_deletes_nothing():
    mongo, storage = FakeMongo(), FakeStorage()
    with pytest.raises(ValueError):
        await hist.cleanup_outside_postgres(mongo, storage, {"project_id": "", "run_ids": [], "pipeline_ids": []})
    assert mongo.writes == [] and storage.deleted_prefixes == []


# ── rows: every stored ref rebuilt under the new project's root ──────────────


def test_every_stored_ref_is_rebuilt_under_the_new_root_or_cleared():
    from app.services.project_export_entities import spec_for

    source, new, job = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run = uuid.uuid4()
    ctx = svc._Context(project_id=new, actor_id=uuid.uuid4(), now=dt.datetime.now(dt.timezone.utc), user_map={},
                       release_keeper=None, schema_version=2, source_project_id=source,
                       history_remap=hist.HistoryRemap(job, {"test_runs": frozenset({run})}))
    row = svc.transform_row(ctx, spec_for("test_runs"), {
        "id": run, "build_number": "b-9", "status": "PASSED", "minio_prefix": f"{source}/builds/b-9/"})
    assert row["id"] == hist.history_id(job, run) and row["minio_prefix"] == f"{new}/builds/b-9/"
    case = svc.transform_row(ctx, spec_for("test_cases"), {
        "id": uuid.uuid4(), "test_run_id": run, "test_fingerprint": "fp", "test_name": "t",
        "minio_s3_prefix": f"{uuid.uuid4()}/builds/b-9/"})                      # another project's tree
    assert case["test_run_id"] == row["id"] and case["minio_s3_prefix"] is None
    assert ctx.cleared[("test_cases", "minio_s3_prefix", "unsafe_key")] == 1
    orphan = svc.transform_row(ctx, spec_for("test_cases"), {
        "id": uuid.uuid4(), "test_run_id": uuid.uuid4(), "test_fingerprint": "fp", "test_name": "t"})
    assert orphan is None and ctx.skipped[("test_cases", "test_run_id", "row_unresolved")] == 1


def test_id_lists_the_product_reads_are_rewritten_and_outsiders_removed():
    from app.services.project_export_entities import spec_for

    job, run, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    release = uuid.uuid4()
    ctx = svc._Context(project_id=uuid.uuid4(), actor_id=uuid.uuid4(), now=dt.datetime.now(dt.timezone.utc),
                       user_map={}, release_keeper=None, schema_version=2,
                       history_remap=hist.HistoryRemap(job, {"test_runs": frozenset({run})}))
    ctx.remap["releases"] = {release: uuid.uuid4()}
    row = svc.transform_row(ctx, spec_for("release_gate_decisions"), {
        "id": uuid.uuid4(), "release_id": release, "verdict": "go",
        "run_ids": [str(run), str(stranger)], "incomplete_runs": []})
    assert row["run_ids"] == [str(hist.history_id(job, run))] and row["incomplete_runs"] == []
    assert ctx.cleared[("release_gate_decisions", "run_ids", "run_not_exported")] == 1
    assert row["release_id"] == ctx.remap["releases"][release]


def test_an_id_map_keyed_by_run_has_its_keys_rewritten_and_outsiders_removed():
    """Production writes ``release_gate_decisions.incomplete_runs`` as a DICT ``{run_id: rejected}``
    (``release_rollup_service``); the seed writes a list. Both shapes: the KEYS go through the remap, the values
    are kept, a run outside the archive is removed and counted."""
    from app.services.project_export_entities import spec_for

    job, run, other, stranger, release = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ctx = svc._Context(project_id=uuid.uuid4(), actor_id=uuid.uuid4(), now=dt.datetime.now(dt.timezone.utc),
                       user_map={}, release_keeper=None, schema_version=2,
                       history_remap=hist.HistoryRemap(job, {"test_runs": frozenset({run, other})}))
    ctx.remap["releases"] = {release: uuid.uuid4()}
    row = svc.transform_row(ctx, spec_for("release_gate_decisions"), {
        "id": uuid.uuid4(), "release_id": release, "verdict": "no_go", "run_ids": [str(run)],
        "incomplete_runs": {str(run): 3, str(other).upper(): 0, str(stranger): 7}})
    assert row["incomplete_runs"] == {str(hist.history_id(job, run)): 3, str(hist.history_id(job, other)): 0}
    assert str(run) not in row["incomplete_runs"] and str(stranger) not in json.dumps(row["incomplete_runs"])
    assert ctx.cleared[("release_gate_decisions", "incomplete_runs", "run_not_exported")] == 1
