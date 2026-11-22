---
name: testlookup-safety
description: Review TestLookup operational constraints, risks, warnings, fail-safe behaviour, and safe integration. Use for release gates, automation, agent actions, approvals, and irreversible effects.
---

# TestLookup Safety and Fail-safe Behaviour

## Identify the risky outcome and safeguards

- Read `docs/reference/api/release-readiness.md`, `docs/reference/api/agent-operations.md`, and `architecture/AI_QUALITY.md` as applicable. Identify the outcome that could harm data, release decisions, or downstream systems. Trace the decision sources, approved policy and evidence versions, allowed operating state, and actor with authority to proceed.
- Identify the prescribed response to missing, stale, or conflicting evidence. Check that the operator sees the relevant warning and knows which prerequisite or review is needed. Follow any handoff to an external integration through approval, delivery, and durable disposition.

## Verify normal and guarded outcomes

- Compare a permitted path with a fixture where a required condition is blocked or unverified. Use synthetic inputs, mock dependencies, and disposable stores or receivers. Verify the intended disposition, user-visible reason, and durable result before and after a retry or reconnect.
- Use `backend/tests/regression/test_release_gate_decision.py`, `backend/tests/regression/test_release_gate_snapshot_explains_verdict.py`, or `backend/tests/regression/test_compliance_pack_release_gate.py` for applicable gating contracts. Use [AI and agent quality](../testlookup-ai-quality/SKILL.md) when the model or workflow participates in the decision.
- Record the risky outcome, guard, fixture condition, emitted warning, decision and effect, and residual risk. Link [Auditability](../testlookup-auditability/SKILL.md) for policy version, actor, and decision evidence.
