"""The run history of a project export (W30 ADM-05 phase 2, contract §2.4) — stage-only, READ-ONLY.

Everything here reads the export's ONE read-only ``REPEATABLE READ`` snapshot
(the runner's session), the Mongo database (``find`` / ``count_documents``
only) and the object store (``list_objects`` / ``stream_object`` only):

* :func:`run_window` — ONE ``SELECT`` of the window's exported run ids (status ∈
  ``HISTORY_RUN_STATUSES``, ``created_at`` in the window, newest first, ≤
  ``max_runs``) into a list of ≤ ``HISTORY_MAX_RUNS`` UUIDs + one aggregate of the
  skipped counts. Every history ``SELECT`` is scoped by ``= ANY(:run_window)``
  (one bound array) or by a SUBQUERY on an exported parent — never a Python list
  of case ids.
* :func:`rows_statement` — a schema-2 entity's rows: a catalog entity by its
  phase-1 scope, a history entity by its ``window`` (+ ``select_where``); every
  ``null_unless`` column as ``CASE WHEN col IN (<exported ids>) THEN col END``.
* :func:`iter_history_rows` — phase 1's part writer over that statement.
* :func:`iter_documents` — per ``MongoSpec``: the keys in ``$in`` chunks of
  ``MONGO_BATCH_DOCS`` → ``find(...).sort("_id", 1).batch_size(...)`` ITERATED
  (never ``to_list``) → ``redact_tree`` + the text pass → relaxed Extended JSON.
* :func:`plan_objects` / :func:`iter_objects` — the object listing ONCE, the
  run's ``minio_prefix`` range only (R-1), then one ``StreamedMember`` per object
  (≤ ``OBJECT_MAX_BYTES``; ZIP_STORED for media) and the ``objects/index`` parts.
* :func:`precheck` — refuses ``history_too_large`` BEFORE the first member.
* :func:`estimate` — counts only (the estimate GET): a store that cannot be read
  answers ``measured: false`` and NULL counts — never 0.

Stage-only (``STAGE_ONLY_SERVICES``): no commit, no INSERT / UPDATE / DELETE. Logs
carry job / project ids, entity / collection names, counts, byte totals and error
TYPE names — never a row or document value, a storage key, a name or a digest.
"""
from __future__ import annotations

import bisect
import hashlib
import mimetypes
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Optional

import structlog
from sqlalchemy import and_, any_, bindparam, case, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.services import project_export_vocab as vocab

_slog = structlog.get_logger(__name__)

#: The estimate's per-item sizes (compressed, in the archive) — an ESTIMATE, documented as one.
ROW_BYTES_ESTIMATE = 600
DOCUMENT_BYTES_ESTIMATE = 1_200
#: Members besides the objects that the precheck reserves (entity / document / index parts, directory, README).
MEMBER_MARGIN = 64
#: Progress is written after each this many documents / objects (on a FRESH session, by the caller).
DOCUMENT_PROGRESS_EVERY = 5_000
OBJECT_PROGRESS_EVERY = 100
#: The objects/index ``root`` of every copied object (``project_export_objects.ROOT_MINIO_PREFIX``, asserted).
OBJECT_ROOT = "minio_prefix"

#: ``(phase, done, total)`` → the caller writes ``progress.phase*`` on a fresh short session.
OnPhase = Callable[[str, int, int], Awaitable[None]]


@dataclass
class RunWindow:
    """The runs a history export writes: ≤ ``max_runs`` ids, newest first (the only id list it holds)."""

    project_id: uuid.UUID
    run_ids: list[uuid.UUID]
    days: Optional[int]
    max_runs: int
    since: Optional[datetime] = None
    in_progress: int = 0            # IN_PROGRESS runs in the window — never exported
    over_cap: int = 0               # window runs beyond max_runs — not exported
    oldest_run_at: Optional[datetime] = None
    newest_run_at: Optional[datetime] = None
    release_id: Optional[uuid.UUID] = None   # W37 F-W30-1: the runs linked to this release (no day window)
    release_name: Optional[str] = None
    _param: Any = field(default=None, repr=False)

    @property
    def id_set(self) -> frozenset[uuid.UUID]:
        return frozenset(self.run_ids)

    @property
    def param(self) -> Any:
        """The ONE bound array every history statement compares with (``= ANY(:run_window)``)."""
        if self._param is None:
            self._param = bindparam("run_window", value=list(self.run_ids), type_=ARRAY(PG_UUID(as_uuid=True)))
        return self._param


def _table(name: str) -> Any:
    from app.models.postgres import Base

    return Base.metadata.tables[name]


def _option(options: Any, name: str, default: Any) -> Any:
    if options is None:
        return default
    if isinstance(options, Mapping):
        return options.get(name, default)
    return getattr(options, name, default)


def release_of(options: Any) -> Optional[uuid.UUID]:
    """W37 F-W30-1: the ``history_release_id`` of ``options`` (None = a day window)."""
    value = _option(options, "history_release_id", None)
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def window_bounds(options: Any, *, now: datetime) -> tuple[Optional[int], int, Optional[datetime]]:
    """``(history_days, max_runs, since)`` of ``options`` (``history_days`` None = all history). W37 D-2: a
    release scope REPLACES the day window — no time cut (``(None, max_runs, None)``)."""
    days = _option(options, "history_days", vocab.HISTORY_DEFAULT_DAYS)
    if release_of(options) is not None:
        days = None
    max_runs = _option(options, "max_runs", None) or vocab.HISTORY_MAX_RUNS
    max_runs = max(1, min(int(max_runs), vocab.HISTORY_MAX_RUNS))
    since = now - timedelta(days=int(days)) if days is not None else None
    return (int(days) if days is not None else None), max_runs, since


def _window_filter(
    runs: Any, project_id: uuid.UUID, since: Optional[datetime], release_id: Optional[uuid.UUID] = None,
) -> list[Any]:
    """``project_id = :source`` [+ ``id IN (the release's linked runs)``] [+ ``created_at >= :since``]. W37 D-2: a
    release's runs are every ``release_test_run_links`` row of it (primary AND secondary) — a SUBQUERY, never a
    JOIN (a run linked twice is one run); the link's own ``project_id`` AND the run's are both the source (a
    historical cross-project link row names nothing here)."""
    clauses = [runs.c.project_id == project_id]
    if release_id is not None:
        links = _table("release_test_run_links")
        clauses.append(runs.c.id.in_(
            select(links.c.test_run_id).where(links.c.release_id == release_id, links.c.project_id == project_id)
        ))
    if since is not None:
        clauses.append(runs.c.created_at >= since)
    return clauses


def window_statement(
    project_id: uuid.UUID, *, since: Optional[datetime], max_runs: int, release_id: Optional[uuid.UUID] = None,
) -> Any:
    """``SELECT id, created_at FROM test_runs WHERE project_id = :source [AND id IN (SELECT
    release_test_run_links.test_run_id ... WHERE release_id = :release AND project_id = :source)] AND status IN
    :HISTORY_RUN_STATUSES [AND created_at >= :since] ORDER BY created_at DESC, id DESC LIMIT :max_runs`` — the
    ONE window statement."""
    runs = _table("test_runs")
    return (
        select(runs.c.id, runs.c.created_at)
        .where(*_window_filter(runs, project_id, since, release_id), runs.c.status.in_(vocab.HISTORY_RUN_STATUSES))
        .order_by(runs.c.created_at.desc(), runs.c.id.desc())
        .limit(max_runs)
    )


def window_counts_statement(
    project_id: uuid.UUID, *, since: Optional[datetime], release_id: Optional[uuid.UUID] = None,
) -> Any:
    """The window's run counts in ONE aggregate: exportable (terminal) and in progress (the same filter)."""
    runs = _table("test_runs")
    terminal = func.count().filter(runs.c.status.in_(vocab.HISTORY_RUN_STATUSES))
    in_progress = func.count().filter(runs.c.status.not_in(vocab.HISTORY_RUN_STATUSES))
    return select(terminal, in_progress).where(*_window_filter(runs, project_id, since, release_id))


def release_name_statement(project_id: uuid.UUID, release_id: uuid.UUID) -> Any:
    """W37: the scoped release's name for the manifest — the path project's release only."""
    releases = _table("releases")
    return select(releases.c.name).where(releases.c.id == release_id, releases.c.project_id == project_id)


async def run_window(db: Any, *, project_id: uuid.UUID, options: Any, now: Optional[datetime] = None) -> RunWindow:
    """The window's run ids (≤ ``max_runs``, newest first) and its skipped counts, on the caller's snapshot."""
    at = now or datetime.now(timezone.utc)
    days, max_runs, since = window_bounds(options, now=at)
    release_id = release_of(options)
    rows = (await db.execute(window_statement(project_id, since=since, max_runs=max_runs, release_id=release_id))).all()
    counts = (await db.execute(window_counts_statement(project_id, since=since, release_id=release_id))).one()
    terminal, in_progress = int(counts[0] or 0), int(counts[1] or 0)
    window = RunWindow(
        project_id=project_id, run_ids=[row[0] for row in rows], days=days, max_runs=max_runs, since=since,
        in_progress=in_progress, over_cap=max(0, terminal - len(rows)), release_id=release_id,
    )
    if release_id is not None:
        window.release_name = (await db.execute(release_name_statement(project_id, release_id))).scalar()
    stamps = [row[1] for row in rows if row[1] is not None]
    if stamps:
        window.oldest_run_at, window.newest_run_at = min(stamps), max(stamps)
    return window


# ── rows (schema 2) ──────────────────────────────────────────────────────────

def _predicate(spec: Any, table: Any) -> Any:
    from app.services.project_export_entities import HISTORY_PREDICATES

    column, allowed = HISTORY_PREDICATES[spec.select_where]
    return table.c[column].in_(allowed)


def exported_ids(table_name: str, window: RunWindow) -> Any:
    """What this export writes of ``table_name``, as a SELECT of ids (``test_runs``: the bound window array):
    a history entity through its window, a catalog entity through its phase-1 scope."""
    from app.services.project_export_entities import spec_for
    from app.services.project_export_service import scope_clause

    table = _table(table_name)
    spec = spec_for(table_name)
    if getattr(spec, "history", False):
        return select(table.c.id).where(window_clause(spec, window))
    return select(table.c.id).where(scope_clause(spec, window.project_id))


def _points_into(column: Any, target: str, window: RunWindow) -> Any:
    if target == "test_runs":
        return column == any_(window.param)
    return column.in_(exported_ids(target, window))


def window_clause(spec: Any, window: RunWindow) -> Any:
    """The WHERE of a history entity's rows (contract §2.1 windows) + its ``select_where`` predicate."""
    table = _table(spec.table)
    parts = spec.window_parts
    if parts is None:
        raise ValueError(f"{spec.table} has no history window")
    kind, column = parts
    if kind == "runs":
        clause = table.c.id == any_(window.param)
    elif kind == "project":
        clause = table.c.project_id == window.project_id
    elif kind == "run":
        clause = table.c[column] == any_(window.param)
    elif kind == "case":
        clause = table.c[column].in_(exported_ids("test_cases", window))
    elif kind == "pipeline":
        clause = table.c[column].in_(exported_ids("agent_pipeline_runs", window))
    elif kind == "release":
        clause = table.c[column].in_(exported_ids("releases", window))
    elif kind == "canonical":
        clause = table.c[column].in_(exported_ids("canonical_test_cases", window))
    else:
        raise ValueError(f"{spec.table}: unknown history window kind {kind!r}")
    if getattr(spec, "select_where", None):
        clause = and_(clause, _predicate(spec, table))
    return clause


def _uuid_text(value: Any) -> Optional[str]:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def filter_run_ids(value: Any, keep: frozenset[str]) -> tuple[Any, int]:
    """A ``json_id_paths`` value pointing at ``test_runs`` (a LIST of run ids — ``release_gate_decisions.run_ids``
    — or a DICT keyed by run id — production's ``incomplete_runs`` ``{run_id: rejected}``) with every run outside
    the archive removed, and how many were. The export knows its run window exactly, so a source run that does
    not travel is never named inside a copied row (the ``null_unless`` rule, applied to id lists)."""
    if isinstance(value, list):
        kept = [item for item in value if _uuid_text(item) in keep]
        return kept, len(value) - len(kept)
    if isinstance(value, dict):
        kept_map = {key: item for key, item in value.items() if _uuid_text(key) in keep}
        return kept_map, len(value) - len(kept_map)
    return value, 0


def row_hook(spec: Any, window: RunWindow, row_skips: dict[tuple[str, str], int]) -> Optional[Callable[[dict], None]]:
    """The per-row adjustment of a history export (None when ``spec`` needs none): the ``json_id_paths`` into
    ``test_runs`` filtered to the window (counted ``run_not_exported``), an ``evidence_artifacts.uri_or_ref`` into
    pipeline scratch counted (the scrub writes it NULL)."""
    from app.services.project_export_scrub import is_pipeline_scratch_ref

    run_paths = [column for column, target in dict(getattr(spec, "json_id_paths", None) or {}).items()
                 if target == "test_runs"]
    scratch = spec.table == "evidence_artifacts"
    if not run_paths and not scratch:
        return None
    keep = frozenset(str(run_id) for run_id in window.run_ids)

    def hook(raw: dict) -> None:
        if scratch and is_pipeline_scratch_ref(raw.get("uri_or_ref")):
            row_skips[("evidence_artifacts", "pipeline_scratch")] = row_skips.get(
                ("evidence_artifacts", "pipeline_scratch"), 0) + 1
        for column in run_paths:
            filtered, removed = filter_run_ids(raw.get(column), keep)
            if removed:
                raw[column] = filtered
                row_skips[(spec.table, "run_not_exported")] = row_skips.get((spec.table, "run_not_exported"), 0) + removed

    return hook


def columns_of(spec: Any) -> tuple[str, ...]:
    """The columns of ``spec`` in an export WITH history (schema 3, W37 D-1: phase 1's + C-1's ``history_fks``
    + the ``schema3_columns``), table order."""
    from app.services.project_export_entities import export_schema_version, exported_columns

    return exported_columns(spec, schema_version=export_schema_version(include_history=True), history=True)


def rows_statement(spec: Any, window: RunWindow) -> Any:
    """ONE streamed ``SELECT`` of a schema-2 entity's rows, ordered by the primary key: a catalog entity by its
    phase-1 scope, a history entity by its window; every ``null_unless`` column written NULL in SQL when its
    target row is outside the archive (``CASE WHEN col IN (<exported ids>) THEN col END``)."""
    from app.services.project_export_service import scope_clause

    table = _table(spec.table)
    null_unless = dict(getattr(spec, "null_unless", None) or {})
    columns = []
    for name in columns_of(spec):
        column = table.c[name]
        target = null_unless.get(name)
        if target is not None:
            columns.append(case((_points_into(column, target, window), column), else_=None).label(name))
        else:
            columns.append(column)
    where = window_clause(spec, window) if getattr(spec, "history", False) else scope_clause(spec, window.project_id)
    return (
        select(*columns)
        .where(where)
        .order_by(*table.primary_key.columns)
        .execution_options(yield_per=vocab.CURSOR_BATCH_ROWS)
    )


def count_statement(spec: Any, window: RunWindow) -> Any:
    """``count(*)`` of what :func:`rows_statement` would write (the estimate)."""
    from app.services.project_export_service import scope_clause

    table = _table(spec.table)
    where = window_clause(spec, window) if getattr(spec, "history", False) else scope_clause(spec, window.project_id)
    return select(func.count()).select_from(table).where(where)


def skipped_statements(window: RunWindow) -> dict[tuple[str, str], Any]:
    """What a window run carries that the export leaves out, counted for the manifest: a non-terminal pipeline
    (``pipeline_not_terminal``) and its stage results."""
    from app.services.project_export_entities import TERMINAL_PIPELINE_STATUSES

    pipes = _table("agent_pipeline_runs")
    stages = _table("agent_stage_results")
    live = and_(pipes.c.test_run_id == any_(window.param), pipes.c.status.not_in(TERMINAL_PIPELINE_STATUSES))
    return {
        ("agent_pipeline_runs", "pipeline_not_terminal"): select(func.count()).select_from(pipes).where(live),
        ("agent_stage_results", "pipeline_not_terminal"): select(func.count()).select_from(stages).where(
            stages.c.pipeline_run_id.in_(select(pipes.c.id).where(live))
        ),
    }


async def count_skipped(db: Any, window: RunWindow) -> list[dict[str, Any]]:
    out = []
    for (entity, reason), statement in skipped_statements(window).items():
        count = int((await db.execute(statement)).scalar() or 0)
        if count:
            out.append({"entity": entity, "reason": reason, "count": count})
    return out


async def iter_history_rows(
    db: Any, *, spec: Any, window: RunWindow, stats: dict[str, Any], referenced: Optional[set[Any]] = None,
) -> AsyncGenerator[tuple[str, bytes], None]:
    """Phase 1's part writer over :func:`rows_statement` (``stats["entities"][table]`` filled when done)."""
    from app.services.project_export_service import entity_parts

    users = referenced if referenced is not None else stats.setdefault("user_ids", set())
    async for member in entity_parts(
        db, spec, rows_statement(spec, window), columns_of(spec),
        entities=stats.setdefault("entities", {}), referenced=users,
    ):
        yield member


# ── parts ────────────────────────────────────────────────────────────────────

class PartWriter:
    """JSONL lines → parts of ≤ ``PART_ROWS_MAX`` lines / ≤ ``PART_BYTES_MAX`` bytes (a larger line sits alone)."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.parts = 0
        self.lines = 0
        self._buffer: list[bytes] = []
        self._size = 0

    def _emit(self) -> tuple[str, bytes]:
        self.parts += 1
        member = (f"{self.prefix}part-{self.parts:04d}.jsonl", b"".join(self._buffer))
        self._buffer, self._size = [], 0
        return member

    def add(self, line: bytes) -> list[tuple[str, bytes]]:
        out = []
        if self._buffer and self._size + len(line) > vocab.PART_BYTES_MAX:
            out.append(self._emit())
        self._buffer.append(line)
        self._size += len(line)
        self.lines += 1
        if len(self._buffer) >= vocab.PART_ROWS_MAX:
            out.append(self._emit())
        return out

    def flush(self) -> list[tuple[str, bytes]]:
        return [self._emit()] if self._buffer else []


# ── documents (S-9) ──────────────────────────────────────────────────────────

def _is_uuid_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _id_field_names(spec: Any) -> list[str]:
    names = [*dict(getattr(spec, "id_fields", None) or {}), *tuple(getattr(spec, "kept_ids", None) or ()),
             *tuple(getattr(spec, "run_fields", None) or ())]
    if getattr(spec, "project_field", None):
        names.append(spec.project_field)
    return names


def encode_document(spec: Any, doc: Mapping[str, Any], *, slugs: Optional[Mapping[str, str]] = None) -> bytes:
    """One stored document as ONE relaxed Extended JSON line (with its ``\\n``, G-A's
    ``project_export_documents.encode_document``): ``_id`` removed, then ``scrub_document`` (S-9: ``redact_tree``
    + the phase-1 text / key pass). The fields the import rewrites or keys by — the spec's key and the
    uuid-shaped ``id_fields`` / ``kept_ids`` / ``run_fields`` / ``project_field`` — are ids, never secrets: they
    are restored after the scrub so a scrubber can never break a document's join. A live event keyed by a
    session SLUG (``slugs``: slug → canonical run id) is written under its run's UUID string — the archive's key
    is always a run of the archive (G-C's import refuses a non-UUID key; G-A's validator accepts either). A BSON
    type the archive does not carry raises ``UnsupportedDocumentType``."""
    from app.services.project_export_documents import encode_document as encode_line
    from app.services.project_export_scrub import scrub_document

    body = {name: value for name, value in doc.items() if name != "_id"}
    scrubbed = scrub_document(body)
    key = body.get(spec.key)
    if isinstance(key, str):
        scrubbed[spec.key] = (slugs or {}).get(key, key) if getattr(spec, "slug_key", False) else key
    for name in _id_field_names(spec):
        if _is_uuid_text(body.get(name)):
            scrubbed[name] = body[name]
    return encode_line(scrubbed).encode("utf-8") + b"\n"


async def _stream_ids(db: Any, statement: Any) -> AsyncIterator[list[str]]:
    """Ids of ``statement`` (one column) from the snapshot, as text, in chunks of ``MONGO_BATCH_DOCS``."""
    result = await db.stream(statement.execution_options(yield_per=vocab.MONGO_BATCH_DOCS))
    try:
        chunk: list[str] = []
        async for row in result:
            chunk.append(str(row[0]))
            if len(chunk) >= vocab.MONGO_BATCH_DOCS:
                yield chunk
                chunk = []
        if chunk:
            yield chunk
    finally:
        await result.close()


async def live_session_keys(db: Any, window: RunWindow) -> dict[str, str]:
    """``{session slug: canonical run id}`` of the source's live sessions whose run is in the window (read from
    the EXCLUDED ``live_sessions`` rows, read-only): a live event is keyed by the CLIENT's run id (G-A's
    ``canonical_run_uuid`` == ``stream_service.canonical_test_run_uuid``, asserted by G-A's guard)."""
    from app.services.project_export_documents import canonical_run_uuid as canonical_test_run_uuid

    sessions = _table("live_sessions")
    ids = window.id_set
    out: dict[str, str] = {}
    result = await db.stream(
        select(sessions.c.run_id).where(sessions.c.project_id == window.project_id)
        .execution_options(yield_per=vocab.CURSOR_BATCH_ROWS)
    )
    try:
        async for row in result:
            slug = row[0]
            if not slug:
                continue
            run_id = canonical_test_run_uuid(str(slug))
            if run_id in ids and str(slug) != str(run_id):
                out[str(slug)] = str(run_id)
    finally:
        await result.close()
    return out


async def document_key_chunks(
    db: Any, spec: Any, window: RunWindow, *, slugs: Mapping[str, str],
) -> AsyncIterator[list[str]]:
    """The ``$in`` chunks of ``spec.key`` values (≤ ``MONGO_BATCH_DOCS`` each): window run ids (+ the live-session
    slugs of window runs for a ``run_id`` key), a window run's case ids, or an exported pipeline's id — the
    case / pipeline ids streamed from the snapshot, never held as one list."""
    size = vocab.MONGO_BATCH_DOCS
    if spec.key_map == "test_runs":
        keys = [str(run_id) for run_id in window.run_ids]
        if getattr(spec, "slug_key", False):
            keys += sorted(slugs)
        for start in range(0, len(keys), size):
            yield keys[start:start + size]
    elif spec.key_map in ("test_cases", "agent_pipeline_runs"):
        table = _table(spec.key_map)
        async for chunk in _stream_ids(db, exported_ids(spec.key_map, window).order_by(table.c.id)):
            yield chunk
    else:
        raise ValueError(f"{spec.collection}: unknown key map {spec.key_map!r}")


def _mongo_specs() -> tuple[Any, ...]:
    from app.services.project_export_documents import MONGO_COLLECTIONS

    return tuple(MONGO_COLLECTIONS)


async def count_documents(
    db: Any, mongo: Any, window: RunWindow, *, slugs: Optional[Mapping[str, str]] = None,
) -> dict[str, int]:
    """``{collection: documents}`` the export would write (``count_documents`` per ``$in`` chunk)."""
    if slugs is None:
        slugs = await live_session_keys(db, window)
    out: dict[str, int] = {}
    for spec in _mongo_specs():
        total = 0
        async for keys in document_key_chunks(db, spec, window, slugs=slugs):
            total += int(await mongo[spec.collection].count_documents({spec.key: {"$in": keys}}))
        out[spec.collection] = total
    return out


async def iter_documents(
    db: Any, mongo: Any, *, window: RunWindow, stats: dict[str, Any], on_progress: Optional[OnPhase] = None,
) -> AsyncGenerator[tuple[str, bytes], None]:
    """``documents/<collection>/part-NNNN.jsonl`` per ``MongoSpec``, in spec order. Each chunk of keys is ONE
    ``find({key: {"$in": chunk}}).sort("_id", 1).batch_size(MONGO_BATCH_DOCS)`` cursor ITERATED — a run's
    documents are never loaded as a list. A document over ``MONGO_DOC_MAX_BYTES`` fails the export
    ``row_too_large`` (the collection and the count); a type relaxed Extended JSON would not round-trip fails it
    ``export_build (UnsupportedDocumentType)``. Fills ``stats["history"]["documents"]``."""
    from app.services.project_export_documents import UnsupportedDocumentType
    from app.services.project_export_service import ProjectExportFailed

    history = stats.setdefault("history", {})
    documents: dict[str, dict[str, int]] = history.setdefault("documents", {})
    slugs = await live_session_keys(db, window)
    total = 0
    if on_progress is not None:
        total = sum((await count_documents(db, mongo, window, slugs=slugs)).values())
        await on_progress("documents", 0, total)
    written = 0
    for spec in _mongo_specs():
        writer = PartWriter(f"documents/{spec.collection}/")
        oversized = 0
        collection = mongo[spec.collection]
        async for keys in document_key_chunks(db, spec, window, slugs=slugs):
            cursor = collection.find({spec.key: {"$in": keys}}).sort("_id", 1).batch_size(vocab.MONGO_BATCH_DOCS)
            async for doc in cursor:
                try:
                    line = encode_document(spec, doc, slugs=slugs)
                except UnsupportedDocumentType as exc:
                    _slog.warning("project_export_document_unsupported", collection=spec.collection,
                                  value_type=str(exc))
                    raise ProjectExportFailed("export_build", error_type="UnsupportedDocumentType") from exc
                if len(line) - 1 > vocab.MONGO_DOC_MAX_BYTES:
                    oversized += 1
                    continue
                for member in writer.add(line):
                    yield member
                written += 1
                if on_progress is not None and written % DOCUMENT_PROGRESS_EVERY == 0:
                    await on_progress("documents", written, total)
        if oversized:
            raise ProjectExportFailed("row_too_large", entity=spec.collection, count=oversized)
        for member in writer.flush():
            yield member
        documents[spec.collection] = {"docs": writer.lines, "parts": writer.parts}
    if on_progress is not None:
        await on_progress("documents", written, total)


# ── objects (R-1) ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlannedObject:
    run_id: uuid.UUID
    key: str               # the SOURCE key: read with stream_object, never written anywhere, never logged
    suffix: str            # key minus the run's validated minio_prefix (OBJECT_KEY_SUFFIX_RE)
    size: int


@dataclass
class ObjectPlan:
    objects: list[PlannedObject] = field(default_factory=list)
    skipped: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.objects)

    @property
    def bytes(self) -> int:
        return sum(item.size for item in self.objects)

    def skip(self, entity: str, reason: str) -> None:
        self.skipped[(entity, reason)] = self.skipped.get((entity, reason), 0) + 1


def _in_range(listing: list[dict], prefix: str) -> list[tuple[str, int]]:
    """``(key, size)`` of every listed object under ``prefix`` (the listing is sorted by Key)."""
    start = bisect.bisect_left(listing, prefix, key=lambda entry: entry["Key"])
    out = []
    for entry in listing[start:]:
        key = entry["Key"]
        if not key.startswith(prefix):
            break
        out.append((key, int(entry.get("Size") or 0)))
    return out


async def run_prefixes(db: Any, window: RunWindow) -> dict[uuid.UUID, Optional[str]]:
    """``{run id: minio_prefix}`` of the window's runs (one statement, the bound window array)."""
    runs = _table("test_runs")
    rows = (await db.execute(select(runs.c.id, runs.c.minio_prefix).where(runs.c.id == any_(window.param)))).all()
    return {row[0]: row[1] for row in rows}


async def plan_objects(db: Any, storage: Any, *, project_id: uuid.UUID, window: RunWindow) -> ObjectPlan:
    """The objects a history export copies (R-1): ``list_project_objects`` ONCE, then :func:`plan_from_listing`.
    The listing is the only whole-project list held, and only here (≈ 150 bytes per object)."""
    from app.services.retention_export_service import list_project_objects

    prefixes = await run_prefixes(db, window)
    listing = await list_project_objects(storage, project_id=project_id)
    return plan_from_listing(listing, project_id=project_id, window=window, prefixes=prefixes)


def plan_from_listing(
    listing: list[dict], *, project_id: uuid.UUID, window: RunWindow, prefixes: Mapping[uuid.UUID, Optional[str]],
) -> ObjectPlan:
    """Per window run (newest first) the range of its validated ``minio_prefix`` — the ``uploads/<project>/<run>/``
    range is ingest input and never travels. A prefix the import could not rebuild under the new project (G-A's
    ``rebuild_prefix``: outside ``{project}/``, the bare root, an unsafe path) copies nothing (``test_runs`` /
    ``unsafe_key``); a key ``split_key`` refuses is ``unsafe_key``; an object over ``OBJECT_MAX_BYTES`` is
    ``too_large``. Each key is planned once (the newest run that holds it)."""
    from app.services.project_export_objects import ROOT_MINIO_PREFIX, rebuild_prefix, safe_suffix, split_key

    plan = ObjectPlan()
    seen: set[str] = set()
    ingest_root = f"uploads/{project_id}/"
    for run_id in window.run_ids:
        prefix = (prefixes.get(run_id) or "").strip()
        if not prefix or prefix.startswith(ingest_root):
            continue
        if rebuild_prefix(prefix, source_project_id=project_id, new_project_id=project_id) is None:
            plan.skip("test_runs", "unsafe_key")
            continue
        for key, size in _in_range(listing, prefix):
            if key in seen:
                continue
            seen.add(key)
            split = split_key(key, project_id=project_id, run_id=run_id, minio_prefix=prefix)
            if split is None or split[0] != ROOT_MINIO_PREFIX or not safe_suffix(split[1]):
                plan.skip("objects", "unsafe_key")
                continue
            if size > vocab.OBJECT_MAX_BYTES:
                plan.skip("objects", "too_large")
                continue
            plan.objects.append(PlannedObject(run_id=run_id, key=key, suffix=split[1], size=size))
    return plan


def precheck(plan: ObjectPlan) -> None:
    """E-H2, before any member: refuse ``history_too_large`` when the objects alone pass the member cap (with
    ``MEMBER_MARGIN`` for the parts) or the byte cap — the export never builds 2 GB to discover it."""
    from app.services.project_export_service import ProjectExportFailed

    if plan.count + MEMBER_MARGIN > vocab.HISTORY_MAX_MEMBERS or plan.bytes > vocab.HISTORY_EXPORT_MAX_BYTES:
        raise ProjectExportFailed("history_too_large")


def _stored(suffix: str) -> bool:
    from app.services.project_export_objects import is_stored

    return is_stored(suffix)


def _content_type(suffix: str) -> str:
    guessed, _encoding = mimetypes.guess_type(suffix.rsplit("/", 1)[-1])
    return guessed or "application/octet-stream"


async def _object_chunks(storage: Any, key: str, seen: dict[str, Any]) -> AsyncGenerator[bytes, None]:
    """The stored bytes in ≤ ``OBJECT_CHUNK_BYTES`` chunks, hashed and counted on the way (peak = one chunk)."""
    digest = hashlib.sha256()
    size = 0
    pending: list[bytes] = []
    buffered = 0
    stream = storage.stream_object(key)
    try:
        async for piece in stream:
            if not piece:
                continue
            pending.append(piece)
            buffered += len(piece)
            if buffered >= vocab.OBJECT_CHUNK_BYTES:
                chunk = b"".join(pending)
                pending, buffered = [], 0
                digest.update(chunk)
                size += len(chunk)
                yield chunk
        if pending:
            chunk = b"".join(pending)
            digest.update(chunk)
            size += len(chunk)
            yield chunk
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()
    seen["sha256"], seen["bytes"] = digest.hexdigest(), size


async def iter_objects(
    db: Any, storage: Any, *, project_id: uuid.UUID, window: RunWindow, stats: dict[str, Any],
    on_progress: Optional[OnPhase] = None, plan: Optional[ObjectPlan] = None,
) -> AsyncGenerator[tuple[str, Any], None]:
    """``objects/<source run id>/<NNNNNN>.bin`` — one ``StreamedMember`` per planned object, its bytes AS STORED
    (ZIP_STORED for ``STORED_SUFFIXES``) — then ``objects/index/part-NNNN.jsonl`` (``member``, ``run_id``,
    ``root``, ``suffix``, ``bytes``, ``sha256``, ``content_type``). A member name is the source run id + an
    ordinal, never a stored key. Fills ``stats["history"]["objects"]``."""
    from app.services.compliance_pack_service import StreamedMember
    from app.services.project_export_service import encode_row

    planned = plan if plan is not None else await plan_objects(db, storage, project_id=project_id, window=window)
    total = planned.count
    index = PartWriter("objects/index/")
    pending_index: list[tuple[str, bytes]] = []
    ordinals: dict[uuid.UUID, int] = {}
    copied = copied_bytes = 0
    if on_progress is not None:
        await on_progress("objects", 0, total)
    for item in planned.objects:
        ordinals[item.run_id] = ordinals.get(item.run_id, 0) + 1
        member = f"objects/{item.run_id}/{ordinals[item.run_id]:06d}.bin"
        seen: dict[str, Any] = {}
        yield member, StreamedMember(chunks=_object_chunks(storage, item.key, seen), stored=_stored(item.suffix))
        if "sha256" not in seen:   # build_archive consumes a member whole before asking for the next one
            raise RuntimeError("an object member was not written")
        copied += 1
        copied_bytes += int(seen["bytes"])
        pending_index.extend(index.add(encode_row({
            "member": member, "run_id": str(item.run_id), "root": OBJECT_ROOT, "suffix": item.suffix,
            "bytes": int(seen["bytes"]), "sha256": seen["sha256"], "content_type": _content_type(item.suffix),
        })))
        if on_progress is not None and copied % OBJECT_PROGRESS_EVERY == 0:
            await on_progress("objects", copied, total)
    pending_index.extend(index.flush())
    for part in pending_index:
        yield part
    stats.setdefault("history", {})["objects"] = {"count": copied, "bytes": copied_bytes, "parts": index.parts}
    if on_progress is not None:
        await on_progress("objects", copied, total)


# ── the manifest block + the estimate ────────────────────────────────────────

def _excluded_object_notes() -> tuple[str, ...]:
    from app.services.project_export_objects import EXCLUDED_OBJECT_NOTES

    return tuple(EXCLUDED_OBJECT_NOTES)


def history_block(window: RunWindow, stats: dict[str, Any], *, skipped: list[dict[str, Any]],
                  plan: Optional[ObjectPlan]) -> dict[str, Any]:
    """The manifest's ``history`` block (contract §1.3)."""
    history = stats.get("history") or {}
    rows = [dict(entry) for entry in skipped]
    for (entity, reason), count in sorted((plan.skipped if plan is not None else {}).items()):
        rows.append({"entity": entity, "reason": reason, "count": count})
    for (entity, reason), count in sorted((history.get("row_skips") or {}).items()):
        rows.append({"entity": entity, "reason": reason, "count": count})
    block: dict[str, Any] = {
        "window_days": window.days,            # W37: null for a release scope (the release replaces the window)
        "max_runs": window.max_runs,
        "runs": len(window.run_ids),
        "runs_in_progress_skipped": window.in_progress,
        "runs_over_cap": window.over_cap,
        "oldest_run_at": window.oldest_run_at.isoformat() if window.oldest_run_at else None,
        "newest_run_at": window.newest_run_at.isoformat() if window.newest_run_at else None,
        "documents": dict(history.get("documents") or {}),
        "objects": dict(history.get("objects") or {"count": 0, "bytes": 0, "parts": 0}),
        "skipped": rows,
        "excluded_objects": list(_excluded_object_notes()),
    }
    if window.release_id is not None:          # W37 F-W30-1 (the W30 importer ignores an unknown history key)
        block["release"] = {"id": str(window.release_id), "name": window.release_name}
    return block


def history_counts(stats: dict[str, Any]) -> dict[str, int]:
    """The job's ``counts`` additions: ``documents.<collection>`` and ``objects``."""
    history = stats.get("history") or {}
    out = {f"documents.{name}": int(entry.get("docs", 0)) for name, entry in (history.get("documents") or {}).items()}
    if "objects" in history:
        out["objects"] = int(history["objects"].get("count", 0))
    return out


async def estimate(
    db: Any, mongo: Any, storage: Any, *, project_id: uuid.UUID, options: Any, now: Optional[datetime] = None,
) -> Any:
    """``ProjectExportEstimate`` (the estimate GET, E-H7): the window statement, ``count(*)`` per planned entity,
    ``count_documents`` per collection and the object plan's count / bytes — counts only, NO write of any kind.
    A store that cannot be read answers ``measured[store] = False`` with NULL counts (absence is not health), and
    ``estimated_bytes`` / ``over_limit`` are then NULL too."""
    from app.models.schemas import ProjectExportEstimate
    from app.services.project_export_entities import plan as entity_plan
    from app.services.retention_export_service import list_project_objects

    include_history = bool(_option(options, "include_history", False))
    at = now or datetime.now(timezone.utc)
    if include_history:
        window = await run_window(db, project_id=project_id, options=options, now=at)
    else:
        window = RunWindow(project_id=project_id, run_ids=[], days=None, max_runs=vocab.HISTORY_MAX_RUNS)
    rows = 0
    test_cases: Optional[int] = None
    for spec in entity_plan(include_history=include_history):
        count = int((await db.execute(count_statement(spec, window))).scalar() or 0)
        rows += count
        if spec.table == "test_cases":
            test_cases = count
    if not include_history:
        catalog_bytes = rows * ROW_BYTES_ESTIMATE
        return ProjectExportEstimate(
            include_history=False, history_days=None, documents={}, objects=0, object_bytes=0,
            estimated_bytes=catalog_bytes, over_limit=catalog_bytes > vocab.EXPORT_MAX_BYTES,
            measured={"postgres": True, "documents": True, "objects": True},
        )
    measured = {"postgres": True, "documents": True, "objects": True}
    slugs = await live_session_keys(db, window)
    prefixes = await run_prefixes(db, window)
    documents: Optional[dict[str, int]] = None
    try:
        documents = await count_documents(db, mongo, window, slugs=slugs)
    except Exception as exc:  # noqa: BLE001 - an unreachable Mongo is "not measured", never 0
        measured["documents"] = False
        _slog.warning("project_export_estimate_unmeasured", store="documents", error_type=type(exc).__name__)
    objects: Optional[int] = None
    object_bytes: Optional[int] = None
    try:
        listing = await list_project_objects(storage, project_id=project_id)
        plan = plan_from_listing(listing, project_id=project_id, window=window, prefixes=prefixes)
        objects, object_bytes = plan.count, plan.bytes
    except Exception as exc:  # noqa: BLE001 - a storage fault is "not measured", never 0
        measured["objects"] = False
        _slog.warning("project_export_estimate_unmeasured", store="objects", error_type=type(exc).__name__)
    estimated: Optional[int] = None
    over: Optional[bool] = None
    if documents is not None and objects is not None and object_bytes is not None:
        estimated = rows * ROW_BYTES_ESTIMATE + sum(documents.values()) * DOCUMENT_BYTES_ESTIMATE + object_bytes
        over = (estimated > vocab.HISTORY_EXPORT_MAX_BYTES
                or object_bytes > vocab.HISTORY_EXPORT_MAX_BYTES
                or objects + MEMBER_MARGIN > vocab.HISTORY_MAX_MEMBERS)
    return ProjectExportEstimate(
        include_history=True, history_days=window.days, runs=len(window.run_ids),
        runs_in_progress=window.in_progress, runs_over_cap=window.over_cap, test_cases=test_cases,
        documents=documents, objects=objects, object_bytes=object_bytes,
        estimated_bytes=estimated, over_limit=over, measured=measured, history_release_id=window.release_id,
    )
