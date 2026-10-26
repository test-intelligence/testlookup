"""W16: the AI-evaluation vocabularies are ONE module, and agree with the schema.

``services/ai_eval_vocab.py`` holds every enum-like value the wave adds (plain
``str`` on the wire). Pinned here: each vocabulary is non-empty and unique; the
run / item / metric-kind / label-status sets equal the CHECK constraints of the
ORM (and so of migration 0231 — ``test_migration_0231.py`` pins ORM ⇄
migration); ``TASK_TIERS`` only offers known tiers for known task types;
``current_prompt_tag()`` is the registry tags of the two prompts the LLM
classifier stamps, in sorted id order. Then the grown ``AIEvalRunResponse``:
a legacy row, a fake without the W16 attributes and an UNFLUSHED ORM row all
validate (a new field on a validated model breaks stored rows unless it has a
default), and the validator fills ``metric_label`` / ``error_text`` /
``measured``.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.services import ai_eval_vocab as v


def _check_values(model, name: str) -> tuple[str, ...]:
    (check,) = [
        c for c in model.__table__.constraints if isinstance(c, sa.CheckConstraint) and c.name == name
    ]
    return tuple(re.findall(r"'([^']+)'", str(check.sqltext)))


TUPLES = [
    "METRIC_KINDS", "RUN_STATUSES", "TERMINAL_RUN_STATUSES", "ITEM_STATUSES", "CANDIDATE_TIERS",
    "TASK_TYPES", "SHADOW_VERDICTS", "SHADOW_LABEL_STATUSES",
]
MAPPINGS = [
    "METRIC_LABELS", "TASK_TIERS", "SHADOW_AGENT_LABELS", "SHADOW_LABEL_FIELDS", "ITEM_ERROR_SENTENCES",
    "RUN_ERROR_SENTENCES", "TIER_UNAVAILABLE",
]


@pytest.mark.parametrize("name", TUPLES)
def test_every_vocabulary_is_a_non_empty_tuple_of_unique_strings(name):
    values = getattr(v, name)
    assert isinstance(values, tuple) and values, name
    assert len(set(values)) == len(values), name
    assert all(isinstance(x, str) and x == x.strip() and x for x in values), name


@pytest.mark.parametrize("name", MAPPINGS)
def test_every_mapping_is_non_empty_with_string_keys(name):
    mapping = getattr(v, name)
    assert isinstance(mapping, dict) and mapping, name
    assert all(isinstance(k, str) and k for k in mapping), name


def test_the_sets_equal_the_orm_checks():
    from app.models.postgres import AIEvalBaseline, AIEvalItemOutput, AIEvalRun, AIEvalShadowPair

    assert _check_values(AIEvalRun, "ck_aer_metric_kind") == v.METRIC_KINDS
    assert _check_values(AIEvalBaseline, "ck_aeb_metric_kind") == v.METRIC_KINDS
    assert _check_values(AIEvalRun, "ck_aer_status") == v.RUN_STATUSES
    assert _check_values(AIEvalItemOutput, "ck_aeio_status") == v.ITEM_STATUSES
    assert _check_values(AIEvalShadowPair, "ck_aesp_label_status") == v.SHADOW_LABEL_STATUSES


def test_the_labels_and_the_subsets():
    assert tuple(v.METRIC_LABELS) == v.METRIC_KINDS
    assert v.METRIC_LABELS["label_agreement"] == "Legacy label-agreement metric"
    assert set(v.TERMINAL_RUN_STATUSES) < set(v.RUN_STATUSES)
    assert {"queued", "running"} == set(v.RUN_STATUSES) - set(v.TERMINAL_RUN_STATUSES)
    assert tuple(v.TASK_TIERS) == v.TASK_TYPES
    for task, tiers in v.TASK_TIERS.items():
        assert tiers and set(tiers) <= set(v.CANDIDATE_TIERS), task
    # Duplicate detection and release decision replay the deterministic engine only (§0.8 / D-7).
    assert v.TASK_TIERS["duplicate_detection"] == v.TASK_TIERS["release_decision"] == ("rules",)
    assert set(v.SHADOW_LABEL_FIELDS) == set(v.SHADOW_AGENT_LABELS)
    assert v.SHADOW_LABEL_FIELDS["agent.root_cause_analysis.v1"] == ("failure_category",)
    assert v.SHADOW_LABEL_FIELDS["agent.summary.v1"] == ()
    assert set(v.ITEM_ERROR_SENTENCES) >= {"tier_fallback", "item_timeout", "budget_exhausted", "worker_lost"}
    assert set(v.RUN_ERROR_SENTENCES) >= {"worker_lost", "budget_exhausted"}


def test_the_task_types_are_the_dataset_create_pattern():
    from app.models.schemas import AIEvalDatasetCreate

    pattern = AIEvalDatasetCreate.model_fields["task_type"].metadata[0].pattern
    assert set(re.findall(r"[a-z_]+", pattern)) == set(v.TASK_TYPES)


def test_the_numbers():
    assert v.MAX_CANDIDATE_ITEMS == 200
    assert v.RUN_HEARTBEAT_STALE_SECONDS == 900
    assert v.ITEM_TIMEOUT_FLOOR_SECONDS == 30 < 600 < v.RUN_HEARTBEAT_STALE_SECONDS
    assert v.SHADOW_PAIR_RETENTION_DAYS == 30
    assert (v.SHADOW_SWEEP_BATCH, v.GOLDEN_MATCH_BATCH) == (1000, 500)


def test_the_current_prompt_tag_is_the_two_classifier_prompts_in_id_order():
    from app.services.prompt_registry import prompt_versions_used

    tag = v.current_prompt_tag()
    assert re.fullmatch(
        r"fast_classifier_system@v\d+:[0-9a-f]{12}\+react_triage@v\d+:[0-9a-f]{12}", tag
    ), tag
    versions = prompt_versions_used("react_triage", "fast_classifier_system")
    assert tag == f"fast_classifier_system@{versions['fast_classifier_system']}+react_triage@{versions['react_triage']}"
    assert len(tag) <= 400, "fits EvalCandidateIn.prompt_version"
    assert v.current_prompt_tag() == tag, "deterministic"


# ── The grown AIEvalRunResponse ──────────────────────────────────────────────


def _legacy_fake() -> SimpleNamespace:
    """A pre-W16 row as a fake: none of the W16 attributes exist on it."""
    return SimpleNamespace(
        id=uuid.uuid4(), dataset_id=uuid.uuid4(), model_name="AnalysisAgent", model_version_id=None,
        task_type="classification", precision=0.8, recall=0.7, f1_score=0.75, accuracy=0.8,
        agreement_rate=None, total_items=10, correct_items=8, fallback_used=False,
        evaluated_at=datetime(2026, 10, 1, tzinfo=timezone.utc), duration_ms=12,
    )


def test_a_legacy_fake_without_the_new_attributes_validates_at_the_defaults():
    from app.models.schemas import AIEvalRunResponse

    body = AIEvalRunResponse.model_validate(_legacy_fake()).model_dump()
    assert body["metric_kind"] == "label_agreement"
    assert body["metric_label"] == "Legacy label-agreement metric"
    assert body["status"] == "completed"
    assert (body["scored_items"], body["unscored_items"], body["error_items"]) == (0, 0, 0)
    assert body["items_capped"] is False and body["measured"] is True
    assert body["candidate"] is None and body["metrics"] is None and body["error_text"] is None


def test_an_unflushed_orm_row_whose_server_defaults_read_none_validates():
    from app.models.postgres import AIEvalRun
    from app.models.schemas import AIEvalRunResponse

    # The pre-W16 columns as every writer sets them; the W16 columns left to their defaults.
    row = AIEvalRun(
        id=uuid.uuid4(), dataset_id=uuid.uuid4(), model_name="rules", task_type="classification",
        evaluated_at=datetime(2026, 10, 25, tzinfo=timezone.utc), total_items=12, correct_items=0,
        fallback_used=False,
    )
    assert row.metric_kind is None and row.status is None and row.scored_items is None  # before the flush
    body = AIEvalRunResponse.model_validate(row)
    assert (body.metric_kind, body.status, body.scored_items, body.items_capped) == (
        "label_agreement", "completed", 0, False,
    )
    assert body.metric_label == "Legacy label-agreement metric"


def test_a_replay_row_fills_its_label_error_sentence_and_measured():
    from app.models.schemas import AIEvalRunResponse

    project = uuid.uuid4()
    fake = SimpleNamespace(
        **vars(_legacy_fake()), metric_kind="replay", status="partial", error_code="worker_lost",
        candidate={"tier": "rules", "offline_mode": True, "requested": {"tier": "rules"}},
        project_id=project, requested_by=None, started_at=None, completed_at=None, heartbeat_at=None,
        scored_items=0, unscored_items=1, error_items=2, items_capped=True,
        metrics=[{"name": "accuracy", "value": None, "n": 0, "measured": False, "kind": "score"}],
    )
    body = AIEvalRunResponse.model_validate(fake)
    assert body.metric_label == "Replay of the candidate"
    assert body.error_text == v.RUN_ERROR_SENTENCES["worker_lost"]
    assert body.measured is False, "a replay that scored nothing measured nothing (never 0 %)"
    assert body.candidate is not None and body.candidate.tier == "rules" and body.candidate.requested.tier == "rules"
    assert body.metrics is not None and body.metrics[0].value is None
    assert body.project_id == project and body.items_capped is True
    scored = AIEvalRunResponse.model_validate(SimpleNamespace(**{**vars(fake), "scored_items": 3}))
    assert scored.measured is True


def test_the_request_models_forbid_extras_and_bound_their_strings():
    from pydantic import ValidationError

    from app.models.schemas import CandidateEvaluationRequest, EvalCandidateIn, ShadowPairLabelRequest

    body = CandidateEvaluationRequest.model_validate({"candidate": {"tier": "rules"}, "project_id": str(uuid.uuid4())})
    assert body.limit is None and body.candidate.model is None
    for bad in (
        {"candidate": {"tier": "rules", "endpoint": "http://example.invalid"}, "project_id": str(uuid.uuid4())},
        {"candidate": {"tier": "rules"}, "project_id": str(uuid.uuid4()), "limit": 201},
        {"candidate": {"tier": "rules"}},
    ):
        with pytest.raises(ValidationError):
            CandidateEvaluationRequest.model_validate(bad)
    with pytest.raises(ValidationError):
        EvalCandidateIn.model_validate({"tier": "x"})
    with pytest.raises(ValidationError):
        ShadowPairLabelRequest.model_validate({"verdict": "both", "label_status": "golden_match"})
    assert ShadowPairLabelRequest.model_validate({"verdict": "anything"}).verdict == "anything", (
        "a plain str: the router judges the vocabulary with one sentence"
    )


def test_the_retention_preview_grows_the_shadow_pair_class_with_a_default():
    from app.models.schemas import RetentionPreviewCandidates

    field = RetentionPreviewCandidates.model_fields["shadow_pairs_unlabelled"]
    assert field.annotation is int and field.default == 0
