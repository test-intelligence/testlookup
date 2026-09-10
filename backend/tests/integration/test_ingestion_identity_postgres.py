"""Real PostgreSQL proof that manual and reusable ingestion identities stay isolated."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.mark.parametrize("contextful_ci_first", [False, True])
async def test_manual_and_ci_runs_remain_distinct_and_ci_retry_converges(
    contextful_ci_first: bool,
):
    from app.models.postgres import Project, TestRun
    from app.services.ingestion_pipeline import (
        build_ingestion_identity,
        create_run_from_payload,
    )

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    token = uuid.uuid4().hex
    build = f"identity-{token[:12]}"
    ci_kwargs = {
        "project_id": str(project_id),
        "build_number": build,
        "ingestion_source": "sdk",
        "reuse_existing": True,
        "ci_provider": "github_actions",
        "ci_repo": "acme/repo",
        "ci_run_url": f"https://github.com/acme/repo/actions/runs/{token}",
    }
    manual_kwargs = {
        "project_id": str(project_id),
        "build_number": build,
        "run_id": str(uuid.uuid4()),
        "ingestion_source": "upload",
        "reuse_existing": False,
    }
    try:
        async with factory() as db:
            db.add(Project(id=project_id, name=f"Identity {token}", slug=f"identity-{token}"))
            await db.commit()

        first_kwargs, second_kwargs = (
            (ci_kwargs, manual_kwargs) if contextful_ci_first else (manual_kwargs, ci_kwargs)
        )
        async with factory() as first_db:
            first = await create_run_from_payload(first_db, **first_kwargs)
            await first_db.commit()
        async with factory() as second_db:
            second = await create_run_from_payload(second_db, **second_kwargs)
            await second_db.commit()
        async with factory() as retry_db:
            retry = await create_run_from_payload(retry_db, **ci_kwargs)
            await retry_db.commit()

        async with factory() as check_db:
            rows = list(
                (
                    await check_db.execute(
                        select(TestRun)
                        .where(TestRun.project_id == project_id)
                        .order_by(TestRun.created_at, TestRun.id)
                    )
                ).scalars()
            )

        assert len(rows) == 2
        assert first.id != second.id
        manual = next(row for row in rows if row.ingestion_source == "upload")
        ci = next(row for row in rows if row.ingestion_source == "sdk")
        assert manual.ingestion_identity is None
        assert ci.ingestion_identity == build_ingestion_identity(
            project_id=project_id,
            build_number=build,
            ingestion_source="sdk",
            ci_provider="github_actions",
            ci_repo="acme/repo",
            ci_run_url=ci_kwargs["ci_run_url"],
        )
        assert retry.id == ci.id
    finally:
        async with factory() as cleanup_db:
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.commit()
        await engine.dispose()


async def test_context_free_sdk_ingest_does_not_promote_manual_upload():
    from app.models.postgres import Project, TestRun
    from app.routers.ingest import _resolve_run_id
    from app.services.ingestion_pipeline import create_run_from_payload

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    token = uuid.uuid4().hex
    build = f"legacy-{token[:12]}"
    try:
        async with factory() as db:
            db.add(Project(id=project_id, name=f"Legacy {token}", slug=f"legacy-{token}"))
            await db.commit()
            manual = await create_run_from_payload(
                db,
                project_id=str(project_id),
                build_number=build,
                run_id=str(uuid.uuid4()),
                ingestion_source="upload",
                reuse_existing=False,
            )
            await db.commit()
            manual_id = manual.id

        async with factory() as sdk_db:
            sdk_run_id = await _resolve_run_id(sdk_db, project_id, build)
            sdk = await create_run_from_payload(
                sdk_db,
                project_id=str(project_id),
                build_number=build,
                run_id=sdk_run_id,
                ingestion_source="sdk",
                reuse_existing=True,
            )
            await sdk_db.commit()
            sdk_id = sdk.id

        async with factory() as retry_db:
            retry_run_id = await _resolve_run_id(retry_db, project_id, build)
            retry = await create_run_from_payload(
                retry_db,
                project_id=str(project_id),
                build_number=build,
                run_id=retry_run_id,
                ingestion_source="sdk",
                reuse_existing=True,
            )
            await retry_db.commit()

        async with factory() as check_db:
            rows = list(
                (
                    await check_db.execute(
                        select(TestRun).where(TestRun.project_id == project_id)
                    )
                ).scalars()
            )
        assert len(rows) == 2
        assert sdk_id != manual_id
        assert retry.id == sdk_id
        persisted_manual = next(row for row in rows if row.id == manual_id)
        assert persisted_manual.ingestion_source == "upload"
        assert persisted_manual.ingestion_identity is None
    finally:
        async with factory() as cleanup_db:
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.commit()
        await engine.dispose()


async def test_concurrent_manual_and_ci_creation_stays_isolated():
    from app.models.postgres import Project, TestRun
    from app.services.ingestion_pipeline import create_run_from_payload

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    token = uuid.uuid4().hex
    build = f"concurrent-{token[:12]}"
    start = asyncio.Event()

    async def _create(**kwargs):
        async with factory() as db:
            await start.wait()
            run = await create_run_from_payload(db, **kwargs)
            await db.commit()
            return run.id

    ci_kwargs = {
        "project_id": str(project_id),
        "build_number": build,
        "ingestion_source": "sdk",
        "reuse_existing": True,
        "ci_provider": "github_actions",
        "ci_repo": "acme/repo",
        "ci_run_url": f"https://github.com/acme/repo/actions/runs/{token}",
    }
    manual_kwargs = {
        "project_id": str(project_id),
        "build_number": build,
        "run_id": str(uuid.uuid4()),
        "ingestion_source": "upload",
        "reuse_existing": False,
    }
    try:
        async with factory() as db:
            db.add(Project(id=project_id, name=f"Concurrent {token}", slug=f"concurrent-{token}"))
            await db.commit()

        manual_task = asyncio.create_task(_create(**manual_kwargs))
        ci_task = asyncio.create_task(_create(**ci_kwargs))
        start.set()
        manual_id, ci_id = await asyncio.gather(manual_task, ci_task)

        async with factory() as retry_db:
            retry = await create_run_from_payload(retry_db, **ci_kwargs)
            await retry_db.commit()

        async with factory() as check_db:
            rows = list(
                (
                    await check_db.execute(
                        select(TestRun).where(TestRun.project_id == project_id)
                    )
                ).scalars()
            )
        assert manual_id != ci_id
        assert retry.id == ci_id
        assert len(rows) == 2
        manual = next(row for row in rows if row.id == manual_id)
        ci = next(row for row in rows if row.id == ci_id)
        assert manual.ingestion_source == "upload"
        assert manual.ingestion_identity is None
        assert ci.ingestion_source == "sdk"
        assert ci.ingestion_identity is not None
    finally:
        async with factory() as cleanup_db:
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.commit()
        await engine.dispose()
