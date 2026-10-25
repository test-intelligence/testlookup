"""The ai_evaluation history and labelling routes through the app on real PostgreSQL (W16 G-B).

What only SQL shows: the agent-stack history EXCLUDES the project-scoped G2
rows sharing its table (``gate_type IS NULL``; the §0.4 leak) and counts its
``total``; ``GET /tier-comparison/runs`` and ``/reviewer-quality/runs`` see only
the QUERY project, and a non-member is refused before anything is read;
``GET /runs`` filters by ``metric_kind`` / ``status`` and its ``total`` counts the
same set; the evaluate POST's in-flight probe (``candidate ->> 'tier'``) refuses a
second replay of a dataset and tier; the label route writes the label, its
labeller and its activity row in ONE commit, and another project's pair is the
missing-id 404.

Uses ``tests/integration/defect_w06_world.py`` (it awaits
``dispose_engine_for_loop()`` before building its engine and after disposing it,
and closes its Redis client); this module's fixture also closes the app's Redis /
Mongo clients. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0231)
and ``REDIS_URL``. Timestamps are built inside the fixture.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from tests.integration.defect_w06_world import defect_world, env

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _gate_row(*, change_id, evaluated_at, gate_type=None, project_id=None, agent_id=None):
    from app.models.postgres import AIEvalGateRun

    return AIEvalGateRun(
        id=uuid.uuid4(), change_id=change_id, status="pass", manifest_checksum_sha256="c" * 64,
        manifest={"change_id": change_id}, gate_results=[], blocking_gates=[], version_changes=[],
        gate_type=gate_type, project_id=project_id, agent_id=agent_id, evaluated_at=evaluated_at,
    )


def _reviewer_row(project_id, *, evaluated_at):
    from app.models.postgres import AIEvalReviewerQuality

    return AIEvalReviewerQuality(
        id=uuid.uuid4(), project_id=project_id, agent_id="agent.reviewer.v1", source="scheduled", status="pass",
        manifest_checksum_sha256="d" * 64, window_started_at=evaluated_at - timedelta(days=30),
        window_ended_at=evaluated_at, mutation_observations=[{"m": "obsMarkerZq"}], metrics=[], regressions=[],
        semantic_sample_counts={"clean": 4}, clean_sample_count=4, human_outcome_count=1,
        evaluated_at=evaluated_at,
    )


async def _seed(w, db):
    from app.models.postgres import AIEvalDataset, AIEvalRun, AIEvalShadowPair, ProjectMember, UserRole

    now = datetime.now(timezone.utc)
    w.lead_p = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Lead P")
    w.lead_q = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Lead Q")
    db.add(ProjectMember(project_id=w.P, user_id=w.lead_p, role=UserRole.QA_LEAD.value))
    db.add(ProjectMember(project_id=w.Q, user_id=w.lead_q, role=UserRole.QA_LEAD.value))
    w.change = f"w16b-{w.tag}"
    stack = [_gate_row(change_id=w.change, evaluated_at=now - timedelta(minutes=m)) for m in (5, 10)]
    tiers_p = [_gate_row(change_id=w.change, evaluated_at=now - timedelta(minutes=m), gate_type="tier_comparison",
                         project_id=w.P, agent_id="agent.summary.v1") for m in (3, 7)]
    tier_q = _gate_row(change_id=w.change, evaluated_at=now, gate_type="tier_comparison", project_id=w.Q,
                       agent_id="agent.summary.v1")
    w.gate_ids = [row.id for row in (*stack, *tiers_p, tier_q)]
    w.stack_ids = {str(row.id) for row in stack}
    w.tier_p_ids = [str(row.id) for row in tiers_p]
    for row in (*stack, *tiers_p, tier_q):
        db.add(row)
    w.reviewer_p = _reviewer_row(w.P, evaluated_at=now - timedelta(hours=1))
    db.add(w.reviewer_p)
    db.add(_reviewer_row(w.Q, evaluated_at=now))
    w.dataset = AIEvalDataset(id=uuid.uuid4(), name=f"w16b-ds-{w.tag}", task_type="classification",
                              items=[{"input": {"test_name": "tA"}, "expected_output": {"failure_category": "FLAKY"}}],
                              item_count=1)
    db.add(w.dataset)
    await db.flush()
    common = {"dataset_id": w.dataset.id, "task_type": "classification"}
    rules = {"tier": "rules", "model": None, "provider": None, "prompt_version": None, "offline_mode": False,
             "requested": {"tier": "rules", "model": None, "prompt_version": None}}
    db.add(AIEvalRun(model_name="legacy", **common, evaluated_at=now - timedelta(days=1)))
    w.replay_done = uuid.uuid4()
    db.add(AIEvalRun(id=w.replay_done, model_name="rules", metric_kind="replay", status="completed",
                     candidate=rules, project_id=w.P, scored_items=1, **common))
    db.add(AIEvalRun(model_name="rules", metric_kind="replay", status="queued", candidate=rules,
                     project_id=w.P, **common))
    w.case_p = await w.case(db)  # a test case of P's run: the pair's subject
    w.pair_p = AIEvalShadowPair(
        id=uuid.uuid4(), project_id=w.P, agent_id="agent.root_cause_analysis.v1", sample_key=f"root_cause:{w.case_p}",
        incumbent_tier="llm", candidate_tier="slm", incumbent_output={"failure_category": "PRODUCT_BUG"},
        candidate_output={"failure_category": "FLAKY"}, incumbent_tokens=30, candidate_tokens=9, total_tokens=39,
    )
    db.add(w.pair_p)


@pytest.fixture
async def world():
    env("REDIS_URL")
    from app.db.mongo import close_mongo
    from app.db.redis_client import close_redis
    from app.models.postgres import AIEvalDataset, AIEvalGateRun

    try:
        async with defect_world() as w:
            try:
                async with w.sessions.begin() as db:
                    await _seed(w, db)
                w.lead_p_jwt, w.lead_q_jwt = w.jwt(w.lead_p), w.jwt(w.lead_q)
                yield w
            finally:
                # Project-less rows the world's project cascade cannot reach (the dataset
                # cascades to its runs, a run to its items).
                async with w.sessions.begin() as db:
                    await db.execute(delete(AIEvalGateRun).where(AIEvalGateRun.id.in_(getattr(w, "gate_ids", []))))
                    if getattr(w, "dataset", None) is not None:
                        await db.execute(delete(AIEvalDataset).where(AIEvalDataset.id == w.dataset.id))
    finally:
        await close_redis()
        await close_mongo()


async def test_the_agent_stack_history_excludes_g2_rows_and_counts_total(world):
    w = world
    resp = await w.client.get("/api/v1/ai-eval/agent-stack-release-gate/runs",
                              params={"change_id": w.change}, headers=w.lead_q_jwt)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # five rows share the change id; only the two agent-stack rows are listed -- not P's or Q's G2 verdicts
    assert body["total"] == 2
    assert {row["id"] for row in body["items"]} == w.stack_ids
    assert all(row["gate_type"] is None and row["project_id"] is None for row in body["items"])
    page = await w.client.get("/api/v1/ai-eval/agent-stack-release-gate/runs",
                              params={"change_id": w.change, "limit": 1}, headers=w.lead_q_jwt)
    assert len(page.json()["items"]) == 1 and page.json()["total"] == 2  # a top-N length is not a count


async def test_g2_history_is_read_per_project(world):
    w = world
    mine = await w.client.get("/api/v1/ai-eval/tier-comparison/runs",
                              params={"project_id": str(w.P)}, headers=w.lead_p_jwt)
    assert mine.status_code == 200, mine.text
    assert [row["id"] for row in mine.json()["items"]] == w.tier_p_ids  # newest first, P only
    assert mine.json()["total"] == 2
    other_agent = await w.client.get("/api/v1/ai-eval/tier-comparison/runs",
                                     params={"project_id": str(w.P), "agent_id": "agent.root_cause_analysis.v1"},
                                     headers=w.lead_p_jwt)
    assert other_agent.json() == {"items": [], "total": 0}
    foreign = await w.client.get("/api/v1/ai-eval/tier-comparison/runs",
                                 params={"project_id": str(w.P)}, headers=w.lead_q_jwt)
    assert foreign.status_code == 403


async def test_reviewer_history_is_read_per_project_without_its_observations(world):
    w = world
    mine = await w.client.get("/api/v1/ai-eval/reviewer-quality/runs",
                              params={"project_id": str(w.P)}, headers=w.lead_p_jwt)
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["total"] == 1 and [row["id"] for row in body["items"]] == [str(w.reviewer_p.id)]
    assert body["items"][0]["semantic_sample_counts"] == {"clean": 4}
    assert "obsMarkerZq" not in mine.text and "mutation_observations" not in mine.text
    foreign = await w.client.get("/api/v1/ai-eval/reviewer-quality/runs",
                                 params={"project_id": str(w.P)}, headers=w.lead_q_jwt)
    assert foreign.status_code == 403


async def test_runs_filter_by_metric_kind_and_status(world):
    w = world
    base = {"dataset_id": str(w.dataset.id)}
    every = await w.client.get("/api/v1/ai-eval/runs", params=base, headers=w.lead_q_jwt)
    assert every.status_code == 200, every.text
    assert every.json()["total"] == 3
    replay_done = await w.client.get("/api/v1/ai-eval/runs", headers=w.lead_q_jwt,
                                     params={**base, "metric_kind": "replay", "status": "completed"})
    assert replay_done.json()["total"] == 1
    (row,) = replay_done.json()["items"]
    assert (row["id"], row["metric_label"]) == (str(w.replay_done), "Replay of the candidate")
    legacy = await w.client.get("/api/v1/ai-eval/runs", headers=w.lead_q_jwt,
                                params={**base, "metric_kind": "label_agreement"})
    (old,) = legacy.json()["items"]
    assert (old["status"], old["metric_label"]) == ("completed", "Legacy label-agreement metric")


async def test_a_second_replay_of_the_same_dataset_and_tier_is_refused(world):
    """The in-flight probe ``candidate ->> 'tier'`` on real JSONB: the QUEUED rules run blocks a second one
    (the completed one does not); refused before anything is written or dispatched."""
    from sqlalchemy import func

    from app.models.postgres import AIEvalRun

    w = world
    resp = await w.client.post(f"/api/v1/ai-eval/runs/evaluate/{w.dataset.id}", headers=w.admin,
                               json={"candidate": {"tier": "rules"}, "project_id": str(w.P)})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == {
        "code": "candidate_run_in_progress", "message": "A rules evaluation of this dataset is already running."}
    async with w.sessions() as db:
        runs = await db.scalar(select(func.count()).select_from(AIEvalRun).where(AIEvalRun.dataset_id == w.dataset.id))
    assert runs == 3


async def test_the_label_write_and_its_activity_row_commit_together(world):
    from app.models.postgres import AIEvalShadowPair, ProjectActivityEvent

    w = world
    path = f"/api/v1/ai-eval/shadow-pairs/{w.pair_p.id}/label"
    body = {"verdict": "incumbent", "failure_category": "PRODUCT_BUG"}
    foreign = await w.client.post(path, json=body, headers=w.lead_q_jwt)
    missing = await w.client.post(f"/api/v1/ai-eval/shadow-pairs/{uuid.uuid4()}/label", json=body,
                                  headers=w.lead_q_jwt)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {
        "detail": {"code": "shadow_pair_not_found", "message": "Shadow pair not found."}}

    labelled = await w.client.post(path, json=body, headers=w.lead_p_jwt)
    assert labelled.status_code == 200, labelled.text
    assert (labelled.json()["label_status"], labelled.json()["labelled_by_name"]) == ("human_labelled", "Lead P")
    source = labelled.json()["source"]
    assert (source["kind"], source["id"], source["href"]) == ("test_case", str(w.case_p), f"/runs/{w.run_r}?tab=tests")
    again = await w.client.post(path, json=body, headers=w.lead_p_jwt)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "shadow_pair_already_labelled"

    async with w.sessions() as db:
        pair = (await db.execute(select(AIEvalShadowPair).where(AIEvalShadowPair.id == w.pair_p.id))).scalar_one()
        events = (await db.execute(select(ProjectActivityEvent).where(
            ProjectActivityEvent.project_id == w.P,
            ProjectActivityEvent.event_type == "ai_eval.shadow_pair_labelled",
        ))).scalars().all()
    assert (pair.label_status, pair.labelled_by) == ("human_labelled", w.lead_p)
    assert pair.label == {"verdict": "incumbent", "failure_category": "PRODUCT_BUG", "source": "human"}
    assert len(events) == 1  # the refused second label wrote nothing
    assert events[0].actor_id == w.lead_p
    assert events[0].summary == "A Root-cause analysis shadow pair was labelled: incumbent"

    listed = await w.client.get("/api/v1/ai-eval/shadow-pairs", headers=w.lead_p_jwt,
                                params={"project_id": str(w.P), "label_status": "all"})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1 and listed.json()["pending_total"] == 0
