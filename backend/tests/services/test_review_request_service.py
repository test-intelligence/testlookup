"""E8.1: one live review request per report-producing run.

The fake session below enforces the partial unique index
``uq_review_requests_live_subject`` at every flush, so a change that inserts the
replacement before superseding the old row fails here rather than only in
Postgres. The real index is exercised in
``tests/integration/test_review_requests_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import review_request_service as svc

PROJECT = uuid.uuid4()
TEST_RUN = uuid.uuid4()
HASH_A = "a" * 64
HASH_B = "b" * 64


class _Session:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.flushes = 0

    async def execute(self, stmt):
        p = stmt.compile().params
        if "test_run_id_1" in p:  # the "older pending in this scope" query
            found = [
                r for r in self.rows
                if r.project_id == p["project_id_1"] and r.test_run_id == p["test_run_id_1"]
                and r.workflow_type == p["workflow_type_1"] and r.state == "pending_review"
                and r.subject_id != p["subject_id_1"]
            ]
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: found))
        live = [
            r for r in self.rows
            if r.kind == "report" and r.subject_type == "pipeline_run"
            and r.subject_id == p["subject_id_1"] and r.state != "superseded"
        ]
        return SimpleNamespace(scalar_one_or_none=lambda: live[0] if live else None)

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        self.flushes += 1
        live: dict = {}
        for r in self.rows:
            if r.state != "superseded":
                key = (r.kind, r.subject_type, r.subject_id)
                assert key not in live, f"two live review requests for {key}"
                live[key] = r


def _run(workflow="offline", test_run=TEST_RUN):
    return SimpleNamespace(id=uuid.uuid4(), test_run_id=test_run, workflow_type=workflow)


def _live(session, run):
    return [r for r in session.rows if r.subject_id == str(run.id) and r.state != "superseded"]


async def _create(session, run, *, stages=("summary",), evidence=HASH_A):
    return await svc.create_run_review_request(
        session, run=run, project_id=PROJECT, report_stage_names=list(stages),
        evidence_bundle_sha256=evidence,
    )


# ── creation ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_report_run_gets_one_pending_request():
    from app.core.metrics import review_requests_total

    session, run = _Session(), _run()
    metric = review_requests_total.labels(state="pending_review")
    before = metric._value.get()

    request = await _create(session, run)

    assert metric._value.get() == before + 1
    assert request.state == "pending_review"
    assert request.kind == "report" and request.subject_type == "pipeline_run"
    assert request.subject_id == str(run.id)
    assert request.pipeline_run_id == run.id
    assert request.test_run_id == TEST_RUN and request.workflow_type == "offline"
    assert request.evidence_bundle_sha256 == HASH_A
    assert request.ai_disclaimer_version == svc.AI_DISCLAIMER_VERSION
    assert request.created_by == "system"
    assert request.requested_by is None, (
        "pipeline runs do not record who triggered them yet; a guessed requester "
        "would make separation of duties (section 8.3) enforce against the wrong person"
    )


@pytest.mark.asyncio
async def test_a_run_with_no_report_stage_gets_none():
    session = _Session()
    assert await _create(session, _run(), stages=()) is None
    assert session.rows == []


@pytest.mark.asyncio
async def test_an_unknown_project_gets_none_rather_than_a_guess():
    session = _Session()
    result = await svc.create_run_review_request(
        session, run=_run(), project_id=None, report_stage_names=["summary"],
    )
    assert result is None and session.rows == []


# ── re-finalizing the same run ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refinalizing_the_same_run_keeps_one_request_and_refreshes_its_payload():
    session, run = _Session(), _run()
    first = await _create(session, run, evidence=HASH_A)

    second = await _create(session, run, evidence=HASH_B)

    assert second is first
    assert len(session.rows) == 1
    assert first.evidence_bundle_sha256 == HASH_B


@pytest.mark.asyncio
async def test_a_settled_review_of_the_same_payload_is_left_alone():
    session, run = _Session(), _run()
    first = await _create(session, run, evidence=HASH_A)
    first.state = "accepted"

    again = await _create(session, run, evidence=HASH_A)

    assert again is first
    assert first.state == "accepted"
    assert len(session.rows) == 1


@pytest.mark.asyncio
async def test_a_changed_report_supersedes_its_settled_review():
    """Section 8.3: a later change to the payload invalidates the acceptance."""
    session, run = _Session(), _run()
    first = await _create(session, run, evidence=HASH_A)
    first.state = "accepted"
    from app.core.metrics import review_requests_total

    metric = review_requests_total.labels(state="superseded")
    before = metric._value.get()

    replacement = await _create(session, run, evidence=HASH_B)

    assert metric._value.get() == before + 1
    assert replacement is not first
    assert first.state == "superseded"
    assert first.superseded_by == replacement.id
    assert replacement.state == "pending_review"
    assert [r.id for r in _live(session, run)] == [replacement.id]


# ── a newer run over the same subject ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_newer_run_supersedes_the_older_pending_review():
    from app.core.metrics import review_requests_total

    session = _Session()
    older_run, newer_run = _run(), _run()
    older = await _create(session, older_run)
    metric = review_requests_total.labels(state="superseded")
    before = metric._value.get()

    newer = await _create(session, newer_run)

    assert metric._value.get() == before + 1
    assert older.state == "superseded"
    assert older.superseded_by == newer.id
    assert newer.state == "pending_review"


@pytest.mark.asyncio
async def test_a_newer_run_never_overwrites_an_accepted_review():
    session = _Session()
    older_run = _run()
    older = await _create(session, older_run)
    older.state = "accepted"

    await _create(session, _run())

    assert older.state == "accepted", "an accepted review is history, not a draft to replace"


@pytest.mark.asyncio
@pytest.mark.parametrize("other", [{"workflow": "deep"}, {"test_run": uuid.uuid4()}])
async def test_a_run_over_a_different_subject_supersedes_nothing(other):
    session = _Session()
    older = await _create(session, _run())

    await _create(session, _run(**other))

    assert older.state == "pending_review"


# ── helpers Finalize relies on ───────────────────────────────────────────────


def test_report_stages_counts_only_completed_report_producers():
    rows = [
        SimpleNamespace(stage_name="ingestion", status="completed"),
        SimpleNamespace(stage_name="summary", status="completed"),
        SimpleNamespace(stage_name="root_cause_analysis", status="skipped"),
        SimpleNamespace(stage_name="decision_report", status="failed"),
    ]
    assert svc.report_stages(rows) == ["summary"]


@pytest.mark.parametrize(
    "final_state, expected",
    [
        ({"decision_intelligence": {"evidence_bundle_sha256": HASH_A}}, HASH_A),
        ({"decision_intelligence": {"evidence_bundle_sha256": "not-a-hash"}}, None),
        ({"decision_intelligence": None}, None),
        (None, None),
    ],
)
def test_evidence_hash_is_taken_only_when_valid(final_state, expected):
    assert svc.evidence_hash_from(final_state) == expected


@pytest.mark.asyncio
async def test_the_savepoint_wrapper_is_used_when_the_session_has_one():
    entered: list[str] = []

    class _Nested:
        async def __aenter__(self):
            entered.append("in")

        async def __aexit__(self, *exc):
            entered.append("out")
            return False

    session = _Session()
    session.begin_nested = lambda: _Nested()

    request = await svc.stage_run_review_request(
        session, run=_run(), project_id=PROJECT, report_stage_names=["summary"],
    )

    assert request is not None
    assert entered == ["in", "out"]
