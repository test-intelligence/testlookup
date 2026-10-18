# Test catalog governance and ownership

The decisions a QA lead or administrator makes about the test catalogue: the authored test-case lifecycle and its flag, ownership rules, the automatic assignment of failures, and the default QA lead. Day-to-day use of **Suites** and **Test cases** (moving tests, promoting an observed test, reviewing a case, plans, strategy, duplicates) is in the in-app guide: **Test management** (`/docs/test-management`, source [test-management.md](../frontend/src/content/guide/test-management.md)).

## Authored test-case lifecycle

Authored cases follow one server-enforced state machine. The governed table (a project that requires review):

```text
draft → review_requested → under_review → approved → active
  ↑             ↕              │            │         │
  └── revise/rework ← rejected ─┘            └─────────┴→ needs_update → draft

draft | rejected | approved | active | needs_update → deprecated → archived
deprecated | archived → draft (reinstate)
```

**Require review before Active** is a per-project setting (`projects.require_case_review`; **Projects → Edit**, `QA_LEAD` or higher; the `PUT /api/v1/projects/{id}` field `require_case_review`, and its `project.updated` activity entry names it). It is **off** for every project by default, existing projects included, so an upgrade changes how existing projects behave until a QA lead turns it on:

- **Off**: the table above plus `activate` from `draft` and `needs_update`, and `revise` from `active`. A case moves straight between Draft and Active; `request_review` stays available and optional.
- **On**: the table above exactly. In addition, a `QA_LEAD` or `ADMIN` who wrote the case may approve it themselves with a reason (`approve` from `review_requested`, the claim folded into the same version, or from `under_review` when they hold the claim; the reason, or the review notes, is required). A `QA_ENGINEER` never approves their own case. The audit row's policy snapshot records `review_required` and `self_approval`.

The API reports the actions available to the signed-in user (`allowed_actions` on each case, and the subset that needs a reason for that user in `reason_required_actions`; each `…/allowed-transitions` item carries `requires_reason`), the project's setting (`review_required` on each case), and the **Details** toolbar and the **Lifecycle** tab show only those. The rules:

- Every write to a test case, its comments, its review or its AI notes needs `TESTER` or higher, checked by the server before any work: create, edit, comment, request review, the transition route, the legacy review action, AI generation and AI review. A `VIEWER` gets `403 Requires at least TESTER role` and reads everything. The legacy `DELETE` (deprecate) needs `QA_ENGINEER` at the route and the lifecycle's own rule below.
- Claiming a review and deciding it (approve, reject, request changes) need `QA_ENGINEER` or higher, and only the reviewer who claimed it can decide. An author cannot approve their own case, except a lead's self-approval above.
- **Deprecate**, **Reinstate** and **Archive** need `QA_LEAD` or higher and a recorded reason, as before; **Flag stale** needs a reason too. Decisions need review notes.
- **Editing never changes the status.** A save (`PATCH /api/v1/test-management/cases/{id}` with the `expected_version` the editor read, and only the changed fields) bumps the version and snapshots it in every state except deprecated and archived (`409`). A stale `expected_version` answers `409` with `{current_version, expected_version, message}`; the page re-reads the case and its history and shows the reader who changed what, and never retries by itself.
- With review on, an edit that changes `steps`, `expected_result` or `preconditions` of an `approved` or `active` case sets `edited_since_approval_version` (the case answers `edited_after_approval: true`) and adds `request_review` from `approved` and `active`. Approving the case, or moving it out of approved / active, clears the marker; metadata edits never set it; with review off it is never set.
- Masked parameter values (`masked: true`, or mode `masked` / `hidden`) read `null` in every `/cases` and `/history` answer. A `PATCH` that sends a masked row with `value: null` keeps the stored value; a typed value replaces it.
- Every save and transition creates an immutable version snapshot and an audit entry with the transition, reason, actor and effective policy. Versions written from this release on record their `source` (`person`, `ai` or `api`, from the credential that made the change; `null` = not recorded before the upgrade, except an AI-generated case's first version) and the owner (`assignee_id`); the history answers each version's `changed_by_name` and the plan executions recorded against it (`executions`).
- `POST /api/v1/test-management/cases/{id}/restore` `{ version, expected_version }` (`TESTER`) saves an older version's content and classification as a new version (`change_type: restored`); status, owner and fingerprint are never restored. Restoring the current version is `422`; identical content writes nothing; a stale `expected_version` is the same `409` as an edit. It records `test_case.restored` in the activity log.
- `POST /api/v1/test-management/cases/bulk` (`TESTER`; the body's `project_id` is checked first) applies one action (`transition`, `set_owner`, `set_priority`, `add_tags`, `remove_tags`, `move_to_suite`) to up to 200 case ids of that project, each in its own savepoint with its own version and audit row. It answers `200` with a result per id (`changed`, `unchanged`, `skipped` with a reason, `not_found`, the last also for another project's id) and records one `test_case.bulk_changed` activity entry when anything changed. A retire move (`deprecate`, `archive`, `reinstate`) below `QA_LEAD` refuses the whole request (`403`); a stale `expected_versions` entry skips that case, never overwrites it.
- New test plans pick Active cases: **AI Create Plan** takes `approved` and `active` cases, and **Link Suite** links those by default (the others only when asked, deprecated and archived never). A plan item records the case version its latest execution ran against (`executed_case_version`; `null` for executions recorded before the upgrade), and the plan's items answer the case's current `case_version`.
- Deprecated and archived cases leave the duplicate review queue and its `open_count` (read-time: reinstating brings a pair back), both evidence-gap lists, and the **Suites** tab's manual counts. Listings (a suite's cases) keep them.
- AI review is advisory evidence: it creates its own completed AI-review record and never counts as a human review. AI-generated cases arrive as `draft` with `ai_generated` set; the **AI drafts** view lists them.

The lifecycle is controlled by the **`test_case_lifecycle_v2`** feature flag, which ships **on**. With it off for a project, the direct transition API is not exposed (it answers `404`, and so does a bulk `transition`), the **Lifecycle** tab and the status moves disappear, and the older request-review / review-action / **Delete** paths remain (Delete deprecates the case). Those older paths enforce the same row-locked policy with review required.

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
