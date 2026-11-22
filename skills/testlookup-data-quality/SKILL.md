---
name: testlookup-data-quality
description: Review TestLookup data quality, durability, and consistency. Use for correctness, completeness, freshness, deduplication, lineage, reconciliation, and persisted test or release evidence.
---

# TestLookup Data Quality and Durability

## Trace source data to released evidence

- Read `docs/pipelines/ingestion.md`, `docs/pipelines/reporting.md`, and the affected schema or `contracts/viz` contract. Identify the authoritative record for each field, the transformations, the project and run boundaries, and the point at which downstream views should be current.
- Derive expected identities, statuses, counts, units, and timestamps from the fixture. Include a valid edge case, such as an empty report, a skipped result, equal timestamps, or a retry of the same receipt. Use the contract to decide which records merge, remain distinct, or surface as incomplete.

## Reconcile every relevant view

- Compare source, persisted state, summaries, exports, and selected client views for the same identifiers. Read durable state in a separate request after the documented completion or reconciliation point. Check accepted records after a replay or restart when persistence is in scope.
- Use `backend/tests/integration/test_suite_membership_parity_postgres.py` or `backend/tests/integration/test_mcp_chart_data_parity_postgres.py` as examples of reconciliation. Use [Data and storage](../testlookup-data-storage-quality/SKILL.md) for schema, backup, and storage verification.
- Record the candidate revision, fixture lineage, expected and observed counts or values, completion or freshness bound, and cause of any discrepancy. Use [Auditability](../testlookup-auditability/SKILL.md) when the data feeds a review or release decision.
