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

The list of test cases, filterable by status, type, priority and suite, and searchable. **Automation tests** shows the observed tests too. A **Library health** banner scores the catalogue (**HEALTHY**, **NEEDS ATTENTION** or **AT RISK**) from its cases, reviews waiting, automation and stale drafts; **Insights** explains it. Below the list, evidence gaps: authored cases that have never been executed, cases whose automation has vanished, and automation retirements waiting for a QA lead to confirm.

- **New test case** writes one by hand.
- **AI Generate** drafts test cases from a description (**Generate & Save**) or from your documents (see [Generating from documents](#generating-from-documents)).
- **Export** downloads the catalogue as Excel.

Click a case to open it: **Details**, **Lifecycle**, **History**, **Reviews**, **Comments** and **AI Review** (an advisory review by the AI, recorded separately; it never counts as a human review).

### Authored test cases and reviews

Authored cases follow a governed lifecycle: **draft → review requested → under review → approved → active**, with **needs update**, **rejected**, **deprecated** and **archived** along the way. The **Lifecycle** tab shows only the actions you can take now; a disabled action says why.

| Action | From | Who |
|---|---|---|
| **Request review** / **Withdraw review** | draft, rejected / review requested | Anyone who can edit test cases |
| **Claim review** | review requested | `QA_ENGINEER` or higher |
| **Unclaim review** | under review | The reviewer who claimed it, or a QA lead |
| **Approve** | under review | The reviewer who claimed it, never the case's author. Needs review notes |
| **Reject**, **Request changes** | under review | The reviewer who claimed it. Needs review notes |
| **Activate** | approved | Anyone who can edit test cases |
| **Flag stale** | approved, active | Anyone who can edit test cases, with a reason |
| **Revise** | rejected, needs update | Anyone who can edit test cases |
| **Deprecate** | draft, rejected, approved, active, needs update | `QA_LEAD` or higher, with a reason |
| **Archive** | deprecated | `QA_LEAD` or higher, with a reason |
| **Reinstate** | deprecated, archived | `QA_LEAD` or higher, with a reason |

Editing an approved or active case moves it to needs update, because the old approval no longer covers it. Every save and transition keeps a full version and an audit entry.

Cases waiting for review are on the **Approvals** tab here and in your [Inbox → Approvals](/docs/inbox#test-cases).

The lifecycle is controlled by the `test_case_lifecycle_v2` feature flag, which is on by default. With it off, the **Lifecycle** tab disappears, **Details** offers **Request Review**, and the row action is **Delete** (which deprecates the case) instead of **Deprecate**.

### Plans, strategy and duplicates

- **Plans** — group cases into a test plan for a release or initiative (**New Plan**, or **AI Create Plan**), and export a plan to Word or PDF.
- **Strategy** — **Generate Strategy** drafts a test strategy with AI: objective, scope, test types, risk assessment, entry and exit criteria, environments and automation approach. Export to Word or PDF.
- **Duplicates** — likely duplicate test cases, banded **Exact**, **Strong** or **Possible**, which you merge or dismiss. Needs one project.
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
