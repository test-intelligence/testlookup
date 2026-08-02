# Value Metrics — the engineer-hours-saved model

The `/value-metrics` page answers "what is this tool saving us" with a
single headline — **engineer-hours saved in the last 30 days** (and its
FTE equivalent) — plus a monthly trend. This page documents exactly how
that number is computed; the same content is served by
`GET /api/v1/value-metrics/methodology` and linked from the headline.

## The model (methodology v1)

Hours saved is the sum of three legs, each `count × minutes / 60`:

| Leg | Formula | What the count means |
|---|---|---|
| **Triage** | `clustered_failures × triage_minutes_per_failure / 60` | Failing executions absorbed into failure clusters of size ≥ 2 during the month (sum of cluster sizes) |
| **Quarantine** | `runs_unblocked_proxy × blocked_run_wait_minutes / 60` | Runs with ≥ 1 failing case where *every* failing case was under an active quarantine window at run time |
| **Dedup** | `duplicates_absorbed × defect_filing_minutes / 60` | `SUM(size − 1)` over clusters of size ≥ 2 — the duplicate failures absorbed into an existing investigation |

The monthly trend also reports `auto_triaged` (AI analyses persisted that
month) for context — it is **not** multiplied into any leg, to avoid
double-counting failures that were both clustered and AI-analyzed.

The FTE equivalent divides headline hours by **173.2** engineer-hours per
month.

## Honest-labeling caveats

- **Cluster-instances, not distinct defects.** Clusters are computed per
  run, so monthly sums count each recurrence of the same underlying defect
  — every time it would have interrupted an engineer. The dedup leg is
  labeled "duplicate failures absorbed", never "defects deduped".
- **`runs_unblocked_proxy` is a proxy.** No persisted per-run "unblocked
  by quarantine" verdict exists; the model approximates it as runs whose
  only failures were quarantine-suppressed. Quarantines later released
  stop contributing their historical windows — a deliberate undercount.

## Tunable assumptions (per project)

Defaults come from published research anchors: a non-trivial test-failure
investigation costs **~3 engineer-hours** end-to-end, and interruption
studies put the refocus/context-switch cost at **15–25 minutes**. The
triage default of 20 minutes sits inside that refocus band — deliberately
far below the full-investigation anchor.

| Assumption | Default | Bounds |
|---|---|---|
| `triage_minutes_per_failure` | 20 | 0 < x ≤ 480 |
| `blocked_run_wait_minutes` | 30 | 0 < x ≤ 480 |
| `defect_filing_minutes` | 15 | 0 < x ≤ 480 |

QA Leads (and above) can tune these per project via
`PUT /api/v1/projects/{project_id}/value-metrics/assumptions`; any project
member can read the effective values (`GET` on the same path). A project
without a saved row uses the defaults (`assumptions_source: "default"`).

## When the headline is hidden

The number stays hidden (`available: false`) until the project has:

1. at least **14 days** between its first and last ingested run, **and**
2. at least one nonzero leg in the last **30 days**.

Until then the API returns `insufficient_data_reason` (e.g. "fewer than
14 days of ingested runs") and the digest omits its hours-saved line —
better no number than a fabricated one.

## Where the number shows up

- `/value-metrics` — headline, monthly trend, and the methodology link.
- Email/Slack digests — one line, "≈ N engineer-hours saved in the last
  30 days (see /value-metrics)", plus a stat tile in the email header.
  Suppressed when unavailable and in zero-change digest windows.
