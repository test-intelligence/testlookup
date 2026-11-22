---
name: testlookup-e2e-quality
description: Review and improve TestLookup end-to-end tests, track journey coverage, triage functional defects, and assess PR, nightly, and release-candidate regressions. Use for TestLookup feature and bug-fix verification, flaky specs, coverage gaps, and release readiness.
---

# TestLookup E2E Quality

Trace each changed or high-risk product behavior to an assertion, an execution result, and the remaining risk. Work in the TestLookup product repository and follow its current workflows and local instructions.

## Establish the baseline

- Record the requested ref, candidate commit, comparison ref, and working-tree state. For a PR use its actual base or merge base. For a release use the previous verified build and the intended candidate. Read files at the relevant ref when the local checkout lags, and preserve the user's worktree.
- Read the changed requirements, PR or defect acceptance criteria, implementation, and existing tests. Confirm the route or interface, five-role expectations where relevant, project scope, feature flags, and fixture dependencies before judging coverage.
- Use [TestLookup test entry points](references/testlookup-workflows.md) to select a test lane and verify it at the candidate ref. For live runs, establish the target, deployed revision and image evidence, test identity, write permissions, and dependency mode. If a prerequisite is missing, record the affected checks as blocked and continue with the work that can be verified.

## Review and strengthen coverage

1. Map critical and changed obligations to actual assertions by journey, role, project scope, state and test layer. Check Playwright collection, skips and filters, CI triggers and job execution for the candidate ref, and the release catalogue and result registry. A route visit, a mocked success, and a deployed feature check provide different evidence.
2. Review assertions for meaningful user outcomes, persisted readback after reload or requery, a working control, deterministic fixtures, and observable terminal state. Trace asynchronous work from acceptance to completion. Identify duplicate checks, catch-all mocks, sleeps, retries and stale selectors that can hide a regression.
3. When changes are in scope, use the smallest test that verifies the behavior and add a live journey when the risk spans deployed boundaries. Reuse existing fixtures and keep data isolated per run. For a confirmed bug, add a discriminating regression and a nearby control. Demonstrate fail-before and pass-after on comparable local builds when available. Keep proposed, collected and executed tests distinct.
4. Update the existing coverage record with the before/after counts, missing cases, test mappings and evidence. Use [coverage and defect reporting](references/coverage-and-defects.md) and the [coverage ledger template](assets/coverage-ledger.md) when the repository has no equivalent.

## Use in the release cycle

- **PR or fix** · Review the change, run targeted tests and the relevant CI lane, and link the fix or acceptance criteria to the regression. Add required release obligations to the runner's catalogue and registry as applicable.
- **Nightly** · Review the current scheduled regression evidence for first-attempt failures, retries, blocked cases, and new coverage gaps. Compare like-for-like runs, deduplicate against open defects, and assign follow-up for each new gap or failing required check.
- **Release candidate** · Review the checked-in release gate for the exact candidate. Verify its disposable regression evidence and applicable upgrade/rollback drill, including the observed revision and manifest hash. Use the runner's verdict and record any additional unresolved risk. Keep promotion decisions with the existing release workflow and its owners.
- **Deployed smoke** · Use the approved read-only lane and verify the build and assigned identities. Keep writes, destructive paths, fault injection and paid or outbound integrations in an authorized disposable environment with synthetic fixtures.

Hand off the verdict, exact tested ref and target, required cases missing evidence, confirmed defects, and practical next actions. Refresh the catalogue and coverage record when a feature, role, flag or fixed defect changes a test obligation. Update this skill's guidance when those changes introduce a critical failure mode or alter the repository's test entry points, environment assumptions or evidence contract.
