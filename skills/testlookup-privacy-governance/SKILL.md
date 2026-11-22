---
name: testlookup-privacy-governance
description: Review TestLookup privacy and data governance controls. Use for data minimization, redaction, configured egress, retention, holds, export, erasure, and evidence handling.
---

# TestLookup Privacy and Data Governance

## Map purpose and data lifecycle

- Read `DATA_AND_PRIVACY.md`, `docs/reference/api/retention.md`, and `docs/operations/configuration.md`. Identify the data class, purpose, project scope, legitimate consumers, transformations, storage locations, and any configured delivery. Include derived indexes, logs, attachments, and exported evidence where the workflow creates them.
- Identify the project or deployment retention policy, holds, exceptions, redaction points, and permitted egress. Trace how they apply to original uploads, normalized records, and user-visible projections. Record expectations from the applicable product policy and configuration.

## Verify policy with synthetic data

- Use synthetic markers and disposable projects. Follow a permitted lifecycle, check which fields may appear at each sink, and compare an applicable refusal, redaction, hold, or deletion outcome. Verify configured external delivery with a local mock receiver and the selected profile's network policy.
- Choose cases such as `backend/tests/integration/test_retention_holds_postgres.py`, `backend/tests/integration/test_retention_export_postgres.py`, or `backend/tests/integration/test_retention_search_index_scope_postgres.py`. Check storage and retrieval or export after the lifecycle transition.
- Record the candidate, data class, policy version, allowed projection, and lifecycle result with sanitized evidence IDs. Link [Security](../testlookup-security-quality/SKILL.md) for access controls and [Auditability](../testlookup-auditability/SKILL.md) for attributable processing decisions.
