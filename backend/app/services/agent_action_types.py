"""The agent-action catalogue and its vocabularies (W15 AGT-01/02/03, contract §1.2).

ONE place for every enum-like value the agent-action queue, the investigator's
typed proposals and the Act executor share: the side effects, the risk words,
the queue views, the ledger statuses a decision moves a row to, the ONLY types
Act may run (owner decision D16), the legacy-type alias, the proposer labels,
the payload model of every registered type, the outcome codes and their
sentences, and the refusal / error codes the executor writes into
``AgentActionLedger.error_code``.

The request / response fields that carry one of these values are plain ``str``
(never ``Literal`` / Enum); the router or service judges them against the
constants here with one fixed sentence.

Payload models are ``extra="forbid"``: an adapter reads ONLY their typed
fields (ids, fingerprints, bounded enums). ``title`` / ``rationale`` /
``evidence`` are display-only — never parsed, never passed to a service call,
never an ``href``.

Pure and commit-free: no I/O, no session. ``proposing_mode_of`` imports the
agent-config module lazily (that module imports ``ACT_ACTION_TYPES`` from here).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.postgres import AGENT_CONFIG_MODES, REVIEW_REASON_CODES, UserRole
from app.models.schemas import AgentActionResultLink, AgentActionTarget

__all__ = [
    "ACTION_ERROR_SENTENCES",
    "ACTION_TYPES",
    "ACTION_TYPE_ALIASES",
    "ACT_ACTION_LABELS",
    "ACT_ACTION_TYPES",
    "DECIDED_STATUSES",
    "DECISION_REFUSAL_SENTENCES",
    "EXECUTION_LEASE_SECONDS",
    "LEDGER_STATUSES",
    "MAX_DEFECT_PROPOSALS",
    "MAX_QUARANTINE_PROPOSALS",
    "MAX_REASSIGN_PROPOSALS",
    "OUTCOME_CODES",
    "OUTCOME_SENTENCES",
    "PROPOSABLE_BY",
    "PROPOSER_LABELS",
    "PROPOSING_REVIEW_STATES",
    "QUEUE_VIEWS",
    "REAPER_BATCH",
    "RESULT_KINDS",
    "REVIEW_REASON_CODES",
    "RISK_LEVELS",
    "SIDE_EFFECTS",
    "TARGET_KINDS",
    "UNKNOWN_DECIDER",
    "ActionTypeSpec",
    "DecisionAcknowledgePayload",
    "DefectPromotePayload",
    "FailureReassignPayload",
    "QuarantineApplyPayload",
    "action_error_sentence",
    "activity_entity_label",
    "canonical_action_type",
    "outcome_of",
    "proposer_label",
    "proposing_mode_of",
    "rejection_reason_code",
    "resolve_action_type",
    "result_link_of",
    "target_href",
    "target_of",
]

# ── Vocabularies ─────────────────────────────────────────────────────────────
SIDE_EFFECTS: tuple[str, ...] = ("none", "internal", "external")
RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high", "unknown")
PROPOSING_REVIEW_STATES: tuple[str, ...] = (
    "accepted", "pending_review", "rejected", "superseded", "none",
)
QUEUE_VIEWS: tuple[str, ...] = ("pending", "decided")   # GET ?view=; pending = status pending_review
#: Every ledger status, in the order the list route's 422 sentence names them
#: (the table's CHECK constraint, ``models/postgres.py`` ``ck_agent_action_status``).
LEDGER_STATUSES: tuple[str, ...] = (
    "proposed", "pending_review", "approved", "executing", "executed",
    "failed", "rejected", "rolled_back",
)
DECIDED_STATUSES: tuple[str, ...] = (
    "approved", "executing", "executed", "failed", "rejected", "rolled_back",
)
#: D16: the ONLY types Act may run, each opt-in per project.
ACT_ACTION_TYPES: tuple[str, ...] = ("quarantine.apply", "failure.reassign", "defect.promote")
#: The legacy ledger type the decision report writes (§0.22) → its catalogue type.
ACTION_TYPE_ALIASES: dict[str, str] = {"decision_report_action": "decision.acknowledge"}
PROPOSER_LABELS: dict[str, str] = {
    "investigator": "Investigator",
    "agent.decision_report.v1": "Decision report",
}
TARGET_KINDS: tuple[str, ...] = ("test", "test_case", "cluster", "release_item")
RESULT_KINDS: tuple[str, ...] = ("quarantine", "defect", "assignee")

MAX_QUARANTINE_PROPOSALS = 5      # offenders per investigation (sorted by fingerprint)
MAX_REASSIGN_PROPOSALS = 5        # core tests per investigation (sorted by test name, then fingerprint)
MAX_DEFECT_PROPOSALS = 1          # the largest cluster, only when concentrated
EXECUTION_LEASE_SECONDS = 600     # executing longer than this → failed / outcome_unknown (never retried)
REAPER_BATCH = 100

_UUID_TEXT = 64                   # an id written as text (uuid4 is 36 characters)


# ── Payload models (every one extra="forbid"; ids are str, bounded) ─────────
class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _EffectPayload(_Payload):
    """The fields every effectful (investigator) proposal carries."""

    proposing_agent_id: str = Field(min_length=1, max_length=120)
    proposing_agent_mode: str
    investigation_id: str = Field(min_length=1, max_length=_UUID_TEXT)
    test_run_id: str = Field(min_length=1, max_length=_UUID_TEXT)
    title: str = Field(max_length=200)
    rationale: str = Field(max_length=500)
    risk: str
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=5)  # display only

    @field_validator("proposing_agent_mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        if value not in AGENT_CONFIG_MODES:
            raise ValueError(f"proposing_agent_mode must be one of {list(AGENT_CONFIG_MODES)}")
        return value

    @field_validator("risk")
    @classmethod
    def _known_risk(cls, value: str) -> str:
        if value not in RISK_LEVELS:
            raise ValueError(f"risk must be one of {list(RISK_LEVELS)}")
        return value


class QuarantineApplyPayload(_EffectPayload):
    test_fingerprint: str = Field(min_length=1, max_length=255)
    test_name: Optional[str] = Field(None, max_length=500)
    suite_name: Optional[str] = Field(None, max_length=255)


class FailureReassignPayload(_EffectPayload):
    test_case_id: str = Field(min_length=1, max_length=_UUID_TEXT)
    assignee_user_id: str = Field(min_length=1, max_length=_UUID_TEXT)
    suite_name: str = Field(max_length=255)
    test_name: Optional[str] = Field(None, max_length=500)


class DefectPromotePayload(_EffectPayload):
    failure_cluster_id: str = Field(min_length=1, max_length=_UUID_TEXT)
    cluster_id: str = Field(min_length=1, max_length=255)
    cluster_label: Optional[str] = Field(None, max_length=200)


class DecisionAcknowledgePayload(_Payload):
    """EXACTLY ``persist_report_action_proposals``' keys (+ the optional mode, §0.14)."""

    proposing_agent_id: str
    proposing_agent_mode: Optional[str] = None
    action_id: str
    title: Optional[str] = None
    owner: Optional[str] = None
    rationale: Optional[str] = None
    evidence: list[Any] = Field(default_factory=list)
    risk: Optional[str] = None
    required_permission: Optional[str] = None


# ── The catalogue ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ActionTypeSpec:
    action_type: str
    target_type: str
    payload_model: type[_Payload]
    side_effect: str
    reversible: bool
    required_role: UserRole
    act_opt_in: bool
    label: str
    proposers: frozenset[str]


ACTION_TYPES: dict[str, ActionTypeSpec] = {
    "quarantine.apply": ActionTypeSpec(
        action_type="quarantine.apply", target_type="test",
        payload_model=QuarantineApplyPayload, side_effect="internal", reversible=True,
        required_role=UserRole.QA_LEAD, act_opt_in=True,
        label="Quarantine flaky offenders", proposers=frozenset({"investigator"}),
    ),
    "failure.reassign": ActionTypeSpec(
        action_type="failure.reassign", target_type="test_case",
        payload_model=FailureReassignPayload, side_effect="internal", reversible=True,
        required_role=UserRole.QA_LEAD, act_opt_in=True,
        label="Reassign failures to the suite owner", proposers=frozenset({"investigator"}),
    ),
    "defect.promote": ActionTypeSpec(
        action_type="defect.promote", target_type="cluster",
        payload_model=DefectPromotePayload, side_effect="internal", reversible=True,
        required_role=UserRole.QA_LEAD, act_opt_in=True,
        label="Promote an internal defect", proposers=frozenset({"investigator"}),
    ),
    "decision.acknowledge": ActionTypeSpec(
        action_type="decision.acknowledge", target_type="release_item",
        payload_model=DecisionAcknowledgePayload, side_effect="none", reversible=False,
        required_role=UserRole.QA_LEAD, act_opt_in=False,
        label="Acknowledge", proposers=frozenset({"agent.decision_report.v1"}),
    ),
}

#: ``{action_type: label}`` for the Act types, in ``ACT_ACTION_TYPES`` order
#: (mirrored by ``frontend/src/types/agentActions.ts``).
ACT_ACTION_LABELS: dict[str, str] = {t: ACTION_TYPES[t].label for t in ACT_ACTION_TYPES}

#: ``{agent_id: (action types it may propose)}``, sorted.
PROPOSABLE_BY: dict[str, tuple[str, ...]] = {
    agent: tuple(sorted(t for t, spec in ACTION_TYPES.items() if agent in spec.proposers))
    for agent in sorted({a for spec in ACTION_TYPES.values() for a in spec.proposers})
}


# ── Outcomes (one place; ``{name}`` = the decider's display name) ───────────
OUTCOME_CODES: tuple[str, ...] = (
    "awaiting_approval", "approved_queued", "acknowledged", "executed", "rolled_back",
    "rejected", "superseded", "not_executed_review", "not_executed_policy",
    "not_executed_refused", "no_executor", "outcome_unknown", "failed", "unknown",
)
OUTCOME_SENTENCES: dict[str, str] = {
    "awaiting_approval": "Waiting for a QA lead's approval.",
    "approved_queued": "Approved by {name} · running…",
    "acknowledged": "Acknowledged by {name}.",
    "executed": "Executed · approved by {name} · {result}.",
    "rolled_back": "Undone by {name}.",
    "rejected": "Rejected by {name} ({reason_code}).",
    "superseded": "Replaced by a newer proposal for the same target.",
    "not_executed_review": "Not executed: the AI report was not accepted.",
    "not_executed_policy": "Not executed: this action type does not run in Act mode in this project.",
    "not_executed_refused": "Not executed: {refusal}",
    "no_executor": "Approved and recorded — this action type has no executor.",
    "outcome_unknown": (
        "The outcome is unknown: the run stopped while the action was executing. "
        "It is not retried — check {target} and act on its own page."
    ),
    "failed": "Not executed: the action failed ({error_code}).",
    "unknown": "",
}
#: When the decider's name is not known (a deleted user, a row without one).
UNKNOWN_DECIDER = "a QA lead"

_ACT_OFF = (
    "This action type does not run in this project: a QA lead turns it on under "
    "Settings › AI agents › Investigator › Act, or do it yourself."
)

#: The refusal / error codes the executor (and the approve check) write into
#: ``AgentActionLedger.error_code`` — every code ≤ 100 characters (the column).
ACTION_ERROR_SENTENCES: dict[str, str] = {
    "review_not_accepted": "Accept the AI report first: an approved action runs only on an accepted report.",
    "act_mode_required": _ACT_OFF,
    "action_type_not_allowed": _ACT_OFF,
    "proposer_not_permitted": "This agent may not propose this action type.",
    "payload_tampered": "The stored proposal no longer matches its hash.",
    "payload_invalid": "The stored proposal is not a valid {type} proposal.",
    "approver_not_authorized": "The approver is no longer a QA lead of this project.",
    "target_not_in_project": "The target does not belong to this project.",
    "quarantine_disabled": "Quarantine is turned off for this project.",
    "target_already_quarantined": "The test is already quarantined.",
    "test_case_not_failing": "The test is no longer failing in this run.",
    "assignee_no_longer_owner": "The proposed assignee is no longer the suite owner.",
    "already_assigned": "The failure is already assigned to the suite owner.",
    "cluster_not_found": "The failure cluster no longer exists in this run.",
    "defect_already_exists": "An open defect already covers this cluster.",
    "offline_mode_blocked": "Offline mode blocks actions that leave TestLookup.",
    "execution_error": "The action failed while running; nothing was changed.",
    "outcome_unknown": "The outcome is unknown: the run stopped while the action was executing.",
    "superseded": "Replaced by a newer proposal for the same target.",
    "rollback_target_changed": "The target changed after the action ran: undo it on its own page.",
    "action_executor_not_configured": "This action type has no executor.",
    "policy_denied": "This action type does not run in Act mode in this project.",
}

#: The PATCH-only refusals (never stored on a row): the router's ``{code, message}`` details.
DECISION_REFUSAL_SENTENCES: dict[str, str] = {
    "interactive_login_required": (
        "Deciding an agent action requires an interactive login; API keys are refused."
    ),
    "synthetic_account": "Synthetic accounts cannot decide agent actions.",
    "reason_code_required": (
        "A rejection needs a reason_code: " + ", ".join(REVIEW_REASON_CODES) + "."
    ),
    "reason_code_on_reject_only": "reason_code applies to a rejection only.",
    "action_not_found": "Agent action not found.",
    "action_not_pending": "This action is already {status}.",
    "separation_of_duties": (
        "The person who requested this investigation cannot approve the changes it proposes."
    ),
    "action_not_executed": "Only an executed action can be undone.",
    "action_not_reversible": "This action cannot be undone.",
    "action_transition_invalid": "This action cannot move to that status.",
}


# ── Lookups ──────────────────────────────────────────────────────────────────
def canonical_action_type(raw: Any) -> Optional[str]:
    """The catalogue type for a stored ``action_type`` (aliases first); None = unregistered."""
    if not isinstance(raw, str) or not raw:
        return None
    name = ACTION_TYPE_ALIASES.get(raw, raw)
    return name if name in ACTION_TYPES else None


def resolve_action_type(raw: Any) -> Optional[ActionTypeSpec]:
    name = canonical_action_type(raw)
    return ACTION_TYPES[name] if name is not None else None


def proposer_label(agent_id: Any) -> Optional[str]:
    """``PROPOSER_LABELS[agent_id]``, else the id itself; None without an id."""
    if not isinstance(agent_id, str) or not agent_id:
        return None
    return PROPOSER_LABELS.get(agent_id, agent_id)


def _capability_id_for(agent_id: str) -> Optional[str]:
    """``agent_id`` as a configurable capability id OR a registry stage name."""
    # Lazy: agent_config_service imports ACT_ACTION_TYPES from this module.
    from app.services.agent_capability_registry import CAPABILITY_REGISTRY
    from app.services.agent_config_service import configurable_capabilities

    if agent_id in configurable_capabilities():
        return agent_id
    spec = CAPABILITY_REGISTRY.get(agent_id)
    return spec.capability_id if spec is not None else None


def proposing_mode_of(
    payload: Any,
    *,
    workflow_configs: Any = None,
    invocation_configs: Any = None,
) -> Optional[str]:
    """The mode the proposing agent ran in when it proposed ``payload`` (§0.5).

    1. The payload's own ``proposing_agent_mode`` (hashed into the row at
       proposal time) wins; a present but unknown value answers None.
    2. Else ``proposing_agent_id`` resolved as a capability id OR a stage name,
       looked up in the pipeline's frozen ``resolved_agent_configs`` (the
       invocation snapshot, ``{cap: {"config": {...}}}``) and then
       ``workflow_agent_configs`` (``{cap: {...}}``).
    3. Else None — the caller fails closed.
    """
    if not isinstance(payload, Mapping):
        return None
    own = payload.get("proposing_agent_mode")
    if own is not None:
        return own if isinstance(own, str) and own in AGENT_CONFIG_MODES else None
    agent_id = payload.get("proposing_agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        return None
    capability_id = _capability_id_for(agent_id)
    if capability_id is None:
        return None
    frozen: Any = None
    if isinstance(invocation_configs, Mapping):
        snapshot = invocation_configs.get(capability_id)
        frozen = snapshot.get("config") if isinstance(snapshot, Mapping) else None
    if not isinstance(frozen, Mapping) and isinstance(workflow_configs, Mapping):
        frozen = workflow_configs.get(capability_id)
    if not isinstance(frozen, Mapping):
        return None
    mode = frozen.get("mode")
    return mode if isinstance(mode, str) and mode in AGENT_CONFIG_MODES else None


# ── Targets and result links (typed fields only; hrefs from ids only) ────────
_TARGET_FIELDS: dict[str, tuple[str, Optional[str]]] = {
    "quarantine.apply": ("test_fingerprint", "test_name"),
    "failure.reassign": ("test_case_id", "test_name"),
    "defect.promote": ("cluster_id", "cluster_label"),
    "decision.acknowledge": ("action_id", None),
}
_TARGET_TABS: dict[str, str] = {
    "test": "tests", "test_case": "tests", "cluster": "analysis", "release_item": "evidence",
}


def _uuid_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _as_mapping(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, BaseModel):
        return payload.model_dump()
    return payload if isinstance(payload, Mapping) else {}


def target_href(kind: str, *, test_run_id: Any) -> Optional[str]:
    """The Run-page tab that shows a target of ``kind``; None without a valid run id."""
    run = _uuid_text(test_run_id)
    tab = _TARGET_TABS.get(kind)
    if run is None or tab is None:
        return None
    return f"/runs/{run}?tab={tab}"


def target_of(spec: ActionTypeSpec, payload: Any, *, test_run_id: Any = None) -> AgentActionTarget:
    """What the action acts on, from the type's TYPED payload fields only."""
    data = _as_mapping(payload)
    id_field, label_field = _TARGET_FIELDS.get(spec.action_type, ("", None))
    target_id = _text(data.get(id_field), 255) if id_field else None
    label = _text(data.get(label_field), 200) if label_field else None
    run = test_run_id if test_run_id is not None else data.get("test_run_id")
    return AgentActionTarget(
        kind=spec.target_type,
        id=target_id,
        label=label or target_id,
        href=target_href(spec.target_type, test_run_id=run),
    )


def activity_entity_label(spec: Optional[ActionTypeSpec], payload: Any) -> str:
    """The activity row's ``entity_label`` (§1.4): typed fields, ≤ 120, never the rationale."""
    if spec is None:
        return "Agent action"
    target = target_of(spec, payload)
    name = target.label or target.id or "?"
    text = {
        "quarantine.apply": f"Quarantine {name}",
        "failure.reassign": f"Reassign {name}",
        "defect.promote": f"Defect for cluster {name}",
        "decision.acknowledge": f"Release item {name}",
    }.get(spec.action_type, spec.label)
    return text[:120]


def result_link_of(row: Any) -> Optional[AgentActionResultLink]:
    """An EXECUTED row's ``result_payload["result_link"]`` (the executor writes it)."""
    if getattr(row, "status", None) != "executed":
        return None
    result = getattr(row, "result_payload", None)
    link = result.get("result_link") if isinstance(result, Mapping) else None
    if not isinstance(link, Mapping) or link.get("kind") not in RESULT_KINDS:
        return None
    href = link.get("href")
    safe_href = (
        href
        if isinstance(href, str) and href.startswith("/") and not href.startswith("//")
        and "\\" not in href
        else None
    )
    return AgentActionResultLink(
        kind=str(link["kind"]),
        id=_text(link.get("id"), 255),
        label=_text(link.get("label"), 200),
        href=safe_href,
    )


def rejection_reason_code(row: Any) -> Optional[str]:
    result = getattr(row, "result_payload", None)
    rejection = result.get("rejection") if isinstance(result, Mapping) else None
    code = rejection.get("reason_code") if isinstance(rejection, Mapping) else None
    return code if isinstance(code, str) and code in REVIEW_REASON_CODES else None


# ── Outcomes (pure: status + error_code + type → code and sentence) ─────────
_POLICY_CODES = frozenset({"policy_denied", "act_mode_required", "action_type_not_allowed"})
#: Codes with an outcome of their own; every OTHER code of ACTION_ERROR_SENTENCES
#: is a refusal (``not_executed_refused``).
_OWN_OUTCOME_CODES = _POLICY_CODES | {
    "review_not_accepted", "action_executor_not_configured", "outcome_unknown",
}


def action_error_sentence(code: Any, *, type_label: Optional[str] = None) -> Optional[str]:
    """The sentence of a stored refusal / error code; None for an unknown code."""
    if not isinstance(code, str):
        return None
    sentence = ACTION_ERROR_SENTENCES.get(code)
    if sentence is None:
        return None
    return sentence.replace("{type}", type_label or "agent action")


def outcome_of(
    row: Any,
    spec: Optional[ActionTypeSpec],
    *,
    decider_name: Optional[str] = None,
    target_label: Optional[str] = None,
) -> tuple[str, str]:
    """``(OUTCOME_CODES member, sentence)`` for a ledger row — pure, no I/O.

    ``decider_name`` is the approver / rejecter's display name (the list joins
    ``users``); ``target_label`` names the target in the outcome-unknown
    sentence. Both fall back to neutral words.
    """
    status = getattr(row, "status", None)
    code = getattr(row, "error_code", None) or None
    name = decider_name or UNKNOWN_DECIDER

    def _say(outcome: str, **values: str) -> tuple[str, str]:
        text = OUTCOME_SENTENCES[outcome]
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        return outcome, text

    if status in ("pending_review", "proposed"):
        return _say("awaiting_approval")
    if status in ("approved", "executing"):
        return _say("approved_queued", name=name)
    if status == "executed":
        if spec is not None and spec.side_effect == "none":
            return _say("acknowledged", name=name)
        link = result_link_of(row)
        if link is not None and link.label:
            # An assignee's label is a person's name: "approved by Ana · assigned to Lee", not
            # "approved by Ana · Lee" (which reads as a second approver).
            result = f"assigned to {link.label}" if link.kind == "assignee" else link.label
            return _say("executed", name=name, result=result)
        return "executed", f"Executed · approved by {name}."
    if status == "rolled_back":
        return _say("rolled_back", name=name)
    if status == "rejected":
        if code == "superseded":
            return _say("superseded")
        reason = rejection_reason_code(row)
        if reason is None:
            return "rejected", f"Rejected by {name}."
        return _say("rejected", name=name, reason_code=reason)
    if status == "failed":
        if code == "review_not_accepted":
            return _say("not_executed_review")
        if code in _POLICY_CODES:
            return _say("not_executed_policy")
        if code == "action_executor_not_configured":
            return _say("no_executor")
        if code == "outcome_unknown":
            return _say("outcome_unknown", target=target_label or "the target")
        if code in ACTION_ERROR_SENTENCES:
            refusal = action_error_sentence(code, type_label=spec.label if spec else None)
            return _say("not_executed_refused", refusal=refusal or "")
        return _say("failed", error_code=str(code or "unknown")[:100])
    return _say("unknown")
