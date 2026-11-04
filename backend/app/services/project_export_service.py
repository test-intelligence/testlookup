"""The project export: settings + catalog as ONE signed, verified ZIP (W24 ADM-05 phase 1, contract §2.2).

* :func:`iter_members` walks ``project_export_entities.plan()`` on the caller's
  session -- ONE streamed ``SELECT`` per table (``yield_per(CURSOR_BATCH_ROWS)``,
  ordered by the primary key, scoped by ``project_id = :source`` or by the
  exported parents through a SUBQUERY, never a Python id list) → the columns
  ``spec.drop`` removes are never selected → ``project_export_scrub.scrub_row``
  (§2.2) → JSONL parts of ≤ ``PART_ROWS_MAX`` rows / ≤ ``PART_BYTES_MAX`` bytes.
  Each part is one ``build_archive`` member, so peak memory is one part. Then
  ``directory/users.jsonl`` (only the users an exported row references: id,
  username, full name -- no e-mail, no role) and ``README.txt``.
* :func:`run_project_export` -- ``compliance_pack_service.build_archive`` (the ONE
  archive builder, manifest LAST) into a spool → ``_sign_pack`` → the object
  store → ``on_written(key)`` → RE-READ + verify (``verify_stored_archive``).
  The source session only reads: the runner holds it on a READ ONLY
  ``REPEATABLE READ`` transaction; the signing key (created on a deployment's
  first pack) is written on ``sign_db``, never on the snapshot.
* :func:`sweep` -- the janitor's body (expire exports, remove import uploads,
  fail stale running jobs). Stage-only: the runner commits.

Stage-only (``STAGE_ONLY_SERVICES``): no commit here. Logs carry job / project
ids, entity names, counts, byte totals, codes and error TYPE names -- never a
row value, a name, a URL, a storage key or a digest.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional
from urllib.parse import urlsplit

import structlog
from sqlalchemy import Table, select

from app.services import project_export_vocab as vocab
from app.services.project_export_entities import (
    EXCLUDED,
    SCOPE_ROOT,
    EntitySpec,
    exported_columns,
    plan,
    spec_for,
)
from app.services.project_export_scrub import scrub_row

_slog = structlog.get_logger(__name__)

OnTable = Callable[[int, int], Awaitable[None]]
DIRECTORY_MEMBER = "directory/users.jsonl"
README_MEMBER = "README.txt"
_USER_CHUNK = 1_000


class ProjectExportFailed(Exception):
    """An export that must end ``failed``: ``code`` is an ``ERROR_SENTENCES`` key, ``message`` its
    formatted sentence (type names and counts only -- never a row value)."""

    def __init__(self, code: str, **fmt: Any) -> None:
        self.code = code
        self.params = fmt
        self.message = vocab.ERROR_SENTENCES[code].format(**fmt)
        super().__init__(self.message)


@dataclass
class ProjectExportOutcome:
    """What a verified export produced. ``key`` is for the job row ONLY (never returned, never logged)."""

    key: str
    bytes: int
    file_count: int
    manifest_sha256: str
    archive_sha256: str
    signature: str                       # "signed" | "unsigned"
    signing_key_id: Optional[str]
    counts: dict[str, int] = field(default_factory=dict)
    verify_attempts: int = 1


# ── rows ─────────────────────────────────────────────────────────────────────

def _table(name: str) -> Table:
    from app.models.postgres import Base

    return Base.metadata.tables[name]


def _jsonable(value: Any) -> Any:
    """The §1.3 row encoding: datetime / date / time → ISO 8601, UUID → text, Decimal → string."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not exportable")


def encode_row(row: dict[str, Any]) -> bytes:
    """One JSONL line (with its ``\\n``): keys sorted, UTF-8, no NaN."""
    text = json.dumps(row, default=_jsonable, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))
    return text.encode("utf-8") + b"\n"


def scope_clause(spec: EntitySpec, project_id: uuid.UUID) -> Any:
    """The WHERE of ``spec``'s rows: the root row, ``project_id = :source``, or ``fk IN (SELECT id FROM
    parent WHERE <the parent's own scope>)`` -- a subquery, never a Python id list."""
    table = _table(spec.table)
    if spec.scope == SCOPE_ROOT:
        return table.c.id == project_id
    parent = spec.parent
    if parent is None:
        return table.c.project_id == project_id
    parent_table, fk_column = parent
    parent_rows = select(_table(parent_table).c.id).where(scope_clause(spec_for(parent_table), project_id))
    return table.c[fk_column].in_(parent_rows)


def rows_statement(spec: EntitySpec, project_id: uuid.UUID) -> Any:
    """ONE ``SELECT`` of the exported columns of ``spec``'s rows, ordered by the primary key, streamed."""
    table = _table(spec.table)
    columns = [table.c[name] for name in exported_columns(spec)]
    return (
        select(*columns)
        .where(scope_clause(spec, project_id))
        .order_by(*table.primary_key.columns)
        .execution_options(yield_per=vocab.CURSOR_BATCH_ROWS)
    )


def user_columns(spec: EntitySpec) -> tuple[str, ...]:
    """The ``user_refs`` / ``cleared`` columns of ``spec`` that reference ``users.id`` (the directory's input)."""
    table = _table(spec.table)
    out = []
    for name in (*spec.user_refs, *spec.cleared):
        column = table.c.get(name)
        if column is not None and any(fk.column.table.name == "users" for fk in column.foreign_keys):
            out.append(name)
    return tuple(out)


def _part_name(table: str, number: int) -> str:
    return f"entities/{table}/part-{number:04d}.jsonl"


async def _directory(db: Any, user_ids: set[Any]) -> list[dict[str, Any]]:
    """``{id, username, full_name}`` of the referenced users, ordered by id (no e-mail, role or hash)."""
    users = _table("users")
    ids = sorted(user_ids, key=str)
    found: list[dict[str, Any]] = []
    for start in range(0, len(ids), _USER_CHUNK):
        chunk = ids[start:start + _USER_CHUNK]
        result = await db.execute(
            select(users.c.id, users.c.username, users.c.full_name).where(users.c.id.in_(chunk)).order_by(users.c.id)
        )
        for row in result.all():
            found.append({"id": str(row[0]), "username": row[1], "full_name": row[2]})
    found.sort(key=lambda entry: entry["id"])
    return found


README_TEXT = "\n".join((
    "TestLookup project export: settings and test catalog",
    "",
    "This ZIP holds ONE project's settings and test catalog (suites, test cases and their versions,",
    "plans, strategies, releases, gates, policies, ownership rules, workflow definitions, cadences),",
    "one JSONL file per part under entities/<table>/, in dependency order.",
    "",
    "It does NOT hold: run history, logs, attachments or other stored files, members, roles, API keys,",
    "or any secret. Secrets kept in the deployment's secret store never travel; a secret typed into a",
    "free-text or JSON field is replaced by [REDACTED]. Pattern fields (ownership, attribution and",
    "classification patterns, test fingerprints) travel byte-exact.",
    "",
    "Import it as a NEW project (Settings > Project data > Import as a new project, or",
    "`testlookup projects import <zip>`). Nothing is merged into an existing project; webhooks, team",
    "channels, Git integrations and notification rules arrive switched off, run cadences paused.",
    "",
    "Verification (tamper-evident checksum chain + Ed25519 signature):",
    "1. For each file listed in manifest.json `files`, compute its SHA-256 and compare it with the",
    "   manifest. No other file may be in the ZIP except signature.json.",
    "2. signature.json: its `manifest_sha256` must equal the SHA-256 of manifest.json. Verify",
    "   `signature` (base64 Ed25519) over the UTF-8 statement",
    "   `testlookup-compliance-pack-v1\\n<pack_id>\\n<manifest_sha256>\\n<key_id>\\n` with the exporting",
    "   deployment's public key for `key_id` (GET /api/v1/compliance-packs/signing-keys).",
    "3. `testlookup compliance-pack verify <zip> --public-key <pem>` does all of it offline.",
    "An archive without signature.json is tamper-evident only.",
    "",
))

#: The README of an export WITH run history (schema 2): phase 1's text + a "Run history" section.
HISTORY_README_TEXT = README_TEXT.replace(
    "It does NOT hold: run history, logs, attachments or other stored files, members, roles, API keys,",
    "It does NOT hold: members, roles, API keys, or any secret (see Run history below for what of the",
).replace(
    "or any secret. Secrets kept in the deployment's secret store never travel; a secret typed into a",
    "runs travels). Secrets kept in the deployment's secret store never travel; a secret typed into a",
) + "\n".join((
    "Run history (schema_version 2):",
    "The runs of the chosen window (newest first, at most 2000; finished runs only) with their test results,",
    "AI analyses and their versions, AI pipeline results (finished pipelines only), clusters, reviews, release",
    "links and gate decisions, under entities/<table>/; the runs' stored documents under",
    "documents/<collection>/ (MongoDB relaxed Extended JSON, one document per line); and the runs' attachments",
    "and result files under objects/<run>/, listed in objects/index/. Attachments are copied byte-for-byte and",
    "NOT scanned: a binary cannot be scrubbed, so a token a test wrote into a screenshot or a result file",
    "travels as it is stored. Rows and documents ARE scrubbed. A scrubbed AI version's content_sha256 may no",
    "longer match its content.",
    "Not included: raw report uploads (ingest input), pipeline scratch files, defects, retention holds, AI",
    "feedback, outboxes and live-ingest state. Imported runs keep their original dates; importing runs nothing",
    "(no AI analysis, notification, webhook or gate evaluation).",
    "",
))


async def iter_members(
    db: Any, *, project_id: uuid.UUID, stats: dict[str, Any], on_table: Optional[OnTable] = None,
) -> AsyncGenerator[tuple[str, bytes], None]:
    """Every member of the archive but the manifest, in plan order: the JSONL parts, the directory, the
    README. Fills ``stats["entities"]`` ({table: {rows, parts, columns}}) table by table and
    ``stats["directory"]`` ({users}); calls ``on_table(done, total)`` after each table.

    A row whose encoding exceeds ``ROW_BYTES_MAX`` is counted and NOT written; the table then fails the
    export with ``row_too_large`` (the entity and the count, never the row)."""
    specs = plan(include_history=False)
    total = len(specs)
    entities: dict[str, dict[str, Any]] = stats.setdefault("entities", {})
    referenced: set[Any] = set()
    for done, spec in enumerate(specs, start=1):
        async for member in entity_parts(
            db, spec, rows_statement(spec, project_id), exported_columns(spec),
            entities=entities, referenced=referenced,
        ):
            yield member
        if on_table is not None:
            await on_table(done, total)
    async for member in directory_and_readme(db, referenced, stats, readme=README_TEXT):
        yield member


async def entity_parts(
    db: Any, spec: EntitySpec, statement: Any, columns: tuple[str, ...], *,
    entities: dict[str, dict[str, Any]], referenced: set[Any],
    observe: Optional[Callable[[dict[str, Any]], None]] = None,
) -> AsyncGenerator[tuple[str, bytes], None]:
    """ONE streamed ``SELECT`` (``statement``) → ``scrub_row`` → JSONL parts of ≤ ``PART_ROWS_MAX`` rows / ≤
    ``PART_BYTES_MAX`` bytes; ``entities[table] = {rows, parts, columns}`` when the table is done; the referenced
    user ids are added to ``referenced``; ``observe(raw)`` sees every source row before the scrub (W30: it counts,
    and may narrow a JSON id list to the archive's runs IN the row dict — it never writes anywhere).

    A row whose encoding exceeds ``ROW_BYTES_MAX`` is counted and NOT written; the table then fails the
    export with ``row_too_large`` (the entity and the count, never the row)."""
    refs = user_columns(spec)
    rows = parts = oversized = 0
    buffer: list[bytes] = []
    buffered = 0
    result = await db.stream(statement)
    try:
        async for mapping in result.mappings():
            raw = dict(mapping)
            if observe is not None:
                observe(raw)
            line = encode_row(scrub_row(spec, raw))
            if len(line) - 1 > vocab.ROW_BYTES_MAX:
                oversized += 1
                continue
            for column in refs:
                if raw.get(column) is not None:
                    referenced.add(raw[column])
            if buffer and buffered + len(line) > vocab.PART_BYTES_MAX:
                parts += 1
                yield _part_name(spec.table, parts), b"".join(buffer)
                buffer, buffered = [], 0
            buffer.append(line)
            buffered += len(line)
            rows += 1
            if len(buffer) >= vocab.PART_ROWS_MAX:
                parts += 1
                yield _part_name(spec.table, parts), b"".join(buffer)
                buffer, buffered = [], 0
    finally:
        await result.close()
    if oversized:
        raise ProjectExportFailed("row_too_large", entity=spec.table, count=oversized)
    if buffer:
        parts += 1
        yield _part_name(spec.table, parts), b"".join(buffer)
    entities[spec.table] = {"rows": rows, "parts": parts, "columns": list(columns)}


async def directory_and_readme(
    db: Any, referenced: set[Any], stats: dict[str, Any], *, readme: str,
) -> AsyncGenerator[tuple[str, bytes], None]:
    """``directory/users.jsonl`` (the referenced users: id, username, full name) then ``README.txt``."""
    directory = await _directory(db, referenced)
    stats["directory"] = {"users": len(directory)}
    yield DIRECTORY_MEMBER, b"".join(encode_row(entry) for entry in directory)
    yield README_MEMBER, readme.encode("utf-8")


def counts_of(stats: dict[str, Any]) -> dict[str, int]:
    """Rows written per entity (the job's ``counts``)."""
    return {table: int(entry.get("rows", 0)) for table, entry in (stats.get("entities") or {}).items()}


def _public_host() -> Optional[str]:
    from app.core.config import settings

    base = getattr(settings, "PUBLIC_BASE_URL", None)
    if not base:
        return None
    try:
        return urlsplit(str(base)).hostname or None
    except ValueError:
        return None


def excluded_by_reason() -> dict[str, list[str]]:
    """``{reason: [tables]}`` -- what this archive deliberately leaves out, and why."""
    out: dict[str, list[str]] = {}
    for table, reason in EXCLUDED.items():
        out.setdefault(reason, []).append(table)
    return {reason: sorted(tables) for reason, tables in sorted(out.items())}


def manifest_extra(
    *, project_name: str, project_slug: str, stats: dict[str, Any],
    schema_version: int = vocab.CATALOG_SCHEMA_VERSION, history: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """The project branch's manifest keys (§1.3), from what the member iterator recorded. A catalog export
    (no ``history``) writes ``CATALOG_SCHEMA_VERSION`` and exactly phase 1's keys; a history export writes
    ``SCHEMA_VERSION`` and the ``history`` block (W30 §1.3)."""
    from app.core.config import settings

    extra = {
        "format": vocab.ARCHIVE_FORMAT,
        "schema_version": schema_version,
        "source": {"app_version": getattr(settings, "APP_VERSION", None), "host": _public_host()},
        "project": {"name": project_name, "slug": project_slug},
        "entities": dict(stats.get("entities") or {}),
        "directory": dict(stats.get("directory") or {"users": 0}),
        "excluded": excluded_by_reason(),
    }
    if history is not None:
        extra["history"] = history
    return extra


# ── build → sign → write → re-read + verify ─────────────────────────────────

@dataclass
class HistoryPlan:
    """What a history export decided BEFORE its first member (W30 E-H1 / E-H2), on the same snapshot."""

    window: Any                          # project_export_history.RunWindow
    objects: Any                         # project_export_history.ObjectPlan (prechecked)
    skipped: list[dict[str, Any]]        # non-terminal pipelines / their stage results, counted
    mongo: Any
    storage: Any


OnProgress = Callable[[dict[str, Any]], Awaitable[None]]


def _include_history(options: Any) -> bool:
    if options is None:
        return False
    value = options.get("include_history") if isinstance(options, dict) else getattr(options, "include_history", False)
    return bool(value)


async def iter_history_members(
    db: Any, *, project_id: uuid.UUID, history: HistoryPlan, stats: dict[str, Any],
    on_progress: Optional[OnProgress] = None,
) -> AsyncGenerator[tuple[str, Any], None]:
    """Every member of a schema-2 archive but the manifest, in order, on ONE snapshot: the 29 catalog entities
    (schema-2 columns) → the history entities → documents → objects (+ index) → directory → README. Progress
    ``{done, total, phase, phase_done, phase_total}`` after each table / document batch / object batch."""
    from app.services import project_export_history as hx

    specs = plan(include_history=True)
    total = len(specs)
    catalog_total = sum(1 for spec in specs if not spec.history)
    entities: dict[str, dict[str, Any]] = stats.setdefault("entities", {})
    row_skips: dict[tuple[str, str], int] = stats.setdefault("history", {}).setdefault("row_skips", {})
    referenced: set[Any] = set()

    async def report(done: int, phase: str, phase_done: int, phase_total: int) -> None:
        if on_progress is not None:
            await on_progress({"done": done, "total": total, "phase": phase,
                               "phase_done": phase_done, "phase_total": phase_total})

    async def phase_progress(phase: str, done: int, of: int) -> None:
        await report(total, phase, done, of)

    for done, spec in enumerate(specs, start=1):
        async for member in entity_parts(
            db, spec, hx.rows_statement(spec, history.window), hx.columns_of(spec),
            entities=entities, referenced=referenced, observe=hx.row_hook(spec, history.window, row_skips),
        ):
            yield member
        if spec.history:
            await report(done, "runs", done - catalog_total, total - catalog_total)
        else:
            await report(done, "catalog", done, catalog_total)
    async for member in hx.iter_documents(
        db, history.mongo, window=history.window, stats=stats, on_progress=phase_progress,
    ):
        yield member
    async for member in hx.iter_objects(
        db, history.storage, project_id=project_id, window=history.window, stats=stats,
        on_progress=phase_progress, plan=history.objects,
    ):
        yield member
    async for member in directory_and_readme(db, referenced, stats, readme=HISTORY_README_TEXT):
        yield member


async def _build(
    db: Any, *, project_id: uuid.UUID, project_name: str, project_slug: str, sink: Any, now: datetime,
    stats: dict[str, Any], on_table: Optional[OnTable], history: Optional[HistoryPlan] = None,
    on_progress: Optional[OnProgress] = None,
) -> Any:
    from app.services import compliance_pack_service as packs

    extra: dict[str, Any] = {}
    scope = packs.ExportScope.for_project(project_id=project_id, manifest_extra=extra)

    if history is None:
        inner: AsyncGenerator[tuple[str, Any], None] = iter_members(
            db, project_id=project_id, stats=stats, on_table=on_table,
        )
        max_bytes = vocab.EXPORT_MAX_BYTES
    else:
        inner = iter_history_members(db, project_id=project_id, history=history, stats=stats, on_progress=on_progress)
        max_bytes = vocab.HISTORY_EXPORT_MAX_BYTES

    async def members() -> AsyncGenerator[tuple[str, Any], None]:
        async for item in inner:
            yield item
        # build_archive writes the manifest AFTER the last member: fill the scope's keys now.
        if history is None:
            extra.update(manifest_extra(project_name=project_name, project_slug=project_slug, stats=stats))
        else:
            from app.services import project_export_history as hx

            extra.update(manifest_extra(
                project_name=project_name, project_slug=project_slug, stats=stats,
                schema_version=vocab.SCHEMA_VERSION,
                history=hx.history_block(history.window, stats, skipped=history.skipped, plan=history.objects),
            ))

    outer = members()
    try:
        archive = await packs.build_archive(
            scope, members=outer, sink=sink, generated_at=now, max_bytes=max_bytes,
        )
    finally:
        # A refusal mid-table (the byte cap) leaves both iterators suspended: close them now, so the
        # server-side cursor of the table being read is released before the caller records the failure.
        await outer.aclose()
        await inner.aclose()
    return scope, archive


async def _plan_history(
    db: Any, *, project_id: uuid.UUID, options: Any, storage: Any, mongo: Any, now: datetime,
) -> HistoryPlan:
    """E-H1 + E-H2 on the snapshot, BEFORE any member: the run window, the skipped counts, the object plan
    (ONE listing) and its precheck (``history_too_large``)."""
    from app.services import project_export_history as hx

    window = await hx.run_window(db, project_id=project_id, options=options, now=now)
    objects = await hx.plan_objects(db, storage, project_id=project_id, window=window)
    hx.precheck(objects)
    skipped = await hx.count_skipped(db, window)
    if mongo is None:
        from app.db.mongo import get_mongo_db

        mongo = get_mongo_db()
    _slog.info(
        "project_export_history_planned", project_id=str(project_id), runs=len(window.run_ids),
        runs_in_progress=window.in_progress, runs_over_cap=window.over_cap, objects=objects.count,
        object_bytes=objects.bytes,
    )
    return HistoryPlan(window=window, objects=objects, skipped=skipped, mongo=mongo, storage=storage)


async def run_project_export(
    db: Any,
    *,
    job_id: uuid.UUID,
    project: Any,
    storage: Any,
    now: datetime,
    on_written: Optional[Callable[[str], Awaitable[Any]]] = None,
    sign_db: Any = None,
    on_table: Optional[OnTable] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    options: Any = None,
    mongo: Any = None,
    on_progress: Optional[OnProgress] = None,
) -> ProjectExportOutcome:
    """Build → sign → write → RE-READ + verify. Raises :class:`ProjectExportFailed`.

    ``db`` only READS (the runner holds it on a read-only snapshot); ``sign_db`` (default ``db``) is
    where ``_sign_pack`` may CREATE the deployment's signing key and where the public keys are read --
    the caller commits it. ``on_written(key)`` runs right after the upload (the runner commits the key
    there, so a crash before verification leaves the object for the janitor). Any failure after the
    upload removes the object, best effort.

    ``options.include_history`` (W30): the run window, the object plan and its precheck run FIRST on the same
    snapshot (``history_too_large`` before any member), then the schema-2 members (``mongo`` = the Motor
    database, default ``get_mongo_db()``), capped at ``HISTORY_EXPORT_MAX_BYTES``; ``on_progress`` receives the
    phase progress. No options (every phase-1 caller) = exactly phase 1: schema 1, 50 MiB."""
    from app.services import compliance_pack_service as packs
    from app.services import compliance_signing
    from app.services.retention_export_service import (
        ExportFailed,
        new_spool,
        remove_archive,
        verify_stored_archive,
    )

    signer = sign_db if sign_db is not None else db
    project_id = uuid.UUID(str(project.id))
    stats: dict[str, Any] = {}
    spool = new_spool()
    key: Optional[str] = None
    with_history = _include_history(options)
    max_bytes = vocab.HISTORY_EXPORT_MAX_BYTES if with_history else vocab.EXPORT_MAX_BYTES
    too_large = "history_too_large" if with_history else "export_too_large"
    try:
        try:
            history = await _plan_history(
                db, project_id=project_id, options=options, storage=storage, mongo=mongo, now=now,
            ) if with_history else None
            scope, archive = await _build(
                db, project_id=project_id, project_name=str(project.name), project_slug=str(project.slug),
                sink=spool, now=now, stats=stats, on_table=on_table, history=history, on_progress=on_progress,
            )
            # End the read-only snapshot NOW: every row is in the spool, and holding its connection through the
            # sign / write / verify steps needed a THIRD one (on_written, the storage config) — the worker's pool
            # is size 1 + overflow 1, so the homelab's first export waited out two 30 s pool timeouts and failed
            # export_write (TimeoutError). Nothing below reads `project` (its id was taken above).
            await db.rollback()
            spool, signature, signing_key_id, _signing = await packs._sign_pack(
                signer, spool, pack_id=job_id, manifest_sha256=archive.manifest_sha256, actor=None, signed_at=now,
            )
        except packs.ArchiveTooLarge as exc:
            raise ProjectExportFailed(too_large) from exc
        except ProjectExportFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - only the type name reaches the job row
            raise ProjectExportFailed("export_build", error_type=type(exc).__name__) from exc
        archive_sha256 = await asyncio.to_thread(packs.sha256_of_file, spool)
        size = spool.seek(0, 2)
        if size > max_bytes:   # signature.json is appended after the builder's cap
            raise ProjectExportFailed(too_large)
        key = packs.build_export_key(scope, now, job_id)
        try:
            await storage.put_object_stream(
                key, spool, size=size, content_type="application/zip", bucket=packs.COMPLIANCE_BUCKET,
            )
            if on_written is not None:
                await on_written(key)
        except Exception as exc:  # noqa: BLE001
            raise ProjectExportFailed("export_write", error_type=type(exc).__name__) from exc
        if with_history and on_progress is not None:
            total = len(stats.get("entities") or {})
            await on_progress({"done": total, "total": total, "phase": "verify", "phase_done": 0, "phase_total": 1})
        try:
            public_keys = await compliance_signing.public_keys(signer)
        except Exception as exc:  # noqa: BLE001
            raise ProjectExportFailed("export_unverified", reason="signing keys unreadable") from exc
        try:
            attempts = await verify_stored_archive(
                storage, key=key, archive_sha256=archive_sha256, manifest_sha256=archive.manifest_sha256,
                public_keys=public_keys, job_id=job_id, signature=signature, signing_key_id=signing_key_id,
                sleep=sleep,
            )
        except ExportFailed as failed:
            raise ProjectExportFailed(
                "export_unverified", reason=str(failed.params.get("reason") or failed.code),
            ) from failed
        except Exception as exc:  # noqa: BLE001 - an unproven archive is a failed export
            raise ProjectExportFailed("export_unverified", reason=type(exc).__name__) from exc
    except ProjectExportFailed as failed:
        if key is not None:
            await remove_archive(storage, key=key)
        _slog.warning("project_export_failed", job_id=str(job_id), project_id=str(project_id), code=failed.code)
        raise
    finally:
        spool.close()

    counts = counts_of(stats)
    _slog.info(
        "project_export_verified", job_id=str(job_id), project_id=str(project_id), tables=len(counts),
        rows=sum(counts.values()), bytes=size, files=archive.file_count, verify_attempts=attempts,
        history=with_history,
    )
    if with_history:
        from app.services import project_export_history as hx

        counts.update(hx.history_counts(stats))
    return ProjectExportOutcome(
        key=key, bytes=size, file_count=archive.file_count,
        manifest_sha256=archive.manifest_sha256, archive_sha256=archive_sha256,
        signature="signed" if signature else "unsigned", signing_key_id=signing_key_id,
        counts=counts, verify_attempts=attempts,
    )


# ── the janitor's body (E-7) ─────────────────────────────────────────────────

SWEEP_LIMIT = 200


def _modified_at(entry: dict[str, Any]) -> Optional[datetime]:
    """A listing entry's time: S3 gives a datetime, the local provider an epoch float."""
    from datetime import timezone

    value = entry.get("LastModified")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return None


async def _remove(storage: Any, key: str) -> bool:
    from app.services.retention_export_service import remove_archive

    return bool(await remove_archive(storage, key=key))


def is_history_job(kind: Any, options: Any) -> bool:
    """A job that runs under the history time limits: an export WITH history, or a PARTED import (W30)."""
    raw = options if isinstance(options, dict) else {}
    return bool(raw.get("include_history")) if kind == "export" else bool(raw.get("parted"))


def stale_minutes(kind: Any, options: Any) -> int:
    """The janitor's ``running`` threshold for one job: ``HISTORY_JOB_STALE_MINUTES`` for a history job (its
    per-message limit is 3 h), else phase 1's ``JOB_STALE_MINUTES`` (the global 31 min limit + margin)."""
    return vocab.HISTORY_JOB_STALE_MINUTES if is_history_job(kind, options) else vocab.JOB_STALE_MINUTES


async def sweep(
    db: Any, storage: Any, *, now: datetime, limit: int = SWEEP_LIMIT, mongo: Any = None,
) -> dict[str, int]:
    """Stage the janitor's work on ``db`` (the caller commits); idempotent -- every UPDATE is guarded by the
    state it leaves. A storage fault leaves its row for the next run. The ONLY expiry path (no GET expires).

    1. a succeeded export past ``expires_at`` → object removed, ``expired``;
    2. an object no job may keep (an import upload of a finished job; the object of a failed / expired
       export) → removed, ``storage_key`` NULL;
    3. a job ``running`` past ITS threshold (:func:`stale_minutes` — its worker died) → ``failed``
       ``interrupted`` (not a data failure), its partial object removed;
    4. a stray upload under ``project-imports/`` (parts excluded) older than ``IMPORT_UPLOAD_TTL_HOURS`` that no
       queued / running job owns (its route never committed) → removed;
    5. (W30) an ``uploading`` job older than ``UPLOAD_STALE_HOURS`` → its parts removed, then ``failed``
       ``interrupted``; the parts of a finished (or vanished) job older than ``IMPORT_UPLOAD_TTL_HOURS`` → removed;
    6. (W30, I-H9) a finished import that still holds its ``cleanup`` ledger: when the ledger's project does NOT
       exist (the transaction never committed) → ``cleanup_outside_postgres`` by the ledger (idempotent), then
       the ledger cleared; when it DOES exist (the commit happened) → the ledger only cleared. ``mongo`` defaults
       to ``get_mongo_db()``, read only when a ledger needs a cleanup."""
    from sqlalchemy import and_, or_, update

    from app.models.postgres import ProjectDataJob as Job
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    out = {"expired": 0, "objects_removed": 0, "interrupted_export": 0, "interrupted_import": 0,
           "strays_removed": 0, "remove_failed": 0, "uploads_interrupted": 0, "parts_removed": 0,
           "ledgers_cleaned": 0, "ledgers_cleared": 0, "ledger_failed": 0}

    due = (await db.execute(
        select(Job.id, Job.storage_key)
        .where(Job.kind == "export", Job.status == "succeeded", Job.expires_at.is_not(None), Job.expires_at <= now)
        .order_by(Job.expires_at).limit(limit)
    )).all()
    for job_id, key in due:
        if key and not await _remove(storage, key):
            out["remove_failed"] += 1
            continue
        result = await db.execute(
            update(Job).where(Job.id == job_id, Job.status == "succeeded").values(status="expired", storage_key=None)
        )
        out["expired"] += int(result.rowcount or 0)

    doomed = (await db.execute(
        select(Job.id, Job.storage_key)
        .where(Job.storage_key.is_not(None), or_(
            and_(Job.kind == "import", Job.status.in_(vocab.TERMINAL_STATUSES)),
            and_(Job.kind == "export", Job.status.in_(("failed", "expired"))),
        ))
        .order_by(Job.requested_at).limit(limit)
    )).all()
    for job_id, key in doomed:
        if not await _remove(storage, key):
            out["remove_failed"] += 1
            continue
        await db.execute(update(Job).where(Job.id == job_id, Job.storage_key == key).values(storage_key=None))
        out["objects_removed"] += 1

    stale_before = now - timedelta(minutes=min(vocab.JOB_STALE_MINUTES, vocab.HISTORY_JOB_STALE_MINUTES))
    stale = (await db.execute(
        select(Job.id, Job.kind, Job.storage_key, Job.options, Job.started_at)
        .where(Job.status == "running", Job.started_at.is_not(None), Job.started_at <= stale_before)
        .order_by(Job.started_at).limit(limit)
    )).all()
    for job_id, kind, key, options, started_at in stale:
        if started_at > now - timedelta(minutes=stale_minutes(kind, options)):
            continue                      # a history job's 3 h limit: not stale yet
        removed = bool(key) and await _remove(storage, key)
        values: dict[str, Any] = {
            "status": "failed", "error_code": "interrupted", "error": vocab.ERROR_SENTENCES["interrupted"],
            "finished_at": now,
        }
        if removed:
            values["storage_key"] = None
        result = await db.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(**values))
        if result.rowcount:
            out["interrupted_export" if kind == "export" else "interrupted_import"] += 1

    stray_before = now - timedelta(hours=vocab.IMPORT_UPLOAD_TTL_HOURS)
    try:
        listing = await storage.list_objects(vocab.STORAGE_PREFIX_IMPORT, bucket=COMPLIANCE_BUCKET)
    except Exception as exc:  # noqa: BLE001 - a listing fault waits for the next run
        _slog.warning("project_data_sweep_list_failed", error_type=type(exc).__name__)
        listing = []
    old = [
        str(entry["Key"]) for entry in listing
        if entry.get("Key") and not str(entry["Key"]).startswith(vocab.STORAGE_PREFIX_IMPORT_PARTS)
        and (_modified_at(entry) or now) <= stray_before
    ][:limit]
    if old:
        owned = {
            row[0] for row in (await db.execute(
                select(Job.storage_key).where(Job.storage_key.in_(old), Job.status.in_(("queued", "running")))
            )).all()
        }
        for key in old:
            if key in owned:
                continue
            if await _remove(storage, key):
                out["strays_removed"] += 1
            else:
                out["remove_failed"] += 1
    await _sweep_uploads(db, storage, now=now, limit=limit, out=out)
    await _sweep_ledgers(db, storage, limit=limit, out=out, mongo=mongo)
    return out


async def _remove_parts(storage: Any, job_id: Any) -> bool:
    """Every part of one parted upload removed (best effort: a fault waits for the next run)."""
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    try:
        await storage.delete_prefix(f"{vocab.STORAGE_PREFIX_IMPORT_PARTS}{job_id}/", bucket=COMPLIANCE_BUCKET)
        return True
    except Exception as exc:  # noqa: BLE001 - retried by the next run
        _slog.warning("project_data_sweep_parts_remove_failed", job_id=str(job_id), error_type=type(exc).__name__)
        return False


async def _sweep_uploads(db: Any, storage: Any, *, now: datetime, limit: int, out: dict[str, int]) -> None:
    """W30 step 5: abandoned ``uploading`` jobs, and the parts no job may keep."""
    from sqlalchemy import update

    from app.models.postgres import ProjectDataJob as Job
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    upload_before = now - timedelta(hours=vocab.UPLOAD_STALE_HOURS)
    abandoned = (await db.execute(
        select(Job.id).where(Job.status == "uploading", Job.requested_at <= upload_before)
        .order_by(Job.requested_at).limit(limit)
    )).scalars().all()
    for job_id in abandoned:
        if not await _remove_parts(storage, job_id):
            out["remove_failed"] += 1
            continue
        result = await db.execute(
            update(Job).where(Job.id == job_id, Job.status == "uploading").values(
                status="failed", error_code="interrupted", error=vocab.ERROR_SENTENCES["interrupted"],
                finished_at=now, upload=None,
            ).execution_options(synchronize_session=False)
        )
        if result.rowcount:
            out["uploads_interrupted"] += 1
            out["interrupted_import"] += 1
    # The parts of a finished (or vanished) job: the runner removes them when the job ends; this is the backstop.
    try:
        listing = await storage.list_objects(vocab.STORAGE_PREFIX_IMPORT_PARTS, bucket=COMPLIANCE_BUCKET)
    except Exception as exc:  # noqa: BLE001 - a listing fault waits for the next run
        _slog.warning("project_data_sweep_list_failed", step="parts", error_type=type(exc).__name__)
        return
    parts_before = now - timedelta(hours=vocab.IMPORT_UPLOAD_TTL_HOURS)
    owners: dict[uuid.UUID, bool] = {}
    for entry in listing:
        key = str(entry.get("Key") or "")
        job_text = key[len(vocab.STORAGE_PREFIX_IMPORT_PARTS):].split("/", 1)[0]
        try:
            owner = uuid.UUID(job_text)
        except ValueError:
            continue
        if (_modified_at(entry) or now) <= parts_before:
            owners[owner] = True
    if not owners:
        return
    candidates = sorted(owners, key=str)[:limit]
    live = set((await db.execute(
        select(Job.id).where(Job.id.in_(candidates), Job.status.in_(("uploading", "queued", "running")))
    )).scalars().all())
    for owner in candidates:
        if owner in live:
            continue
        if await _remove_parts(storage, owner):
            out["parts_removed"] += 1
        else:
            out["remove_failed"] += 1


async def _sweep_ledgers(db: Any, storage: Any, *, limit: int, out: dict[str, int], mongo: Any) -> None:
    """W30 step 6 (I-H9): a FINISHED import still holding its ledger (a crash before its handler ran — the janitor
    marked it ``interrupted`` above — or a cleanup / post-commit clear that failed). Never touches a project that
    exists: the ledger's ``{project_id}/`` root and run ids are the import's own only while it never committed."""
    from sqlalchemy import null, update

    from app.models.postgres import Project
    from app.models.postgres import ProjectDataJob as Job

    rows = (await db.execute(
        select(Job.id, Job.cleanup)
        .where(Job.kind == "import", Job.cleanup.is_not(None), Job.status.in_(vocab.TERMINAL_STATUSES))
        .order_by(Job.requested_at).limit(limit)
    )).all()
    for job_id, ledger in rows:
        project_id = None
        if isinstance(ledger, dict) and ledger.get("project_id"):
            try:
                project_id = uuid.UUID(str(ledger["project_id"]))
            except ValueError:
                project_id = None
        if project_id is not None:
            exists = (await db.execute(select(Project.id).where(Project.id == project_id))).first() is not None
            if exists:
                out["ledgers_cleared"] += 1
            else:
                try:
                    if mongo is None:
                        from app.db.mongo import get_mongo_db

                        mongo = get_mongo_db()
                    from app.services.project_import_history import cleanup_outside_postgres

                    await cleanup_outside_postgres(mongo, storage, ledger)
                except Exception as exc:  # noqa: BLE001 - the ledger stays: retried next hour (idempotent)
                    out["ledger_failed"] += 1
                    _slog.warning("project_data_sweep_ledger_failed", job_id=str(job_id),
                                  error_type=type(exc).__name__)
                    continue
                out["ledgers_cleaned"] += 1
        await db.execute(
            update(Job).where(Job.id == job_id).values(cleanup=null()).execution_options(synchronize_session=False)
        )
