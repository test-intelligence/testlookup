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

On a run's **Analysis** tab, **File defect** promotes a failure cluster to a defect (**Promote to Defect**, `QA_ENGINEER`). The cluster's analyses, error signatures and dimension scores travel with it; the severity is pre-filled from the composite risk score — **CRITICAL** (≥ 70), **HIGH** (≥ 50), **MEDIUM** (≥ 30), else **LOW** — and can be changed. A **Jira Project Key** files a ticket as well; leave it blank for a TestLookup-only defect.

Some promotions come back as "Defect created — awaiting approval": the defect needs a QA lead's approval before a Jira ticket is created. That approval is API-only today (`GET /api/v1/deep-investigate/defects/pending-review` and `POST /api/v1/deep-investigate/defects/{id}/review`, `QA_LEAD`).

## Automatic promotion (`defect_commander`)

The deep pipeline's `defect_commander` stage can create defects from clusters on its own, and file tickets when Jira is enabled. It is the only pipeline stage that changes state outside its own records, and it runs only when a feature flag named `defect_commander` exists and is enabled for the project. No install creates that flag, so automatic promotion is **off** until an administrator adds it under **Feature flags**. Try it on one project first.

## Duplicate detection

TestLookup fingerprints test cases (normalising names and steps) and flags likely duplicates on **Test cases → Duplicates**, banded Exact, Strong or Possible, so the catalogue stays deduplicated as teams add tests independently. Duplicate failures absorbed into one cluster are counted on **Reports → Value** ("Duplicates avoided").

## A promotion routine

1. Triage a cluster; confirm it is a product bug, not flaky or a script issue.
2. Promote it (or file from the failing test), accepting or adjusting the severity.
3. With Jira connected and offline mode off, file the ticket from the same dialog; otherwise keep the defect in TestLookup or use the webhook fallback.
4. Watch **Time to resolve** and the defects with no Jira ticket on **Failures → Defects**.
