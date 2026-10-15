"""Does a manual retry resume, or start over? (architecture E7.4)

The problem this solves
-----------------------
An automatic retry (E7.2) and a manual retry are not the same operation, and
conflating them produces a silent, expensive lie.

An *automatic* retry is one attempt continuing: the worker died, the model
timed out, the lease lapsed. Nothing about the world changed, so
``_claim_pipeline_resume`` deliberately replays the frozen
``initial_workflow_plan`` and ``analysis_mode_resolution``. That is the right
call -- rebuilding the plan mid-run would let budgets, flags and task selection
drift between attempts of a single run (memory: "a rebuilt plan dropped every
flag").

A *manual* retry is the opposite. An operator clicks retry precisely BECAUSE
something changed: they narrowed a tool allowlist, pointed the stack at a
different model, or flipped ``AI_OFFLINE_MODE``. If we replayed the frozen plan
there, the retry would quietly run under the old configuration and the operator
would have no way to tell. Worse, the completed checkpoints we would replay were
authorised under the *old* config -- replaying them can re-enter a tool the new
allowlist forbids.

So the rule is: **a manual retry resumes only when the configuration it would
resume under is the same one it was frozen with. Otherwise it starts a new run.**

``rerun_of`` is what makes the second case honest: the new run points back at
the one it replaces, so the history shows two rows and a link rather than one
row that appears to have changed its mind.

What is in the fingerprint
--------------------------
Only fields that change *what the pipeline decides*: the resolved analysis
mode, the provider, the model, and the offline flag. Deliberately excluded:

* ``resolved_at`` -- a timestamp, different on every resolve, so including it
  would make every retry a rerun;
* ``requested`` and ``resolution_reason`` -- provenance. ``requested=auto``
  resolving to ``rules`` runs identically to ``requested=rules`` resolving to
  ``rules``, and forcing a rerun between them would burn a full pipeline for
  no behavioural difference.

W04 (AIC-02/AIC-03): a pipeline also freezes the Settings > AI confidence
threshold and LLM timeout (``execution_metadata["ai_config_snapshot"]``). When
the caller passes the live values (``current_ai_config``) and the row carries
a snapshot, those two fields join the fingerprint: an operator who raises the
timeout 5 -> 300 and presses Retry wants the new value, not a resume that
replays the frozen 5 s. A pre-W04 row has no snapshot and is compared on the
original four fields only, so existing failed pipelines keep resuming.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional

__all__ = [
    "AI_CONFIG_FINGERPRINT_FIELDS",
    "FINGERPRINT_FIELDS",
    "RetryPlan",
    "config_fingerprint",
    "decide_retry_mode",
]

# Ordered so the hash is stable across dict insertion order.
FINGERPRINT_FIELDS: tuple[str, ...] = ("resolved", "provider", "model", "offline")
#: ``AIConfigSnapshot`` fields compared when both sides carry one (W04).
AI_CONFIG_FINGERPRINT_FIELDS: tuple[str, ...] = ("confidence_threshold", "llm_timeout_seconds")


def config_fingerprint(
    mode_snapshot: Optional[Mapping[str, Any]],
    ai_config: Optional[Mapping[str, Any]] = None,
) -> str:
    """A stable hash of the decision-relevant configuration.

    An absent or malformed snapshot hashes to the empty shape rather than
    raising: a legacy row with no snapshot must be *comparable*, and it will
    simply never match a freshly resolved one, so it reruns. That is the safe
    direction -- a needless new run costs tokens, a wrong resume costs
    correctness.
    """
    snapshot = mode_snapshot or {}
    shape = {field: snapshot.get(field) for field in FINGERPRINT_FIELDS}
    # ``offline`` is not in the workflow's snapshot today; fall back to the
    # live setting so an env flip is always visible to the comparison.
    if shape["offline"] is None:
        try:
            from app.core.config import settings  # noqa: PLC0415

            shape["offline"] = bool(settings.AI_OFFLINE_MODE)
        except Exception:  # noqa: BLE001 -- a fingerprint must never fail a retry
            shape["offline"] = None
    if ai_config is not None:
        # Only when given: without it the hash stays byte-identical to the
        # pre-W04 four-field fingerprint.
        for field in AI_CONFIG_FINGERPRINT_FIELDS:
            shape[f"ai_config.{field}"] = ai_config.get(field)
    payload = json.dumps(shape, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


class RetryPlan:
    """How a manual retry should be carried out.

    ``mode`` is ``"resume"`` (same id, keep completed stages) or ``"rerun"``
    (new id, ``rerun_of`` set, nothing replayed).
    """

    __slots__ = ("mode", "reason", "frozen_fingerprint", "current_fingerprint")

    def __init__(
        self,
        mode: str,
        reason: str,
        *,
        frozen_fingerprint: str = "",
        current_fingerprint: str = "",
    ) -> None:
        self.mode = mode
        self.reason = reason
        self.frozen_fingerprint = frozen_fingerprint
        self.current_fingerprint = current_fingerprint

    @property
    def is_rerun(self) -> bool:
        return self.mode == "rerun"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "frozen_config": self.frozen_fingerprint,
            "current_config": self.current_fingerprint,
        }

    def __repr__(self) -> str:  # pragma: no cover -- debugging aid
        return f"RetryPlan({self.mode!r}, {self.reason!r})"


def decide_retry_mode(
    execution_metadata: Optional[Mapping[str, Any]],
    current_snapshot: Optional[Mapping[str, Any]],
    current_ai_config: Optional[Mapping[str, Any]] = None,
) -> RetryPlan:
    """Resume under unchanged config; rerun under changed config.

    Also reruns when the row carries no frozen plan at all -- there is nothing
    to resume *into*, and ``_claim_pipeline_resume`` would refuse the claim
    anyway, leaving the operator's retry silently doing nothing.

    ``current_ai_config`` is ``build_ai_config_snapshot(<live effective
    config>)``; with it, a row that froze an ``ai_config_snapshot`` also
    compares :data:`AI_CONFIG_FINGERPRINT_FIELDS`.
    """
    metadata = dict(execution_metadata or {})
    frozen_ai = metadata.get("ai_config_snapshot")
    compare_ai = isinstance(current_ai_config, Mapping) and isinstance(frozen_ai, Mapping)
    frozen = config_fingerprint(
        metadata.get("analysis_mode_resolution"),
        frozen_ai if compare_ai else None,
    )
    current = config_fingerprint(
        current_snapshot,
        current_ai_config if compare_ai else None,
    )

    plan = metadata.get("initial_workflow_plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("stages"), list):
        return RetryPlan(
            "rerun",
            "no_frozen_plan",
            frozen_fingerprint=frozen,
            current_fingerprint=current,
        )
    if frozen != current:
        return RetryPlan(
            "rerun",
            "config_changed",
            frozen_fingerprint=frozen,
            current_fingerprint=current,
        )
    return RetryPlan(
        "resume",
        "config_unchanged",
        frozen_fingerprint=frozen,
        current_fingerprint=current,
    )
