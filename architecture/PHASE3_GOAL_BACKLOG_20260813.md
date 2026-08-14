# Phase 3 AI Test Intelligence backlog

| ID | Severity | Status | Owner area | Finding | Next action | Exit evidence |
|---|---|---|---|---|---|---|
| P3-GOAL-001 | P2 | Completed | Runtime observability | Frozen execution-context persistence failures were only logged. | Added `testlookup_pipeline_execution_context_persist_failures_total` and regression coverage. | Targeted pytest and Ruff pass. |
| P3-GOAL-002 | P2 | Deferred / external | Product + QA | Pilot readiness needs named personas/owners and an approved representative corpus. | Approve corpus, personas, rubric, and owners outside the code repository. | Readiness endpoint can evaluate the approved tenant/corpus. |
| P3-GOAL-003 | P2 | Deferred / external | QA + Product | Release gate requires two consecutive passing cycles over one corpus hash with at least 80% qualified-user utility. | Run and persist two cycles with qualified feedback. | No streak reset, missing-feedback, or threshold bypass. |
| P3-GOAL-004 | P2 | Deferred / external | Security + Platform | Provider residency/allowlist, privacy, and action-owner sign-off remain governance gates. | Record approvals and enable only approved project flags. | Audit record plus default-off rollback path. |
| P3-GOAL-005 | P3 | Deferred | Platform | Python 3.14 emits upstream Pydantic/LangGraph deprecation warnings. | Track dependency upgrade compatibility separately. | Supported dependency upgrade removes the warning budget. |

Items 002–004 are human/governance inputs, not missing implementation. The
cluster-child, autonomous mutation, and async supersession paths remain
default-off until those gates are approved. No code-level blocker remains for
the approved Phase 3 implementation, test, documentation, and homelab goal.
