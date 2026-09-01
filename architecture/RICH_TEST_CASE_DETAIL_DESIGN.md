# Rich Test Case Detail — MVP Design Contract

Status: implementation baseline for the Rich Test Case Detail epic
Date: 2026-08-31

## Contract boundary

The first implementation uses an additive, versioned read contract. It does not merge authored test definitions with executed result snapshots:

- `definition` contains editable `ManagedTestCase` content and follows authored-case versioning.
- `execution` contains immutable source-run facts and follows report ingestion/retention.
- `canonical_test_case_id` links the two domains when TestLookup can establish the relationship.

Existing flat response fields remain available as compatibility projections while consumers migrate to the nested contract.

## Optional-step semantics

`steps_present` distinguishes source semantics:

- `false`: the producer did not provide a step collection, or provided `null`/an empty collection.
- `true`: a non-empty step collection was provided and at least one valid node was retained.

The public response always uses `steps: []` when no steps are available. Invalid child nodes are skipped with bounded warnings; they do not reject the surrounding test case or run. All traversal and metadata payloads remain bounded.

## Identity and retention decisions

The platform preserves source identifiers when available (`uuid`, logical test ID, history ID, full name) and retains the existing project-scoped fingerprint for compatibility. Retry collapse and parameterized variants are tested separately from canonical identity.

The current MVP keeps the existing latest-run step snapshot behavior and exposes its provenance. Full historical step/evidence snapshots are a follow-up storage decision; the compact per-run step-outcome history remains available for step-flip analysis.

## Classification and provenance

Service/component values are never silently inferred as authoritative. Every resolved value carries a source such as `explicit`, `mapping`, `derived`, or `unknown`. Project-scoped mapping and future manual overrides must be auditable and must not mutate the source execution snapshot.

## Compatibility and rollout

The first release is additive and feature-flag compatible. Sparse JUnit/XML results are valid and must render with honest empty states. Rich Allure and Playwright fixtures form the primary acceptance path; other adapters publish only the metadata they can support.
