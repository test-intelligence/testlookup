"""Per-project storage footprint — how much a project is actually costing.

S3 of the retention epic. The product could report how many *rows* a purge
would remove and never how many *bytes* it would reclaim, so the question an
operator actually asks — "how much storage am I using, and what would I get
back?" — had no answer anywhere.

Two rules shape every number here.

**Reached, or not reached.** Each store reports ``measured``. A store that
could not be contacted reports ``measured=False`` and a null figure — never
``0``. Zero and unreachable are opposite findings, and rendering both as "0 B"
tells an operator their project is free when the truth is that nothing looked.
(This is a fix to existing behaviour, not a new constraint:
``semantic_search.purge_project_documents`` returns ``0`` on a vector-store
outage, and its own comment says a purge that could not visit a store must not
read as "nothing to delete there".)

**Exact, or estimated.** Only object storage can attribute bytes to a project
precisely — every listed object carries its own ``Size``. Postgres and Mongo
share their tables and collections across projects, so their byte figures are
proportional estimates and say so in the payload. Callers must not sum an exact
and an estimated figure and present the result as measured; ``total_bytes``
here is explicitly labelled by ``total_is_estimate``.

Read-only. No commits, no deletes, no mutation of any kind.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mongo import Collections, get_mongo_db
from app.db.storage import get_storage_provider
from app.models.postgres import Project, ProjectRetentionPolicy, TestCase, TestRun

logger = structlog.get_logger(__name__)

#: Object-storage prefixes that belong to one project. ``uploads/{project}/`` is
#: listed ONCE for the whole project — a per-run loop is O(runs) paginated LIST
#: calls, roughly 100k round-trips on a 100k-run project.
_UPLOADS_PREFIX = "uploads/{project_id}/"

#: Mongo collections keyed by ``test_run_id``. Counting these needs the run ids,
#: which is why the caller passes them in rather than us re-deriving them.
_RUN_SCOPED_COLLECTIONS = (
    Collections.RAW_ALLURE_JSON,
    Collections.EXECUTION_LOGS,
    Collections.OCP_POD_EVENTS,
    Collections.RUN_SUMMARIES,
    Collections.DECISION_EVIDENCE_SNAPSHOTS,
)

_MONGO_ID_CHUNK = 500


@dataclass
class StoreFootprint:
    """One store's contribution, with its own honesty flags.

    ``bytes_`` and the count fields are ``None`` when ``measured`` is False —
    deliberately not ``0``, so a caller cannot render an outage as an empty
    project without noticing.
    """

    store: str
    measured: bool
    exact: bool
    bytes_: Optional[int] = None
    items: Optional[int] = None
    estimate_basis: Optional[str] = None
    unreachable_reason: Optional[str] = None

    def as_payload(self) -> dict[str, Any]:
        out = asdict(self)
        out["bytes"] = out.pop("bytes_")
        return out


@dataclass
class ProjectStorageFootprint:
    project_id: str
    computed_at: datetime
    stores: list[StoreFootprint] = field(default_factory=list)

    @property
    def total_bytes(self) -> Optional[int]:
        """Sum of every store that was reached and reported bytes.

        ``None`` when nothing was measurable — an unreachable everything must
        not total to zero.
        """
        parts = [s.bytes_ for s in self.stores if s.measured and s.bytes_ is not None]
        return sum(parts) if parts else None

    @property
    def total_is_estimate(self) -> bool:
        """True when any contributing store was an estimate.

        A total mixing exact object-storage bytes with a proportional Postgres
        estimate is an estimate, and calling it anything else is the kind of
        confident-but-invented figure this page has shipped before.
        """
        return any(
            s.measured and s.bytes_ is not None and not s.exact for s in self.stores
        )

    @property
    def fully_measured(self) -> bool:
        return all(s.measured for s in self.stores)

    def as_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "computed_at": self.computed_at,
            "stores": [s.as_payload() for s in self.stores],
            "total_bytes": self.total_bytes,
            "total_is_estimate": self.total_is_estimate,
            "fully_measured": self.fully_measured,
        }


async def _object_storage_footprint(
    project_id: uuid.UUID, minio_prefixes: list[str], storage: Any
) -> StoreFootprint:
    """Exact bytes. Every listed object carries its own ``Size``.

    Two prefix families: the per-project uploads tree, listed once, and the
    per-run ``TestRun.minio_prefix`` values, which are uploader-derived and
    therefore unbounded in shape — they have to be listed individually, so the
    caller caps how many are passed in.
    """
    total_bytes = 0
    total_objects = 0
    seen_keys: set[str] = set()

    prefixes = [_UPLOADS_PREFIX.format(project_id=project_id), *minio_prefixes]
    try:
        for prefix in prefixes:
            for obj in await storage.list_objects(prefix):
                key = obj.get("Key")
                # The uploads tree and a run prefix can overlap; counting an
                # object twice would inflate the headline figure.
                if key is not None and key in seen_keys:
                    continue
                if key is not None:
                    seen_keys.add(key)
                total_bytes += int(obj.get("Size") or 0)
                total_objects += 1
    except Exception as exc:  # noqa: BLE001 — an outage is a reportable state
        logger.warning(
            "storage_accounting_object_store_unreachable",
            project_id=str(project_id),
            error=str(exc),
        )
        return StoreFootprint(
            store="object_storage",
            measured=False,
            exact=True,
            unreachable_reason=type(exc).__name__,
        )

    return StoreFootprint(
        store="object_storage",
        measured=True,
        exact=True,
        bytes_=total_bytes,
        items=total_objects,
    )


async def _postgres_footprint(
    db: AsyncSession, project_id: uuid.UUID
) -> StoreFootprint:
    """Exact row counts; bytes deliberately omitted.

    Postgres cannot attribute bytes to a project without either a proportional
    guess over shared tables or a per-row ``pg_column_size`` scan. Neither is
    worth presenting as a storage figure, and a bulk DELETE would not return
    the space to the OS anyway — dead tuples are reused by the table, and disk
    only comes back under ``VACUUM FULL`` / ``pg_repack``. So this reports what
    it can count exactly and leaves ``bytes`` null rather than inventing one.
    """
    try:
        runs = await db.scalar(
            select(func.count(TestRun.id)).where(TestRun.project_id == project_id)
        )
        cases = await db.scalar(
            select(func.count(TestCase.id))
            .select_from(TestCase)
            .join(TestRun, TestCase.test_run_id == TestRun.id)
            .where(TestRun.project_id == project_id)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "storage_accounting_postgres_unreachable",
            project_id=str(project_id),
            error=str(exc),
        )
        return StoreFootprint(
            store="postgres",
            measured=False,
            exact=True,
            unreachable_reason=type(exc).__name__,
        )

    return StoreFootprint(
        store="postgres",
        measured=True,
        exact=True,
        items=int(runs or 0) + int(cases or 0),
        estimate_basis=(
            "Row counts are exact. Bytes are not reported: rows share tables "
            "across projects, and deleting them does not return disk to the OS "
            "without VACUUM FULL / pg_repack."
        ),
    )


async def _mongo_footprint(
    project_id: uuid.UUID, run_ids: list[uuid.UUID], mongo: Any
) -> StoreFootprint:
    """Exact document counts; bytes estimated from ``avgObjSize``.

    Collections are shared across projects, so per-project bytes come from the
    collection's average object size multiplied by this project's document
    count. Labelled an estimate in the payload because that is what it is.
    """
    if not run_ids:
        return StoreFootprint(
            store="mongo", measured=True, exact=False, bytes_=0, items=0,
            estimate_basis="No runs in this project.",
        )

    run_strs = [str(r) for r in run_ids]
    total_docs = 0
    est_bytes = 0
    try:
        for collection in _RUN_SCOPED_COLLECTIONS:
            coll = mongo[collection]
            docs = 0
            for i in range(0, len(run_strs), _MONGO_ID_CHUNK):
                chunk = run_strs[i : i + _MONGO_ID_CHUNK]
                docs += await coll.count_documents({"test_run_id": {"$in": chunk}})
            total_docs += docs
            if docs:
                stats = await mongo.command("collStats", collection)
                est_bytes += docs * int(stats.get("avgObjSize") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "storage_accounting_mongo_unreachable",
            project_id=str(project_id),
            error=str(exc),
        )
        return StoreFootprint(
            store="mongo",
            measured=False,
            exact=False,
            unreachable_reason=type(exc).__name__,
        )

    return StoreFootprint(
        store="mongo",
        measured=True,
        exact=False,
        bytes_=est_bytes,
        items=total_docs,
        estimate_basis=(
            "Document counts are exact. Bytes are the collection's avgObjSize "
            "multiplied by this project's document count — collections are "
            "shared across projects."
        ),
    )


#: Cap on per-run MinIO prefixes listed individually. Beyond this the uploads
#: tree still gives the bulk of the figure; the alternative is an unbounded
#: number of LIST calls on a large project.
MAX_RUN_PREFIXES = 2000


#: How many deleted projects one call will actually measure. Each footprint
#: costs at least one paginated object-store listing, so this is bounded and
#: the response says when it truncated — a silently capped total would
#: understate the very number this endpoint exists to surface.
MAX_DELETED_PROJECTS_SCANNED = 25


@dataclass
class DeletedProjectFootprint:
    project_id: str
    name: str
    #: True when the nightly beat would eventually purge this project anyway.
    #: The beat selects on ``ProjectRetentionPolicy.enabled`` **alone** and does
    #: not filter ``is_active``, so a deleted project that opted in before
    #: deletion still gets swept. One that never opted in — the default, since
    #: ``enabled`` defaults False — is purged by nothing, ever.
    reachable_by_retention: bool
    footprint: ProjectStorageFootprint

    def as_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "reachable_by_retention": self.reachable_by_retention,
            "footprint": self.footprint.as_payload(),
        }


@dataclass
class DeletedProjectsReport:
    computed_at: datetime
    projects_total: int
    projects: list[DeletedProjectFootprint] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.projects_total > len(self.projects)

    @property
    def total_bytes(self) -> Optional[int]:
        parts = [
            p.footprint.total_bytes
            for p in self.projects
            if p.footprint.total_bytes is not None
        ]
        return sum(parts) if parts else None

    @property
    def total_is_estimate(self) -> bool:
        return any(p.footprint.total_is_estimate for p in self.projects)

    @property
    def unreachable_by_retention(self) -> int:
        """Deleted projects nothing will ever purge — the headline number."""
        return sum(1 for p in self.projects if not p.reachable_by_retention)

    def as_payload(self) -> dict[str, Any]:
        return {
            "computed_at": self.computed_at,
            "projects_total": self.projects_total,
            "projects_measured": len(self.projects),
            "truncated": self.truncated,
            "projects": [p.as_payload() for p in self.projects],
            "total_bytes": self.total_bytes,
            "total_is_estimate": self.total_is_estimate,
            "unreachable_by_retention": self.unreachable_by_retention,
        }


async def deleted_project_footprints(
    db: AsyncSession,
    *,
    limit: int = MAX_DELETED_PROJECTS_SCANNED,
    mongo: Any = None,
    storage: Any = None,
    now: Optional[datetime] = None,
) -> DeletedProjectsReport:
    """What deleted projects are still costing, and what will never reclaim it.

    Deleting a project sets ``is_active = False`` and revokes its credentials.
    Nothing then reconciles the data, and the three retention paths disagree
    about it: the nightly beat does not check ``is_active`` (so it sweeps a
    deleted project **if it opted in**), the manual purge does check and 404s,
    and preview does not check and works. Since ``enabled`` defaults to False,
    the default case — delete a project that never turned retention on — is
    purged by nothing, ever, across all five stores, and is invisible on every
    screen in the product.

    Measured in this codebase already: ``purge_project_documents``'s own
    docstring records 49,380 search-index documents against 600 belonging to
    active projects — 98.8% was deleted projects' embeddings.

    There is no ``projects.deleted_at`` column, so this cannot report **when**
    a project was deleted, only that it was. Ageing a reclamation policy needs
    that column first.
    """
    computed_at = now or datetime.now(timezone.utc)

    # One query for the deleted projects and their retention posture — the
    # per-project lookup this endpoint exists to avoid is the object-store
    # listing, not this.
    rows = (
        await db.execute(
            select(Project.id, Project.name, ProjectRetentionPolicy.enabled)
            .outerjoin(
                ProjectRetentionPolicy,
                ProjectRetentionPolicy.project_id == Project.id,
            )
            .where(Project.is_active.is_(False))
            .order_by(Project.name)
        )
    ).all()

    report = DeletedProjectsReport(
        computed_at=computed_at, projects_total=len(rows)
    )
    for project_id, name, enabled in rows[:limit]:
        footprint = await project_storage_footprint(
            db, project_id, mongo=mongo, storage=storage, now=computed_at
        )
        report.projects.append(
            DeletedProjectFootprint(
                project_id=str(project_id),
                name=name,
                reachable_by_retention=bool(enabled),
                footprint=footprint,
            )
        )
    return report


async def project_storage_footprint(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    mongo: Any = None,
    storage: Any = None,
    now: Optional[datetime] = None,
) -> ProjectStorageFootprint:
    """Footprint for one project across every store that holds its data.

    ``mongo`` / ``storage`` are injection seams for tests, matching
    ``retention_service.run_purge``'s signature.

    Read-only: no writes, no commits. A failure in any one store degrades that
    store to ``measured=False`` and leaves the rest of the answer intact —
    partial truth beats a 500 for a page whose whole job is reporting.
    """
    mongo = mongo if mongo is not None else get_mongo_db()
    storage = storage if storage is not None else get_storage_provider()
    computed_at = now or datetime.now(timezone.utc)

    rows = (
        await db.execute(
            select(TestRun.id, TestRun.minio_prefix).where(
                TestRun.project_id == project_id
            )
        )
    ).all()
    run_ids = [r[0] for r in rows]
    prefixes = [r[1] for r in rows[:MAX_RUN_PREFIXES] if r[1]]

    footprint = ProjectStorageFootprint(
        project_id=str(project_id), computed_at=computed_at
    )
    footprint.stores.append(
        await _object_storage_footprint(project_id, prefixes, storage)
    )
    footprint.stores.append(await _postgres_footprint(db, project_id))
    footprint.stores.append(await _mongo_footprint(project_id, run_ids, mongo))
    return footprint
