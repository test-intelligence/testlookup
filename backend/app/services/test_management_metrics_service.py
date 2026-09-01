"""Post-commit, best-effort metrics for test-case governance writes."""
from __future__ import annotations

import uuid
from typing import Awaitable, Callable, Iterable

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import CanonicalTestCase, ManagedTestCase, TestCaseLifecycleState

logger = structlog.get_logger("services.test_management_metrics")

_QUEUE_KEY = "test_management_post_commit_metrics"
_STATE_REFRESH_KEY = "test_management_state_metric_projects"
_ORPHAN_REFRESH_KEY = "test_management_orphan_metric_projects"


def _emit_counter(name: str, label_values: tuple[str, ...]) -> None:
    from app.core.metrics import (
        test_case_deprecations_without_reason_total,
        test_case_promotions_total,
        test_case_transitions_total,
    )

    if name == "transition":
        test_case_transitions_total.labels(*label_values).inc()
    elif name == "promotion":
        test_case_promotions_total.labels(*label_values).inc()
    elif name == "deprecation_without_reason":
        test_case_deprecations_without_reason_total.labels(*label_values).inc()
    else:
        raise ValueError(f"Unknown test-management counter: {name}")


def stage_test_management_counter(
    db: AsyncSession,
    name: str,
    label_values: Iterable[str],
) -> None:
    """Queue a counter for emission after the request session commits.

    Lightweight test doubles often do not expose SQLAlchemy's ``info`` dict;
    those fall back to an immediate best-effort emission without changing
    production transaction semantics.
    """
    values = tuple(str(value) for value in label_values)
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        info.setdefault(_QUEUE_KEY, []).append((name, values))
        return
    try:
        _emit_counter(name, values)
    except Exception:
        logger.warning("test_management_metric_failed", metric=name)


def _stage_project_refresh(db: AsyncSession, key: str, project_id: uuid.UUID) -> None:
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        info.setdefault(key, set()).add(project_id)


def stage_test_case_state_refresh(db: AsyncSession, project_id: uuid.UUID) -> None:
    _stage_project_refresh(db, _STATE_REFRESH_KEY, project_id)


def stage_orphan_gauge_refresh(db: AsyncSession, project_id: uuid.UUID) -> None:
    _stage_project_refresh(db, _ORPHAN_REFRESH_KEY, project_id)


async def _refresh_state_gauge(db: AsyncSession, project_id: uuid.UUID) -> None:
    from app.core.metrics import test_cases_by_state

    result = await db.execute(
        select(ManagedTestCase.status, func.count(ManagedTestCase.id))
        .where(ManagedTestCase.project_id == project_id)
        .group_by(ManagedTestCase.status)
    )
    counts = {state: int(count) for state, count in result.all()}
    for state in TestCaseLifecycleState:
        test_cases_by_state.labels(str(project_id), state.value).set(
            counts.get(state.value, 0)
        )


async def _refresh_orphan_gauge(db: AsyncSession, project_id: uuid.UUID) -> None:
    from app.core.metrics import automation_cases_orphaned

    count = int(
        (
            await db.execute(
                select(func.count(CanonicalTestCase.id)).where(
                    CanonicalTestCase.project_id == project_id,
                    CanonicalTestCase.status == "deleted",
                    CanonicalTestCase.retirement_confirmed_at.is_(None),
                )
            )
        ).scalar()
        or 0
    )
    automation_cases_orphaned.labels(str(project_id)).set(count)


async def _run_gauge_refresh(
    request_db: AsyncSession,
    refresh: Callable[[AsyncSession, uuid.UUID], Awaitable[None]],
    project_id: uuid.UUID,
) -> None:
    """Keep best-effort post-commit reads off the request transaction.

    PostgreSQL marks a transaction failed after a statement error.  Reusing
    the request session for gauge reads could therefore turn an already-
    committed mutation into a response-time 500.  Production AsyncSessions
    receive a short-lived read session; lightweight unit doubles keep using
    themselves.
    """
    if not isinstance(request_db, AsyncSession):
        await refresh(request_db, project_id)
        return

    from app.db.postgres import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as metrics_db:
        await refresh(metrics_db, project_id)


async def emit_staged_test_management_metrics(db: AsyncSession) -> None:
    info = getattr(db, "info", None)
    if not isinstance(info, dict):
        return
    queued = list(info.pop(_QUEUE_KEY, []))
    state_projects = set(info.pop(_STATE_REFRESH_KEY, set()))
    orphan_projects = set(info.pop(_ORPHAN_REFRESH_KEY, set()))
    for name, values in queued:
        try:
            _emit_counter(name, values)
        except Exception:
            logger.warning("test_management_metric_failed", metric=name)
    for project_id in state_projects:
        try:
            await _run_gauge_refresh(db, _refresh_state_gauge, project_id)
        except Exception:
            logger.warning(
                "test_management_state_metric_failed",
                project_id=str(project_id),
            )
    for project_id in orphan_projects:
        try:
            await _run_gauge_refresh(db, _refresh_orphan_gauge, project_id)
        except Exception:
            logger.warning(
                "automation_orphan_metric_failed",
                project_id=str(project_id),
            )


def discard_staged_test_management_metrics(db: AsyncSession) -> None:
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        info.pop(_QUEUE_KEY, None)
        info.pop(_STATE_REFRESH_KEY, None)
        info.pop(_ORPHAN_REFRESH_KEY, None)
