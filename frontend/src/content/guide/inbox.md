# Your inbox and approvals

**Inbox** (`/my-failures`) is your personal queue: the failures assigned to you, and the decisions waiting for you. It has two tabs, **Assigned to me** and **Approvals**. The badge on **Inbox** in the sidebar counts your assigned failures. The old `/reviews` address opens the **Approvals** tab.

## How failures get assigned

When a run finishes, every failed or broken test that nobody owns yet is assigned automatically, to the first of:

1. the **suite owner** set for its suite;
2. the owner from a **path rule or CODEOWNERS** match;
3. one of the project's **QA leads**, spread evenly (this includes the project's default QA-lead account);
4. the **project manager**;
5. one of the **administrators**, spread evenly.

If none exists, the failure stays unassigned. A failure you reassigned by hand is never taken back by a later run. Suite owners and ownership rules are set by QA leads; see [Test management](/docs/test-management#ownership-and-assignment).

## Assigned to me

The list shows the failed and broken tests assigned to you that are still **Pending review**, within the window you pick (24h, 7d or 30d). It refreshes every 30 seconds.

Each row shows the test (with its error, the step it failed at, and why it was assigned to you when a CODEOWNERS or path rule did it), the suite, the run, how many times it failed, and how long it has waited. Click a row to open the test result. Empty, the tab says **You're caught up**.

**QA leads and administrators** also get a **Scope** filter: **mine** (assigned to you) or **team** (every unresolved failure in the project). For them **team** is the default and the tab reads **Team failures**, because most automatic assignments land on QA-lead accounts and an individual "mine" list can look empty.

### Resolving a failure

**Update status** sets what you found. Anything other than pending review removes the row from the inbox:

| Status | Use it when |
|---|---|
| **Reviewed & approved** | You looked; the failure is understood or expected. No further action |
| **Defect created** | It is a real product bug and a defect has been filed (see [Investigating failures](/docs/failure-analysis#promoting-to-a-defect)) |
| **Automation script issue** | The test is broken, not the product |
| **Flaky test** | It flips without a code change. It shows as **Flagged** on the Failures page; see [Flaky tests](/docs/flaky) |
| **Won't fix** | Real, but deliberately not being fixed |

Notes are optional (up to 2,000 characters). The assignee, or a QA lead or administrator in the project, can update a failure's status.

**Reassign** (QA leads and administrators) moves a failure to the suite owner or to a QA engineer in the project. If neither exists, add a QA engineer to the project first (**Admin → Project → Members & access**).

## Approvals

**Approvals** collects the decisions waiting on someone in the selected project. It needs one project selected. The **source** filter shows **All**, or one kind:

- **AI reports** — reports produced by the AI pipeline, waiting for a human review.
- **Quarantine proposals** — tests proposed for quarantine, the same list as **Flaky tests → Proposed**.
- **Test cases** — authored test cases waiting for review.

Anyone in the project can see the queue; the decision buttons depend on your role.

### AI reports

Every report the AI pipeline produces enters this queue as **Awaiting review**. A state filter shows **Pending** (the default), **Accepted**, **Rejected**, **Superseded** or **All**. **Open report** opens the run's **Analysis** tab so you can read the report and its evidence first.

To decide (QA leads and administrators):

1. **Accept**, or **Reject**.
2. For a rejection, choose the **Reason**: **Wrong category**, **Unsupported claim**, **Missing evidence**, **Contradiction**, **Stale data** or **Other**. **Notes** are optional.
3. **Confirm accept** or **Confirm reject**.

The report then carries a banner wherever it is shown: **Draft: awaiting human review** (with **Open review queue**), **Reviewed and accepted**, **Rejected by a reviewer**, or **Superseded by a newer report** when a later analysis of the same run replaced it. Reviews are refused to API keys; and when a run proposes actions for an agent to take, the person who requested that run cannot review its report.

Reviewing is how a team keeps AI-written reports honest. By default an unreviewed report can still be exported and shared, marked as a draft. An administrator can make review mandatory, so that unreviewed reports are not exported, shared, sent in notifications or used for a release verdict until someone accepts them. If your exports are refused with "pending review", that setting is on: accept the report here first.

### Quarantine proposals

QA leads and administrators **Approve** or **Reject** a proposal, then confirm. **Open in Flaky tests** shows it with its history. An approved quarantine lasts 14 days by default. A proposal nobody decides expires after 7 days. See [Flaky tests](/docs/flaky#quarantine-on-the-flaky-tests-page).

### Test cases

Each row shows the test case, its priority, its review state (**Requested** or **Claimed**) and when review was requested. The actions are **Claim review**, **Unclaim**, **Approve**, **Request changes** and **Reject**; approving, requesting changes and rejecting need **Review notes**. QA engineers, QA leads and administrators can review, but never their own test case. Only the reviewer who claimed a review can decide it. See [Test management](/docs/test-management#authored-test-cases-and-reviews).

## Related

- [Investigating failures](/docs/failure-analysis)
- [Flaky tests](/docs/flaky)
- [AI agents and the pipeline](/docs/ai-agents)
