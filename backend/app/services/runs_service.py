from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from sqlalchemy import ARRAY, Integer, Numeric, case, cast, false, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    CanonicalTestCase,
    ManagedTestCase,
    Project,
    Release,
    ReleaseTestRunLink,
    TestAttachment,
    TestCase,
    TestRun,
    TestStep,
    TestStepRun,
)
from app.services.flaky_step_flip import StepFlipReport, compute_step_flips

# Step status vocab (strict TestStatus values, see ingestion._map_step_status).
# A step "failed" if it ended FAILED or BROKEN; a step "passed" if PASSED.
_STEP_FAILED_STATUSES = ("FAILED", "BROKEN")
_STEP_PASSED_STATUS = "PASSED"


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
    suite_map: dict[str, list[str]] | None = None,
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
        if suite_map is not None:
            # Read-side half of the effective-suite rule: the run-level
            # columns are a denormalisation of ``test_cases.suite_name``, and
            # only the live-stream path ever wrote them. Batch-ingested runs
            # therefore carried NULL despite every one of their test cases
            # naming a suite, and the UI rendered "Unknown suite".
            #
            # Fill only what is missing — a recorded label is a user-visible
            # choice and must never be overwritten by a derived one.
            derived = suite_map.get(str(run.id)) or []
            if not item.get("suite_names") and derived:
                item["suite_names"] = derived
            if not item.get("primary_suite_name") and derived:
                item["primary_suite_name"] = derived[0]
        enriched.append(item)
    return enriched


async def fetch_run_suites_map(
    db: AsyncSession, run_ids: list[uuid.UUID],
) -> dict[str, list[str]]:
    """Distinct suite names per run, read from the test cases themselves.

    ``TestRun.primary_suite_name`` / ``suite_names`` are a denormalisation
    maintained by the live-stream drain path. Nothing populated them for
    batch-ingested runs, so twelve runs whose test cases named three suites
    between them all reported no suite at all, and every picker built from
    that list read "Unknown suite".

    One grouped query for the whole page — never per run. Empty input fires
    no query.
    """
    if not run_ids:
        return {}
    from app.models.postgres import TestCase

    rows = (
        await db.execute(
            select(TestCase.test_run_id, TestCase.suite_name)
            .where(
                TestCase.test_run_id.in_(run_ids),
                TestCase.suite_name.isnot(None),
                func.trim(TestCase.suite_name) != "",
            )
            .distinct()
        )
    ).all()

    grouped: dict[str, list[str]] = {}
    for run_id, suite in rows:
        grouped.setdefault(str(run_id), []).append(suite)
    # Sorted so the "primary" pick is deterministic rather than
    # whatever order the database happened to return.
    for suites in grouped.values():
        suites.sort()
    return grouped


# Suite normalisation expression used by ``fetch_run_seq_map``. NULL +
# empty-string + whitespace-only suite names all collapse to a single
# "unnamed" partition so a run that never had ``primary_suite_name`` set
# still gets a stable sequence number within its project.
#
# The '' is a SQL literal, not a bind parameter: migration 0169 indexes this
# exact expression, and under a generic plan a bind parameter is ``$n``, which
# never matches the literal the index was built with (re-audit N17; M6).
#
# md5 of the normalised name, not the name (review R-B45-D-1): the name is a
# String(500), up to 2,000 bytes in four-byte characters, and an index row past
# btree's 2,704-byte limit fails the INSERT -- an ingest that 500s. The hash is
# 32 bytes whatever the name, and equal names hash equal, so the partitions --
# and every "Run #N" -- are exactly what they were.
_SUITE_NORM = func.md5(
    func.lower(func.trim(func.coalesce(TestRun.primary_suite_name, literal_column("''"))))
)


# list_project_runs gives a multi-project caller one index-ordered branch per
# project up to this many projects (re-audit N18). Past it, the statement and
# its plan grow with the membership list, and walking 0170's global index to
# the LIMIT is the better trade.
_PER_PROJECT_BRANCH_CAP = 50


# The key's constants are SQL literals, not bind parameters: see
# natural_build_number_key (re-audit M6).
_A_DIGIT = literal_column("'[0-9]'")
_NON_DIGIT_RUN = literal_column("'[^0-9]+'")
_SPACE = literal_column("' '")
_EVERY_MATCH = literal_column("'g'")


def natural_build_number_key(column=None):
    """PostgreSQL natural-sort key for free-form CI build numbers.

    Numeric chunks compare as a numeric array, so ``ui-2`` sorts before
    ``ui-10``. Values without digits return NULL and are deliberately placed
    after numbered builds by callers. The textual build number and persistence
    timestamp remain tie-breakers because build-number schemes can collide.

    Re-audit M6. Migration 0167 indexes this exact expression, which puts two
    constraints on it:

    * Its constants are SQL literals, not bind parameters. Postgres uses an
      expression index only for a query expression written identically, and
      under a generic plan -- which asyncpg's prepared statements can reach --
      a bind parameter is ``$n``, never the literal the index was built with.
    * It casts to ``numeric[]``, not ``bigint[]``. A digit run longer than
      nineteen digits overflowed ``bigint``: the listing errored, and behind
      the index the run's INSERT would have failed instead. The order is
      identical for every value ``bigint`` could hold.
    """
    if column is None:
        column = TestRun.build_number
    numeric_chunks = func.string_to_array(
        func.trim(func.regexp_replace(column, _NON_DIGIT_RUN, _SPACE, _EVERY_MATCH)),
        _SPACE,
    )
    return case(
        (column.op("~")(_A_DIGIT), cast(numeric_chunks, ARRAY(Numeric))),
        else_=None,
    )


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
      2. Number EVERY run in each of those partitions, naturally ordered
         by numeric build-number chunks, then the raw build number,
         persistence timestamp, and id -- one UNION ALL branch per pair,
         each read in that order from migration 0169's index, so no page
         sorts a suite's history (re-audit N17).
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

    # 2. One branch per (project, suite) partition, UNION ALL-ed (re-audit
    # N17). Each branch pins both partition keys with equalities, so its
    # rows arrive already in window order from migration 0169's index
    # (project_id, suite key, natural key, build_number, created_at, id):
    # no Sort. A single window over an OR of pairs, as this used to be,
    # can only reach those rows through a BitmapOr, which returns them
    # unordered -- so every page sorted each suite's whole history.
    # Partition count is bounded by the page size (one project and a
    # handful of suites in practice). Attribute access (not unpacking) so
    # mock rows that aren't tuple-iterable still work in unit tests.
    from sqlalchemy import union_all  # noqa: PLC0415

    branches = [
        select(
            TestRun.id.label("id"),
            func.row_number().over(
                order_by=(
                    natural_build_number_key().asc().nulls_last(),
                    TestRun.build_number.asc(),
                    TestRun.created_at.asc(),
                    TestRun.id.asc(),
                ),
            ).label("rn"),
        ).where(TestRun.project_id == p.project_id, _SUITE_NORM == p.suite_key)
        for p in pairs
    ]
    ranked = (branches[0] if len(branches) == 1 else union_all(*branches)).subquery()
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
    """Map each run id to the ONE release its badge should show.

    ``release_test_run_links`` is many-to-many by design — a hotfix build can
    genuinely be validated for both 2.3.1 and 2.4.0 — but this returns a dict
    keyed on ``test_run_id``, so before migration 0151 a multi-linked run
    silently kept whichever row Postgres happened to return last and the run
    list showed a non-deterministic release name.

    That was not hypothetical: ``link_run_to_release`` never removes a prior
    link, so any run that was auto-attributed and then re-labelled by hand
    already carried two rows.

    Filtering on ``is_primary`` makes the answer deterministic. The partial
    unique index ``ix_rtr_links_primary`` guarantees at most one such row per
    run, so the dict comprehension can no longer lose data.
    """
    if not run_ids:
        return {}
    result = await db.execute(
        select(ReleaseTestRunLink.test_run_id, Release.id, Release.name)
        .join(Release, Release.id == ReleaseTestRunLink.release_id)
        .where(
            ReleaseTestRunLink.test_run_id.in_(run_ids),
            ReleaseTestRunLink.is_primary.is_(True),
        )
    )
    return {
        str(test_run_id): {"id": str(release_id), "name": release_name}
        for test_run_id, release_id, release_name in result.all()
    }


def _run_suite_filter(suite_name: str | tuple[str, ...] | None):
    """The run list's suite rule -- one name or several (OR). The clause lives
    in ``analytics_scope`` (VIZ-201 "one rule, one module"); one name compiles
    to exactly the statement this function built before it took a list."""
    from app.services.analytics_scope import run_list_suite_clause

    return run_list_suite_clause(suite_name)


async def list_project_runs(
    db: AsyncSession,
    project_id: str | None,
    page: int,
    size: int,
    status: str | None = None,
    release_id: str | tuple[str, ...] | None = None,
    accessible_project_ids: set | None = None,
    days: int | None = 6,
    suite_name: str | tuple[str, ...] | None = None,
):
    """Paginated test run listing, enriched with release + project_name.

    ``release_id`` and ``suite_name`` take one value (the legacy call, whose
    statements are unchanged) or a tuple of several: OR within each, AND
    across. The router resolves and authorises every release id first
    (``analytics_scope.resolve_release_query_scope_list``).

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
    # DELETE /projects/{id} is a SOFT delete — it flips is_active to False, and
    # only the project LIST honoured that flag. Runs from deleted projects kept
    # appearing here: rows for a project the user cannot open, filter by, or
    # navigate to, and which is gone from every picker. Measured live at 1422
    # runs across 43 deleted projects against 67 in the 2 live ones.
    #
    # my_failures already carries the same exclusion (_live_projects_only); this
    # is the identical defect one endpoint over.
    #
    # It goes in the SHARED filters list deliberately: `filters` is reused by
    # both the row query and the count query, and a count that includes rows the
    # list excludes is the next bug along (see F-038, /analytics/defects
    # returning items: [] with total: 5).
    filters.append(
        TestRun.project_id.in_(select(Project.id).where(Project.is_active.is_(True)))
    )
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
    if release_id and not isinstance(release_id, (str, uuid.UUID)):
        # VIZ-303: several releases, OR within the dimension -- the shared
        # predicate on the same denormalised column (the sentinel among them
        # adds ``IS NULL``). A malformed id cannot reach here: the router
        # parses every id (C1 ``release_id_format``) before any query.
        from app.core.release_filter import release_predicate

        filters.extend(release_predicate(list(release_id)))
    elif release_id:
        # Filters the DENORMALIZED primary column, not membership of
        # ``release_test_run_links``.
        #
        # This used to select every run with ANY link to the release, while
        # every other release-scoped read in the product — the analytics
        # endpoints, the KPI cards, the trend charts — filters
        # ``primary_release_id``. Behind a single global release picker that
        # meant the run list and the numbers above it disagreed for any run
        # linked to two releases, with nothing on screen to explain it. Worse,
        # this same function already reads ``is_primary`` when it builds each
        # row's release BADGE, so a run could appear in release B's list
        # wearing a badge that said release A.
        #
        # The accepted cost: a run linked to a release as a SECONDARY link no
        # longer appears in that release's run list. It was already absent from
        # that release's analytics, so this makes the two agree rather than
        # introducing a new exclusion.
        from app.core.release_filter import is_unattributed

        if is_unattributed(str(release_id)):
            filters.append(TestRun.primary_release_id.is_(None))
            release_uuid = None
        else:
            try:
                release_uuid = uuid.UUID(str(release_id))
            except (ValueError, TypeError, AttributeError):
                # Fail CLOSED. An unparseable release filter must not fall through
                # to "no filter" — that would answer a narrow question with every
                # run in the project, which reads as data the caller did not ask
                # for rather than as an error. The router validates first and 422s;
                # this is defence-in-depth for any other caller.
                filters.append(false())
            else:
                filters.append(TestRun.primary_release_id == release_uuid)
    suite_filter = _run_suite_filter(suite_name)
    if suite_filter is not None:
        filters.append(suite_filter)

    # Count query: strips ORDER BY, counts by PK, no subquery wrap.
    count_stmt = select(func.count(TestRun.id)).where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Items query: LEFT JOIN project to pick up project_name in one trip.
    order_keys = (
        natural_build_number_key().desc().nulls_first(),
        TestRun.build_number.desc(),
        TestRun.created_at.desc(),
        TestRun.id.desc(),
    )
    items_stmt = (
        select(TestRun, Project.name.label("project_name"))
        .outerjoin(Project, Project.id == TestRun.project_id)
    )
    if (
        not project_id
        and accessible_project_ids
        # A member of ONE project takes the plain path below (QA-B45-D-4): its
        # membership filter is `project_id IN (x)`, an equality that 0167's
        # index serves with a LIMIT. As a one-branch join it planned, under a
        # generic plan, as a hash join over a Seq Scan of every run (59 ms at
        # 400k runs, against 0.15 ms).
        and 1 < len(accessible_project_ids) <= _PER_PROJECT_BRANCH_CAP
    ):
        # A member of several projects (re-audit N18). Walking a global
        # natural-order index and filtering membership passes every other
        # project's rows first; a BitmapOr over 0167's index returns rows
        # unordered and sorts them all. One branch per project instead: each
        # is 0167's index in order, stopped at the deepest row this page can
        # need, so the outer sort sees at most projects x page*size rows.
        from sqlalchemy import union_all  # noqa: PLC0415

        # A literal, not a bind parameter (QA-B45-D-4): under a generic plan a
        # bound LIMIT is `$n`, and the planner, unable to see how small it is,
        # estimated thousands of candidates per branch. int() keeps it a
        # number whatever the caller passed; page and size are the router's
        # validated integers.
        deepest = literal_column(str(int(page) * int(size)), Integer())
        branches = [
            select(TestRun.id.label("id"))
            .where(*filters, TestRun.project_id == member)
            .order_by(*order_keys)
            .limit(deepest)
            for member in sorted(accessible_project_ids, key=str)
        ]
        candidates = (
            branches[0] if len(branches) == 1 else union_all(*branches)
        ).subquery("page_candidates")
        items_stmt = items_stmt.join(candidates, candidates.c.id == TestRun.id)
    else:
        # One project (0167's index), or every live project -- the admin view
        # -- walking 0170's global natural-order index to the LIMIT.
        items_stmt = items_stmt.where(*filters)
    items_stmt = (
        items_stmt
        .order_by(*order_keys)
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
    suite_map = await fetch_run_suites_map(db, run_ids)
    pages = -(-total // size)
    return (
        enrich_runs_with_release(
            runs, release_map, project_map,
            run_seq_map=run_seq_map, suite_map=suite_map,
        ),
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
    suite_map = await fetch_run_suites_map(db, [run_id])
    return enrich_runs_with_release(
        [run], release_map, project_map,
        run_seq_map=run_seq_map, suite_map=suite_map,
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
    # per-test rows live in the per-run durable evidence Stream (with a
    # legacy LIST mirror during cutover) and are materialised by the
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
        from app.streams import LIVE_EVIDENCE_STREAM_KEY, LIVE_TESTCASES_KEY
        from app.services.ingestion_sanitization import sanitize_test_result_payload
    except Exception:
        return None

    try:
        redis = get_redis()
        from app.core.config import settings as _settings

        stream_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=str(run_id))
        list_key = LIVE_TESTCASES_KEY.format(run_id=str(run_id))
        events: list[dict] = []

        # Prefer the durable per-run Stream.  Its committed prefix is trimmed
        # by the drainer, so these are precisely the events not yet visible in
        # PostgreSQL.  During rolling upgrades an empty Stream/group key may
        # coexist with a legacy LIST; fall back when no test-result payload was
        # recoverable from the Stream.
        try:
            # One Stream row represents one batch. Bound the Redis reply by the
            # configured per-run evidence capacity; when operators explicitly
            # disable that limit, retain a defensive 50k-record read ceiling.
            stream_record_cap = int(
                _settings.LIVE_BUFFER_MAX_EVENTS_PER_RUN or 50_000
            )
            stream_entries = await redis.xrange(
                stream_key,
                min="-",
                max="+",
                count=max(1, stream_record_cap),
            )
        except Exception:
            stream_entries = []

        def _stream_field(fields: dict, name: str, default=None):
            return fields.get(name, fields.get(name.encode(), default))

        def _text(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value

        for _message_id, fields in stream_entries or []:
            # Current producers append exactly one manifest per accepted batch.
            # Expand it for the run-detail projection.  Validate cardinality so
            # a torn/corrupt manifest cannot present a partial batch as truth.
            raw_manifest = _stream_field(fields, "events_json")
            if raw_manifest is not None:
                try:
                    manifest = _json.loads(_text(raw_manifest))
                    event_ids = _json.loads(
                        _text(_stream_field(fields, "event_ids_json", "[]"))
                    )
                    event_count = int(
                        _text(_stream_field(fields, "event_count", "-1"))
                    )
                except Exception:
                    continue
                if (
                    not isinstance(manifest, list)
                    or not isinstance(event_ids, list)
                    or len(manifest) != event_count
                    or len(event_ids) != event_count
                ):
                    continue
                events.extend(
                    sanitize_test_result_payload(event)
                    for event in manifest
                    if isinstance(event, Mapping)
                    and event.get("event_type", "test_result") == "test_result"
                )
                continue

            # Rolling-upgrade fallback for the earlier one-entry-per-event
            # stream shape.
            event_type = _text(
                _stream_field(fields, "event_type", "test_result")
            )
            if event_type != "test_result":
                continue
            try:
                payload = _json.loads(
                    _text(_stream_field(fields, "payload", "{}"))
                )
            except Exception:
                continue
            events.append(sanitize_test_result_payload(payload))

        if not events:
            raw_entries = await redis.lrange(list_key, 0, -1)
            for raw in raw_entries:
                try:
                    events.append(sanitize_test_result_payload(_json.loads(raw)))
                except Exception:
                    continue
    except Exception:
        # Redis unavailable — fall back to the empty DB result rather than 500.
        return None

    if not events:
        return None

    status_filter = status.upper() if status else None
    suite_lower = suite.strip().lower() if suite else None

    rows: list[dict] = []
    for ev in events:
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
            "source_test_run_id": a.source_test_run_id,
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
            "parameters": _safe_step_parameters(s.parameters),
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
        "snapshot_source_test_run_id": (
            str(steps_rows[0].source_test_run_id)
            if steps_rows and steps_rows[0].source_test_run_id is not None
            else (
                str(att_rows[0].source_test_run_id)
                if att_rows and att_rows[0].source_test_run_id is not None
                else None
            )
        ),
        "steps": roots,
        "attachments": att_by_step.get(None, []),
    }


def _safe_step_parameters(value: object) -> list[dict] | dict | None:
    """Redact parameter payloads from every producer at response time."""
    if isinstance(value, list):
        return _safe_source_parameters(value)
    if isinstance(value, dict):
        from app.services.redaction_service import redact_dict
        safe = redact_dict(value)
        if not isinstance(safe, dict):
            return {"value": "[REDACTED]"}
        mode = str(safe.get("mode") or "").lower()
        if safe.get("masked") is True or mode in {"masked", "hidden"}:
            if "value" in safe:
                safe["value"] = None
            safe["masked"] = True
        return safe
    return None


def _safe_source_parameters(value: object) -> list[dict]:
    """Return only object parameters; malformed persisted JSON is omitted."""
    if not isinstance(value, list):
        return []
    from app.services.redaction_service import redact_dict
    result: list[dict] = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("mode") or "").lower()
        masked = bool(item.get("masked")) or mode in {"masked", "hidden"}
        safe = redact_dict(item)
        safe = safe if isinstance(safe, dict) else {"value": "[REDACTED]"}
        if masked:
            safe["value"] = None
            safe["masked"] = True
        result.append(safe)
    return result


def _safe_authored_parameters(value: object) -> list[dict]:
    """Normalize authored parameters and fail closed for marked secrets."""
    if not isinstance(value, list):
        return []
    from app.services.redaction_service import redact_dict
    result: list[dict] = []
    for item in value[:100]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        safe = redact_dict(item)
        safe = safe if isinstance(safe, dict) else {"name": item["name"]}
        mode = str(item.get("mode") or "").lower()
        if bool(item.get("masked")) or mode in {"masked", "hidden"}:
            safe["value"] = None
            safe["masked"] = True
        result.append(safe)
    return result


def _safe_source_links(value: object) -> list[dict[str, str | None]]:
    """Return only bounded link records accepted by the public contract."""
    if not isinstance(value, list):
        return []
    result: list[dict[str, str | None]] = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        result.append({
            "url": url[:2000],
            "name": item.get("name")[:500] if isinstance(item.get("name"), str) else None,
            "type": item.get("type")[:100] if isinstance(item.get("type"), str) else None,
        })
    return result


def _safe_source_labels(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        name, label_value = item.get("name"), item.get("value")
        if isinstance(name, str) and name.strip() and isinstance(label_value, str):
            result.append({"name": name[:255], "value": label_value[:500]})
    return result


def _safe_source_extensions(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    from app.services.redaction_service import redact_dict
    return redact_dict(dict(list(value.items())[:100])) or {}


def build_enriched_test_case_detail(test_case: TestCase, step_tree: dict) -> dict:
    """Build the versioned additive read contract from existing rows.

    This function is pure so the contract can be tested without a database. It
    preserves the legacy flat fields while grouping the same data into
    identity/classification/execution/provenance sections.
    """
    steps = step_tree.get("steps") if isinstance(step_tree.get("steps"), list) else []
    attachments = step_tree.get("attachments") if isinstance(step_tree.get("attachments"), list) else []
    tags = getattr(test_case, "tags", None) or []
    steps_present = bool(getattr(test_case, "steps_present", bool(steps)))
    # Sparse current executions must not surface a previous non-empty
    # canonical snapshot as if it belonged to this run.
    if hasattr(test_case, "steps_present") and not test_case.steps_present:
        steps = []
        if not getattr(test_case, "has_attachments", False):
            attachments = []
    source_run_id = step_tree.get("snapshot_source_test_run_id") or str(test_case.test_run_id)
    flat = {
        "id": test_case.id,
        "test_run_id": test_case.test_run_id,
        "test_name": test_case.test_name,
        "suite_name": test_case.suite_name,
        "class_name": test_case.class_name,
        "status": test_case.status,
        "duration_ms": test_case.duration_ms,
        "severity": test_case.severity,
        "feature": test_case.feature,
        "failure_category": test_case.failure_category,
        "has_attachments": bool(test_case.has_attachments),
        "step_count": getattr(test_case, "step_count", None),
        "assigned_to_user_id": getattr(test_case, "assigned_to_user_id", None),
        "created_at": test_case.created_at,
        "full_name": test_case.full_name,
        "package_name": test_case.package_name,
        "story": test_case.story,
        "epic": test_case.epic,
        "owner": test_case.owner,
        "tags": [str(tag) for tag in tags if tag is not None],
        "error_message": test_case.error_message,
        "minio_s3_prefix": test_case.minio_s3_prefix,
    }
    return {
        **flat,
        "contract": "test-case-detail",
        "schema_version": 1,
        "contract_version": "1",
        "identity": {
            "test_case_id": test_case.id,
            "test_run_id": test_case.test_run_id,
            "canonical_test_case_id": getattr(test_case, "canonical_test_case_id", None),
            "test_fingerprint": test_case.test_fingerprint,
            "test_name": test_case.test_name,
            "full_name": test_case.full_name,
            "source_uuid": getattr(test_case, "source_uuid", None),
            "source_history_id": getattr(test_case, "source_history_id", None),
            "source_test_case_id": getattr(test_case, "source_test_case_id", None),
            "uuid": getattr(test_case, "source_uuid", None),
            "history_id": getattr(test_case, "source_history_id", None),
            "fingerprint": test_case.test_fingerprint,
            "display_name": test_case.test_name,
        },
        "classification": {
            "suite_name": test_case.suite_name,
            "class_name": test_case.class_name,
            "package_name": test_case.package_name,
            "severity": test_case.severity,
            "feature": test_case.feature,
            "story": test_case.story,
            "epic": test_case.epic,
            "owner": test_case.owner,
            "tags": [str(tag) for tag in tags if tag is not None],
            "service_name": getattr(test_case, "service_name", None),
            "component_name": None,
            "suite": ({"name": test_case.suite_name, "legacy_name": test_case.suite_name}
                      if test_case.suite_name else None),
            "service": ({"name": test_case.service_name, "source": "explicit", "confidence": "high"}
                        if getattr(test_case, "service_name", None) else None),
            "components": [
                {"name": str(component), "source": "explicit", "confidence": "high"}
                for component in (getattr(test_case, "component_names", None) or [])
                if component
            ],
            "file_path": None,
            "framework": getattr(test_case, "parser_format", None),
            "language": None,
            "labels": _safe_source_labels(getattr(test_case, "source_labels", None)),
        },
        "execution": {
            "status": test_case.status,
            "duration_ms": test_case.duration_ms,
            "retry_count": getattr(test_case, "retry_count", None),
            "is_flaky_run": getattr(test_case, "is_flaky_run", None),
            "step_count": getattr(test_case, "step_count", None),
            "steps_present": steps_present,
            "has_attachments": bool(test_case.has_attachments),
            "failure_category": test_case.failure_category,
            "error_message": test_case.error_message,
            "stack_trace": getattr(test_case, "stack_trace", None),
            "parameters": _safe_source_parameters(getattr(test_case, "source_parameters", None)),
        },
        "provenance": {
            "source_test_run_id": source_run_id,
            "parser_format": getattr(test_case, "parser_format", None),
            "parser_version": getattr(test_case, "parser_version", None),
            "minio_s3_prefix": test_case.minio_s3_prefix,
            "format": getattr(test_case, "parser_format", None),
            "source_file": None,
            "field_sources": {
                "identity": "source_report" if getattr(test_case, "source_uuid", None) else "platform_fallback",
                "suite": "source_report" if test_case.suite_name else "unknown",
            },
            "warnings": [],
        },
        "steps_present": steps_present,
        "steps": steps,
        "attachments": attachments,
        "definition": step_tree.get("definition"),
        "links": _safe_source_links(getattr(test_case, "source_links", None)),
        "extensions": _safe_source_extensions(getattr(test_case, "source_extensions", None)),
    }


async def get_enriched_test_case_detail(
    db: AsyncSession,
    run_id: uuid.UUID,
    test_id: uuid.UUID,
) -> dict | None:
    """Return the enriched contract for a test authorized within ``run_id``."""
    result = await db.execute(
        select(TestCase, TestRun.project_id)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestCase.id == test_id, TestCase.test_run_id == run_id)
    )
    row = result.first()
    if row is None:
        return None
    test_case, project_id = row

    from app.services.feature_flags import is_enabled
    if not await is_enabled("test_case_rich_detail", db=db, project_id=project_id):
        return None

    definition = None
    managed = None
    if test_case.canonical_test_case_id:
        managed_id = (
            await db.execute(
                select(CanonicalTestCase.managed_test_case_id).where(
                    CanonicalTestCase.id == test_case.canonical_test_case_id,
                    CanonicalTestCase.project_id == project_id,
                )
            )
        ).scalar_one_or_none()
        if managed_id is not None:
            managed = (
                await db.execute(
                    select(ManagedTestCase).where(
                        ManagedTestCase.id == managed_id,
                        ManagedTestCase.project_id == project_id,
                    )
                )
            ).scalar_one_or_none()
    if managed is None and test_case.test_fingerprint:
        managed_result = await db.execute(
            select(ManagedTestCase)
            .where(
                ManagedTestCase.project_id == project_id,
                (ManagedTestCase.test_fingerprint == test_case.test_fingerprint)
                | (ManagedTestCase.title == test_case.test_name),
            )
            .order_by(ManagedTestCase.updated_at.desc())
            .limit(1)
        )
        managed = managed_result.scalar_one_or_none()
    if managed is not None:
        definition = {
            "id": managed.id,
            "version": managed.version,
            "title": managed.title,
            "description": managed.description,
            "objective": managed.objective,
            "preconditions": managed.preconditions,
            "expected_result": managed.expected_result,
            "test_data": managed.test_data,
            "steps": managed.steps,
            "parameters": _safe_authored_parameters(getattr(managed, "parameters", None)),
            "test_type": managed.test_type,
            "priority": managed.priority,
            "severity": managed.severity,
            "suite_name": managed.suite_name,
            "tags": managed.tags or [],
        }

    step_tree = await get_test_steps_tree(db, run_id, test_id)
    return build_enriched_test_case_detail(
        test_case,
        {**(step_tree or {"steps": [], "attachments": []}), "definition": definition},
    )


# ── Granular-step batched read helpers (Phase 5 enrichment) ─────────────────
#
# The LATEST-RUN-ONLY step snapshot is anchored to ``canonical_test_cases``
# (one snapshot per project + test_fingerprint). These helpers batch the read
# by a *set* of canonical ids so report/coverage/my-failures surfaces never
# N+1 over individual tests. They are pure reads — no staging, no commit.


async def first_failed_step_by_canonical(
    db: AsyncSession,
    canonical_ids: list[uuid.UUID] | set[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Map ``canonical_test_case_id -> first FAILED/BROKEN step name``.

    "First" = lowest ``ordinal`` among the failing steps of that canonical
    test's snapshot (ordinal is the depth-first insertion order, so the first
    failing step in document order). Canonical ids with no captured steps — or
    no failing step — are simply absent from the result, so callers default to
    ``None``.

    Batched: ONE query keyed by the whole set of canonical ids (expanding IN),
    NOT a per-test lookup. The snapshot is project-scoped by construction (the
    canonical anchor carries ``project_id``), so passing only the canonical ids
    the caller already resolved for its tenant keeps the read scoped.
    """
    ids = [c for c in {*canonical_ids} if c is not None]
    if not ids:
        return {}

    # Pull (canonical_id, ordinal, name) for failing steps only, ordered so the
    # lowest ordinal per canonical wins. A single pass over the ordered rows
    # keeps the FIRST seen per canonical.
    stmt = (
        select(TestStep.canonical_test_case_id, TestStep.ordinal, TestStep.name)
        .where(
            TestStep.canonical_test_case_id.in_(ids),
            TestStep.status.in_(_STEP_FAILED_STATUSES),
        )
        .order_by(TestStep.canonical_test_case_id, TestStep.ordinal)
    )
    out: dict[uuid.UUID, str] = {}
    for cid, _ordinal, name in (await db.execute(stmt)).all():
        if cid not in out:  # ordered by ordinal asc → first row is the first fail
            out[cid] = name
    return out


async def first_failed_step_by_fingerprint(
    db: AsyncSession,
    project_id: uuid.UUID | str,
    fingerprints: list[str] | set[str],
) -> dict[str, str]:
    """Map ``test_fingerprint -> first failed step name`` within a project.

    Resolves each fingerprint to its project-scoped ``CanonicalTestCase`` anchor
    (``test_fingerprint`` is not salted, so the project filter is what scopes
    the tenant), then reuses :func:`first_failed_step_by_canonical`. Two batched
    queries total — no N+1. Fingerprints without a snapshot (or without a
    failing step) are absent so callers default to ``None``.
    """
    fps = [f for f in {*fingerprints} if f]
    if not fps:
        return {}

    anchor_rows = (
        await db.execute(
            select(CanonicalTestCase.id, CanonicalTestCase.test_fingerprint).where(
                CanonicalTestCase.project_id == project_id,
                CanonicalTestCase.test_fingerprint.in_(fps),
            )
        )
    ).all()
    fp_by_canonical: dict[uuid.UUID, str] = {cid: fp for cid, fp in anchor_rows}
    if not fp_by_canonical:
        return {}

    by_canonical = await first_failed_step_by_canonical(db, list(fp_by_canonical))
    return {
        fp_by_canonical[cid]: name
        for cid, name in by_canonical.items()
        if cid in fp_by_canonical
    }


async def failing_step_detail_by_fingerprint(
    db: AsyncSession,
    project_id: uuid.UUID | str,
    fingerprints: list[str] | set[str],
) -> dict[str, dict]:
    """FLK-P5: map ``test_fingerprint -> {first_failing, total_steps,
    failing_step_count}`` for granular surgical attribution.

    ``first_failing`` is the lowest-ordinal FAILED/BROKEN step's full
    attribution payload (name, ordinal, keyword, assertion_message,
    expected/actual, assertion_trace) — or ``None`` when the snapshot has no
    failing step. From the LATEST-RUN-ONLY ``test_steps`` snapshot, project
    scoped via the canonical anchor. Two batched queries; pure read; never N+1.
    """
    fps = [f for f in {*fingerprints} if f]
    if not fps:
        return {}

    anchor_rows = (
        await db.execute(
            select(CanonicalTestCase.id, CanonicalTestCase.test_fingerprint).where(
                CanonicalTestCase.project_id == project_id,
                CanonicalTestCase.test_fingerprint.in_(fps),
            )
        )
    ).all()
    fp_by_canonical: dict[uuid.UUID, str] = {cid: fp for cid, fp in anchor_rows}
    if not fp_by_canonical:
        return {}

    rows = (
        await db.execute(
            select(
                TestStep.canonical_test_case_id,
                TestStep.ordinal,
                TestStep.status,
                TestStep.name,
                TestStep.keyword,
                TestStep.assertion_message,
                TestStep.expected_value,
                TestStep.actual_value,
                TestStep.assertion_trace,
            )
            .where(TestStep.canonical_test_case_id.in_(list(fp_by_canonical)))
            .order_by(TestStep.canonical_test_case_id, TestStep.ordinal)
        )
    ).all()

    # Fold per-canonical: total steps, failing count, and the first failing step.
    acc: dict[uuid.UUID, dict] = {}
    for r in rows:
        cid = r.canonical_test_case_id
        entry = acc.setdefault(cid, {"first_failing": None, "total_steps": 0, "failing_step_count": 0})
        entry["total_steps"] += 1
        if str(r.status).upper().rsplit(".", 1)[-1] in _STEP_FAILED_STATUSES:
            entry["failing_step_count"] += 1
            if entry["first_failing"] is None:  # rows are ordinal-ascending
                entry["first_failing"] = {
                    "ordinal": r.ordinal,
                    "name": r.name,
                    "keyword": r.keyword,
                    "assertion_message": r.assertion_message,
                    "expected_value": r.expected_value,
                    "actual_value": r.actual_value,
                    "assertion_trace": r.assertion_trace,
                }

    return {
        fp_by_canonical[cid]: detail
        for cid, detail in acc.items()
        if cid in fp_by_canonical
    }


async def step_success_by_canonical(
    db: AsyncSession,
    canonical_ids: list[uuid.UUID] | set[uuid.UUID],
) -> dict[uuid.UUID, tuple[int, int]]:
    """Map ``canonical_test_case_id -> (passed_steps, total_steps)``.

    Counts leaf+nested steps from the LATEST-RUN-ONLY snapshot. ``passed`` is
    the count of ``PASSED`` steps; ``total`` is every captured step. Canonical
    ids with no captured steps are absent (so a suite/test that has no step data
    yields ``None`` step-success at the caller). Batched single query.
    """
    ids = [c for c in {*canonical_ids} if c is not None]
    if not ids:
        return {}

    passed_col = func.sum(
        case((TestStep.status == _STEP_PASSED_STATUS, 1), else_=0)
    ).label("passed")
    total_col = func.count(TestStep.id).label("total")
    stmt = (
        select(TestStep.canonical_test_case_id, passed_col, total_col)
        .where(TestStep.canonical_test_case_id.in_(ids))
        .group_by(TestStep.canonical_test_case_id)
    )
    return {
        cid: (int(passed or 0), int(total or 0))
        for cid, passed, total in (await db.execute(stmt)).all()
        if int(total or 0) > 0
    }


async def step_flip_report_by_fingerprint(
    db: AsyncSession,
    project_id: uuid.UUID | str,
    fingerprints: list[str] | set[str],
    *,
    since: datetime | None = None,
    max_runs: int = 25,
) -> dict[str, StepFlipReport]:
    """FLK-P6 slice 3: read the per-run step history and compute cross-run
    step-flip per fingerprint.

    Unlike :func:`failing_step_detail_by_fingerprint`, which reads the
    LATEST-RUN-ONLY ``test_steps`` snapshot, this reads ``test_step_runs`` —
    which RETAINS one outcome row per ``(canonical, run, ordinal)`` (migration
    0097) — so it can answer *which step oscillated PASSED<->FAILED across runs*.
    This is the DB read FLK-P6 slice 2 deferred: it assembles the per-run window
    (ordered oldest->newest, capped to the most recent ``max_runs`` runs) that
    :func:`compute_step_flips` expects and returns its report per fingerprint.

    Project scoped via the canonical anchor; ``since`` bounds the window by
    ``TestRun.created_at``. Two batched queries; pure read; never N+1. A
    fingerprint that resolves to a canonical but has <2 runs of step history maps
    to an "insufficient history" report, so callers can distinguish "no flip"
    from "no history"; a fingerprint with no canonical anchor is absent.
    """
    fps = [f for f in {*fingerprints} if f]
    if not fps:
        return {}

    anchor_rows = (
        await db.execute(
            select(CanonicalTestCase.id, CanonicalTestCase.test_fingerprint).where(
                CanonicalTestCase.project_id == project_id,
                CanonicalTestCase.test_fingerprint.in_(fps),
            )
        )
    ).all()
    fp_by_canonical: dict[uuid.UUID, str] = {cid: fp for cid, fp in anchor_rows}
    if not fp_by_canonical:
        return {}

    filters = [TestStepRun.canonical_test_case_id.in_(list(fp_by_canonical))]
    if since is not None:
        filters.append(TestRun.created_at >= since)
    rows = (
        await db.execute(
            select(
                TestStepRun.canonical_test_case_id,
                TestStepRun.source_test_run_id,
                TestStepRun.ordinal,
                TestStepRun.status,
                TestStepRun.name,
            )
            .join(TestRun, TestRun.id == TestStepRun.source_test_run_id)
            .where(*filters)
            # Oldest->newest per canonical (the order compute_step_flips wants);
            # run id + ordinal are deterministic tiebreaks when created_at
            # collides for runs ingested together.
            .order_by(
                TestStepRun.canonical_test_case_id,
                TestRun.created_at.asc(),
                TestStepRun.source_test_run_id,
                TestStepRun.ordinal,
            )
        )
    ).all()

    # Fold the flat, ordered rows into per-canonical per-run windows. ``order``
    # keeps run ids oldest->newest (first-seen wins, the query is ordered);
    # ``steps`` accumulates each run's step outcomes in ordinal order.
    per_canonical: dict[uuid.UUID, dict] = {}
    for r in rows:
        bucket = per_canonical.setdefault(
            r.canonical_test_case_id, {"order": [], "steps": {}}
        )
        run_id = r.source_test_run_id
        if run_id not in bucket["steps"]:
            bucket["order"].append(run_id)
            bucket["steps"][run_id] = []
        bucket["steps"][run_id].append(
            {"ordinal": r.ordinal, "name": r.name, "status": r.status}
        )

    reports: dict[str, StepFlipReport] = {}
    for cid, fp in fp_by_canonical.items():
        bucket = per_canonical.get(cid)
        if not bucket:
            # Resolved canonical with no retained step history yet — emit the
            # empty-window report so the caller sees "insufficient history".
            reports[fp] = compute_step_flips([])
            continue
        # Cap to the most recent ``max_runs`` runs; the window stays
        # oldest->newest so adjacent-run comparison is unaffected.
        run_ids = bucket["order"][-max_runs:] if max_runs and max_runs > 0 else bucket["order"]
        window = [
            {"run_id": str(run_id), "steps": bucket["steps"][run_id]}
            for run_id in run_ids
        ]
        reports[fp] = compute_step_flips(window)

    return reports


async def step_flip_report_for_test(
    db: AsyncSession,
    run_id: uuid.UUID,
    test_id: uuid.UUID,
    *,
    since: datetime | None = None,
    max_runs: int = 25,
) -> dict | None:
    """FLK-P6 slice 4: cross-run step-flip report for one ``(run_id, test_id)``.

    READ-ONLY surface for the per-test detail view. Resolves the test's
    ``test_fingerprint`` + ``project_id`` from the PROVIDED ``run_id`` (the router
    guards it with ``require_run_access`` — IDOR ratchet), then defers to the
    batched, project-scoped :func:`step_flip_report_by_fingerprint`. No DB writes.

    Returns ``None`` (→ 404 at the router) when ``test_id`` doesn't belong to
    ``run_id``. When the test has no retained per-run step history yet, the
    embedded report is the empty-window "insufficient history" report so the UI
    can distinguish "no flip" from "no history".
    """
    row = (
        await db.execute(
            select(TestCase.test_fingerprint, TestRun.project_id)
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestCase.id == test_id, TestCase.test_run_id == run_id)
        )
    ).first()
    if row is None:
        return None
    fingerprint, project_id = row[0], row[1]
    if not fingerprint:
        # No fingerprint anchor → no cross-run identity to compute flips over.
        return {
            "run_id": str(run_id),
            "test_id": str(test_id),
            "test_fingerprint": None,
            "report": compute_step_flips([]).to_dict(),
        }

    reports = await step_flip_report_by_fingerprint(
        db, project_id, [fingerprint], since=since, max_runs=max_runs
    )
    report = reports.get(fingerprint) or compute_step_flips([])
    return {
        "run_id": str(run_id),
        "test_id": str(test_id),
        "test_fingerprint": fingerprint,
        "report": report.to_dict(),
    }


async def step_flip_report_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    since: datetime | None = None,
    max_runs: int = 25,
    max_tests: int = 200,
) -> dict | None:
    """FLK-P6 slice 5: run-level roll-up of cross-run step-flip.

    READ-ONLY surface for the run-intelligence view. Slice 4 answered "which step
    oscillated for THIS test"; this answers "which TESTS in this run have a
    flickering step", so a QA engineer triaging a whole run sees the step-level
    flakiness across it without opening each test. Resolves the run's
    ``project_id`` from the PROVIDED ``run_id`` (the router guards it with
    ``require_run_access`` — IDOR ratchet), gathers the run's fingerprint-anchored
    tests, and defers to the batched, project-scoped
    :func:`step_flip_report_by_fingerprint`. No DB writes; never N+1 (one resolve
    + the batched read's two queries).

    Returns ``None`` (→ 404 at the router) when ``run_id`` has no ``TestRun`` row.
    Only tests whose report has at least one flip are returned in ``tests``
    (sorted by total flips desc, then name); ``tests_analyzed`` /
    ``tests_with_flips`` / ``total_flips`` summarise the window. ``max_tests``
    bounds the fingerprints fed to the batched read; ``truncated`` is ``True`` (and
    surfaced to the UI — no silent cap) when the run has more anchored tests than
    that.
    """
    run_row = (
        await db.execute(select(TestRun.project_id).where(TestRun.id == run_id))
    ).first()
    if run_row is None:
        return None
    project_id = run_row[0]

    # Fingerprints are unique per run (uq on (test_run_id, test_fingerprint)), so
    # one row == one logical test. Order by name for a deterministic truncation
    # boundary; fetch one extra to detect "more than max_tests" without a count.
    fetch_limit = max_tests + 1 if max_tests and max_tests > 0 else None
    stmt = (
        select(TestCase.id, TestCase.test_name, TestCase.test_fingerprint, TestCase.status)
        .where(TestCase.test_run_id == run_id, TestCase.test_fingerprint.isnot(None))
        .order_by(TestCase.test_name, TestCase.id)
    )
    if fetch_limit is not None:
        stmt = stmt.limit(fetch_limit)
    test_rows = (await db.execute(stmt)).all()

    truncated = fetch_limit is not None and len(test_rows) > max_tests
    if truncated:
        test_rows = test_rows[:max_tests]

    meta_by_fp: dict[str, dict] = {}
    for test_id, test_name, fingerprint, status in test_rows:
        # First occurrence wins; fingerprints are unique per run anyway.
        meta_by_fp.setdefault(
            fingerprint,
            {
                "test_id": str(test_id),
                "test_name": test_name,
                "test_fingerprint": fingerprint,
                "status": str(status),
            },
        )

    reports = (
        await step_flip_report_by_fingerprint(
            db, project_id, list(meta_by_fp), since=since, max_runs=max_runs
        )
        if meta_by_fp
        else {}
    )

    tests: list[dict] = []
    total_flips = 0
    for fingerprint, meta in meta_by_fp.items():
        report = reports.get(fingerprint)
        if report is None or not report.has_step_flip:
            continue
        total_flips += report.total_flips
        tests.append({**meta, "report": report.to_dict()})

    tests.sort(key=lambda t: (-t["report"]["total_flips"], t["test_name"]))

    return {
        "run_id": str(run_id),
        "project_id": str(project_id),
        "tests_analyzed": len(meta_by_fp),
        "tests_with_flips": len(tests),
        "total_flips": total_flips,
        "truncated": truncated,
        "tests": tests,
    }
