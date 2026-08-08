# Test management & ownership

Beyond runs and failures, TestLookup maintains a **catalog** of your logical tests — with lifecycle states, plans, reviews, and owners. Ownership is what makes triage routing work: every failure lands on a person because every suite resolves to one.

## The Test Management page

**Test Management** (`/test-management`) is the catalog view:

- **Test cases** — every logical test (by fingerprint) with its **type** (functional, API, integration, …), **priority** (critical/high/…/low), and **lifecycle status**: `draft → active → approved`, with `deprecated` for retirement. Filters combine, and the counts shown are execution-aware (a suite's card shows real execution totals, not just distinct-test snapshots).
- **Test-case detail** — click a case for its drawer: **Details**, **History** (cross-run results), **Reviews**, **Comments**, and **Unautomated** (manual-test tracking). Deep links expand and scroll to the right case automatically.
- **Test plans** — group cases into plans for a release or initiative and track their execution state.
- **Test suites** — the suite catalog, including each suite's **owner** — assign one here (the picker also lets a QA lead set themselves).
- **Review workflow** — cases can be sent for review (including **AI Review**); review states flow through the same catalog so approvals are visible where the tests live.
- **Duplicate detection** — near-identical test cases are flagged so the catalog stays deduplicated as teams add tests independently.

## Ownership: who gets the failure?

Two layers decide the owner of anything that fails:

1. **Suite owners** — set per suite (Test Management → Suites, or `/ownership`).
2. **Ownership rules** (`/ownership`) — pattern-based rules for bulk mapping (e.g. suites matching `payments-*` → the payments QA lead), edited in the ownership editor.

### Auto-assignment (what happens on every run)

When a run is finalized, every `FAILED`/`BROKEN` test case is auto-assigned by this resolution chain:

```
TestSuiteOwner for the suite
  → the project's default QA lead
  → the project's manager (legacy fallback)
  → unassigned (needs an admin to configure owners)
```

Assignment lands the failure in the owner's [My Failures](triaging-failures.md#your-inbox-my-failures) inbox. Two guarantees worth knowing:

- **It never overwrites a human decision.** Auto-assignment only fills rows that are currently unassigned — if you reassigned a failure by hand, a re-run or recovery pass won't take it back.
- **It can't break ingestion.** Assignment runs as an isolated finalization step; if it hiccups, the run still lands.

### The default QA lead

Every project gets a **default QA-lead** user automatically (created with the project), so auto-assignment always has a destination and My Failures is never structurally empty. Practical consequence for admins: most unrouted failures sit on that synthetic user — flip My Failures to **Team** scope to see them, and configure real suite owners to route future failures to actual people.

## Recommended setup order

1. Create suite owners for your top suites (Test Management → Suites).
2. Add `/ownership` rules for families of suites.
3. Verify with the next run: failures should appear in the right inboxes (Team scope shows anything that fell through to the default QA lead).


## Canonical test detail (`/canonical-test-cases/:id`)

A **canonical test case** is the deduped, cross-run identity of a test — what
"the same test" means when it has run five hundred times under slightly
different names. It is the *observed* counterpart to an authored
[managed test case](#managed-test-cases): one is what your suites actually ran,
the other is what someone wrote down.

The detail page shows a single canonical test's history across runs — its
status over time, class name and suite membership, and how long it has been
taking — reached from a suite's test list.

**Nothing here is hard-deleted.** Canonical rows and suite memberships carry a
status plus the run in which they disappeared, so a test that vanishes from a
suite leaves a trace rather than silently ceasing to exist. That lineage is what
lets the product tell "this test was removed" apart from "this test stopped
being reported", which are very different problems.


## Related

- Where assignments surface: [Triaging failures](triaging-failures.md)
- What suite data feeds: [Release gates](release-gates.md), coverage and trends pages
