"""Which MongoDB documents travel with run history, and how their ids are rewritten (W30 ADM-05 phase 2).

The ten run-scoped collections are exactly the retention purge's plan
(``retention_service._MONGO_PLANS``): the purge and the export agree on what a
run owns. Each collection has ONE :class:`MongoSpec` naming the field the export
selects by (``key``), the remap that key goes through on import (``key_map``),
every other top-level id field the product WRITES (``id_fields`` → a remap,
``project_field`` → the new project, ``run_fields`` → the new run id, or
``kept_ids`` — ids of the document itself, kept verbatim), the one field an
import ADDS (``added_fields``) and the collection's unique index
(``unique_key``). ``tests/test_project_export_documents.py`` keeps this complete:
every ``Collections.*`` member and every ``_MONGO_PLANS`` entry is classified
here, and every top-level ``*_id`` field a writer sets is named.

Pure: imports ``app.db.mongo.Collections``, ``bson`` and the vocabulary only —
no session, no client, no I/O (``STAGE_ONLY_SERVICES``). Ids nested INSIDE a
document body stay source ids (history text; every read is scoped by the copy's
own run / case / project id first — contract R-6).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Union

from bson import ObjectId, json_util
from bson.json_util import RELAXED_JSON_OPTIONS

from app.db.mongo import Collections
from app.services.project_export_vocab import MONGO_DOC_MAX_BYTES, MONGO_MAX_DEPTH

#: == ``retention_service._PIPELINE_EVENT_LOG`` (asserted) — not a ``Collections`` member; the
#: literal is repeated here so this pure module never imports the purge.
PIPELINE_EVENT_LOG = "pipeline_event_log"

#: ``validate_document`` reason codes → the short text the ``invalid_document`` sentence may carry.
DOCUMENT_REASONS: Mapping[str, str] = {
    "not_an_object": "not a JSON object",
    "operator_key": "a key starting with $",
    "dotted_key": "a key containing a dot or NUL",
    "too_deep": f"nested deeper than {MONGO_MAX_DEPTH}",
    "too_large": "larger than 4 MB",
    "missing_key": "its run / case / pipeline key is missing or malformed",
    "id_present": "an _id field",
    "duplicate_key": "a repeated unique key",
    "unsupported_type": "a stored type the archive does not carry",
}


@dataclass(frozen=True)
class MongoSpec:
    collection: str                                              # a Collections.* value or PIPELINE_EVENT_LOG
    key: str                                                     # the field the export selects by (== the purge's)
    key_map: str                                                 # the remap the key goes through: test_runs | test_cases | agent_pipeline_runs
    id_fields: Mapping[str, str] = field(default_factory=dict, hash=False)   # other top-level id fields → remap (unresolved → None)
    project_field: str | None = None                             # a field carrying the project id → the NEW project id
    added_fields: Mapping[str, str] = field(default_factory=dict, hash=False)  # fields the IMPORT adds → "test_runs" = the NEW run id
    unique_key: tuple[str, ...] = ()                             # the collection's unique index (app/db/mongo.ensure_indexes)
    run_fields: tuple[str, ...] = ()                             # fields OVERWRITTEN with the NEW run id string (whatever their value)
    slug_key: bool = False                                       # the key may be a live-session SLUG (canonical_run_uuid resolves it)
    kept_ids: tuple[str, ...] = ()                               # top-level *_id fields of the document ITSELF, kept verbatim


_RUNS, _CASES, _PIPELINES = "test_runs", "test_cases", "agent_pipeline_runs"

#: The ten run-scoped collections, in export order (contract §2.2 M-1 … M-10).
MONGO_COLLECTIONS: tuple[MongoSpec, ...] = (
    # M-1: test_case_id here is the ALLURE result uuid (ingestion._store_raw_allure: allure_uuid), not a
    # TestCase id — it equals test_cases.source_uuid, which travels byte-exact, so it is kept verbatim
    MongoSpec(Collections.RAW_ALLURE_JSON, key="test_run_id", key_map=_RUNS, kept_ids=("test_case_id",)),
    # M-2: the HTTP exchanges (headers redacted at write); run_created_at kept (== the kept run created_at)
    MongoSpec(Collections.REST_API_PAYLOADS, key="test_run_id", key_map=_RUNS,
              id_fields={"test_case_id": _CASES}, project_field="project_id"),
    # M-3: run_id is a TestRun uuid string OR a live-session slug; session_id → the NEW run id (a source slug
    # kept verbatim would let a read by session join the SOURCE's documents); run_start events carry project_id
    MongoSpec(Collections.LIVE_EXECUTION_EVENTS, key="run_id", key_map=_RUNS, project_field="project_id",
              run_fields=("session_id",), slug_key=True),
    MongoSpec(Collections.RUN_SUMMARIES, key="test_run_id", key_map=_RUNS, project_field="project_id",
              unique_key=("test_run_id",)),
    MongoSpec(Collections.AI_RUN_VERSIONS, key="test_run_id", key_map=_RUNS, id_fields={"pipeline_run_id": _PIPELINES},
              project_field="project_id", unique_key=("test_run_id", "kind", "version"), kept_ids=("version_id",)),
    MongoSpec(Collections.DECISION_EVIDENCE_SNAPSHOTS, key="test_run_id", key_map=_RUNS,
              id_fields={"pipeline_run_id": _PIPELINES}, project_field="project_id",
              unique_key=("project_id", "test_run_id", "pipeline_run_id")),
    # M-7: keyed by case only; the import ADDS test_run_id (the new run) so the copy is removable by run
    MongoSpec(Collections.AI_ANALYSIS_PAYLOADS, key="test_case_id", key_map=_CASES, added_fields={"test_run_id": _RUNS}),
    # M-8: the writer (pipeline_event_log.emit_event) sets pipeline_run_id + test_case_id (open point 4)
    MongoSpec(PIPELINE_EVENT_LOG, key="pipeline_run_id", key_map=_PIPELINES, id_fields={"test_case_id": _CASES}),
    MongoSpec(Collections.DECISION_REPORTS, key="test_run_id", key_map=_RUNS, id_fields={"pipeline_run_id": _PIPELINES},
              project_field="project_id", unique_key=("test_run_id", "report_version"),
              kept_ids=("report_id", "supersedes_report_id", "evidence_snapshot_id")),
    MongoSpec(Collections.DECISION_REPORT_ATTEMPTS, key="test_run_id", key_map=_RUNS,
              id_fields={"pipeline_run_id": _PIPELINES}, project_field="project_id",
              kept_ids=("attempt_id", "supersedes_report_id")),
)
MONGO_SPECS: Mapping[str, MongoSpec] = {spec.collection: spec for spec in MONGO_COLLECTIONS}

#: Every other ``Collections.*`` member → why it does not travel (none today; the guard keeps it complete).
MONGO_EXCLUDED: Mapping[str, str] = {}

Remap = Union[Mapping[str, str], Callable[[str], Union[str, None]]]
_SCALARS = (str, int, float, bool, type(None), datetime, ObjectId)
_SLUG_MAX = 255
#: Relaxed Extended JSON; a decoded ``{"$date": …}`` is a tz-aware UTC datetime (the stored instant).
DOCUMENT_JSON_OPTIONS = RELAXED_JSON_OPTIONS.with_options(tz_aware=True, tzinfo=timezone.utc)


class UnsupportedDocumentType(Exception):
    """A stored document holds a BSON type the archive does not carry (Binary, Decimal128, Regex, Code …)."""


def canonical_run_uuid(value: str) -> uuid.UUID:
    """== ``stream_service.canonical_test_run_uuid`` (asserted): a run uuid string, or a live-session slug
    mapped through ``uuid5(NAMESPACE_DNS, slug)`` — repeated here so this module never imports ingest code."""
    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_DNS, value)


def _check_types(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _check_types(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_types(item)
    elif not isinstance(value, _SCALARS):
        raise UnsupportedDocumentType(type(value).__name__)


def encode_document(doc: Mapping[str, Any]) -> str:
    """One stored document (already scrubbed, ``_id`` removed) as ONE relaxed Extended JSON line, keys
    sorted: a ``datetime`` round-trips as ``{"$date": …}``, an ``ObjectId`` value as ``{"$oid": …}``."""
    _check_types(doc)
    return json_util.dumps(doc, json_options=DOCUMENT_JSON_OPTIONS, sort_keys=True)


def decode_document(line: str | bytes) -> Any:
    """The inverse of :func:`encode_document` (Extended-JSON wrappers become values FIRST, so an operator
    key left after decoding can only be a forged one)."""
    return json_util.loads(line, json_options=DOCUMENT_JSON_OPTIONS)


def _shape_problem(value: Any, depth: int) -> str | None:
    if depth > MONGO_MAX_DEPTH:
        return "too_deep"
    items: list[Any] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.startswith("$"):
                return "operator_key"
            if "." in key or "\x00" in key:
                return "dotted_key"
            items.append(item)
    elif isinstance(value, list):
        items = value
    for item in items:
        if isinstance(item, (dict, list)):
            problem = _shape_problem(item, depth + 1)
            if problem:
                return problem
    return None


def _key_ok(spec: MongoSpec, value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if spec.slug_key:
        return len(value) <= _SLUG_MAX and value.isprintable()
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def validate_document(doc: Any, spec: MongoSpec | None = None, *, encoded_size: int | None = None) -> str | None:
    """None = valid; else a ``DOCUMENT_REASONS`` code. Checked BEFORE any write (export or import): a JSON
    object at the top, no ``_id``, no ``$`` / dotted / NUL key at any depth, depth ≤ ``MONGO_MAX_DEPTH``, the
    encoded line ≤ ``MONGO_DOC_MAX_BYTES`` (``encoded_size`` = the line's length when the caller has it), and
    (given a spec) its key field present and well formed (a uuid string; a slug allowed for a slug key)."""
    if not isinstance(doc, dict):
        return "not_an_object"
    if "_id" in doc:
        return "id_present"
    problem = _shape_problem(doc, 1)
    if problem:
        return problem
    if encoded_size is None:
        try:
            encoded_size = len(encode_document(doc).encode("utf-8"))
        except UnsupportedDocumentType:
            return "unsupported_type"
    if encoded_size > MONGO_DOC_MAX_BYTES:
        return "too_large"
    if spec is not None and not _key_ok(spec, doc.get(spec.key)):
        return "missing_key"
    return None


def unique_value(spec: MongoSpec, doc: Mapping[str, Any]) -> tuple[str, ...] | None:
    """The document's ``unique_key`` values, canonically encoded (None when the collection has none) — the
    import keeps a set per collection and refuses a repeat inside the archive (``duplicate_key``)."""
    if not spec.unique_key:
        return None
    return tuple(json_util.dumps(doc.get(name), sort_keys=True) for name in spec.unique_key)


def _resolve(remaps: Mapping[str, Remap], table: str, source_id: str) -> str | None:
    remap = remaps.get(table)
    if remap is None:
        return None
    if callable(remap):
        return remap(source_id)
    return remap.get(source_id)


def rewrite_document(
    spec: MongoSpec, doc: Mapping[str, Any], *, remaps: Mapping[str, Remap], new_project_id: str,
    new_run_id: str | None = None,
) -> dict[str, Any] | None:
    """The document as the COPY stores it, or None when its key does not resolve (skipped, counted).

    ``remaps``: table → a source-id → new-id mapping or function (None / absent = unresolved). The key goes
    through ``key_map`` (a slug key through :func:`canonical_run_uuid` first); every present ``id_fields``
    value through its remap (unresolved → None); ``project_field`` → ``new_project_id``; ``run_fields`` and
    ``added_fields`` → the new run id (the resolved key, or ``new_run_id`` for a case-keyed collection).
    ``_id`` is never set: the database assigns a fresh one. A top-level copy; nested values are shared."""
    raw = doc.get(spec.key)
    if not isinstance(raw, str) or not raw:
        return None
    source_key = str(canonical_run_uuid(raw)) if spec.slug_key else raw
    resolved = _resolve(remaps, spec.key_map, source_key)
    if resolved is None:
        return None
    out = {name: value for name, value in doc.items() if name != "_id"}
    out[spec.key] = resolved
    for name, table in spec.id_fields.items():
        value = out.get(name)
        if value is not None:
            out[name] = _resolve(remaps, table, str(value))
    if spec.project_field is not None and spec.project_field in out:
        out[spec.project_field] = new_project_id
    run_id = resolved if spec.key_map == _RUNS else new_run_id
    if (spec.run_fields or spec.added_fields) and run_id is None:
        raise ValueError(f"{spec.collection}: new_run_id is required to rewrite this collection")
    for name in spec.run_fields:
        if name in out:
            out[name] = run_id
    for name in spec.added_fields:
        out[name] = run_id
    return out
