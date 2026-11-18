"""Two uploads of the same build at once converge on one run with one row per test (GAP-C07).

CI retries, and an SDK upload racing a file upload of the same build, are routine. INGESTION_SCALE.md §5a
measured 145 duplicate-key events from this class. ``test_ingestion_identity_postgres.py`` races a manual
upload against a CI run (different identities) and creates runs only; nothing raced two SAME-identity ingests
through ``ingest_test_results``.

Here two tasks start on one ``asyncio.Event``, each doing what ``worker.tasks.ingest_uploaded_results`` does in
its own session: ``create_run_from_payload`` (two fresh run ids, as the ingest router mints them; the same
project, build, provider, repository and CI run URL, so the same ingestion identity) then
``ingest_test_results`` with the same results, ``_update_run_aggregates`` and one commit. The run insert of the
later task waits on ``uq_test_runs_project_ingestion_identity`` and falls back to the winner; its cases then take
the update path of ``_upsert_test_case`` against ``uq_test_cases_run_fingerprint``.

Asserted after both, and again after a third sequential retry: no exception, one run, ``count(test_cases)`` and
distinct fingerprints and history rows equal to the results, the aggregates equal to the cases, and the run
marked complete with nothing rejected. Both tasks issuing a run INSERT proves the race really happened.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (skips only without it). One raising cleanup.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import event, func, select, text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CASES = 400
FAILED = 7


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _results(tag: str) -> list[dict]:
    return [
        {
            "test_name": f"test_concurrent_{n:04d}",
            "class_name": f"perf.concurrent.{tag}",
            "suite_name": "perf-concurrent",
            "status": "failed" if n < FAILED else "passed",
            "duration_ms": n,
            "error_message": "AssertionError: concurrent fixture"
            if n < FAILED
            else None,
        }
        for n in range(CASES)
    ]


async def _cleanup(factory, project_id: uuid.UUID) -> None:
    """Delete the project (runs, cases and history cascade) and prove nothing is left. Raises on a leak."""
    errors: list[str] = []
    for statement in (
        "DELETE FROM canonical_test_cases WHERE project_id = :p",
        "DELETE FROM projects WHERE id = :p",
    ):
        try:
            async with factory.begin() as db:
                await db.execute(text(statement), {"p": project_id})
        except Exception as exc:  # noqa: BLE001 -- keep cleaning, then raise
            errors.append(f"{statement}: {type(exc).__name__}: {str(exc)[:160]}")
    async with factory() as db:
        leftovers = {
            "projects": await db.scalar(
                text("SELECT count(*) FROM projects WHERE id = :p"), {"p": project_id}
            ),
            "test_runs": await db.scalar(
                text("SELECT count(*) FROM test_runs WHERE project_id = :p"),
                {"p": project_id},
            ),
        }
    leaked = {table: n for table, n in leftovers.items() if n}
    if errors or leaked:
        raise AssertionError(
            f"perf-ingest cleanup failed: errors={errors} leftovers={leaked}"
        )


async def test_two_concurrent_same_build_ingests_converge_on_one_run():
    pytest.importorskip("asyncpg")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.postgres import Project, TestCase, TestCaseHistory, TestRun
    from app.services.ingestion import _update_run_aggregates
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        ingest_test_results,
    )

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tag = "pi" + uuid.uuid4().hex[:8]
    project_id = uuid.uuid4()
    identity = {
        "project_id": str(project_id),
        "build_number": f"{tag}-build-7",
        "ingestion_source": "sdk",
        "ci_provider": "github_actions",
        "ci_repo": "example/perf",
        "ci_run_url": f"https://ci.example.test/perf/{tag}/runs/7",
    }
    results = _results(tag)
    run_inserts: list[str] = []

    def _count_run_inserts(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO TEST_RUNS"):
            run_inserts.append(statement)

    async def _ingest(start: asyncio.Event | None) -> uuid.UUID:
        async with factory() as db:
            if start is not None:
                await start.wait()
            run = await create_run_from_payload(
                db, run_id=str(uuid.uuid4()), **identity
            )
            accepted = await ingest_test_results(db, run, results)
            assert accepted == CASES, f"ingest accepted {accepted} of {CASES}"
            await db.flush()
            await _update_run_aggregates(db, run.id)
            await db.commit()
            return run.id

    async def _state():
        async with factory() as db:
            runs = list(
                (
                    await db.execute(
                        select(TestRun).where(TestRun.project_id == project_id)
                    )
                ).scalars()
            )
            run_ids = [r.id for r in runs]
            cases = await db.scalar(
                select(func.count(TestCase.id)).where(TestCase.test_run_id.in_(run_ids))
            )
            distinct = await db.scalar(
                select(func.count(func.distinct(TestCase.test_fingerprint))).where(
                    TestCase.test_run_id.in_(run_ids)
                )
            )
            history = await db.scalar(
                select(func.count(TestCaseHistory.id)).where(
                    TestCaseHistory.test_run_id.in_(run_ids)
                )
            )
        return runs, cases, distinct, history

    def _assert_converged(runs, cases, distinct, history, when: str):
        assert len(runs) == 1, (
            f"{when}: {len(runs)} runs for one build identity: {[str(r.id) for r in runs]}"
        )
        run = runs[0]
        assert cases == CASES, f"{when}: {cases} test_cases rows for {CASES} results"
        assert distinct == CASES, f"{when}: {distinct} distinct fingerprints"
        assert history == CASES, (
            f"{when}: {history} history rows (one per case per run)"
        )
        assert (run.total_tests, run.failed_tests, run.passed_tests) == (
            CASES,
            FAILED,
            CASES - FAILED,
        ), (
            f"{when}: aggregates total={run.total_tests} failed={run.failed_tests} passed={run.passed_tests}"
        )
        assert run.ingestion_rejected_tests == 0, (
            f"{when}: {run.ingestion_rejected_tests} rows rejected: {run.ingestion_rejection_reasons}"
        )
        assert run.ingestion_attempted_tests == CASES
        assert run.ingestion_complete is True, f"{when}: the run is marked incomplete"

    try:
        async with factory.begin() as db:
            db.add(
                Project(
                    id=project_id, name=f"perf-ingest-{tag}", slug=f"perf-ingest-{tag}"
                )
            )

        start = asyncio.Event()
        event.listen(engine.sync_engine, "before_cursor_execute", _count_run_inserts)
        try:
            tasks = [asyncio.create_task(_ingest(start)) for _ in range(2)]
            await asyncio.sleep(0)
            start.set()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            event.remove(
                engine.sync_engine, "before_cursor_execute", _count_run_inserts
            )
        raised = [repr(o)[:300] for o in outcomes if isinstance(o, BaseException)]
        assert not raised, f"a concurrent same-build ingest raised: {raised}"
        # Both tasks tried to create the run: they really raced (neither saw the other's committed row).
        assert len(run_inserts) == 2, (
            f"expected both tasks to insert the run, saw {len(run_inserts)} inserts"
        )
        assert outcomes[0] == outcomes[1], (
            f"the two ingests returned different runs: {outcomes}"
        )
        _assert_converged(*await _state(), when="after the concurrent pair")

        retry_id = await _ingest(None)
        assert retry_id == outcomes[0], (
            "a sequential retry of the same build created another run"
        )
        _assert_converged(*await _state(), when="after a third, sequential retry")
    finally:
        try:
            await _cleanup(factory, project_id)
        finally:
            await engine.dispose()
