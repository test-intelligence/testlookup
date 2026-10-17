"""W09 FLK-03 on real PostgreSQL: the four-signal score on ``GET /projects/{id}/flaky-coach``.

* two projects share a fingerprint, each with its own ``flaky_score`` row:
  project P's coach carries P's score and components only;
* a test without a row reads ``insufficient`` / ``pending`` from the sweep's
  own count — executions in the last 30 days, in THIS project (older ones and
  the other project's do not count);
* the two statements are bounded and indexed: the score lookup is an index
  scan bounded by the project (``ux_flaky_score_project_fingerprint`` or
  ``ix_flaky_score_project_score``), never a Seq Scan;
* a broken lookup leaves ``flaky_score`` null and the page still answers 200.

Uses ``tests/integration/defect_w06_world.py`` (P and Q, a QA-engineer member
of P, the app over ASGI). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to
head and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tests.integration.defect_w06_world import StatementCounter, defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)
SHARED, THIN, READY = "w09c-fp-shared", "w09c-fp-thin", "w09c-fp-ready"


async def _coach_row(db, project_id, fingerprint, impact):
    from app.models.postgres import FlakyCoachResult

    db.add(FlakyCoachResult(
        project_id=project_id, test_fingerprint=fingerprint, test_name=f"test_{fingerprint}",
        suite_name="checkout-api", failure_rate=0.4, total_runs=10, failed_runs=4,
        quarantine_recommendation="INVESTIGATE", impact_score=impact,
        status_history=["PASSED", "FAILED", "PASSED", "FAILED"], stabilization_actions=[],
    ))


async def _score_row(db, project_id, fingerprint, score, components, weights, observations, confidence):
    from app.models.postgres import FlakyScore

    db.add(FlakyScore(
        project_id=project_id, test_fingerprint=fingerprint, test_name=f"test_{fingerprint}",
        score=score, components=components, weights=weights, observation_count=observations,
        confidence=confidence, window_days=30,
    ))


async def _executions(db, w, project_id, fingerprint, count, *, days_ago=1):
    from app.models.postgres import LaunchStatus, TestCase, TestRun

    for _ in range(count):
        run_id = uuid.uuid4()
        db.add(TestRun(
            id=run_id, project_id=project_id, build_number=f"w09c-{run_id.hex[:8]}", jenkins_job="w09c",
            status=LaunchStatus.PASSED, ingestion_source="unknown", total_tests=1, passed_tests=1,
            created_at=NOW - timedelta(days=days_ago),
        ))
        await db.flush()
        db.add(TestCase(id=uuid.uuid4(), test_run_id=run_id, test_name=f"test_{fingerprint}",
                        suite_name="checkout-api", status="PASSED", test_fingerprint=fingerprint))
    await db.flush()


@pytest.fixture
async def world():
    async with defect_world() as w:
        async with w.sessions.begin() as db:
            for fingerprint, impact in ((SHARED, 30.0), (THIN, 20.0), (READY, 10.0)):
                await _coach_row(db, w.P, fingerprint, impact)
            await _coach_row(db, w.Q, SHARED, 50.0)
            await _score_row(
                db, w.P, SHARED, 0.4,
                {"result_volatility": 0.6, "retry_rate": 0.2, "environment_instability": 0.1, "duration_variance": 0.05},
                {"result_volatility": 0.45, "retry_rate": 0.25, "environment_instability": 0.2, "duration_variance": 0.1},
                12, "medium",
            )
            await _score_row(
                db, w.Q, SHARED, 0.95,
                {"result_volatility": 1.0, "retry_rate": 1.0, "environment_instability": 1.0, "duration_variance": 1.0},
                {"result_volatility": 0.7, "retry_rate": 0.1, "environment_instability": 0.1, "duration_variance": 0.1},
                40, "high",
            )
            await _executions(db, w, w.P, THIN, 3)
            await _executions(db, w, w.P, THIN, 4, days_ago=45)
            await _executions(db, w, w.Q, THIN, 9)
            await _executions(db, w, w.P, READY, 6)
        yield w


def _by_fingerprint(body) -> dict:
    return {e["test_fingerprint"]: e["flaky_score"] for e in body["entries"]}


async def test_each_project_reads_its_own_score_and_its_own_count(world):
    w = world
    with StatementCounter(w.engine) as counter:
        response = await w.client.get(f"/api/v1/projects/{w.P}/flaky-coach", headers=w.member)
    assert response.status_code == 200, response.text
    scores = _by_fingerprint(response.json())
    assert set(scores) >= {SHARED, THIN, READY}

    shared = scores[SHARED]
    assert (shared["status"], shared["score"], shared["confidence"], shared["observations"]) == (
        "scored", 0.4, "medium", 12,
    )
    assert [(c["name"], c["value"], c["weight"]) for c in shared["components"]] == [
        ("result_volatility", 0.6, 0.45),
        ("retry_rate", 0.2, 0.25),
        ("environment_instability", 0.1, 0.2),
        ("duration_variance", 0.05, 0.1),
    ]
    assert sum(c["weight"] for c in shared["components"]) == pytest.approx(1.0, abs=1e-9)
    assert shared["computed_at"] and shared["computed_at"].endswith("+00:00")

    thin = scores[THIN]
    assert (thin["status"], thin["score"], thin["observations"], thin["min_observations"]) == (
        "insufficient", None, 3, 5,
    ), "only P's executions of the last 30 days count"
    ready = scores[READY]
    assert (ready["status"], ready["score"], ready["observations"]) == ("pending", None, 6)

    score_reads = [s for s in counter.statements if "FROM flaky_score" in s]
    count_reads = [s for s in counter.statements if "count(*)" in s.lower() and "test_fingerprint IN" in s]
    assert len(score_reads) == 1, score_reads
    assert "flaky_score.project_id = " in score_reads[0]
    assert len(count_reads) == 1, count_reads
    assert "test_runs.project_id = " in count_reads[0]

    other = await w.client.get(f"/api/v1/projects/{w.Q}/flaky-coach", headers=w.admin)
    assert other.status_code == 200, other.text
    q_shared = _by_fingerprint(other.json())[SHARED]
    assert (q_shared["score"], q_shared["confidence"], q_shared["observations"]) == (0.95, "high", 40)


async def test_the_score_lookup_can_use_the_project_fingerprint_index(world):
    w = world
    async with w.sessions() as db:
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in (await db.execute(text(
            "EXPLAIN SELECT * FROM flaky_score WHERE project_id = :pid AND test_fingerprint IN (:a, :b)"
        ), {"pid": w.P, "a": SHARED, "b": THIN})).all())
    # Either project-leading index serves it (on a tiny table the planner may
    # prefer ix_flaky_score_project_score and filter the fingerprints); what
    # matters is that the project bounds an index scan, never a Seq Scan.
    assert "Seq Scan" not in plan, plan
    assert any(f"Index Scan using {ix}" in plan or f"Bitmap Index Scan on {ix}" in plan
               for ix in ("ux_flaky_score_project_fingerprint", "ix_flaky_score_project_score")), plan
    assert "project_id = " in plan, plan


async def test_a_broken_lookup_leaves_the_score_null_and_the_page_answers(world, monkeypatch):
    from app.services import flaky_score_brief

    async def broken(db, project_id, fingerprints):
        await db.execute(text("SELECT * FROM flaky_score_table_that_does_not_exist"))
        return {}

    monkeypatch.setattr(flaky_score_brief, "_load_briefs", broken)
    monkeypatch.setattr(flaky_score_brief.logger, "warning", lambda *a, **k: None)
    w = world
    response = await w.client.get(f"/api/v1/projects/{w.P}/flaky-coach", headers=w.member)
    assert response.status_code == 200, response.text
    scores = _by_fingerprint(response.json())
    assert scores and all(value is None for value in scores.values())
