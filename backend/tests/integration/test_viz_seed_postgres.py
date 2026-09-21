"""VIZ-213 — the demo seed and the large-dataset generator against real Postgres.

The pure-plan tests prove what the seeders intend to write. Only a real
database can prove what they wrote: that the linker left every run with one
primary link and a matching ``primary_release_id``, that the canonical sync
stamped the stale suite's ``last_seen_run_id`` with a run older than 30
days, that asyncpg COPY accepted every column, and that ``--wipe`` and
``_wipe_seed_data`` take everything back out through the RESTRICT foreign
key on ``canonical_test_cases.test_suite_id``.

Same fixture pattern as ``test_run_duration_aggregate_postgres.py``:
requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head,
skips otherwise. Every project it creates carries a unique slug and marker
and is removed in ``finally`` — the base seed's projects are never touched.

Runs in CI as part of the ``postgres-integration`` job's file list in
``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def factory():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _scalar(factory, sql: str, **params):
    async with factory() as db:
        return (await db.execute(text(sql), params)).scalar_one()


async def _rows(factory, sql: str, **params):
    async with factory() as db:
        return (await db.execute(text(sql), params)).all()


async def _drop_project(factory, project_id: uuid.UUID) -> None:
    async with factory() as db:
        # Canonicals first: their suite foreign key is RESTRICT. Everything
        # else under the project cascades.
        await db.execute(
            text("DELETE FROM canonical_test_cases WHERE project_id = :p"),
            {"p": project_id},
        )
        await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project_id})
        await db.commit()


# ── the viz seed ─────────────────────────────────────────────────────────────


async def _seed_viz_project(factory, marker: str):
    from app.models.postgres import Project
    from scripts.seed_viz_data import apply_viz_seed, build_viz_seed_plan

    now = datetime.now(timezone.utc)
    project_id = uuid.uuid4()
    slug = f"viz-it-{project_id.hex[:12]}"
    plan = build_viz_seed_plan(slug, now)
    async with factory() as db:
        project = Project(
            id=project_id,
            name=f"viz {slug}",
            slug=slug,
            description=f"throwaway · {marker}",
        )
        db.add(project)
        await db.flush()
        summary = await apply_viz_seed(db, project, None, plan)
        await db.commit()
    return project_id, plan, summary, now


async def test_viz_seed_guarantees_hold_in_the_database(factory) -> None:
    from scripts.seed_viz_data import (
        ALWAYS_SKIPPED_SUITE,
        STALE_AFTER_DAYS,
        STALE_SUITE,
    )

    project_id, plan, summary, now = await _seed_viz_project(factory, marker="no-wipe")
    try:
        assert summary.skipped is False
        assert summary.runs == len(plan.runs)

        # 1. at least eight releases
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM releases WHERE project_id = :p",
                p=project_id,
            )
            >= 8
        )
        assert (
            await _scalar(
                factory,
                "SELECT count(DISTINCT status) FROM releases WHERE project_id = :p",
                p=project_id,
            )
            >= 3
        )

        # 4. one in-progress run, dated today (UTC), aggregates partially filled,
        #    in the shape the live path writes (stream_service stub + drainer)
        live = await _rows(
            factory,
            "SELECT id, total_tests, start_time, end_time, duration_ms, pass_rate, trigger_source, "
            "ingestion_source, (start_time AT TIME ZONE 'UTC')::date AS day "
            "FROM test_runs WHERE project_id = :p AND status = 'IN_PROGRESS'",
            p=project_id,
        )
        assert len(live) == 1
        assert live[0].day == now.date()
        assert live[0].duration_ms is None and live[0].pass_rate is None
        assert (
            live[0].trigger_source == "live_stream"
            and live[0].ingestion_source == "live"
        )
        assert live[0].end_time is not None and live[0].end_time >= live[0].start_time
        full = await _scalar(
            factory,
            "SELECT min(total_tests) FROM test_runs WHERE project_id = :p AND status <> 'IN_PROGRESS'",
            p=project_id,
        )
        assert 0 < live[0].total_tests < full
        live_id = live[0].id
        # Not yet finalised, so not yet synced: its cases carry no canonical
        # link, it has no history rows, and no catalogue pointer names it...
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM test_cases WHERE test_run_id = :r AND canonical_test_case_id IS NOT NULL",
                r=live_id,
            )
            == 0
        )
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM test_case_history WHERE test_run_id = :r",
                r=live_id,
            )
            == 0
        )
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM canonical_test_cases WHERE project_id = :p "
                "AND (first_seen_run_id = :r OR last_seen_run_id = :r)",
                p=project_id,
                r=live_id,
            )
            == 0
        )
        # ...yet every test it reports is already catalogued by a finished run.
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM test_cases tc WHERE tc.test_run_id = :r AND NOT EXISTS ("
                "  SELECT 1 FROM canonical_test_cases ctc"
                "  WHERE ctc.project_id = :p AND ctc.test_fingerprint = tc.test_fingerprint)",
                p=project_id,
                r=live_id,
            )
            == 0
        )

        # 7. one suite skipped in every run — not measured, not 0%
        skipped = (
            await _rows(
                factory,
                "SELECT count(*) AS total, count(*) FILTER (WHERE tc.status <> 'SKIPPED') AS executed, "
                "count(DISTINCT tc.test_run_id) AS runs "
                "FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
                "WHERE tr.project_id = :p AND tc.suite_name = :s",
                p=project_id,
                s=ALWAYS_SKIPPED_SUITE,
            )
        )[0]
        assert skipped.total > 0 and skipped.executed == 0
        assert skipped.runs == len(plan.runs) - 1  # every finished run
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM test_suites WHERE project_id = :p AND name = :s",
                p=project_id,
                s=ALWAYS_SKIPPED_SUITE,
            )
            == 1
        )

        # 8. a canonical test (and its suite) last executed before the cutoff
        cutoff = now - timedelta(days=STALE_AFTER_DAYS)
        stale = await _rows(
            factory,
            "SELECT ctc.test_fingerprint, tr.start_time "
            "FROM canonical_test_cases ctc "
            "JOIN test_suites ts ON ts.id = ctc.test_suite_id "
            "JOIN test_runs tr ON tr.id = ctc.last_seen_run_id "
            "WHERE ctc.project_id = :p AND ts.name = :s",
            p=project_id,
            s=STALE_SUITE,
        )
        assert stale, "the stale suite has no canonical rows with a last-seen run"
        assert all(row.start_time < cutoff for row in stale)
        executions = await _rows(
            factory,
            "SELECT tr.start_time FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
            "WHERE tr.project_id = :p AND tc.suite_name = :s",
            p=project_id,
            s=STALE_SUITE,
        )
        assert len(executions) > len(
            stale
        ), "executed before the cutoff, more than once"
        assert all(row.start_time < cutoff for row in executions)
        # and the same fingerprint is linked from every finished run's rows
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
                "WHERE tr.project_id = :p AND tr.status <> 'IN_PROGRESS' AND tc.canonical_test_case_id IS NULL",
                p=project_id,
            )
            == 0
        )

        # 9a. aggregates equal the cases, pass_rate canonical
        drift = await _rows(
            factory,
            "SELECT tr.id FROM test_runs tr LEFT JOIN ("
            "  SELECT test_run_id, count(*) AS total,"
            "         count(*) FILTER (WHERE status = 'PASSED') AS passed,"
            "         count(*) FILTER (WHERE status = 'FAILED') AS failed,"
            "         count(*) FILTER (WHERE status = 'SKIPPED') AS skipped,"
            "         count(*) FILTER (WHERE status = 'BROKEN') AS broken,"
            "         count(*) FILTER (WHERE status = 'UNKNOWN') AS unknown,"
            "         sum(duration_ms) AS duration"
            "  FROM test_cases GROUP BY test_run_id"
            ") c ON c.test_run_id = tr.id "
            "WHERE tr.project_id = :p AND ("
            "  tr.total_tests <> coalesce(c.total, 0) OR tr.passed_tests <> coalesce(c.passed, 0)"
            "  OR tr.failed_tests <> coalesce(c.failed, 0) OR tr.skipped_tests <> coalesce(c.skipped, 0)"
            "  OR tr.broken_tests <> coalesce(c.broken, 0) OR tr.unknown_tests <> coalesce(c.unknown, 0)"
            # An in-flight run has timed cases but no duration yet, by design.
            "  OR (tr.status <> 'IN_PROGRESS' AND tr.duration_ms IS DISTINCT FROM c.duration::int))",
            p=project_id,
        )
        assert drift == []
        from app.core.pass_rate import canonical_pass_rate

        for row in await _rows(
            factory,
            "SELECT passed_tests, failed_tests, broken_tests, pass_rate FROM test_runs "
            "WHERE project_id = :p AND status <> 'IN_PROGRESS'",
            p=project_id,
        ):
            assert row.pass_rate == canonical_pass_rate(
                row.passed_tests, row.failed_tests, row.broken_tests
            )

        # 9b. exactly one primary link per attributed run, none otherwise,
        #     and the denormalised column agrees with the link table
        links = await _rows(
            factory,
            "SELECT tr.id, tr.primary_release_id, "
            "  (SELECT count(*) FROM release_test_run_links l WHERE l.test_run_id = tr.id) AS links, "
            "  (SELECT count(*) FROM release_test_run_links l WHERE l.test_run_id = tr.id AND l.is_primary) AS primaries, "
            "  (SELECT l.release_id FROM release_test_run_links l WHERE l.test_run_id = tr.id AND l.is_primary LIMIT 1) AS linked "
            "FROM test_runs tr WHERE tr.project_id = :p",
            p=project_id,
        )
        assert len(links) == len(plan.runs)
        unattributed = [row for row in links if row.primary_release_id is None]
        for row in links:
            if row.primary_release_id is None:
                assert row.links == 0
            else:
                assert row.links == 1 and row.primaries == 1
                assert row.linked == row.primary_release_id
        assert 1 <= len(unattributed) <= 6
        assert len(unattributed) == summary.unattributed_runs
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM release_test_run_links l JOIN test_runs tr ON tr.id = l.test_run_id "
                "WHERE tr.project_id = :p AND (l.link_source IS NULL OR l.project_id IS DISTINCT FROM :p)",
                p=project_id,
            )
            == 0
        )

        # applying again is a no-op, not a duplicate
        from app.models.postgres import Project
        from scripts.seed_viz_data import apply_viz_seed

        async with factory() as db:
            project = await db.get(Project, project_id)
            again = await apply_viz_seed(db, project, None, plan)
            await db.commit()
        assert again.skipped is True
        assert await _scalar(
            factory,
            "SELECT count(*) FROM test_runs WHERE project_id = :p",
            p=project_id,
        ) == len(plan.runs)
    finally:
        await _drop_project(factory, project_id)


async def test_the_base_wipe_removes_a_viz_seeded_project(factory, monkeypatch) -> None:
    """``_wipe_seed_data`` must get through canonical_test_cases → test_suites
    (RESTRICT) now that the viz seed writes catalog rows. Scoped to this
    project only: the marker and the user list are swapped for the call."""
    from scripts import seed_dev_data

    marker = f"viz-wipe-{uuid.uuid4().hex}"
    project_id, _plan, _summary, _now = await _seed_viz_project(factory, marker=marker)
    try:
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM canonical_test_cases WHERE project_id = :p",
                p=project_id,
            )
            > 0
        )

        monkeypatch.setattr(seed_dev_data, "SEED_MARKER", marker)
        monkeypatch.setattr(seed_dev_data, "USERS", [])
        async with factory() as db:
            await seed_dev_data._wipe_seed_data(db)

        for table in (
            "projects",
            "releases",
            "test_runs",
            "test_suites",
            "canonical_test_cases",
        ):
            column = "id" if table == "projects" else "project_id"
            assert (
                await _scalar(
                    factory,
                    f"SELECT count(*) FROM {table} WHERE {column} = :p",
                    p=project_id,
                )
                == 0
            ), table
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM release_test_run_links WHERE project_id = :p",
                p=project_id,
            )
            == 0
        )
    finally:
        await _drop_project(factory, project_id)


# ── the large dataset generator ──────────────────────────────────────────────


async def test_small_scale_load_and_wipe(factory, monkeypatch) -> None:
    from scripts import seed_large_dataset as sld

    dsn = _dsn()
    identity = sld.DatasetIdentity(slug=f"synthetic-perf-it-{uuid.uuid4().hex[:12]}")
    spec = sld.SCALES["small"]
    expected = sld.plan_counts(spec)
    try:
        await sld.wipe_dataset(
            dsn, identity=identity, log=lambda *_: None
        )  # a crashed earlier run
        counts = await sld.write_dataset(
            dsn, spec, identity=identity, log=lambda *_: None
        )
        assert counts == expected

        p = identity.project_id
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM projects WHERE id = :p AND strpos(description, :m) > 0",
                p=p,
                m=sld.DATASET_MARKER,
            )
            == 1
        )
        assert (
            await _scalar(
                factory, "SELECT count(*) FROM releases WHERE project_id = :p", p=p
            )
            == expected.releases
        )
        assert (
            await _scalar(
                factory, "SELECT count(*) FROM test_suites WHERE project_id = :p", p=p
            )
            == expected.suites
        )
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM canonical_test_cases WHERE project_id = :p",
                p=p,
            )
            == expected.canonical_tests
        )
        assert (
            await _scalar(
                factory, "SELECT count(*) FROM test_runs WHERE project_id = :p", p=p
            )
            == expected.runs
        )
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id WHERE tr.project_id = :p",
                p=p,
            )
            == expected.test_cases
        )
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM release_test_run_links WHERE project_id = :p",
                p=p,
            )
            == expected.linked_runs
        )

        # aggregates and the link column hold in the database too
        assert (
            await _rows(
                factory,
                "SELECT tr.id FROM test_runs tr JOIN ("
                "  SELECT test_run_id, count(*) AS total, count(*) FILTER (WHERE status = 'PASSED') AS passed,"
                "         count(*) FILTER (WHERE status = 'FAILED') AS failed, sum(duration_ms) AS duration"
                "  FROM test_cases GROUP BY test_run_id) c ON c.test_run_id = tr.id "
                "WHERE tr.project_id = :p AND (tr.total_tests <> c.total OR tr.passed_tests <> c.passed "
                "  OR tr.failed_tests <> c.failed OR tr.duration_ms IS DISTINCT FROM c.duration::int)",
                p=p,
            )
            == []
        )
        assert (
            await _rows(
                factory,
                "SELECT tr.id FROM test_runs tr WHERE tr.project_id = :p AND tr.primary_release_id IS DISTINCT FROM "
                "  (SELECT l.release_id FROM release_test_run_links l WHERE l.test_run_id = tr.id AND l.is_primary LIMIT 1)",
                p=p,
            )
            == []
        )
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM canonical_test_cases WHERE project_id = :p AND (first_seen_run_id IS NULL OR last_seen_run_id IS NULL)",
                p=p,
            )
            == 0
        )
        assert (
            await _scalar(
                factory,
                "SELECT count(DISTINCT error_message) FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
                "WHERE tr.project_id = :p AND tc.status IN ('FAILED', 'BROKEN')",
                p=p,
            )
            > 5
        )

        # a second load refuses rather than duplicating
        with pytest.raises(sld.DatasetExistsError):
            await sld.write_dataset(dsn, spec, identity=identity, log=lambda *_: None)

        # --wipe through the CLI's own entry point leaves nothing behind
        run_ids = [
            row.id
            for row in await _rows(
                factory, "SELECT id FROM test_runs WHERE project_id = :p", p=p
            )
        ]
        monkeypatch.setattr(
            sld,
            "settings",
            type("S", (), {"is_production": False, "DATABASE_URL": dsn})(),
        )
        assert (
            await sld.run(
                sld.build_parser().parse_args(
                    ["--wipe", "--yes", "--slug", identity.slug]
                )
            )
            == 0
        )

        for table in (
            "projects",
            "releases",
            "test_suites",
            "canonical_test_cases",
            "test_runs",
            "release_test_run_links",
        ):
            column = "id" if table == "projects" else "project_id"
            assert (
                await _scalar(
                    factory, f"SELECT count(*) FROM {table} WHERE {column} = :p", p=p
                )
                == 0
            ), table
        assert (
            await _scalar(
                factory,
                "SELECT count(*) FROM test_cases WHERE test_run_id = ANY(CAST(:ids AS uuid[]))",
                ids=run_ids,
            )
            == 0
        )
        # and a second wipe is a harmless no-op
        assert await sld.wipe_dataset(dsn, identity=identity, log=lambda *_: None) == 0
    finally:
        await sld.wipe_dataset(dsn, identity=identity, log=lambda *_: None)
