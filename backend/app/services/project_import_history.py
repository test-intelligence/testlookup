"""Import the RUN HISTORY of a schema-2 project export (W30 ADM-05 phase 2, contract §2.6).

History is DATA, never an ingest: rows go in with ``insert()`` (the service),
documents with ``insert_many``, objects with ``put_object_stream`` — nothing
here imports an ingest, notification, webhook, gate, defect, flaky, agent / LLM
or search module (the static guard ``test_project_import_side_effect_free.py``).

* :func:`assemble_parts` — a PARTED upload's parts 1…n streamed, in order, into
  ONE spool while hashing; the whole file's SHA-256 is returned for the caller to
  compare with the start's (``upload_digest_mismatch``).
* :func:`validate_history` — I-H2 (h): EVERY document line (``validate_document``
  after the relaxed Extended-JSON decode, the spec's key a UUID string, no repeat
  of a unique key) and EVERY object index line (a safe suffix, its member present
  and listed once, ``bytes`` / ``sha256`` equal to the signed manifest's entry, its
  run among the archive's runs) — BEFORE the first write in any store.
* :func:`history_id` — a history row's id: ``uuid5(job_id, source id)``: stable per
  job (a re-run of the same job writes the same ids, which the cleanup removed
  first), distinct across jobs, never a source id.
* :func:`import_documents` / :func:`import_objects` — I-H6 / I-H7, outside the
  Postgres transaction, every id field rewritten, every object key rebuilt under
  ``{new_project_id}/`` (the only root an import writes).
* :func:`cleanup_outside_postgres` — I-H9: by the LEDGER only (new run / pipeline
  ids, the new project root); idempotent.

Stage-only (``STAGE_ONLY_SERVICES``): no commit. Log lines carry job / project
ids, collection names, counts, byte totals, codes and error TYPE names — never a
document value, a member name, a storage key or a digest.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import uuid
import zipfile
from typing import IO, Any, Awaitable, Callable, Iterator, Mapping, Optional

import structlog

from app.services import project_export_vocab as vocab

_slog = structlog.get_logger(__name__)

DOCUMENT_PART = re.compile(r"^documents/(?P<collection>[a-z_]{1,63})/part-(?P<index>\d{4})\.jsonl$")
OBJECT_INDEX_PART = re.compile(r"^objects/index/part-(?P<index>\d{4})\.jsonl$")
OBJECT_MEMBER = re.compile(r"^objects/(?P<run>[0-9a-f-]{36})/(?P<ordinal>\d{6})\.bin$")
#: The schema-2 member shapes (I-H2 (d)); a schema-1 archive accepts none of them.
HISTORY_MEMBER = re.compile(
    r"^(documents/[a-z_]{1,63}/part-\d{4}\.jsonl|objects/index/part-\d{4}\.jsonl|objects/[0-9a-f-]{36}/\d{6}\.bin)$"
)
_INDEX_LINE_MAX = 4 * 1024
_CONTENT_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}$")
_DEFAULT_CONTENT_TYPE = "application/octet-stream"
_DELETE_CHUNK = 500
#: Bounded parents whose source ids the preflight keeps (≤ 2 000 runs + their pipelines, clusters, steps).
BOUNDED_PARENTS = ("test_runs", "agent_pipeline_runs", "failure_clusters", "test_steps")


def _fail(code: str, **fmt: Any) -> Exception:
    """The service's ``ProjectImportFailed`` (imported lazily: the service imports this module)."""
    from app.services.project_import_service import ProjectImportFailed

    return ProjectImportFailed(code, **fmt)


def history_id(job_id: uuid.UUID, source_id: Any) -> uuid.UUID:
    """A history row's NEW id: ``uuid5(job_id, str(UUID(source_id)))`` — deterministic per job, distinct
    across jobs (another namespace), and never equal to a source id."""
    return uuid.uuid5(job_id, str(uuid.UUID(str(source_id))))


def as_uuid(value: Any) -> Optional[uuid.UUID]:
    """``value`` as a UUID (a UUID or its text), else None — never raises."""
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


# ── I-H1: a parted upload's parts, assembled ─────────────────────────────────


def part_key(job_id: uuid.UUID, part: int) -> str:
    """``project-imports/parts/<job_id>/part-0001`` (``COMPLIANCE_BUCKET``) — the job service's ONE builder."""
    from app.services.project_data_job_service import part_key as _part_key

    return _part_key(job_id, part)


def parts_prefix(job_id: uuid.UUID) -> str:
    """Every part of one upload — removed when the job ends and by the janitor after ``UPLOAD_STALE_HOURS``."""
    from app.services.project_data_job_service import parts_prefix as _parts_prefix

    return _parts_prefix(job_id)


def upload_plan(job: Any) -> dict[str, Any]:
    """A parted upload's plan as the START recorded it: ``options`` = ``{"parted", "parts", "bytes", "sha256"}``
    (kept for the job's life — ``upload`` is NULL once the upload completed), ``upload`` as a fallback."""
    plan: dict[str, Any] = {}
    for source in (getattr(job, "upload", None), getattr(job, "options", None)):
        if isinstance(source, Mapping):
            plan.update({k: source[k] for k in ("parts", "bytes", "sha256") if source.get(k) is not None})
    return plan


def upload_sha256(job: Any) -> Optional[str]:
    """The whole file's SHA-256 given when the upload started (``options.sha256``, else ``archive_sha256``)."""
    return str(upload_plan(job).get("sha256") or getattr(job, "archive_sha256", None) or "") or None


async def assemble_parts(storage: Any, job: Any, *, spool: IO[bytes]) -> str:
    """Stream parts 1…n of ``job``'s upload into ``spool`` IN ORDER, hashing; returns the hex SHA-256.

    ``upload_incomplete`` when a part cannot be read (names it); ``upload_too_large_history`` past
    ``IMPORT_ARCHIVE_MAX_BYTES``; ``upload_digest_mismatch`` when the assembled size differs from the
    start's ``bytes`` or the digest from its ``sha256``. Nothing is parsed; one chunk in memory.
    """
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    plan = upload_plan(job)
    parts, expected_bytes = int(plan.get("parts") or 0), int(plan.get("bytes") or 0)
    if not 1 <= parts <= vocab.IMPORT_MAX_PARTS:
        raise _fail("upload_parts_invalid")
    digest = hashlib.sha256()
    total = 0
    for part in range(1, parts + 1):
        try:
            async for chunk in storage.stream_object(part_key(job.id, part), bucket=COMPLIANCE_BUCKET):
                total += len(chunk)
                if total > vocab.IMPORT_ARCHIVE_MAX_BYTES:
                    raise _fail("upload_too_large_history")
                digest.update(chunk)
                spool.write(chunk)
        except Exception as exc:  # noqa: BLE001 - a missing part: the type decides, the key is never logged
            if getattr(exc, "code", None) == "upload_too_large_history":
                raise
            _slog.warning("project_import_part_unreadable", job_id=str(job.id), part=part,
                          error_type=type(exc).__name__)
            raise _fail("upload_incomplete", missing=str(part)) from None
    hexdigest = digest.hexdigest()
    expected = upload_sha256(job)
    if total != expected_bytes or (expected is not None and hexdigest != expected.lower()):
        _slog.warning("project_import_upload_digest_mismatch", job_id=str(job.id), parts=parts, bytes=total)
        raise _fail("upload_digest_mismatch")
    _slog.info("project_import_parts_assembled", job_id=str(job.id), parts=parts, bytes=total)
    return hexdigest


async def remove_parts(storage: Any, job_id: uuid.UUID) -> int:
    """Delete every part of ``job_id``'s upload; returns how many (a storage fault propagates)."""
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    return int(await storage.delete_prefix(parts_prefix(job_id), bucket=COMPLIANCE_BUCKET))


# ── I-H2 (h): every document and object index line, before any write ────────


@dataclasses.dataclass
class HistoryPreflight:
    """What :func:`validate_history` proved; nothing here came from an unverified member."""

    document_parts: dict[str, list[str]] = dataclasses.field(default_factory=dict)   # collection → parts, in order
    documents: dict[str, int] = dataclasses.field(default_factory=dict)              # collection → valid lines
    index_parts: list[str] = dataclasses.field(default_factory=list)
    objects: int = 0
    object_bytes: int = 0
    source_project_id: Optional[uuid.UUID] = None                                   # the manifest's project_id
    run_prefixes: dict[uuid.UUID, Optional[str]] = dataclasses.field(default_factory=dict)   # source run → minio_prefix
    #: what the EXPORT left out (manifest ``history.skipped``: non-terminal pipelines, unsafe / too-large objects,
    #: pipeline scratch refs, runs outside the archive) → (entity, reason) → count, so the report says it too
    export_skips: dict[tuple[str, str], int] = dataclasses.field(default_factory=dict)


def _invalid_document(collection: str, line: int, file: str, reason: str) -> Exception:
    """``invalid_document`` naming the collection, the line, the file (a strict member name) and the reason
    (``DOCUMENT_REASONS`` text) — never a value."""
    from app.services.project_export_documents import DOCUMENT_REASONS

    return _fail("invalid_document", collection=collection, line=line, file=file,
                 reason=DOCUMENT_REASONS.get(reason, reason))


def _tampered(reason: str) -> Exception:
    return _fail("tampered", reason=reason)


def _iter_raw_lines(zf: zipfile.ZipFile, name: str, *, max_line: int) -> Iterator[tuple[int, Optional[bytes]]]:
    """``(line number, raw line)`` streamed; ``None`` for a line longer than ``max_line``."""
    with zf.open(name) as reader:
        number = 0
        while True:
            chunk = reader.readline(max_line + 1)
            if not chunk:
                return
            number += 1
            if len(chunk) > max_line and not chunk.endswith(b"\n"):
                yield number, None
                while chunk and not chunk.endswith(b"\n"):     # skip the rest of the over-long line
                    chunk = reader.readline(max_line + 1)
                continue
            yield number, chunk


_PLAIN = (str, int, float, bool, type(None))


def _unsupported_type(value: Any, depth: int = 0) -> bool:
    """True when a decoded value holds a BSON type the export never writes (only datetime / ObjectId pass)."""
    import datetime as dt

    from bson import ObjectId

    if depth > vocab.MONGO_MAX_DEPTH + 1:
        return False                      # validate_document refuses the depth itself
    if isinstance(value, dict):
        return any(_unsupported_type(v, depth + 1) for v in value.values())
    if isinstance(value, list):
        return any(_unsupported_type(v, depth + 1) for v in value)
    return not isinstance(value, (*_PLAIN, dt.datetime, ObjectId))


def decode_document(raw: bytes, spec: Any) -> tuple[Any, Optional[str]]:
    """One archive line → ``(document, None)`` or ``(None, reason)``: relaxed Extended JSON (``$date`` /
    ``$oid`` become values first, so an operator key left over can only be a forged one), then
    ``validate_document`` with the collection's spec (its key present and well formed), then no BSON type
    beyond datetime / ObjectId (a forged ``$binary`` / ``$regex`` / ``$code`` decodes to one)."""
    from app.services.project_export_documents import decode_document as _decode
    from app.services.project_export_documents import validate_document

    if len(raw) > vocab.MONGO_DOC_MAX_BYTES + 1:
        return None, "too_large"
    try:
        doc = _decode(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 - not UTF-8 / not JSON / a malformed $date: a reason code, never the text
        return None, "not_an_object"
    reason = validate_document(doc, spec, encoded_size=len(raw.rstrip(b"\r\n")))
    if reason is not None:
        return None, reason
    if _unsupported_type(doc):
        return None, "unsupported_type"
    return doc, None


def _manifest_history(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    history = manifest.get("history")
    if not isinstance(history, Mapping):
        raise _tampered("the history block is missing")
    return history


_SKIP_ENTITY = re.compile(r"^[a-z_]{1,63}$")


def _export_skips(history: Mapping[str, Any]) -> dict[tuple[str, str], int]:
    """The manifest's ``history.skipped`` (``[{"entity", "reason", "count"}]``), validated: a malformed entry →
    ``tampered``; a reason this deployment has no sentence for (a newer exporter) is left out of the report."""
    out: dict[tuple[str, str], int] = {}
    entries = history.get("skipped") or []
    if not isinstance(entries, list):
        raise _tampered("the history block is not valid")
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise _tampered("the history block is not valid")
        entity, reason = entry.get("entity"), entry.get("reason")
        count = _listed_count(entry, "count")
        if not (isinstance(entity, str) and _SKIP_ENTITY.match(entity) and isinstance(reason, str)):
            raise _tampered("the history block is not valid")
        if reason in vocab.SKIP_REASONS and count > 0:
            out[(entity, reason)] = out.get((entity, reason), 0) + count
    return out


def _listed_count(entry: Any, key: str) -> int:
    value = entry.get(key) if isinstance(entry, Mapping) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _tampered("a history count is not a number")
    return value


def _validate_documents(zf: zipfile.ZipFile, names: list[str], history: Mapping[str, Any], out: HistoryPreflight) -> None:
    """Every ``documents/<collection>/part-NNNN.jsonl`` line: a known collection (else ``unknown_entity``),
    every listed part present and nothing else, every line a valid document whose key is a UUID string,
    no repeat of the collection's unique key; the counts the manifest states."""
    from app.services.project_export_documents import MONGO_COLLECTIONS, unique_value

    specs = {spec.collection: spec for spec in MONGO_COLLECTIONS}
    listed = history.get("documents") or {}
    if not isinstance(listed, Mapping):
        raise _tampered("the history block is not valid")
    present: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        match = DOCUMENT_PART.match(name)
        if match:
            present.setdefault(match["collection"], []).append((int(match["index"]), name))
    for collection in sorted(set(listed) | set(present)):
        if collection not in specs:
            text = str(collection)
            raise _fail("unknown_entity", entity=text if re.fullmatch(r"[a-z_]{1,63}", text) else "an unknown collection")
    for collection, entry in sorted(listed.items()):
        parts = _listed_count(entry, "parts")
        docs = _listed_count(entry, "docs")
        expected = [f"documents/{collection}/part-{i:04d}.jsonl" for i in range(1, parts + 1)]
        found = [name for _, name in sorted(present.get(collection, []))]
        if found != expected:
            raise _tampered("a listed part is missing" if set(expected) - set(found) else "an unexpected member")
        spec = specs[collection]
        seen: set[bytes] = set()
        count = 0
        for name in expected:
            for line, raw in _iter_raw_lines(zf, name, max_line=vocab.MONGO_DOC_MAX_BYTES):
                if raw is None:
                    raise _invalid_document(collection, line, name, "too_large")
                doc, reason = decode_document(raw, spec)
                if reason is not None:
                    raise _invalid_document(collection, line, name, reason)
                unique = unique_value(spec, doc)
                if unique is not None:
                    key = hashlib.blake2b(json.dumps(unique).encode("utf-8"), digest_size=16).digest()
                    if key in seen:
                        raise _invalid_document(collection, line, name, "duplicate_key")
                    seen.add(key)
                count += 1
        if count != docs:
            raise _tampered("a document count differs from the manifest")
        out.document_parts[collection] = expected
        out.documents[collection] = count
    for collection in present:
        if collection not in listed:
            raise _tampered("an unexpected member")


def _index_line(raw: Optional[bytes]) -> Optional[dict[str, Any]]:
    """One ``objects/index`` line → its seven fields, typed; None when it is not exactly that shape."""
    if raw is None:
        return None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    keys = {"member", "run_id", "root", "suffix", "bytes", "sha256", "content_type"}
    if not isinstance(obj, dict) or set(obj) != keys:
        return None
    size, digest, content_type = obj["bytes"], obj["sha256"], obj["content_type"]
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= vocab.OBJECT_MAX_BYTES:
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    if content_type is not None and not (isinstance(content_type, str) and _CONTENT_TYPE.match(content_type)):
        return None
    if not all(isinstance(obj[k], str) for k in ("member", "run_id", "root", "suffix")):
        return None
    return obj


def _validate_objects(
    zf: zipfile.ZipFile, names: list[str], manifest: Mapping[str, Any], history: Mapping[str, Any],
    run_ids: frozenset[uuid.UUID], out: HistoryPreflight,
) -> None:
    """Every ``objects/index`` line: a safe suffix (never trusted from the export), the root kind
    ``minio_prefix``, its member present / of its run / listed once, ``bytes`` and ``sha256`` equal to the
    SIGNED manifest's entry for that member, its run among the archive's runs; every object member indexed."""
    from app.services.project_export_objects import safe_suffix

    listed = history.get("objects") or {}
    if not isinstance(listed, Mapping):
        raise _tampered("the history block is not valid")
    parts = _listed_count(listed, "parts") if listed else 0
    expected = [f"objects/index/part-{i:04d}.jsonl" for i in range(1, parts + 1)]
    found = sorted(name for name in names if OBJECT_INDEX_PART.match(name))
    if found != expected:
        raise _tampered("a listed part is missing" if set(expected) - set(found) else "an unexpected member")
    members = {name for name in names if OBJECT_MEMBER.match(name)}
    signed: dict[str, tuple[Any, Any]] = {
        str(f.get("name")): (f.get("bytes"), f.get("sha256"))
        for f in manifest.get("files") or [] if isinstance(f, Mapping) and str(f.get("name")) in members
    }
    indexed: set[str] = set()
    for name in expected:
        for _line, raw in _iter_raw_lines(zf, name, max_line=_INDEX_LINE_MAX):
            obj = _index_line(raw)
            if obj is None:
                raise _tampered("an object index line is not valid")
            member, run_id = obj["member"], as_uuid(obj["run_id"])
            match = OBJECT_MEMBER.match(member)
            if match is None or member not in members or member in indexed:
                raise _tampered("an unexpected member")
            if run_id is None or run_id not in run_ids or as_uuid(match["run"]) != run_id:
                raise _tampered("an object belongs to a run that is not in the archive")
            if obj["root"] != "minio_prefix" or not safe_suffix(obj["suffix"]):
                raise _tampered("an unsafe object key")
            if signed.get(member) != (obj["bytes"], obj["sha256"]) or zf.getinfo(member).file_size != obj["bytes"]:
                raise _tampered("an object does not match its manifest entry")
            indexed.add(member)
            out.objects += 1
            out.object_bytes += int(obj["bytes"])
    if indexed != members:
        raise _tampered("an unexpected member")
    if listed and (_listed_count(listed, "count"), _listed_count(listed, "bytes")) != (out.objects, out.object_bytes):
        raise _tampered("an object count differs from the manifest")
    out.index_parts = expected


def validate_history(zf: zipfile.ZipFile, manifest: Mapping[str, Any], pre: Any) -> HistoryPreflight:
    """I-H2 (h), on the verified spool, BEFORE any write in any store: every document line and every object
    index line (``pre.bounded_ids["test_runs"]`` = the archive's source run ids, from the validated rows).
    Raises the first refusal (``invalid_document`` / ``unknown_entity`` / ``tampered``)."""
    names = [info.filename for info in zf.infolist()]
    history = _manifest_history(manifest)
    out = HistoryPreflight(
        source_project_id=getattr(pre, "source_project_id", None),
        run_prefixes=dict(getattr(pre, "run_prefixes", None) or {}),
        export_skips=_export_skips(history),
    )
    _validate_documents(zf, names, history, out)
    run_ids = frozenset((getattr(pre, "bounded_ids", None) or {}).get("test_runs", set()))
    _validate_objects(zf, names, manifest, history, run_ids, out)
    return out


# ── the remap of history ids (no dict of a million case ids) ─────────────────


@dataclasses.dataclass(frozen=True)
class HistoryRemap:
    """``uuid5(job_id, source)`` for every history table; for a BOUNDED parent (runs, pipelines, clusters,
    steps — ≤ 2 000 runs and their children) only when the source id is in the archive, else None."""

    job_id: uuid.UUID
    bounded: Mapping[str, frozenset[uuid.UUID]]

    def new_id(self, table: str, source: Any) -> Optional[uuid.UUID]:
        src = as_uuid(source)
        if src is None:
            return None
        known = self.bounded.get(table)
        if known is not None and src not in known:
            return None
        return history_id(self.job_id, src)

    def mapper(self, table: str) -> Callable[[Any], Optional[str]]:
        """``source id (text) → NEW id text`` or None — the form a document rewrite uses."""
        def _map(value: Any) -> Optional[str]:
            new = self.new_id(table, value)
            return str(new) if new is not None else None

        return _map

    def ledger(self, project_id: uuid.UUID) -> dict[str, Any]:
        """I-H5: what the import is about to write outside Postgres — every id is NEW (uuid5 of THIS job)."""
        return {
            "project_id": str(project_id),
            "run_ids": sorted(str(history_id(self.job_id, s)) for s in self.bounded.get("test_runs", ())),
            "pipeline_ids": sorted(str(history_id(self.job_id, s)) for s in self.bounded.get("agent_pipeline_runs", ())),
        }


# ── I-H6: documents ──────────────────────────────────────────────────────────

Progress = Callable[[str, int, int], Awaitable[None]]
CaseRuns = Callable[[list[uuid.UUID]], Awaitable[dict[uuid.UUID, uuid.UUID]]]


def _rewrite(spec: Any, doc: dict[str, Any], *, remap: HistoryRemap, new_project_id: uuid.UUID,
             new_run_id: Optional[str]) -> Optional[dict[str, Any]]:
    """G-A's ``rewrite_document`` with this import's remaps (key + id fields + project field; ``_id`` dropped)."""
    from app.services.project_export_documents import rewrite_document

    remaps = {table: remap.mapper(table) for table in ("test_runs", "test_cases", "agent_pipeline_runs")}
    return rewrite_document(spec, doc, remaps=remaps, new_project_id=str(new_project_id), new_run_id=new_run_id)


async def _insert(collection: Any, batch: list[dict[str, Any]]) -> None:
    try:
        await collection.insert_many(batch, ordered=True)
    except Exception as exc:  # noqa: BLE001 - DuplicateKeyError / BulkWriteError / a lost server: the type names it
        raise _fail("documents_unwritable", error_type=type(exc).__name__) from None


async def import_documents(
    mongo: Any, zf: zipfile.ZipFile, *, pre: HistoryPreflight, remaps: HistoryRemap, new_project_id: uuid.UUID,
    on_progress: Optional[Progress] = None, case_runs: Optional[CaseRuns] = None,
    skipped: Optional[dict[str, int]] = None,
) -> dict[str, int]:
    """Per ``MongoSpec`` in order, part by part: decode → validate (again, cheap) → rewrite → ``insert_many``
    (``ordered=True``, ≤ ``MONGO_BATCH_DOCS``). A document whose key does not resolve is not written
    (``skipped[collection]``). A case-keyed document (``ai_analysis_payloads``) gains ``test_run_id`` = its
    imported case's NEW run (``case_runs``: new case ids → new run ids, read from the import's own session).
    Returns collection → documents inserted (every spec present, 0 allowed)."""
    from app.services.project_export_documents import MONGO_COLLECTIONS

    inserted: dict[str, int] = {spec.collection: 0 for spec in MONGO_COLLECTIONS}
    total = sum(pre.documents.values())
    done = 0
    for spec in MONGO_COLLECTIONS:
        parts = pre.document_parts.get(spec.collection, [])
        collection = mongo[spec.collection]
        pending: list[dict[str, Any]] = []

        async def flush(pending: list[dict[str, Any]], spec: Any = spec, collection: Any = collection) -> None:
            ready = await _resolve_batch(spec, pending, remap=remaps, new_project_id=new_project_id,
                                         case_runs=case_runs, skipped=skipped)
            if ready:
                await _insert(collection, ready)
                inserted[spec.collection] += len(ready)

        for name in parts:
            for line, raw in _iter_raw_lines(zf, name, max_line=vocab.MONGO_DOC_MAX_BYTES):
                doc, reason = decode_document(raw, spec) if raw is not None else (None, "too_large")
                if reason is not None or doc is None:
                    raise _invalid_document(spec.collection, line, name, reason or "not_json")
                pending.append(doc)
                done += 1
                if len(pending) >= vocab.MONGO_BATCH_DOCS:
                    await flush(pending)
                    pending = []
                if on_progress is not None and done % 5_000 == 0:
                    await on_progress("documents", done, total)
        if pending:
            await flush(pending)
    if on_progress is not None:
        await on_progress("documents", done, total)
    _slog.info("project_import_documents_written", project_id=str(new_project_id),
               documents=sum(inserted.values()), collections=sum(1 for n in inserted.values() if n))
    return inserted


async def _resolve_batch(
    spec: Any, docs: list[dict[str, Any]], *, remap: HistoryRemap, new_project_id: uuid.UUID,
    case_runs: Optional[CaseRuns], skipped: Optional[dict[str, int]],
) -> list[dict[str, Any]]:
    """The batch's documents rewritten; an unresolved key → not written, counted ``skipped[collection]``."""
    runs_by_case: dict[uuid.UUID, uuid.UUID] = {}
    if spec.key_map == "test_cases":
        cases = [remap.new_id("test_cases", doc.get(spec.key)) for doc in docs]
        wanted = sorted({c for c in cases if c is not None})
        runs_by_case = await case_runs(wanted) if (case_runs is not None and wanted) else {}
    ready: list[dict[str, Any]] = []
    for doc in docs:
        if spec.key_map == "test_cases":
            case = remap.new_id("test_cases", doc.get(spec.key))
            run = runs_by_case.get(case) if case is not None else None
            out = (
                _rewrite(spec, doc, remap=remap, new_project_id=new_project_id, new_run_id=str(run))
                if run is not None else None
            )
        else:                       # a run key (a slug through canonical_run_uuid) or a pipeline key
            out = _rewrite(spec, doc, remap=remap, new_project_id=new_project_id, new_run_id=None)
        if out is None:
            if skipped is not None:
                skipped[spec.collection] = skipped.get(spec.collection, 0) + 1
            continue
        out.pop("_id", None)                         # never chosen by the import: a fresh ObjectId on insert
        ready.append(out)
    return ready


# ── I-H7: objects ────────────────────────────────────────────────────────────


class _HashingReader:
    """A member stream that hashes and counts what the storage provider reads (``seek(0)`` restarts)."""

    def __init__(self, zf: zipfile.ZipFile, member: str) -> None:
        self._zf, self._member = zf, member
        self._fh = zf.open(member)
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._fh.read(size if size is not None and size >= 0 else -1)   # the providers read in chunks
        self.digest.update(chunk)
        self.size += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        if offset != 0 or whence != 0:
            raise OSError("only a rewind to the start is supported")
        if self.size:
            self._fh.close()
            self._fh = self._zf.open(self._member)
            self.digest, self.size = hashlib.sha256(), 0
        return 0

    def tell(self) -> int:
        return self.size

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        self._fh.close()


def _run_roots(pre: Any, *, new_project_id: uuid.UUID) -> dict[uuid.UUID, Optional[str]]:
    """source run id → the copy's ``minio_prefix`` (``rebuild_prefix``; None = refused, objects not written)."""
    from app.services.project_export_objects import rebuild_prefix

    source_project = getattr(pre, "source_project_id", None)
    roots: dict[uuid.UUID, Optional[str]] = {}
    for run_id, prefix in (getattr(pre, "run_prefixes", None) or {}).items():
        roots[run_id] = (
            rebuild_prefix(prefix, source_project_id=source_project, new_project_id=new_project_id)
            if prefix and source_project else None
        )
    return roots


async def import_objects(
    storage: Any, zf: zipfile.ZipFile, *, pre: HistoryPreflight, remaps: HistoryRemap, new_project_id: uuid.UUID,
    on_progress: Optional[Progress] = None, skipped: Optional[dict[str, int]] = None,
) -> tuple[int, int]:
    """Per index line: ``zf.open(member)`` through a hashing reader → ``put_object_stream(rebuild_prefix(run's
    minio_prefix) + suffix)`` in the default bucket → size and SHA-256 re-checked. Every key starts with
    ``{new_project_id}/`` (asserted). A run whose prefix ``rebuild_prefix`` refuses writes nothing
    (``skipped["objects"]``, reason ``unsafe_key``). A mismatch or a storage error → ``object_unreadable``.
    Returns ``(objects written, bytes written)``."""
    from app.services.project_export_objects import object_key

    roots = _run_roots(pre, new_project_id=new_project_id)
    project_root = f"{new_project_id}/"
    written = written_bytes = done = 0
    for name in pre.index_parts:
        for _line, raw in _iter_raw_lines(zf, name, max_line=_INDEX_LINE_MAX):
            obj = _index_line(raw)
            if obj is None:
                raise _tampered("an object index line is not valid")
            done += 1
            root = roots.get(as_uuid(obj["run_id"]) or uuid.UUID(int=0))
            key = object_key(root, obj["suffix"]) if root else None       # the suffix RE-CHECKED here
            if key is None or not key.startswith(project_root):
                if skipped is not None:
                    skipped["objects"] = skipped.get("objects", 0) + 1
                continue
            reader = _HashingReader(zf, obj["member"])
            try:
                await storage.put_object_stream(
                    key, reader, size=int(obj["bytes"]), content_type=obj["content_type"] or _DEFAULT_CONTENT_TYPE,
                )
            except Exception as exc:  # noqa: BLE001 - only the error TYPE reaches the job row
                raise _fail("object_unreadable", error_type=type(exc).__name__) from None
            finally:
                reader.close()
            if reader.size != obj["bytes"] or reader.digest.hexdigest() != obj["sha256"]:
                raise _fail("object_unreadable", error_type="DigestMismatch")
            written += 1
            written_bytes += reader.size
            if on_progress is not None and done % 100 == 0:
                await on_progress("objects", done, pre.objects)
    if on_progress is not None:
        await on_progress("objects", done, pre.objects)
    _slog.info("project_import_objects_written", project_id=str(new_project_id), objects=written, bytes=written_bytes)
    return written, written_bytes


# ── I-H9: three stores, one outcome ──────────────────────────────────────────


def _ledger_ids(ledger: Mapping[str, Any], key: str) -> list[str]:
    values = ledger.get(key) or []
    return [str(u) for u in (as_uuid(v) for v in values if isinstance(v, (str, uuid.UUID))) if u is not None]


async def cleanup_outside_postgres(mongo: Any, storage: Any, ledger: Mapping[str, Any]) -> dict[str, int]:
    """Remove everything an import wrote outside Postgres, by its LEDGER only — idempotent (a second call
    deletes 0). Documents: per ``MongoSpec`` ``delete_many({field: {"$in": ids}})`` where field / ids are the
    run key and the ledger's NEW run ids (``ai_analysis_payloads`` by its added ``test_run_id``), or the
    pipeline key and the NEW pipeline ids. Objects: ``delete_prefix("{ledger project_id}/")`` — a root only
    this import's new project can own. Returns ``{collection: deleted, "objects": deleted}``."""
    from app.services.project_export_documents import MONGO_COLLECTIONS

    project_id = as_uuid(ledger.get("project_id"))
    if project_id is None:
        raise ValueError("a cleanup ledger needs the new project's id")
    by_map = {"test_runs": _ledger_ids(ledger, "run_ids"), "agent_pipeline_runs": _ledger_ids(ledger, "pipeline_ids")}
    removed: dict[str, int] = {}
    for spec in MONGO_COLLECTIONS:
        if spec.key_map == "test_cases":
            field, ids = "test_run_id", by_map["test_runs"]       # the one field an import adds (§2.2 M-7)
        else:
            field, ids = spec.key, by_map.get(spec.key_map, [])
        count = 0
        for start in range(0, len(ids), _DELETE_CHUNK):
            result = await mongo[spec.collection].delete_many({field: {"$in": ids[start:start + _DELETE_CHUNK]}})
            count += int(getattr(result, "deleted_count", 0) or 0)
        removed[spec.collection] = count
    removed["objects"] = int(await storage.delete_prefix(f"{project_id}/"))
    _slog.info("project_import_cleanup_outside_postgres", project_id=str(project_id),
               documents=sum(v for k, v in removed.items() if k != "objects"), objects=removed["objects"])
    return removed
