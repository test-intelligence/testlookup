# Test management

Working with tests as durable objects rather than rows in one run.

## Managed suites and test cases

Tests are tracked by [fingerprint](/docs/concepts), so a test has a life across runs. Suites group them. A suite comes from your report where present and is inferred otherwise.

> **Note.** Rename a test and its history stays with the old fingerprint. The new name starts fresh. If a test's history disappears after a refactor, this is usually why.

## Ownership and assignment

- **Ownership** attaches a team or person to tests or suites.
- **Assignment** puts a specific failure in someone's queue, visible under **My failures**.
- Failures auto-assigned without a better candidate go to a synthetic per-project QA-lead account — which is why **My failures** can look empty until you switch to team scope.

## Triage

Each failure carries a triage state, distinct from its execution status. A test's *status* is what happened when it ran; its *triage state* is where a human has got to with it.

## Bulk operations and history

Suite and ownership changes can be applied in bulk, and changes are recorded so a later reader can see who changed what.

## Project scope

Everything here is project-scoped. Suites, ownership and assignments never cross a project boundary.

Required role: `QA_ENGINEER` for triage and assignment; `QA_LEAD` for ownership across a team.

## Related

- [Investigating failures](/docs/failure-analysis)
- [Concepts and terminology](/docs/concepts)
