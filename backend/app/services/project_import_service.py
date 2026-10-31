"""Import a project export as a NEW project (W24 ADM-05 phase 1: settings + catalog).

Two entry points (contract §1.4, §2.3):

* :func:`accept_upload` — the ROUTE's work: the name check, the idempotent
  ``client_token`` lookup, the upload streamed into a spool with a running
  byte count (> 50 MiB → ``upload_too_large`` BEFORE any job row exists), the
  ZIP magic check, the object write to ``project-imports/…`` and the job row.
  The route commits, then enqueues. The API process never parses the archive.
* :func:`run_import` — the WORKER's work, on the runner's session, as ONE
  transaction the runner commits on success and rolls back on any exception:

  I-1 the upload streamed into a spool; I-2 EVERYTHING that can refuse —
  ``scan_zip`` with the import caps, the manifest's format / scope / schema
  version, the member names, ``verify_archive_file`` (chain intact; signature
  ``valid`` / ``unknown_key`` / ``unsigned``, never ``invalid``), the listed
  entities / columns / parts, and EVERY row through its typed model
  (``project_import_rows``) — plus I-3 the user directory, all BEFORE the
  first INSERT (only SELECTs run until then); I-4 the project row +
  ``provision_new_project``; I-5 the entities in ``plan()`` order with the id
  remap, batched; I-6 the self-reference second pass; I-7 the one-active-
  release invariant; I-7b the legacy ``suite_memberships`` mirror of the
  imported catalog (``suite_membership_reconcile``: a derived table, never
  exported, so the parity harness sees an imported project as clean); I-8 the
  job outcome + the ``project.imported`` activity.

An archive is attacker-controlled and never grants access: no member name is
used as a path (members are read with ``zf.open`` only), no archive id
addresses an existing row (every id is remapped or dropped), authorship maps
by USERNAME to an active user or NULL, assignments are cleared, and senders
arrive switched off (``EntitySpec.inert``). Log lines carry job / project ids,
entity names, counts and codes — never a row value, a name, a member name, a
storage key or a digest. Stage-only: no commit here.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import hashlib
import json
import re
import uuid
import zipfile
from collections import Counter
from typing import IO, Annotated, Any, Awaitable, Callable, Iterator, Mapping, Optional, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import UniqueConstraint, bindparam, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Base, Project, ProjectDataJob, User
from app.services import project_export_vocab as vocab
from app.services.project_export_entities import (
    IMPORT_NOW,
    IMPORT_NOW_PLUS_PAUSE,
    IMPORTER,
    PROJECT_ROW_COLUMNS,
    EntitySpec,
    ImportValue,
    plan,
)
from app.services.project_import_rows import row_model_for, row_values
from app.services.safe_archive import UnsafeZipError, looks_like_zip, scan_zip

_slog = structlog.get_logger(__name__)

_MANIFEST = "manifest.json"
_SIGNATURE = "signature.json"
_DIRECTORY = "directory/users.jsonl"
_README = "README.txt"
_MEMBER_NAME = re.compile(
    r"^(entities/[a-z_]{1,63}/part-\d{4}\.jsonl|directory/users\.jsonl|README\.txt|manifest\.json|signature\.json)$"
)
_PART_NAME = re.compile(r"^entities/(?P<table>[a-z_]{1,63})/part-(?P<index>\d{4})\.jsonl$")
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SLUG = re.compile(r"^[a-z0-9-]+$")
_MANIFEST_MAX_BYTES = 10_000_000          # == compliance_pack_verify.MAX_MANIFEST_BYTES
_DIRECTORY_LINE_MAX = 4 * 1024
_UPLOAD_CHUNK = 1024 * 1024
_NAME_MIN, _NAME_MAX = 2, 255             # == ProjectCreate.name (schemas.py)
_SLUG_MAX = 100                           # == Project.slug length
_LADDER_MAX = 50                          # "(imported 50)" is the last name tried
_ACCEPTED_SIGNATURES = ("valid", "unknown_key", "unsigned")
_REVIEW_RESET = "managed_test_cases.review_reset"
_RELEASES_DEACTIVATED = "releases.deactivated"

#: The HTTP status the import ROUTE answers for each refusal ``accept_upload``
#: raises (the job's own refusals never reach a route).
ROUTE_STATUS = {
    "upload_too_large": 413,
    "upload_empty": 422,
    "not_a_zip": 422,
    "name_invalid": 422,
    "client_token_invalid": 422,
    "name_taken": 409,
}


class ProjectImportFailed(Exception):
    """A refusal or failure with its ``ERROR_SENTENCES`` code.

    ``message`` is the filled sentence (it may name the project the person
    typed or the entity / file / field of a bad row — never a row value);
    ``str(exc)`` is the CODE only, so a log line that prints the exception
    leaks nothing.
    """

    def __init__(self, code: str, **fmt: Any) -> None:
        super().__init__(code)
        self.code = code
        self.fmt = fmt
        self.message = vocab.ERROR_SENTENCES[code].format(**fmt)

    @property
    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _safe_ident(value: Any, fallback: str = "an unknown field") -> str:
    """An archive-supplied identifier, only when it looks like a column name."""
    text = str(value)
    return text if _SAFE_IDENT.match(text) else fallback


def _invalid_row(entity: str, line: int, file: str, field: str) -> ProjectImportFailed:
    return ProjectImportFailed("invalid_row", entity=entity, line=line, file=file, field=field)


def _refuse_constant(token: str) -> Any:
    raise ValueError("NaN / Infinity are not JSON")


def _iter_lines(zf: zipfile.ZipFile, name: str, *, max_line: int, entity: str) -> Iterator[tuple[int, Any]]:
    """``(line number, parsed JSON object)`` per line of one member, streamed.

    A line longer than ``max_line``, a line that is not UTF-8 JSON, a JSON
    value that is not an object, NaN / Infinity → ``invalid_row`` naming the
    entity, the line and the file (the member name already passed the strict
    member-name pattern) — never the content.
    """
    with zf.open(name) as reader:                     # ZipExtFile: a buffered stream with readline(limit)
        number = 0
        while True:
            chunk = reader.readline(max_line + 1)
            if not chunk:
                return
            number += 1
            if len(chunk) > max_line and not chunk.endswith(b"\n"):
                raise _invalid_row(entity, number, name, "the row is too long")
            try:
                obj = json.loads(chunk.decode("utf-8"), parse_constant=_refuse_constant)
            except (UnicodeDecodeError, ValueError):
                raise _invalid_row(entity, number, name, "not a JSON object") from None
            if not isinstance(obj, dict):
                raise _invalid_row(entity, number, name, "not a JSON object")
            yield number, obj


def _validation_field(exc: ValidationError) -> tuple[str, str]:
    """``(error type, field NAME)`` of the first error — never its input value."""
    errors = exc.errors(include_input=False, include_url=False)
    if not errors:
        return "value_error", "an unknown field"
    first = errors[0]
    loc = first.get("loc") or ()
    return str(first.get("type", "value_error")), _safe_ident(loc[0]) if loc else "an unknown field"


# ── I-0: the upload (the route's half) ───────────────────────────────────────


class _Readable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


def parse_client_token(value: Any) -> uuid.UUID:
    """The multipart ``client_token`` as a UUID, else ``client_token_invalid``."""
    try:
        return uuid.UUID(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        raise ProjectImportFailed("client_token_invalid") from None


def clean_name(name: Optional[str]) -> Optional[str]:
    """A supplied project name, stripped: 2–255 characters, or None when absent."""
    if name is None:
        return None
    text = name.strip()
    if not text:
        return None
    if not (_NAME_MIN <= len(text) <= _NAME_MAX):
        raise ProjectImportFailed("name_invalid")
    return text


async def _taken_names(db: AsyncSession, candidates: list[str]) -> set[str]:
    """The lower-cased candidates an ACTIVE project already carries (one SELECT)."""
    lowered = sorted({c.lower() for c in candidates})
    rows = await db.execute(
        select(func.lower(Project.name)).where(Project.is_active.is_(True), func.lower(Project.name).in_(lowered))
    )
    return {str(value) for value in rows.scalars().all()}


async def check_name_free(db: AsyncSession, name: str) -> None:
    """``name_taken`` when an ACTIVE project already has ``name`` (case-insensitive)."""
    if name.lower() in await _taken_names(db, [name]):
        raise ProjectImportFailed("name_taken", name=name)


async def _existing_import(db: AsyncSession, *, actor_id: uuid.UUID, client_token: uuid.UUID) -> Optional[ProjectDataJob]:
    result = await db.execute(
        select(ProjectDataJob).where(
            ProjectDataJob.kind == "import",
            ProjectDataJob.requested_by_id == actor_id,
            ProjectDataJob.client_token == client_token,
            ProjectDataJob.status != "failed",
        )
    )
    return result.scalars().first()


async def _spool_upload(file: _Readable) -> tuple[IO[bytes], int]:
    """Stream the upload into a spool with a running count; refuses past the cap."""
    from app.services.retention_export_service import new_spool

    spool = new_spool()
    total = 0
    head = b""
    try:
        while True:
            chunk = await file.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > vocab.IMPORT_UPLOAD_MAX_BYTES:
                raise ProjectImportFailed("upload_too_large")
            if len(head) < 4:
                head += chunk[: 4 - len(head)]
            spool.write(chunk)
        if total == 0:
            raise ProjectImportFailed("upload_empty")
        if not looks_like_zip(head):
            raise ProjectImportFailed("not_a_zip")
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool, total


def upload_key(job_id: uuid.UUID, now: dt.datetime) -> str:
    """``project-imports/YYYY/MM/DD/<job_id>.zip`` in ``COMPLIANCE_BUCKET``."""
    return f"{vocab.STORAGE_PREFIX_IMPORT}{now:%Y/%m/%d}/{job_id}.zip"


async def accept_upload(
    db: AsyncSession,
    storage: Any,
    *,
    file: _Readable,
    name: Optional[str],
    client_token: uuid.UUID,
    actor: User,
) -> tuple[ProjectDataJob, bool]:
    """Accept one import upload; ``(job, created)`` — ``created`` False = the token's existing job.

    Order (contract §2.3 I-0, the token FIRST): an existing non-failed job of
    ``(actor, client_token)`` is returned with NOTHING read or stored — before
    the name check, or a re-sent upload whose ``name`` its own first import
    created would read ``name_taken`` instead of "already imported"; then the
    name check; the body is counted into a spool BEFORE ``create_import_job``
    (an oversize upload leaves no job row); ZIP magic; the object write; the
    job's storage key. Staged and flushed only — the route commits, then enqueues.
    """
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET
    from app.services.project_data_job_service import create_import_job

    existing = await _existing_import(db, actor_id=actor.id, client_token=client_token)
    if existing is not None:
        return existing, False
    supplied = clean_name(name)
    if supplied is not None:
        await check_name_free(db, supplied)
    spool, size = await _spool_upload(file)
    try:
        job, created = await create_import_job(db, actor=actor, client_token=client_token, project_name=supplied)
        if not created:
            return job, False
        key = upload_key(job.id, dt.datetime.now(dt.timezone.utc))
        await storage.put_object_stream(key, spool, size=size, content_type="application/zip", bucket=COMPLIANCE_BUCKET)
        job.storage_key = key
        job.bytes = size
        await db.flush()
    finally:
        spool.close()
    _slog.info("project_import_upload_accepted", job_id=str(job.id), bytes=size)
    return job, True


# ── I-2: everything that can refuse, before the first write ─────────────────

_Str255 = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class _ManifestProject(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: _Str255
    slug: _Str255


class _ManifestEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rows: int = Field(ge=0)
    parts: int = Field(default=0, ge=0, le=9999)
    columns: Optional[list[str]] = None


class _ManifestSource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    app_version: Optional[Annotated[str, StringConstraints(max_length=100)]] = None


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    generated_at: dt.datetime
    project: _ManifestProject
    entities: dict[str, _ManifestEntity]
    source: Optional[_ManifestSource] = None


class _DirectoryUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    username: _Str255
    full_name: Optional[Annotated[str, StringConstraints(max_length=255)]] = None


@dataclasses.dataclass
class _Preflight:
    """What I-2 proved about the archive; nothing here came from an unverified member."""

    manifest_sha256: str
    signature: str
    source_name: str
    source_slug: str
    source_app_version: Optional[str]
    exported_at: dt.datetime
    file_count: int
    parts: dict[str, list[str]] = dataclasses.field(default_factory=dict)       # table → member names, in order
    rows: dict[str, int] = dataclasses.field(default_factory=dict)              # table → validated rows
    directory: dict[uuid.UUID, str] = dataclasses.field(default_factory=dict)   # source user id → lower(username)
    release_keeper: Optional[uuid.UUID] = None                                  # the imported release that stays active


def _scan(spool: IO[bytes]) -> int:
    """(a) the safe scan with the import caps; returns the member count."""
    try:
        members = scan_zip(
            spool,
            max_total=vocab.IMPORT_MAX_TOTAL_UNCOMPRESSED,
            max_entries=vocab.IMPORT_MAX_ENTRIES,
            max_entry=vocab.IMPORT_MAX_ENTRY_UNCOMPRESSED,
            max_ratio=vocab.IMPORT_MAX_RATIO,
        )
    except UnsafeZipError as exc:
        if exc.code == "bad_zip":
            raise ProjectImportFailed("not_a_zip") from None
        raise ProjectImportFailed("unsafe_archive", message=exc.message) from None
    return len(members)


def _read_manifest(zf: zipfile.ZipFile) -> tuple[dict[str, Any], str]:
    """(b) the manifest's kind and schema version — read before the member names
    so a retention archive reads ``not_a_project_export`` and a newer export
    reads ``schema_newer`` instead of "an unexpected member"."""
    try:
        info = zf.getinfo(_MANIFEST)
    except KeyError:
        raise ProjectImportFailed("not_a_project_export") from None
    with zf.open(info) as fh:
        raw = fh.read(_MANIFEST_MAX_BYTES + 1)
    if len(raw) > _MANIFEST_MAX_BYTES:
        raise ProjectImportFailed("not_a_project_export")
    try:
        manifest = json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError):
        raise ProjectImportFailed("not_a_project_export") from None
    if not isinstance(manifest, dict):
        raise ProjectImportFailed("not_a_project_export")
    if manifest.get("format") != vocab.ARCHIVE_FORMAT or manifest.get("scope") != "project":
        raise ProjectImportFailed("not_a_project_export")
    version = manifest.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProjectImportFailed("schema_unknown")
    if version > max(vocab.SUPPORTED_SCHEMA_VERSIONS):
        raise ProjectImportFailed(
            "schema_newer", found=version, supported=", ".join(str(v) for v in vocab.SUPPORTED_SCHEMA_VERSIONS)
        )
    if version not in vocab.SUPPORTED_SCHEMA_VERSIONS:
        raise ProjectImportFailed("schema_unknown")
    return manifest, hashlib.sha256(raw).hexdigest()


def _check_member_names(zf: zipfile.ZipFile) -> list[str]:
    """(c) every entry — directories included — has one of the five shapes."""
    names = [info.filename for info in zf.infolist()]
    if any(not _MEMBER_NAME.match(name) for name in names):
        raise ProjectImportFailed("tampered", reason="an unexpected member")
    return names


def _tamper_reason(result: Mapping[str, Any]) -> str:
    reason = result.get("reason")
    if reason == "manifest_unreadable":
        return "the manifest is unreadable"
    if reason == "manifest_missing":
        return "the manifest is missing"
    statuses = {f.get("status") for f in result.get("files") or [] if isinstance(f, Mapping)}
    if "mismatch" in statuses:
        return "a member was changed"
    if "missing" in statuses:
        return "a listed member is missing"
    if "unexpected" in statuses:
        return "an unexpected member"
    return "the chain is broken"


def _verify(spool: IO[bytes], public_keys: Mapping[str, str]) -> str:
    """(d) the ONE verifier: chain intact; ``invalid`` refused; returns the signature."""
    from app.services.compliance_pack_verify import verify_archive_file

    result = verify_archive_file(spool, recorded_manifest_sha256=None, public_keys=public_keys, pack_id=None)
    if result.get("chain") != "intact":
        raise ProjectImportFailed("tampered", reason=_tamper_reason(result))
    signature = str(result.get("signature"))
    if signature not in _ACCEPTED_SIGNATURES:
        raise ProjectImportFailed("signature_invalid")
    return signature


def _check_entities(raw: dict[str, Any], names: list[str]) -> tuple[_Manifest, dict[str, list[str]]]:
    """(e) every listed entity is EXPORTED, every listed column known, every listed part present."""
    from app.services.project_import_rows import column_names

    try:
        manifest = _Manifest.model_validate(raw)
    except ValidationError:
        raise ProjectImportFailed("not_a_project_export") from None
    exported = {spec.table: spec for spec in plan()}
    by_table: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        match = _PART_NAME.match(name)
        if match:
            by_table.setdefault(match["table"], []).append((int(match["index"]), name))
    for table in sorted(set(manifest.entities) | set(by_table)):
        if table not in exported:
            raise ProjectImportFailed("unknown_entity", entity=_safe_ident(table, "an unknown table"))
    for table, entry in sorted(manifest.entities.items()):
        if entry.columns is not None:
            known = column_names(row_model_for(exported[table]))
            for column in entry.columns:
                if column not in known:
                    raise ProjectImportFailed(
                        "unknown_column", entity=table, column=_safe_ident(column, "an unknown column")
                    )
    parts: dict[str, list[str]] = {}
    for table in sorted(set(manifest.entities) | set(by_table)):
        listed = manifest.entities.get(table)
        if listed is None:
            raise ProjectImportFailed("tampered", reason="an unexpected member")
        expected = [f"entities/{table}/part-{i:04d}.jsonl" for i in range(1, listed.parts + 1)]
        present = [name for _, name in sorted(by_table.get(table, []))]
        if present != expected:
            missing = set(expected) - set(present)
            raise ProjectImportFailed("tampered", reason="a listed part is missing" if missing else "an unexpected member")
        parts[table] = expected
    if "projects" not in manifest.entities:
        raise ProjectImportFailed("not_a_project_export")
    return manifest, parts


def _single_row_tables() -> frozenset[str]:
    """Exported tables UNIQUE on ``project_id`` alone (one settings row per project)."""
    out: set[str] = set()
    for spec in plan():
        table = Base.metadata.tables[spec.table]
        keys = [
            [c.name for c in constraint.columns]
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        keys += [
            [c.name for c in index.columns]
            for index in table.indexes
            if index.unique and index.dialect_options["postgresql"].get("where") is None
        ]
        if ["project_id"] in keys:
            out.add(spec.table)
    return frozenset(out)


def _key_digest(values: tuple[Any, ...]) -> bytes:
    return hashlib.blake2b(json.dumps(values, default=str, sort_keys=True).encode("utf-8"), digest_size=16).digest()


def _validate_rows(zf: zipfile.ZipFile, manifest: _Manifest, pre: _Preflight) -> None:
    """(f) EVERY row through its typed model before the first INSERT: types, unknown
    columns, over-long values, repeated ids / natural keys, one-per-project settings,
    the row counts the manifest states. Keeps keys and counts, never rows."""
    single = _single_row_tables()
    for spec in plan():
        model = row_model_for(spec)
        ids_seen: set[uuid.UUID] = set()
        keys_seen: set[bytes] = set()
        active: list[tuple[str, str, uuid.UUID]] = []
        count = 0
        for name in pre.parts.get(spec.table, []):
            for line, obj in _iter_lines(zf, name, max_line=vocab.ROW_BYTES_MAX, entity=spec.table):
                try:
                    values = row_values(model.model_validate(obj))
                except ValidationError as exc:
                    kind, field = _validation_field(exc)
                    if kind == "extra_forbidden" and field != "an unknown field":
                        raise ProjectImportFailed("unknown_column", entity=spec.table, column=field) from None
                    raise _invalid_row(spec.table, line, name, field) from None
                count += 1
                if (spec.table in single or spec.table == "projects") and count > 1:
                    raise _invalid_row(spec.table, line, name, "a second row of a one-per-project setting")
                source_id = values.get("id")
                if isinstance(source_id, uuid.UUID):
                    if source_id in ids_seen:
                        raise _invalid_row(spec.table, line, name, "duplicate id")
                    ids_seen.add(source_id)
                if spec.natural_key:
                    digest = _key_digest(tuple(values.get(c) for c in spec.natural_key))
                    if digest in keys_seen:
                        raise _invalid_row(spec.table, line, name, "duplicate " + "/".join(spec.natural_key))
                    keys_seen.add(digest)
                if spec.table == "releases" and values.get("is_active") and isinstance(source_id, uuid.UUID):
                    stamp = values.get("activated_at")
                    active.append((stamp.isoformat() if stamp else "", str(source_id), source_id))
        listed = manifest.entities.get(spec.table)
        if listed is not None and listed.rows != count:
            raise ProjectImportFailed("tampered", reason="a row count differs from the manifest")
        pre.rows[spec.table] = count
        if active:
            pre.release_keeper = max(active)[2]   # the latest activated_at stays active (I-7)
    if pre.rows.get("projects") != 1:
        raise _invalid_row("projects", 1, "entities/projects/part-0001.jsonl", "exactly one project row")


def _read_directory(zf: zipfile.ZipFile, names: list[str]) -> dict[uuid.UUID, str]:
    """I-3: ``directory/users.jsonl`` → source user id → lower(username). Nothing else is trusted."""
    if _DIRECTORY not in names:
        return {}
    out: dict[uuid.UUID, str] = {}
    for line, obj in _iter_lines(zf, _DIRECTORY, max_line=_DIRECTORY_LINE_MAX, entity="users"):
        try:
            user = _DirectoryUser.model_validate(obj)
        except ValidationError as exc:
            raise _invalid_row("users", line, _DIRECTORY, _validation_field(exc)[1]) from None
        if user.id in out:
            raise _invalid_row("users", line, _DIRECTORY, "duplicate id")
        out[user.id] = user.username.lower()
    return out


async def _map_users(db: AsyncSession, directory: Mapping[uuid.UUID, str]) -> dict[uuid.UUID, uuid.UUID]:
    """source user id → an ACTIVE target user with the same username (case-insensitive), by SELECTs only."""
    names = sorted(set(directory.values()))
    target: dict[str, uuid.UUID] = {}
    for start in range(0, len(names), 1000):
        rows = await db.execute(
            select(User.id, func.lower(User.username))
            .where(func.lower(User.username).in_(names[start:start + 1000]), User.is_active.is_(True))
            .order_by(User.id)
        )
        for user_id, username in rows.all():
            target.setdefault(str(username), user_id)
    return {source: target[name] for source, name in directory.items() if name in target}


def _preflight_sync(spool: IO[bytes], public_keys: Mapping[str, str], file_count: int) -> _Preflight:
    """(b)–(f) + the directory, on the spool, in order; raises the first refusal."""
    spool.seek(0)
    with zipfile.ZipFile(spool) as zf:
        raw, manifest_sha256 = _read_manifest(zf)
        names = _check_member_names(zf)
    signature = _verify(spool, public_keys)
    spool.seek(0)
    with zipfile.ZipFile(spool) as zf:
        manifest, parts = _check_entities(raw, names)
        pre = _Preflight(
            manifest_sha256=manifest_sha256,
            signature=signature,
            source_name=manifest.project.name,
            source_slug=manifest.project.slug,
            source_app_version=manifest.source.app_version if manifest.source else None,
            exported_at=manifest.generated_at,
            file_count=file_count,
            parts=parts,
        )
        _validate_rows(zf, manifest, pre)
        pre.directory = _read_directory(zf, names)
    return pre


# ── I-4: the name, the slug, the project ─────────────────────────────────────


def name_candidates(source: str) -> list[str]:
    """``<source> (imported)``, ``<source> (imported 2)`` … ``(imported 50)``, each ≤ 255 characters."""
    suffixes = [vocab.IMPORTED_NAME_SUFFIX] + [f" (imported {n})" for n in range(2, _LADDER_MAX + 1)]
    return [source[: _NAME_MAX - len(suffix)].rstrip() + suffix for suffix in suffixes]


def slug_candidates(source_slug: str) -> list[str]:
    """``<slug>-imported``, ``-imported-2`` … — a slug that fails ``^[a-z0-9-]+$`` is re-slugified first."""
    base = source_slug if _SLUG.match(source_slug) else re.sub(r"[^a-z0-9]+", "-", source_slug.lower()).strip("-")
    base = base or "project"
    suffixes = [vocab.IMPORTED_SLUG_SUFFIX] + [f"{vocab.IMPORTED_SLUG_SUFFIX}-{n}" for n in range(2, _LADDER_MAX + 1)]
    return [(base[: _SLUG_MAX - len(suffix)].rstrip("-") or "project") + suffix for suffix in suffixes]


def pick_free(candidates: list[str], taken_lower: set[str]) -> Optional[str]:
    """The first candidate whose lower-cased form is not taken."""
    return next((c for c in candidates if c.lower() not in taken_lower), None)


async def choose_name(db: AsyncSession, *, requested: Optional[str], source_name: str) -> str:
    """A supplied name verbatim (``name_taken`` when an ACTIVE project has it), else the ladder."""
    if requested is not None:
        await check_name_free(db, requested)
        return requested
    candidates = name_candidates(source_name)
    chosen = pick_free(candidates, await _taken_names(db, candidates))
    if chosen is None:
        raise ProjectImportFailed("name_taken", name=candidates[0])
    return chosen


async def choose_slug(db: AsyncSession, *, source_slug: str) -> str:
    """The first free slug of the ladder across ALL projects (archived ones keep theirs)."""
    candidates = slug_candidates(source_slug)
    rows = await db.execute(select(Project.slug).where(Project.slug.in_(candidates)))
    chosen = pick_free(candidates, {str(s).lower() for s in rows.scalars().all()})
    if chosen is None:
        suffix = "-" + uuid.uuid4().hex[:6]
        chosen = candidates[0][: _SLUG_MAX - len(suffix)] + suffix
    return chosen


# ── I-5: one row at a time through the remap ─────────────────────────────────


@dataclasses.dataclass
class _Context:
    project_id: uuid.UUID
    actor_id: uuid.UUID
    now: dt.datetime
    user_map: dict[uuid.UUID, uuid.UUID]
    release_keeper: Optional[uuid.UUID]
    remap: dict[str, dict[uuid.UUID, uuid.UUID]] = dataclasses.field(default_factory=dict)
    self_pending: dict[tuple[str, str], list[tuple[uuid.UUID, uuid.UUID]]] = dataclasses.field(default_factory=dict)
    inserted: Counter[str] = dataclasses.field(default_factory=Counter)
    skipped: Counter[tuple[str, Optional[str], str]] = dataclasses.field(default_factory=Counter)
    cleared: Counter[tuple[str, Optional[str], str]] = dataclasses.field(default_factory=Counter)
    switched_off: Counter[str] = dataclasses.field(default_factory=Counter)
    active_release_kept: bool = False

    def resolve(self, value: Any) -> Any:
        """An ``inert`` value; ``ImportValue`` sentinels become this import's clock / importer."""
        if not isinstance(value, ImportValue):
            return value
        if value == IMPORT_NOW:
            return self.now
        if value == IMPORT_NOW_PLUS_PAUSE:
            return self.now + dt.timedelta(days=vocab.IMPORTED_PAUSE_DAYS)
        if value == IMPORTER:
            return self.actor_id
        raise ValueError(f"unknown import value {value.name}")


def _entity_rules(ctx: _Context, spec: EntitySpec, values: dict[str, Any], source_id: Any) -> None:
    """The per-entity rules of contract §2.1 that are not plain column specs."""
    if spec.table == "managed_test_cases" and values.get("status") in vocab.REVIEW_RESET_STATES:
        values["status"] = "draft"                          # row 3: review requests are not exported
        ctx.switched_off[_REVIEW_RESET] += 1
    elif spec.table == "releases" and values.get("is_active"):
        if source_id == ctx.release_keeper and not ctx.active_release_kept:
            ctx.active_release_kept = True                  # I-7: the latest activated release stays active
        else:
            values["is_active"] = False
            ctx.switched_off[_RELEASES_DEACTIVATED] += 1
    elif spec.table == "workflow_definitions" and isinstance(values.get("definition"), dict):
        definition = dict(values["definition"])             # the stored document names its project
        if "project_id" in definition:
            definition["project_id"] = str(ctx.project_id)
        values["definition"] = definition
    elif spec.table == "dismissed_duplicate_pairs":
        a, b = values.get("case_a_id"), values.get("case_b_id")
        if isinstance(a, uuid.UUID) and isinstance(b, uuid.UUID) and a > b:
            values["case_a_id"], values["case_b_id"] = b, a  # ck_dismissed_dup_canonical_order on the NEW ids


def transform_row(ctx: _Context, spec: EntitySpec, values: dict[str, Any]) -> Optional[dict[str, Any]]:
    """One validated archive row → the row to INSERT, or None when it is skipped.

    FKs through the remap (an unresolved REQUIRED fk skips the row, an optional
    one becomes NULL); authorship by username (miss → NULL); assignments
    cleared; self references NULL until the second pass; ``project_id`` = the
    NEW project; the inert values forced; a fresh id remembered in the remap.
    No archive id ever reaches the database.
    """
    table = Base.metadata.tables[spec.table]
    for column, parent in spec.fks.items():
        ref = values.get(column)
        if ref is None:
            continue
        target = ctx.remap.get(parent, {}).get(ref)
        if target is not None:
            values[column] = target
        elif not table.c[column].nullable:
            ctx.skipped[(spec.table, column, "row_unresolved")] += 1
            return None
        else:
            values[column] = None
            ctx.cleared[(spec.table, column, "row_unresolved")] += 1
    for column in spec.user_refs:
        ref = values.get(column)
        if ref is None:
            continue
        mapped = ctx.user_map.get(ref)
        if mapped is None:
            ctx.cleared[(spec.table, column, "user_not_found")] += 1
            if not table.c[column].nullable:
                # release_gate_policies.created_by is NOT NULL: the importer is
                # the only person this deployment can vouch for (counted alike).
                mapped = ctx.actor_id
        values[column] = mapped
    for column in spec.cleared:
        if values.get(column) is not None:
            ctx.cleared[(spec.table, column, "assignment")] += 1
        values[column] = None
    self_refs = {column: values.get(column) for column in spec.self_fks}
    for column in spec.self_fks:
        values[column] = None
    if "project_id" in table.c:
        values["project_id"] = ctx.project_id
    differed = False
    for column, forced in spec.inert.items():
        value = ctx.resolve(forced)
        differed = differed or values.get(column) != value
        values[column] = value
    if differed:
        ctx.switched_off[spec.table] += 1
    source_id = values.get("id")
    _entity_rules(ctx, spec, values, source_id)
    if "id" in table.c and table.c["id"].primary_key:
        new_id = uuid.uuid4()
        values["id"] = new_id
        if isinstance(source_id, uuid.UUID):
            ctx.remap.setdefault(spec.table, {})[source_id] = new_id
        for column, ref in self_refs.items():
            if isinstance(ref, uuid.UUID):
                ctx.self_pending.setdefault((spec.table, column), []).append((new_id, ref))
    return values


def _bind_type(column: Any) -> Any:
    """A JSON column binds Python ``None`` as SQL NULL (not the JSON ``null`` literal)."""
    from sqlalchemy import JSON

    if isinstance(column.type, JSON):
        return type(column.type)(none_as_null=True)
    return column.type


async def _insert_batch(db: AsyncSession, table: Any, rows: list[dict[str, Any]]) -> None:
    """One executemany INSERT per distinct column set (an older archive may omit columns)."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(tuple(sorted(row)), []).append(row)
    for keys, group in groups.items():
        statement = insert(table).values({key: bindparam(f"p_{key}", type_=_bind_type(table.c[key])) for key in keys})
        await db.execute(statement, [{f"p_{key}": row[key] for key in keys} for row in group])


async def _second_pass(db: AsyncSession, ctx: _Context) -> None:
    """I-6: set every self reference through the remap, in batches."""
    for (table_name, column), pairs in sorted(ctx.self_pending.items()):
        table = Base.metadata.tables[table_name]
        resolved: list[dict[str, Any]] = []
        for new_id, ref in pairs:
            target = ctx.remap.get(table_name, {}).get(ref)
            if target is None:
                ctx.cleared[(table_name, column, "row_unresolved")] += 1
            else:
                resolved.append({"p_id": new_id, "p_value": target})
        statement = update(table).where(table.c.id == bindparam("p_id")).values({column: bindparam("p_value")})
        for start in range(0, len(resolved), vocab.INSERT_BATCH_ROWS):
            await db.execute(statement, resolved[start:start + vocab.INSERT_BATCH_ROWS])


async def _create_project(
    db: AsyncSession, zf: zipfile.ZipFile, ctx: _Context, *, spec: EntitySpec, pre: _Preflight,
    name: str, slug: str, actor: User,
) -> tuple[Project, list[uuid.UUID]]:
    """I-4: the project row from ``entities/projects`` + the SAME provisioning ``create_project`` runs."""
    from app.services.project_provisioning import provision_new_project

    model = row_model_for(spec)
    part = pre.parts["projects"][0]
    values: dict[str, Any] = {}
    for _line, obj in _iter_lines(zf, part, max_line=vocab.ROW_BYTES_MAX, entity="projects"):
        values = row_values(model.model_validate(obj))
    for column in spec.cleared:
        if values.get(column) is not None:
            ctx.cleared[("projects", column, "assignment")] += 1
    settings = {column: values[column] for column in PROJECT_ROW_COLUMNS if column in values}
    project = Project(id=ctx.project_id, name=name, slug=slug, **settings)
    db.add(project)
    await db.flush()
    enrolled = await provision_new_project(
        db, project=project, creator=actor, provision_release=False,
        activity_context={"imported_from": pre.source_name},
    )
    return project, enrolled


async def _insert_entity(db: AsyncSession, zf: zipfile.ZipFile, ctx: _Context, spec: EntitySpec, pre: _Preflight) -> None:
    """I-5 for one entity: its parts line by line, batched executemany INSERTs."""
    table = Base.metadata.tables[spec.table]
    model = row_model_for(spec)
    batch: list[dict[str, Any]] = []
    for part in pre.parts.get(spec.table, []):
        for _line, obj in _iter_lines(zf, part, max_line=vocab.ROW_BYTES_MAX, entity=spec.table):
            row = transform_row(ctx, spec, row_values(model.model_validate(obj)))
            if row is None:
                continue
            batch.append(row)
            if len(batch) >= vocab.INSERT_BATCH_ROWS:
                await _insert_batch(db, table, batch)
                ctx.inserted[spec.table] += len(batch)
                batch = []
    if batch:
        await _insert_batch(db, table, batch)
        ctx.inserted[spec.table] += len(batch)


def _skips(counter: Counter[tuple[str, Optional[str], str]]) -> list[Any]:
    from app.models.schemas import ProjectDataSkip

    return [
        ProjectDataSkip(entity=entity, column=column, reason=reason, count=count)
        for (entity, column, reason), count in sorted(counter.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2]))
    ]


async def run_import(
    db: AsyncSession,
    *,
    job: ProjectDataJob,
    storage: Any,
    actor: User,
    now: dt.datetime,
    on_table: Callable[[int, int], Awaitable[None]],
    on_manifest: Optional[Callable[[str, int], Awaitable[None]]] = None,
    outcome: Optional[dict[str, Any]] = None,
) -> Any:
    """Import ``job``'s upload as a NEW project on ``db`` — ONE transaction the CALLER commits.

    Returns the ``ProjectDataImportReport``. Raises ``ProjectImportFailed(code)``
    for every refusal; until I-2 / I-3 complete only SELECTs run on ``db``. The
    job row is set ``succeeded`` IN the transaction (I-8), so "succeeded" and
    "the project exists" can never disagree. ``on_manifest(source_name, tables)``
    (optional) runs once the archive is proven, for the runner's fresh-session
    ``project_name`` / progress write; ``outcome`` (optional) receives
    ``enrolled_service_account_ids`` for the post-commit cache invalidation.
    """
    from app.models.schemas import ProjectDataImportReport
    from app.services import compliance_signing
    from app.services.activity.service import ActorRef
    from app.services.activity.service import record as record_activity
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET, sha256_of_file
    from app.services.release_lifecycle_service import ensure_active_release_for_new_project
    from app.services.retention_export_service import new_spool
    from app.services.suite_membership_reconcile import reconcile_project

    requested = clean_name(job.project_name)
    specs = plan()
    total = len(specs)
    if not job.storage_key:
        raise ProjectImportFailed("interrupted")
    spool = new_spool()
    try:
        try:                                                        # I-1
            async for chunk in storage.stream_object(job.storage_key, bucket=COMPLIANCE_BUCKET):
                spool.write(chunk)
        except Exception:  # noqa: BLE001 - a missing / unreadable upload: nothing was kept
            raise ProjectImportFailed("interrupted") from None
        size = spool.tell()
        if size > vocab.IMPORT_UPLOAD_MAX_BYTES:
            raise ProjectImportFailed("upload_too_large")
        archive_sha256 = await asyncio.to_thread(sha256_of_file, spool)
        file_count = await asyncio.to_thread(_scan, spool)          # I-2 (a)
        public_keys = await compliance_signing.public_keys(db)
        pre = await asyncio.to_thread(_preflight_sync, spool, public_keys, file_count)
        user_map = await _map_users(db, pre.directory)              # I-3
        name = await choose_name(db, requested=requested, source_name=pre.source_name)
        slug = await choose_slug(db, source_slug=pre.source_slug)
        if on_manifest is not None:
            await on_manifest(pre.source_name, total)
        ctx = _Context(
            project_id=uuid.uuid4(), actor_id=actor.id, now=now, user_map=user_map,
            release_keeper=pre.release_keeper,
        )
        spool.seek(0)
        with zipfile.ZipFile(spool) as zf:                         # the first write is below this line
            project, enrolled = await _create_project(
                db, zf, ctx, spec=specs[0], pre=pre, name=name, slug=slug, actor=actor,
            )
            ctx.inserted["projects"] = 1
            await on_table(1, total)
            for done, spec in enumerate(specs[1:], start=2):        # I-5
                await _insert_entity(db, zf, ctx, spec, pre)
                await on_table(done, total)
        await _second_pass(db, ctx)                                 # I-6
        if not ctx.active_release_kept:                             # I-7
            await ensure_active_release_for_new_project(db, project)
        # I-7b: suite_memberships is derived (never exported), so the legacy table mirrors the imported
        # catalog here, in this transaction — else every import is a parity missing_in_legacy (W25a Day-0).
        mirrored = await reconcile_project(db, project.id, apply=True)
    finally:
        spool.close()

    inserted = {spec.table: ctx.inserted.get(spec.table, 0) for spec in specs}
    report = ProjectDataImportReport(
        project_id=project.id, project_name=project.name, project_slug=project.slug,
        inserted=inserted, skipped=_skips(ctx.skipped), cleared=_skips(ctx.cleared),
        switched_off=dict(sorted(ctx.switched_off.items())), signature=pre.signature,
        source_project_name=pre.source_name, source_app_version=pre.source_app_version,
        exported_at=pre.exported_at,
    )
    row = await db.get(ProjectDataJob, job.id)                      # I-8, same transaction
    if row is None:
        raise ProjectImportFailed("interrupted")
    row.project_id = project.id
    row.project_name = pre.source_name
    row.status = "succeeded"
    row.report = report.model_dump(mode="json")
    row.counts = inserted
    row.finished_at = now
    row.bytes = size
    row.file_count = pre.file_count
    row.manifest_sha256 = pre.manifest_sha256
    row.archive_sha256 = archive_sha256
    row.signature = pre.signature
    row.progress = {"done": total, "total": total}
    await record_activity(
        db,
        project_id=project.id,
        event_type="project.imported",
        actor=ActorRef.from_user(actor),
        entity_id=project.id,
        entity_label=project.name,
        context={"source": pre.source_name, "rows": sum(inserted.values())},
    )
    await db.flush()
    if outcome is not None:
        outcome["enrolled_service_account_ids"] = list(enrolled)
    _slog.info(
        "project_import_succeeded", job_id=str(job.id), project_id=str(project.id),
        rows=sum(inserted.values()), tables=total, signature=pre.signature,
        legacy_memberships=mirrored.legacy_inserted,
    )
    return report
