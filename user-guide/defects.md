# Defects & promotion

When a failure is a real product bug, it becomes a **defect** — a tracked item with severity, an owner, and (optionally) a linked ticket in your issue tracker. TestLookup's job is to make that promotion one click and carry the evidence along, so the defect arrives explaining *itself*.

## The Defects page (`/defects`)

The defect queue for the project, with **KPIs** at the top: open defects, **mean time to resolve**, **escape rate** (bugs that reached a release), and **linked defects**. Around the queue:

- **Status filter** and **Severity** — narrow to what you're working.
- **Defect queue verdict** — the health read on the queue itself.
- **Recommended actions** and **Auto-link rule misses** — where the auto-linker couldn't match a failure to an existing defect (candidates for a new promotion or a rule fix).
- **Last sync** — freshness of the external-tracker link.

## Promoting a cluster to a defect

The natural flow is cluster → defect. From a failure cluster (in [Failure Analysis](triaging-failures.md) or a [Deep Investigation](ai-features.md#deep-investigation-deep-investigate-deep-investigaterunid)), promote it. The promotion:

- **Bundles the evidence** — the cluster's analyses, error signatures, and dimension scores travel with the defect, so whoever picks it up sees the same picture you did.
- **Assigns a severity automatically** from the composite risk score — **CRITICAL** (≥70), **HIGH** (≥50), **MEDIUM** (≥30), else **LOW** — which you can override.
- **Resolves an owner**, drawing on prior assignment memory so recurring areas land with the right person.

Promotion is the outcome behind the `DEFECT_CREATED` triage status ([Triaging failures](triaging-failures.md#your-inbox-my-failures)) — resolving a My-Failures row as `DEFECT_CREATED` and promoting the cluster are two ends of the same action.

## Auto-linking to your issue tracker

TestLookup can link defects to external tickets (e.g. Jira) so the two systems stay in sync — the `Last sync` and `Auto-link rule misses` surfaces track how well that's working. **This is subordinate to `AI_OFFLINE_MODE`**: offline mode is a *hard kill-switch above any integration flag*, so in a default install nothing is created or synced externally regardless of settings. Enable outbound integrations deliberately (see [Administration](administration.md#connecting-to-your-world)) before expecting tickets to appear.

## One-click Jira issues

From the **Failure Analysis** page (headline failing test) or a **Quarantine** row without a linked defect, `Create Jira issue` / `File Jira` opens a review dialog with a **server-prefilled** ticket: summary (`[TestLookup] <test or cluster>`), failure message + truncated stack trace, occurrence history (first/last seen, failing-run count), branch/build context, a deep link back to TestLookup, and — when an AI analysis exists — a clearly labelled *"Suggested root cause (AI, confidence X%)"* block. You pick the Jira project and issue type (fetched live from Jira, cached ~5 minutes), optionally add a comment, and confirm — **nothing is filed silently**.

Two guarantees:

- **Recurrence dedup** — if an open defect is already linked to Jira for the same failure signature, no duplicate issue is created. The existing issue gets a *"recurred in build X"* comment and the dialog reports *"Linked to existing KEY (recurrence noted)"*.
- **Status sync-back** — every ~15 minutes TestLookup mirrors the Jira status of linked, still-open defects. If Jira says the issue is done but the same test kept failing in the last 7 days, the defect (and its quarantine row) shows a **"closed in Jira but still failing"** warning badge.

As with auto-linking, `AI_OFFLINE_MODE` is a hard kill-switch: with it on, the Jira target is disabled with an explanatory tooltip.

### No Jira? Use the webhook fallback

The create dialog also offers **Webhook event** as the delivery target: instead of calling Jira, TestLookup emits a `defect.create_requested` [outbound webhook](administration.md#connecting-to-your-world) carrying the full prefilled payload —

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
    "deep_link": "https://testlookup…/runs/…", "extra_comment": null, "requested_by": "qa.lead"
  }
}
```

Subscribe a receiver to that event (Settings → Webhooks) and open tickets anywhere. A minimal GitHub-Issues receiver sketch:

```python
# flask receiver: defect.create_requested → GitHub issue
@app.post("/hooks/testlookup")
def testlookup_hook():
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

(Verify the `X-TestLookup-Signature` HMAC header in production — see the webhook docs.)

## Duplicate detection

As teams add tests and file defects independently, near-duplicates creep in. TestLookup fingerprints test cases (normalizing names + steps) and flags likely duplicates so the catalog and the defect queue stay deduplicated — the same machinery that powers duplicate detection in [Test Management](test-management.md#the-test-management-page). Fewer duplicate tickets is one of the numbers on [Value Metrics](dashboards.md#value-metrics-value-metrics).

## A promotion routine

1. Triage a cluster ([Triaging failures](triaging-failures.md)); confirm it's a product bug, not flaky or a script issue.
2. Promote the cluster — accept or adjust the auto-severity, confirm the owner.
3. If outbound integrations are on, let the auto-linker file/sync the ticket; otherwise record the defect internally.
4. Watch **escape rate** and **mean time to resolve** on the Defects KPIs to see whether triage is keeping bugs out of releases.
