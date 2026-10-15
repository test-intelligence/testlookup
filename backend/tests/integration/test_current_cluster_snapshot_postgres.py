"""A re-analysed run's clusters are its newest snapshot, on real PostgreSQL.

``persist_failure_cluster_snapshot`` keeps one immutable cluster set per
pipeline run (investigations reference those rows), so a run analysed twice
holds every cluster id twice. Readers that selected by ``test_run_id`` alone
listed each cluster twice, double-counted project metrics, and a lookup by
``cluster_id`` raised ``MultipleResultsFound`` (homelab, 2026-10-14: three
runs). ``cluster_snapshot.is_current_snapshot()`` keeps each run's newest set
and leaves the older one in the table.
"""
from __future__ import annotations

import os
import sys
import uuid
import warnings
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_readers_see_each_runs_newest_cluster_snapshot():
    from app.models.postgres import AgentPipelineRun, FailureCluster, Project, TestRun
    from app.services.cluster_service import check_duplicate
    from app.services.cluster_snapshot import current_snapshot_sql, is_current_snapshot

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    reanalysed = uuid.uuid4()   # analysed twice: two snapshots
    seeded = uuid.uuid4()       # demo data: one snapshot, no pipeline
    old_pipeline, new_pipeline = uuid.uuid4(), uuid.uuid4()
    earlier = datetime.now(timezone.utc) - timedelta(hours=1)
    later = datetime.now(timezone.utc)

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            db.add(Project(id=project_id, name=f"Snapshots {token}", slug=f"snapshots-{token}"))
            await db.flush()
            for run_id in (reanalysed, seeded):
                db.add(TestRun(id=run_id, project_id=project_id, build_number=f"b-{run_id.hex[:8]}",
                               jenkins_job="cluster-snapshot-integration", branch="main"))
            await db.flush()
            db.add_all([AgentPipelineRun(id=old_pipeline, test_run_id=reanalysed),
                        AgentPipelineRun(id=new_pipeline, test_run_id=reanalysed)])
            await db.flush()

            def cluster(run_id, pipeline_id, cluster_id, label, size, at):
                return FailureCluster(test_run_id=run_id, pipeline_run_id=pipeline_id, cluster_id=cluster_id,
                                      label=label, size=size, member_test_ids=[], created_at=at)

            db.add_all([
                cluster(reanalysed, old_pipeline, "cl_001", "checkout total (first analysis)", 3, earlier),
                cluster(reanalysed, old_pipeline, "cl_002", "rate service (first analysis)", 2, earlier),
                cluster(reanalysed, new_pipeline, "cl_001", "checkout total", 2, later),
                cluster(reanalysed, new_pipeline, "cl_003", "auth token", 2, later),
                cluster(seeded, None, "cl_001", "seeded cluster", 2, earlier),
            ])
            await db.commit()

        async with sessions() as db:
            # One run: the newest snapshot only.
            current = (await db.execute(
                select(FailureCluster.cluster_id, FailureCluster.label, FailureCluster.pipeline_run_id)
                .where(FailureCluster.test_run_id == reanalysed, is_current_snapshot())
                .order_by(FailureCluster.cluster_id)
            )).all()
            assert [(r.cluster_id, r.label) for r in current] == [("cl_001", "checkout total"), ("cl_003", "auth token")]
            assert {r.pipeline_run_id for r in current} == {new_pipeline}

            # A lookup by cluster id no longer finds two rows.
            found = await check_duplicate(reanalysed, "cl_001", db)
            assert found["cluster_label"] == "checkout total"

            # Across runs: each run's newest snapshot, a pipeline-less seed included.
            across = (await db.execute(
                select(FailureCluster.test_run_id, FailureCluster.cluster_id)
                .where(FailureCluster.test_run_id.in_([reanalysed, seeded]), is_current_snapshot())
            )).all()
            assert sorted((str(r.test_run_id), r.cluster_id) for r in across) == sorted([
                (str(reanalysed), "cl_001"), (str(reanalysed), "cl_003"), (str(seeded), "cl_001"),
            ])

            # The raw-SQL form agrees: clustered failures = 2 + 2 + 2, not 3 + 2 + 2 + 2 + 2.
            total = (await db.execute(text(f"""
                SELECT COALESCE(SUM(fc.size), 0) FROM failure_clusters fc
                JOIN test_runs tr ON tr.id = fc.test_run_id
                WHERE tr.project_id = :project_id AND fc.size >= 2 AND {current_snapshot_sql('fc')}
            """), {"project_id": str(project_id)})).scalar_one()
            assert total == 6

            # History stays: the first analysis's rows are still there.
            kept = (await db.execute(
                select(func.count()).select_from(FailureCluster).where(FailureCluster.test_run_id == reanalysed)
            )).scalar_one()
            assert kept == 4
    finally:
        primary_error = sys.exception()
        try:
            async with sessions() as db:
                await db.execute(delete(Project).where(Project.id == project_id))
                await db.commit()
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            warnings.warn(f"snapshot cleanup failed after primary failure: {cleanup_error!r}",
                          RuntimeWarning, stacklevel=1)
        finally:
            await engine.dispose()
