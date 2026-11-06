"""The drop-folder polling watcher (W34 STRAT-13, D40): vocabulary, decisions and staged SQL.

A CI job that drops its results under ``<project>/runs/<build>/`` in the
object store and writes ``<project>/runs/<build>/upload_complete.json`` last
becomes a run with NO webhook: the bundled S3 store (versitygw) has no bucket
notifications, so a beat task (``worker/tasks.py:watch_drop_folders``, every
minute, queue ``default``) polls instead. ``POST /webhooks/minio`` stays, for
an external MinIO that does send notifications.

One sweep, under a deployment-wide Redis lock (fail closed):

1. the ``drop_folder_watcher`` flag (any scope) -- off, nothing else happens;
2. ONE short session: the active projects (id + slug, each still asked the flag
   for ITS project), the baseline time (``app_settings``, written by the first
   sweep), ``pending`` rows seen 24 h ago -> ``abandoned``; committed, closed;
3. per project prefix (``<uuid>/runs/`` and ``<slug>/runs/``), with NO session
   open: one delimiter listing of the build folders
   (``StorageProvider.list_common_prefixes``, never the objects below them);
   a short read session for the rows of the listed folders; then a HEAD of
   ``<folder>upload_complete.json`` only for a folder that is new or still
   ``pending``; then a short write session for the decisions.

A found sentinel is queued through the SAME ``ingest_test_run.delay(
sentinel_key=..., minio_prefix=...)`` the webhook uses (its sentinel checks,
its Redis dedup and its dead-letter queue apply unchanged) and its row becomes
``queued``. A sentinel older than the baseline is recorded ``baseline`` and
never queued: switching the watcher on never re-ingests history. A folder
whose key fails :func:`~app.services.minio_sentinel.sentinel_key_problem` is
``refused`` without a HEAD.

Rows are never deleted with runs: a deleted slug-prefixed run, whose objects
stay in the bucket, must not come back. A terminal row is never HEADed again,
so re-dropping the SAME build folder is not picked up -- use a new build
number. Trust: the watcher trusts bucket writers exactly as the webhook does --
the key's first segment is the project (``architecture/SECURITY.md`` 7a).

This module stages only (no commit: the worker owns the transactions,
``tests/test_architectural_transaction_boundaries.py``).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from app.services.minio_sentinel import SENTINEL_NAME, sentinel_key_problem

#: The switch (seeded ON by migration 0254). ``FEATURE_FLAG`` so the catalogue guard reads it.
FEATURE_FLAG = "drop_folder_watcher"
#: One sweep at a time, deployment-wide (``tasks._claim_sweep_lock``); TTL above the task's hard limit.
LOCK_KEY = "testlookup:lock:drop-folder-watcher"
LOCK_TTL_SECONDS = 360
#: The ``app_settings`` row holding the first sweep's time: ``{"at": "<ISO-8601 UTC>"}``.
BASELINE_KEY = "drop_folder_watcher_baseline"
RUNS_SEGMENT = "runs"

STATUS_PENDING = "pending"        # listed, no sentinel yet: HEADed each sweep for PENDING_WINDOW
STATUS_QUEUED = "queued"          # the sentinel was queued for ingestion (task_id set)
STATUS_BASELINE = "baseline"      # the sentinel predates the baseline: history, never queued
STATUS_ABANDONED = "abandoned"    # pending for PENDING_WINDOW: no more HEADs
STATUS_REFUSED = "refused"        # the key fails sentinel_key_problem: never HEADed or queued
#: The ``ck_dfb_status`` literals (migration 0254 holds a copy; pinned by tests/test_migration_0254.py).
BUILD_STATUSES = (STATUS_PENDING, STATUS_QUEUED, STATUS_BASELINE, STATUS_ABANDONED, STATUS_REFUSED)

PENDING_WINDOW = timedelta(hours=24)
#: Per-sweep bounds; the rest waits for the next minute (logged as ``drop_folder_sweep_capped``).
#: NEW folders and pending RE-CHECKS have separate budgets, and a prefix's new folders are examined
#: first, so a pile of folders that never get a sentinel cannot starve a new upload for 24 hours.
MAX_NEW_CHECKS_PER_SWEEP = 300      # new folders examined: a HEAD or a refusal
MAX_PENDING_CHECKS_PER_SWEEP = 300  # pending folders HEADed again (least recently checked first)
MAX_QUEUED_PER_SWEEP = 100          # ingestions queued
SWEEP_DEADLINE_SECONDS = 180        # wall clock, well inside the task's soft limit (240 s)
_LOOKUP_CHUNK = 1000


@dataclass(frozen=True)
class KnownBuild:
    """What the table already says about one build folder."""

    status: str
    first_seen_at: datetime
    last_checked_at: datetime | None = None


@dataclass
class BuildDecision:
    """One row to write: a folder examined by this sweep."""

    build_prefix: str
    status: str
    sentinel_etag: str | None = None
    sentinel_last_modified: datetime | None = None
    queued_at: datetime | None = None
    task_id: str | None = None


def project_prefixes(projects: Iterable[tuple[Any, str | None]]) -> list[str]:
    """``<uuid>/runs/`` and ``<slug>/runs/`` for each project, without duplicates
    (a slug equal to the id string is scanned once)."""
    out: list[str] = []
    seen: set[str] = set()
    for project_id, slug in projects:
        for segment in (str(project_id), (slug or "").strip()):
            if segment and segment not in seen:
                seen.add(segment)
                out.append(f"{segment}/{RUNS_SEGMENT}/")
    return out


def segment_of(build_prefix: str) -> str:
    """The project segment (the key's first segment) of ``<segment>/runs/<build>/``."""
    return build_prefix.split("/", 1)[0]


def sentinel_key_for(build_prefix: str) -> str:
    """``<segment>/runs/<build>/upload_complete.json`` -- exactly the webhook's key shape."""
    return f"{build_prefix}{SENTINEL_NAME}"


def key_problem(build_prefix: str) -> str | None:
    """Why the folder's sentinel key cannot be queued, or None: the webhook's
    rule (``sentinel_key_problem``) plus the exact ``<segment>/runs/<build>/``
    shape, so the run's files are read from the folder that was listed."""
    parts = build_prefix.split("/")
    if len(parts) != 4 or parts[1] != RUNS_SEGMENT or parts[3] != "":
        return "is not <project>/runs/<build>/"
    return sentinel_key_problem(sentinel_key_for(build_prefix))


def needs_head(known: KnownBuild | None, now: datetime) -> bool:
    """A folder is HEADed when it is new, or still ``pending`` and under 24 h old."""
    if known is None:
        return True
    return known.status == STATUS_PENDING and now - known.first_seen_at < PENDING_WINDOW


def candidates(listed: Sequence[str], known: dict[str, KnownBuild], now: datetime) -> list[str]:
    """The folders to examine, in order: the NEW ones (listing order), then the
    pending ones under 24 h, least recently checked first (so a re-check budget
    smaller than the pile still reaches every folder in turn)."""
    new = [prefix for prefix in listed if prefix not in known]
    pending = [prefix for prefix in listed if prefix in known and needs_head(known[prefix], now)]
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    pending.sort(key=lambda prefix: known[prefix].last_checked_at or oldest)
    return new + pending


def classify(head: Any, baseline: datetime) -> str:
    """``pending`` (no sentinel), ``baseline`` (older than the baseline) or
    ``queued`` (to be queued) for a HEAD result (``ObjectHead | None``)."""
    if head is None:
        return STATUS_PENDING
    if head.last_modified < baseline:
        return STATUS_BASELINE
    return STATUS_QUEUED


def parse_baseline(value: Any) -> datetime | None:
    """The baseline time stored in ``app_settings`` (``{"at": iso}``), or None."""
    raw = value.get("at") if isinstance(value, dict) else None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ── Staged SQL (the caller's session; the worker commits) ────────────────────


async def load_projects(db: Any) -> list[tuple[uuid.UUID, str]]:
    """The active projects' (id, slug), in id order."""
    from sqlalchemy import select

    from app.models.postgres import Project

    rows = await db.execute(select(Project.id, Project.slug).where(Project.is_active.is_(True)).order_by(Project.id))
    return [(row[0], row[1]) for row in rows.all()]


async def ensure_baseline(db: Any, now: datetime) -> datetime:
    """The baseline time, written as ``now`` by the first sweep (``ON CONFLICT
    DO NOTHING``: a concurrent first sweep cannot move it). An unreadable stored
    value is replaced by ``now`` -- the same as a first run -- rather than read as
    "no baseline", which would queue history."""
    from sqlalchemy import select, update
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.postgres import AppSetting

    value = {"at": now.isoformat()}
    await db.execute(
        pg_insert(AppSetting)
        .values(key=BASELINE_KEY, value=value, is_secret_backed=False)
        .on_conflict_do_nothing(index_elements=["key"])
    )
    stored = (await db.execute(select(AppSetting.value).where(AppSetting.key == BASELINE_KEY))).scalar_one_or_none()
    baseline = parse_baseline(stored)
    if baseline is None:
        await db.execute(update(AppSetting).where(AppSetting.key == BASELINE_KEY).values(value=value))
        baseline = now
    return baseline


async def abandon_stale_pending(db: Any, now: datetime) -> int:
    """``pending`` rows first seen ``PENDING_WINDOW`` ago -> ``abandoned`` (never HEADed again)."""
    from sqlalchemy import update

    from app.models.postgres import DropFolderBuild

    result = await db.execute(
        update(DropFolderBuild)
        .where(DropFolderBuild.status == STATUS_PENDING, DropFolderBuild.first_seen_at <= now - PENDING_WINDOW)
        .values(status=STATUS_ABANDONED, last_checked_at=now)
    )
    return int(result.rowcount or 0)


async def known_builds(db: Any, build_prefixes: Sequence[str]) -> dict[str, KnownBuild]:
    """The rows of the listed folders (``uq_dfb_build_prefix``), in chunks."""
    from sqlalchemy import select

    from app.models.postgres import DropFolderBuild

    known: dict[str, KnownBuild] = {}
    for start in range(0, len(build_prefixes), _LOOKUP_CHUNK):
        chunk = list(build_prefixes[start:start + _LOOKUP_CHUNK])
        rows = await db.execute(
            select(
                DropFolderBuild.build_prefix, DropFolderBuild.status, DropFolderBuild.first_seen_at,
                DropFolderBuild.last_checked_at,
            ).where(DropFolderBuild.build_prefix.in_(chunk))
        )
        for prefix, status, first_seen_at, last_checked_at in rows.all():
            known[prefix] = KnownBuild(status=status, first_seen_at=first_seen_at, last_checked_at=last_checked_at)
    return known


async def record_decisions(db: Any, decisions: Sequence[BuildDecision], now: datetime) -> None:
    """Upsert one row per decision. A new folder is inserted (``first_seen_at`` =
    ``now``); an existing row is updated ONLY while it is still ``pending``, so a
    terminal row is never rewritten and ``first_seen_at`` never moves."""
    if not decisions:
        return
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.postgres import DropFolderBuild

    # One row per folder: Postgres refuses an ON CONFLICT DO UPDATE that touches a row twice.
    unique = {d.build_prefix: d for d in decisions}
    values = [
        {
            "id": uuid.uuid4(),
            "build_prefix": d.build_prefix,
            "project_segment": segment_of(d.build_prefix)[:100],
            "status": d.status,
            "sentinel_etag": d.sentinel_etag[:255] if d.sentinel_etag else None,
            "sentinel_last_modified": d.sentinel_last_modified,
            "first_seen_at": now,
            "last_checked_at": now,
            "queued_at": d.queued_at,
            "task_id": d.task_id,
        }
        for d in unique.values()
    ]
    statement = pg_insert(DropFolderBuild).values(values)
    excluded = statement.excluded
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=["build_prefix"],
            set_={
                "status": excluded.status,
                "sentinel_etag": excluded.sentinel_etag,
                "sentinel_last_modified": excluded.sentinel_last_modified,
                "last_checked_at": excluded.last_checked_at,
                "queued_at": excluded.queued_at,
                "task_id": excluded.task_id,
            },
            where=DropFolderBuild.status == STATUS_PENDING,
        )
    )
