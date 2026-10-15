# Issue trackers and defect automation

How TestLookup files and tracks issues in Jira (or your own tracker through a webhook), and what can create defects automatically. Using the **Defects** page and promoting a failure by hand are covered in the in-app guide: **Investigating failures** (`/docs/failure-analysis`, source [failure-analysis.md](../frontend/src/content/guide/failure-analysis.md)).

## Connecting Jira

1. Under **Settings → Integrations → Jira, Splunk, OCP, Slack, Teams**, an administrator fills in the Jira **Domain**, **Email**, **Default Project Key** and **API Token**, and ticks **Enabled** (or sets the equivalent `JIRA_*` environment variables; see the [integrations reference](../docs/operations/integrations.md)).
2. Set each project's **Jira Project Key** on **Projects** if it differs from the default.
3. Turn off `AI_OFFLINE_MODE`, or the Jira target stays disabled: offline mode is a hard switch above every integration setting.
4. Check **Integration health** shows Jira as healthy.

## One-click Jira issues

From **Failures** (**Jira** on a failing test), from a quarantined or proposed test (**File Jira** on Flaky tests), or from Ask AI's suggested action, **Create Jira issue** opens a review dialog with a **server-prefilled** ticket: summary (`[TestLookup] <test or cluster>`), failure message and truncated stack trace, occurrence history (first and last seen, failing-run count), branch and build, a link back to TestLookup, and — when an AI analysis exists — a clearly labelled *"Suggested root cause (AI, confidence X%)"* block. The person picks the Jira project and issue type (fetched live from Jira), optionally adds a comment, and confirms — **nothing is filed silently**. Filing needs `QA_ENGINEER`.

Two guarantees:

- **Recurrence dedup** — if an open defect is already linked to Jira for the same failure signature, no duplicate issue is created. The button reads **Note recurrence**; the existing issue gets a *"recurred in build X"* comment.
- **Status sync-back** — about every 15 minutes TestLookup mirrors the Jira status of linked, still-open defects. If Jira says the issue is done but the same test kept failing in the last 7 days, the defect (and its quarantine row) shows **closed in Jira but still failing**.

A test result's **Create Jira Defect** button (on the AI root-cause panel) is disabled when the project has no Jira key or when the analysis needs human review first.

### No Jira? Use the webhook fallback

The create dialog also offers **Webhook event** as the delivery target: instead of calling Jira, TestLookup emits a `defect.create_requested` [outbound webhook](administration.md#outbound-webhooks-settingswebhooks) carrying the full prefilled payload:

```json
{
  "event_type": "defect.create_requested",
  "data": {
    "signature": "…", "summary": "[TestLookup] checkout_flow_test",
    "description": "…full prefilled body…", "issue_type": "Bug",
    "test_name": "checkout_flow_test", "suite_name": "checkout", "cluster_id": null,
    "occurrences": {"first_seen": "…", "last_seen": "…", "failing_runs": 6},
    "context": {"branch": "main", "build_number": "1234", "ci_run_url": null},
    "ai_analysis": {"root_cause": "…", "confidence": 82, "failure_category": "PRODUCT_BUG"},
    "deep_link": "https://testlookup.example.com/runs/…", "extra_comment": null, "requested_by": "qa.lead"
  }
}
```

Subscribe a receiver to that event (**Settings → Integrations → Outbound webhooks**; needs the `outbound_webhooks` flag on and offline mode off) and open tickets anywhere. A minimal GitHub-Issues receiver sketch:

```python
# flask receiver: defect.create_requested → GitHub issue
import hashlib, hmac

@app.post("/hooks/testlookup")
def testlookup_hook():
    expected = hmac.new(WEBHOOK_SECRET.encode(), request.get_data(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("X-TestLookup-Signature", "")):
        return "", 401
    body = request.get_json()
    if body.get("event_type") != "defect.create_requested":
        return "", 204
    d = body["data"]
    requests.post(
        "https://api.github.com/repos/ORG/REPO/issues",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        json={"title": d["summary"],
              "body": d["description"] + f"\n\n[Open in TestLookup]({d['deep_link']})",
              "labels": ["testlookup", d["issue_type"].lower()]},
    )
    return "", 204
```

The signature is the hex HMAC-SHA256 of the raw request body, keyed with the subscription's secret; see [Administration → Outbound webhooks](administration.md#outbound-webhooks-settingswebhooks).

## Promotion from clusters

On a run's **Analysis** tab, **File defect** promotes a failure cluster to a defect (**Promote to Defect**, `QA_ENGINEER`). The cluster's analyses, error signatures and dimension scores travel with it; the severity is pre-filled from the composite risk score — **CRITICAL** (≥ 70), **HIGH** (≥ 50), **MEDIUM** (≥ 30), else **LOW** — and can be changed. A **Jira Project Key** asks for a ticket in that Jira project; leave it blank for a TestLookup-only defect.

### Approving a promoted defect

A promotion that names a Jira project key, has severity **CRITICAL** or **HIGH**, or scores below the configured AI confidence threshold comes back as "Defect created — awaiting approval". It waits for a QA lead in **Inbox › Approvals**, source **Defects** (`/my-failures?tab=approvals&source=defects`; the promotion dialog links there for a QA lead). Each row shows the title, severity, component and owner team, why it waits (the policy reasons), the Jira project it would be filed in (or "No Jira project: approving keeps it in TestLookup"), its age and a link to the run it came from. The queue and its count are a QA lead's: QA engineers and viewers do not see the source and never request it.

- **Approve** makes the defect effective. When the promotion named a Jira project key and Jira is reachable (configured, enabled, offline mode off), approving files the ticket **on that defect**, or links it to the open issue already filed for the same failure (the same recurrence rule as the one-click issue, so the two paths never file one failure twice). The approval is saved before Jira is called, so a slow or failing Jira never undoes it. Offline, the toast says **Approved — Jira not filed (offline mode)** (or *Jira integration disabled*, *Jira not configured*) and the defect's Jira cell on **Defects** reads **not filed (offline)**; a failed filing reads **filing failed** with the server's message. Without a key the defect simply becomes effective (**Approved**).
- **Reject** needs a reason. The defect is closed as **Invalid** (so it stops counting toward the release gate and the open KPIs), and the reason is recorded in the activity log, not on the defect.

The API behind the queue: `GET /api/v1/deep-investigate/defects/pending-review?project_id=…&page=1&page_size=100` (`QA_LEAD`) returns `{items, total, page, page_size}`, where `total` counts every pending defect in scope (it was a bare list before this release); `POST /api/v1/deep-investigate/defects/{id}/review` with `{"action": "approve"}` or `{"action": "reject", "reason": "…"}`. The approve answer adds `jira_status` (`filed`, `linked`, `not_filed`, `failed`, `unknown` or `not_requested`), `jira_reason` and `jira_error`.

When a promoted defect takes effect (on promotion when no approval is needed, or on approval), the activity log records "Failure cluster promoted to defect …" and, with the `outbound_webhooks` flag on and offline mode off, every subscription to [`defect.promoted`](../docs/operations/integrations.md#outbound-webhooks) gets exactly one delivery for that defect.

## Triage a defect

On **Defects** (`/defects`), a QA engineer, QA lead or administrator gets a **⋯ Defect actions** menu on every row. Viewers and testers see the rows (and **Open** for a linked Jira issue) without the menu. Every change is recorded in the activity log (**Activity**, category *quality*: "Defect … changed: status → In progress", "Defect … linked to Jira ABC-123"), and the row updates in place.

- **Change status…** — **Open**, **In progress**, **Resolved** or **Invalid**, with an optional note for the activity log. **Resolved** and **Invalid** stamp the time it closed (the trend and Time to resolve read it); re-opening clears it. A test can have only one live defect: re-opening a defect whose test already has an open one is refused with the other defect's key, so close that one first or link the failure to it. A defect **In progress** is still live: it blocks the release gate and counts as open exactly like an **Open** one.
- **Assign…** — a person of the project (an administrator may be assigned without being a member; service accounts, the synthetic per-project QA lead and deactivated users are not offered and are refused), and a **Team**. Clearing the person leaves the defect unassigned.
- **Link Jira issue…** (**Change Jira issue…** once linked) — a Jira key such as `ABC-123`. TestLookup builds the link itself from the configured Jira domain (`https://<domain>/browse/ABC-123`); a URL is never accepted from the request. **Remove link** unlinks it. Two defects may share one Jira issue.

**Offline (the default), or with Jira not configured or disabled**, all three actions work: a key is saved without a check ("saved locally (offline mode)"), linked to the configured domain (plain text when no domain is set), and nothing is sent to Jira. **With Jira reachable** (the same rule as the one-click issue), a pasted key is checked against Jira first: an unknown key is refused ("Jira has no issue ABC-999."), Jira's own status is mirrored onto the defect, and an unreachable Jira saves nothing. Then a status change on a linked defect is **written back**: TestLookup moves the Jira issue to a status in the matching category (Open → *To Do*, In progress → *In Progress*, Resolved or Invalid → *Done*), using the first transition the issue's workflow offers into that category. It does nothing when the issue is already there or no transition leads there, and a Jira that refuses the move (a workflow screen with required fields, for example) is reported in a second message while the TestLookup status stays saved. The write-back runs after the change is saved, so Jira never decides whether TestLookup records it.

The API: `PATCH /api/v1/projects/{project_id}/defects/{defect_id}` (`QA_ENGINEER` and project access) with any of `resolution_status`, `assignee_user_id`, `owner_team`, `jira_key` (`null` or `""` clears the last three) and an optional `note`. It answers the updated row, `changed_fields`, `jira` (`validated`, `saved_locally` with a reason, or `unlinked`) and `write_back` (`sent`, `skipped`, `not_sent` or `failed`, with a reason). The list (`GET /api/v1/analytics/defects`) accepts `resolution_status=ACTIVE` for Open and In progress together.

## Owners

The **Owner** column reads, in order: the assigned person (with their team under the name); else the defect's own team; else the team the project's ownership rules give the defect's failing test, marked *from ownership rules* (a rule on **Ownership**), *from the component map* (the project's component map) or *from the test's owner label* (an `@Owner` label in the results); else **Unassigned**. A rule of one project is never applied to another project's defect. The table's search covers owners too. Queue health counts **Ownership** (20 %) as the share of open defects with an assignee or a team, so assigning owners raises it.

## Only this release

With one release picked in the top bar, **Only this release** narrows the table, its tabs, the KPI tiles and the sparklines to the defects that **affect** that release, by the release gate's own rule: the releases a defect asserts it affects, or, when it asserts none, the release it was found in. The release gate counts the same set, so **Open defects** then equals the gate's open-defect count. The banner says **Affecting {release}** while it is on. Off (the default), the page lists every release's defects, as before: a defect raised against an earlier release can still be open now. The **suite** select narrows to defects whose failing test ran in that suite. Both are kept in the URL (`?scope=release`) and in a saved view.

## Trend and escape rate

The window picker (7, 30 or 90 days) drives the KPI tiles' sparklines, **Time to resolve** and **Escape rate**; the table always lists every defect.

- **Open defects**, **P0 / P1 open** and **Time to resolve** draw one point per UTC day; hovering a point names the day's opened and closed defects and what was open at its end (Time to resolve: the 7-day mean). Their values count every defect in scope, not only the newest rows the table loads. The banner's **Net this week** is the last seven days' opened minus closed. Defects closed before close times were recorded (Invalid rows from the Jira resolution webhook of earlier releases) appear in no point; the endpoint counts them as `closed_without_time`.
- **Escape rate** is the share of the defects reported in the window that were filed **after a release they affect had shipped**, among the defects in the window that affect a shipped release ("affect": as in *Only this release*; "shipped": the release has a release date). A defect affecting several shipped releases counts once. Below 5 such defects the tile reads **Not measured** with the reason, never 0 %. Its hover adds the production outcomes marked on releases in the window (incidents, rollbacks). To file a defect against a shipped release, pick it in **New defect → Affects release**; the release must belong to the project. A selected suite does not narrow it (an escape is counted per release).

The API: `GET /api/v1/analytics/defects/trend?project_id=…&days=30` (`points` of `{date, opened, closed, open_backlog, p0p1_open, mttr_days}` and `totals`) and `GET /api/v1/analytics/defects/escape-rate?project_id=…&days=30` (`rate`, `escaped`, `attributed`, `measured`, `reason`, `outcomes`). Both accept `release_id`; the trend also takes `suite_name` and `release_match=affects`. `null` always means *not measured*.

## Automatic promotion (`defect_commander`)

The deep pipeline's `defect_commander` stage can create defects from clusters on its own. Its defects always wait for a QA lead in **Inbox › Approvals** ([Approving a promoted defect](#approving-a-promoted-defect)); a ticket is filed only when one is approved with Jira reachable. It is the only pipeline stage that changes state outside its own records, and it runs only when two switches are on for the project: the feature flag `defect_commander` and the agent itself, enabled in **Act** mode under **Settings → AI agents** (a mutating agent starts disabled, in Shadow mode). The flag is listed under **Settings → Feature flags** (group "Agent stages (deep pipeline)", marked MUTATING) and is **off** on every install. To try it on one project first, an administrator switches it on and, under **Edit**, lists that project: the flag is then on for that project only. A deep investigation started while the flag is off skips the stage, and its plan says why ("DefectCommander feature flag is off for this project", or, with the flag on, "agent agent.defect_commander.v1 is disabled in this project's agent configuration"); a run keeps the value it started with, also when it is resumed.

## Duplicate detection

TestLookup fingerprints test cases (normalising names and steps) and flags likely duplicates on **Test cases → Duplicates**, banded Exact, Strong or Possible, so the catalogue stays deduplicated as teams add tests independently. Duplicate failures absorbed into one cluster are counted on **Reports → Value** ("Duplicates avoided").

## A promotion routine

1. Triage a cluster; confirm it is a product bug, not flaky or a script issue.
2. Promote it (or file from the failing test), accepting or adjusting the severity.
3. Give a Jira project key to have the ticket filed when a QA lead approves the defect in **Inbox › Approvals** (with Jira connected and offline mode off); otherwise keep the defect in TestLookup, link an existing issue with **Link Jira issue…**, or use the webhook fallback.
4. Triage it on **Failures → Defects**: set its status, assign an owner, link its Jira issue.
5. Watch **Time to resolve**, the **Escape rate** and the defects with no Jira ticket there.
