"""AIH-01/06 on real PostgreSQL: migrations 0205/0206, the backfill, and the per-test version store.

* At head: ``ai_analysis_versions`` has its columns, the five pinned
  constraint/index names with their predicates, and the CASCADE / SET NULL
  foreign keys; ``project_retention_policies`` has the two bounded caps.
* 0205 and 0206 go up and down through a real Alembic context on copies of the
  referenced tables in a scratch schema (``LIKE ... INCLUDING ALL``, so the
  foreign keys have primary keys to point at); the shared tables are untouched.
* The migration's OWN backfill statements, run against 50 seeded
  ``ai_analysis`` rows (unicode, nulls, a non-string ``analysis_mode``), write 50
  ``version=1, trigger='backfill'`` rows with project / fingerprint / created_at
  from the joins; a re-run is a no-op; every SQL ``content_sha256`` equals the
  Python ``analysis_content_sha256`` of the same row. Run inside a transaction
  that is rolled back.
* ``stage_analysis_versions``: two pipelines -> v1, v2; a replay is a no-op;
  an identical ``manual_analyze`` is skipped; a ``human_correction`` is v+1
  with its actor; a poisoned row inside the SAVEPOINT leaves the caller's
  transaction usable; the per-test cap prunes oldest-first but never v1 or a
  cited pipeline, and prunes nothing when citations cannot be read.
* ``stage_bulk_reclassify_versions`` versions 1,000 rows in ONE statement.
* ``build_digest`` scopes clusters to the pipeline and their members to the run.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.ai_history_seed import (
    FakeMongo,
    drop_project,
    dsn,
    seed_analysis,
    seed_pipeline,
    seed_project,
    seed_run,
    seed_user,
)

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _migration(name: str):
    spec = importlib.util.spec_from_file_location(f"m_{name}", VERSIONS / name)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def engine():
    eng = create_async_engine(dsn(), pool_size=3, max_overflow=0)
    yield eng
    await eng.dispose()


@pytest.fixture
async def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def world(factory):
    """A project with one run of 3 failed tests, 3 pipelines and a user."""
    from types import SimpleNamespace

    async with factory() as db:
        project = await seed_project(db, "gah-pg")
        run, cases = await seed_run(db, project, 3)
        pipelines = [await seed_pipeline(db, run) for _ in range(6)]
        user = await seed_user(db)
        await db.commit()
    yield SimpleNamespace(project=project, run=run, cases=cases, pipelines=pipelines, user=user)
    await drop_project(factory, project, [user])


# ── structure at head ────────────────────────────────────────────────────────


async def test_head_has_the_table_its_indexes_and_the_caps(engine):
    async with engine.connect() as conn:
        columns = dict((await conn.execute(text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'ai_analysis_versions'"
        ))).all())
        indexes = dict((await conn.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = 'ai_analysis_versions'"
        ))).all())
        constraints = dict((await conn.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'ai_analysis_versions'::regclass"
        ))).all())
        caps = dict((await conn.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'project_retention_policies'::regclass AND conname LIKE 'ck_retention_ai_%'"
        ))).all())
        cap_cols = dict((await conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'project_retention_policies' AND column_name LIKE 'ai_versions_%'"
        ))).all())
    assert len(columns) == 26
    assert columns["pipeline_run_id"] == "YES" and columns["actor_user_id"] == "YES"
    assert columns["content_sha256"] == "NO" and columns["provenance"] == "NO"
    assert indexes["ix_ai_analysis_versions_run"].endswith("USING btree (test_run_id)")
    assert indexes["ix_ai_analysis_versions_project_fp"].endswith(
        "USING btree (project_id, test_fingerprint, created_at DESC)"
    )
    assert indexes["ix_ai_analysis_versions_pipeline"].endswith(
        "USING btree (pipeline_run_id) WHERE (pipeline_run_id IS NOT NULL)"
    )
    assert indexes["uq_ai_analysis_versions_case_pipeline"].startswith("CREATE UNIQUE INDEX")
    assert indexes["uq_ai_analysis_versions_case_pipeline"].endswith(
        "USING btree (test_case_id, pipeline_run_id) WHERE (((trigger)::text = 'pipeline'::text) "
        "AND (pipeline_run_id IS NOT NULL))"
    )
    assert constraints["uq_ai_analysis_versions_case_version"] == "UNIQUE (test_case_id, version)"
    assert constraints["ck_ai_analysis_versions_version"] == "CHECK ((version >= 1))"
    fks = {k: v for k, v in constraints.items() if v.startswith("FOREIGN KEY")}
    assert sorted(fks.values()) == sorted([
        "FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE",
        "FOREIGN KEY (test_run_id) REFERENCES test_runs(id) ON DELETE CASCADE",
        "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE",
        "FOREIGN KEY (pipeline_run_id) REFERENCES agent_pipeline_runs(id) ON DELETE SET NULL",
        "FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL",
    ])
    assert cap_cols == {"ai_versions_per_run": "integer", "ai_versions_per_test": "integer"}
    assert caps == {
        "ck_retention_ai_versions_per_run": "CHECK (((ai_versions_per_run IS NULL) OR ((ai_versions_per_run >= 1) "
                                            "AND (ai_versions_per_run <= 100))))",
        "ck_retention_ai_versions_per_test": "CHECK (((ai_versions_per_test IS NULL) OR ((ai_versions_per_test >= 1) "
                                             "AND (ai_versions_per_test <= 100))))",
    }


async def test_0205_and_0206_go_up_and_down_on_a_scratch_schema(engine):
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    m0205, m0206 = _migration("0205_ai_analysis_versions.py"), _migration("0206_retention_ai_history_caps.py")
    scratch = f"w03a_{uuid.uuid4().hex[:10]}"
    tables = ("projects", "users", "test_runs", "test_cases", "agent_pipeline_runs", "ai_analysis",
              "project_retention_policies")

    def _state(conn):
        has_table = conn.execute(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = :s AND table_name = 'ai_analysis_versions'"
        ), {"s": scratch}).scalar_one()
        idx = conn.execute(text(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = :s AND tablename = 'ai_analysis_versions'"
        ), {"s": scratch}).scalar_one()
        caps = conn.execute(text(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = :s "
            "AND table_name = 'project_retention_policies' AND column_name LIKE 'ai_versions_%'"
        ), {"s": scratch}).scalar_one()
        policy_cols = conn.execute(text(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = :s "
            "AND table_name = 'project_retention_policies'"
        ), {"s": scratch}).scalar_one()
        return has_table, idx, caps, policy_cols

    def _round_trip(conn):
        base = conn.execute(text("SELECT current_schema()")).scalar_one()
        conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
        for table in tables:
            conn.execute(text(f'CREATE TABLE "{scratch}".{table} (LIKE "{base}".{table} INCLUDING ALL)'))
        # The policy table as 0205 found it: without 0206's columns.
        conn.execute(text(f'ALTER TABLE "{scratch}".project_retention_policies '
                          "DROP COLUMN ai_versions_per_run, DROP COLUMN ai_versions_per_test"))
        conn.execute(text(f'SET search_path TO "{scratch}"'))
        conn.commit()
        try:
            before = _state(conn)
            context = MigrationContext.configure(conn)
            for module in (m0205, m0206):
                module.op = Operations(context)
            with context.begin_transaction():
                m0205.upgrade()
                m0206.upgrade()
            up = _state(conn)
            conn.commit()
            with context.begin_transaction():
                m0206.downgrade()
                m0205.downgrade()
            down = _state(conn)
            conn.commit()
            return before, up, down
        finally:
            conn.rollback()
            conn.execute(text(f'SET search_path TO "{base}"'))
            conn.execute(text(f'DROP SCHEMA "{scratch}" CASCADE'))
            conn.commit()

    async with engine.connect() as conn:
        before, up, down = await conn.run_sync(_round_trip)
    assert before[:3] == (0, 0, 0)
    assert up[:3] == (1, 6, 2)  # pkey + uq constraint index + 4 indexes; both caps
    assert up[3] == before[3] + 2
    assert down == before, "the downgrades must drop exactly what the upgrades added"


# ── the backfill and the SQL/Python hash parity ──────────────────────────────


async def test_the_migration_backfill_versions_every_analysis_once_and_hashes_like_python(engine):
    from app.models.postgres import AIAnalysis, AIAnalysisVersion
    from app.services.ai_history_core import analysis_content_sha256

    m0205 = _migration("0205_ai_analysis_versions.py")
    created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    variants = [
        {},
        {"failure_category": None, "confidence_score": None},
        {"root_cause_summary": "café — résumé \U0001F600 multi\nline\ttabs"},
        {"routing_metadata": {"analysis_mode": "rules", "prompt_versions": {"react_triage": "v2:abc"}}},
        {"routing_metadata": {"analysis_mode": 7}},
        {"routing_metadata": {"analysis_mode": True}},
        {"routing_metadata": None, "llm_provider": None, "llm_model": None},
        {"is_flaky": True, "requires_human_review": True, "backend_error_found": True, "pod_issue_found": True},
        {"root_cause_summary": "", "failure_category": "INFRASTRUCTURE", "confidence_score": 0},
        {"root_cause_summary": "has a \x1f unit separator"},
    ]
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            factory = async_sessionmaker(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
            async with factory() as db:
                project = await seed_project(db, "gah-backfill")
                run, cases = await seed_run(db, project, 50)
                for i, case_id in enumerate(cases):
                    await seed_analysis(db, case_id, created_at=created + timedelta(minutes=i), **variants[i % len(variants)])
                await db.commit()
            for sql, params in m0205.backfill_statements():
                await conn.execute(text(sql), params)
            first = (await conn.execute(
                select(AIAnalysisVersion).where(AIAnalysisVersion.test_run_id == run)
            )).all()
            for sql, params in m0205.backfill_statements():
                await conn.execute(text(sql), params)
            second = (await conn.execute(
                select(func.count()).select_from(AIAnalysisVersion).where(AIAnalysisVersion.test_run_id == run)
            )).scalar_one()
            analyses = {
                row.test_case_id: row for row in (await conn.execute(
                    select(AIAnalysis).where(AIAnalysis.test_case_id.in_(cases))
                )).all()
            }
        finally:
            await trans.rollback()

    assert len(first) == 50 and second == 50, "a re-run must be a no-op"
    for v in first:
        a = analyses[v.test_case_id]
        assert (v.version, v.trigger, v.project_id, v.pipeline_run_id, v.actor_user_id) == (1, "backfill", project, None, None)
        assert v.test_fingerprint.startswith("fp") and v.created_at == a.created_at
        assert v.provenance.get("source") == "backfill"
        expected = analysis_content_sha256(
            failure_category=a.failure_category, confidence_score=a.confidence_score, is_flaky=a.is_flaky,
            requires_human_review=a.requires_human_review, backend_error_found=a.backend_error_found,
            pod_issue_found=a.pod_issue_found, llm_provider=a.llm_provider, llm_model=a.llm_model,
            analysis_mode=(a.routing_metadata or {}).get("analysis_mode"), root_cause_summary=a.root_cause_summary,
        )
        assert v.content_sha256 == expected, (a.routing_metadata, a.root_cause_summary)


# ── constraints ──────────────────────────────────────────────────────────────


async def test_a_run_delete_cascades_and_a_duplicate_pipeline_version_is_rejected(factory, world):
    from app.models.postgres import AIAnalysisVersion, TestRun

    def _version(n, pipeline):
        return AIAnalysisVersion(
            test_case_id=world.cases[0], test_run_id=world.run, project_id=world.project, test_fingerprint="fp",
            version=n, trigger="pipeline", pipeline_run_id=pipeline, content_sha256="0" * 64,
        )

    async with factory() as db:
        db.add(_version(1, world.pipelines[0]))
        await db.commit()
        db.add(_version(2, world.pipelines[0]))
        with pytest.raises(IntegrityError, match="uq_ai_analysis_versions_case_pipeline"):
            await db.commit()
        await db.rollback()
        db.add(_version(0, None))
        with pytest.raises(IntegrityError, match="ck_ai_analysis_versions_version"):
            await db.commit()
        await db.rollback()
        # Non-pipeline triggers are not constrained by the replay index.
        for n in (2, 3):
            v = _version(n, world.pipelines[0])
            v.trigger = "human_correction"
            db.add(v)
        await db.commit()
        await db.execute(TestRun.__table__.delete().where(TestRun.id == world.run))
        await db.commit()
        left = (await db.execute(
            select(func.count()).select_from(AIAnalysisVersion).where(AIAnalysisVersion.test_run_id == world.run)
        )).scalar_one()
    assert left == 0


async def test_the_caps_are_bounded_and_null_reads_as_the_defaults(factory, world):
    from app.models.postgres import ProjectRetentionPolicy
    from app.services.ai_history_core import AIHistoryCaps
    from app.services.ai_history_service import resolve_caps

    async with factory() as db:
        assert await resolve_caps(db, world.project) == AIHistoryCaps(20, 10)
        db.add(ProjectRetentionPolicy(project_id=world.project, ai_versions_per_run=0))
        with pytest.raises(IntegrityError, match="ck_retention_ai_versions_per_run"):
            await db.commit()
        await db.rollback()
        db.add(ProjectRetentionPolicy(project_id=world.project, ai_versions_per_test=101))
        with pytest.raises(IntegrityError, match="ck_retention_ai_versions_per_test"):
            await db.commit()
        await db.rollback()
        db.add(ProjectRetentionPolicy(project_id=world.project, ai_versions_per_test=3))
        await db.commit()
        caps = await resolve_caps(db, world.project)
    assert (caps.per_run, caps.per_test) == (20, 3)


# ── stage_analysis_versions against real Postgres ────────────────────────────


def _snap(case_id, **kw):
    from app.services.ai_history_service import snapshot_from_analysis_dict

    mode = kw.pop("mode", "llm")
    analysis = {"root_cause_summary": "Tax rounds twice", "failure_category": "PRODUCT_BUG",
                "confidence_score": 80, "llm_provider": "ollama", "llm_model": "llama3.1",
                "requires_human_review": False}
    analysis.update(kw)
    return snapshot_from_analysis_dict(case_id, analysis, {"analysis_mode": mode})


async def _versions(db, case_id):
    from app.models.postgres import AIAnalysisVersion

    rows = (await db.execute(
        select(AIAnalysisVersion.version, AIAnalysisVersion.trigger, AIAnalysisVersion.pipeline_run_id,
               AIAnalysisVersion.actor_user_id, AIAnalysisVersion.content_sha256)
        .where(AIAnalysisVersion.test_case_id == case_id).order_by(AIAnalysisVersion.version)
    )).all()
    return [tuple(r) for r in rows]


async def test_pipelines_replays_identical_and_human_versions(factory, world):
    from app.services.ai_history_service import stage_analysis_versions

    c0 = world.cases[0]
    p1, p2 = world.pipelines[:2]
    async with factory() as db:
        r1 = await stage_analysis_versions(db, [_snap(c) for c in world.cases], trigger="pipeline", pipeline_run_id=p1)
        await db.commit()
        # A NEW pipeline always versions, even with identical content.
        r2 = await stage_analysis_versions(db, [_snap(c) for c in world.cases], trigger="pipeline", pipeline_run_id=p2)
        await db.commit()
        replay = await stage_analysis_versions(db, [_snap(c0)], trigger="pipeline", pipeline_run_id=p1)
        await db.commit()
        same = await stage_analysis_versions(db, [_snap(c0)], trigger="manual_analyze", actor_user_id=world.user)
        await db.commit()
        human = await stage_analysis_versions(
            db, [_snap(c0, failure_category="INFRASTRUCTURE", requires_human_review=False)],
            trigger="human_correction", actor_user_id=world.user,
        )
        await db.commit()
        versions = await _versions(db, c0)
    assert (r1.staged, r2.staged) == (3, 3)
    assert (replay.staged, replay.skipped_replay) == (0, 1)
    assert (same.staged, same.skipped_identical) == (0, 1)
    assert human.staged == 1
    assert [(v, t, p, a) for v, t, p, a, _ in versions] == [
        (1, "pipeline", p1, None), (2, "pipeline", p2, None), (3, "human_correction", None, world.user),
    ]


async def test_a_poisoned_row_leaves_the_callers_transaction_usable(factory, world):
    from app.models.postgres import TestCase
    from app.services.ai_history_service import stage_analysis_versions

    async with factory() as db:
        # The caller's own write, staged before the history call...
        await db.execute(update(TestCase).where(TestCase.id == world.cases[1]).values(failure_category="FLAKY"))
        poisoned = _snap(world.cases[1], confidence_score=2 ** 40)  # integer out of range in Postgres
        result = await stage_analysis_versions(db, [poisoned, _snap(world.cases[2])], trigger="pipeline",
                                               pipeline_run_id=world.pipelines[0])
        # ...survives the history failure and commits.
        await db.commit()
        category = (await db.execute(select(TestCase.failure_category).where(TestCase.id == world.cases[1]))).scalar_one()
        rows = await _versions(db, world.cases[2])
    assert result.failed == 2 and result.staged == 0
    assert category == "FLAKY"
    assert rows == [], "the whole batch rolled back to the savepoint"


async def test_an_unknown_test_case_is_counted_failed_not_raised(factory, world):
    from app.services.ai_history_service import stage_analysis_versions

    async with factory() as db:
        result = await stage_analysis_versions(db, [_snap(uuid.uuid4()), _snap(world.cases[0])],
                                               trigger="pipeline", pipeline_run_id=world.pipelines[0])
        await db.commit()
    assert (result.staged, result.failed) == (1, 1)


async def test_the_per_test_cap_prunes_oldest_first_but_never_v1_or_a_cited_pipeline(factory, world):
    from app.models.postgres import ProjectRetentionPolicy
    from app.services.ai_history_service import stage_analysis_versions

    c0 = world.cases[0]
    cited = world.pipelines[1]
    mongo = FakeMongo([{"test_run_id": str(world.run), "pipeline_run_id": str(cited), "status": "published"}])
    async with factory() as db:
        db.add(ProjectRetentionPolicy(project_id=world.project, ai_versions_per_test=3))
        await db.commit()
        for p in world.pipelines[:5]:
            await stage_analysis_versions(db, [_snap(c0)], trigger="pipeline", pipeline_run_id=p, mongo=mongo)
            await db.commit()
        kept = [v for v, *_ in await _versions(db, c0)]
        # Citations unreadable -> nothing pruned (fail closed), the version still lands.
        result = await stage_analysis_versions(db, [_snap(c0)], trigger="pipeline",
                                               pipeline_run_id=world.pipelines[5], mongo=None)
        await db.commit()
        after = [v for v, *_ in await _versions(db, c0)]
    assert kept == [1, 2, 5], "v1 and the cited v2 are protected; the newest fills the cap"
    assert result.staged == 1 and result.pruned == 0
    assert after == [1, 2, 5, 6]
    assert mongo.reports.queries and all(q["test_run_id"] == {"$in": [str(world.run)]} for q in mongo.reports.queries)


async def test_bulk_reclassify_versions_a_thousand_rows_in_one_statement(engine, factory):
    from app.models.postgres import AIAnalysis, AIAnalysisVersion
    from app.services.ai_history_service import stage_bulk_reclassify_versions

    async with factory() as db:
        project = await seed_project(db, "gah-bulk")
        run, cases = await seed_run(db, project, 1000)
        user = await seed_user(db, "bulk")
        await db.execute(AIAnalysis.__table__.insert(), [
            {"id": uuid.uuid4(), "test_case_id": c, "failure_category": "UNKNOWN", "confidence_score": 10,
             "is_flaky": False, "backend_error_found": False, "pod_issue_found": False,
             "requires_human_review": True, "routing_metadata": {"analysis_mode": "rules"}}
            for c in cases
        ])
        await db.commit()
    statements: list[str] = []

    def _count(conn, cursor, statement, *_a):
        if statement.lstrip().startswith("INSERT INTO ai_analysis_versions"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        async with factory() as db:
            await db.execute(update(AIAnalysis).where(AIAnalysis.test_case_id.in_(cases)).values(failure_category="PRODUCT_BUG"))
            inserted = await stage_bulk_reclassify_versions(db, cases, actor_user_id=user)
            await db.commit()
            again = await stage_bulk_reclassify_versions(db, cases, actor_user_id=user)
            await db.commit()
            rows = (await db.execute(
                select(AIAnalysisVersion.version, AIAnalysisVersion.trigger, AIAnalysisVersion.actor_user_id,
                       AIAnalysisVersion.failure_category, func.count())
                .where(AIAnalysisVersion.test_run_id == run)
                .group_by(AIAnalysisVersion.version, AIAnalysisVersion.trigger, AIAnalysisVersion.actor_user_id,
                          AIAnalysisVersion.failure_category)
            )).all()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)
        await drop_project(factory, project, [user])
    assert inserted == 1000 and again == 0, "the second call finds every hash already latest"
    assert [tuple(r) for r in rows] == [(1, "bulk_reclassify", user, "PRODUCT_BUG", 1000)]
    assert len(statements) == 2, "one INSERT ... SELECT per call (1,000 ids < one 5,000-id chunk)"


# ── build_digest on real rows ────────────────────────────────────────────────


async def test_the_digest_scopes_clusters_to_the_pipeline_and_members_to_the_run(factory, world):
    from app.models.postgres import FailureCluster, ReleaseDecision
    from app.services.ai_history_service import build_digest, stage_analysis_versions

    p1, p2 = world.pipelines[:2]
    async with factory() as db:
        other_project = await seed_project(db, "gah-other")
        _, foreign_cases = await seed_run(db, other_project, 1)
        for c in world.cases:
            await seed_analysis(db, c)
        t0 = datetime.now(timezone.utc)
        db.add(FailureCluster(test_run_id=world.run, pipeline_run_id=p1, cluster_id="cl_old", label="Old cluster",
                              member_test_ids=[str(world.cases[0])], size=1, created_at=t0 - timedelta(hours=1)))
        db.add(FailureCluster(test_run_id=world.run, pipeline_run_id=p2, cluster_id="cl_001", label="Tax rounding",
                              representative_error="AssertionError: 100.10 != 100.00",
                              member_test_ids=[str(c) for c in world.cases] + [str(foreign_cases[0])], size=4,
                              created_at=t0))
        db.add(ReleaseDecision(test_run_id=world.run, pipeline_run_id=p1, recommendation="GO", risk_score=20,
                               original_recommendation=None, conditions_for_go=["Watch the tax job"]))
        await db.commit()
        await stage_analysis_versions(db, [_snap(c) for c in world.cases[:2]], trigger="pipeline", pipeline_run_id=p2)
        await db.commit()
        stored = await build_digest(db, None, run_id=world.run, pipeline_run_id=p2)
        live = await build_digest(db, None, run_id=world.run)
        old = await build_digest(db, None, run_id=world.run, pipeline_run_id=p1)
    try:
        assert [c["cluster_id"] for c in stored["clusters"]] == ["cl_001"]
        assert stored["clusters"][0]["member_count"] == 3, "the other project's test never resolves"
        assert stored["verdict"]["recommendation"] is None, "a release decision of ANOTHER pipeline is ignored"
        assert stored["provenance"]["carried_over_tests"] == 1
        assert stored["provenance"]["partial"] == ["decision_report_unavailable"]
        assert live["verdict"]["recommendation"] == "GO" and live["actions"] == ["Watch the tax job"]
        assert [c["cluster_id"] for c in live["clusters"]] == ["cl_001"], "live = the newest cluster pipeline"
        assert [c["cluster_id"] for c in old["clusters"]] == ["cl_old"] and old["verdict"]["source"] == "release_decision"
        assert live["content_sha256"] != stored["content_sha256"]
        assert stored["analysed_tests"] == 3 and stored["categories"] == {"PRODUCT_BUG": 3}
    finally:
        await drop_project(factory, other_project)
