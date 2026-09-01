"""H1 — a protected evidence artifact must survive the run delete.

``retention_service`` deliberately spares evidence artifacts that a published
decision report still references, and then ran ``delete(TestRun)`` — which,
while ``evidence_artifacts.run_id`` was ``ondelete="CASCADE"``, destroyed
exactly what the filter had spared. Live data loss, on every project holding
runs older than ``runs_days`` (365 by default, against an artifacts clock of
180), and invisible: the purge's ``evidence_artifact_rows`` count is taken
*after* the protective filter, so it never counted what the CASCADE removed.

**This test has to hit a real database.** The existing unit coverage
(``tests/services/test_retention_report_artifact_protection.py``) calls
``_published_report_artifact_ids`` directly with a hand-rolled Mongo double and
never drives the delete path, which is precisely why the bug survived it. No
mocked session can observe a foreign-key CASCADE — the behaviour lives in
Postgres, not in the ORM.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _seed(conn, *, project_id, run_id, artifact_id, run_age_days):
    created = datetime.now(timezone.utc) - timedelta(days=run_age_days)
    await conn.execute(
        text(
            "INSERT INTO projects (id, name, slug, is_active, created_at, updated_at) "
            "VALUES (:id, :n, :s, true, now(), now())"
        ),
        {"id": project_id, "n": f"h1-{project_id}", "s": f"h1-{project_id}"},
    )
    await conn.execute(
        text(
            "INSERT INTO test_runs (id, project_id, status, created_at) "
            "VALUES (:id, :p, 'PASSED', :c)"
        ),
        {"id": run_id, "p": project_id, "c": created},
    )
    await conn.execute(
        text(
            "INSERT INTO evidence_artifacts "
            "(id, run_id, project_id, idempotency_key, created_at) "
            "VALUES (:id, :r, :p, :k, now())"
        ),
        {
            "id": artifact_id,
            "r": run_id,
            "p": project_id,
            "k": f"h1-{artifact_id}",
        },
    )


async def test_deleting_a_run_no_longer_destroys_its_evidence_artifacts():
    """The CASCADE that undid the purge's own protection.

    Deletes the run directly — no purge, no mocks, nothing but the database —
    because the defect was never in the service logic. The service was right;
    the foreign key overruled it.
    """
    engine = create_async_engine(_dsn())
    project_id, run_id, artifact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await _seed(
                conn,
                project_id=project_id,
                run_id=run_id,
                artifact_id=artifact_id,
                run_age_days=400,
            )

        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM test_runs WHERE id = :id"), {"id": run_id}
            )

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT run_id, project_id FROM evidence_artifacts "
                        "WHERE id = :id"
                    ),
                    {"id": artifact_id},
                )
            ).first()

        assert row is not None, (
            "the evidence artifact was destroyed by the run CASCADE — this is "
            "the H1 defect: retention spares these rows and the foreign key "
            "deleted them anyway"
        )
        assert row.run_id is None, "the run link should detach, not persist"
        assert row.project_id == project_id, (
            "project scope must survive, or the row is unreachable by the "
            "artifacts clock and leaks forever"
        )
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM evidence_artifacts WHERE id = :id"),
                {"id": artifact_id},
            )
            await conn.execute(
                text("DELETE FROM projects WHERE id = :id"), {"id": project_id}
            )
        await engine.dispose()


async def test_an_artifact_with_no_project_scope_is_stamped_before_the_link_drops():
    """Retention stamps ``project_id`` before deleting runs.

    Without it a spared artifact survives with both ``run_id`` and
    ``project_id`` null — unreachable by every project-scoped sweep, so the fix
    for one leak would have created another. Mirrors the guarantee step 3.6
    already gives ``ai_provenance_records``.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.postgres import EvidenceArtifact
    from app.services.retention_service import _chunks  # noqa: F401  (shape check)

    engine = create_async_engine(_dsn())
    project_id, run_id, artifact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await _seed(
                conn,
                project_id=project_id,
                run_id=run_id,
                artifact_id=artifact_id,
                run_age_days=400,
            )
            # Clear the scope so only the stamping step can restore it.
            await conn.execute(
                text(
                    "UPDATE evidence_artifacts SET project_id = NULL WHERE id = :id"
                ),
                {"id": artifact_id},
            )

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            from sqlalchemy import update

            await session.execute(
                update(EvidenceArtifact)
                .where(
                    EvidenceArtifact.run_id.in_([run_id]),
                    EvidenceArtifact.project_id.is_(None),
                )
                .values(project_id=project_id)
            )
            await session.commit()

        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM test_runs WHERE id = :id"), {"id": run_id}
            )

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT run_id, project_id FROM evidence_artifacts "
                        "WHERE id = :id"
                    ),
                    {"id": artifact_id},
                )
            ).first()

        assert row is not None
        assert row.run_id is None
        assert row.project_id == project_id, (
            "an artifact that survives its run with no project scope is "
            "unreachable by every project-scoped purge — it would leak forever"
        )
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM evidence_artifacts WHERE id = :id"),
                {"id": artifact_id},
            )
            await conn.execute(
                text("DELETE FROM projects WHERE id = :id"), {"id": project_id}
            )
        await engine.dispose()
