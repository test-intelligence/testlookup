"""E2E pass 2026-10-10: the pipeline's category never reached the test case.

``POST /analyze`` writes a failure's category to ``ai_analysis`` AND
``test_cases``; the pipeline's ``_batch_upsert_analyses`` wrote only
``ai_analysis``. Failure Analysis, the chart rows, digests, failure groups and
My Failures read ``test_cases.failure_category``, so a pipeline-analysed run
read "100% Unknown" there while Run Intelligence showed INFRASTRUCTURE for the
same six failures. Homelab: 69 pipeline analyses, 0 categories on their test
cases. Human corrections had the same gap.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.regression


def _sql(stmt) -> tuple[str, dict]:
    c = stmt.compile(dialect=postgresql.dialect())
    return str(c), dict(c.params)


@pytest.mark.asyncio
async def test_the_mirror_fills_only_an_unset_or_unknown_category():
    from app.services.failure_category_sync import mirror_ai_category

    db = AsyncMock()
    tc = uuid.uuid4()
    await mirror_ai_category(db, tc, "infrastructure")
    sql, params = _sql(db.execute.await_args.args[0])
    assert sql.startswith("UPDATE test_cases SET failure_category=")
    assert "failure_category IS NULL OR test_cases.failure_category =" in sql
    assert "INFRASTRUCTURE" in params.values() and "UNKNOWN" in params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [None, "UNKNOWN", "not-a-category", 3])
async def test_nothing_worth_copying_writes_nothing(category):
    from app.services.failure_category_sync import mirror_ai_category

    db = AsyncMock()
    await mirror_ai_category(db, uuid.uuid4(), category)
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_human_correction_overrides_whatever_was_there():
    from app.services.failure_category_sync import apply_human_category

    db = AsyncMock()
    await apply_human_category(db, uuid.uuid4(), "PRODUCT_BUG")
    sql, params = _sql(db.execute.await_args.args[0])
    assert "IS NULL" not in sql
    assert "PRODUCT_BUG" in params.values()


@pytest.mark.asyncio
async def test_the_pipeline_upsert_writes_the_test_case_category_in_the_same_session():
    from app.agents import analysis_agent as mod

    executed: list = []
    session = MagicMock()
    session.execute = AsyncMock(side_effect=lambda stmt, *a, **k: executed.append(stmt))
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    tc_a, tc_b = str(uuid.uuid4()), str(uuid.uuid4())
    analyses = {
        tc_a: {"root_cause_summary": "db refused", "failure_category": "INFRASTRUCTURE", "confidence_score": 90},
        tc_b: {"root_cause_summary": "unclear", "failure_category": "UNKNOWN", "confidence_score": 20},
    }
    with patch.object(mod, "AsyncSessionLocal", return_value=cm), \
            patch("app.services.cache_service.bump_analytics_epoch", AsyncMock()):
        await mod.AnalysisAgent()._batch_upsert_analyses(analyses, project_id="p")

    updates = [_sql(s) for s in executed if _sql(s)[0].startswith("UPDATE test_cases")]
    assert len(updates) == 1, "one UPDATE for the categorised test, none for UNKNOWN"
    assert "INFRASTRUCTURE" in updates[0][1].values()
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_a_correction_moves_the_test_case_category_too():
    from types import SimpleNamespace

    from app.models.postgres import FeedbackRating
    from app.services import feedback_service

    analysis = SimpleNamespace(id=uuid.uuid4(), test_case_id=uuid.uuid4(), failure_category="INFRASTRUCTURE",
                               root_cause_summary="x", requires_human_review=True)
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=analysis)))
    body = SimpleNamespace(rating=FeedbackRating.INCORRECT, corrected_category="PRODUCT_BUG",
                           corrected_root_cause=None, comment=None)
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")
    with patch.object(feedback_service, "_require_analysis_access", AsyncMock()), \
            patch.object(feedback_service, "apply_human_category", AsyncMock()) as human:
        await feedback_service.submit_feedback(db, analysis.id, body, user)
    human.assert_awaited_once_with(db, analysis.test_case_id, "PRODUCT_BUG")
    assert analysis.failure_category == "PRODUCT_BUG"


def test_the_backfill_migration_never_overwrites_a_set_label():
    import importlib

    m = importlib.import_module("migrations.versions.0200_test_case_category_from_its_analysis")
    sql = " ".join(m.SQL.split())
    assert "(tc.failure_category IS NULL OR tc.failure_category = 'UNKNOWN')" in sql
    assert "'UNKNOWN'" not in sql.split("a.failure_category IN")[1].split(")")[0]
    assert m.down_revision == "0199"
