"""W05 REL-01 on real PostgreSQL: ``current_gate_summaries``, the verdict a release list embeds.

* Only the CURRENT release-level decision is read: history rows
  (``is_current = false``) and phase rows (``phase_id`` set) are ignored, and a
  release with no decision is absent (the list embeds ``gate: null``).
* A row whose ``blocking_reasons`` / ``run_ids`` hold the JSON scalar ``null``
  (what SQLAlchemy's ``JSON`` stores for ``None``) or SQL NULL does not raise.
* The statement count is the same for 1 release as for 20 (counted with
  ``before_cursor_execute``), and a 5,000-reason decision embeds 20 reasons
  and the full total.
* A fingerprint is named only from the release's OWN project: the same
  fingerprint in another tenant's run never names it.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``. Every row is created under fresh
projects and removed (cascade) at teardown.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world():
    import app.db.postgres as app_postgres
    from app.models.postgres import Project, TestCase, TestRun

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    tag = uuid.uuid4().hex[:10]
    mine, foreign = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with AsyncSession(engine) as db:
        db.add_all([
            Project(id=mine, name=f"gs-{tag}", slug=f"gs-{tag}", is_active=True),
            Project(id=foreign, name=f"gs-f-{tag}", slug=f"gs-f-{tag}", is_active=True),
        ])
        await db.flush()
        mine_run, foreign_run = uuid.uuid4(), uuid.uuid4()
        db.add_all([
            TestRun(id=mine_run, project_id=mine, build_number=f"b-{tag}", status="FAILED",
                    total_tests=1, failed_tests=1, created_at=now, start_time=now),
            TestRun(id=foreign_run, project_id=foreign, build_number=f"bf-{tag}", status="FAILED",
                    total_tests=2, failed_tests=2, created_at=now, start_time=now),
        ])
        await db.flush()
        db.add_all([
            TestCase(test_run_id=mine_run, test_fingerprint=f"{tag}-pay", test_name="test_pay",
                     suite_name="checkout-e2e", status="FAILED"),
            # The foreign tenant has a test under the SAME fingerprint as one of
            # ours that has no rows in our project: it must never name ours.
            TestCase(test_run_id=foreign_run, test_fingerprint=f"{tag}-ghost", test_name="foreign_secret",
                     suite_name="x", status="FAILED"),
            TestCase(test_run_id=foreign_run, test_fingerprint=f"{tag}-pay", test_name="foreign_pay",
                     suite_name="x", status="FAILED"),
        ])
        await db.commit()
    try:
        yield engine, mine, tag
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM projects WHERE id IN (:a, :b)"), {"a": mine, "b": foreign})
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _release(db, project_id, name):
    from app.models.postgres import Release

    release = Release(id=uuid.uuid4(), project_id=project_id, name=name, status="in_progress")
    db.add(release)
    await db.flush()
    return release.id


def _decision(release_id, **kw):
    from app.models.postgres import ReleaseGateDecision

    fields = dict(
        id=uuid.uuid4(), release_id=release_id, is_current=True, verdict="GO",
        denominator=10, evidence_count=10, run_ids=["r1", "r2"],
        status_rollup={"PASSED": 10, "FAILED": 0, "SKIPPED": 0, "BROKEN": 0, "UNKNOWN": 0},
        ingestion_complete=True, incomplete_runs={}, blocking_reasons=[],
    )
    fields.update(kw)
    return ReleaseGateDecision(**fields)


async def test_only_the_current_release_level_decision_is_embedded(world):
    from app.models.postgres import ReleasePhase
    from app.services.release_gate_service import current_gate_summaries

    engine, project, tag = world
    async with AsyncSession(engine) as db:
        evaluated = await _release(db, project, f"R1-{tag}")
        never = await _release(db, project, f"R2-{tag}")
        phase_only = await _release(db, project, f"R3-{tag}")
        phase = ReleasePhase(id=uuid.uuid4(), release_id=phase_only, name="Soak", order_index=0)
        db.add(phase)
        await db.flush()
        old = datetime.now(timezone.utc) - timedelta(days=2)
        db.add_all([
            # History: an earlier NO_GO, demoted.
            _decision(evaluated, is_current=False, verdict="NO_GO", created_at=old,
                      blocking_reasons=["checkout-e2e::x is FAILED"]),
            _decision(evaluated, verdict="GO"),
            # A phase verdict is not the release's verdict.
            _decision(phase_only, phase_id=phase.id, verdict="NO_GO"),
        ])
        await db.commit()
        out = await current_gate_summaries(db, [evaluated, never, phase_only])
    assert set(out) == {str(evaluated)}, "never-evaluated and phase-only releases are absent"
    summary = out[str(evaluated)]
    assert summary["verdict"] == "GO"
    assert summary["pass_rate"] == 100.0
    assert summary["run_count"] == 2
    assert summary["blocking_reasons"] == [] and summary["blocking_total"] == 0
    assert summary["measured"] is True and summary["insufficient_reason"] is None
    assert summary["decided_at"].endswith("+00:00")


async def test_json_null_and_sql_null_arrays_do_not_raise(world):
    from app.services.release_gate_service import current_gate_summaries

    engine, project, tag = world
    async with AsyncSession(engine) as db:
        json_null = await _release(db, project, f"JN-{tag}")
        sql_null = await _release(db, project, f"SN-{tag}")
        # SQLAlchemy's JSON type stores Python None as the JSON scalar null.
        db.add(_decision(json_null, verdict="NOT_EVALUATED", run_ids=None, blocking_reasons=None,
                         status_rollup=None, incomplete_runs=None, evidence_count=0, denominator=0))
        await db.flush()
        await db.execute(text(
            "INSERT INTO release_gate_decisions (id, release_id, is_current, verdict, denominator, "
            "evidence_count, run_ids, status_rollup, blocking_reasons, created_at) "
            "VALUES (:id, :r, true, 'GO', 6, 6, NULL, NULL, NULL, now())"
        ), {"id": uuid.uuid4(), "r": sql_null})
        await db.commit()
        kinds = dict((await db.execute(text(
            "SELECT release_id::text, json_typeof(run_ids) FROM release_gate_decisions "
            "WHERE release_id IN (:a, :b)"
        ), {"a": json_null, "b": sql_null})).all())
        out = await current_gate_summaries(db, [json_null, sql_null])
    assert kinds == {str(json_null): "null", str(sql_null): None}, "both null shapes are exercised"
    for rid in (json_null, sql_null):
        summary = out[str(rid)]
        assert summary["run_count"] == 0 and summary["blocking_total"] == 0
        assert summary["blocking_reasons"] == [] and summary["status_counts"] == {}
        assert summary["pass_rate"] is None, "a legacy row with no PASSED count has no pass rate"
    assert out[str(json_null)]["insufficient_reason"] == "0 of 0 tests carry evidence; 5 required"


async def test_the_statement_count_is_constant_in_the_number_of_releases(world):
    from app.services.release_gate_service import current_gate_summaries

    engine, project, tag = world
    statements: list[str] = []

    def _count(conn, cursor, statement, *a):
        statements.append(statement)

    async with AsyncSession(engine) as db:
        ids = []
        for i in range(20):
            rid = await _release(db, project, f"N{i}-{tag}")
            ids.append(rid)
            db.add(_decision(rid, verdict="NO_GO", blocking_reasons=[
                f"checkout-e2e::{tag}-pay is FAILED", "CRITICAL defect open: Totals",
            ]))
        await db.commit()
        event.listen(engine.sync_engine, "before_cursor_execute", _count)
        try:
            one = await current_gate_summaries(db, ids[:1])
            count_one = len(statements)
            statements.clear()
            twenty = await current_gate_summaries(db, ids)
            count_twenty = len(statements)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _count)
    assert len(one) == 1 and len(twenty) == 20
    assert count_one == count_twenty == 2, (count_one, count_twenty)
    for summary in twenty.values():
        assert summary["blocking_reasons"] == [
            "test_pay (checkout-e2e) is FAILED", "CRITICAL defect open: Totals",
        ]


async def test_a_huge_decision_embeds_twenty_reasons_and_the_full_total(world):
    from app.services.release_gate_service import GATE_LIST_REASON_CAP, current_gate_summaries

    engine, project, tag = world
    reasons = [f"suite::fp{i:05d} is FAILED" for i in range(5000)]
    async with AsyncSession(engine) as db:
        rid = await _release(db, project, f"BIG-{tag}")
        db.add(_decision(rid, verdict="NO_GO", blocking_reasons=reasons,
                         run_ids=[str(uuid.uuid4()) for _ in range(300)]))
        await db.commit()
        out = await current_gate_summaries(db, [rid])
    summary = out[str(rid)]
    assert summary["blocking_total"] == 5000
    assert summary["run_count"] == 300
    assert summary["blocking_reasons"] == reasons[:GATE_LIST_REASON_CAP]


async def test_a_fingerprint_is_named_only_from_the_releases_own_project(world):
    from app.services.release_gate_service import current_gate_summaries

    engine, project, tag = world
    async with AsyncSession(engine) as db:
        rid = await _release(db, project, f"T-{tag}")
        db.add(_decision(rid, verdict="NO_GO", blocking_reasons=[
            f"checkout-e2e::{tag}-pay is FAILED",
            f"checkout-e2e::{tag}-ghost is FAILED",
        ]))
        await db.commit()
        out = await current_gate_summaries(db, [rid])
    assert out[str(rid)]["blocking_reasons"] == [
        "test_pay (checkout-e2e) is FAILED",
        # Only the foreign tenant has a test under this fingerprint.
        f"checkout-e2e::{tag}-ghost is FAILED",
    ]


async def test_the_release_form_names_tests_from_its_own_project(world):
    from app.services.release_gate_reasons import display_reasons_for_release

    engine, project, tag = world
    async with AsyncSession(engine) as db:
        rid = await _release(db, project, f"D-{tag}")
        await db.commit()
        out = await display_reasons_for_release(db, rid, [
            f"checkout-e2e::{tag}-pay is FAILED", f"x::{tag}-ghost is FAILED", "open defect",
        ])
    assert out == ["test_pay (checkout-e2e) is FAILED", f"x::{tag}-ghost is FAILED", "open defect"]
