# Flaky Intelligence — subsystem architecture

> Companion to [README.md](./README.md). Covers the FLK series (P1–P6): how
> TestLookup decides a test is *flaky* rather than *broken*, down to the
> individual step, and what that decision drives (quarantine, release gates,
> UI/MCP surfaces). Verified against the implementation 2026-08-08.

The core design principle: **a flaky verdict is a structured, evidence-backed
claim, not a threshold on a failure rate.** Every layer below produces evidence
that `build_flaky_verdict` assembles into `{is_flaky, confidence, likely_cause,
likely_cause_code, evidence[]}` — and every consumer (sentinel agent,
quarantine, UI) works from that verdict rather than re-deriving its own.

## 1. Component map

```mermaid
flowchart TB
    subgraph Ingest["Ingestion (per run)"]
        ING["services/ingestion.py<br/>persists test_cases + retains per-run test_step_runs"]
    end

    subgraph Evidence["Evidence layers (pure services)"]
        SIG["flaky_signals.py — FLK-P1<br/>IntermittencySignals, error_signature, stack_fingerprint"]
        STAT["flaky_statistics.py — FLK-P2<br/>wilson_failure_confidence (Wilson CI on failure ratio)"]
        ML["ml/flaky_confidence.py — FLK-P3<br/>build_flaky_feature_vector: failure-rate trend,<br/>run-interval variance, … → ML confidence"]
        STEP["flaky_step_flip.py — FLK-P5/P6<br/>compute_step_flips over test_step_runs → StepFlipReport"]
    end

    subgraph Verdict["Verdict assembly"]
        INV["flaky_investigator.py — FLK-P4<br/>cluster_failures · determine_likely_cause ·<br/>build_flaky_verdict → {is_flaky, confidence,<br/>likely_cause_code, evidence[]}"]
    end

    subgraph Consumers["Consumers"]
        SENT["agents/flaky_sentinel_agent.py<br/>FlakySentinelAgent — recommendation reconciled<br/>with the verdict (_reconcile_recommendation)"]
        QUAR["flaky_quarantine_service.py<br/>propose → approve/reject → release"]
        RS["runs_service.step_flip_report_by_fingerprint<br/>+ per-test / run-level roll-up endpoints (routers/runs.py)"]
    end

    subgraph Surfaces["Surfaces"]
        UI["Frontend: Flaky Coach, Quarantine page,<br/>StepFlipPanel + RunStepFlipCard (SWR hooks)"]
        MCP["MCP tool: get_test_step_flips"]
        GATE["Release gate / risk signals"]
    end

    ING --> SIG
    ING --> STEP
    SIG --> INV
    STAT --> INV
    ML --> INV
    INV --> SENT
    SENT --> QUAR
    STEP --> RS
    RS --> UI
    RS --> MCP
    INV --> GATE
    QUAR --> GATE
    QUAR --> UI
```

## 2. The evidence layers

| Layer | Module | What it contributes |
|---|---|---|
| **Intermittency signals** (FLK-P1) | `services/flaky_signals.py` | Normalizes statuses across frameworks, derives an `error_signature` and `stack_fingerprint` per failure, and computes `IntermittencySignals` — pass/fail alternation across a fingerprint's run history. Same-signature repeated failures look like a *regression*; varied signatures with alternation look *flaky*. |
| **Statistical confidence** (FLK-P2) | `services/flaky_statistics.py` | `wilson_failure_confidence` puts a Wilson score interval around the failure ratio, so 2-fails-of-3-runs is treated as far weaker evidence than 20-of-30. Returns `FailureRatioConfidence` with the interval bounds the investigator reads (`wilson.low`). |
| **ML confidence** (FLK-P3) | `services/ml/flaky_confidence.py` | `build_flaky_feature_vector` extracts behavioral features per fingerprint — failure-rate *trend*, run-interval variance, and friends — for the flaky-confidence model. Deliberately separate from the 6-class triage classifier in `ml/classifier.py` (that one answers *what kind of failure*; this one answers *how confident are we it's flaky*). |
| **Step flips** (FLK-P5/P6) | `services/flaky_step_flip.py` | Ingestion retains per-run step outcomes in `test_step_runs` (it deletes/rewrites per-run rows but **retains history across runs** — that retention is what makes cross-run analysis possible). `compute_step_flips` walks a fingerprint's step history and emits a `StepFlipReport`: which *step* flips verdict across runs, isolating the flaky part of an otherwise stable test. |

## 3. Verdict assembly (FLK-P4)

`services/flaky_investigator.py` is the single assembly point:

- `cluster_failures` groups a fingerprint's failure records by signature (`FailureClusters`).
- `determine_likely_cause` maps the P1 signals plus the ML confidence to a `(cause_text, cause_code)` — e.g. `likely_regression` vs a flaky cause.
- `build_flaky_verdict` combines all of the above with the Wilson interval into the canonical verdict dict. Each contributing layer lands in `evidence[]` as an `EvidenceRef` with a `strength` (`weak|medium|strong`) and a numeric `contribution` — the AIQ-P3 evidence-weighting scheme, so the final `confidence` is auditable back to its inputs.

## 3a. One definition of "intermittent"

A test is flaky when its history **oscillates**, not when it merely fails often.
That distinction has one home:

```python
# services/flaky_signals.py
MIN_FLIPS_FOR_INTERMITTENCY = 2      # pass<->fail transitions, IN RUN ORDER
```

and one shared predicate for history-shaped checks:

```python
# services/test_health_coach_service.py
history_is_intermittent(status_history) -> bool
```

**Why this is centralised.** Four surfaces used to re-derive the rule
independently and disagreed on the same data — the dashboard KPI, the
`/failures` verdict, the flaky coach headline, and the ROI figure. A test that
broke once and stayed broken (`p f f f f`: one transition, a **regression**) was
counted as flaky by some and not others, so one project reported 2, 2 and 5
simultaneously. A persistent regression is a different problem with a different
fix, and calling it flaky sends it to the wrong queue.

Consumers, all importing rather than restating:

| surface | imports |
|---|---|
| `metrics_service` (dashboard KPI) | `MIN_FLIPS_FOR_INTERMITTENCY as _FLAKY_MIN_FLIPS` |
| `analytics_service` (`/failures` verdict) | same |
| `test_health_coach_service` (coach headline) | `history_is_intermittent` |
| `value_metrics_service` (ROI hours-saved) | `history_is_intermittent` |

**`BROKEN` counts as a failure** for flip purposes — an infra error that
alternates with passes is still oscillation. Note the coach's *list* still shows
persistent regressions (with a downgraded recommendation and "treat as a
regression, not a flake" guidance); only the **count** is gated on flips.

> **Ordering caveat.** Flip analysis replays history by ingest time
> (`created_at`), not build sequence — a deliberate decision, since
> `build_number` is not reliably numeric, present, or monotonic across branches.
> Where CI uploads out of order (parallel jobs, retries, backfills), the
> flake-vs-regression verdict can differ from a build-ordered replay.

## 4. The sentinel and the quarantine lifecycle

`FlakySentinelAgent` (in `app/agents/`, subject to the agent-contract ratchet) runs over a window of results and produces recommendations. Its `_reconcile_recommendation` guard exists because of a real bug class: the old failure-rate ladder could shout "QUARANTINE" at a consistently-failing test whose own verdict said `is_flaky: false, likely_cause_code: likely_regression`. The recommendation is now **derived from the verdict**, never allowed to contradict it — a regression gets "fix it", not "quarantine it".

Quarantine itself is a small, audited state machine in `services/flaky_quarantine_service.py` (feature-gated, every transition audit-logged):

```mermaid
stateDiagram-v2
    [*] --> PROPOSED : propose_quarantine (sentinel or human)
    PROPOSED --> APPROVED : approve (human decision)
    PROPOSED --> REJECTED : reject
    PROPOSED --> EXPIRED : expire_stale_proposals (beat task)
    APPROVED --> RELEASED : release (test fixed / manually freed)
    REJECTED --> [*]
    EXPIRED --> [*]
    RELEASED --> [*]
```

`active_quarantines_for_project` is what the release-gate side reads: quarantined tests stop counting against the verdict while they're being fixed.


### Lifecycle policy (`quarantine_lifecycle_policies`)

Per-project, so a team can tune how aggressive quarantine is:

| column | meaning |
|---|---|
| `detection_flip_rate_threshold`, `detection_min_runs` | when a test is *proposed* |
| `sla_days` | how long a quarantine may sit before it is chased |
| `auto_create_defect` | file a defect on quarantine |
| `auto_promote`, `promote_after_passes` | release automatically after N consecutive passes |

Requests carry an `owner_user_id`, so a quarantined test has someone
accountable rather than sitting in a shared bucket.

**Transitions are guarded.** Every illegal move returns `409` naming the actual
state (`Cannot approve from status QUARANTINED`), and the
`(project_id, test_fingerprint)` live-uniqueness invariant is enforced by
idempotent upsert — a duplicate live proposal returns the **existing** row
rather than creating a second. Terminal rows are retained as history, so
re-proposing after a release creates a new row.

## 5. Step-flip read path (FLK-P6 surfaces)

The step-flip data has one compute service and three read surfaces, all funneled through `runs_service`:

```mermaid
sequenceDiagram
    participant UI as StepFlipPanel / RunStepFlipCard (SWR)
    participant API as routers/runs.py
    participant RS as runs_service
    participant FSF as flaky_step_flip.compute_step_flips
    participant DB as test_step_runs (Postgres)

    UI->>API: GET /runs/{run}/tests/{test}/step-flips (per-test)<br/>or GET /runs/{run}/step-flips (run roll-up)
    API->>API: require_run_access (IDOR guard)
    API->>RS: step_flip_report_for_test / _for_run
    RS->>RS: resolve run → fingerprints (batched, max_tests cap)
    RS->>DB: load per-run step history per fingerprint
    RS->>FSF: compute_step_flips(runs)
    FSF-->>RS: StepFlipReport (flip counts, flip probability per step)
    RS-->>API: report (truncated flag when capped)
    API-->>UI: JSON → "Cross-Run Step Flakiness" panel
```

The MCP tool `get_test_step_flips` (in `mcp/tools/runs.py`) reuses the same per-test endpoint — no separate backend path — and renders insufficient-history / stable / flicker-table states for agent consumers.

## 5a. Failure groups and systemic cluster identity (VIZ-207)

- **One signature, computed in SQL.** `services/failure_signature.py` ports `flaky_signals.error_signature` (`_NOISE` normalisation of the first line, 80-char signature) to one SQL expression, `FAILURE_SIGNATURE_SQL`. `GET /api/v1/analytics/failure-groups` groups failing executions on it, and `GET /api/v1/analytics/chart-data/rows?group_by=error_signature` selects a group's rows with the same constant, so a group's `failure_count` equals its rows' total. There is no Python merge stage. The Python function stays the reference: `tests/test_failure_signature_parity.py` runs a corpus of over 2,000 messages through both. The remaining differences are documented and tested as differences: `lower()` special casings follow the database collation; the statement reads at most 1,000 characters and normalises at most 300 characters of the first line.
- **Groups are deterministic, not AI.** No model is involved, and the per-run AI `FailureCluster`s are a separate thing (they are not shown here). The 200 largest groups are returned. `__NO_MESSAGE__` (blank or NULL message), `__SINGLETONS__` (signatures seen once) and `omitted` are roll-ups. Every share has the same denominator (all failing executions in scope). `include=edges` adds Jaccard links between the affected-test sets of the 60 largest groups (weight >= 0.2, at most 300). A test is `(project_id, test_fingerprint)`: the fingerprint is not unique across projects, so without a `project_id` the same test in two projects counts twice in `affected_tests`, is two `top_tests` entries (each names its `project_id`), and never links two groups across projects. `first_seen` / `last_seen` are bounded by the window. A failing run dated after today stays in the counts and is reported in `meta.outside_window`, as on the heatmap.
- **`membership_key` (migration 0193).** The nightly sweep (`recompute_systemic_clusters`, 06:40 UTC) deletes and re-inserts every `systemic_flake_clusters` row, and `cluster_key` is a rank (`sfc_001` = tonight's largest). Neither one survives a night. `membership_key` is the first 32 hex characters of sha256 over the members' fingerprints, deduplicated, sorted by code point and joined by newlines. The migration backfills it in SQL with `COLLATE "C"` to get the same order. It identifies an **exact** member set only: one test joining or leaving produces a new key (OD-4: no lineage tracking). `/analytics/systemic-clusters` returns it on every item (null only for a cluster with no members); for a row written before 0193 it computes the key from the full member set, never from a filtered subset.
- **Cache.** After its commits, the sweep makes one `bump_analytics_epochs` call covering each project whose clusters it wrote or removed. The `backend.analytics-epoch-bump` guard registers `systemic_cluster_service:store_clusters`.

## 6. Design invariants

- **Pure evidence services** — the signal/statistics/step layers are pure functions over records; they take data, not sessions. That's what makes the verdict unit-testable and the pipeline cheap to re-run.
- **One verdict, many consumers** — nothing downstream (sentinel, quarantine, gates, UI) re-decides flakiness; they consume `build_flaky_verdict` output. Divergence between a recommendation and the verdict is treated as a bug.
- **Retention over recomputation** — `test_step_runs` retains prior runs on re-ingest precisely so step flips are computable without replaying old reports.
- **Fully offline** — every layer here is rules/ML on local data; nothing in the flaky path requires an LLM or leaves the machine.
