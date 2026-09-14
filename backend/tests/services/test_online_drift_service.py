from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("asyncpg")

from app.services import online_drift_service as drift  # noqa: E402


def test_rate_drift_requires_non_overlapping_confidence_intervals():
    degrading = drift.compare_rates(
        "accuracy",
        current_successes=50,
        current_total=100,
        previous_successes=90,
        previous_total=100,
    )
    noisy = drift.compare_rates(
        "accuracy",
        current_successes=79,
        current_total=100,
        previous_successes=80,
        previous_total=100,
    )

    assert degrading["direction"] == "degrading"
    assert noisy["direction"] == "stable"


def test_insufficient_windows_are_explicitly_unmeasured():
    result = drift.compare_rates(
        "accept_rate",
        current_successes=9,
        current_total=10,
        previous_successes=10,
        previous_total=10,
    )

    assert result["measured"] is False
    assert result["direction"] == "unmeasured"


def test_higher_incident_rate_is_degrading():
    result = drift.compare_rates(
        "incident_after_go_rate",
        current_successes=15,
        current_total=20,
        previous_successes=1,
        previous_total=20,
        lower_is_worse=False,
    )

    assert result["direction"] == "degrading"


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ReviewSession:
    def __init__(self, live=None):
        self.live = live
        self.added = []
        self.flushes = 0

    async def execute(self, _statement):
        return _ScalarResult(self.live)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1


class _PinSession:
    def __init__(self, value):
        self.value = value
        self.sql = ""

    async def scalar(self, statement):
        self.sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        return self.value


@pytest.mark.asyncio
async def test_only_a_pending_capability_review_is_an_active_pin():
    db = _PinSession(uuid.uuid4())

    assert await drift.has_active_drift_pin(
        db, uuid.uuid4(), "agent.root_cause_analysis.v1"
    ) is True
    assert "review_requests.state = 'pending_review'" in db.sql


@pytest.mark.asyncio
async def test_detected_drift_creates_one_pending_capability_review():
    db = _ReviewSession()
    project_id = uuid.uuid4()
    capability_id = "agent.root_cause_analysis.v1"

    row, created = await drift.ensure_drift_review(
        db,
        project_id=project_id,
        capability_id=capability_id,
        report={"drift": True, "degrading_metrics": ["eval_accuracy"]},
    )

    assert created is True
    assert row.kind == "eval_drift"
    assert row.subject_type == "capability"
    assert row.subject_id == f"{project_id}:{capability_id}"
    assert row.capability_id == capability_id
    assert row.state == "pending_review"
    assert len(row.evidence_bundle_sha256) == 64


@pytest.mark.asyncio
async def test_existing_pending_review_keeps_one_live_pin():
    live = SimpleNamespace(state="pending_review")
    db = _ReviewSession(live)

    row, created = await drift.ensure_drift_review(
        db,
        project_id=uuid.uuid4(),
        capability_id="agent.summary.v1",
        report={"drift": True},
    )

    assert row is live
    assert created is False
    assert db.added == []


def test_task_type_bridge_is_explicit_and_complete():
    assert set(drift.TASK_CAPABILITIES) == {
        "classification",
        "root_cause",
        "duplicate_detection",
        "release_decision",
    }
    assert all(value.startswith("agent.") for value in drift.TASK_CAPABILITIES.values())


@pytest.mark.asyncio
async def test_weekly_sweep_opens_a_review_only_for_a_drifting_configured_capability(monkeypatch):
    project_id = uuid.uuid4()
    capability_id = drift.TASK_CAPABILITIES["root_cause"]

    class _Rows:
        def all(self):
            return [(project_id, capability_id)]

    class _DB:
        async def execute(self, _statement):
            return _Rows()

    async def evaluate(_db, **kwargs):
        return {**kwargs, "measured": True, "drift": True}

    created = []

    async def ensure(_db, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(), True

    monkeypatch.setattr(drift, "evaluate_project_capability_drift", evaluate)
    monkeypatch.setattr(drift, "ensure_drift_review", ensure)

    result = await drift.run_weekly_online_drift(_DB())

    assert result["configured_capabilities_evaluated"] == 1
    assert result["drifted"] == 1
    assert result["review_requests_created"] == 1
    assert created[0]["project_id"] == project_id
    assert created[0]["capability_id"] == capability_id
