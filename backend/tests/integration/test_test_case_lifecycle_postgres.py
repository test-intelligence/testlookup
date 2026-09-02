"""Real PostgreSQL lifecycle, migration-repair, and catalog checks.

The configured database is used only for self-owned, project-scoped fixtures.
Migration repair is exercised in a separate temporary database created from
``template0`` and dropped afterwards.  The module skips only when the explicit
opt-in DSN is absent; once CI supplies a DSN, setup or database failures fail
the suite instead of silently reducing coverage.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from sqlalchemy.engine import make_url
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import (
    CanonicalTestCase,
    ManagedTestCase,
    Project,
    TestCase as ExecutionCaseModel,
    TestCaseAuditLog as AuditLogModel,
    TestCaseReview as ReviewModel,
    TestRun as RunModel,
    TestCaseVersion as VersionModel,
    TestSuite as SuiteModel,
    User,
    UserRole,
)
from app.services.test_case_lifecycle_service import transition
from app.services.test_management_service import (
    list_combined_test_case_identities,
    list_managed_test_cases,
)
from app.services.test_suite_service import promote_canonical_test_case

asyncpg = pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_FLAG_ID = uuid.UUID("7e3384b4-1a9f-4d52-b19a-a08f866195c9")


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_engine():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def pg_seed(pg_engine):
    """Create all authority/catalog rows used by a test and remove them after."""
    token = uuid.uuid4().hex
    author = User(
        id=uuid.uuid4(),
        email=f"lifecycle-author-{token}@example.invalid",
        username=f"lifecycle-author-{token}",
        full_name="Lifecycle PostgreSQL Author",
        hashed_password="not-a-real-password",
        role=UserRole.QA_ENGINEER,
    )
    reviewer = User(
        id=uuid.uuid4(),
        email=f"lifecycle-reviewer-{token}@example.invalid",
        username=f"lifecycle-reviewer-{token}",
        full_name="Lifecycle PostgreSQL Reviewer",
        hashed_password="not-a-real-password",
        role=UserRole.QA_LEAD,
    )
    project = Project(
        id=uuid.uuid4(),
        name=f"Lifecycle PostgreSQL {token}",
        slug=f"lifecycle-postgres-{token}",
    )
    suite = SuiteModel(
        id=uuid.uuid4(),
        project_id=project.id,
        name=f"Lifecycle Suite {token}",
        is_default=False,
    )
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([author, reviewer, project])
        await session.flush()
        session.add(suite)
        await session.commit()
    try:
        yield SimpleNamespace(
            author=author,
            reviewer=reviewer,
            project=project,
            suite=suite,
        )
    finally:
        async with factory() as session:
            await session.execute(
                delete(AuditLogModel).where(AuditLogModel.project_id == project.id)
            )
            await session.execute(delete(Project).where(Project.id == project.id))
            await session.execute(
                delete(User).where(User.id.in_([author.id, reviewer.id]))
            )
            await session.commit()


@pytest.fixture
async def isolated_migration_dsn():
    """Create a database where Alembic can prove 0143 -> 0144 -> 0143."""
    source = make_url(_dsn())
    database_name = f"testlookup_lifecycle_{uuid.uuid4().hex[:20]}"
    admin = await asyncpg.connect(
        user=source.username,
        password=source.password,
        host=source.host or "localhost",
        port=source.port or 5432,
        database="postgres",
    )
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}" TEMPLATE template0')
    finally:
        await admin.close()

    isolated = source.set(database=database_name).render_as_string(
        hide_password=False
    )
    try:
        yield isolated
    finally:
        admin = await asyncpg.connect(
            user=source.username,
            password=source.password,
            host=source.host or "localhost",
            port=source.port or 5432,
            database="postgres",
        )
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await admin.close()


async def _run_alembic(dsn: str, direction: str, revision: str) -> None:
    """Run Alembic out-of-loop so its async environment may call asyncio.run."""
    def invoke() -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["DATABASE_URL"] = dsn
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "alembic.ini",
                direction,
                revision,
            ],
            cwd=BACKEND_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    result = await asyncio.to_thread(invoke)
    assert result.returncode == 0, (
        f"alembic {direction} {revision} failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


async def test_migration_postconditions_are_true_in_postgres(pg_engine):
    async with pg_engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        # Assert the database is AT HEAD, not at a literal revision.
        #
        # This was pinned to a hardcoded number and had already been bumped
        # once ("head advanced to 0146..."). Every unrelated migration then
        # failed this test, and the failure said nothing about the lifecycle
        # postconditions the test actually exists to check — it only said
        # somebody added a migration. Reading the head from the script
        # directory keeps the real check (every migration applied) and drops
        # the false one.
        head = ScriptDirectory.from_config(
            AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
        ).get_current_head()
        assert revision == head, (
            f"database is at {revision}, head is {head} — the fixture did not "
            "run every migration, so the postconditions below are being "
            "asserted against a partially migrated schema"
        )

        duplicate_versions = await connection.scalar(text(
            "SELECT count(*) FROM ("
            " SELECT test_case_id, version FROM test_case_versions"
            " GROUP BY test_case_id, version HAVING count(*) > 1"
            ") AS duplicate_versions"
        ))
        duplicate_open_reviews = await connection.scalar(text(
            "SELECT count(*) FROM ("
            " SELECT test_case_id FROM test_case_reviews"
            " WHERE status IN ('pending','in_progress')"
            " GROUP BY test_case_id HAVING count(*) > 1"
            ") AS duplicate_reviews"
        ))
        phantom_ai_claims = await connection.scalar(text(
            "SELECT count(*) FROM test_case_reviews"
            " WHERE status = 'in_progress' AND ai_review_completed IS TRUE"
            " AND reviewer_id IS NULL"
        ))
        assert duplicate_versions == 0
        assert duplicate_open_reviews == 0
        assert phantom_ai_claims == 0

        index_rows = (await connection.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes"
            " WHERE schemaname = current_schema()"
            " AND indexname IN ("
            " 'uq_test_case_reviews_one_open_per_case',"
            " 'uq_ctc_managed_test_case_id',"
            " 'ix_mtc_project_fingerprint',"
            " 'ix_mtc_project_last_executed'"
            " )"
        ))).all()
        indexes = {name: definition for name, definition in index_rows}
        assert set(indexes) == {
            "uq_test_case_reviews_one_open_per_case",
            "uq_ctc_managed_test_case_id",
            "ix_mtc_project_fingerprint",
            "ix_mtc_project_last_executed",
        }
        assert "UNIQUE INDEX" in indexes["uq_test_case_reviews_one_open_per_case"]
        assert "UNIQUE INDEX" in indexes["uq_ctc_managed_test_case_id"]
        assert "UNIQUE INDEX" not in indexes["ix_mtc_project_fingerprint"]
        assert "UNIQUE INDEX" not in indexes["ix_mtc_project_last_executed"]
        assert await connection.scalar(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conname = "
                "'uq_canonical_test_cases_project_fp'"
            )
        ) == 1
        assert await connection.scalar(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'test_cases' "
                "AND column_name = 'steps_present'"
            )
        ) == "false"


async def test_isolated_0143_to_0144_repairs_constraints_and_safe_downgrade(
    isolated_migration_dsn,
):
    await _run_alembic(isolated_migration_dsn, "upgrade", "0143")
    engine = create_async_engine(isolated_migration_dsn)
    project_id = uuid.uuid4()
    suite_id = uuid.uuid4()
    managed_id = uuid.uuid4()
    archive_id = uuid.uuid4()
    version_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    ai_review_id = uuid.uuid4()
    kept_review_id = uuid.uuid4()
    losing_review_id = uuid.uuid4()
    canonical_ids = [uuid.uuid4(), uuid.uuid4()]
    operator_flag_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO projects (id, name, slug) "
                    "VALUES (:id, :name, :slug)"
                ),
                {
                    "id": project_id,
                    "name": "Lifecycle migration repair",
                    "slug": f"lifecycle-migration-{project_id.hex}",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO test_suites (id, project_id, name, is_default) "
                    "VALUES (:id, :project_id, 'Migration Suite', false)"
                ),
                {"id": suite_id, "project_id": project_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO managed_test_cases "
                    "(id, project_id, title, status, version) VALUES "
                    "(:managed_id, :project_id, 'Legacy pending review', "
                    " 'pending_review', 1), "
                    "(:archive_id, :project_id, 'Legacy archive mapping', "
                    " 'draft', 1)"
                ),
                {
                    "managed_id": managed_id,
                    "archive_id": archive_id,
                    "project_id": project_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO test_case_versions "
                    "(id, test_case_id, version, title, status, created_at) VALUES "
                    "(:first, :case_id, 1, 'first snapshot', 'draft', "
                    " '2026-01-01T00:00:00Z'), "
                    "(:second, :case_id, 1, 'retry snapshot', 'draft', "
                    " '2026-01-02T00:00:00Z'), "
                    "(:third, :case_id, 3, 'gapped snapshot', 'draft', "
                    " '2026-01-03T00:00:00Z')"
                ),
                {
                    "first": version_ids[0],
                    "second": version_ids[1],
                    "third": version_ids[2],
                    "case_id": managed_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO test_case_reviews "
                    "(id, test_case_id, status, ai_review_completed, created_at) "
                    "VALUES "
                    "(:ai_id, :case_id, 'in_progress', true, "
                    " '2026-01-01T00:00:00Z'), "
                    "(:kept_id, :case_id, 'in_progress', false, "
                    " '2026-01-02T00:00:00Z'), "
                    "(:losing_id, :case_id, 'pending', false, "
                    " '2026-01-03T00:00:00Z')"
                ),
                {
                    "ai_id": ai_review_id,
                    "kept_id": kept_review_id,
                    "losing_id": losing_review_id,
                    "case_id": managed_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO canonical_test_cases "
                    "(id, project_id, test_suite_id, test_fingerprint, "
                    " test_name, managed_test_case_id, source, updated_at) VALUES "
                    "(:older_id, :project_id, :suite_id, 'legacy-link-older', "
                    " 'older link', :managed_id, 'linked', "
                    " '2026-01-01T00:00:00Z'), "
                    "(:newer_id, :project_id, :suite_id, 'legacy-link-newer', "
                    " 'newer link', :managed_id, 'linked', "
                    " '2026-01-02T00:00:00Z')"
                ),
                {
                    "older_id": canonical_ids[0],
                    "newer_id": canonical_ids[1],
                    "project_id": project_id,
                    "suite_id": suite_id,
                    "managed_id": managed_id,
                },
            )

        await _run_alembic(isolated_migration_dsn, "upgrade", "0144")
        async with engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0144"
            assert await connection.scalar(
                text("SELECT status FROM managed_test_cases WHERE id = :id"),
                {"id": managed_id},
            ) == "draft"
            assert list(
                (
                    await connection.execute(
                        text(
                            "SELECT version FROM test_case_versions "
                            "WHERE test_case_id = :id ORDER BY version"
                        ),
                        {"id": managed_id},
                    )
                ).scalars()
            ) == [1, 2, 3]
            assert await connection.scalar(
                text("SELECT version FROM managed_test_cases WHERE id = :id"),
                {"id": managed_id},
            ) == 3
            review_rows = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT id, status FROM test_case_reviews "
                            "WHERE test_case_id = :id"
                        ),
                        {"id": managed_id},
                    )
                ).all()
            )
            assert review_rows == {
                ai_review_id: "ai_completed",
                kept_review_id: "in_progress",
                losing_review_id: "changes_requested",
            }
            link_rows = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT id, managed_test_case_id "
                            "FROM canonical_test_cases "
                            "WHERE id IN (:older_id, :newer_id)"
                        ),
                        {
                            "older_id": canonical_ids[0],
                            "newer_id": canonical_ids[1],
                        },
                    )
                ).all()
            )
            assert link_rows[canonical_ids[0]] is None
            assert link_rows[canonical_ids[1]] == managed_id
            constraints = set(
                (
                    await connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint WHERE conname = "
                            "'uq_test_case_versions_case_version'"
                        )
                    )
                ).scalars()
            )
            indexes = set(
                (
                    await connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes WHERE indexname IN "
                            "('uq_test_case_reviews_one_open_per_case', "
                            " 'uq_ctc_managed_test_case_id')"
                        )
                    )
                ).scalars()
            )
            assert constraints == {"uq_test_case_versions_case_version"}
            assert indexes == {
                "uq_test_case_reviews_one_open_per_case",
                "uq_ctc_managed_test_case_id",
            }
            seeded_flag = (
                await connection.execute(
                    text(
                        "SELECT id, enabled_global, rollout_percent "
                        "FROM feature_flags WHERE key = 'test_case_lifecycle_v2'"
                    )
                )
            ).one()
            assert tuple(seeded_flag) == (MIGRATION_FLAG_ID, True, 100)
            assert await connection.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'test_cases' "
                    "AND column_name = 'steps_present'"
                )
            ) == "false"

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE managed_test_cases SET status = 'needs_update' "
                    "WHERE id = :id"
                ),
                {"id": managed_id},
            )
            await connection.execute(
                text(
                    "UPDATE managed_test_cases SET status = 'archived' "
                    "WHERE id = :id"
                ),
                {"id": archive_id},
            )

        await _run_alembic(isolated_migration_dsn, "downgrade", "0143")
        async with engine.connect() as connection:
            statuses = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT id, status FROM managed_test_cases "
                            "WHERE id IN (:managed_id, :archive_id)"
                        ),
                        {
                            "managed_id": managed_id,
                            "archive_id": archive_id,
                        },
                    )
                ).all()
            )
            assert statuses == {managed_id: "draft", archive_id: "deprecated"}
            assert await connection.scalar(
                text("SELECT status FROM test_case_reviews WHERE id = :id"),
                {"id": ai_review_id},
            ) == "changes_requested"
            assert await connection.scalar(
                text(
                    "SELECT count(*) FROM feature_flags "
                    "WHERE key = 'test_case_lifecycle_v2'"
                )
            ) == 0
            assert await connection.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'test_cases' "
                    "AND column_name = 'steps_present'"
                )
            ) is None

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO feature_flags "
                    "(id, key, description, enabled_global, rollout_percent) "
                    "VALUES (:id, 'test_case_lifecycle_v2', "
                    " 'operator-owned lifecycle flag', false, 17)"
                ),
                {"id": operator_flag_id},
            )

        await _run_alembic(isolated_migration_dsn, "upgrade", "0144")
        await _run_alembic(isolated_migration_dsn, "downgrade", "0143")
        async with engine.connect() as connection:
            operator_flag = (
                await connection.execute(
                    text(
                        "SELECT id, description, enabled_global, rollout_percent "
                        "FROM feature_flags WHERE key = 'test_case_lifecycle_v2'"
                    )
                )
            ).one()
            assert tuple(operator_flag) == (
                operator_flag_id,
                "operator-owned lifecycle flag",
                False,
                17,
            )
    finally:
        await engine.dispose()


async def test_partial_unique_index_allows_one_concurrent_open_review(
    pg_engine,
    pg_seed,
):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    case_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            ManagedTestCase(
                id=case_id,
                project_id=pg_seed.project.id,
                title="Competing PostgreSQL reviews",
                status="review_requested",
                version=1,
                author_id=pg_seed.author.id,
            )
        )
        await session.commit()
    row_ids = [uuid.uuid4(), uuid.uuid4()]

    async def insert_open(row_id: uuid.UUID) -> str:
        async with factory() as session:
            try:
                await session.execute(text(
                    "INSERT INTO test_case_reviews (id, test_case_id, status)"
                    " VALUES (:id, :case_id, 'pending')"
                ), {"id": row_id, "case_id": case_id})
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return "conflict"
            return "created"

    try:
        outcomes = await asyncio.gather(*(insert_open(row_id) for row_id in row_ids))
        assert sorted(outcomes) == ["conflict", "created"]
    finally:
        async with pg_engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM test_case_reviews "
                    "WHERE id IN (:first_id, :second_id)"
                ),
                {"first_id": row_ids[0], "second_id": row_ids[1]},
            )


async def test_alembic_advisory_lock_serializes_real_transactions(pg_engine):
    # Must match backend/migrations/env.py.
    lock_id = 6075990748104101441
    async with pg_engine.connect() as first, pg_engine.connect() as second:
        first_tx = await first.begin()
        second_tx = await second.begin()
        try:
            await first.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            available = await second.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            assert available is False
            await first_tx.rollback()
            available_after_release = await second.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            assert available_after_release is True
        finally:
            if first_tx.is_active:
                await first_tx.rollback()
            if second_tx.is_active:
                await second_tx.rollback()


async def test_full_lifecycle_round_trips_every_committed_state_in_postgres(
    pg_engine,
    pg_seed,
):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    case_id = uuid.uuid4()
    async with factory() as session:
        case = ManagedTestCase(
            id=case_id,
            project_id=pg_seed.project.id,
            title="Lifecycle PostgreSQL integration proof",
            status="draft",
            version=1,
            author_id=pg_seed.author.id,
        )
        session.add(case)
        await session.commit()

        expected = [
            ("request_review", pg_seed.author, None, "review_requested"),
            ("claim_review", pg_seed.reviewer, None, "under_review"),
            ("approve", pg_seed.reviewer, None, "approved"),
            ("activate", pg_seed.author, None, "active"),
            (
                "flag_stale",
                pg_seed.author,
                "execution evidence changed",
                "needs_update",
            ),
            ("revise", pg_seed.author, None, "draft"),
            ("deprecate", pg_seed.reviewer, "obsolete workflow", "deprecated"),
            ("archive", pg_seed.reviewer, "retention complete", "archived"),
            ("reinstate", pg_seed.reviewer, "feature restored", "draft"),
        ]
        try:
            for action, actor, reason, expected_status in expected:
                await transition(
                    session,
                    case_id,
                    action,
                    actor,
                    reason=reason,
                )
                await session.commit()
                persisted_status = await session.scalar(
                    select(ManagedTestCase.status).where(
                        ManagedTestCase.id == case_id
                    )
                )
                assert persisted_status == expected_status
                filtered, total, _ = await list_managed_test_cases(
                    session,
                    project_id=pg_seed.project.id,
                    page=1,
                    size=10,
                    status=expected_status,
                )
                assert total == 1
                assert [row.id for row in filtered] == [case_id]

            versions = int(
                await session.scalar(
                    select(func.count())
                    .select_from(VersionModel)
                    .where(VersionModel.test_case_id == case_id)
                )
                or 0
            )
            assert versions == len(expected)
        finally:
            await session.rollback()
            await session.execute(
                delete(AuditLogModel).where(AuditLogModel.entity_id == case_id)
            )
            await session.execute(
                delete(ReviewModel).where(ReviewModel.test_case_id == case_id)
            )
            await session.execute(
                delete(VersionModel).where(VersionModel.test_case_id == case_id)
            )
            await session.execute(
                delete(ManagedTestCase).where(ManagedTestCase.id == case_id)
            )
            await session.commit()


async def test_promotion_copies_fingerprint_and_refuses_duplicate_in_postgres(
    pg_engine,
    pg_seed,
):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    canonical_id = uuid.uuid4()
    fingerprint = f"pg-promotion-{uuid.uuid4().hex}"
    managed_id = None
    async with factory() as session:
        canonical = CanonicalTestCase(
            id=canonical_id,
            project_id=pg_seed.project.id,
            test_suite_id=pg_seed.suite.id,
            test_fingerprint=fingerprint,
            test_name="PostgreSQL promotion proof",
            status="active",
            source="execution",
        )
        run = RunModel(
            id=uuid.uuid4(),
            project_id=pg_seed.project.id,
            build_number=f"promotion-{uuid.uuid4().hex}",
            jenkins_job="lifecycle-postgres",
            status="PASSED",
        )
        execution = ExecutionCaseModel(
            id=uuid.uuid4(),
            test_run_id=run.id,
            test_fingerprint=fingerprint,
            canonical_test_case_id=canonical_id,
            test_name="PostgreSQL promotion proof",
            status="PASSED",
        )
        session.add_all([canonical, run])
        await session.flush()
        session.add(execution)
        await session.commit()
        try:
            before, before_total, _ = await list_combined_test_case_identities(
                session,
                project_id=pg_seed.project.id,
                include_archived=False,
                test_type=None,
                search=None,
                suite_name=None,
                page=1,
                size=10,
            )
            assert before_total == 1
            assert before == [("automation", execution.id)]

            promoted, managed = await promote_canonical_test_case(
                session, canonical_id, pg_seed.author
            )
            managed_id = managed.id
            await session.commit()
            persisted_link = (
                await session.execute(
                    select(
                        CanonicalTestCase.managed_test_case_id,
                        CanonicalTestCase.source,
                    ).where(CanonicalTestCase.id == canonical_id)
                )
            ).one()
            persisted_fingerprint = await session.scalar(
                select(ManagedTestCase.test_fingerprint).where(
                    ManagedTestCase.id == managed_id
                )
            )
            assert persisted_link.managed_test_case_id == managed_id
            assert persisted_link.source == "linked"
            assert persisted_fingerprint == fingerprint

            after, after_total, _ = await list_combined_test_case_identities(
                session,
                project_id=pg_seed.project.id,
                include_archived=False,
                test_type=None,
                search=None,
                suite_name=None,
                page=1,
                size=10,
            )
            assert after_total == 1
            assert after == [("managed", managed_id)]

            with pytest.raises(HTTPException) as exc:
                await promote_canonical_test_case(
                    session,
                    canonical_id,
                    pg_seed.author,
                )
            assert exc.value.status_code == 409
        finally:
            await session.rollback()
            await session.execute(
                delete(AuditLogModel).where(
                    AuditLogModel.entity_id.in_(
                        [value for value in (canonical_id, managed_id) if value is not None]
                    )
                )
            )
            if managed_id is not None:
                await session.execute(
                    delete(VersionModel).where(
                        VersionModel.test_case_id == managed_id
                    )
                )
            await session.execute(
                delete(CanonicalTestCase).where(CanonicalTestCase.id == canonical_id)
            )
            if managed_id is not None:
                await session.execute(
                    delete(ManagedTestCase).where(ManagedTestCase.id == managed_id)
                )
            await session.commit()


async def test_concurrent_promotion_of_one_canonical_creates_one_managed_case(
    pg_engine,
    pg_seed,
):
    """The canonical identity constraint plus row lock serialize promotion.

    PostgreSQL forbids two canonical rows with the same project/fingerprint,
    while concurrent promotion attempts for the one legal canonical row are
    serialized by ``SELECT FOR UPDATE``.  Exactly one request may create the
    managed case; the replay receives the stable 409 contract.
    """
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    canonical_id = uuid.uuid4()
    fingerprint = f"pg-concurrent-promotion-{uuid.uuid4().hex}"

    async with factory() as session:
        session.add(
            CanonicalTestCase(
                id=canonical_id,
                project_id=pg_seed.project.id,
                test_suite_id=pg_seed.suite.id,
                test_fingerprint=fingerprint,
                test_name="Concurrent PostgreSQL promotion proof",
                status="active",
                source="execution",
            )
        )
        await session.commit()

    async def promote_once():
        async with factory() as session:
            try:
                _, managed = await promote_canonical_test_case(
                    session,
                    canonical_id,
                    pg_seed.author,
                )
                await session.commit()
                return "created", managed.id
            except HTTPException as exc:
                await session.rollback()
                assert exc.status_code == 409
                return "conflict", None

    managed_ids: list[uuid.UUID] = []
    try:
        outcomes = await asyncio.gather(promote_once(), promote_once())
        assert sorted(status for status, _ in outcomes) == ["conflict", "created"]
        managed_ids = [
            managed_id
            for _, managed_id in outcomes
            if managed_id is not None
        ]
        assert len(managed_ids) == 1

        async with factory() as session:
            managed_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ManagedTestCase)
                    .where(
                        ManagedTestCase.project_id == pg_seed.project.id,
                        ManagedTestCase.test_fingerprint == fingerprint,
                    )
                )
                or 0
            )
            linked_id = await session.scalar(
                select(CanonicalTestCase.managed_test_case_id).where(
                    CanonicalTestCase.id == canonical_id
                )
            )
            assert managed_count == 1
            assert linked_id == managed_ids[0]
    finally:
        async with factory() as session:
            entity_ids = [canonical_id, *managed_ids]
            await session.execute(
                delete(AuditLogModel).where(AuditLogModel.entity_id.in_(entity_ids))
            )
            if managed_ids:
                await session.execute(
                    delete(VersionModel).where(
                        VersionModel.test_case_id.in_(managed_ids)
                    )
                )
            await session.execute(
                delete(CanonicalTestCase).where(CanonicalTestCase.id == canonical_id)
            )
            if managed_ids:
                await session.execute(
                    delete(ManagedTestCase).where(ManagedTestCase.id.in_(managed_ids))
                )
            await session.commit()
