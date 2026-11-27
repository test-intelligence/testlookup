"""Post-commit, best-effort metrics for test-case governance writes."""
from __future__ import annotations

from typing import Iterable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metric_dimensions import ACTOR_ROLES, LIFECYCLE_STATES, bounded_dimension

logger = structlog.get_logger("services.test_management_metrics")

_QUEUE_KEY = "test_management_post_commit_metrics"


def _emit_counter(name: str, label_values: tuple[str, ...]) -> None:
    from app.core.metrics import (
        test_case_deprecations_without_reason_total,
        test_case_promotions_total,
        test_case_transitions_total,
    )

    if name == "transition":
        # Services keep the transaction payload's project identity for their
        # domain boundary. It is never a Prometheus dimension.
        _project, previous, current, role = label_values
        test_case_transitions_total.labels(
            bounded_dimension(previous, LIFECYCLE_STATES),
            bounded_dimension(current, LIFECYCLE_STATES),
            bounded_dimension(role, ACTOR_ROLES),
        ).inc()
    elif name == "promotion":
        test_case_promotions_total.inc()
    elif name == "deprecation_without_reason":
        test_case_deprecations_without_reason_total.inc()
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


async def emit_staged_test_management_metrics(db: AsyncSession) -> None:
    info = getattr(db, "info", None)
    if not isinstance(info, dict):
        return
    queued = list(info.pop(_QUEUE_KEY, []))
    for name, values in queued:
        try:
            _emit_counter(name, values)
        except Exception:
            logger.warning("test_management_metric_failed", metric=name)


def discard_staged_test_management_metrics(db: AsyncSession) -> None:
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        info.pop(_QUEUE_KEY, None)
