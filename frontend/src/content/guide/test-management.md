# Test management

Working with tests as durable objects rather than rows in one run: suites, the observed tests your runs report, the test cases your team writes and reviews, and who owns what.

TestLookup keeps two related catalogues:

- **Observed tests** (on **Suites**): every test your runs have reported, identified by its [fingerprint](/docs/concepts) across runs. You do not create these; ingestion does.
- **Authored test cases** (on **Test cases**): test cases your team writes, reviews and plans, automated or manual. An observed test can be promoted into one.

> **Note.** Rename a test and its history stays with the old fingerprint. The new name starts fresh. If a test's history disappears after a refactor, this is usually why.

## Suites

**Suites** in the sidebar (`/suites`, titled **Test Suites**) lists the project's suites: name, description and number of test cases, and with one project selected, each suite's pass rate (the share of its tests whose latest result passed), last run, executions, failing tests and owner. A suite with no owner shows the project's default QA lead as **(project default)**. The **Default** suite catches tests ingested without a suite name.

- **New suite** (`QA_ENGINEER` or higher) creates an empty suite with a name and description.
- The star, **Make default**, makes a suite the project's default suite (`QA_LEAD` or higher).
- The bin deletes a suite once its tests have been moved out (`QA_LEAD` or higher).

A suite's name comes from your reports where present and is inferred otherwise; see [Getting results in](/docs/ingestion#suite-name-resolution).

When a suite's latest run no longer reports some of its tests, the suite sync moves them aside as **Deleted from Suite (N tests need review)** on the **Suites** tab of **Test cases**. The **Coverage map** shows the same count on the suite (`3 need review`, in its tooltip and its table), and its **Open the tests that need review** action opens that list (`/test-management?tab=suites&suite=<suite>`).

A suite you create is listed at once, before any run reports it: on the **Suites** tab of **Test cases** (one project selected, no release filter) its card carries an **Empty** badge and reads "Waiting for its first run: no result reports this suite yet.", with its owner and no counts. The **Default** suite is never shown as empty: it is the catch-all for tests ingested without a suite name.

### Editing a suite

**Edit** (the pencil on a suite's row on **Suites**, or **Edit suite** on the suite's own page; `QA_ENGINEER` or higher) opens the suite's form in the page: **Name**, **Description** and **Tags** (comma-separated). **Save** sends only what you changed. Renaming has two consequences the form states before you save:

- Past runs keep the name they reported: their results stay under the old name on the **Suites** tab of **Test cases**.
- A run that still sends the old name creates a suite of that name again for tests new to the project. Rename the suite in your reporter's configuration too.

An explicit owner moves with a rename (the new name gets the same owner when it has none; the old name keeps its owner for the runs that still report it). Every change is recorded in the project's activity log as `Suite <name> changed: <fields>`.

On a suite's **Tests** tab, the latest-run cell shows the run's id and, under it, when that run happened.

### A suite's page

Click a suite to open it (`/suites/<id>`). The KPIs for the window are **Tests run**, **Executions**, **Pass rate**, **Failed** and **Avg duration**. Three tabs:

- **Tests** — each test's pass rate, executions, average duration, last result and status. Click a test for its detail page. **Move** (`QA_ENGINEER` or higher) moves a test to another suite; tick several and use **Move** in the selection bar to move up to 200 at once.
- **Runs** — the suite's runs in the window, with passed, failed and skipped counts.
- **Charts** — run history, the pass-rate trend, test results by run, and test duration against failure rate. Old `/coverage/suite?name=<suite>` links open here.

### An observed test's page

From a suite, click a test to open `/canonical-test-cases/<id>`: its status, source, suite and fingerprint, a history timeline, and **Run history**. **Open latest run** goes to its most recent result.

**Promote** (`QA_ENGINEER` or higher) turns an observed test into an authored test case. If exactly one authored case in the project has the same fingerprint, the two are linked; otherwise a new draft is created. Promotion is refused when the test is already promoted, has no fingerprint, or matches several authored cases (resolve those first). **Unlink managed case** undoes a wrong link, with a reason; neither record is deleted.

Nothing here is hard-deleted. A test that stops being reported keeps its history and the run in which it disappeared, so "this test was removed" can be told apart from "this test stopped being reported".

## Test cases

**Test cases** in the sidebar (`/test-management`, titled **Test Case Management**) is the authored catalogue. It is read-only in **All Projects**. The tabs are **Cases**, **Suites**, **Plans**, **Approvals**, and under **More**: **Strategy**, **Duplicates**, **Audit log** and **Knowledge**.

### Cases

The list of test cases, filtered and searched from the bar above it (see [Filters and views](#filters-and-views)). **Automation tests** shows the observed tests too. A **Library health** banner scores the whole catalogue (**HEALTHY**, **NEEDS ATTENTION** or **AT RISK**); **Insights** explains it (see [Library health](#library-health)). Below the list, evidence gaps: authored cases that have never been executed, cases whose automation has vanished, and automation retirements waiting for a QA lead to confirm. Deprecated and archived cases are left out of the evidence gaps.

- **New test case** writes one by hand (`TESTER` or higher).
- **AI Generate** drafts test cases from a description (**Generate & Save**) or from your documents (see [Generating from documents](#generating-from-documents)); `TESTER` or higher. The drafts are marked with the AI sparkle and land in **AI drafts**.
- **Import** reads test cases from a CSV or Excel file (`TESTER` or higher, one project; see [Importing cases](#importing-cases)).
- **Export** downloads the cases the list shows (its filters and search apply) as Excel.

Click a case to open it: **Details**, **Timeline** (see [A case's timeline](#a-cases-timeline)), **Lifecycle**, **History**, **Reviews**, **Comments** and **AI Review** (an advisory review by the AI, recorded separately; it never counts as a human review). A viewer reads all of it but changes nothing: there is no **New test case**, **AI Generate**, **Edit**, status move, selection box, comment box or **Run AI Review** below `TESTER`, and the API refuses those writes too.

**Edit** (in the case's **Details**, `TESTER` or higher, one project selected) opens the case's form in place of its details: title, objective, description, preconditions, steps (add, move up or down, remove; a step with no action is dropped and the rest renumbered), the overall expected result, test data, parameters, type, priority, severity, feature area, suite, tags, owner, estimated duration, automation, and an optional change summary. **Save changes** sends only the fields you changed and saves a new version (`Test case saved (v2)`). Editing never changes the status. A masked parameter's value is never shown: leave it empty to keep it, or type a new value to replace it. A deprecated or archived case cannot be edited until it is reinstated, and a case from automation is promoted before it is edited.

If someone saved the case after you opened it, nothing is overwritten. The form says who changed it and what (for example "Changed by Priya Shah since you opened it (v1 → v3). Changed: Title"), and you choose **Reload theirs** (start again from their version; what you typed is dropped) or **Keep editing** (your changes on top of their version: the next **Save changes** keeps theirs for every field you did not touch and writes yours for the fields it lists under "You also changed").

The **Details** toolbar also offers the status moves you can make now, named by where they take the case: **Make active**, **Back to draft**, **Request review**, **Withdraw review**, **Approve my case…**, **Needs update…**, **Deprecate…**, **Archive…** and **Reinstate…**. A move ending in "…" asks for a reason first, recorded in the audit trail. A line under the toolbar says whether the project requires review before Active (see [Authored test cases and reviews](#authored-test-cases-and-reviews)). The **Lifecycle** tab keeps the full list of lifecycle actions and says why each unavailable one is unavailable.

**History** lists every version, newest first: who saved it (marked **AI** when an AI generation wrote it and **API** when an API key did), its summary, and what changed, field by field and step by step. **Compare with current** shows how a version differs from the case now. **Restore this version** (`TESTER` or higher) saves that version's content as a new version; the status and the owner stay as they are, and nothing is removed from the history. Each version also lists the plan executions recorded against it.

**Bulk edit.** With one project selected (`TESTER` or higher), tick cases in the list (the box in the header ticks every case on the page) and the bulk bar opens above the list: **Change status**, **Owner**, **Priority**, **Add tags**, **Remove tags** or **Move to suite**, then **Apply**. Every case is changed on its own, with its own version and audit entry, and the bar reports the result (for example `2 changed · 1 skipped`) with each skipped case and why: it changed since you selected it, the move is not available from its status, or it is deprecated. **Deprecate**, **Archive** and **Reinstate** are offered only to QA leads and administrators. **Ctrl+E** (**⌘E** on a Mac) opens the bar, as the list's footer says. The selection is the cases you can see: changing the page, a filter, the search or the view clears it.

### Filters and views

The bar above the list holds the search (title, tags or ID; with requirement traceability on, requirement keys too), **Automation tests**, and five filters: **Status**, **Type** and **Priority** (pick several values each), **Suite** and **Owner** (one each). **Owner** offers **Me** and the project's members; it is absent in **All Projects**. A case's owner is its assignee, else its author.

Every filter is kept in the page's address (`?status=draft,review_requested&owner=me`), so a reload, a bookmark or a link you send someone opens the same list. Changing a filter goes back to the first page and clears the bulk selection. When a filter can only be answered from authored cases (an owner, an automation state, a requirement, or several values of one filter), the observed tests are left out and a line under the bar says so: "Authored cases only: automation-ingested tests are left out under these filters."

The **Views** row under the list's title applies a set of filters in one click, replacing the current ones:

- **My drafts** — my cases in Draft.
- **My P0/P1** — my critical and high priority cases.
- **Unautomated** — cases not marked automated.
- **AI drafts** — AI-generated cases still in Draft (accept them by making them Active, one at a time or in bulk).

**Save view** keeps the current filters under a name, for you or, with **Share with** the project ticked, for every member of it. **Manage** renames, shares, stops sharing or deletes your own views; a view another member shared can be applied but not changed. In a shared view, **Me** means whoever applies it: a shared "my drafts" shows each reader their own.

### Library health

With one project selected, the **Library health** banner and the **Insights** cards are computed by the server over the project's whole authored catalogue (deprecated and archived cases aside), read once when the page opens. In **All Projects** the banner reads "Choose a project: library health is per project." A project with no authored case reads "No authored test cases yet" instead of a score: an empty library is not measured, never 0.

The score (0 to 100) weighs four components; each shows both of its numbers on the **Library health** card (for example `Automated 38 · 13 of 34 cases automated (target 60 %)`):

| Component | Weight | Score |
|---|---|---|
| **Automated** | 35 % | the share of cases marked automated |
| **Stale drafts** | 20 % | 100 − 400 × the share of drafts untouched for 30 days or more |
| **Review freshness** | 20 % | 100 − 100 × the oldest review's wait in days ÷ 14 (100 with nothing waiting) |
| **Retired cases in open plans** | 25 % | 100 − 400 × the share of deprecated or archived cases still in a plan that is not completed or archived |

**Healthy** is 85 or more, **Needs attention** 70 or more, **At risk** below. The automation target is a project setting (60 % unless changed); a QA lead or administrator changes it with **Change target** in the **Library health** card.

**Insights** holds the **Library health** card, **Review queue · N** (the five cases waiting longest, oldest first; the **Approvals** tab has the rest), **Strategy gaps**, **Recent activity** and **Automation coverage**. The strategy gaps, each shown only when it has something in it:

- **Deprecated cases in active plans** — retired cases still items of an open test plan.
- **Aging reviews over SLA** — the oldest review has waited 7 days or more.
- **Automation below target** — the automated share is under the project's target.
- **Stale drafts ≥ 30d** — drafts untouched in the last month.
- **Flaky and active** — approved or active cases whose automated test is flaky: a flaky score of 0.5 or more with a confidence above low, the same bar as the [Flaky tests](/docs/flaky#the-flaky-tests-page) page. A case's automated test is the observed test promoted from it, else the one with its fingerprint, in the same project. When no score has been computed yet, or no approved or active case has an automated test, the row says which instead of a count.
- **No owner** — cases nobody active owns: no assignee and no author, or an owner whose account is deactivated.

**Show 5** opens the first five of a list in place; **Show all N** pages through the rest, 25 at a time.

### Importing cases

**Import** (beside **Export**; `TESTER` or higher, one project selected) reads a CSV file (UTF-8) or an Excel workbook (`.xlsx`, its first sheet), up to 5 MB and 2,000 rows. **Download the CSV template** gives the columns it reads: **Title** (required, 3 to 500 characters), **Type**, **Priority**, **Severity**, **Feature Area**, **Objective**, **Preconditions**, **Steps**, **Expected Result**, **Automated**, **Automation Status**, **Tags**, **Suite**, **Description** and **Test Data**. Headers are matched without regard to case. An **Export** file imports as it is: its other columns (**Status**, **AI Generated**, **AI Quality Score**, **Last Execution Status**, **Version**, **Created At**, **ID**) are listed as ignored, and any column the import does not know is listed as unknown and never imported.

Choosing a file runs a check first and writes nothing: a summary (`20 rows: 17 to create · 0 duplicates · 3 invalid`), then every row with what would happen to it. **Import 17 cases** then creates the valid rows, each as a **Draft** at version 1, owned by you, with its own version and audit entry; one entry in the project's activity log records the import (`Imported 17 test cases from cases.csv`). A row that fails while saving is reported and the others are still created.

- **Priority**: critical, high, medium or low, or P0 to P3 (P0 is critical). Blank is medium.
- **Severity**: blocker, critical, major, minor or trivial; any other value is kept as written, with a warning. Blank is major.
- **Type**: up to 50 letters, digits, spaces, `-` or `_` (blank is functional). **Automated**: Yes or No. **Automation Status**: not_automated, in_progress, automated or broken.
- **Tags**: separated by commas or semicolons, at most 20, each up to 100 characters.
- **Steps**: the export's own form (`Step 1: open the basket; Step 2: pay`), or one step per line.
- **Status** is never applied: every case starts as a Draft, and a row that named another status says so (its audit entry records it).
- **Duplicates**: a row with the same title, objective, steps and expected result as a case already in the project, or as an earlier row of the file, is not imported; the preview names the case or the row.

Imported text is kept as it was typed. A cell starting with `=`, `+`, `-` or `@` is stored as text, never as a formula, and every Excel export writes it back with a leading `'` so a spreadsheet does not evaluate it; the import removes that one `'` again, so an export imports unchanged.

### A case's timeline

An authored case's **Timeline** tab lists everything that happened to it, newest first: every saved version (who saved it, and whether a person, an AI generation or an API key did), each review request and decision with its notes, comments, every plan execution (with the case version the latest one ran against; an older one says "version not recorded"), each run of its automated test with the build and branch, the defects raised on those runs, and each requirement linked or unlinked. The chips narrow it: **All**, **Changes**, **Reviews**, **Comments**, **Executions** and **Defects**. **Load more** reads further back; "Start of the timeline." marks the end.

The automated runs and defects are those of the case's automated test (the observed test promoted from it, else the one with its fingerprint) in the case's own project only. A case with no automated test says "No automated test is linked to this case; plan executions are shown." An entry with nobody recorded reads "by not recorded". **History**, **Reviews** and **Comments** stay as they are: **History** is where versions are compared and restored.

### Requirements

With the `test_case_requirement_traceability` feature flag on for the project, a case's **Details** end with **Requirements**: the requirement keys someone typed on it (for example `REQ-PAY-07`), each with an optional title, its link type (**verifies**, **partly verifies** or **related**) and who added it. **Add requirement** and **Remove** need `TESTER` or higher, one project selected and a case that is not deprecated or archived. Keys are compared after brackets around the key are dropped, spaces collapsed and letters upper-cased, so `[REQ-12]`, `(req-12)` and `REQ-12` are one key; a case links each key once. Clicking a key filters the list to the cases that link it (the **Requirement:** chip under the filter bar removes the filter).

A **Requirement coverage** card joins **Insights**: **Requirements verified** `12 of 15` (always both numbers). A project's known requirements are the keys typed on its cases, the requirement ids of its generation batches and the requirement ids found in its active knowledge sources; a requirement is verified when at least one case that is not deprecated or archived **verifies** it. The gaps say why: "No case links it.", "Only "related" links: no case verifies it." or "Only partly verified.", and where the key is known from (a generation batch's claim that cases cover it is shown, but only a link counts). **Show all gaps** pages through them; the card also counts the approved or active cases that link no requirement. With no key known yet the card says "No requirement keys are known to this project yet: add one to a test case."

This is traceability "lite": no requirement matrix, no export, no Jira look-up. The flag is on for a new installation and off for an upgraded one; an administrator turns it on under [Feature flags](/docs/administration#feature-flags). With it off, none of this is shown and the search placeholder does not mention requirements.

### Authored test cases and reviews

Authored cases follow a lifecycle: **draft → review requested → under review → approved → active**, with **needs update**, **rejected**, **deprecated** and **archived** along the way. The **Details** toolbar offers the moves you can make now; the **Lifecycle** tab shows every action, and a disabled one says why.

Each project decides whether a case needs an approved review before it becomes Active: **Require review before a test case becomes Active**, in the project's **Edit** on **Projects** (`QA_LEAD` or higher). It is **off** by default, for every project.

- **Review off**: a case moves straight between Draft and Active (**Make active**, **Back to draft**), and a Needs update case can be made Active again. **Request review** stays available for a team that wants a second pair of eyes on one case.
- **Review on**: a Draft reaches Active only through a review. Request it, a reviewer claims and approves it, then make it Active. A QA lead or administrator who wrote the case may approve it themselves, with a reason (**Approve my case…**); a QA engineer cannot approve their own case.

| Action | From (review on) | Also, with review off | Who |
|---|---|---|---|
| **Request review** | draft, rejected; approved or active once edited after approval | — | `TESTER` or higher |
| **Withdraw review** | review requested | — | The author, or a QA lead |
| **Claim review** | review requested | — | `QA_ENGINEER` or higher |
| **Unclaim review** | under review | — | The reviewer who claimed it, or a QA lead |
| **Approve** | under review | — | The reviewer who claimed it, never the case's author. Needs review notes. A QA lead or administrator approving a case they wrote: from review requested (or under review, claimed by them), with a reason |
| **Reject**, **Request changes** | under review | — | The reviewer who claimed it. Needs review notes |
| **Activate** (**Make active**) | approved | draft, needs update | `TESTER` or higher |
| **Flag stale** (**Needs update…**) | approved, active | — | `TESTER` or higher, with a reason |
| **Revise** (**Back to draft**) | rejected, needs update | active | `TESTER` or higher |
| **Deprecate** | draft, rejected, approved, active, needs update | — | `QA_LEAD` or higher, with a reason |
| **Archive** | deprecated | — | `QA_LEAD` or higher, with a reason |
| **Reinstate** | deprecated, archived | — | `QA_LEAD` or higher, with a reason |

A viewer can take none of these actions.

Editing never changes the status: an Active case stays Active when you edit it. With review on, editing the steps, the overall expected result or the preconditions of an approved or active case marks it **Edited after approval**: the **Details** toolbar says since which version, and **Request review** moves it to review requested until a reviewer approves the new definition (plans that already hold it keep it). Editing only the title, tags, priority, owner, suite or other details marks nothing. Every save and status change keeps a full version and an audit entry.

Cases waiting for review are on the **Approvals** tab here and in your [Inbox → Approvals](/docs/inbox#test-cases).

The lifecycle is controlled by the `test_case_lifecycle_v2` feature flag, which is on by default. With it off, the **Lifecycle** tab disappears, the **Details** toolbar offers no status moves (only **Edit**) and **Details** offers **Request Review**, the bulk bar has no **Change status**, and the row action is **Delete** (which deprecates the case) instead of **Deprecate**.

### Plans, strategy and duplicates

- **Plans** — group cases into a test plan for a release or initiative (**New Plan**, or **AI Create Plan**), and export a plan to Word or PDF. **Link Suite** adds the suite's Active (approved or active) manual cases; tick **Include cases that are not Active** to add its other manual cases too. Deprecated and archived cases are never linked, and the toast says how many were left out. **AI Create Plan** picks approved and active cases only. Recording a result remembers the version of the case it ran against: the **Ran against** column shows it (`v3 · case now v5` once the case has changed since), and the version opens the steps as they were when it ran. A result recorded before this was tracked says "not recorded".
- **Strategy** — **Generate Strategy** drafts a test strategy with AI: objective, scope, test types, risk assessment, entry and exit criteria, environments and automation approach. Export to Word or PDF.
- **Duplicates** — likely duplicate test cases, banded **Exact**, **Strong** or **Possible**, which you merge or dismiss. Needs one project. A pair whose case was deprecated or archived leaves the list and its count, and comes back if the case is reinstated.
- **Suites** — the case count of a suite leaves out its deprecated and archived manual cases; the suite's own list still shows them.
- **Audit log** — every change to the catalogue: when, what, who.

### Generating from documents

**Knowledge** (and **AI Generate → From documents**) drafts test cases grounded in documents you register as knowledge sources: each draft cites the passages it came from, and arrives for review rather than straight into the catalogue. It needs **Knowledge RAG** turned on in the AI configuration and a reachable language model; otherwise the tab says "Knowledge RAG is not enabled". How sources are registered and kept fresh is in the operator guide in the repository (`user-guide/knowledge-base.md`).

## Ownership and assignment

- **Suite owners** are set per suite on the **Suites** tab of **Test cases** (**Add test suite** or **change** next to the owner; `QA_LEAD` or higher).
- **Ownership rules** (**Admin → Project → Ownership**) map tests to teams by suite name, component or class, package, path or feature label, with glob patterns and priorities, and can be imported from a CODEOWNERS file.
- **Assignment** puts a specific failure in someone's [Inbox](/docs/inbox). New failures are assigned automatically to the suite owner, else a path or CODEOWNERS owner, else one of the project's QA leads; see [How failures get assigned](/docs/inbox#how-failures-get-assigned).
- **Team channels** (**Admin → Project → Team channels**) send each owning team's alerts to that team's own Slack, Teams or email channel.

Failures that fall through to the project's synthetic default QA-lead account are why a QA lead's **Inbox** opens on the team view.

## Project scope

Everything here is project-scoped. Suites, ownership and assignments never cross a project boundary.

## Related

- [Your inbox and approvals](/docs/inbox)
- [Investigating failures](/docs/failure-analysis)
- [Concepts and terminology](/docs/concepts)
