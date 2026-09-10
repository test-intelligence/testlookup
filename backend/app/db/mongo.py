"""Async MongoDB client using Motor."""
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings

_client: Optional[AsyncIOMotorClient] = None


def get_mongo_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
            maxPoolSize=settings.MONGO_MAX_POOL_SIZE,
            minPoolSize=settings.MONGO_MIN_POOL_SIZE,
            socketTimeoutMS=settings.MONGO_SOCKET_TIMEOUT_MS,
            maxIdleTimeMS=settings.MONGO_MAX_IDLE_TIME_MS,
        )
    return _client


def get_mongo_db() -> AsyncIOMotorDatabase:
    """Return the Motor database handle. **Synchronous — do not await it.**

    Motor's client and database objects are built eagerly; only the QUERIES
    are awaitable. ``await get_mongo_db()`` therefore raises

        TypeError: object AsyncIOMotorDatabase can't be used in 'await'
        expression

    at runtime, and nothing catches it: it surfaced as a bare 500 from
    ``DELETE /api/v1/runs/{run_id}`` with no traceback in the structured logs.
    Four call sites had it. The confusing part is that
    ``await get_mongo_db()[coll].find_one(...)`` IS correct — there the await
    binds to ``find_one``, not to this function.
    """
    return get_mongo_client()[settings.MONGO_DB]


def reset_mongo_client() -> None:
    """Drop the cached Motor client so the next caller builds one on its loop.

    ``AsyncIOMotorClient`` binds to the event loop that is running when it is
    created. A Celery task that builds its own loop, uses Mongo, then closes
    that loop leaves this cache holding a client whose loop is gone — and the
    *next* task on the same worker child gets ``RuntimeError: Event loop is
    closed`` on its first Mongo call.

    Synchronous on purpose: it runs before a task's loop exists. Motor's
    ``close()`` is itself synchronous, and is best-effort here because a client
    whose loop has already closed can raise while shutting down — a noisy
    teardown must not fail the task that is about to start.
    """
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001 — teardown of an already-dead loop
            pass
        _client = None


async def close_mongo() -> None:
    global _client
    if _client is not None:
        _client.close()  # Motor's close() is synchronous (returns None, not a coroutine)
        _client = None


async def ensure_indexes() -> None:
    """
    Create indexes on hot-path Mongo collections.

    Idempotent — safe to call on every startup. Runs in the background so a
    transient Mongo hiccup cannot block the API lifespan. Must be kept in sync
    with the query patterns in ``app/tools/`` and ``app/services/ingestion.py``
    — any new lookup field needs a matching index here.
    """
    db = get_mongo_db()
    specs = [
        # Hot path: tools/fetch_rest_payload.py — lookup by test_case_id.
        (Collections.REST_API_PAYLOADS, [("test_case_id", 1)], {}),
        # Hot path: tools/fetch_stacktrace.py
        (Collections.RAW_ALLURE_JSON, [("test_case_id", 1)], {}),
        (Collections.RAW_TESTNG_XML, [("test_case_id", 1)], {}),
        # Splunk log correlation window — by run + timestamp.
        (Collections.EXECUTION_LOGS, [("test_run_id", 1), ("timestamp", -1)], {}),
        # AI analysis audit — lookup per test case.
        (Collections.AI_ANALYSIS_PAYLOADS, [("test_case_id", 1)], {}),
        # OCP pod events correlation — by run + timestamp.
        (Collections.OCP_POD_EVENTS, [("test_run_id", 1), ("timestamp", -1)], {}),
        # Run summaries keyed by run_id.
        (Collections.RUN_SUMMARIES, [("test_run_id", 1)], {"unique": True}),
        (
            Collections.DECISION_EVIDENCE_SNAPSHOTS,
            [("project_id", 1), ("test_run_id", 1), ("pipeline_run_id", 1)],
            {"unique": True},
        ),
        (
            Collections.DECISION_REPORTS,
            [("test_run_id", 1), ("report_version", 1)],
            {"unique": True},
        ),
        (
            Collections.DECISION_REPORT_ATTEMPTS,
            [("test_run_id", 1), ("attempted_at", -1)],
            {},
        ),        # Live event timeline — session_id + seq.
        (Collections.LIVE_EXECUTION_EVENTS, [("session_id", 1), ("seq", 1)], {}),
        # Retention purge (US-11.4): the webhook ingest path writes these
        # docs keyed by run_id (routers/live.py), and the purge deletes by
        # run_id — without this index every purge collection-scans.
        (Collections.LIVE_EXECUTION_EVENTS, [("run_id", 1)], {}),
    ]
    for coll, keys, kwargs in specs:
        try:
            await db[coll].create_index(keys, background=True, **kwargs)
        except Exception as exc:  # pragma: no cover — non-fatal
            # Never let index creation block startup; the query path still works.
            import logging
            logging.getLogger("db.mongo").warning(
                "Failed to create index on %s %s: %s", coll, keys, exc,
            )


# Collection name constants
class Collections:
    RAW_ALLURE_JSON = "raw_allure_json"
    RAW_TESTNG_XML = "raw_testng_xml"
    REST_API_PAYLOADS = "rest_api_payloads"
    EXECUTION_LOGS = "execution_logs"
    AI_ANALYSIS_PAYLOADS = "ai_analysis_payloads"
    OCP_POD_EVENTS = "ocp_pod_events"
    RUN_SUMMARIES = "run_summaries"           # Generated by SummaryAgent
    DECISION_EVIDENCE_SNAPSHOTS = "decision_evidence_snapshots"
    DECISION_REPORTS = "decision_reports"
    DECISION_REPORT_ATTEMPTS = "decision_report_attempts"
    LIVE_EXECUTION_EVENTS = "live_execution_events"  # Sanitized events from live runner
