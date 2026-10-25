"""The Investigator's deterministic cause → typed-action plan (W15 AGT-02).

A PURE function (no I/O, no LLM, no prompt): it turns the verdict's
``primary_cause`` and the plan node's evidence bundle into at most ten typed
actions (``InvestigatorStructuredActionV1`` dumps) that ``_finalize`` files
as ``pending_review`` proposals in Suggest / Act mode. Shadow persists
nothing; the verdict still carries the plan so the cockpit can show what
WOULD have been proposed.

The map (deterministic order, the caps of ``services/agent_action_types``):

======================  ====================================================
``primary_cause``       structured actions
======================  ====================================================
``known_flaky``         ``quarantine.apply`` per offender = target ∩ the
                        known-flaky set (target = the compare's newly-failed
                        tests when a compare exists, else every failing row
                        — the hypothesis' own rule), sorted by fingerprint,
                        at most ``MAX_QUARANTINE_PROPOSALS``.
``regression``          ``failure.reassign`` per core test (newly − flaky −
                        infra) whose failure row has a ``test_case_id`` and
                        whose suite has an owner, sorted by (test name,
                        fingerprint), at most ``MAX_REASSIGN_PROPOSALS``;
                        PLUS one ``defect.promote`` when the largest cluster
                        concentrates the core (``CLUSTER_CONCENTRATION_RATIO``)
                        and carries a ``cluster_id``.
everything else         none — the text recommendations only.
======================  ====================================================

The plan reads ONLY ids, fingerprints, names and counts from the bundle —
never an error text, a narrative or an LLM field — so the offline path and a
model-enriched run yield the SAME actions, and no model output can ever reach
an executor (the prompt-injection boundary). An action whose payload does not
validate against its type's payload model is dropped here, so a bad row never
fails a verdict.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import structlog
from pydantic import ValidationError

from app.agents.investigator.hypotheses import CLUSTER_CONCENTRATION_RATIO
from app.models.agent_contracts import InvestigatorStructuredActionV1
from app.services.agent_action_types import (
    ACTION_TYPES,
    MAX_DEFECT_PROPOSALS,
    MAX_QUARANTINE_PROPOSALS,
    MAX_REASSIGN_PROPOSALS,
)

logger = structlog.get_logger("agents.investigator.action_plan")

PROPOSING_AGENT_ID = "investigator"
MAX_STRUCTURED_ACTIONS = 10  # InvestigatorVerdictV1.structured_actions max_length
_TITLE_MAX = 200

QUARANTINE_RATIONALE = "Failing in this run and already in the known-flaky set."
REASSIGN_RATIONALE = (
    "A newly-failed test outside the flaky and infrastructure sets: "
    "its suite owner should triage it."
)
DEFECT_RATIONALE = (
    "The newly-failed core concentrates in one failure cluster: "
    "an internal defect tracks it in one place."
)


def _text(value: Any) -> Optional[str]:
    """The value as text, or None when absent / empty. Never altered."""
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _title(text: str) -> str:
    return text if len(text) <= _TITLE_MAX else text[: _TITLE_MAX - 1] + "…"


def _target_tests(bundle: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The known-flaky hypothesis' target: newly-failed when a compare
    exists, else every failing row (``hypotheses.py`` KnownFlaky.evaluate)."""
    newly = (bundle.get("compare") or {}).get("newly_failed_tests")
    target = newly if newly is not None else (bundle.get("failures") or [])
    return [t for t in target if isinstance(t, Mapping)]


def _failure_rows(bundle: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The run's failure rows by fingerprint (first row wins)."""
    rows: dict[str, Mapping[str, Any]] = {}
    for row in bundle.get("failures") or []:
        if isinstance(row, Mapping) and row.get("fingerprint"):
            rows.setdefault(str(row["fingerprint"]), row)
    return rows


def flaky_offenders(bundle: Mapping[str, Any]) -> list[str]:
    """Fingerprints of target ∩ the known-flaky set, sorted. Its length is
    the known-flaky hypothesis' ``signals["flaky_overlap"]``."""
    target_fps = {str(t.get("fingerprint")) for t in _target_tests(bundle) if t.get("fingerprint")}
    flaky_fps = {str(fp) for fp in (bundle.get("flaky_fingerprints") or [])}
    return sorted(target_fps & flaky_fps)


def regression_core(bundle: Mapping[str, Any]) -> list[str]:
    """Fingerprints of newly − flaky − infra, sorted; ``[]`` without a
    compare. Its length is the regression hypothesis' ``signals["core"]``."""
    compare = bundle.get("compare")
    if compare is None:
        return []
    newly = compare.get("newly_failed_tests") or []
    newly_fps = {str(t.get("fingerprint")) for t in newly if isinstance(t, Mapping) and t.get("fingerprint")}
    flaky_fps = {str(fp) for fp in (bundle.get("flaky_fingerprints") or [])}
    infra_fps = {
        str(f.get("fingerprint"))
        for f in (bundle.get("failures") or [])
        if isinstance(f, Mapping) and str(f.get("category") or "").upper() == "INFRASTRUCTURE"
    }
    return sorted(newly_fps - flaky_fps - infra_fps)


def concentrated_cluster(bundle: Mapping[str, Any], core: list[str]) -> Optional[Mapping[str, Any]]:
    """The largest cluster when it concentrates the core — the regression
    hypothesis' ``cluster_concentrated`` rule, the same ``max`` tie-break."""
    clusters = [c for c in (bundle.get("clusters") or []) if isinstance(c, Mapping)]
    largest = max(clusters, key=lambda c: int(c.get("size") or 0), default=None)
    if largest is None or not core:
        return None
    if int(largest.get("size") or 0) >= CLUSTER_CONCENTRATION_RATIO * len(core):
        return largest
    return None


def _build(
    action_type: str, target_id: Optional[str], title: str, payload: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Validate one action against its type's payload model and the verdict
    contract; ``None`` (dropped, logged with the type only) when either
    refuses it or the catalogue no longer lets the Investigator propose it."""
    spec = ACTION_TYPES.get(action_type)
    if spec is None or PROPOSING_AGENT_ID not in spec.proposers or not target_id:
        return None
    try:
        typed = spec.payload_model.model_validate(payload).model_dump(mode="json")
        action = InvestigatorStructuredActionV1.model_validate({
            "action_type": action_type,
            "target_type": spec.target_type,
            "target_id": target_id,
            "title": _title(title),
            "payload": typed,
        })
    except ValidationError as exc:
        logger.info(
            "investigator_action_dropped",
            action_type=action_type,
            error_count=len(exc.errors()),
        )
        return None
    return action.model_dump(mode="json")


def _identity(
    *, investigation_id: str, proposing_agent_mode: str, test_run_id: str
) -> dict[str, Any]:
    return {
        "proposing_agent_id": PROPOSING_AGENT_ID,
        "proposing_agent_mode": proposing_agent_mode,
        "investigation_id": investigation_id,
        "test_run_id": test_run_id,
    }


def _quarantine_actions(bundle: Mapping[str, Any], identity: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _failure_rows(bundle)
    target_rows = {str(t["fingerprint"]): t for t in _target_tests(bundle) if t.get("fingerprint")}
    actions: list[dict[str, Any]] = []
    for fingerprint in flaky_offenders(bundle):
        if len(actions) >= MAX_QUARANTINE_PROPOSALS:
            break
        row = rows.get(fingerprint) or target_rows.get(fingerprint) or {}
        test_name = _text(row.get("test_name"))
        title = f"Quarantine {test_name or fingerprint}"
        built = _build("quarantine.apply", fingerprint, title, {
            **identity,
            "test_fingerprint": fingerprint,
            "test_name": test_name,
            "suite_name": _text(row.get("suite_name")),
            "title": _title(title),
            "rationale": QUARANTINE_RATIONALE,
            "risk": "low",
            "evidence": [{"type": "flaky_set", "id": fingerprint}],
        })
        if built is not None:
            actions.append(built)
    return actions


def _reassign_actions(
    bundle: Mapping[str, Any], core: list[str], suite_owners: Mapping[str, str],
    identity: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = _failure_rows(bundle)
    candidates: list[tuple[str, str, Mapping[str, Any], str]] = []
    for fingerprint in core:
        row = rows.get(fingerprint)
        if row is None:
            continue
        test_case_id = _text(row.get("test_case_id"))
        suite_name = _text(row.get("suite_name"))
        owner = _text(suite_owners.get(suite_name)) if suite_name else None
        if not test_case_id or not owner:
            continue
        candidates.append((str(row.get("test_name") or ""), fingerprint, row, owner))
    actions: list[dict[str, Any]] = []
    for _name, fingerprint, row, owner in sorted(candidates, key=lambda c: (c[0], c[1])):
        if len(actions) >= MAX_REASSIGN_PROPOSALS:
            break
        test_case_id = str(row["test_case_id"])
        suite_name = str(row["suite_name"])
        test_name = _text(row.get("test_name"))
        title = f"Reassign {test_name or fingerprint} to the {suite_name} owner"
        built = _build("failure.reassign", test_case_id, title, {
            **identity,
            "test_case_id": test_case_id,
            "assignee_user_id": owner,
            "suite_name": suite_name,
            "test_name": test_name,
            "title": _title(title),
            "rationale": REASSIGN_RATIONALE,
            "risk": "medium",
            "evidence": [{"type": "regression_core", "id": fingerprint}],
        })
        if built is not None:
            actions.append(built)
    return actions


def _defect_actions(
    bundle: Mapping[str, Any], core: list[str], identity: dict[str, Any]
) -> list[dict[str, Any]]:
    cluster = concentrated_cluster(bundle, core)
    if cluster is None:
        return []
    cluster_id = _text(cluster.get("cluster_id"))
    failure_cluster_id = _text(cluster.get("failure_cluster_id"))
    if not cluster_id or not failure_cluster_id:
        return []
    label = _text(cluster.get("label"))
    title = f"Promote a defect for cluster {label or cluster_id}"
    built = _build("defect.promote", cluster_id, title, {
        **identity,
        "failure_cluster_id": failure_cluster_id,
        "cluster_id": cluster_id,
        "cluster_label": label[:200] if label else None,
        "title": _title(title),
        "rationale": DEFECT_RATIONALE,
        "risk": "medium",
        "evidence": [{"type": "cluster", "id": cluster_id}],
    })
    return [built][:MAX_DEFECT_PROPOSALS] if built is not None else []


def plan_structured_actions(
    primary_cause: str,
    bundle: Mapping[str, Any],
    *,
    suite_owners: Mapping[str, str],
    investigation_id: str = "",
    proposing_agent_mode: str = "shadow",
) -> list[dict[str, Any]]:
    """The typed actions for ``primary_cause`` over ``bundle`` (module doc).

    Returns ``InvestigatorStructuredActionV1`` dumps in a deterministic order.
    ``investigation_id`` / ``proposing_agent_mode`` stamp the payload's
    identity fields; ``_finalize`` re-stamps them from the investigation row
    before anything is persisted.
    """
    run = bundle.get("run") or {}
    test_run_id = _text(run.get("id")) if isinstance(run, Mapping) else None
    if not test_run_id:
        return []
    identity = _identity(
        investigation_id=investigation_id,
        proposing_agent_mode=proposing_agent_mode,
        test_run_id=test_run_id,
    )
    actions: list[dict[str, Any]] = []
    if primary_cause == "known_flaky":
        actions = _quarantine_actions(bundle, identity)
    elif primary_cause == "regression":
        core = regression_core(bundle)
        actions = _reassign_actions(bundle, core, suite_owners or {}, identity)
        actions += _defect_actions(bundle, core, identity)
    return actions[:MAX_STRUCTURED_ACTIONS]
