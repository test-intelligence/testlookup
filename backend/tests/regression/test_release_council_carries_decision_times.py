"""HON-06: the release decision carries the stored row's own times.

The gate's "How this was decided" timeline stamped every stage at
``now - n * 45 s``: invented times rendered as real dates. The fix dates the
decision from the ``ReleaseDecision`` row, so the response must carry the
row's ``created_at`` / ``updated_at``, and must carry ``None`` for a
synthesised quick-look view (no decision was stored, so there is no time).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import ReleaseCouncilResponse

RUN = uuid.uuid4()
CREATED = datetime(2026, 10, 1, 9, 30, 12, tzinfo=timezone.utc)
UPDATED = datetime(2026, 10, 2, 14, 5, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v

    def scalar_one(self):
        return self._v

    def scalars(self):
        return SimpleNamespace(all=lambda: self._v)

    def first(self):
        return self._v


def _decision():
    # ``human_override`` set: the band floor is skipped (a human decided), so
    # the session sees exactly three reads: decision, run, clusters.
    return SimpleNamespace(
        test_run_id=RUN,
        recommendation="NO_GO",
        risk_score=64,
        composite_risk=None,
        dimension_scores=None,
        blocking_issues=["payments suite failing"],
        conditions_for_go=[],
        reasoning="stored reasoning",
        score_model_version=2,
        input_snapshot=None,
        human_override="perf soak pending",
        overridden_by=None,
        original_recommendation="GO",
        original_risk_score=40,
        override_audit=[],
        policy_id=None,
        policy_evaluation=None,
        created_at=CREATED,
        updated_at=UPDATED,
    )


@pytest.mark.asyncio
async def test_a_stored_decision_carries_its_row_times():
    from app.services.release_council_service import get_release_council

    run = SimpleNamespace(id=RUN, project_id=None, pass_rate=91.0, build_number="b-7")
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Result(_decision()), _Result(run), _Result([])])
    )

    council = await get_release_council(RUN, db)

    assert council is not None
    assert council.synthesized is False
    assert council.created_at == CREATED
    assert council.updated_at == UPDATED


@pytest.mark.asyncio
async def test_a_synthesised_view_has_no_decision_time():
    from app.services.release_council_service import get_release_council

    run = SimpleNamespace(id=RUN, project_id=uuid.uuid4(), pass_rate=100.0, build_number="b-1")
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _Result(None),   # no ReleaseDecision row
            _Result(run),
            _Result(0),      # open defects
            _Result(None),   # no project policy
            _Result(None),   # no system policy
        ])
    )

    council = await get_release_council(RUN, db)

    assert council is not None
    assert council.synthesized is True
    assert council.created_at is None
    assert council.updated_at is None


@pytest.mark.asyncio
async def test_the_release_readiness_route_returns_the_decision_time(monkeypatch):
    """Through the route: the review-gate projection must keep the times, and
    the JSON the page reads must carry them as ISO strings."""
    from app.routers import release_readiness
    from app.services import report_distribution_policy as policy
    from app.services.review_envelope import not_ai_generated

    council = ReleaseCouncilResponse(
        run_id=str(RUN),
        recommendation="GO",
        risk_score=20,
        created_at=CREATED,
        updated_at=UPDATED,
    )
    monkeypatch.setattr(release_readiness, "get_release_council", AsyncMock(return_value=council))
    monkeypatch.setattr(
        policy,
        "release_review_projection",
        AsyncMock(return_value=(not_ai_generated(), False, False)),
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result((None, uuid.uuid4()))),
        commit=AsyncMock(),
    )

    out = await release_readiness.get_release_decision(
        RUN,
        allow_advisory=False,
        current_user=SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER"),
        db=db,
    )

    body = out.model_dump(mode="json")
    assert datetime.fromisoformat(body["created_at"]) == CREATED
    assert datetime.fromisoformat(body["updated_at"]) == UPDATED


def test_the_response_model_declares_the_times():
    fields = ReleaseCouncilResponse.model_fields
    assert "created_at" in fields and "updated_at" in fields
    # A view built without a stored row says "no time", not "now".
    bare = ReleaseCouncilResponse(run_id=str(RUN), recommendation="GO", risk_score=0)
    assert bare.created_at is None and bare.updated_at is None
