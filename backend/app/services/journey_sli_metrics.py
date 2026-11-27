"""Read-only matured SQL-intent cohort snapshots, never task-success counts.

Windows apply to acceptance deadlines, not a success-only completion window.
Every mature retained intent has exactly one outcome, including never-completed
work. Replicas/restarts rederive the same absolute gauges; never sum replicas
or use increase/rate on these gauges. These are current retained cohorts, not
an immutable historical ledger: authorized retention and same-ID AI resumes
can change prior outcomes. collect_bounded owns scrape timeout/freshness.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from time import monotonic
from copy import deepcopy
from collections.abc import Iterator

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import Metric
from prometheus_client.registry import Collector

from sqlalchemy import and_, case, extract, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.metrics import (
    journey_cohort_events, journey_cohort_observed_at, journey_source_supported,
)
from app.services.journey_sli_contracts import COHORT_OUTCOMES, JOURNEYS, WINDOWS

CACHE_SECONDS = 30.0


@dataclass(frozen=True)
class _CachedSnapshot:
    key: tuple[str, str, int]
    expires_at: float
    observed: datetime
    exported: bytes


_CACHE: _CachedSnapshot | None = None
_PUBLISHED: _CachedSnapshot | None = None


def _render_snapshot(*, installation: str, environment: str, observed: datetime, snapshot: dict) -> bytes:
    # Unregistered templates never write native multiprocess metric files.
    # Copies belong to this one completed snapshot; encoding is immutable.
    events, timestamp, supported = deepcopy((journey_cohort_events, journey_cohort_observed_at, journey_source_supported))
    scope = [installation, environment]
    for contract in JOURNEYS:
        supported.add_metric([*scope, contract.name], int(contract.supported and contract.name in snapshot))
    for journey, values in snapshot.items():
        for (window, outcome), count in values.items():
            events.add_metric([*scope, journey, window, outcome], count)
    timestamp.add_metric(scope, observed.timestamp())

    class Bundle(Collector):
        def collect(self) -> Iterator[Metric]:
            return iter((events, timestamp, supported))

    registry = CollectorRegistry()
    registry.register(Bundle())
    return generate_latest(registry)


def render_journey_cohort_metrics() -> bytes:
    """One serving-child bundle; append only after a successful bounded read.

    Native multiprocess per-Gauge aggregation cannot preserve an atomic group
    of changing outcome counts. This exact byte string keeps counts, support
    and actual observation time together through concurrent child scrapes.
    """
    key = (settings.OBSERVABILITY_INSTALLATION, settings.APP_ENV, settings.AI_PIPELINE_DEADLINE_SECONDS)
    return _PUBLISHED.exported if _PUBLISHED is not None and _PUBLISHED.key == key and key[0] else b""


def _elapsed(db: AsyncSession, finished, accepted):
    if db.get_bind().dialect.name == "sqlite":
        # SQLite is supported only by isolated test fixtures. Its date math is
        # millisecond precision, unlike PostgreSQL's native timestamp epoch.
        return func.round((func.julianday(finished) - func.julianday(accepted)) * 86400, 3)
    return extract("epoch", finished) - extract("epoch", accepted)


def _classification(*, accepted, completed, status, known, success, seconds, now, elapsed, degraded=None):
    invalid = or_(
        ~status.in_(known),
        and_(status.in_(success), or_(completed.is_(None), completed < accepted, completed > now)),
    )
    branches = [
        (invalid, "unknown"),
        (status == "failed", "failed"),
        (and_(status.in_(success), elapsed > seconds), "late"),
    ]
    if degraded is not None:
        branches.append((and_(status.in_(success), degraded), "degraded"))
    branches.append((status.in_(success), "good"))
    return case(*branches, else_="overdue")


async def _counts_pair(db: AsyncSession, *, accepted, outcome, seconds: int, now: datetime,
                       second_outcome=None, subset=None) -> tuple[dict, dict]:
    # Bound the database range, result cardinality and time budget. The outer
    # collector additionally bounds connection/cancellation behavior.
    latest = now - timedelta(seconds=seconds)
    earliest = latest - timedelta(seconds=max(WINDOWS.values()))
    columns = [outcome.label("outcome")]
    if second_outcome is not None:
        columns.append(second_outcome.label("second_outcome"))
    columns.extend(func.sum(case((accepted > latest - timedelta(seconds=span), 1), else_=0)).label(name)
                   for name, span in WINDOWS.items())
    if subset is not None:
        columns.extend(func.sum(case((and_(accepted > latest - timedelta(seconds=span), subset), 1), else_=0)).label("subset_" + name)
                       for name, span in WINDOWS.items())
    statement = select(*columns).where(accepted > earliest, accepted <= latest)
    groups = (outcome, second_outcome) if second_outcome is not None else (outcome,)
    rows = (await db.execute(statement.group_by(*groups))).all()
    snapshot = {(window, name): 0 for window in WINDOWS for name in COHORT_OUTCOMES}
    second = dict(snapshot)
    for row in rows:
        name = row[0]
        second_name = row[1] if second_outcome is not None else name
        offset = 2 if second_outcome is not None else 1
        counts = row[offset:offset + len(WINDOWS)]
        second_counts = row[offset + len(WINDOWS):] if subset is not None else counts
        if name not in COHORT_OUTCOMES or second_name not in COHORT_OUTCOMES:
            raise ValueError("unknown journey cohort outcome")
        for window, count in zip(WINDOWS, counts):
            snapshot[(window, name)] += int(count or 0)
        for window, count in zip(WINDOWS, second_counts):
            second[(window, second_name)] += int(count or 0)
    return snapshot, second


async def read_journey_cohorts(db: AsyncSession, *, now: datetime) -> dict:
    """Derive all supported cohorts in one read-only repeatable snapshot."""
    from app.models.postgres import AgentPipelineRun as Pipeline, RunDownstreamOutbox as Outbox
    from app.services.run_downstream_outbox import (
        EXECUTION_LEASE_SECONDS, FINALIZATION_RECOVERY_GRACE_SECONDS, _OPERATIONS,
    )

    # This conservative diagnostic deadline includes one finalization recovery
    # grace plus one execution lease. It is proposed, not a product promise.
    downstream_seconds = FINALIZATION_RECOVERY_GRACE_SECONDS + EXECUTION_LEASE_SECONDS
    outbox_outcome = _classification(
        accepted=Outbox.created_at, completed=Outbox.completed_at, status=Outbox.status,
        known=("waiting", "pending", "sending", "published", "processing", "completed", "failed"),
        success=("completed",), seconds=downstream_seconds, now=now,
        elapsed=_elapsed(db, Outbox.completed_at, Outbox.created_at),
    )
    # Unknown operations cannot quietly count as successful work.
    outbox_outcome = case((~Outbox.operation.in_(_OPERATIONS), literal("unknown")), else_=outbox_outcome)
    downstream, live = await _counts_pair(
        db, accepted=Outbox.created_at, outcome=outbox_outcome, seconds=downstream_seconds,
        now=now, subset=Outbox.operation == "persist_live_session",
    )
    result = {"downstream": downstream, "live_materialization": live}
    ai_seconds = settings.AI_PIPELINE_DEADLINE_SECONDS
    # A zero pipeline deadline disables that execution bound. Do not invent
    # an AI time objective or suppress otherwise valid outbox measurements.
    if type(ai_seconds) is not int or ai_seconds <= 0:
        return result
    arguments = dict(
        accepted=Pipeline.created_at, completed=Pipeline.completed_at, status=Pipeline.status,
        known=("pending", "running", "retry_wait", "completed", "passed", "failed"),
        success=("completed", "passed"), seconds=ai_seconds, now=now,
        elapsed=_elapsed(db, Pipeline.completed_at, Pipeline.created_at),
    )
    quality = Pipeline.execution_metadata["stage_quality"].as_string()
    # Preserve JSON type. Casting "false" or numeric 0 to SQL boolean would
    # turn malformed/legacy metadata into positive full-quality evidence.
    if db.get_bind().dialect.name == "sqlite":
        explicit_no_fallback = func.json_type(Pipeline.execution_metadata, "$.fallback_used") == "false"
    else:
        explicit_no_fallback = and_(
            func.json_typeof(Pipeline.execution_metadata["fallback_used"]) == "boolean",
            Pipeline.execution_metadata["fallback_used"].as_string() == "false",
        )
    # No explicit quality evidence is not proof of a full-quality model result.
    # The writer omits stage_quality for its normal path, but always persists
    # fallback_used. Unknown quality markers are conservatively degraded too.
    degraded = or_(explicit_no_fallback.is_distinct_from(True), and_(quality.is_not(None), quality != "normal"))
    completion, full_quality = await _counts_pair(
        db, accepted=Pipeline.created_at, outcome=_classification(**arguments),
        second_outcome=_classification(**arguments, degraded=degraded), seconds=ai_seconds, now=now,
    )
    result.update(ai_completion=completion, ai_reported_full_quality=full_quality)
    return result


async def refresh_journey_cohort_metrics(*, now: datetime | None = None) -> bool:
    """Publish a complete scoped snapshot, or leave prior values explicitly stale."""
    global _CACHE, _PUBLISHED
    installation, environment = settings.OBSERVABILITY_INSTALLATION, settings.APP_ENV
    if not installation:
        return True  # no objective series, no database work, no inferred site
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("journey observation requires timezone")
    observed = observed.astimezone(timezone.utc)
    key = (installation, environment, settings.AI_PIPELINE_DEADLINE_SECONDS)
    cached = _CACHE if now is None else None  # deterministic explicit-now tests bypass cache
    if cached is not None and cached.key == key and monotonic() < cached.expires_at:
        completed = cached
    else:
        from app.db.postgres import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            if db.get_bind().dialect.name == "postgresql":
                # Both queries must see one committed state despite concurrent
                # workers. Must precede the first data query in this transaction.
                await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                await db.execute(text("SET LOCAL statement_timeout = '750ms'"))
            snapshot = await read_journey_cohorts(db, now=observed)
        completed = _CachedSnapshot(key, monotonic() + CACHE_SECONDS, observed, _render_snapshot(
            installation=installation, environment=environment, observed=observed, snapshot=snapshot,
        ))
        if now is None:
            _CACHE = completed
    _PUBLISHED = completed
    return True
