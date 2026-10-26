"""The AI-evaluation vocabularies (W16 AGT-09 / AGT-13), in ONE place.

Every enum-like field the wave adds is a plain ``str`` on the wire; the values
it may take live here as module constants, and a request field that takes one
is judged in the router with one fixed sentence. The frontend mirrors the
values it renders in ``frontend/src/types/aiEval.ts`` (pinned by
``tests/test_ai_eval_vocab_frontend_parity.py``).

Pure and commit-free (``STAGE_ONLY_SERVICES``): no session, no I/O at import.
``current_prompt_tag()`` reads the prompt registry lazily, so ``schemas.py``
can import the labels without pulling the registry in.
"""
from __future__ import annotations

from typing import Final

# ── AGT-09 (b): runs, items, candidates ──────────────────────────────────────

#: ``label_agreement`` = the legacy metric (the dataset's own labels, never
#: recomputed); ``replay`` = the candidate replayed through the real engine.
METRIC_KINDS: Final[tuple[str, ...]] = ("label_agreement", "replay")
METRIC_LABELS: Final[dict[str, str]] = {
    "label_agreement": "Legacy label-agreement metric",
    "replay": "Replay of the candidate",
}

#: Legacy rows read ``completed`` (the column's server default).
RUN_STATUSES: Final[tuple[str, ...]] = ("queued", "running", "completed", "partial", "failed")
TERMINAL_RUN_STATUSES: Final[tuple[str, ...]] = ("completed", "partial", "failed")
ITEM_STATUSES: Final[tuple[str, ...]] = ("correct", "incorrect", "unscored", "error")

CANDIDATE_TIERS: Final[tuple[str, ...]] = ("rules", "ml", "llm")
TASK_TYPES: Final[tuple[str, ...]] = (
    "classification",
    "root_cause",
    "duplicate_detection",
    "release_decision",
)
#: Duplicate detection replays the pure title ratio and release decision the
#: policy engine: both are deterministic, so only ``rules`` is offered.
TASK_TIERS: Final[dict[str, tuple[str, ...]]] = {
    "classification": ("rules", "ml", "llm"),
    "root_cause": ("rules", "ml", "llm"),
    "duplicate_detection": ("rules",),
    "release_decision": ("rules",),
}

MAX_CANDIDATE_ITEMS: Final[int] = 200
#: Per item: max(floor, effective ai timeout_seconds + 15), capped at 600.
ITEM_TIMEOUT_FLOOR_SECONDS: Final[int] = 30
#: A running run silent this long → partial / worker_lost (the reconcile sweep).
RUN_HEARTBEAT_STALE_SECONDS: Final[int] = 900

# ── AGT-13: shadow pairs ─────────────────────────────────────────────────────

SHADOW_VERDICTS: Final[tuple[str, ...]] = ("incumbent", "candidate", "both", "neither")
#: Equal to the CHECK ``ck_aesp_label_status`` of ``ai_eval_shadow_pairs``.
SHADOW_LABEL_STATUSES: Final[tuple[str, ...]] = ("pending", "human_labelled", "golden_match")
SHADOW_AGENT_LABELS: Final[dict[str, str]] = {
    "agent.root_cause_analysis.v1": "Root-cause analysis",
    "agent.summary.v1": "Run summary",
}
#: The label fields a person may set per agent (the capability's label schema).
SHADOW_LABEL_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "agent.root_cause_analysis.v1": ("failure_category",),
    "agent.summary.v1": (),
}
SHADOW_PAIR_RETENTION_DAYS: Final[int] = 30
#: Delete batch of the nightly sweep (≤ 100 batches a night).
SHADOW_SWEEP_BATCH: Final[int] = 1000
GOLDEN_MATCH_BATCH: Final[int] = 500

#: The prompts the LLM classifier path depends on (``analysis_router``'s
#: ``prompt_versions_used("react_triage", "fast_classifier_system")``).
_REPLAY_PROMPT_IDS: Final[tuple[str, ...]] = ("react_triage", "fast_classifier_system")


def current_prompt_tag() -> str:
    """``"+".join(f"{id}@{tag}")`` over the replayed prompts, in sorted id order.

    The ``prompt_version`` a candidate may assert, and the key of the recorded
    CI fixtures: a prompt edit changes it.
    """
    from app.services.prompt_registry import prompt_versions_used  # noqa: PLC0415

    versions = prompt_versions_used(*_REPLAY_PROMPT_IDS)
    return "+".join(f"{pid}@{versions[pid]}" for pid in sorted(versions))


# ── Sentences (rendered as text; one place so tests import one string) ──────

ITEM_ERROR_SENTENCES: Final[dict[str, str]] = {
    "tier_fallback": "The {tier} engine was unavailable; {used} answered instead, so the item is not scored.",
    "item_timeout": "The engine did not answer within {seconds} s.",
    "engine_error": "The engine raised an error ({error_type}).",
    "budget_exhausted": "The project's LLM call budget for this run was used up before this item.",
    "input_missing": "The item has no input this task type can replay.",
    "worker_lost": "The evaluation stopped before this item ran.",
}
RUN_ERROR_SENTENCES: Final[dict[str, str]] = {
    "worker_lost": "The evaluation stopped part-way (no progress for 15 minutes); the scored items are kept.",
    "dataset_missing": "The dataset was deleted before the evaluation ran.",
    "budget_exhausted": "The project's LLM call budget ran out; the remaining items were not replayed.",
    "project_missing": "The project was deleted before the evaluation ran.",
    # Written by the runner (G-C): a run-level exception, and a candidate whose
    # effective AI config no longer resolves to what was queued.
    "run_error": "The evaluation failed with an internal error; the items already replayed are kept.",
    "candidate_changed": "The AI configuration changed after the evaluation was queued; queue it again.",
}
TIER_UNAVAILABLE: Final[dict[str, str]] = {
    "ml_not_trained": "No ML model is trained yet; the ML tier would answer with the rules engine.",
    "llm_offline_cloud": "Offline mode is on and the configured model is not local ({provider}).",
    "llm_not_configured": "No language model is configured.",
}
