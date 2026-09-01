# Test management & ownership

Beyond runs and failures, TestLookup maintains a **catalog** of your logical tests — with lifecycle states, plans, reviews, and owners. Ownership is what makes triage routing work: every failure lands on a person because every suite resolves to one.

## The Test Management page

**Test Management** (`/test-management`) is the catalog view:

- **Test cases** — every logical test (by fingerprint) with its **type** (functional, API, integration, …), **priority** (critical/high/…/low), and governed lifecycle status. Filters combine, and the counts shown are execution-aware (a suite's card shows real execution totals, not just distinct-test snapshots).
- **Test-case detail** — click a case for its drawer: **Details**, **History** (cross-run results), **Reviews**, **Comments**, and **Unautomated** (manual-test tracking). Deep links expand and scroll to the right case automatically.
- **Test plans** — group cases into plans for a release or initiative and track their execution state.
- **Test suites** — the suite catalog, including each suite's **owner** — assign one here (the picker also lets a QA lead set themselves).
- **Review workflow** — cases can be sent for review (including **AI Review**); review states flow through the same catalog so approvals are visible where the tests live.
- **Duplicate detection** — near-identical test cases are flagged so the catalog stays deduplicated as teams add tests independently.

## Authored test-case lifecycle

Authored cases follow one server-enforced state machine:

```text
draft → review_requested → under_review → approved → active
  ↑             ↕              │            │         │
  └── revise/rework ← rejected ─┘            └─────────┴→ needs_update → draft

draft | rejected | approved | active | needs_update → deprecated → archived
deprecated | archived → draft (reinstate)
```

The API reports the actions currently available to the signed-in user. Review
decisions belong to the reviewer who claimed the review; another reviewer
cannot race or replace that decision. An author cannot approve their own case.
Deprecating, reinstating, and archiving require a QA lead or administrator and
a recorded reason. The legacy Delete action is still accepted without a reason
during the first compatibility release, but the server records that omission
for operators to find and migrate.

Editing an approved or active definition invalidates the old approval and moves
the case to `needs_update`. Every save and lifecycle transition creates a full
immutable version snapshot and an audit entry containing the transition,
reason, actor, and effective policy. `approved` remains selectable by existing
test-plan workflows during this compatibility release; new workflows should
activate approved cases before execution.

AI review is advisory evidence. It creates its own completed AI-review record
and never claims a human review.

Rollout is controlled by the `test_case_lifecycle_v2` feature flag. When it is
off for a project, the direct transition and allowed-transition APIs are not
exposed; the compatibility request-review, review-action, and Delete paths
remain available and still enforce the same row-locked policy.

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

An observed canonical case can be **promoted** into Test Management. Promotion
links an existing same-project authored case with the same fingerprint when
there is exactly one; otherwise it creates one draft authored case and copies
the automation fingerprint verbatim. Multiple matching authored cases are a
conflict that must be resolved rather than guessed. This keeps the combined
catalog deduplicated. Promoting an already-linked case is refused. A wrong link
can be removed with a required reason without deleting either record.

When automation disappears across the configured observation window, the
canonical case enters the orphan queue. The queue is derived from observed
deletion state; a reappearing test leaves it automatically. A QA lead can
confirm intentional retirement with a reason. Each new disappearance is fresh
evidence and clears any confirmation made for an earlier disappearance.

Two evidence-gap lists help validate the governed-library workflow:

- authored cases that have never been linked to execution evidence;
- authored cases whose linked automation has vanished.

**Nothing here is hard-deleted.** Canonical rows and suite memberships carry a
status plus the run in which they disappeared, so a test that vanishes from a
suite leaves a trace rather than silently ceasing to exist. That lineage is what
lets the product tell "this test was removed" apart from "this test stopped
being reported", which are very different problems.


## Related

- Where assignments surface: [Triaging failures](triaging-failures.md)
- What suite data feeds: [Release gates](release-gates.md), coverage and trends pages
