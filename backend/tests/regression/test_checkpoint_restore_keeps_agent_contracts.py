"""Regression guard: a resumed pipeline keeps every stage's accumulated state.

The defect
----------
``_load_checkpoint`` folded each stage's saved checkpoint into the restored
state with a plain ``dict.update``::

    merged_state.update(data)

That is last-writer-wins. But ``WorkflowState`` declares **reducers** for its
accumulating fields -- ``agent_contracts``, ``analyses``, ``deep_findings``,
``stage_metrics`` merge; ``completed_stages``, ``errors``, ``tools_used``
concatenate -- and each node returns only its own contribution. Restoring N
stages therefore kept only the **Nth** stage's contributions and silently threw
away the rest.

Why it was fatal rather than merely lossy
-----------------------------------------
The critic requires a contract for every stage that completed and was not
skipped. Restored stages were marked complete with their contracts discarded,
so ``completed_agent_contracts_present`` failed, terminal verification failed
closed, and no decision report was published -- leaving the run ``partial``.

``_load_checkpoint`` selects the latest **failed or partial** prior run, so the
next attempt restored the same way and failed the same way. **Once a deep
pipeline went partial it could never publish again.** Measured on the homelab
2026-08-23: 5 of 5 re-runs failed with ``missing_contracts`` naming exactly the
two stages the logs showed as ``stage_restored_from_checkpoint``, while 3 runs
with no checkpoint completed and published.

What is guarded
---------------
* every restored stage's contract survives, not just the last one;
* the other accumulating fields survive too -- the contract failure was the
  visible symptom of a wider loss;
* the reducers come from the state schema itself, so a newly-added field is
  covered without editing a second list;
* a field with no reducer still takes the newest value.
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from app.agents.decision_report_critic_agent import _missing_contracts  # noqa: E402
from app.agents.workflow import (  # noqa: E402
    _merge_checkpoint_stage_state,
    _state_field_reducers,
)


def _contract(stage: str) -> dict:
    return {"schema_version": 1, "agent": stage, "status": "ok"}


def _stage_checkpoint(stage: str) -> dict:
    """What one node contributes: its own slice, not the accumulated whole."""
    return {
        "completed_stages": [stage],
        "current_stage": stage,
        "agent_contracts": {stage: _contract(stage)},
        "stage_metrics": {stage: {"latency_ms": 10}},
    }


def _restore(*stages: str) -> dict:
    merged: dict = {}
    for stage in stages:
        _merge_checkpoint_stage_state(merged, _stage_checkpoint(stage))
    return merged


# ── The regression ───────────────────────────────────────────────────────────


def test_every_restored_stage_keeps_its_contract():
    """The exact pair the homelab reported as missing."""
    merged = _restore("summary", "flaky_sentinel")

    assert set(merged["agent_contracts"]) == {"summary", "flaky_sentinel"}, (
        "a plain dict.update keeps only the last stage's contracts"
    )


def test_the_critic_finds_no_missing_contracts_after_a_restore():
    """The end effect: verification no longer fails closed on a resume."""
    merged = _restore("summary", "flaky_sentinel", "test_health")
    merged["skipped_stages"] = []

    assert _missing_contracts(merged) == []


def test_a_restore_without_the_fix_would_fail_the_critic():
    """Pins WHY this matters, modelling what the graph actually produces.

    Each restored node returns ``{"completed_stages": [its own name]}``, so
    that field is rebuilt in full by the graph even when the merge clobbered
    it. ``agent_contracts`` gets no such second chance -- nothing re-emits it
    -- so the critic sees three completed stages and one contract.
    """
    stages = ("summary", "flaky_sentinel", "test_health")
    naive: dict = {}
    for stage in stages:
        naive.update(_stage_checkpoint(stage))          # last-writer-wins
    naive["completed_stages"] = list(stages)            # rebuilt by the graph
    naive["skipped_stages"] = []

    assert _missing_contracts(naive) == ["flaky_sentinel", "summary"], (
        "if this stops failing, the critic no longer detects dropped contracts"
    )


# ── The wider loss, not just contracts ───────────────────────────────────────


def test_completed_stages_accumulate_across_restored_stages():
    merged = _restore("ingestion", "summary", "triage")

    assert merged["completed_stages"] == ["ingestion", "summary", "triage"]


def test_other_accumulating_fields_survive():
    merged = _restore("summary", "flaky_sentinel")

    assert set(merged["stage_metrics"]) == {"summary", "flaky_sentinel"}


def test_analyses_from_earlier_stages_are_not_discarded():
    """root_cause_analysis fans out per test case; losing it loses the run."""
    merged: dict = {}
    _merge_checkpoint_stage_state(merged, {"analyses": {"tc-1": {"category": "FLAKY"}}})
    _merge_checkpoint_stage_state(merged, {"analyses": {"tc-2": {"category": "BUG"}}})

    assert set(merged["analyses"]) == {"tc-1", "tc-2"}


# ── The reducers come from the schema ────────────────────────────────────────


def test_reducers_are_read_from_the_state_schema():
    """A hand-kept second list is the drift this function exists to avoid."""
    reducers = _state_field_reducers()

    for field in ("agent_contracts", "analyses", "completed_stages", "stage_metrics"):
        assert field in reducers, f"{field} declares a reducer but none was found"


def test_a_field_with_no_reducer_takes_the_newest_value():
    merged: dict = {}
    _merge_checkpoint_stage_state(merged, {"test_run_id": "run-a"})
    _merge_checkpoint_stage_state(merged, {"test_run_id": "run-b"})

    assert merged["test_run_id"] == "run-b"


def test_a_last_writer_wins_reducer_still_takes_the_newest_value():
    """current_stage declares _last_str -- resume must land on the newest."""
    merged = _restore("summary", "flaky_sentinel")

    assert merged["current_stage"] == "flaky_sentinel"


def test_a_reducer_mismatch_falls_back_instead_of_losing_the_stage():
    """A type change must not abort the resume."""
    merged: dict = {}
    _merge_checkpoint_stage_state(merged, {"completed_stages": "not-a-list"})
    _merge_checkpoint_stage_state(merged, {"completed_stages": ["summary"]})

    assert merged["completed_stages"] == ["summary"]
