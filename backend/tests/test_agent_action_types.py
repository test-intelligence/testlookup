"""W15 §2.1 test 4: the agent-action catalogue and its vocabularies (pure, no I/O)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from app.models.postgres import REVIEW_REASON_CODES, UserRole
from app.services import agent_action_types as t

RUN = "0b6f1c9e-2a4d-4c8e-9f1a-5d3b7e2c4a10"


def _base(**extra):
    return {
        "proposing_agent_id": "investigator",
        "proposing_agent_mode": "suggest",
        "investigation_id": str(uuid.uuid4()),
        "test_run_id": RUN,
        "title": "Quarantine checkout-retry",
        "rationale": "Failing in this run and already in the known-flaky set.",
        "risk": "low",
        **extra,
    }


VALID_PAYLOADS = {
    "quarantine.apply": _base(test_fingerprint="fpA1b2C3d4", test_name="checkout-retry", suite_name="checkout"),
    "failure.reassign": _base(
        test_case_id=str(uuid.uuid4()), assignee_user_id=str(uuid.uuid4()),
        suite_name="payments", test_name="payment-refund",
    ),
    "defect.promote": _base(
        failure_cluster_id=str(uuid.uuid4()), cluster_id="clA1b2", cluster_label="Timeout in checkout",
    ),
    "decision.acknowledge": {
        "proposing_agent_id": "agent.decision_report.v1", "action_id": "release-condition-1",
        "title": "Confirm the smoke suite", "owner": "qa", "rationale": "Open condition",
        "evidence": [], "risk": "unknown", "required_permission": "human_review",
    },
}


def _row(status, *, error_code=None, result_payload=None):
    return SimpleNamespace(status=status, error_code=error_code, result_payload=result_payload)


# ── the catalogue ────────────────────────────────────────────────────────────
def test_aliases_resolve_the_legacy_decision_type():
    assert t.canonical_action_type("decision_report_action") == "decision.acknowledge"
    assert t.resolve_action_type("decision_report_action") is t.ACTION_TYPES["decision.acknowledge"]
    assert t.resolve_action_type("quarantine.apply") is t.ACTION_TYPES["quarantine.apply"]
    for unknown in ("check_quarantine_status", "", None, 7, "QUARANTINE.APPLY"):
        assert t.canonical_action_type(unknown) is None
        assert t.resolve_action_type(unknown) is None
    for alias, target in t.ACTION_TYPE_ALIASES.items():
        assert target in t.ACTION_TYPES and alias not in t.ACTION_TYPES


def test_act_types_are_the_d16_three_each_opt_in_and_internal_reversible():
    assert t.ACT_ACTION_TYPES == ("quarantine.apply", "failure.reassign", "defect.promote")
    assert set(t.ACT_ACTION_TYPES) < set(t.ACTION_TYPES)
    for name in t.ACT_ACTION_TYPES:
        spec = t.ACTION_TYPES[name]
        assert spec.act_opt_in and spec.side_effect == "internal" and spec.reversible
        assert spec.proposers == frozenset({"investigator"})
    assert list(t.ACT_ACTION_LABELS) == list(t.ACT_ACTION_TYPES)
    ack = t.ACTION_TYPES["decision.acknowledge"]
    assert (ack.side_effect, ack.reversible, ack.act_opt_in) == ("none", False, False)


def test_no_spec_has_an_external_effect_and_every_role_is_qa_lead():
    for name, spec in t.ACTION_TYPES.items():
        assert spec.action_type == name
        assert spec.side_effect in t.SIDE_EFFECTS and spec.side_effect != "external"
        assert spec.required_role == UserRole.QA_LEAD
        assert spec.target_type in t.TARGET_KINDS
    assert t.PROPOSABLE_BY == {
        "agent.decision_report.v1": ("decision.acknowledge",),
        "investigator": ("defect.promote", "failure.reassign", "quarantine.apply"),
    }


def test_vocabularies_are_closed_tuples():
    assert t.QUEUE_VIEWS == ("pending", "decided")
    assert set(t.DECIDED_STATUSES) < set(t.LEDGER_STATUSES)
    assert "pending_review" not in t.DECIDED_STATUSES and "proposed" not in t.DECIDED_STATUSES
    assert t.REVIEW_REASON_CODES == REVIEW_REASON_CODES
    assert t.PROPOSING_REVIEW_STATES[-1] == "none"
    assert t.EXECUTION_LEASE_SECONDS > 120  # the task's time_limit: a live task is never reaped


# ── payload models ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", sorted(t.ACTION_TYPES))
def test_every_payload_model_forbids_extra_fields(name):
    model = t.ACTION_TYPES[name].payload_model
    assert issubclass(model, BaseModel) and model.model_config.get("extra") == "forbid"
    payload = VALID_PAYLOADS[name]
    assert model.model_validate(payload).model_dump(exclude_none=True).keys() <= set(payload) | {"evidence"}
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "href": "javascript:alert(1)"})


@pytest.mark.parametrize("name", sorted(t.ACT_ACTION_TYPES))
@pytest.mark.parametrize("field,value", [
    ("risk", "catastrophic"), ("proposing_agent_mode", "autonomous"), ("title", "x" * 201),
    ("rationale", "y" * 501), ("test_run_id", "r" * 65),
])
def test_effect_payloads_bound_their_enums_and_ids(name, field, value):
    with pytest.raises(ValidationError):
        t.ACTION_TYPES[name].payload_model.model_validate({**VALID_PAYLOADS[name], field: value})


def test_acknowledge_payload_accepts_a_stored_legacy_row_and_the_new_mode_key():
    stored = dict(VALID_PAYLOADS["decision.acknowledge"], title=None, risk=None)
    assert t.DecisionAcknowledgePayload.model_validate(stored).proposing_agent_mode is None
    with_mode = {**stored, "proposing_agent_mode": "suggest"}
    assert t.DecisionAcknowledgePayload.model_validate(with_mode).proposing_agent_mode == "suggest"


def test_every_error_code_fits_the_ledger_column_and_has_a_sentence():
    for code, sentence in t.ACTION_ERROR_SENTENCES.items():
        assert len(code) <= 100 and code == code.strip() and sentence
    for code in ("outcome_unknown", "superseded", "rollback_target_changed", "payload_tampered"):
        assert code in t.ACTION_ERROR_SENTENCES
    assert t.action_error_sentence("payload_invalid", type_label="Acknowledge") == (
        "The stored proposal is not a valid Acknowledge proposal."
    )
    assert t.action_error_sentence("not_a_code") is None
    for reason in REVIEW_REASON_CODES:
        assert reason in t.DECISION_REFUSAL_SENTENCES["reason_code_required"]


# ── targets ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,kind,target_id,label,tab", [
    ("quarantine.apply", "test", "fpA1b2C3d4", "checkout-retry", "tests"),
    ("defect.promote", "cluster", "clA1b2", "Timeout in checkout", "analysis"),
])
def test_target_of_reads_typed_fields_only(name, kind, target_id, label, tab):
    spec = t.ACTION_TYPES[name]
    payload = {**VALID_PAYLOADS[name], "rationale": "<img src=x onerror=alert(1)>"}
    target = t.target_of(spec, payload)
    assert (target.kind, target.id, target.label) == (kind, target_id, label)
    assert target.href == f"/runs/{RUN}?tab={tab}"
    # the validated model gives the same answer as the stored dict
    assert t.target_of(spec, spec.payload_model.model_validate(VALID_PAYLOADS[name])) == t.target_of(
        spec, VALID_PAYLOADS[name]
    )


def test_target_of_a_reassign_and_an_acknowledge():
    reassign = t.target_of(t.ACTION_TYPES["failure.reassign"], VALID_PAYLOADS["failure.reassign"])
    assert reassign.kind == "test_case" and reassign.label == "payment-refund"
    assert reassign.id == VALID_PAYLOADS["failure.reassign"]["test_case_id"]
    ack_spec = t.ACTION_TYPES["decision.acknowledge"]
    ack = t.target_of(ack_spec, VALID_PAYLOADS["decision.acknowledge"], test_run_id=uuid.UUID(RUN))
    assert (ack.kind, ack.id, ack.label, ack.href) == (
        "release_item", "release-condition-1", "release-condition-1", f"/runs/{RUN}?tab=evidence",
    )
    # no run → no link; a non-uuid run id never becomes part of a link
    assert t.target_of(ack_spec, VALID_PAYLOADS["decision.acknowledge"]).href is None
    tampered = {**VALID_PAYLOADS["quarantine.apply"], "test_run_id": "javascript:alert(1)"}
    assert t.target_of(t.ACTION_TYPES["quarantine.apply"], tampered).href is None
    # a missing name falls back to the id; non-text values are ignored
    nameless = {**VALID_PAYLOADS["quarantine.apply"], "test_name": None}
    assert t.target_of(t.ACTION_TYPES["quarantine.apply"], nameless).label == "fpA1b2C3d4"
    assert t.target_of(t.ACTION_TYPES["quarantine.apply"], {"test_fingerprint": 42}).id is None


def test_activity_entity_label_never_carries_the_rationale():
    labels = {
        name: t.activity_entity_label(t.ACTION_TYPES[name], VALID_PAYLOADS[name]) for name in t.ACTION_TYPES
    }
    assert labels == {
        "quarantine.apply": "Quarantine checkout-retry",
        "failure.reassign": "Reassign payment-refund",
        "defect.promote": "Defect for cluster Timeout in checkout",
        "decision.acknowledge": "Release item release-condition-1",
    }
    long_name = {**VALID_PAYLOADS["quarantine.apply"], "test_name": "n" * 400}
    assert len(t.activity_entity_label(t.ACTION_TYPES["quarantine.apply"], long_name)) == 120
    assert t.activity_entity_label(None, {}) == "Agent action"


def test_result_link_of_only_for_executed_rows_and_same_site_paths():
    link = {"kind": "quarantine", "id": "qr-1", "label": "quarantined", "href": "/flaky?tab=quarantined"}
    executed = _row("executed", result_payload={"result_link": link})
    assert t.result_link_of(executed).model_dump() == link
    assert t.result_link_of(_row("rolled_back", result_payload={"result_link": link})) is None
    for href in ("//evil.example/x", "https://evil.example/x", "javascript:alert(1)", "/a\\b"):
        bad = _row("executed", result_payload={"result_link": {**link, "href": href}})
        assert t.result_link_of(bad).href is None
    assert t.result_link_of(_row("executed", result_payload={"result_link": {**link, "kind": "jira"}})) is None


# ── outcomes: every row of the contract's table ──────────────────────────────
QUARANTINE = t.ACTION_TYPES["quarantine.apply"]
ACK = t.ACTION_TYPES["decision.acknowledge"]
_LINK = {"result_link": {"kind": "quarantine", "label": "quarantined", "href": "/flaky?tab=quarantined"}}
_REJECTION = {"rejection": {"reason_code": "unsupported_claim"}}


@pytest.mark.parametrize("row,spec,outcome,sentence", [
    (_row("pending_review"), QUARANTINE, "awaiting_approval", "Waiting for a QA lead's approval."),
    (_row("proposed"), QUARANTINE, "awaiting_approval", "Waiting for a QA lead's approval."),
    (_row("approved"), QUARANTINE, "approved_queued", "Approved by Lena Lead · running…"),
    (_row("executing"), QUARANTINE, "approved_queued", "Approved by Lena Lead · running…"),
    (_row("executed"), ACK, "acknowledged", "Acknowledged by Lena Lead."),
    (_row("executed", result_payload=_LINK), QUARANTINE, "executed",
     "Executed · approved by Lena Lead · quarantined."),
    (_row("executed"), QUARANTINE, "executed", "Executed · approved by Lena Lead."),
    # An assignee label is a name: the sentence says what happened to it (W15 walk: "· QA Lead." read
    # as a second approver).
    (_row("executed", result_payload={"result_link": {"kind": "assignee", "label": "Dana Owner",
                                                      "href": "/runs/r1?tab=tests"}}),
     t.ACTION_TYPES["failure.reassign"], "executed", "Executed · approved by Lena Lead · assigned to Dana Owner."),
    (_row("rolled_back"), QUARANTINE, "rolled_back", "Undone by Lena Lead."),
    (_row("rejected", result_payload=_REJECTION), QUARANTINE, "rejected",
     "Rejected by Lena Lead (unsupported_claim)."),
    (_row("rejected"), QUARANTINE, "rejected", "Rejected by Lena Lead."),
    (_row("rejected", error_code="superseded"), QUARANTINE, "superseded",
     "Replaced by a newer proposal for the same target."),
    (_row("failed", error_code="review_not_accepted"), QUARANTINE, "not_executed_review",
     "Not executed: the AI report was not accepted."),
    (_row("failed", error_code="policy_denied"), QUARANTINE, "not_executed_policy",
     "Not executed: this action type does not run in Act mode in this project."),
    (_row("failed", error_code="act_mode_required"), QUARANTINE, "not_executed_policy",
     "Not executed: this action type does not run in Act mode in this project."),
    (_row("failed", error_code="action_type_not_allowed"), QUARANTINE, "not_executed_policy",
     "Not executed: this action type does not run in Act mode in this project."),
    (_row("failed", error_code="target_not_in_project"), QUARANTINE, "not_executed_refused",
     "Not executed: The target does not belong to this project."),
    (_row("failed", error_code="payload_invalid"), QUARANTINE, "not_executed_refused",
     "Not executed: The stored proposal is not a valid Quarantine flaky offenders proposal."),
    (_row("failed", error_code="action_executor_not_configured"), None, "no_executor",
     "Approved and recorded — this action type has no executor."),
    (_row("failed", error_code="outcome_unknown"), QUARANTINE, "outcome_unknown",
     "The outcome is unknown: the run stopped while the action was executing. "
     "It is not retried — check checkout-retry and act on its own page."),
    (_row("failed", error_code="kaboom_from_a_legacy_row"), QUARANTINE, "failed",
     "Not executed: the action failed (kaboom_from_a_legacy_row)."),
    (_row("failed"), QUARANTINE, "failed", "Not executed: the action failed (unknown)."),
    (_row("mystery"), QUARANTINE, "unknown", ""),
])
def test_outcome_of_every_row_of_the_table(row, spec, outcome, sentence):
    assert t.outcome_of(row, spec, decider_name="Lena Lead", target_label="checkout-retry") == (outcome, sentence)
    assert outcome in t.OUTCOME_CODES


def test_outcome_of_without_a_name_or_target_uses_neutral_words():
    assert t.outcome_of(_row("rolled_back"), QUARANTINE) == ("rolled_back", "Undone by a QA lead.")
    code, sentence = t.outcome_of(_row("failed", error_code="outcome_unknown"), QUARANTINE)
    assert code == "outcome_unknown" and "check the target and act" in sentence
    # every outcome code has a sentence template, and no template is left with a placeholder
    assert set(t.OUTCOME_SENTENCES) == set(t.OUTCOME_CODES)
    for status in t.LEDGER_STATUSES:
        for error_code in (None, *t.ACTION_ERROR_SENTENCES):
            _, text = t.outcome_of(_row(status, error_code=error_code), QUARANTINE)
            assert "{" not in text


def test_rejection_reason_code_only_answers_a_known_code():
    assert t.rejection_reason_code(_row("rejected", result_payload=_REJECTION)) == "unsupported_claim"
    bogus = {"rejection": {"reason_code": "<b>x</b>", "notes": "secret"}}
    assert t.rejection_reason_code(_row("rejected", result_payload=bogus)) is None
    assert t.rejection_reason_code(_row("rejected")) is None


# ── the proposing mode (§0.5) ────────────────────────────────────────────────
def test_proposing_mode_of_prefers_the_payloads_own_mode():
    payload = {"proposing_agent_id": "investigator", "proposing_agent_mode": "suggest"}
    frozen_act = {"agent.decision_report.v1": {"mode": "act"}}
    assert t.proposing_mode_of(payload, workflow_configs=frozen_act) == "suggest"
    assert t.proposing_mode_of({**payload, "proposing_agent_mode": "act"}) == "act"
    # present but not a mode: fail closed (None), never fall through to a config
    assert t.proposing_mode_of({**payload, "proposing_agent_mode": "root"}) is None
    # the investigator is no registry capability: without its own mode → None
    assert t.proposing_mode_of({"proposing_agent_id": "investigator"}) is None


@pytest.mark.parametrize("agent_id", ["agent.decision_report.v1", "decision_report"])
def test_proposing_mode_of_resolves_a_capability_id_or_a_stage_name(agent_id):
    payload = {"proposing_agent_id": agent_id}
    workflow = {"agent.decision_report.v1": {"mode": "suggest"}}
    assert t.proposing_mode_of(payload, workflow_configs=workflow) == "suggest"
    invocation = {"agent.decision_report.v1": {"config": {"mode": "act"}}}
    assert t.proposing_mode_of(payload, workflow_configs=workflow, invocation_configs=invocation) == "act"
    assert t.proposing_mode_of(payload) is None
    assert t.proposing_mode_of(payload, workflow_configs={"agent.decision_report.v1": {"mode": "x"}}) is None


@pytest.mark.parametrize("payload", [None, [], {}, {"proposing_agent_id": ""}, {"proposing_agent_id": "nope"}])
def test_proposing_mode_of_fails_closed_on_an_unidentifiable_payload(payload):
    workflow = {"agent.decision_report.v1": {"mode": "suggest"}}
    assert t.proposing_mode_of(payload, workflow_configs=workflow) is None


def test_proposer_label():
    assert t.proposer_label("investigator") == "Investigator"
    assert t.proposer_label("agent.decision_report.v1") == "Decision report"
    assert t.proposer_label("some.other") == "some.other"
    assert t.proposer_label(None) is None
