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
"""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
_FULL_RESET_TABLES = [
    ("test_suites", TestSuite),
    ("canonical_test_cases", CanonicalTestCase),
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


async def reset_project(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    mode: str,
    confirmation_name: str,
    actor_id: Optional[uuid.UUID] = None,
    actor_name: Optional[str] = None,
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
    # no-op explicitly. See docs/DATABASE_AUDIT_2026-05-16.md (P2-5).
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
        },
    )
    db.add(audit_row)
    await db.commit()

    logger.info(
        "project_reset_completed",
        project_id=str(project_id),
        project_name=project.name,
        mode=mode,
        counts=counts,
        actor_id=str(actor_id) if actor_id else None,
    )
    return {"mode": mode, "deleted": counts}
