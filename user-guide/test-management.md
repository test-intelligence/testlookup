# Test catalog governance and ownership

The decisions a QA lead or administrator makes about the test catalogue: the authored test-case lifecycle and its flag, ownership rules, the automatic assignment of failures, and the default QA lead. Day-to-day use of **Suites** and **Test cases** (moving tests, promoting an observed test, reviewing a case, plans, strategy, duplicates) is in the in-app guide: **Test management** (`/docs/test-management`, source [test-management.md](../frontend/src/content/guide/test-management.md)).

## Authored test-case lifecycle

Authored cases follow one server-enforced state machine:

```text
draft → review_requested → under_review → approved → active
  ↑             ↕              │            │         │
  └── revise/rework ← rejected ─┘            └─────────┴→ needs_update → draft

draft | rejected | approved | active | needs_update → deprecated → archived
deprecated | archived → draft (reinstate)
```

The API reports the actions available to the signed-in user, and the **Lifecycle** tab shows only those. The rules:

- Claiming a review and deciding it (approve, reject, request changes) need `QA_ENGINEER` or higher, and only the reviewer who claimed it can decide. An author cannot approve their own case.
- **Deprecate**, **Reinstate** and **Archive** need `QA_LEAD` or higher and a recorded reason; **Flag stale** needs a reason too. Decisions need review notes.
- Editing an approved or active definition invalidates the old approval and moves the case to `needs_update`.
- Every save and transition creates an immutable version snapshot and an audit entry with the transition, reason, actor and effective policy.
- `approved` cases remain selectable by existing test-plan workflows; new workflows should activate approved cases before execution.
- AI review is advisory evidence: it creates its own completed AI-review record and never counts as a human review.

The lifecycle is controlled by the **`test_case_lifecycle_v2`** feature flag, which ships **on**. With it off for a project, the direct transition API is not exposed (it answers `404`), the **Lifecycle** tab disappears, and the older request-review / review-action / **Delete** paths remain (Delete deprecates the case). Those older paths enforce the same row-locked policy.

## Ownership: who gets the failure?

Two layers decide the owner of anything that fails:

1. **Suite owners** — set per suite on **Test cases → Suites** (`QA_LEAD`). A suite with no owner falls back to the project's default QA lead.
2. **Ownership rules** (**Settings → Project → Ownership**, `/ownership`, `QA_LEAD`) — pattern rules mapping tests to a service and team by **Suite Name**, **Component / Class**, **Package**, **Full Path** or **Feature / Epic Label**, with glob patterns and a priority (higher first). **Import CODEOWNERS** (paste it, or fetch it from GitHub) creates path rules and replaces the previously imported ones.

Team names on ownership rules are also what **Team channels** route alerts by.

### Auto-assignment (what happens on every run)

When a run is finalised, every `FAILED`/`BROKEN` test result that is still unassigned gets an assignee from this chain, first match wins:

```text
the suite's owner
  → the owner from a path or CODEOWNERS rule
  → one of the project's QA leads (spread by a stable hash; includes the default QA lead)
  → the project's manager (legacy fallback)
  → one of the administrators (spread by a stable hash)
  → unassigned
```

Assignment lands the failure in the assignee's **Inbox**. Two guarantees:

- **It never overwrites a human decision.** Auto-assignment only fills rows that are currently unassigned — a failure you reassigned by hand is not taken back by a re-run or recovery pass.
- **It cannot break ingestion.** Assignment runs as an isolated finalisation step; if it fails, the run still lands.

Reassigning by hand (**Reassign** in the Inbox) needs `QA_LEAD` and can only target the suite owner or a `QA_ENGINEER` member of the project.

### The default QA lead

Every project gets a **default QA-lead** account automatically when it is created (`qalead-<project slug>`, role `QA_LEAD`, with a random password that is never shown), so auto-assignment always has a destination. Practical consequences:

- Many unrouted failures sit on that synthetic account. QA leads' Inbox opens on the **team** scope for this reason.
- Configure real suite owners and ownership rules to route future failures to people.
- An administrator can choose a different default QA lead under **Members & access**; a QA lead can reset the synthetic account's password there if someone needs to sign in as it.

## Recommended setup order

1. Set suite owners for your top suites.
2. Add ownership rules for families of suites, or import CODEOWNERS.
3. Add team channels for the teams those rules name.
4. Verify with the next run: failures should appear in the right Inboxes (the team scope shows anything that fell through to the QA-lead pool).

## Observed tests and promotion

An **observed** (canonical) test is the deduplicated cross-run identity of a test your runs reported; an **authored** test case is one someone wrote. Promoting an observed test (`QA_ENGINEER`) links it to an existing authored case with the same fingerprint when there is exactly one, and otherwise creates a draft and copies the fingerprint. Multiple matches are a conflict to resolve, never a guess. A wrong link can be removed with a reason; neither record is deleted.

When automation disappears across the configured observation window, the observed test enters the orphan queue; a QA lead can confirm the retirement with a reason, and a reappearing test leaves the queue automatically. Nothing is hard-deleted: rows keep their status and the run in which they disappeared, so "this test was removed" can be told apart from "this test stopped being reported".

## Related

- [Issue trackers and defect automation](defects.md)
- [Gate policies and CI gating](release-gates.md)
- [Administration & settings](administration.md)
