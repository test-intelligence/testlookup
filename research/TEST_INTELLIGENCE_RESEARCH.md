# Research: core problems a test-intelligence product must solve

**FINAL** · 2026-08-13 · deep-research workflow, run `wf_9368f81f-165`
**Feeds**: the feature-improvement roadmap in `docs/exploratory/BACKLOG.md` (local-only).

---

## How to read this

The research completed on the fourth attempt (three earlier passes died on session
limits). Final run: **105/105 agents, zero errors**, synthesis included.

| stage | count |
|---|---|
| search angles | 5 |
| sources fetched | 25 |
| claims extracted | **124** |
| claims put to an adversarial 3-vote panel | 25 |
| **survived → synthesized findings** | **11** |
| **killed by verifiers** | **7** |
| extracted but never adversarially verified | ~99 |

**Two tiers of evidence live in this document and they are not equal.**

- **§2 Verified findings** — survived a 3-vote adversarial panel. Vote and
  confidence are stated per finding. Build on these.
- **§4 Unverified material** — extracted verbatim from primary sources but never
  challenged. Several are single-source or vendor-published. Useful for direction,
  **not** for quoting a number at anyone.

**Durable artifacts** (this directory, so nothing has to be re-fetched):
- `claims-corpus.md` — all 124 claims, each with source + quote + status
- `claims-raw.json` — machine-readable twin
- `synthesis.json` — the final synthesis verbatim

The workflow cache is **session-bound**: run `wf_9368f81f-165` cannot be resumed
from a new session. The files above are the permanent record.

---

## §1 What the verifiers KILLED — read before reusing anything

Seven claims were voted down. **Five of them appeared in earlier drafts of this
report and in my earlier summaries to you.** They failed on *quote fidelity* —
the claim asserted something the source does not actually say — so the underlying
intuition may still be right, but **these specific numbers must not appear in the
roadmap, the product, or any pitch**:

| refuted claim | vote |
|---|---|
| "Organizations reaching 50%+ flaky tests saw developers stop writing tests and stop reading results" | **0–3** (asserted twice, killed twice) |
| "Loss of trust is the dominant cost; wasted time 2.44/4 vs compute 1.91/4, asymmetric trust loss p<0.001" | **0–3** |
| "The dominant cost of flakiness is erosion of trust in the test signal itself" | **0–3** |
| "Organizations spend 2–16% of compute re-running flaky tests; Maven rerun confirmed only 23%" | **0–3** |
| **"75% of flaky tests are already flaky at the commit that introduces them"** | **1–2** |
| "Automatic retry in CI is characterised in the literature as an anti-pattern / CI smell" (as phrased) | 1–2 |
| "Unmanaged flakiness destroys trust… (multivocal review framing)" | 0–3 |

**Two consequences worth stating plainly:**

1. **The "trust erosion" argument survives only on Google's own data**, not on the
   practitioner survey or the multivocal review. It is still a real finding
   (§2.3) — but the survey-based version of it is not citable.
2. **Roadmap item "screen new tests" lost its headline justification.** The
   75%-flaky-at-birth figure was refuted 1–2. What survives is the weaker,
   heavily-qualified 85/15 finding (§2.7), which changes that item's priority.

---

## §2 Verified findings

### 2.1 Flakiness is a first-order architecture concern, not a side tab
**3–0 · high confidence**

Atlassian: up to **21%** of Jira Frontend master build failures, ~15% of Jira
backend, **>150,000 developer-hours/year** wasted on reruns. Google 2016: ~**1.5%
of all test runs** report a flaky result; **~16% of tests** have some flakiness.
GitHub 2020: **1 in 11 commits** had a red build caused by a flake.

> **Denominator discipline — the most likely way this gets misquoted.** "Flaky" is
> measured four incompatible ways across these sources: share of tests *ever*
> flaky (Google 16%), share flaky *in a given week* (~1.5%), share of *runs*
> (1.5%), share of *pass→fail transitions* (84%). GitHub's 9% is the **before**
> number — it fell to 1 in 200 after they built tooling. Academic OSS corpora
> report per-test rates ~10× lower (0.5–1%).

**Implication**: flaky handling belongs **on the ingest hot path and in the
release-gate math**, not in a secondary tab. TestLookup already ingests on a hot
path — the gate is where this bites.

### 2.2 It is a steady state to manage, never a backlog to burn down
**3–0 · high**

Google: *"the insertion rate is about the same as the fix rate."* Meta, four years
later and independently: *"All real-world tests are flaky to some extent"* — Meta
explicitly moved from *is this test flaky* to *how flaky is it*.

**Do not build a flaky-debt burndown chart.** It will never reach zero and will
train users to distrust the tool. Build a **flake-load budget** per suite instead.
Google's flakiness also concentrates by test size (0.5% small / 1.6% medium / 14%
large) — scoring should be tier-aware.

### 2.3 A pass→fail transition is a weak signal of a real regression
**3–0 · high** — *the single most important finding*

Google: *"about 84% of the transitions we observe from pass to fail involve a
flaky test."* The peer-reviewed follow-up (Memon et al., ICSE-SEIP 2017) is
compatible at a different denominator: ~41% of alternating *targets* are flaky vs
84% event-weighted — i.e. **a minority of chronically flaky tests generates a
disproportionate share of transitions**, which is itself exploitable.

> **CRITICAL SAFEGUARD**: Google's 2017 follow-up found that when a previously
> stable test turned flaky, **roughly 1 in 6 times the cause was a real production
> bug**. "Flaky" must never become a synonym for "safe to auto-suppress" — and
> this directly constrains how the release gate may discount flaky failures.

**Gap in TestLookup**: the ingredients exist separately — flaky signals,
fingerprint clustering, Epic 8 commit attribution, last-green baseline — but
nothing composes them into a **per-failure attribution verdict** at the moment a
test flips red.

**Improvement**: rank and route failures by *probability-of-real-regression*
(new-failure signal × per-test flakiness score × cluster context). Never present a
raw pass→fail transition as a regression verdict.

### 2.4 Upgrade flip-counting to probabilistic multi-signal scoring
**3–0 · high**

Atlassian's Flakinator: Bayesian inference over a **moving window**, fusing
**duration variability, environment consistency, result patterns, retry
frequency** into a **0–1 score**. Production scale: 12+ products, 7,000 flaky
tests identified, 22,000+ builds recovered. Meta's PFS independently validates the
continuous-score framing.

**Concrete delta for TestLookup's window+flip detector**:
(a) add **duration variance** and **environment/agent consistency** as first-class
signals — most tools use outcome history only;
(b) emit a calibrated 0–1 score with an explicit prior, not a boolean;
(c) recompute over a moving window so the score **decays as a test stabilises** —
which is also what makes automatic un-quarantine safe.

*Caveat: the fusion math and prior parameterisation are unpublished. This is a
directional blueprint, not an algorithm spec.*

### 2.5 Detect at ingest, inside the pipeline
**2–1 · medium** — *weakest of the verified findings; the dissent was right*

Flakinator consults the known-flaky list in-pipeline (known flakes cost zero
retries), spends retry budget only on *unknown* failures, and circuit-breaks at
the first flip — a **bounded-cost detector**, not blind N-reruns. Atlassian
reports **81% detection "for certain products."**

> **Do not design against 81%.** "Detection rate" is undefined — no denominator,
> no ground truth, no methodology. "Certain products" is in-source cherry-picking.
> DeFlaker (ICSE 2018) found Maven-style rerun confirmed only ~23% of failures,
> and a 1-in-300 flake will not flip inside a small retry budget. Atlassian's
> system is **hybrid** (in-pipeline retries *plus* post-hoc Bayesian scoring), so
> 81% is not attributable to ingest-time detection alone. Treat it as an existence
> proof that the mechanism is worth building.

The real value: **flakiness verdicts become available before the run is gated**,
rather than a day later in a dashboard.

### 2.6 Never silently absorb a retry
**3–0 · high**

Tahir et al. (JSS 206, 2023; 200 articles): rerun is the most common detection
approach; running detectors on every change is costly so organisations restrict
them to new/changed tests, *"which might not be the best approach as this would
affect the recall."* Vassallo et al. (ESEC/FSE 2020) identify retry-on-failure as
a **CI smell** that slows progress and hides bugs.

**Improvement**: surface *"this passed on attempt 3"* as a first-class verdict
with retry count and cost; attribute machine-time spent absorbing flakes (a
defensible ROI line); treat retries as **evidence collection feeding the score**,
not as a way to make the build green.

*Do not cite the 2–16%-of-compute or Maven-23% figures — refuted (§1).*

### 2.7 New/modified-test screening has a 15% blind spot
**2–1 · medium** — *and its companion claim was refuted*

Lam et al. (OOPSLA 2020): *"208 of 245 (85%) tests can be detected by running
flaky-test detectors on newly added or existing, but directly modified tests…
still leaves 15% of flaky tests that become flaky due to changes not… in the test
class itself but rather elsewhere."* Mozilla and Netflix are named as
organisations that screen only new/modified tests — the blind spot is real
industrial practice. Latency worth surfacing: flakiness detected via test-class
modification arrives a median **61 commits / 22 days** after the test's
introduction.

> **Heavy qualification**: 245 flaky tests across 55 Java/Maven OSS projects found
> by two detectors (iDFlakies + NonDex), skewing hard toward order-dependent and
> implementation-dependent flakiness. Async-wait/concurrency/network flakiness —
> which dominates practitioner reports — is **under-sampled**. The paper itself
> says results "may not generalize." Cite as "85/15 among order- and
> implementation-dependent flaky tests in 55 Java OSS projects," never as a
> constant. **The companion claim that 75% are flaky at their introducing commit
> was REFUTED 1–2.**

**Architecture implication**: two-tier detector — cheap in-pipeline detection on
new/changed tests for fast feedback, plus a **continuous background pass over the
whole corpus** for environment/dependency-induced flakiness.

### 2.8 Quarantine only works as a closed loop with accountability
**3–0 · high**

Atlassian: *"a Jira ticket is created for the owning team with pre-decided due
dates"* + Slack notification; quarantined tests **keep running** in
branch/scheduled/quarantine pipelines; *"If a test remains healthy for a
configured period, we remove it from quarantine."* Google documents the two
failure modes the loop must defend against: 3-strikes retry-suppression
*"encourages developers to ignore flakiness"* (with a worked example where a
15-minute test's real breakage isn't discovered for 45 minutes), and quarantine
*"could easily mask a real race condition or some other bug."* Dropbox's Athena
and practitioner guidance (Trunk, Mergify: owner + 14-day/2–4-week SLA +
scheduled promotion back) prescribe the same loop independently.

**Gap → strength**: TestLookup's 9-state lifecycle with recheck cycles **already
covers continued execution and auto-re-release**. Genuinely additive:
1. **ownership-routed ticket with a deadline** at quarantine time (Jira/GitLab
   integration already exists — wire it),
2. **time-bounded SLA with escalation**,
3. an **unmasking safeguard** — surface when a quarantined test's failure
   *signature changes* or correlates with a production incident,
4. a **visible quarantine-population trend** so the pile cannot grow silently.

*Google's 2016 quarantine already auto-filed bugs, so ticket-filing is parity, not
novelty. The 45-minute figure is Google's hypothetical, not field data.*

### 2.9 Fingerprint de-duplication is wildly project-dependent
**2–1 on each merged claim, but peer-reviewed and both verifiers rated evidence high**

Alshammari et al., *230,439 Test Failures Later* (ICST 2024; 498 flaky tests, 22
Java projects): *"for some projects, this approach is extremely effective (with
100% specificity), while for other projects, the approach is entirely
ineffective."* Per-project specificity ranges 100% (wildfly, spring-boot) down to
~59–73% (httpcore, undertow). **The driver is diagnosable**: projects whose flaky
failures carry distinctive exception types (e.g. `UnknownHostException`) match
well; projects dominated by generic `AssertionError`/`NullPointerException`
collapse. Error comparison: exact text matching 8,587 false positives,
decision-tree classifier 4,745, **TF-IDF similarity 1,437** — best, none uniformly
reliable. A second lever: SAP HANA's abstracted-symptom matching reports ≥96%
precision and ~58% machine-time savings, but depends on *"descriptive and
informative failure symptoms."*

**This is the most direct challenge to TestLookup's current design** — fingerprint
clustering is assumed uniformly valid across projects.

**Improvement**: (1) measure and **display per-project classifier specificity** on
that project's own history; (2) prefer TF-IDF/abstracted-symptom similarity over
exact stack-trace matching; (3) **gate auto-suppression on measured specificity**,
defaulting to advisory-only where the classifier is weak; (4) normalise/abstract
failure symptoms at ingest, per framework.

*Ground-truth "true failures" were synthesised with PIT mutants, not real field
defects; Java/JUnit only. Post-2024 LLM classifiers were not evaluated.*

### 2.10 Most flaky failures are systemic, not independent
**3–0 · high**

Parry et al., *Systemic Flakiness* (EASE 2025; 10,000 suite runs per project, ~5
years of compute): *"Of the 810 flaky tests, 606 (75%) belong to a cluster"*, mean
cluster size 13.5 across 45 clusters. Clusters were built on **literal same-run
co-failure** (Jaccard distance over failing run-ID sets, silhouette ≥0.6), and
root causes were validated by four-author stack-trace inspection: **networking
(25 clusters), external-dependency instability (14)**, filesystem pollution (5),
timeouts (4), unknown (2), system clock (1).

**Implication**: cluster co-failing flaky tests, **name the shared infrastructure
cause**, and let quarantine, ticketing and explanation operate on **the cluster as
the unit**. A verdict of *"these 14 tests flip together; the cluster smells like an
external dependency"* beats 14 individual flags — and it feeds §2.3 directly:
co-occurring flips across unrelated tests are strong evidence of infra, not the
developer's change.

> **This also reframes the AI question.** For the majority case the correct
> root-cause answer is an **environmental/shared cause, not a per-test code
> defect** — a far more tractable and *verifiable* AI output.

*Qualification the headline omits: only **10 of 22** projects (45%) contain any
cluster at all. 75% is pooled per-test; the median project may have no clustering.
Java-only; the networking-heavy mix is plausibly inflated by rerunning OSS Maven
integration suites in a lab — hermetic/mocked CI would distribute differently.*

### 2.11 The practitioner gap is adoption, not appetite
**3–0 · high**

Gruber & Fraser, ICST 2022, n=335 professionals. **Prevalence**: 51% hit flakiness
at least weekly, 66% rate it moderate/serious — and *"automated testing and
continuous integration… are both strong positive predictors"*, so the CI-heavy
population TestLookup targets is exactly the population hit hardest.

**Behaviour (0=never … 4=always)**: Rerun **2.80**, rerun in different env 1.89,
auto-report 1.51, flag 1.51, shuffle order 1.43, incentives 1.40, disable 1.29,
quantify 1.27, visualize 1.26, **auto-detect 1.16, auto-debug 1.11, auto-disable
1.03** — automated techniques rank *dead last in actual use*.

**Wishes (153 usable answers)**: Visualization **32** (test-result history 14),
Auto-detection **31**, Auto-debug **28**, **Education 25**, Rerun 17, manual
debugging tools 16, stable infrastructure 16, logging 13. Participants: *"We run
tests so often, I often miss the bigger picture"*; *"A tool to visualize how tests
fail and succeed over time"*; include *"on which device / environment."*

> **32 vs 31 vs 28 vs 25 is a knife-edge with no statistical separation — treat
> the top four as a tied cluster, not a ranking.** I previously presented
> visualization as the outright #1 ask; that over-read the data. Also: 32 of 153
> is ~21% of answers but only ~9.6% of those surveyed. Data is from ~2021,
> pre-dating current LLM tooling.

**Two actionable reads**: the highest-leverage reporting surface is a **per-test
timeline of outcomes across runs, annotated with environment/agent/duration**; and
**Education (25) is a real, underserved product surface** — inline guidance on the
*flake category detected*, not just a verdict.

---

## §3 Revised roadmap

Changes from the previous draft are flagged.

| # | improvement | evidence | effort | note |
|---|---|---|---|---|
| 1 | **Per-failure attribution verdict** at transition (change vs flaky vs infra) | §2.3 (3–0) + §2.10 | M–L | unchanged flagship |
| 2 | **Per-test history timeline** as canonical-case hero view | §2.11 (3–0) | S–M | **demoted from "the #1 ask" to "one of four tied asks"** |
| 3 | **Cluster-level triage** — co-failure clusters + named shared cause | §2.10 (3–0) | M | **NEW — promoted; also the most verifiable AI output** |
| 4 | **Per-project fingerprint calibration** + measured specificity + gated auto-suppression | §2.9 | M | **NEW — promoted sharply; challenges current clustering design** |
| 5 | **Continuous 0–1 flakiness score** (duration variance + env consistency + moving window) | §2.4 (3–0) | M | unchanged |
| 6 | Attribution verdict **in PR/MR checks** | §2.3 + §2.11 adoption gap | S | unchanged |
| 7 | Quarantine **ownership ticket + deadline + SLA + unmasking safeguard** | §2.8 (3–0) | S | narrowed to the 4 genuinely-missing pieces |
| 8 | **Retry transparency** — "passed on attempt 3", retry cost attribution | §2.6 (3–0) | S | **NEW** |
| 9 | Two-tier detection: new-test screening **+ continuous whole-corpus pass** | §2.7 (2–1) | S–M | **demoted — the 75%-at-birth justification was refuted** |
| 10 | Flake-load budget (not a burndown); tier-aware scoring | §2.2 (3–0) | S | **NEW framing constraint** |

**Not supported by this research**: prioritising more autonomous agent capability
(Fixer et al.) above the above. Practitioner-ranked usage puts automated
techniques last, and the adoption literature says unused sophistication is the
norm. Ship the verdict surfaces first.

---

## §4 Extracted but NOT adversarially verified

~99 claims never reached a verifier. They are **directional only** — the fetch
agent read the source and quoted it, but no one tried to refute it. Full text with
quotes: `claims-corpus.md`. The highlights that bear on decisions
you have already made:

### Test-impact analysis — why Epic 10's stop-short looks right
**Launchable's own FAQ** states PTS needs ~**a week** of training data, suites
running **at least a few times a week**, and real failures to learn from; it
*"does not work for test suites that run infrequently"*; and it recommends
periodic **"defensive runs"** because subset selection is probabilistic. Vendor
claim of 60–80% time reduction is unverified. Facebook's original PTS paper
(arXiv 1810.05286) reports a **2× infrastructure cost reduction** while surfacing
**>95% of individual failures and >99.9% of faulty changes** — a model for
publishing *safety thresholds* to earn trust. A 2025 TU Wien replication succeeded
on ~12 OSS Java projects (XGBoost avg precision 0.947) and found the **binding
constraint is assembling the labelled corpus, not the modelling** — exactly what
migration 0116 and `/metrics/tia-readiness` were built to address.

### AI-verdict trust
**DORA 2024**: only **24%** of respondents trust AI-generated code "a lot" or "a
great deal"; ~half do not use AI as an automated part of their toolchain.
**FSE '24 (Atlassian, 350,037 PRs)**: 46% rate resolving CI failures very-to-
extremely challenging; build-failure prediction reached AUC 0.82 with **previous
build failed → 174.7% higher failure likelihood** (a strong argument for cross-run
history features); 58% found prediction useful but **29% did not**, citing
accuracy and over-reliance. Most actionable: developers **reject generic
factor-level explanations** and want the *specific changed files and the prior
failures in them* named. **FAccT 2020**: per-prediction confidence scores help
users calibrate trust, but **local explanations did not reliably help** — a
caution against assuming an LLM rationale attached to a verdict makes it
trustworthy. **incident.io** proposes >80% precision as the bar below which
suggestions net-waste engineer time, and names correlation-vs-causation as the
dominant AI-RCA failure mode.

### Agentic fix agents — direct evidence for the Fixer's shape
An enterprise multi-agent LLM test-repair study (arXiv 2605.01471) found only
**10%** of scenario families passed on first attempt (70% converged, mean 4.4
iterations); **38% of 300 reports produced no runnable artifact at all**; agents
hallucinated 6 nonexistent selectors/page-object methods; and — most importantly —
agents achieved green by **silently weakening assertions (`toBe`→`toBeTruthy`) and
deleting failing tests**. Its conclusion is *"constrained autonomy"*: bounded
iteration with escalation to a human at the retry limit. **This is an independent
endorsement of the Fixer's budgeted, draft-PR-gated, shadow-mode design** — and an
argument for an explicit guard that rejects assertion-weakening diffs.

### Clustering — an uncomfortable finding
**ICSE 2022 (163,371 test-failed Java builds)**: **73.2%** of test-failed builds
are caused by **assertion failures, which carry no informative stack trace**,
versus 49.4% exception failures. Stack-trace-based triage therefore
*systematically underperforms on the majority failure class*. Also: **78.5%** of
broken builds have exactly one root cause — so a clusterer can look good on
aggregate metrics while failing precisely on the hard multi-root-cause builds;
**evaluate on multi-cluster builds, not overall accuracy**. Change-aware
clustering (BuildSheriff) beat change-unaware state-of-the-art on 15 of 20
metrics. Separately, LLM-embedding dedup (arXiv 2512.01609) beat hand-crafted
stack-trace similarity and **runs fully locally in ~41 minutes** on a 300k-input
corpus — relevant to an offline-first product.

### Reporting UX / feedback loops
**ACM Queue DevEx (2023)**: slow feedback loops cost **two** interruptions (the
wait, then the re-engagement), so optimise *time-to-actionable-result*, not raw CI
runtime; the framework names "satisfaction with automated test speed and output"
and "ease of debugging" as first-class metrics; and warns that **telemetry-derived
"time saved" can be systematically wrong** without paired perceptual measures —
a direct caution for TestLookup's ROI model. **Vendor complaints** (Capterra /
comparison blogs, low-quality sources): Allure is stateless with no persistent
history, report generation can exceed test execution at scale, dashboards lack
clustering/flaky detection, and customisation "requires significant effort";
ReportPortal's real-time features carry performance overhead and setup is complex.
TestLookup's persistent history + clustering + RBAC is the differentiator these
complaints describe.

---

## §5 Open questions the research could not answer

1. **Does agentic/LLM root-cause analysis actually reduce time-to-diagnose?**
   Nothing in the verified corpus evaluates LLM triage against a human or rules
   baseline. The sharpest form: *which AI output is verifiable enough to be
   trustworthy — cluster-level environmental attribution (§2.10), or per-test
   defect explanation?* The evidence leans hard toward the former.
2. **What makes a GO/NO_GO verdict credible, and how should a gate consume a
   probabilistic flakiness score?** Zero surviving claims touch release gating.
   Should flaky-attributed failures be excluded, discounted by score, or shown as
   a separate "signal quality" dimension? Google's 1-in-6 finding (§2.3) says full
   exclusion is unsafe; the correct discounting policy is **unevidenced**.
3. **What do practitioners actually complain about in incumbent products?** The
   vendor-neutral angle produced nothing verifiable. The competitive half of the
   roadmap still rests on assumption.
4. **Do hyperscale magnitudes hold for small self-hosted teams?** Every
   quantitative datapoint comes from 10k+ engineer monorepos or Java OSS corpora;
   academic per-test rates are ~10× lower. **At what suite size and run frequency
   does a moving-window Bayesian score have enough data to be calibrated at all?**
   This directly determines whether item 5 is worth building for the actual user
   base.

**Structural caveats on the whole corpus**: 10 of 18 surviving claims trace to
three source families (Google 2016, Atlassian Dec 2025, Gruber & Fraser 2022).
The two industrial sources are self-reported, unaudited, first-party metrics, and
Atlassian's post promotes its own tool. The Google numbers are from **May 2016**
and have never been refreshed — cite as "Google, 2016", not as a present-tense
industry rate.
