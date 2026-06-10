from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    Project,
    Release,
    ReleaseTestRunLink,
    TestAttachment,
    TestCase,
    TestRun,
    TestStep,
)


def serialize_run(run: TestRun) -> dict:
    data = {}
    for column in run.__table__.columns:
        value = getattr(run, column.name)
        if hasattr(value, "isoformat"):
            data[column.name] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            data[column.name] = str(value)
        else:
            data[column.name] = value
    return data


def enrich_runs_with_release(
    runs: list[TestRun],
    release_map: dict[str, dict[str, str | None]],
    project_map: dict[str, str] | None = None,
    run_seq_map: dict[str, int] | None = None,
) -> list[dict]:
    enriched = []
    for run in runs:
        item = serialize_run(run)
        release = release_map.get(str(run.id), {})
        item["release_name"] = release.get("name")
        item["release_id"] = release.get("id")
        if project_map is not None:
            item["project_name"] = project_map.get(str(run.project_id)) if run.project_id else None
        if run_seq_map is not None:
            item["run_seq"] = run_seq_map.get(str(run.id))
        enriched.append(item)
    return enriched


# Suite normalisation expression used by ``fetch_run_seq_map``. NULL +
# empty-string + whitespace-only suite names all collapse to a single
# "unnamed" partition so a run that never had ``primary_suite_name`` set
# still gets a stable sequence number within its project.
_SUITE_NORM = func.lower(func.trim(func.coalesce(TestRun.primary_suite_name, "")))


async def fetch_run_seq_map(
    db: AsyncSession, run_ids: list[uuid.UUID],
) -> dict[str, int]:
    """Per-(project, primary_suite_name) incremental run number, starting at 1.

    The sequence is computed across the **entire** history of the
    partitions involved — not just the requested ``run_ids`` — so a run's
    number is stable as the user paginates, filters, or revisits the
    page weeks later. Filtering to only the page rows would shift the
    sequence per request, which would defeat the whole point of a
    human-readable identifier.

    Strategy:
      1. Look up each requested run's ``(project_id, suite_key)`` pair.
      2. Run a single window-function pass over EVERY run in those
         partitions, with stable tie-breaker ``ORDER BY created_at ASC,
         id ASC`` so equal-timestamp inserts don't swap numbers.
      3. Return ``{run_id: rn}`` for the originally requested ids.

    Empty input → empty result, no queries fired. Used by ``/runs``,
    ``/live``, and ``/my-failures`` so a given run's "Run #N" label
    matches everywhere it appears.
    """
    if not run_ids:
        return {}

    # 1. Pairs in scope.
    pair_q = select(
        TestRun.project_id,
        _SUITE_NORM.label("suite_key"),
    ).where(TestRun.id.in_(run_ids)).distinct()
    pairs = list((await db.execute(pair_q)).all())
    if not pairs:
        return {}

    # 2. Build the per-pair filter as an OR of AND-pairs. SQLAlchemy
    # supports ``tuple_(a, b).in_(...)`` but its asyncpg compilation
    # path is finicky with mixed-type tuples — explicit ORs are
    # uglier but bulletproof. Partition count is bounded by the page
    # size (one project + a handful of suites in practice).
    # Attribute access (not unpacking) so mock rows that aren't
    # tuple-iterable still work in unit tests.
    pair_filters = [
        and_(TestRun.project_id == p.project_id, _SUITE_NORM == p.suite_key)
        for p in pairs
    ]
    ranked = (
        select(
            TestRun.id,
            func.row_number().over(
                partition_by=(TestRun.project_id, _SUITE_NORM),
                order_by=(TestRun.created_at.asc(), TestRun.id.asc()),
            ).label("rn"),
        )
        .where(or_(*pair_filters))
        .subquery()
    )
    final_q = select(ranked.c.id, ranked.c.rn).where(ranked.c.id.in_(run_ids))
    return {str(rid): int(rn) for rid, rn in (await db.execute(final_q)).all()}


async def fetch_project_name_map(db: AsyncSession, project_ids: list[uuid.UUID]) -> dict[str, str]:
    if not project_ids:
        return {}
    result = await db.execute(
        select(Project.id, Project.name).where(Project.id.in_(project_ids))
    )
    return {str(pid): name for pid, name in result.all()}


async def paginate_query(db: AsyncSession, query, page: int, size: int):
    """Paginate a SELECT statement: run the count, then the page query.

    The count query strips ``ORDER BY`` before wrapping in a subquery.
    Without this, PostgreSQL has to materialize the full sorted result
    just to throw it away for the count — ``EXPLAIN ANALYZE`` on the
    run-list query dropped from 0.141ms/0.748ms (exec/plan) to
    0.032ms/0.083ms after the strip. Small absolute numbers on a small
    table, but the effect compounds under concurrency and scales with
    row count.
    """
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset((page - 1) * size).limit(size))
    return result.scalars().all(), total, -(-total // size)


async def fetch_release_map(db: AsyncSession, run_ids: list[uuid.UUID]) -> dict[str, dict[str, str | None]]:
    if not run_ids:
        return {}
    result = await db.execute(
        select(ReleaseTestRunLink.test_run_id, Release.id, Release.name)
        .join(Release, Release.id == ReleaseTestRunLink.release_id)
        .where(ReleaseTestRunLink.test_run_id.in_(run_ids))
    )
    return {
        str(test_run_id): {"id": str(release_id), "name": release_name}
        for test_run_id, release_id, release_name in result.all()
    }


def _normalise_suite_name(suite_name: str | None) -> str:
    return (suite_name or "").strip().lower()


def _run_suite_filter(suite_name: str | None):
    suite_key = _normalise_suite_name(suite_name)
    if not suite_key:
        return None
    case_exists = (
        select(TestCase.id)
        .where(
            TestCase.test_run_id == TestRun.id,
            func.lower(func.trim(func.coalesce(TestCase.suite_name, "Unknown Suite"))) == suite_key,
        )
        .exists()
    )
    return or_(
        func.lower(func.trim(func.coalesce(TestRun.primary_suite_name, "Unknown Suite"))) == suite_key,
        case_exists,
    )


async def list_project_runs(
    db: AsyncSession,
    project_id: str | None,
    page: int,
    size: int,
    status: str | None = None,
    release_id: str | None = None,
    accessible_project_ids: set | None = None,
    days: int | None = 6,
    suite_name: str | None = None,
):
    """Paginated test run listing, enriched with release + project_name.

    Performance notes:
      * Filters are built once and reused by both the count query and the
        items query — no duplicated WHERE logic.
      * The count query counts ``TestRun.id`` directly (no subquery wrap,
        no ``ORDER BY``).
      * ``project_name`` is fetched via ``LEFT JOIN`` in the items query
        instead of a separate round-trip, saving one DB call per request.
      * Release names stay in a single ``WHERE id IN (...)`` follow-up
        rather than joining — releases are 1:1 with runs in practice but
        the column isn't UNIQUE, so joining risks row-duplication we'd
        have to DISTINCT away. One extra query is cheaper than an extra
        DISTINCT.
    """
    filters = []
    if days and days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        filters.append(TestRun.created_at >= cutoff)
    if project_id:
        filters.append(TestRun.project_id == project_id)
    if accessible_project_ids is not None:
        # Tenant isolation (defence-in-depth): non-admin callers are confined
        # to their memberships even when an explicit project_id is supplied.
        # Previously this was an ``elif`` — a provided project_id skipped the
        # membership filter entirely, so a caller passing ``?project_id=<foreign>``
        # could read another tenant's runs if the router forgot to verify it.
        # Applying both filters keeps the service safe regardless of caller.
        filters.append(TestRun.project_id.in_(accessible_project_ids))
    if status:
        filters.append(TestRun.status == status)
    if release_id:
        linked_ids_q = select(ReleaseTestRunLink.test_run_id).where(
            ReleaseTestRunLink.release_id == uuid.UUID(release_id)
        )
        filters.append(TestRun.id.in_(linked_ids_q))
    suite_filter = _run_suite_filter(suite_name)
    if suite_filter is not None:
        filters.append(suite_filter)

    # Count query: strips ORDER BY, counts by PK, no subquery wrap.
    count_stmt = select(func.count(TestRun.id)).where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Items query: LEFT JOIN project to pick up project_name in one trip.
    items_stmt = (
        select(TestRun, Project.name.label("project_name"))
        .outerjoin(Project, Project.id == TestRun.project_id)
        .where(*filters)
        .order_by(TestRun.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(items_stmt)).all()
    runs = [row[0] for row in rows]
    project_map = {
        str(row[0].project_id): row.project_name
        for row in rows
        if row[0].project_id and row.project_name
    }

    run_ids = [run.id for run in runs]
    release_map = await fetch_release_map(db, run_ids)
    run_seq_map = await fetch_run_seq_map(db, run_ids)
    pages = -(-total // size)
    return (
        enrich_runs_with_release(runs, release_map, project_map, run_seq_map=run_seq_map),
        total,
        pages,
    )


async def get_run_with_release(db: AsyncSession, run_id: uuid.UUID):
    run = (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one_or_none()
    if not run:
        # Live-session fallback: a brand-new live session that hasn't
        # drained any events yet has a LiveSession row but no TestRun
        # row — the drainer only creates the TestRun on first non-empty
        # drain (every 30s) and ``persist_live_session`` creates it on
        # session-complete. Without this fallback, clicking the row
        # from /live during the first 30s 404s. (Bug 2026-05-19.)
        #
        # ``LiveSession.id`` == the ``run_id`` returned to the SDK in the
        # modern flow (``create_live_session``: ``session_id =
        # str(uuid.uuid4())`` then ``run_id = session_id``). For the
        # SDK-supplied slug flow, ``LiveSession.run_id`` is the slug; we
        # don't match on that here — the user-facing /live page emits
        # the canonical UUID so clicking always hits this branch.
        from app.models.postgres import LiveSession
        live = (
            await db.execute(select(LiveSession).where(LiveSession.id == run_id))
        ).scalar_one_or_none()
        if live is None:
            return None
        run = _synthesize_run_from_live_session(live)
    release_map = await fetch_release_map(db, [run_id])
    project_ids = [run.project_id] if run.project_id else []
    project_map = await fetch_project_name_map(db, project_ids)
    run_seq_map = await fetch_run_seq_map(db, [run_id])
    return enrich_runs_with_release(
        [run], release_map, project_map, run_seq_map=run_seq_map,
    )[0]


def _synthesize_run_from_live_session(live) -> "TestRun":
    """Build a TestRun-shaped object from a still-active LiveSession.

    Detached from the session — never added to ``db``. Caller renders
    it via ``enrich_runs_with_release`` exactly like a real row, so the
    page handlers don't need a separate code path for in-flight runs.
    The aggregates are zero because no events have been drained yet;
    once the 30s drainer fires (or the SDK sends ``run_complete``) the
    real TestRun row materialises and this fallback stops firing.
    """
    from app.models.postgres import LaunchStatus
    run = TestRun(
        id=live.id,
        project_id=live.project_id,
        build_number=live.build_number or str(live.id)[:8],
        trigger_source="live_stream",
        ingestion_source="live",
        status=LaunchStatus.IN_PROGRESS,
        total_tests=int(getattr(live, "total_tests", 0) or 0),
        passed_tests=0,
        failed_tests=0,
        skipped_tests=0,
        broken_tests=0,
        primary_suite_name=getattr(live, "suite_name", None),
        start_time=live.started_at,
        end_time=live.started_at,
        created_at=live.started_at,
    )
    return run


async def list_run_test_cases(
    db: AsyncSession,
    run_id: uuid.UUID,
    page: int,
    size: int,
    status: str | None = None,
    suite: str | None = None,
):
    query = select(TestCase).where(TestCase.test_run_id == run_id)
    if status:
        query = query.where(TestCase.status == status.upper())
    if suite:
        from app.services.sql_utils import like_contains
        query = query.where(TestCase.suite_name.ilike(like_contains(suite), escape="\\"))
    items, total, pages = await paginate_query(
        db, query.order_by(TestCase.status, TestCase.test_name), page, size
    )

    # Live-buffer fallback. While a live-stream run is in progress, its
    # per-test rows live in the Redis event buffer (LIVE_TESTCASES_KEY)
    # and are only materialised into ``test_cases`` by the Phase 4.5
    # drainer (every 30s) or at close_session. If the drainer hasn't
    # ticked yet — or Celery delivery is degraded — the run-detail page
    # and the summary report show an empty table even though /live shows
    # live counts (those read the HINCRBY hash directly). Bridge the gap
    # by reading the buffer here so /runs/{id}/tests reflects the same
    # real-time per-test data the /live page does. Only fires when
    # Postgres has nothing yet; once the drainer/persist lands real rows
    # this branch is skipped. (Bug 2026-05-20.)
    if total == 0:
        live = await _live_buffer_test_cases(run_id, page, size, status, suite)
        if live is not None:
            return live

    return items, total, pages


async def _live_buffer_test_cases(
    run_id: uuid.UUID,
    page: int,
    size: int,
    status: str | None = None,
    suite: str | None = None,
) -> tuple[list[dict], int, int] | None:
    """Return per-test rows synthesised from the Redis live buffer for an
    in-progress live-stream run, or ``None`` when there's no buffer.

    The shape mirrors ``schemas.TestCaseSummary`` so the response model
    serialises it the same as DB-backed rows. ``id`` is a deterministic
    uuid5 of (run_id, fingerprint) so React keys stay stable across the
    page's 5s polling and rows don't flicker as new events arrive.
    """
    import hashlib
    import json as _json

    try:
        from app.db.redis_client import get_redis
        from app.streams import LIVE_TESTCASES_KEY
    except Exception:
        return None

    try:
        redis = get_redis()
        list_key = LIVE_TESTCASES_KEY.format(run_id=str(run_id))
        raw_entries = await redis.lrange(list_key, 0, -1)
    except Exception:
        # Redis unavailable — fall back to the empty DB result rather than 500.
        return None

    if not raw_entries:
        return None

    status_filter = status.upper() if status else None
    suite_lower = suite.strip().lower() if suite else None

    rows: list[dict] = []
    for raw in raw_entries:
        try:
            ev = _json.loads(raw)
        except Exception:
            continue
        if ev.get("event_type") and ev.get("event_type") != "test_result":
            # Skip heartbeats / logs / metrics — only test_result rows
            # belong in the per-test table.
            continue
        ev_status = (ev.get("status") or "UNKNOWN").upper()
        if status_filter and ev_status != status_filter:
            continue
        ev_suite = ev.get("suite_name")
        if suite_lower and (ev_suite or "").strip().lower() != suite_lower:
            continue
        test_name = ev.get("test_name") or ""
        class_name = ev.get("class_name") or ""
        fp = hashlib.md5(f"{test_name}:{class_name}".encode()).hexdigest()
        ts_ms = ev.get("timestamp_ms")
        try:
            created = (
                datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
                if ts_ms else datetime.now(timezone.utc)
            )
        except Exception:
            created = datetime.now(timezone.utc)
        dur = ev.get("duration_ms")
        rows.append({
            "id": uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{fp}"),
            "test_run_id": run_id,
            "test_name": test_name,
            "suite_name": ev_suite,
            "class_name": class_name or None,
            "status": ev_status,
            "duration_ms": int(dur) if isinstance(dur, (int, float)) else None,
            "severity": None,
            "feature": None,
            "failure_category": None,
            "has_attachments": False,
            "created_at": created,
            "assigned_to_user_id": None,
        })

    if not rows:
        return None

    # Dedup by fingerprint — the buffer holds one entry per execution, but
    # the per-test table is keyed by logical test. Keep the most recent
    # execution per fingerprint (later entries in the append-only list win).
    by_fp: dict = {}
    for r in rows:
        by_fp[r["id"]] = r
    deduped = list(by_fp.values())

    # Stable ordering matches the DB path (status, then name).
    deduped.sort(key=lambda r: (str(r["status"]), r["test_name"]))

    total = len(deduped)
    pages = -(-total // size) if size else 1
    start = (page - 1) * size
    page_items = deduped[start:start + size]
    return page_items, total, pages


async def get_test_steps_tree(
    db: AsyncSession,
    run_id: uuid.UUID,
    test_id: uuid.UUID,
) -> dict | None:
    """Return the nested step/attachment tree for a TestCase in a run.

    The granular snapshot is LATEST-RUN-ONLY and anchored to the test's
    project-scoped ``CanonicalTestCase`` (NOT the per-run ``test_cases`` row),
    so the snapshot reflects whichever run most recently ingested this logical
    test — even if that's a newer run than ``run_id``. The ``test_id`` is
    resolved to its canonical anchor; the steps are fetched by that anchor.

    Returns ``None`` when the TestCase doesn't belong to the run (404 at the
    router), or an empty-tree dict when the case has no captured steps yet.
    """
    tc = (
        await db.execute(
            select(TestCase).where(
                TestCase.id == test_id,
                TestCase.test_run_id == run_id,
            )
        )
    ).scalar_one_or_none()
    if tc is None:
        return None

    canonical_id = tc.canonical_test_case_id
    steps_rows: list[TestStep] = []
    att_rows: list[TestAttachment] = []
    if canonical_id is not None:
        steps_rows = list(
            (
                await db.execute(
                    select(TestStep)
                    .where(TestStep.canonical_test_case_id == canonical_id)
                    .order_by(TestStep.ordinal)
                )
            ).scalars().all()
        )
        att_rows = list(
            (
                await db.execute(
                    select(TestAttachment).where(
                        TestAttachment.canonical_test_case_id == canonical_id
                    )
                )
            ).scalars().all()
        )

    # Index attachments by owning step id (NULL = test-level).
    att_by_step: dict[uuid.UUID | None, list[dict]] = {}
    for a in att_rows:
        att_by_step.setdefault(a.test_step_id, []).append({
            "id": a.id,
            "test_step_id": a.test_step_id,
            "name": a.name,
            "source_ref": a.source_ref,
            "media_type": a.media_type,
            "created_at": a.created_at,
        })

    # Build the step nodes, then nest by parent_step_id preserving ordinal order.
    node_by_id: dict[uuid.UUID, dict] = {}
    for s in steps_rows:
        node_by_id[s.id] = {
            "id": s.id,
            "parent_step_id": s.parent_step_id,
            "ordinal": s.ordinal,
            "depth": s.depth,
            "name": s.name,
            "keyword": s.keyword,
            "status": s.status,
            "duration_ms": s.duration_ms,
            "start_ms": s.start_ms,
            "assertion_message": s.assertion_message,
            "assertion_trace": s.assertion_trace,
            "expected_value": s.expected_value,
            "actual_value": s.actual_value,
            "parameters": s.parameters,
            "created_at": s.created_at,
            "steps": [],
            "attachments": att_by_step.get(s.id, []),
        }

    roots: list[dict] = []
    for s in steps_rows:  # steps_rows is ordinal-ordered → children keep order
        node = node_by_id[s.id]
        parent = node_by_id.get(s.parent_step_id) if s.parent_step_id else None
        if parent is not None:
            parent["steps"].append(node)
        else:
            roots.append(node)

    return {
        "run_id": str(run_id),
        "test_id": str(test_id),
        "test_name": tc.test_name,
        "status": tc.status,
        "step_count": tc.step_count,
        "retry_count": tc.retry_count,
        "is_flaky_run": tc.is_flaky_run,
        "stack_trace": tc.stack_trace,
        "steps": roots,
        "attachments": att_by_step.get(None, []),
    }
