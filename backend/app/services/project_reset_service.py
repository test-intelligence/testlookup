"""Project-scoped data reset.

Two modes:

* ``runs``  — drops every TestRun for the project. CASCADE handles
  test_cases, ai_analysis, failure_clusters, release_decisions,
  agent_pipeline_runs, run_intelligence_snapshots, etc. Catalog tables
  (test_suites, canonical_test_cases) survive so the project keeps its
  authored structure; ingest the next run and finalize_run repopulates
  the per-run wiring as usual.

* ``full`` — runs the ``runs`` deletes plus every project-scoped
  catalog/RAG/policy/baseline table. Returns the project to a green-
  field state (Project row itself, members, API keys, and project-
  level AI/SSO config survive — those are deliberate keeps).

The destructive path is gated by an ADMIN role guard at the router and
by a confirmation_name check inside the service. Caller passes the
project's ``name`` verbatim; the service refuses to delete anything if
the value doesn't match exactly.

Audit: every reset writes a ``settings_audit_log`` row with the mode,
the actor, and the per-table deletion counts in ``changed_fields``.

Retention holds (W22a ACT-05): a reset is refused (:class:`ResetBlockedByHold`)
while ANY live hold of the project exists — read under the project row lock,
after the confirmation check and before any count or delete.

Stored run documents (W32 F-7): when the caller passes a Mongo handle, both
modes first delete the runs' run-scoped Mongo documents (every
``retention_service._MONGO_PLANS`` collection, decision reports included),
BEFORE the Postgres delete — the ``test_runs`` CASCADE destroys the only run →
document mapping. The per-collection counts are returned as ``documents`` and
recorded in the audit row; ``documents`` is ``None`` when no store was given
(not measured, never zeros). A Mongo error propagates: the request fails, the
Postgres transaction rolls back (nothing in Postgres is deleted), the documents
already deleted stay deleted, and the admin's retry completes the reset — the
same position as the nightly purge's execute order. The runs' object-store
files and search-index entries are not visited (F-W32-1).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import retention_hold_service

from app.models.postgres import (
    CanonicalTestCase,
    FlakyQuarantineRequest,
    KnowledgeSource,
    ManagedTestCase,
    PerfBaseline,
    Project,
    Release,
    ReleaseGatePolicy,
    SettingsAuditLog,
    SuiteMembership,
    SuiteMembershipEvent,
    TestPlan,
    TestRun,
    TestStrategy,
    TestSuite,
)

logger = structlog.get_logger(__name__)


# Table → SQLAlchemy model map for the explicit "full reset" sweep.
# CASCADE from test_runs.id covers run-derived rows (test_cases,
# ai_analysis, failure_clusters, release_decisions, agent_pipeline_runs,
# run_intelligence_snapshots, etc.). What's listed here is everything
# project-scoped that is NOT cascaded by deleting test_runs.
#
# ``suite_memberships`` / ``suite_membership_events`` must be listed
# EXPLICITLY. Neither is reachable by cascade from anything this reset
# deletes: a membership names its suite by ``suite_name`` (a String, not a
# FK to ``test_suites.id``), and its only CASCADE is on ``projects.id`` —
# which a reset never deletes, because the Project row is a deliberate
# keep. Its three ``test_runs`` pointers are all SET NULL. Leaving the rows
# behind is not inert: ``suite_sync_service`` still writes this table on
# every ingest (migration 0075 planned to move those writes onto
# ``canonical_test_cases`` in a Phase 2 that has not happened), so a
# surviving row makes the first post-reset ingest take its "already seen"
# branch — the test is counted unchanged/restored instead of added,
# ``first_seen_run_id`` is never assigned and stays NULL forever, and any
# survivor absent from that first run is marked ``needs_review`` and moved
# to the ``-deleted`` bucket. A "green-field" project would report
# restoring and deleting tests it had never ingested.
#
# ORDER IS LOAD-BEARING. ``canonical_test_cases.test_suite_id`` is NOT NULL
# with ``ON DELETE RESTRICT`` — deliberately, so a suite that still holds
# cases cannot be dropped out from under them. It therefore has to be
# deleted BEFORE ``test_suites``. With the two the other way round, the
# ``full`` reset raised ForeignKeyViolation on every project that had ever
# ingested a run (canonical rows survive the ``test_runs`` delete — their
# run pointers are SET NULL), the transaction rolled back, and nothing at
# all was deleted, ``test_runs`` included.
_FULL_RESET_TABLES = [
    ("canonical_test_cases", CanonicalTestCase),
    ("test_suites", TestSuite),
    ("suite_memberships", SuiteMembership),
    ("suite_membership_events", SuiteMembershipEvent),
    ("releases", Release),
    ("release_gate_policies", ReleaseGatePolicy),
    ("perf_baselines", PerfBaseline),
    ("flaky_quarantine_requests", FlakyQuarantineRequest),
    ("managed_test_cases", ManagedTestCase),
    ("test_plans", TestPlan),
    ("test_strategies", TestStrategy),
    ("knowledge_sources", KnowledgeSource),
]


class ConfirmationMismatch(Exception):
    """Raised when the typed confirmation_name doesn't match the project's name."""


class ProjectNotFound(Exception):
    """Raised when the project_id doesn't resolve to an active project."""


class ResetBlockedByHold(Exception):
    """A live retention hold of the project refuses the reset (W22a ACT-05, D-4).

    ANY live hold refuses — project, release, run or pack: a ``runs`` reset
    deletes held runs, a ``full`` reset held releases, and a held pack would
    lose its release link. ``reasons`` are the ``Held: <reason>`` sentences.
    Raised after the confirmation check and BEFORE any count or delete.
    """

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


async def reset_project(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    mode: str,
    confirmation_name: str,
    actor_id: Optional[uuid.UUID] = None,
    actor_name: Optional[str] = None,
    mongo: Any | None = None,
) -> dict:
    """Delete project-scoped data. Returns per-table counts.

    Raises:
        ProjectNotFound: project_id has no active Project row.
        ConfirmationMismatch: ``confirmation_name`` is not exactly equal
            to ``project.name``. The check is case-sensitive — typing
            ``acme`` for project ``Acme`` will reject.
        ValueError: ``mode`` is not one of ``"runs"`` | ``"full"``.

    The whole reset (deletes + audit log) commits in a single transaction
    so a failure mid-way leaves no orphans.

    ``mongo`` (W32 F-7): when given, the runs' Mongo documents are deleted
    first and returned as ``documents`` (see the module docstring); when
    ``None``, ``documents`` is ``None`` (not measured).
    """
    if mode not in ("runs", "full"):
        raise ValueError(f"Unknown reset mode: {mode!r}")

    # ``FOR UPDATE`` serialises concurrent resets on the same project.
    # Two admins clicking "Reset" at once previously raced: the first
    # cascaded the deletes and committed; the second saw an empty
    # project (everything already gone) and produced a phantom 200
    # response with no audit-trail signal that nothing happened. With
    # the row lock the second call blocks here until the first commits,
    # then proceeds with counts=0 — the audit log row records the
    # no-op explicitly.
    project = (
        await db.execute(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if project is None or not project.is_active:
        raise ProjectNotFound(f"Project {project_id} not found or inactive")

    if confirmation_name != project.name:
        raise ConfirmationMismatch(
            "confirmation_name must match the project name exactly"
        )

    # W22a: read UNDER the project row lock, so a hold placed concurrently
    # (place_hold takes FOR SHARE on this row) either commits first and is
    # seen here, or waits until this reset has committed or rolled back.
    held = await retention_hold_service.any_live_hold(
        db, project_id=project_id, now=datetime.now(timezone.utc)
    )
    if held:
        raise ResetBlockedByHold([hold.sentence() for hold in held])

    # Count first, then delete in a single transaction. Pre-counting
    # serves two purposes: (1) the audit log captures real numbers even
    # if a downstream count would need an extra round trip, (2) the UI
    # can render "deleted N test runs, M test cases, …" without an
    # additional API call.
    counts: dict[str, int] = {}

    runs_count = (
        await db.execute(
            select(func.count(TestRun.id)).where(TestRun.project_id == project_id)
        )
    ).scalar_one()
    counts["test_runs"] = int(runs_count or 0)

    # W32 F-7: the Mongo phase BEFORE the Postgres delete (the CASCADE below
    # destroys the run → document mapping). ``None`` = no store given.
    documents: Optional[dict[str, int]] = None
    if mongo is not None:
        documents = {}
        if counts["test_runs"]:
            from app.services import retention_service

            documents = await retention_service.delete_project_run_documents(
                db, mongo, project_id=project_id
            )

    if counts["test_runs"]:
        await db.execute(delete(TestRun).where(TestRun.project_id == project_id))

    if mode == "full":
        for table_name, model in _FULL_RESET_TABLES:
            n = (
                await db.execute(
                    select(func.count(model.id)).where(model.project_id == project_id)
                )
            ).scalar_one()
            counts[table_name] = int(n or 0)
            if n:
                await db.execute(
                    delete(model).where(model.project_id == project_id)
                )

    # Audit log — committed with the deletes so the trail is consistent
    # with what actually happened. ``setting_key`` is namespaced so
    # operators can filter for these specifically.
    audit_row = SettingsAuditLog(
        setting_key=f"project_reset.{mode}",
        action="reset",
        actor_id=actor_id,
        actor_name=actor_name,
        changed_fields={
            "project_id": str(project_id),
            "project_name": project.name,
            "mode": mode,
            "counts": counts,
            # W32 F-7: per-collection Mongo deletes; None = no store given.
            "documents": documents,
        },
    )
    db.add(audit_row)

    # ``full`` mode deletes every release (see _FULL_RESET_TABLES), which
    # breaks the active-release invariant (migration 0150): the project would
    # have none until the hourly sweep noticed, and every run ingested in
    # between would fall through the attribution ladder with nowhere to land.
    # Re-provision in the SAME transaction that emptied it, so the invariant is
    # never observably false and the sweep's violation counter stays a real
    # signal rather than counting our own doing.
    #
    # ``runs`` mode is deliberately excluded — it deletes test_runs only and
    # leaves releases intact, so provisioning here would mint a spurious
    # placeholder on every runs-only reset.
    if mode == "full":
        from app.services.release_lifecycle_service import (
            ensure_active_release_for_new_project,
        )

        await ensure_active_release_for_new_project(db, project)

    await db.commit()
    # VIZ-212: after the commit, so a reset that rolls back leaves the cache
    # (and its epoch) exactly as it was.
    from app.services.cache_service import bump_analytics_epoch

    await bump_analytics_epoch(project_id)

    logger.info(
        "project_reset_completed",
        project_id=str(project_id),
        project_name=project.name,
        mode=mode,
        counts=counts,
        documents_total=sum(documents.values()) if documents is not None else None,
        actor_id=str(actor_id) if actor_id else None,
    )
    return {"mode": mode, "deleted": counts, "documents": documents}
