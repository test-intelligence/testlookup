"""E7.4: a manual retry resumes only under unchanged config, else it reruns.

The defect these pin is silent: a retry that replays a frozen plan after the
operator narrowed a tool allowlist runs the OLD allowlist, and nothing in the
API or the UI says so.
"""
from __future__ import annotations

from app.services.pipeline_retry_config import (
    FINGERPRINT_FIELDS,
    config_fingerprint,
    decide_retry_mode,
)


def _snapshot(**kw):
    base = {
        "requested": "auto",
        "resolved": "llm",
        "resolution_reason": "llm_reachable",
        "provider": "ollama",
        "model": "llama3",
        "offline": True,
        "resolved_at": "2026-09-12T10:00:00+00:00",
    }
    base.update(kw)
    return base


def _metadata(snapshot=None, *, with_plan=True):
    meta = {"analysis_mode_resolution": snapshot if snapshot is not None else _snapshot()}
    if with_plan:
        meta["initial_workflow_plan"] = {"stages": ["ingestion", "summary"]}
    return meta


# ── fingerprint ───────────────────────────────────────────────────────────────


def test_the_same_config_fingerprints_the_same():
    assert config_fingerprint(_snapshot()) == config_fingerprint(_snapshot())


def test_resolved_at_is_excluded_or_every_retry_would_rerun():
    """A timestamp differs on every resolve. If it were in the hash, no retry
    could ever resume -- each one would pay for a whole new pipeline."""
    a = config_fingerprint(_snapshot(resolved_at="2026-01-01T00:00:00+00:00"))
    b = config_fingerprint(_snapshot(resolved_at="2026-12-31T23:59:59+00:00"))
    assert a == b


def test_provenance_fields_are_excluded():
    """``requested=auto`` resolving to ``llm`` runs identically to
    ``requested=llm`` resolving to ``llm``."""
    assert config_fingerprint(_snapshot(requested="llm")) == config_fingerprint(
        _snapshot(requested="auto")
    )
    assert config_fingerprint(_snapshot(resolution_reason="other")) == config_fingerprint(
        _snapshot()
    )


def test_every_decision_field_changes_the_fingerprint():
    """Each field in FINGERPRINT_FIELDS must actually move the hash -- a field
    that is listed but ignored is the failure mode this whole module exists to
    prevent."""
    base = config_fingerprint(_snapshot())
    for field in FINGERPRINT_FIELDS:
        changed = config_fingerprint(_snapshot(**{field: "something-else"}))
        assert changed != base, f"{field} is in FINGERPRINT_FIELDS but does not affect the hash"


def test_a_missing_snapshot_is_comparable_not_an_error():
    assert isinstance(config_fingerprint(None), str)
    assert config_fingerprint(None) != config_fingerprint(_snapshot())


# ── decision ──────────────────────────────────────────────────────────────────


def test_unchanged_config_resumes():
    plan = decide_retry_mode(_metadata(), _snapshot())
    assert plan.mode == "resume"
    assert plan.reason == "config_unchanged"
    assert plan.is_rerun is False


def test_a_narrowed_model_reruns_rather_than_replaying_the_old_one():
    plan = decide_retry_mode(_metadata(), _snapshot(model="phi3"))
    assert plan.mode == "rerun"
    assert plan.reason == "config_changed"
    assert plan.frozen_fingerprint != plan.current_fingerprint


def test_an_offline_flip_reruns():
    plan = decide_retry_mode(_metadata(_snapshot(offline=True)), _snapshot(offline=False))
    assert plan.is_rerun


def test_a_mode_change_reruns():
    plan = decide_retry_mode(_metadata(), _snapshot(resolved="rules"))
    assert plan.is_rerun


def test_a_row_with_no_frozen_plan_reruns():
    """_claim_pipeline_resume refuses a row with no initial_workflow_plan, so
    resuming here would make the operator's retry silently do nothing."""
    plan = decide_retry_mode(_metadata(with_plan=False), _snapshot())
    assert plan.mode == "rerun"
    assert plan.reason == "no_frozen_plan"


def test_a_plan_without_stages_is_not_a_plan():
    meta = {"analysis_mode_resolution": _snapshot(), "initial_workflow_plan": {"stages": "nope"}}
    assert decide_retry_mode(meta, _snapshot()).reason == "no_frozen_plan"


def test_empty_metadata_reruns():
    assert decide_retry_mode(None, _snapshot()).is_rerun


def test_as_dict_reports_both_fingerprints():
    payload = decide_retry_mode(_metadata(), _snapshot(model="phi3")).as_dict()
    assert payload["mode"] == "rerun"
    assert payload["frozen_config"] != payload["current_config"]
