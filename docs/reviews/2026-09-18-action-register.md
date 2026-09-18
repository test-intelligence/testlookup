# Review action register: documentation handoff

Date: 2026-09-18. [Review summary](2026-09-18-review-summary.md).

| ID | Severity | Status | Owner area | Finding | Evidence | Recommended action | Validation expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| DOC-01 | P2 | Completed | Documentation | Stale stack/feature/client claims | Baseline README vs package, bootstrap and client registry | New current guides and generated inventories now replace duplicated top-level claims | Manifest/route source comparison and documentation checks | None |
| DOC-02 | P2 | Completed | Data documentation | 27 legacy schema entries absent | Schema checker initially 139/112; final 139/139 | Linked missing definitions and generated full dictionary | `python scripts/gen_schema_docs.py --check` reports zero missing | None |
| DOC-03 | P2 | Open | Backend API | 195 operations include an unstructured response | [Exact list](../reference/api-contract-gaps.md) | Prioritize response models for high-use client endpoints; retain handler semantics | Behavioral response tests, regenerated OpenAPI and consumer compatibility | Product/API prioritization; outside docs-only runtime scope |
| DOC-04 | P3 | Deferred | CI/documentation | Handoff drift check not CI-wired | [Maintenance](../handoff/maintenance.md) commands | Add scoped CI job with backend import dependencies | Deliberately stale generated doc fails job | CI cost/ownership decision |

Status counts: Completed 2; Open 1; Deferred 1. No P0/P1 runtime defect is asserted by this documentation audit. Live readiness remains unassessed; the [handoff checklist](../handoff/README.md) identifies environment-specific acceptance evidence.
