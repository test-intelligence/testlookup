# Coverage and defect reporting

## Compare coverage over time

1. Define the inventory as observable obligations for the chosen tier and layer. Give each row a stable ID, expected outcome, priority, role, scope and state, and link its requirement or defect. Expand cases by role or format when the release runner requires separate recorded results. Include required gaps in the inventory until the obligation is changed or retired.
2. Map each obligation to a concrete runnable and enabled test whose assertions meet the required layer. Count each obligation once per declared scope. Link supporting tests separately. Record manual or proposed cases and skips so the existence of a test plan never inflates automation coverage.
3. Report **automated coverage** as mapped, enabled obligations divided by required obligations. Report **first-attempt verification** as required obligations passed first time divided by the same required obligations. Break out failures, retry-only passes, skips, blocks, and checks not run. Mark missing inventory as unknown. Preserve missing evidence even when a test implementation exists.
4. Compare by layer, tier, role scope and feature state. Record the baseline and candidate commits, counts as well as percentages, and the IDs added, strengthened, retired, or still uncovered. Reconcile any changed denominator before interpreting a trend. Keep journey coverage distinct from source-code line coverage.
5. Reuse the project’s canonical tracker. If none exists, use the [coverage ledger template](../assets/coverage-ledger.md) in an agreed repository location, such as `docs/testing/e2e-quality/coverage-ledger.md`. Add a dated snapshot per review and keep history available. Store run evidence under its own run ID or CI artifact.

For targeted coverage, map the changed product paths to the release catalogue and inspect the actual checks. For example, ingestion tests that compare skipped counts against a producer exercise import semantics. The Runs page exclusion of skipped-only runs from its pass-rate average needs its own UI assertion and a failure-only control. Record the hermetic display and deployed import-to-display obligations separately. Verify the latter by importing synthetic reports, checking persisted counts and asserting the Runs page on a disposable candidate.

## Triage failures

- Classify each unexpected result as a confirmed product defect, suspected defect, test/fixture defect, environment block, or flake. Use expected behavior from requirements or code and observed evidence from the same candidate and appropriate fixtures. Preserve the first failure, and rerun a narrow case where useful. Inspect the outcome from a known-good control to narrow attribution.
- Use existing TestLookup severity conventions. In their absence use S1 for critical system-wide loss, data integrity or incorrect release decisions with severe impact. Use S2 for a major broken user journey or materially wrong data or decisions. Use S3 for limited functional impact and S4 for minor usability or presentation faults. Use P0/P1/P2 separately for test or release priority. Give new confirmed S1/S2 findings immediate visibility in the QA handoff and include the affected release obligations.
- Record each confirmed defect with issue ID, severity and impact, SHA and environment, role and synthetic data setup, minimal normal product steps, expected vs observed, repeatability, and sanitized evidence or request IDs. Link an existing issue when the symptom and cause match. Include expected resolution, fix acceptance condition, regression test ID or proposed coverage, and remaining verification.
- Close a defect as verified once the fixed candidate passes the check at the required layer and the nearby control still holds. Record the evidence link and any independent release run still due. Handle ambiguity by documenting the missing product decision or unavailable evidence.

## Release handoff

Lead with the runner verdict or blocked verification, candidate SHA and target. Include the evidence link, P0/P1 result breakdown, upgrade/rollback outcome, new defects and unresolved coverage. List owner or next action for each outstanding item. Preserve `CONDITIONAL_GO` as its actual verdict when present and identify the approval still required by policy.
