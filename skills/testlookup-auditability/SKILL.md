---
name: testlookup-auditability
description: Review and improve TestLookup auditability, traceability, and evidence quality. Use for actor and project attribution, durable audit trails, release decision replay, action approval, evidence exports, and retention.
---

# TestLookup Auditability

## Follow a decision or operation

- Read `docs/pipelines/reporting.md` and `docs/reference/api/audit-dashboard.md`. Locate the source write in `backend/app/services/audit_log_service.py` or a domain ledger, then trace the projection through `backend/app/services/audit_dashboard_service.py` or the relevant export. Identify which durable record a reviewer can use to reconstruct the operation.
- Identify the requester, approver, service executor and external receiver where applicable. Record actor and credential type, project and resource, operation and correlation IDs, effective policy or decision version, outcome and timestamp. For delayed delivery or replay, link the original decision, effective destination, worker attempt and final outcome with durable IDs.
- Verify a successful action plus a contract-relevant refusal, retry, reversal or supersession. Compare committed business state with its audit event after a fresh read and check which attempts should be distinct and which should deduplicate. Verify visibility, redaction and retention for the stored record and export.

## Verify decision and export evidence

- For durable coverage, use cases such as `backend/tests/integration/test_audit_unified_feed_postgres.py`, `backend/tests/integration/test_agent_action_ledger_postgres.py` and `backend/tests/integration/test_outbox_requeue_audit_postgres.py`. Verify both the underlying record and the authorized projection. Include an expected scope/refusal control where the feature has one.
- For release decisions, trace `backend/app/services/release_policy_replay.py` to the pinned snapshot, policy version and evidence used by the decision. For compliance exports, check `backend/app/services/compliance_pack_verify.py` and `backend/tests/integration/test_compliance_pack_signing_postgres.py`. Verify the chosen format's integrity and signing contract, including old keys if rotation is in scope.
- Record build, fixture scope, expected audit fields, event and correlation IDs, visible result, and a sanitized evidence link. For each gap, identify the missing link, the corrective change, owning component and an acceptance check a reviewer can run from a new session.
