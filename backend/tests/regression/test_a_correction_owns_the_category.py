"""A human correction takes full effect and survives re-analysis (D-06, D-85, S-A-5).

Seen on the default install, rules mode. A failed test labelled Flaky was
corrected to Product Bug:

* the card kept "⚠ High Flakiness", "Exhibits flaky behavior", the quarantine
  and retry actions and "estimated 85%", with no human-corrected marker (D-06);
* the Tests tab's Category column could not follow a later analysis: the
  pipeline filled the column only when empty, so the first analysis won for
  good (D-85, D-150);
* pressing Re-analyse replaced the correction with the engine's answer (S-A-5).
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.models.postgres import FeedbackRating, TestCase
from app.services import failure_category_sync as sync
from app.services import feedback_service

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"


# ── D-06: the whole card follows the corrected category ──────────────────


def test_a_correction_to_product_bug_clears_the_flaky_card():
    row = SimpleNamespace(
        failure_category="FLAKY",
        root_cause_summary="Test has a 15% historical failure rate. Exhibits flaky behavior.",
        is_flaky=True,
        recommended_actions=["Quarantine test to prevent blocking releases"],
        confidence_score=85,
        requires_human_review=False,
        routing_metadata={"analysis_mode": "rules", "confidence_basis": "heuristic_estimate"},
    )
    body = SimpleNamespace(
        rating=FeedbackRating.INCORRECT, corrected_category="PRODUCT_BUG",
        corrected_root_cause=None, comment=None,
    )

    feedback_service._apply_correction(row, body)

    assert row.failure_category == "PRODUCT_BUG"
    assert row.is_flaky is False
    assert not any("Quarantine" in a for a in row.recommended_actions)
    assert "flaky" not in row.root_cause_summary.lower()
    assert row.confidence_score == 95
    assert row.routing_metadata["confidence_basis"] == "human_corrected"
    assert row.routing_metadata["analysis_mode"] == "human_corrected"


# ── D-85: the column records who set it ──────────────────────────────────


class _Recorder:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, *_a, **_k):
        self.statements.append(statement)


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_an_analysis_replaces_an_earlier_analysis_but_not_a_person():
    db = _Recorder()
    await sync.mirror_ai_category(db, uuid.uuid4(), "PRODUCT_BUG")

    sql = _sql(db.statements[0])
    assert "failure_category_source IS NULL OR test_cases.failure_category_source != 'human'" in sql
    assert "failure_category='PRODUCT_BUG'" in sql
    assert "failure_category_source='analysis'" in sql
    # The old rule: only an empty or UNKNOWN column could be filled.
    assert "test_cases.failure_category IS NULL" not in sql


@pytest.mark.asyncio
async def test_unknown_is_never_mirrored():
    db = _Recorder()
    await sync.mirror_ai_category(db, uuid.uuid4(), "UNKNOWN")
    assert db.statements == []


@pytest.mark.asyncio
async def test_a_person_label_is_marked_human():
    db = _Recorder()
    await sync.apply_human_category(db, uuid.uuid4(), "PRODUCT_BUG")

    sql = _sql(db.statements[0])
    assert "failure_category_source='human'" in sql


def test_the_orm_and_migration_agree():
    column = TestCase.__table__.c.failure_category_source
    assert column.nullable is True
    assert column.type.length == 16

    path = VERSIONS / "0258_test_case_category_source.py"
    spec = importlib.util.spec_from_file_location("m0258", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("0258", "0257")

    op = MagicMock()
    module.op = op
    module.upgrade()
    added = op.add_column.call_args.args
    assert added[0] == "test_cases" and added[1].name == "failure_category_source"
    backfill = str(op.execute.call_args.args[0])
    assert "failure_category_source = 'human'" in backfill
    assert "rating = 'incorrect'" in backfill

    op.reset_mock()
    module.downgrade()
    op.drop_column.assert_called_once_with("test_cases", "failure_category_source")


def test_no_other_migration_claims_revision_0258():
    assert sorted(p.name for p in VERSIONS.glob("0258_*.py")) == ["0258_test_case_category_source.py"]


# ── S-A-5: Re-analyse keeps a correction ─────────────────────────────────


@pytest.mark.asyncio
async def test_re_analysing_a_corrected_test_returns_the_correction(monkeypatch):
    from app.core.deps import AuthorizedTestCaseContext
    from app.models.schemas import AnalyzeRequest
    from app.routers import analyze as route
    from app.services import analysis_corrections, classification_hint_service

    project_id = uuid.uuid4()
    tc = SimpleNamespace(
        id=uuid.uuid4(), test_name="charges the discounted total", suite_name="checkout",
        error_message="AssertionError", stack_trace=None, duration_ms=1, severity="P1",
        test_fingerprint="fp-coupon", class_name=None,
    )
    run = SimpleNamespace(end_time=None, start_time=None, ocp_pod_name=None, ocp_namespace=None)
    authorized = AuthorizedTestCaseContext(
        test_case=tc, test_run=run, run_id=uuid.uuid4(), project_id=project_id,
    )
    monkeypatch.setattr(route, "resolve_authorized_test_case", AsyncMock(return_value=authorized))
    monkeypatch.setattr(classification_hint_service, "load_enabled_hints", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        analysis_corrections, "get_corrections_for_fingerprints",
        AsyncMock(return_value={"fp-coupon": {
            "corrected_category": "PRODUCT_BUG", "corrected_root_cause": None,
            "feedback_id": "fb-1",
        }}),
    )
    engine = AsyncMock(side_effect=AssertionError("the engine must not run"))
    monkeypatch.setattr(route.analysis_router, "classify_test", engine)
    persisted = {}

    class _Stop(Exception):
        pass

    def _capture(analysis):
        # The first step of persisting: what reaches it is what gets stored.
        persisted.update(analysis)
        raise _Stop

    monkeypatch.setattr(route, "validate_confidence", _capture)

    with pytest.raises(_Stop):
        await route.analyze_test_case(
            AnalyzeRequest(test_case_id=tc.id), db=MagicMock(), current_user=SimpleNamespace(),
        )

    engine.assert_not_awaited()
    assert persisted["failure_category"] == "PRODUCT_BUG"
    assert persisted["human_corrected"] is True


@pytest.mark.asyncio
async def test_re_analysing_reads_history_and_the_stored_mode(monkeypatch):
    from app.routers import analyze as route
    from app.services import analysis_corrections, test_history_service

    history = {"pass_count": 43, "fail_count": 9, "consecutive_failures": 9}
    monkeypatch.setattr(
        analysis_corrections, "get_corrections_for_fingerprints", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        test_history_service, "load_test_history", AsyncMock(return_value={"fp": history})
    )
    monkeypatch.setattr(
        route.analysis_router, "resolve_effective_analysis_mode",
        AsyncMock(return_value=("rules", "stored", None)),
    )

    correction, got_history, mode = await route._manual_analysis_context(object(), uuid.uuid4(), "fp")

    assert correction is None
    assert got_history == history
    assert mode == "rules"
