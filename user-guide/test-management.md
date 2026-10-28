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
- **Comments and mentions.** Every comment (`POST /api/v1/test-management/cases/{id}/comments`) records a `test_case.commented` activity entry. A reply's `parent_id` must name a comment of the same case, else `404 Parent comment not found on this test case.` An `@username` in the text notifies that person **in the app only** (the bell; never by e-mail, Slack or Teams): an active member of the case's project, not a synthetic or service account and not the author, at most 10 per comment. Any other `@name` stays plain text. The answer lists who was notified (`mentions`: `user_id`, `username`, `display_name`; `[]` on every list read), the activity entry stores their user ids (`context.mentions`), and the bell entry links to the case (`/test-management?case=<id>`) and reads `IN APP`.
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

## Library health and the automation target

`GET /api/v1/test-management/health?project_id=` (every member; a project is required: `422 Choose a project: library health is per project.`) computes the **Test cases** banner and the **Insights** cards on the server over the project's whole authored catalogue, deprecated and archived cases aside; it replaced a client-side sample of 200 cases, so **the numbers change for any project with more than 200 authored cases**, and "retired cases in open plans" now measures plans (it read 0 before). It writes nothing and reads about eight bounded statements. Each component (`automation` 35 %, `stale_drafts` 20 %, `review_freshness` 20 %, `retired_in_open_plans` 25 %) answers its value with its numerator and denominator; a project with no authored case is `measured: false` with no score (never 0). The answer also carries the review queue's first five (with its total), the strategy gaps, and the **Flaky and active** and **No owner** lists' first five with their totals; their full lists page through `GET /api/v1/test-management/cases/evidence-gaps?kind=flaky_active|no_owner`.

- **Flaky and active** uses the product's one flaky bar (`flaky_score.score >= 0.5`, confidence not `none` / `low`) on the case's automation identity: the promoted canonical's fingerprint, else the case's own, matched in the case's project only (fingerprints are not project-salted). No score row for the project, or no approved / active case with an identity, is reported as not measured with the reason.
- **No owner**: neither an assignee nor an author, or an owner whose account is deactivated.
- The automation target is `projects.automation_target_pct` (0-100, default 60; migration `0228`), written through the existing `PUT /api/v1/projects/{id}` (`QA_LEAD`); its `project.updated` activity entry names the field.

## Case-list filters and saved case views

`GET /api/v1/test-management/cases` and the Excel export take `status`, `test_type` and `priority` repeatedly (at most 20 values each), `owner` (`me`, or a member's user id with a project: someone outside the project is a `422`), `automation` (`automated` / `not_automated`) and, with traceability on, `requirement` (a normalised key). Any of the new filters answers authored cases only and says so (`automation_rows_excluded: true`). The export honours the same filters and appends the columns `ID`, `Suite` and `Description` (19-21; columns 1-18 are unchanged, so sheets built on them keep working). Saved case views are `saved_views` rows with `page: test_management` and `filters.kind: test_case_view` (their keys are validated: statuses, priorities, types, owner, automation, suite, q, ai_generated, requirement); a shared one belongs to one project, and its `owner: me` means the reader.

## Importing test cases

`POST /api/v1/test-management/cases/import?project_id=&dry_run=` (`TESTER`; multipart `file`) reads CSV (UTF-8, `,` `;` or tab) or `.xlsx` (the first sheet; formulas are read as their cached values, external links are not followed). `dry_run` defaults to `true` and writes nothing. The caps are checked before parsing goes further: 5 MB while reading (`413`), a workbook expanding past 50 MB (`422`, checked before the workbook is opened), 2,000 rows (`422`). Every case is created as a Draft through the normal create path (membership, suite resolution, version 1, audit entry), one savepoint per row, and the request commits once; the `Status` column is never applied (an audit entry records the override). Duplicates (the same title, objective, steps and expected result as an eligible case of the project, or an earlier row) are not created. One `test_case.imported` activity entry records the file name and the counts.

Imported text is untrusted and stored as typed: a cell starting `=`, `+`, `-`, `@`, tab or CR stays text; the Excel export escapes it with a leading `'` and the import removes exactly that one `'`, so export → import round-trips. A 2,000-row apply takes about 12 seconds on a local PostgreSQL (about 8 statements per row); if it ever exceeds 30 seconds on your hardware, split the file.

## The case timeline

`GET /api/v1/test-management/cases/{id}/timeline?kinds=&cursor=&limit=` (any member of the case's project; `limit` 1-100, default 50) merges eight kinds: `version`, `review_requested`, `review_decided`, `comment`, `plan_execution` (every execution from the audit trail, not just the latest), `automated_execution` and `defect` (through the case's automation identity, in its project only; never `test_case_history`, which has no project column), and `requirement` (the link / unlink activity entries). It is keyset-paged newest first: `next_cursor` continues it and is valid for that case only (`422` otherwise); one bounded statement per kind.

## Requirement traceability (lite)

Owner decision D10: manual requirement keys on authored cases, the uncovered-requirement gaps and the "Requirements verified N of M" stat; no matrix, no export, no Jira, no automatic backfill. Behind the **`test_case_requirement_traceability`** flag (migration `0229`): **on for a new installation, off for an upgraded one** — turn it on with `PATCH /api/v1/feature-flags/test_case_requirement_traceability` (or **Settings → Feature flags**); while it is off every route below answers `404 Requirement traceability is off for this project.` and the page shows nothing of it.

- `GET` / `POST /api/v1/test-management/cases/{id}/requirements` and `DELETE …/requirements/{requirement_link_id}` (writes `TESTER`; a deprecated or archived case is `409 Reinstate it to edit it`). A key is 1-200 characters without control characters, stored as typed beside its normalised form (one pair of surrounding brackets dropped, whitespace collapsed, upper-cased; unique per case). Each link and unlink records `test_case.requirement_linked` / `_unlinked`. Links do not bump the case version.
- The known keys of a project are its links' keys, its generation batches' `requirement_coverage` ids and its active knowledge chunks' extracted `requirement_id`s (each source capped at 5,000 distinct keys; beyond that the counts are lower bounds and say so). A key is verified by a `verifies` link from a case that is not deprecated or archived. `GET /api/v1/test-management/traceability/gaps?project_id=` pages the gaps (`no_link`, `related_only`, `partly_verified`) and lists the approved / active cases that link nothing; the health answer carries the same summary.

## Editing a suite

`PATCH /api/v1/suites/{id}` (`QA_ENGINEER`, unchanged) is now reachable from the UI (**Suites** row **Edit**, and **Edit suite** on the suite page). A change records `suite.updated` with the fields that changed. A rename copies an explicit suite owner to the new name when it has none (owners are keyed by suite name, and the old name keeps its row), and a run that still reports the old name creates a suite of that name again for tests new to the project: rename the suite in the reporters' configuration as well. A first-class suite no run has reported yet is listed on **Test cases → Suites** as **Empty** (one project, no release filter; never the default suite).

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
