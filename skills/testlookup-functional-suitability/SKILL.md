---
name: testlookup-functional-suitability
description: Assess TestLookup functional completeness, correctness, and fitness for a user task. Use for changed workflows, result semantics, feature acceptance, and outcome parity across components.
---

# TestLookup Functional Suitability

## Establish the expected outcome

- Start with the user goal, role, project, feature flags, and relevant product contract. Read `docs/pipelines/ingestion.md`, `docs/pipelines/reporting.md`, and the affected obligations in `e2e-runner/catalogue.yaml`. Identify required inputs, valid terminal outcomes, and the surfaces that consume them.
- Check functional completeness against the full task. Trace each required step from entry through persisted state to visible or exported results. Check appropriateness by walking the intended user's normal route and identifying manual steps or stale information that block the task.
- Derive expected results independently from the implementation. For an ingestion change, use a supported report fixture with known results and check status counts, identifiers, filters, and source links after processing. Include a valid empty or partial result when the contract defines one.

## Verify across the boundary

- Verify the acceptance response and terminal result separately. Read the persisted state in a new request and compare the relevant UI, REST, CLI, or MCP result with the same fixture. Check release decision inputs only where the changed result feeds the configured policy.
- Select an owning component test for the changed behavior and use [E2E quality](../testlookup-e2e-quality/SKILL.md) for the portion that crosses deployed boundaries. Match the actual role and feature flags to the journey.
- Record the candidate revision, required outcomes, case IDs, fixture expectations, and matching evidence for each verified outcome. Record gaps and pending or blocked results in the coverage ledger.
