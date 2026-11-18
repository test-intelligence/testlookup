"""``flaky_score_service.store_scores`` against real Postgres: one row per (project, fingerprint).

``store_scores`` is a read-then-write upsert run by the nightly
``recompute_flaky_scores`` sweep. Until now it was tested only by reading its
source (``tests/regression/test_phase2_signal_quality.py``), so the unique
index ``ux_flaky_score_project_fingerprint`` was never exercised. Here, with
scores built by the real ``compute_score``:

* a second pass UPDATES each row (new score, components, observation count,
  confidence, and the window it was computed over) and never adds one;
* the same fingerprint in another project keeps its own row, untouched;
* an unscored result (below the evidence floor) writes no row: a stored 0.0
  would read as "measured, and clean".

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head; skips only without
it. Writes two throwaway projects and removes them (the cleanup raises if a row
is left behind).
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _cleanup(sessions, project_ids: list[uuid.UUID]) -> None:
    """Delete the seeded projects (scores cascade) and prove nothing is left."""
    async with sessions.begin() as db:
        await db.execute(
            text("DELETE FROM projects WHERE id = ANY(:ids)"), {"ids": project_ids}
        )
    async with sessions() as db:
        left = {
            "projects": (
                await db.execute(
                    text("SELECT count(*) FROM projects WHERE id = ANY(:ids)"),
                    {"ids": project_ids},
                )
            ).scalar_one(),
            "flaky_score": (
                await db.execute(
                    text(
                        "SELECT count(*) FROM flaky_score WHERE project_id = ANY(:ids)"
                    ),
                    {"ids": project_ids},
                )
            ).scalar_one(),
        }
    leaked = {table: count for table, count in left.items() if count}
    if leaked:
        raise AssertionError(f"flaky score store cleanup left rows behind: {leaked}")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def projects():
    from app.models.postgres import Project

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    p, q = uuid.uuid4(), uuid.uuid4()
    try:
        async with sessions.begin() as db:
            db.add_all(
                [
                    Project(
                        id=p,
                        name=f"flakystore-p-{tag}",
                        slug=f"flakystore-p-{tag}",
                        is_active=True,
                        description="throwaway flaky score store",
                    ),
                    Project(
                        id=q,
                        name=f"flakystore-q-{tag}",
                        slug=f"flakystore-q-{tag}",
                        is_active=True,
                        description="throwaway flaky score store, other project",
                    ),
                ]
            )
        yield sessions, p, q, tag
    finally:
        await _cleanup(sessions, [p, q])
        await engine.dispose()


def _score(fingerprint: str, name: str, observations: int, volatility: float):
    from app.services.flaky_score_service import compute_score

    return compute_score(
        test_fingerprint=fingerprint,
        test_name=name,
        observation_count=observations,
        result_volatility=volatility,
        retry_rate=0.0,
        duration_variance=0.0,
        environment_instability=0.0,
    )


async def _store(sessions, project_id, scores, *, window_days: int) -> int:
    from app.services.flaky_score_service import store_scores

    async with sessions() as db:
        try:
            written = await store_scores(
                db, project_id, scores, window_days=window_days
            )
            await db.commit()
        except IntegrityError as exc:  # the unique index refused a second row
            pytest.fail(
                f"store_scores inserted a second row for a stored (project, fingerprint): {exc.orig}"
            )
    return written


async def _rows(sessions, project_id) -> dict[str, object]:
    from app.models.postgres import FlakyScore

    async with sessions() as db:
        rows = (
            (
                await db.execute(
                    select(FlakyScore).where(FlakyScore.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
    by_fp = {row.test_fingerprint: row for row in rows}
    assert len(by_fp) == len(rows), (
        f"duplicate fingerprints for project {project_id}: {[r.test_fingerprint for r in rows]}"
    )
    return by_fp


async def test_a_second_pass_updates_every_row_and_never_duplicates(projects):
    sessions, p, q, tag = projects
    fp_a, fp_b, fp_thin = f"{tag}-a", f"{tag}-b", f"{tag}-thin"

    # The other project scores fp_a first, so a lookup that lost its project
    # predicate would find (and overwrite) Q's row instead of writing P's.
    assert (
        await _store(
            sessions, q, [_score(fp_a, "test_a in Q", 12, 0.10)], window_days=30
        )
        == 1
    )
    q_before = (await _rows(sessions, q))[fp_a]

    first = [
        _score(fp_a, "test_a", 6, 0.20),
        _score(fp_b, "test_b", 25, 0.05),
        _score(fp_thin, "test_thin", 2, 0.90),  # below the evidence floor: no score
    ]
    assert first[2].is_scored is False
    assert await _store(sessions, p, first, window_days=30) == 2

    after_first = await _rows(sessions, p)
    assert {fp_a, fp_b} <= set(after_first), (
        f"project P's scores are not P's rows (a lookup across projects updates another project's row): "
        f"{sorted(after_first)}"
    )
    assert fp_thin not in after_first, "an unscored fingerprint must not be stored"
    assert len(after_first) == 2
    assert after_first[fp_a].score == pytest.approx(first[0].score)
    assert after_first[fp_a].observation_count == 6
    assert after_first[fp_a].confidence == first[0].confidence
    ids = {fp: row.id for fp, row in after_first.items()}

    second = [
        _score(fp_a, "test_a renamed", 30, 0.60),
        _score(fp_b, "test_b", 26, 0.00),
        _score(fp_thin, "test_thin", 4, 0.90),  # still thin
    ]
    assert second[0].score != pytest.approx(first[0].score), (
        "the second pass must carry a changed score"
    )
    assert await _store(sessions, p, second, window_days=90) == 2

    after_second = await _rows(sessions, p)
    assert set(after_second) == {fp_a, fp_b}
    # Updated in place: same row ids, new values.
    assert {fp: row.id for fp, row in after_second.items()} == ids
    a = after_second[fp_a]
    assert a.score == pytest.approx(second[0].score)
    assert a.components == pytest.approx(dict(second[0].components))
    assert a.weights == pytest.approx(dict(second[0].weights))
    assert a.observation_count == 30
    assert a.confidence == second[0].confidence == "high"
    assert a.test_name == "test_a renamed"
    assert a.window_days == 90, (
        "the stored window must be the one the score was computed over"
    )
    assert after_second[fp_b].score == pytest.approx(second[1].score)
    assert after_second[fp_b].window_days == 90

    # The other project's row is its own and untouched.
    q_after = await _rows(sessions, q)
    assert set(q_after) == {fp_a}
    assert q_after[fp_a].id == q_before.id
    assert q_after[fp_a].score == pytest.approx(q_before.score)
    assert q_after[fp_a].observation_count == 12
    assert q_after[fp_a].test_name == "test_a in Q"
    assert q_after[fp_a].window_days == 30

    async with sessions() as db:
        total = (
            await db.execute(
                text("SELECT count(*) FROM flaky_score WHERE project_id = ANY(:ids)"),
                {"ids": [p, q]},
            )
        ).scalar_one()
    assert total == 3
