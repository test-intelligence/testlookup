---
name: testlookup-ai-quality
description: Review and improve TestLookup AI and agent quality and oversight. Use for analysis, retrieval, model evaluation, evidence-backed reporting, human review, action governance, and fallback behaviour.
---

# TestLookup AI and Agent Quality

## Follow the workflow

- Read `docs/pipelines/ai.md`, `docs/pipelines/reporting.md` and `architecture/AI_EVALUATION.md`. Locate the route, `backend/app/agents/workflow.py`, selected provider or tool, durable invocation/action record, evidence and final consumer.
- Record the configuration and prompt versions, source scope, provider mode, allowed tools, policy and approval state. Verify queued and terminal status, frozen execution inputs, retry or cancellation, budget settlement and evidence. Exercise approval and delivery with deterministic providers and fake receivers on a disposable system. Check that offline mode, provider unavailability and missing evidence each take their documented paths.
- Assert typed claims, evidence references, scope and review authority with deterministic fixtures. Assess model behavior with a fixed eval dataset and rubric for the changed capability. Record provider/model, dataset, rubric and prompt version with the score and regression examples.

## Verify failure and recovery

- Select backend agent or service tests first. For durable proof choose the relevant `backend/tests/integration` case, such as `test_ai_config_snapshot_postgres.py`, `test_ai_eval_runner_postgres.py` or `test_agent_action_ledger_postgres.py`.
- Verify applicable transitions with positive and withheld controls. For example, compare accepted and pending/rejected results through report composition, export and downstream delivery. Ensure source references and the final status have observable evidence. Add live candidate coverage using the appropriate `TL-AI-*` journey when the risk crosses services.
- Include the documented response to insufficient or conflicting evidence. Check whether the user can identify why a result was held for review or fell back. Use a disposable receiver to verify the effective approval decision and delivered or withheld effect. Use [Safety](../testlookup-safety/SKILL.md) when the result can trigger a consequential release or external action.
- Keep run provenance and sanitized evidence IDs with the verdict. Trace action proposals, the approving actor and final disposition through the ledger when an action is in scope. Record workflow and evaluation outcomes against their respective acceptance criteria. Update golden fixtures or coverage entries when the approved product contract changes.
