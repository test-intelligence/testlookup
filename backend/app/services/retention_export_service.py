"""Export before delete (W22b, ACT-03): estimate, build, write, re-read, verify.

A criteria deletion may first build an archive of the runs it is about to
delete. The rules this module exists to keep:

* **Nothing is deleted on an unproven archive.** ``run_export`` builds the
  archive into a spooled file, writes it with a streaming upload, RE-READS it
  from storage into a second spool and verifies it (whole-object SHA-256 equal
  to the one taken while writing, the manifest chain intact, the manifest
  digest equal to the recorded one, the signature valid -- or unsigned when
  the job recorded unsigned). Any failure raises ``ExportFailed``; the worker
  then ends the job ``failed`` and deletes nothing. A slow storage read is
  retried (``EXPORT_VERIFY_ATTEMPTS``) and is never counted as verified.
* **One archive builder.** The archive is assembled by
  ``compliance_pack_service.build_archive`` and signed by ``_sign_pack`` --
  the release pack's chain, signature and verifier. **An archive, not a
  backup -- it cannot be re-imported.**
* **Holds are read once, through W22a's hold service** (``holds_covering_runs``)
  -- never a second hold check; a failed read fails the export (fail closed).
* **No storage key leaves the server.** ``ExportOutcome.location`` goes to the
  job row only; nothing here logs a key, a digest, a run name or a hold
  reason (ids, counts and byte totals only).

Stage-only: no commit here (``STAGE_ONLY_SERVICES``). The worker owns the
export session and commits it; the estimate is a read.
"""
from __future__ import annotations

import asyncio
import bisect
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import IO, Any, AsyncIterator, Awaitable, Callable, Mapping, Optional, Sequence

import structlog
from sqlalchemy import any_, bindparam, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.db.mongo import Collections
from app.models.postgres import TestCase, TestRun
from app.services.retention_vocab import (
    ARCHIVE_NOT_BACKUP,
    EXPORT_ERROR_SENTENCES,
    EXPORT_JOB_ERRORS,
    EXPORT_MAX_RUNS,
    EXPORT_SPOOL_BYTES,
    EXPORT_TARGETS,
    EXPORT_TEXT_OBJECT_MAX_BYTES,
    EXPORT_TEXT_SUFFIXES,
    EXPORT_TIERS,
    EXPORT_VERIFY_ATTEMPTS,
)

_slog = structlog.get_logger(__name__)

# ── Estimate formulas (E-2): module constants, every figure labelled an estimate ──
SUMMARY_BYTES_PER_RUN = 2048
SUMMARY_BYTES_PER_CASE = 256
EVIDENCE_BYTES_PER_FAILED_CASE = 4096
EVIDENCE_BYTES_PER_RUN = 32768

#: Mongo ids per ``count_documents`` (the storage-accounting chunk).
MONGO_ID_CHUNK = 500
#: Collections a run's deletion removes (``test_run_id`` keyed) -- the reclaim
#: estimate's Mongo share (docs x ``collStats.avgObjSize``).
RECLAIM_MONGO_COLLECTIONS = (
    Collections.RAW_ALLURE_JSON,
    Collections.EXECUTION_LOGS,
    Collections.OCP_POD_EVENTS,
    Collections.RUN_SUMMARIES,
    Collections.AI_RUN_VERSIONS,
    Collections.DECISION_EVIDENCE_SNAPSHOTS,
)
#: The run-keyed raw collections a ``full`` archive copies (the estimate's raw
#: share; the test-case keyed ones are copied but not estimated -- counting them
#: would need every test case id, beyond the estimate's one Postgres statement).
FULL_RAW_RUN_COLLECTIONS = (Collections.RAW_ALLURE_JSON, Collections.EXECUTION_LOGS)
#: A preview must not wait a minute on an unreachable store: past this, the
#: part is "not measured" (absence is not health -- never 0).
ESTIMATE_STEP_TIMEOUT_SECONDS = 10.0
#: Re-read backoff between attempts (E-5): 30 s, 60 s, 120 s.
VERIFY_BACKOFF_SECONDS = (30, 60, 120)
_FAILED_CASE_STATUSES = ("FAILED", "BROKEN")


class ExportRefused(Exception):
    """A request-path refusal: ``code`` is an ``EXPORT_ERROR_SENTENCES`` key."""

    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(EXPORT_ERROR_SENTENCES[code])
        self.code = code
        self.status_code = status_code
        self.message = EXPORT_ERROR_SENTENCES[code]

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class ExportFailed(Exception):
    """The export phase failed; NOTHING may be deleted.

    ``code`` is an ``EXPORT_JOB_ERRORS`` key and ``message`` its formatted
    sentence (the job's ``error``; every one says nothing was deleted).
    """

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        self.message = EXPORT_JOB_ERRORS[code].format(**params)
        #: The live holds the export had already excluded (``holds_honoured``).
        self.holds: list[Any] = []
        self.held_run_ids: list[str] = []
        super().__init__(self.message)


@dataclass
class ExportOutcome:
    """What a verified export produced. ``location`` is for the job row ONLY."""

    location: str
    manifest_sha256: str
    archive_sha256: str
    bytes: int
    file_count: int
    signing: dict
    run_ids_archived: list[uuid.UUID]
    run_ids_excluded: list[uuid.UUID]
    missing_run_ids: list[uuid.UUID]
    holds: list[Any]
    objects_not_archived: int
    verify_attempts: int = 1
    signature: Optional[str] = None
    signing_key_id: Optional[str] = None
    holds_by_run: dict[str, list[Any]] = field(default_factory=dict)

    def export_meta(self) -> dict[str, Any]:
        """The ``DeletionJob.export_meta`` facts (never the storage key)."""
        return {
            "run_count": len(self.run_ids_archived),
            "run_ids_excluded": sorted(str(r) for r in self.run_ids_excluded),
            "missing_run_ids": sorted(str(r) for r in self.missing_run_ids),
            "file_count": self.file_count,
            "objects_not_archived": self.objects_not_archived,
            "signing": dict(self.signing),
            "verify_attempts": self.verify_attempts,
            # The holds that kept runs out of the archive and the delete set,
            # so the post-acknowledgement delivery can record them too.
            "holds_excluded": [ref.as_json() for ref in self.holds],
        }


def hold_refs_from_meta(meta: Optional[Mapping[str, Any]]) -> list[Any]:
    """The ``HoldRef`` objects ``export_meta["holds_excluded"]`` recorded."""
    from app.services.retention_hold_service import HoldRef

    refs = []
    for item in (meta or {}).get("holds_excluded") or []:
        if not isinstance(item, Mapping) or not item.get("hold_id"):
            continue
        expires = item.get("expires_at")
        refs.append(HoldRef(
            id=uuid.UUID(str(item["hold_id"])),
            entity_type=str(item.get("entity_type") or ""),
            entity_id=uuid.UUID(str(item["entity_id"])) if item.get("entity_id") else None,
            entity_label=item.get("entity_label"),
            reason=str(item.get("reason") or ""),
            expires_at=datetime.fromisoformat(expires) if isinstance(expires, str) else None,
        ))
    return refs


# ── Object listings (E-2, E-9): the project's two roots, listed ONCE each ──


def _project_roots(project_id: Any) -> tuple[str, str]:
    """The only roots ``run_deletion_service.safe_artifact_prefixes`` accepts."""
    pid = str(project_id)
    return (f"uploads/{pid}/", f"{pid}/")


async def list_project_objects(storage: Any, *, project_id: Any) -> list[dict]:
    """Every object under the project's two roots, sorted by ``Key``.

    ONE ``list_objects`` per root (each paginates inside the provider), never
    one per run. Raises on an outage: the callers decide what "not measured"
    means (the estimate fails soft, ``bytes_reclaimed`` stays NULL, a ``full``
    archive fails -- it cannot list what it must copy).
    """
    seen: dict[str, dict] = {}
    for root in _project_roots(project_id):
        for obj in await storage.list_objects(root):
            key = obj.get("Key") if isinstance(obj, dict) else None
            if isinstance(key, str) and key.startswith(root):
                seen.setdefault(key, {"Key": key, "Size": int(obj.get("Size") or 0)})
    return [seen[key] for key in sorted(seen)]


def is_text_object(key: str, size: int) -> bool:
    return key.lower().endswith(EXPORT_TEXT_SUFFIXES) and size <= EXPORT_TEXT_OBJECT_MAX_BYTES


def run_object_keys(listing: Sequence[dict], *, project_id: Any, run_id: Any,
                    minio_prefix: Optional[str]) -> list[tuple[str, int, str]]:
    """``(key, size, prefix)`` of every listed object under this run's SAFE
    prefixes (RET-D9 -- a prefix outside the project scope is never counted or
    copied), each key once, in key order. ``listing`` is sorted by ``Key``
    (``list_project_objects``' order): each prefix is a bisected range."""
    from app.services.run_deletion_service import safe_artifact_prefixes

    safe, _refused = safe_artifact_prefixes(project_id, run_id, minio_prefix)
    picked: dict[str, tuple[str, int, str]] = {}
    for prefix in sorted(safe, key=len):
        start = bisect.bisect_left(listing, prefix, key=lambda o: o["Key"])
        for obj in listing[start:]:
            key = obj["Key"]
            if not key.startswith(prefix):
                break
            picked.setdefault(key, (key, int(obj.get("Size") or 0), prefix))
    return [picked[key] for key in sorted(picked)]


def bytes_under_run(listing: Sequence[dict], *, project_id: Any, run_id: Any,
                    minio_prefix: Optional[str], text_only: bool = False) -> int:
    """Exact bytes under one run's safe prefixes, from an in-memory listing.

    ``text_only``: only the objects a ``full`` archive copies (a text suffix,
    at most ``EXPORT_TEXT_OBJECT_MAX_BYTES``)."""
    return sum(
        size
        for key, size, _prefix in run_object_keys(
            listing, project_id=project_id, run_id=run_id, minio_prefix=minio_prefix
        )
        if not text_only or is_text_object(key, size)
    )


async def per_run_object_bytes(
    storage: Any, *, project_id: Any, prefixes: Mapping[uuid.UUID, Optional[str]],
) -> Optional[dict[uuid.UUID, int]]:
    """Run id -> exact object bytes; ``None`` when the listing raised."""
    try:
        listing = await list_project_objects(storage, project_id=project_id)
    except Exception as exc:  # noqa: BLE001 - the estimate's fail-soft form
        _slog.warning("retention_export_listing_failed", project_id=str(project_id),
                      error_type=type(exc).__name__)
        return None
    return {
        run_id: bytes_under_run(listing, project_id=project_id, run_id=run_id, minio_prefix=prefix)
        for run_id, prefix in prefixes.items()
    }


# ── The estimate (E-2): every part fails SOFT -- "not measured", never 0 ──


def case_counts_statement(run_ids: Sequence[uuid.UUID]) -> Any:
    """ONE statement: per run, its test cases and its failed / broken ones."""
    ids = bindparam("export_run_ids", value=list(run_ids), type_=ARRAY(PG_UUID(as_uuid=True)))
    return (
        select(
            TestCase.test_run_id,
            func.count(),
            func.count().filter(TestCase.status.in_(_FAILED_CASE_STATUSES)),
        )
        .where(TestCase.test_run_id == any_(ids))
        .group_by(TestCase.test_run_id)
    )


async def _case_totals(db: Any, run_ids: Sequence[uuid.UUID]) -> tuple[int, int]:
    """``(test cases, failed cases)`` over the set, in a SAVEPOINT (a failed
    statement must not poison the preview's transaction)."""
    async with db.begin_nested():
        rows = (await db.execute(case_counts_statement(run_ids))).all()
    return (sum(int(r[1] or 0) for r in rows), sum(int(r[2] or 0) for r in rows))


async def _mongo_totals(mongo: Any, run_ids: Sequence[uuid.UUID]) -> tuple[int, int]:
    """``(reclaimed bytes, full-tier raw bytes)``: exact document counts times
    each collection's ``avgObjSize`` -- the storage-accounting estimate rule."""
    run_strs = [str(r) for r in run_ids]
    reclaim = raw = 0
    for collection in RECLAIM_MONGO_COLLECTIONS:
        coll = mongo[collection]
        docs = 0
        for i in range(0, len(run_strs), MONGO_ID_CHUNK):
            chunk = run_strs[i:i + MONGO_ID_CHUNK]
            docs += int(await coll.count_documents({"test_run_id": {"$in": chunk}}))
        if docs:
            stats = await mongo.command("collStats", collection)
            nbytes = docs * int((stats or {}).get("avgObjSize") or 0)
            reclaim += nbytes
            if collection in FULL_RAW_RUN_COLLECTIONS:
                raw += nbytes
    return reclaim, raw


async def _object_totals(storage: Any, *, project_id: Any,
                         prefixes: Mapping[uuid.UUID, Optional[str]]) -> tuple[int, int]:
    """``(all object bytes, text bytes a full archive copies)`` under the set."""
    listing = await list_project_objects(storage, project_id=project_id)
    total = text = 0
    for run_id, prefix in prefixes.items():
        for key, size, _p in run_object_keys(listing, project_id=project_id, run_id=run_id,
                                             minio_prefix=prefix):
            total += size
            if is_text_object(key, size):
                text += size
    return total, text


async def _soft(step: Awaitable[Any], *, part: str, project_id: Any,
                timeout: Optional[float] = ESTIMATE_STEP_TIMEOUT_SECONDS) -> Any:
    """Run one estimate part (under the timeout when given); ``None`` on any
    failure. The Postgres part runs without one: cancelling a statement
    mid-flight would leave the request's connection unusable."""
    try:
        if timeout is None:
            return await step
        return await asyncio.wait_for(step, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - absence is not health: not measured
        _slog.warning("retention_export_estimate_unmeasured", part=part,
                      project_id=str(project_id), error_type=type(exc).__name__)
        return None


def _tier(tier: str, archive_bytes: Optional[int], max_bytes: int) -> dict[str, Any]:
    measured = archive_bytes is not None
    return {
        "tier": tier,
        "archive_bytes": archive_bytes,
        "measured": measured,
        "over_cap": bool(measured and int(archive_bytes or 0) > max_bytes),
    }


async def estimate_export(
    db: Any,
    *,
    project_id: Any,
    run_ids: Sequence[uuid.UUID],
    mongo: Any,
    storage: Any,
    max_bytes: int,
    prefixes: Optional[Mapping[uuid.UUID, Optional[str]]] = None,
    collect_ttl_hours: Optional[int] = None,
) -> dict[str, Any]:
    """The ``ExportEstimate`` of a set of DELETABLE runs (a dict of its fields).

    Cost: one GROUP BY, the two root listings, at most ceil(N/500) counts per
    collection. Each part fails soft; ``summary`` needs Postgres alone, so it
    stays measured when only storage or Mongo is down. ``prefixes`` maps each
    run to its ``minio_prefix`` (the preview's existing row read).
    """
    from app.core.config import settings

    ids = list(dict.fromkeys(run_ids))
    count = len(ids)
    refusal = None
    if count == 0:
        refusal = EXPORT_ERROR_SENTENCES["nothing_to_export"]
    elif count > EXPORT_MAX_RUNS:
        refusal = EXPORT_ERROR_SENTENCES["too_many_runs"]
    out: dict[str, Any] = {
        "run_count": count,
        "exportable": refusal is None,
        "refusal": refusal,
        "max_bytes": int(max_bytes),
        "collect_ttl_hours": int(collect_ttl_hours or settings.RETENTION_EXPORT_COLLECT_TTL_HOURS),
        "note": ARCHIVE_NOT_BACKUP,
    }
    if count == 0:
        out.update(reclaim_bytes=0, reclaim_object_bytes=0, reclaim_mongo_bytes=0,
                   reclaim_measured=True,
                   tiers=[_tier(t, 0, max_bytes) for t in EXPORT_TIERS])
        return out

    run_prefixes = {r: (prefixes or {}).get(r) for r in ids}
    cases = await _soft(_case_totals(db, ids), part="postgres", project_id=project_id, timeout=None)
    # The two outside stores concurrently: one slow store costs one timeout, not two.
    objects, mongo_parts = await asyncio.gather(
        _soft(_object_totals(storage, project_id=project_id, prefixes=run_prefixes),
              part="objects", project_id=project_id),
        _soft(_mongo_totals(mongo, ids), part="mongo", project_id=project_id),
    )

    summary = evidence = full = None
    if cases is not None:
        n_cases, n_failed = cases
        summary = SUMMARY_BYTES_PER_RUN * count + SUMMARY_BYTES_PER_CASE * n_cases
        evidence = summary + EVIDENCE_BYTES_PER_FAILED_CASE * n_failed + EVIDENCE_BYTES_PER_RUN * count
        if objects is not None and mongo_parts is not None:
            full = evidence + mongo_parts[1] + objects[1]
    object_bytes = objects[0] if objects is not None else None
    mongo_bytes = mongo_parts[0] if mongo_parts is not None else None
    out.update(
        reclaim_object_bytes=object_bytes,
        reclaim_mongo_bytes=mongo_bytes,
        # Both halves or nothing: an object-only figure would understate the
        # reclaim while reading as the whole of it.
        reclaim_bytes=(object_bytes + mongo_bytes
                       if object_bytes is not None and mongo_bytes is not None else None),
        reclaim_measured=object_bytes is not None,
        tiers=[_tier("summary", summary, max_bytes), _tier("evidence", evidence, max_bytes),
               _tier("full", full, max_bytes)],
    )
    return out


# ── The execute-time choice (E-8) ──────────────────────────────────────────


def _field(choice: Any, name: str) -> Any:
    if isinstance(choice, Mapping):
        return choice.get(name)
    return getattr(choice, name, None)


def validate_vocabulary(choice: Any) -> None:
    """422 ``unknown_tier`` / ``unknown_target``: checked BEFORE the job row is
    claimed, so an out-of-vocabulary value never reaches the row's CHECK."""
    if _field(choice, "tier") not in EXPORT_TIERS:
        raise ExportRefused("unknown_tier", 422)
    if _field(choice, "target") not in EXPORT_TARGETS:
        raise ExportRefused("unknown_target", 422)


def validate_choice(choice: Any, *, estimate: Optional[Mapping[str, Any]], run_count: int,
                    max_bytes: Optional[int] = None) -> None:
    """Refuse an export the job cannot honour, with its fixed sentence.

    422 ``unknown_tier`` / ``unknown_target`` (the vocabulary); 409
    ``nothing_to_export`` / ``too_many_runs`` (the run cap, refuse-not-truncate)
    / ``over_cap`` (the tier's estimate, staged at preview, passed the byte
    limit). An UNMEASURED estimate is not a refusal: the build enforces the cap
    while writing.
    """
    validate_vocabulary(choice)
    tier = _field(choice, "tier")
    if run_count <= 0:
        raise ExportRefused("nothing_to_export", 409)
    if run_count > EXPORT_MAX_RUNS:
        raise ExportRefused("too_many_runs", 409)
    for entry in (estimate or {}).get("tiers") or []:
        if not isinstance(entry, Mapping) or entry.get("tier") != tier:
            continue
        size = entry.get("archive_bytes")
        if entry.get("over_cap") or (
            max_bytes is not None and entry.get("measured") and size is not None and int(size) > max_bytes
        ):
            raise ExportRefused("over_cap", 409)


# ── Storage helpers: the archive streams in and out; the key never leaves ──


def _new_spool() -> IO[bytes]:
    """Memory up to ``EXPORT_SPOOL_BYTES``, disk above it."""
    return tempfile.SpooledTemporaryFile(max_size=EXPORT_SPOOL_BYTES, mode="w+b")


async def stream_archive(storage: Any, *, key: str) -> AsyncIterator[bytes]:
    """The stored archive in ``stream_object`` chunks (the collect download)."""
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    async for chunk in storage.stream_object(key, bucket=COMPLIANCE_BUCKET):
        yield chunk


async def remove_archive(storage: Any, *, key: str) -> bool:
    """Delete a stored archive, best effort: never raises (the janitor retries)."""
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    try:
        await storage.delete_object(key, bucket=COMPLIANCE_BUCKET)
        return True
    except Exception as exc:  # noqa: BLE001 - removal is retried by the janitor
        _slog.warning("retention_export_remove_failed", error_type=type(exc).__name__)
        return False


_RETRYABLE_ERROR_NAMES = frozenset({
    "EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError",
    "ConnectionClosedError", "ServerTimeoutError", "ClientConnectorError",
})


def _retryable(exc: BaseException) -> bool:
    """A storage TIMEOUT / 5xx / connection failure: worth another re-read.
    Anything else (a 403, a missing object) fails the verification at once."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    if type(exc).__name__ in _RETRYABLE_ERROR_NAMES:
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return isinstance(status, int) and status >= 500
    return False


def _unverified_reason(result: Mapping[str, Any]) -> str:
    if result.get("chain") != "intact":
        return "chain broken"
    if result.get("manifest_matches_record") is False:
        return "digest mismatch"
    if result.get("signature") not in ("valid", "unsigned"):
        return "signature invalid"
    return "not verified"


async def _verify_stored(
    storage: Any, *, key: str, archive_sha256: str, manifest_sha256: str,
    public_keys: Mapping[str, str], job_id: uuid.UUID, signature: Optional[str],
    signing_key_id: Optional[str], sleep: Callable[[float], Awaitable[Any]],
) -> int:
    """RE-READ the stored object into a second spool and verify it; returns
    the number of read attempts. A timeout / 5xx is retried with backoff and
    never counts as verified; a digest, chain or signature mismatch fails at once."""
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET, sha256_of_file
    from app.services.compliance_pack_verify import verify_archive_file

    attempts = 0
    while True:
        attempts += 1
        copy = _new_spool()
        try:
            try:
                async for chunk in storage.stream_object(key, bucket=COMPLIANCE_BUCKET):
                    copy.write(chunk)
            except Exception as exc:  # noqa: BLE001 - classified below
                if _retryable(exc) and attempts < EXPORT_VERIFY_ATTEMPTS:
                    _slog.warning("retention_export_reread_retry", job_id=str(job_id),
                                  attempt=attempts, error_type=type(exc).__name__)
                    await sleep(VERIFY_BACKOFF_SECONDS[min(attempts, len(VERIFY_BACKOFF_SECONDS)) - 1])
                    continue
                reason = "storage did not answer" if _retryable(exc) else "storage refused the read"
                raise ExportFailed("unverified", reason=reason) from exc
            if await asyncio.to_thread(sha256_of_file, copy) != archive_sha256:
                raise ExportFailed("unverified", reason="digest mismatch")
            result = await asyncio.to_thread(
                verify_archive_file, copy, recorded_manifest_sha256=manifest_sha256,
                public_keys=public_keys, pack_id=job_id, record_signature=signature,
                record_signing_key_id=signing_key_id,
            )
            if result.get("result") != "verified":
                raise ExportFailed("unverified", reason=_unverified_reason(result))
            return attempts
        finally:
            copy.close()


#: Public names for the project export (W24 ADM-05) -- the SAME functions, no
#: behaviour change: the spool, and the write -> RE-READ -> verify loop
#: (``EXPORT_VERIFY_ATTEMPTS``, ``VERIFY_BACKOFF_SECONDS``). Its
#: ``ExportFailed("unverified", reason=…)`` is mapped by the caller.
new_spool = _new_spool
verify_stored_archive = _verify_stored


# ── The export phase (E-5) ─────────────────────────────────────────────────


async def deletion_document(db: Any, *, job_id: uuid.UUID, project_id: Any) -> dict[str, Any]:
    """``deletion.json``'s job facts: the project's name, the criteria, the
    frozen set's hash, who asked and when (ONE statement; a name, never an
    e-mail address)."""
    from app.models.postgres import DeletionJob, Project, User

    row = (
        await db.execute(
            select(DeletionJob.criteria, DeletionJob.candidate_hash, DeletionJob.requested_at,
                   DeletionJob.requested_by_id, Project.name, User.full_name, User.username)
            .select_from(DeletionJob)
            .join(Project, Project.id == DeletionJob.project_id)
            .outerjoin(User, User.id == DeletionJob.requested_by_id)
            .where(DeletionJob.id == job_id, DeletionJob.project_id == project_id)
        )
    ).first()
    if row is None:
        return {}
    criteria, digest, requested_at, requested_by, project_name, full_name, username = row
    return {
        "project_name": project_name,
        "criteria": criteria,
        "candidate_hash": digest,
        "requested_by": {"id": str(requested_by) if requested_by else None, "name": full_name or username},
        "requested_at": requested_at.isoformat() if requested_at else None,
    }


async def _resolve_set(db: Any, *, project_id: Any, ids: list[uuid.UUID], now: datetime,
                       ) -> tuple[list[uuid.UUID], dict[uuid.UUID, list[Any]], list[uuid.UUID]]:
    """``(kept, held -> holds, missing)``: holds read ONCE through W22a's hold
    service (fail CLOSED), then which of the rest still exist."""
    from app.services import retention_hold_service

    try:
        covering = await retention_hold_service.holds_covering_runs(
            db, project_id=project_id, run_ids=ids, now=now
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable hold set deletes nothing
        _slog.warning("retention_export_holds_unreadable", project_id=str(project_id),
                      error_type=type(exc).__name__)
        raise ExportFailed("holds_unreadable") from exc
    try:
        rows = (
            await db.execute(
                select(TestRun.id).where(TestRun.project_id == project_id, TestRun.id.in_(ids))
            )
        ).all()
    except Exception as exc:  # noqa: BLE001
        raise ExportFailed("build", error_type=type(exc).__name__) from exc
    existing = {row[0] for row in rows}
    held = {r: list(covering[r]) for r in ids if r in covering}
    kept = [r for r in ids if r in existing and r not in held]
    missing = [r for r in ids if r not in existing and r not in held]
    return kept, held, missing


async def run_export(
    db: Any,
    *,
    job_id: uuid.UUID,
    project_id: uuid.UUID,
    run_ids: Sequence[Any],
    choice: Any,
    mongo: Any,
    storage: Any,
    now: datetime,
    deletion: Optional[Mapping[str, Any]] = None,
    on_written: Optional[Callable[[str], Awaitable[Any]]] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    max_bytes: Optional[int] = None,
) -> ExportOutcome:
    """Holds -> build -> sign -> write -> RE-READ + verify. Raises ``ExportFailed``.

    ``on_written(key)`` runs right after the upload (the worker commits the
    location there, so a crash before verification leaves the object for the
    janitor). Any ``ExportFailed`` after the upload removes the object best
    effort. The signing key may be CREATED here on ``db`` -- the caller commits
    ``db`` after this returns, before it records the verification.
    """
    from app.core.config import settings
    from app.services import compliance_pack_service as packs
    from app.services import compliance_signing
    from app.services.retention_export_content import run_members

    tier, target = _field(choice, "tier"), _field(choice, "target")
    ids = [uuid.UUID(str(r)) for r in dict.fromkeys(run_ids)]
    kept, held, missing = await _resolve_set(db, project_id=project_id, ids=ids, now=now)
    if not kept:
        raise ExportFailed("build", error_type="no runs left to archive")
    if deletion is None:
        try:
            deletion = await deletion_document(db, job_id=job_id, project_id=project_id)
        except Exception as exc:  # noqa: BLE001 - an archive that cannot say what it is fails
            raise ExportFailed("build", error_type=type(exc).__name__) from exc
    limit = int(max_bytes or settings.RETENTION_EXPORT_MAX_BYTES)
    refs: dict[Any, Any] = {}
    for run_holds in held.values():
        for ref in run_holds:
            refs.setdefault(ref.id, ref)
    document = {
        **dict(deletion or {}),
        "job_id": str(job_id),
        "project_id": str(project_id),
        "tier": tier,
        "target": target,
        "run_ids_excluded": [
            {"run_id": str(r), "holds": [ref.as_json() for ref in held[r]]} for r in sorted(held, key=str)
        ],
        "missing_run_ids": sorted(str(r) for r in missing),
    }
    stats: dict[str, int] = {}
    spool = _new_spool()
    key: Optional[str] = None
    try:
        try:
            scope = packs.ExportScope.for_runs(project_id=project_id, run_ids=kept, tier=tier)
            archive = await packs.build_archive(
                scope,
                members=run_members(db, scope, mongo=mongo, storage=storage, deletion=document, stats=stats),
                sink=spool, generated_at=now, max_bytes=limit,
            )
            spool, signature, signing_key_id, signing = await packs._sign_pack(
                db, spool, pack_id=job_id, manifest_sha256=archive.manifest_sha256,
                actor=None, signed_at=now,
            )
        except packs.ArchiveTooLarge as exc:
            raise ExportFailed("over_cap", limit_mb=limit // (1024 * 1024)) from exc
        except ExportFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - only the type name reaches the job row
            raise ExportFailed("build", error_type=type(exc).__name__) from exc
        archive_sha256 = await asyncio.to_thread(packs.sha256_of_file, spool)
        size = spool.seek(0, 2)
        key = packs.build_export_key(scope, now, job_id)
        try:
            await storage.put_object_stream(
                key, spool, size=size, content_type="application/zip", bucket=packs.COMPLIANCE_BUCKET,
            )
            if on_written is not None:
                await on_written(key)
        except Exception as exc:  # noqa: BLE001
            raise ExportFailed("write", error_type=type(exc).__name__) from exc
        try:
            public_keys = await compliance_signing.public_keys(db)
        except Exception as exc:  # noqa: BLE001
            raise ExportFailed("unverified", reason="signing keys unreadable") from exc
        try:
            attempts = await _verify_stored(
                storage, key=key, archive_sha256=archive_sha256, manifest_sha256=archive.manifest_sha256,
                public_keys=public_keys, job_id=job_id, signature=signature,
                signing_key_id=signing_key_id, sleep=sleep,
            )
        except ExportFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - an unproven archive is a failed export
            raise ExportFailed("unverified", reason=type(exc).__name__) from exc
    except ExportFailed as failed:
        # The job record still names what the holds kept out (holds_honoured).
        failed.holds = list(refs.values())
        failed.held_run_ids = sorted(str(r) for r in held)
        if key is not None:
            await remove_archive(storage, key=key)
        _slog.warning("retention_export_failed", job_id=str(job_id), tier=tier, target=target,
                      code=failed.code, runs=len(kept))
        raise
    finally:
        spool.close()

    outcome = ExportOutcome(
        location=str(key), manifest_sha256=archive.manifest_sha256, archive_sha256=archive_sha256,
        bytes=size, file_count=archive.file_count, signing=dict(signing),
        run_ids_archived=kept, run_ids_excluded=sorted(held, key=str), missing_run_ids=missing,
        holds=list(refs.values()), objects_not_archived=int(stats.get("objects_not_archived", 0)),
        verify_attempts=attempts, signature=signature, signing_key_id=signing_key_id,
        holds_by_run={str(r): v for r, v in held.items()},
    )
    _slog.info("retention_export_verified", job_id=str(job_id), tier=tier, target=target,
               runs=len(kept), excluded=len(held), missing=len(missing), bytes=size,
               files=archive.file_count, verify_attempts=attempts)
    return outcome
