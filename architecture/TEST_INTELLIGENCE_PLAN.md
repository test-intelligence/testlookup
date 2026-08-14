# Test-intelligence roadmap — implementation plan

**Status**: plan only, nothing implemented. **Created** 2026-08-14.
**Source of truth for the evidence**: [`research/TEST_INTELLIGENCE_RESEARCH.md`](../research/TEST_INTELLIGENCE_RESEARCH.md)
(11 adversarially-verified findings, 7 refuted claims, §3 roadmap).
**Design docs this will change**: `architecture/FLAKY_INTELLIGENCE.md`,
`architecture/DATABASE_SCHEMA.md`, `architecture/RELEASE_GATE.md`,
`architecture/AI_QUALITY.md`.

Every phase below cites the finding that justifies it. Where the research is
silent, this plan says so rather than inventing a rationale.

---

## 1. What already exists (verified in the code, not assumed)

This inventory exists so no phase rebuilds something we already have. Each line
was checked against the tree at `dfd4b54`.

| capability | where | state |
|---|---|---|
| Flip/volatility signals per fingerprint | `services/flaky_signals.py` → `IntermittencySignals` | **Has** runs, fail_count, flip_count, status_volatility, error_signature_diversity, stack_trace_diversity, in_run_retry_rate |
| Duration statistics per (project, fingerprint) | `perf_baselines` table (Welford: `mean_ms`, `m2`, `stddev_ms`, `p95_ms`, `sample_count`) | **Has** — this is the duration-variance signal, already maintained |
| Retry data per test | `test_cases.retry_count`, `test_cases.is_flaky_run` | **Has** — persisted, but not surfaced in the UI |
| Wilson confidence on failure ratio | `services/flaky_statistics.py` | **Has** |
| Quarantine lifecycle | `FlakyQuarantineStatus` (9 states), `QuarantineLifecyclePolicy` (`sla_days`, `auto_create_defect`, `auto_promote`, `promote_after_passes`, detection thresholds) | **Has more than the research assumed** — SLA and auto-promote already exist |
| Ownership resolution | `services/ownership_resolver_service.py`, `services/codeowners_service.py` | **Has** — used by `change_ownership_agent` |
| Commit attribution | `services/commit_attribution_service.py` (`build_locator`, `path_overlap`), `run_commit_ranges` table | **Has** the hard part (path//name overlap scoring) |
| Failure clustering | `agents/cluster_agent.py` → `tools/embed_and_cluster.py`, persisted by `agents/deep_persistence.py` into `FailureCluster` | **Has, but per-run and semantic** — see the structural finding below. (`services/cluster_service.py` is *not* the clusterer — it is duplicate-detection of clusters against open defects.) |
| Per-test run history UI | `frontend/src/pages/CanonicalDetailPage.tsx` — "Run history" section | **Has a table**; not a timeline, no environment metadata |
| PR/MR delivery surfaces | `services/github_pr_comment_service.py`, `github_checks_service.py`, `gitlab_integration_service.py` | **Has** — needs a new payload, not new plumbing |
| Feature flags with rollout buckets | `services/feature_flags.py` → `is_enabled(db, key, ...)` | **Has** — every phase ships behind one |
| ROI / value metrics | `services/value_metrics_service.py`, `team_value_metrics_service.py` | **Has** |

**Alembic head is `0128`.** New migrations start at `0129`; re-check before
writing one (`database.single-alembic-head` gate).

### 1.1 Two structural findings that shape the plan

**(a) `FailureCluster` cannot carry systemic clustering.** It is keyed
`test_run_id` with `ON DELETE CASCADE` — a cluster is a grouping *inside one
run* — and it is built by **semantic embedding of error messages**
(`tools/embed_and_cluster.py`). The research's systemic-flakiness finding
(§2.10) is a different object on both axes: it clusters **across runs**, by
**literal same-run co-failure** (Jaccard over sets of failing run IDs), not by
message similarity. Different scope, different mechanism, different lifetime.
Phase 3 therefore introduces a new entity and **must not** overload
`FailureCluster`.

This also sharpens Phase 2A: because today's clustering keys on error-message
semantics, it is *precisely* the mechanism whose specificity ICST 2024 measured
swinging from 100% to no-better-than-random across projects. Calibration is not
a nice-to-have bolted onto clustering — it is measuring the thing we already
ship.

**(b) There is no environment dimension.** `test_runs` has `branch`,
`commit_hash`, `ci_provider`, `ci_repo`, `ci_actor`, `oc_*` — but no
`environment`. Atlassian's scoring fuses "environment consistency" (§2.4), and
the practitioner ask is explicitly a history "with environment/device metadata"
(§2.11). This is a **prerequisite gap**, resolved in Phase 0.

---

## 2. Decisions needed from you

These change the shape of the work. I have a recommendation for each; none is
blocking until the phase that needs it.

| # | decision | recommendation | needed by |
|---|---|---|---|
| D1 | Environment: add a first-class `test_runs.environment` column (SDK/CLI/wire change) **or** derive an `env_key` from `(ci_provider, oc_namespace, branch class)`? | **Add the column, derive as fallback.** A derived key silently mislabels anyone not on OpenShift, and the signal is load-bearing for both #5 and #2. | Phase 0 |
| D2 | May a flaky verdict ever **auto-suppress** a failure? | **No — advisory only.** §2.3: ~1 in 6 newly-flaky tests reflected a real production bug. Suppression is the one irreversible mistake here. | Phase 2 |
| D3 | How should the release gate consume a flakiness score — exclude flaky-attributed failures, discount by score, or show as a separate "signal quality" dimension? | **Separate dimension, no automatic discount.** The research explicitly could not answer this (open Q2); inventing a discount policy would be fabricating a threshold. | Phase 4 |
| D4 | Cluster recompute window + cadence (cost vs freshness) | Start **60-day window, nightly**, measured in Phase 0 before committing. | Phase 3 |
| D5 | Fix the **83 latent multi-line structlog positional-arg calls** (mostly `worker/tasks.py`) as part of this work? | **Yes, scoped to files a phase already touches.** Phases 3 and 6 add beat jobs to `worker/tasks.py`, and this bug class silently kills features inside `try/except` (it killed checkpoint restore in #573). | Phase 3 |
| D6 | **F-067 / F-080 denominators** (two pass-rate bases; inert suite filter) — resolve before the verdict ships? | **Yes, before Phase 4.** The research's own warning: a verdict built on numbers users distrust inherits that distrust. | before Phase 4 |

---

## 3. Target design

### 3.1 New data model (cumulative)

```
Phase 0  test_runs.environment            (nullable String(100)) + index
         flaky_classifier_calibration     per-project measured specificity of
                                          fingerprint/error-signature matching

Phase 2  flaky_score                      per (project, fingerprint): 0-1 score,
                                          component breakdown, window bounds,
                                          prior parameters, computed_at

Phase 3  systemic_flake_cluster           per (project, window): label, cause
                                          family, cohesion/silhouette, size
         systemic_flake_cluster_member    (cluster_id, test_fingerprint)

Phase 4  failure_attribution              per test_case: verdict, confidence
                                          band, inputs snapshot, computed_at

Phase 5  quarantine policy gains          max_active_quarantined (cap),
                                          ticket_routing ownership fields
```

Provisional migration numbers, assuming nothing else lands first: `0129`
environment, `0130` calibration, `0131` score, `0132` clusters + members, `0133`
attribution, `0134` quarantine policy. **Re-check the head before writing each
one** — these are reservations on paper, not facts, and parallel agent work is
exactly how multi-head conflicts happen.

Every table is **project-scoped** and every query must carry project scope
(house rule; `test_fingerprint` is not globally unique).

### 3.2 The verdict contract (Phase 4, the flagship)

```python
class AttributionVerdict(str, Enum):
    LIKELY_YOUR_CHANGE = "LIKELY_YOUR_CHANGE"
    LIKELY_FLAKY       = "LIKELY_FLAKY"
    LIKELY_INFRA       = "LIKELY_INFRA"
    UNCERTAIN          = "UNCERTAIN"      # the honest default
```

Composed from five inputs, **each shown to the user** (§2.11 — practitioners
reject generic explanations and want the specific files and prior failures
named):

1. **Transition** — is this a new failure vs the last green? (existing baseline logic)
2. **Flaky score** — Phase 2, with its confidence band
3. **Systemic cluster membership** — Phase 3; co-failing across unrelated tests is
   strong evidence of infra, not the developer's change
4. **Commit-range overlap** — existing `path_overlap` / `build_locator`
5. **Classifier calibration** — Phase 0/2; on a project where fingerprint matching
   measures poorly, the verdict is capped at `UNCERTAIN`

`UNCERTAIN` is not a failure mode, it is the correct output when the inputs do
not agree. Never emit a confident verdict to fill a gap — that is the fabricated
-confidence failure Epic 15 already fixed once.

---

## 4. Phases

Ordered by dependency, not by value. Each ships behind a feature flag, with a
regression test that fails before the change, a CHANGELOG entry, and CI green.

---

### Phase 0 — Measure first, build second
**Goal**: answer the two questions that decide whether later phases are worth
building, and close the environment gap. **No user-visible behaviour change.**

The research's open question #4 is directly about this product: every
quantitative datapoint comes from 10k-engineer monorepos, while academic
per-test flake rates run ~10× lower. *At what suite size and run frequency does a
moving-window Bayesian score have enough data to be calibrated at all?* We answer
that on real data before building the scorer.

**Deliverables**
- **P0-1 Environment dimension** (D1). Migration `0129`: `test_runs.environment`
  nullable + index. Populate from ingestion payload; derive a fallback
  `env_key` from `(ci_provider, oc_namespace, branch)` when absent. SDK/CLI field
  added but optional — **no breaking wire change**.
- **P0-2 Classifier calibration harness** — new `services/flaky_classifier_calibration.py`.
  For each project, backtest "same error signature ⇒ flaky" against what actually
  happened next, and record **measured specificity** + sample count. Migration
  `0130`: `flaky_classifier_calibration`. Read-only; nothing consumes it yet.
- **P0-3 Flaky-readiness census** — `GET /api/v1/metrics/flaky-readiness`,
  mirroring the existing `/metrics/tia-readiness` precedent: per project, how many
  fingerprints have ≥N runs in the window, median runs per fingerprint, share of
  the corpus with enough history for a windowed posterior.

**Acceptance criteria**
- Specificity is reported per project with a sample count, and projects with
  insufficient data are reported as **insufficient**, not as a number.
- The census answers "would a Bayesian score have data here?" with a yes/no per
  project.

**Gate**: if the census says the median project lacks the history for a windowed
posterior, **Phase 2's scorer is descoped to the signals that work at low volume**
(volatility + retry + duration variance) and the Bayesian layer is deferred. That
decision gets recorded here rather than discovered late.

#### GATE READING — measured 2026-08-14 on the homelab

Run against the live database (90-day window, distinct runs per fingerprint —
the same logic `flaky_readiness_service` uses). **Synthetic projects excluded**:
58 projects exist, but 51 are `ZZ …` load-test and probe artefacts created by
this project's own benchmarks, whose run counts are bulk-ingest artefacts rather
than CI cadence. Including them would answer a question nobody asked.

The four genuine projects:

| project | fingerprints | qualifying (≥20 runs) | median runs | max runs |
|---|---|---|---|---|
| Auth Service | 15 | **0** | 12 | 12 |
| Inventory Service | 15 | **0** | 12 | 12 |
| Payment Service | 15 | **0** | 12 | 12 |
| Checkout Service | 12 | **0** | 5 | 5 |

**Verdict: the Bayesian layer is NOT viable on this data, and Phase 2 is
descoped accordingly.** Not one project clears the bar, and not narrowly — the
thresholds want ≥25 fingerprints with ≥20 runs each, while these projects have
12–15 fingerprints *in total* and a median of 5–12 runs. A moving-window
posterior here would be dominated by its prior: a number that looks like a
measurement and is mostly an assumption.

This is research open question #4 — *do hyperscale magnitudes hold for small
self-hosted teams?* — answered with data instead of assumption, which is the
entire reason Phase 0 came first.

**Honest limits of this reading.** It is one deployment, and a developer's
homelab rather than a production tenant, so it is evidence about the plausible
low end, not proof about every user. It does not say a Bayesian scorer is
worthless — it says building one *now*, tuned against data like this, would be
tuning against noise. Re-run the census when a real high-cadence corpus exists
(`GET /api/v1/metrics/flaky-readiness?project_id=…`); the gate is a measurement,
not a permanent ruling.

**Risk**: backtest is retrospective and can be slow on large corpora — run it as
an off-peak Celery beat job, not inline.

---

### Phase 1 — Cheap wins that need no scoring
**Goal**: ship the three items that depend on nothing above them. Independent of
Phase 0's gate; can run in parallel.

**P1-A — Per-test history timeline** (roadmap #2, §2.11 3–0)
`CanonicalDetailPage` already fetches run history and renders a table. Make the
**timeline the hero view**: outcome per run over time, duration sparkline, and
environment/branch/agent annotation (needs P0-1). Keep the table below it.
*Framing note*: the research demotes this from "the #1 ask" to one of four
statistically tied asks (32/31/28/25 with no separation) — worth building, not
worth calling the top request.

**P1-B — Retry transparency** (roadmap #8, §2.6 3–0)
`retry_count` and `is_flaky_run` are already persisted and never shown. Surface
"passed on attempt 3" as a first-class verdict on run detail and test detail;
attribute machine-time absorbed by retries as an ROI line. §2.6: never silently
absorb a retry — a retry is evidence, not a way to make the build green.

**P1-C — Flake-load budget framing** (roadmap #10, §2.2 3–0)
Google's insertion rate ≈ fix rate, and Meta's position is that all real tests are
flaky to some degree. So: **do not build a flaky-debt burndown** — it will never
reach zero and will teach users to distrust the tool. Present a **flake load**
(share of runs carrying ≥1 flaky-attributed failure) as an operational budget.
Audit existing UI for burndown-to-zero framing and correct it. Scoring is
tier-aware later (Google: 0.5% small / 1.6% medium / 14% large).

**Acceptance criteria**: a user can answer "has this test been unstable, on which
environments, and how long has it taken?" from one screen; no surface implies
flakiness trends to zero.

---

### Phase 2 — Signal quality
**Depends on**: Phase 0 (both). **Gate**: P0-3 census.

**P2-A — Per-project calibration gates auto-suppression** (roadmap #4, §2.9)
The most direct challenge to the current design: ICST 2024 measured
fingerprint-dedup specificity from **100% down to no better than random**, driven
by whether failures carry distinctive exception types. Therefore:
1. display measured per-project specificity on that project's own history;
2. prefer TF-IDF / abstracted-symptom similarity over exact stack-trace matching;
3. **gate any suppression on measured specificity**, defaulting to advisory-only
   where the classifier is weak (D2 says advisory always);
4. normalise failure symptoms at ingest, per framework.

*Behaviour change*: on low-specificity projects, flaky verdicts become advisory.
Flag: `flaky.calibration_gate`.

**P2-B — Continuous 0–1 flakiness score** (roadmap #5, §2.4 3–0)

> **DESCOPED BY THE PHASE 0 GATE (measured 2026-08-14).** No project on the
> measured deployment has the history to support a windowed posterior — see the
> gate reading in Phase 0. **Build the four signals and a bounded composite
> score; do NOT build the Bayesian posterior layer yet.** The three signals we
> already compute plus duration variance work at low volume and degrade
> honestly; a posterior does not, because with 5–12 observations it mostly
> reports its prior back. Every score must still carry a confidence band derived
> from observation count, so a thin-history test is visibly thin rather than
> confidently wrong. Revisit when the census clears on a real corpus.

Bayesian posterior over a moving window, fusing four signals — three of which we
already compute:

| signal | source | status |
|---|---|---|
| result patterns / volatility | `IntermittencySignals.status_volatility` | have |
| retry frequency | `IntermittencySignals.in_run_retry_rate` | have |
| duration variability | `perf_baselines.stddev_ms` / `m2` | **have — reuse, do not recompute** |
| environment consistency | P0-1 `environment` | new |

Persist score **and its component breakdown and prior parameters** (migration
`0131`), so a score is reproducible and explainable. Score decays as a test
stabilises — which is also what makes auto-un-quarantine safe.
*Explicitly not*: the "81% detection rate" is a self-reported figure with no
denominator (§2.5 downgrade). **Do not design to it or promise it.**

**Acceptance criteria**: score is reproducible from stored components; a
stabilising test's score decays; low-data fingerprints report a wide confidence
band rather than a confident number.

---

### Phase 3 — Systemic clustering
**Depends on**: Phase 2 (score), Phase 0 (environment). **Roadmap #3, §2.10 3–0.**

75% of flaky tests fail in co-occurring clusters, dominated by networking and
external-dependency causes. Per-test triage mismatches the dominant failure mode.

**Design** (from the paper's method, which is why it is specified this precisely):
- Cluster on **literal same-run co-failure**: Jaccard distance over sets of
  failing run IDs, agglomerative, accepted only at mean silhouette **≥ 0.6**.
- Name the shared cause family: networking / external dependency / filesystem /
  timeout / clock / unknown — feeding the existing `INFRASTRUCTURE` classifier.
- New entities `systemic_flake_cluster` + `_member` (migration `0132`);
  **`FailureCluster` untouched** (finding 1.1a).
- Nightly Celery beat over a 60-day window (D4), project-scoped.
- Quarantine, ticketing and explanation can then operate on **the cluster as the
  unit**.

**Honesty requirement**: only **10 of 22** projects in the study contained any
cluster at all. The UI must handle "no clusters here" as a normal, common answer,
and must not manufacture a cluster to fill the panel.

**Also in this phase (D5)**: `worker/tasks.py` gains a beat job, so convert that
file's multi-line structlog positional-arg calls to kwargs while we are in it.

---

### Phase 4 — The attribution verdict (flagship)
**Depends on**: Phases 2 and 3. **Blocked on D6** (F-067/F-080 denominators).
**Roadmap #1, §2.3 3–0 + §2.10.**

~84% of pass→fail transitions at Google involve a flaky test, so a raw transition
is a weak regression signal — and the false-positive flood trains engineers to
dismiss real failures. This is the item the whole plan exists to reach.

- New `services/failure_attribution_service.py` composing the five inputs in §3.2.
- Persist per-test-case verdict + inputs snapshot (migration `0133`) so the
  verdict is auditable and replayable.
- Surface on run detail and `/failures`, leading each **newly failing** test with
  one verdict and its inputs shown.
- **Hard safeguard**: never auto-suppress, never present "flaky" as "safe to
  ignore" (§2.3, 1-in-6 were real bugs). Rank and route by probability-of-real-
  regression; never present a raw transition as a regression verdict.

**Acceptance criteria**: every verdict renders its five inputs; `UNCERTAIN` is
emitted when inputs disagree; no code path converts a verdict into suppression.

---

### Phase 5 — Distribution and the closed loop
**Depends on**: Phase 4 for the payload.

**P5-A — Verdict in PR/MR checks** (roadmap #6). The adoption gap (§2.11) says the
verdict must arrive before a human is paged. Plumbing exists; the change is the
payload: *"2 failures: 1 attributable to this change, 1 known-flaky (cluster
`net-3`)"* instead of raw counts.

**P5-B — Quarantine closed loop** (roadmap #7, §2.8 3–0). Narrowed by the
inventory — `sla_days`, `auto_create_defect`, `auto_promote` already exist. The
genuinely missing pieces:
1. **ownership-routed ticket with a deadline at quarantine time** (wire
   `ownership_resolver_service` + `codeowners_service` into defect creation);
2. **a cap with a visible warning** (new `max_active_quarantined`; Fowler's ≤8 is
   a suggestion, not a law — make it configurable);
3. **an unmasking safeguard** — surface when a quarantined test's failure
   *signature changes* or correlates with a production incident (§2.8: quarantine
   "could easily mask a real race condition");
4. **a visible quarantine-population trend**, so the pile cannot grow silently.

---

### Phase 6 — Detection timing (lowest priority)
**Roadmap #9, §2.7 2–1 — demoted, and its headline justification was refuted.**

The "75% of flaky tests are already flaky at their introducing commit" claim was
**refuted 1–2** and must not be used to justify this. What survives is the weaker
85/15 finding, heavily qualified (245 flaky tests, 55 Java/Maven OSS projects,
skewed to order-dependent flakiness, async/concurrency under-sampled, "may not
generalize" per the authors).

Two-tier detection: cheap screening of new/changed fingerprints for fast
feedback, plus a continuous background pass over the whole corpus for
environment- and dependency-induced flakiness. Cadence driven by the Phase 0
census, not by the paper's ~150-commit figure.

---

## 5. Cross-cutting requirements

- **Migrations**: `0129`–`0134` as sketched in §3.1 (Phase 5 alters the
  quarantine policy table and needs one too); re-check the head before each
  (`database.single-alembic-head`), and implement real `downgrade()` bodies.
- **Feature flags**: one per phase (`flaky.calibration_gate`,
  `flaky.continuous_score`, `flaky.systemic_clusters`,
  `attribution.verdict`, `attribution.pr_checks`, `quarantine.closed_loop`).
  Default **off**; enable per project.
- **Quality gates**: new services must satisfy transaction-boundary discipline
  (routers own `commit()`), authorization guards on any new path param,
  effective-suite rules on suite-filtered queries, structlog kwargs, and PII
  redaction at log boundaries.
- **Testing**: each phase adds regression tests under `backend/tests/regression/`
  that fail before the change. Guard the **class**, not the instance — e.g. a
  test that iterates the verdict enum so a new member cannot silently go
  unrendered (the vocabulary-subset defect class that produced F-074, F-078 and
  UAT-002).
- **Verification**: deploy to the homelab and verify live, not just in unit
  tests (`frontend/probe-live.config.ts`, `verify` skill).
- **AI-trust discipline**: no fabricated confidence — every score and verdict
  carries provenance and a recorded threshold check (Epic 15 precedent).

---

## 6. Sequencing summary

```
Phase 0  Measure + environment      ── gates Phase 2 ─┐
Phase 1  Timeline / retries / load     (independent)  │  can run in parallel
Phase 2  Calibration + score          ────────────────┤
Phase 3  Systemic clusters            ────────────────┤
Phase 4  Attribution verdict  ← needs D6 (denominators)
Phase 5  PR checks + quarantine loop
Phase 6  Detection timing (lowest)
```

Relative effort (estimates, not commitments): Phase 0 **M**, Phase 1 **S–M**,
Phase 2 **M**, Phase 3 **M**, Phase 4 **M–L**, Phase 5 **S–M**, Phase 6 **S–M**.

**Recommended start**: Phase 0 and Phase 1 together. Phase 0 is measurement that
de-risks everything downstream; Phase 1 delivers visible value immediately and
depends on nothing but P0-1.

---

## 7. Explicit non-goals

- **More autonomous agent capability.** The research does not support
  prioritising Fixer-style autonomy above these items: practitioner usage ranks
  automated techniques last, and the adoption literature says unused
  sophistication is the norm. Ship the verdict surfaces first.
- **Chasing the 81% detection rate.** Self-reported, undefined denominator,
  scoped to "certain products" (§2.5).
- **A flaky burndown to zero.** Structurally impossible (§2.2).
- **Auto-suppression of failures.** §2.3, pending D2.
- **Release-gate discount policy.** Unevidenced (open Q2); needs D3, not a guess.
