"""
Label provenance taxonomy + training-set composition policy (AI-F1).

The ML classifier's training pool has two fundamentally different label
sources, and conflating them creates a circular learning loop: retraining
on the LLM's own high-confidence outputs teaches the classifier to imitate
the LLM, not to learn from ground truth. Every training example is therefore
classified into one of three provenance buckets:

  - ``human_direct``   — an engineer explicitly rated/corrected the analysis
                         (UI feedback card, US-2.4 correction dialog, MCP
                         ``correct_classification`` tool). Source values:
                         ``manual``, ``category_correction``.
  - ``human_indirect`` — a human action implied the label without an explicit
                         rating (Jira-resolution webhook auto-labels:
                         ``jira_resolved``, ``jira_invalid``).
  - ``llm_pseudo``     — a high-confidence LLM/ML analysis with **no** human
                         confirmation. Useful for bootstrapping, but it is the
                         model's own opinion, not ground truth.

Provenance is **derived**, not stored: any ``ai_feedback`` row is
human-originated (its ``source`` column separates direct from indirect), and
an ``ai_analysis`` row with no feedback is a pseudo-label. No schema change
is required.

The composition policy (:func:`apply_composition_policy`) enforces:
  1. Human labels are always included, at full ``sample_weight`` (1.0).
  2. Pseudo-labels are capped at ``ML_PSEUDO_LABEL_CAP`` of the final
     training set and down-weighted to ``ML_PSEUDO_LABEL_WEIGHT``.
  3. When human labels alone are below ``ML_MIN_TRAINING_SAMPLES``, pseudo-
     labels may fill up to that minimum (exceeding the cap), but the
     composition record flags it — and below ``ML_HUMAN_LABEL_FLOOR`` human
     labels the model must present itself as "bootstrap (LLM-imitating)".
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

HUMAN_DIRECT = "human_direct"
HUMAN_INDIRECT = "human_indirect"
LLM_PSEUDO = "llm_pseudo"

PROVENANCE_KINDS = (HUMAN_DIRECT, HUMAN_INDIRECT, LLM_PSEUDO)

# Model-maturity strings surfaced in routing records / settings / eval API.
MATURITY_NOT_TRAINED = "not_trained"
MATURITY_BOOTSTRAP = "bootstrap_llm_imitating"
MATURITY_HUMAN_CALIBRATED = "human_calibrated"

# AIFeedback.source values written by the UI feedback card / correction dialog
# and the MCP correct_classification tool (all route through
# feedback_service.submit_feedback, which stamps source="manual").
_DIRECT_FEEDBACK_SOURCES = frozenset({"manual", "category_correction"})
# Jira-resolution webhook auto-labels (feedback_service.jira_resolution_webhook).
_INDIRECT_FEEDBACK_SOURCES = frozenset({"jira_resolved", "jira_invalid"})


def provenance_for_feedback_source(source: Optional[str]) -> str:
    """Map an ``ai_feedback.source`` value to a provenance bucket.

    Every ``ai_feedback`` row originates from a human action, so unknown
    source values default to ``human_indirect`` (human-originated, but not a
    verified explicit correction) rather than ``llm_pseudo``.
    """
    if source in _DIRECT_FEEDBACK_SOURCES:
        return HUMAN_DIRECT
    return HUMAN_INDIRECT


def resolve_feedback_label(
    rating: Optional[str],
    corrected_category: Optional[str],
    analysis_category: Optional[str],
) -> Optional[str]:
    """Resolve the ground-truth category a feedback row asserts, if any.

    - ``correct``  → the human confirmed the analysis verdict.
    - ``incorrect`` + a corrected category → the correction is the label.
    - ``incorrect`` without a correction → we know the verdict is wrong but
      not what is right: unusable as a classification label.
    - anything else (``partially_correct``, missing) → unusable.
    """
    rating_val = getattr(rating, "value", rating)
    if rating_val == "correct":
        cat = getattr(analysis_category, "value", analysis_category)
        return str(cat) if cat else None
    if rating_val == "incorrect" and corrected_category:
        return str(getattr(corrected_category, "value", corrected_category))
    return None


def apply_composition_policy(
    samples: Sequence[dict],
    labels: Sequence[str],
    provenances: Sequence[str],
    *,
    pseudo_cap: float,
    pseudo_weight: float,
    min_samples: int,
    human_label_floor: int,
) -> tuple[list[dict], list[str], list[str], list[float], dict[str, Any]]:
    """Apply the training-set composition policy.

    Returns ``(samples, labels, provenances, sample_weights, composition)``
    where the first four lists are aligned and ``composition`` is the audit
    record persisted into the deployed model's metadata.

    Policy:
      - every non-``llm_pseudo`` example is kept with weight 1.0;
      - ``llm_pseudo`` examples are kept up to
        ``max(cap_limit, min_samples - n_human)`` where ``cap_limit`` solves
        ``pseudo / (human + pseudo) <= pseudo_cap`` — i.e. the cap may only be
        exceeded to reach ``min_samples`` (bootstrap fill), and that is
        recorded as ``cap_exceeded_to_fill_floor``;
      - kept pseudo examples get ``sample_weight = pseudo_weight``;
      - pseudo selection is deterministic (input order, which is the DB query
        order), so retrains on the same data are reproducible.
    """
    if not (len(samples) == len(labels) == len(provenances)):
        raise ValueError("samples, labels, provenances must be the same length")

    human_idx = [i for i, p in enumerate(provenances) if p != LLM_PSEUDO]
    pseudo_idx = [i for i, p in enumerate(provenances) if p == LLM_PSEUDO]
    n_human = len(human_idx)
    n_pseudo_available = len(pseudo_idx)

    # pseudo/(human+pseudo) <= cap  ⇒  pseudo <= cap/(1-cap) * human
    if pseudo_cap >= 1.0:
        cap_limit = n_pseudo_available
    elif pseudo_cap <= 0.0:
        cap_limit = 0
    else:
        cap_limit = int(pseudo_cap / (1.0 - pseudo_cap) * n_human)

    fill_limit = max(0, min_samples - n_human)
    allowed_pseudo = min(n_pseudo_available, max(cap_limit, fill_limit))
    cap_exceeded_to_fill_floor = allowed_pseudo > cap_limit

    kept_idx = human_idx + pseudo_idx[:allowed_pseudo]
    kept_idx.sort()  # preserve original ordering of the combined pool

    out_samples = [samples[i] for i in kept_idx]
    out_labels = [labels[i] for i in kept_idx]
    out_prov = [provenances[i] for i in kept_idx]
    out_weights = [
        1.0 if p != LLM_PSEUDO else float(pseudo_weight) for p in out_prov
    ]

    total = len(kept_idx)
    counts = {kind: 0 for kind in PROVENANCE_KINDS}
    for p in out_prov:
        counts[p] = counts.get(p, 0) + 1
    fractions = {
        kind: (round(counts[kind] / total, 4) if total else 0.0)
        for kind in PROVENANCE_KINDS
    }

    composition: dict[str, Any] = {
        "counts": counts,
        "fractions": fractions,
        "total": total,
        "human_label_count": n_human,
        "pseudo_available": n_pseudo_available,
        "pseudo_included": allowed_pseudo,
        "pseudo_dropped": n_pseudo_available - allowed_pseudo,
        "pseudo_cap": float(pseudo_cap),
        "pseudo_weight": float(pseudo_weight),
        "cap_exceeded_to_fill_floor": cap_exceeded_to_fill_floor,
        "human_label_floor": int(human_label_floor),
        "bootstrap": n_human < human_label_floor,
    }
    return out_samples, out_labels, out_prov, out_weights, composition


def model_maturity_from_metadata(
    metadata: Optional[dict], human_label_floor: int,
) -> str:
    """Derive the honesty label for a deployed model from its metadata.

    A model whose metadata lacks a ``label_composition`` block predates the
    provenance policy — it was trained under the old pseudo-heavy regime and
    is therefore reported as bootstrap, not silently promoted.
    """
    if not metadata or metadata.get("status") in (None, "not_trained"):
        return MATURITY_NOT_TRAINED
    composition = metadata.get("label_composition")
    if not isinstance(composition, dict):
        return MATURITY_BOOTSTRAP
    human_count = int(composition.get("human_label_count", 0) or 0)
    if human_count < human_label_floor:
        return MATURITY_BOOTSTRAP
    return MATURITY_HUMAN_CALIBRATED
