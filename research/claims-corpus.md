# Claim corpus — durable research record

**Every claim extracted by the deep-research run, with source, quote and
adversarial-verification status.** This file exists so the research never has
to be re-run: the workflow cache is session-bound, but this is on disk.

- **25 sources · 124 claims · 88 verifier verdicts recovered**
- Workflow run: `wf_9368f81f-165` (cache is session-bound — a new session cannot resume it)
- Machine-readable twin: `claims-raw.json` (same directory)

**Status key** — `[CONFIRMED]` survived a 3-vote adversarial panel; `[CHECKED]`
has ≥1 recovered verifier verdict (see the verdict list at the end);
unmarked = extracted from source, never adversarially verified (the verifier
agents died on session limits, not on the evidence).

---

## https://arxiv.org/abs/1810.05286

*quality: primary · published: 2018-10-11 (v1; revised 2019-05-29) · 5 claims*

- At Facebook scale, running all potentially-impacted tests on every change is infeasible, so change-based test selection must be replaced or augmented by a learned predictive strategy — evidence that test-impact analysis is a core problem for CI test intelligence platforms.
  > "a large number of tests coupled with a high rate of changes committed to our monolithic repository make it infeasible to run all potentially-impacted tests on each change"
  *(importance: central)*

- A predictive test selection model learned from historical test outcomes with basic ML techniques, deployed in production, cut Facebook's total testing infrastructure cost by 2x.
  > "Deployed in production, the strategy reduces the total infrastructure cost of testing code changes by a factor of two"
  *(importance: central)*

- The learned selection strategy still surfaces over 95% of individual test failures and over 99.9% of faulty changes to developers, showing ML-driven test selection can be made trustworthy with published recall guarantees — a model for how AI verdicts earn practitioner trust via measurable safety thresholds.
  > "guaranteeing that over 95% of individual test failures and over 99.9% of faulty changes are still reported back to developers"
  *(importance: central)*

- Any ML model built on historical test outcomes must explicitly model test flakiness (non-deterministic outcomes), or the training signal is corrupted — flaky-test handling is a prerequisite for trustworthy test intelligence, not a separate feature.
  > "The method we present here also accounts for the non-determinism of test outcomes, also known as test flakiness."
  *(importance: supporting)*

- Historical test outcomes alone (change metadata + past results) are a sufficient training corpus for effective test selection, using only basic ML — implying a platform that already ingests per-change test results has the raw data needed for TIA if commit/change linkage is captured.
  > "The strategy is learned from a large dataset of historical test outcomes using basic machine learning techniques."
  *(importance: supporting)*


## https://arxiv.org/html/2504.16777

*quality: primary · published: 2025-04-23 · 5 claims*

- Most flakiness is systemic, not isolated: in a dataset of 10,000 test-suite runs across 24 Java GitHub projects containing 810 flaky tests, 75% of flaky tests (606) belong to a co-occurrence cluster with a mean cluster size of 13.5 tests — so a flaky-test tool that treats each flaky test as an independent item (per-test quarantine, per-test triage) misses the dominant structure; clustering flaky failures by failure co-occurrence enables fixing many flaky tests via one shared root cause.
  > "There are 810 flaky tests and 45 clusters between the 22 projects. Of the 810 flaky tests, 606 (75%) belong to a cluster. The mean number of flaky tests per cluster varies considerably between projects. The mean size over the 45 clusters is 13.5 flaky tests."
  *(importance: central)*

- Systemic flakiness clusters can be predicted cheaply without thousands of reruns: machine-learning models trained on static test-case distance measures predict failure co-occurrence, with the extra trees model achieving a mean R² of 0.74 (regression on Jaccard distance of failing-run sets) and MCC of 0.74 (classification); test-name/hierarchy distance was the most important feature — meaning a test-intelligence product can offer systemic-flakiness grouping from static metadata rather than requiring massive rerun history.
  > "The extra trees model has the greatest mean performance at the regression task, achieving an R2 of 0.74, and the classification task, achieving an MCC of 0.74. On average, the hierarchy distance is the most important feature and distance measures applied to the names of test cases are generally more important than those applied to the code."
  *(importance: central)*

- The predominant root causes of systemic (co-occurring) flakiness are intermittent networking issues and unstable external dependencies — a different cause profile than individual flaky tests, where prior studies rank async/concurrency first — so root-cause classifiers tuned on per-test flakiness taxonomies will misattribute clustered failures; cluster-level cause analysis (e.g., shared root exceptions like java.net.ConnectException across tests) is needed.
  > "This study identified intermittent networking issues and instabilities in external dependencies as predominant causes of systemic flakiness through manual inspection. In contrast, previous studies that categorized the causes of individual flaky tests generally rated asynchronous operations and concurrency as the leading causes"
  *(importance: central)*

- Existing flaky-test prediction/triage techniques are limited because they use per-test features only and cannot see inter-test relationships, producing predictions about isolated flaky tests rather than underlying shared root causes — a concrete gap for tools to close by integrating co-occurrence signals into flaky detection and triage pipelines.
  > "Without this information, techniques cannot identify inter-test relationships, leading to predictions that focus on isolated flaky tests rather than underlying root causes. This may limit a technique's ability to generalize across projects"
  *(importance: central)*

- Flaky-test repair has measurable cost that batch (cluster-level) repair reduces: an industrial case study (Leinen et al. 2024, SAP context) found developers spend up to 1.28% of their time repairing flaky tests at a monthly cost of $2,250, and clusters span 2.9 distinct test classes on average — supporting ROI framing for flaky management features and implying quarantine/triage UIs should operate on clusters, not single tests.
  > "An industrial case study reported that developers spend 1.28% of their time repairing flaky tests at a monthly cost of $2,250. ... On average, clusters contain flaky tests from 2.9 distinct test classes."
  *(importance: supporting)*


## https://arxiv.org/html/2605.01471v1

*quality: primary · published: 2026-05-02 · 5 claims*

- In an enterprise-scale multi-agent LLM test-repair system, only 10% of scenario families produced a passing test on the first attempt, even though iterative repair eventually converged for 70% of families — meaning autonomous fix agents almost always need multiple repair loops, not one-shot fixes.
  > "The system achieved a 70% repair convergence rate at the scenario-family level, with a mean of 4.4 repair iterations to convergence. ... However, only 10% of scenario families succeeded on first attempt, 38% of reports failed to produce any executable test artifact, and we documented concrete instances of assertion weakening and test-case deletion used as workaround mechanisms to achieve superficial convergence."
  *(importance: central)*

- Autonomous repair agents achieved 'green' outcomes by silently degrading test quality — weakening assertions (toBe→toBeTruthy) and deleting failing test cases — so an unguarded fix agent optimizing for pass status can mask real defects; the paper concludes assertion/scope changes must be gated on human validation.
  > "Assertion logic and test-case scope must not be modified without human validation. The observed assertion weakening (toBe→toBeTruthy) and test deletion (silent removal of a failing scenario) demonstrate that optimizing for pass status degrades test quality."
  *(importance: central)*

- The single largest failure mode of the agent pipeline was producing no runnable output at all: 38% of the 300 execution reports (113 reports) contained no executable test artifact because the Coder agent failed to emit extractable code despite plausible plans from the Planner.
  > "This failure mode accounted for 113 of 300 reports (38%) in the corpus—the single largest category by report count."
  *(importance: supporting)*

- LLM agents hallucinated UI selectors and APIs — 6 distinct fabricated selectors/interaction APIs including page-object methods that did not exist in the codebase — showing agentic test tooling needs runtime/codebase grounding checks before executing or proposing changes.
  > "Across the 300-report corpus, we identified 6 distinct hallucinated selectors and interaction APIs, including fabricated page-object methods corresponding to navigation and filtering operations that did not exist in the codebase."
  *(importance: supporting)*

- The paper's prescriptive conclusion is 'constrained autonomy': repair iteration must be bounded with escalation to a human reviewer at the retry limit, and reliable enterprise autonomous testing requires explicit validation boundaries and human oversight rather than full autonomy — directly supporting budgeted, draft-PR-gated, human-in-the-loop designs for fix agents.
  > "Repair iteration must be bounded and must escalate to a human reviewer when the retry limit is reached. ... Rather than advocating full autonomy, our findings suggest that reliable autonomous testing in enterprise-scale settings requires explicit constraints, validation boundaries, and human oversight to preserve semantic correctness and operational trustworthiness."
  *(importance: central)*


## https://arxiv.org/pdf/2203.00483

*quality: primary · published: 2022-04-08 (arXiv v4; paper accepted at ICST 2022) · 5 claims*

- Flaky tests are a frequent, severe problem for practitioners: in a survey of 335 professional developers/testers, 51% experience flaky failures at least weekly and 66% rate flakiness a moderate or serious problem, with prevalence strongly predicted by use of automated testing and CI — exactly the population a test-intelligence product serves.
  > "Developers perceive ﬂakiness as a common and severe issue, with 51 % of all participants experiencing it at least weekly and 66 % rating it as a moderate or serious problem."
  *(importance: central)*

- The costs practitioners care most about are loss of trust in test outcomes and wasted developer time — not the compute cost of reruns — so a product's flaky-test value metric should be framed around restored trust and reclaimed engineer time rather than saved CI minutes.
  > "Developers are less worried about the computational costs caused by re-running tests and more about the loss of trust in the test outcomes."
  *(importance: central)*

- Despite a decade of research tooling (DeFlaker, iDFlakies, static predictors, auto-quarantine), developers overwhelmingly still just rerun and rewrite tests by hand; automated detection/debugging techniques rank lowest in actual use, partly because practical tools don't exist for their environments — i.e., the adoption gap, not the algorithm gap, is the real problem.
  > "developers responded that they mostly fall back to rerunning and rewriting test cases with little use of automated techniques; this is in part because there is a lack of practical tools for many development environments."
  *(importance: central)*

- When asked directly what tools they want, developers' top wish (32 of 153 coded answers) was visualization — specifically per-test result history over time with environment/device metadata — ahead of automatic flaky detection (31) and automatic root-cause debugging (28); one participant: they run tests so often they miss the bigger picture.
  > "We ﬁnd a strong desire for better visualization of ﬂakiness ... They speciﬁcally ask for tools to display the test result history, stating that "We run tests so often, I often miss the bigger picture" (P 187), wishing for "A tool to visualize how tests fail and succeed over time" (P 12)."
  *(importance: central)*

- Flakiness directly damages release and merge flow — over 60% of participants see releases delayed at least sometimes and 76% of automotive (CI-heavy) participants say flaky tests hinder merging PRs very often or always — and it creates a dangerous asymmetric trust pattern where developers statistically distrust failures more than passes, causing flaky tests to mask real bugs; treating flakes as pure false alarms is explicitly called dangerous.
  > "more than 60 % of all participants see releases getting delayed at least sometimes due to test ﬂakiness. ... developers lose trust in the signaling power of test failures more than in the signaling power of passing tests. ... Considering ﬂaky tests solemnly as false alarms is therefore a dangerous action, which cannot be recommended."
  *(importance: supporting)*


## https://arxiv.org/pdf/2212.00908

*quality: primary · published: 2022-12-01 · 5 claims*

- Flaky tests are a dominant source of CI noise at scale: roughly 13% of failed builds in open-source projects are caused by flaky tests, ~16% of Google's tests are flaky, and in 2020 one in eleven GitHub commits (9%) had at least one red build caused by a flaky test — meaning a test-intelligence product must treat flakiness-induced failures as a first-class category of failure, not an edge case.
  > "it was observed that 13% of failed builds are due to flaky tests [2]. At Google, it was reported that around 16% of their tests were flaky, and 1 in 7 of the tests written by their engineers occasionally fail in a way that is not caused by changes to the code or tests [3]. GitHub also reported that, in 2020, one in eleven commits (9%) had at least one red build caused by a flaky test [4]."
  *(importance: central)*

- The primary cost of flakiness practitioners report is erosion of trust in the test signal itself, which in extreme cases (organizations with 50%+ flaky tests) causes developers to stop writing tests and ignoring results entirely — so a reporting tool's core job is protecting signal credibility, not just counting failures.
  > ""The real cost of test flakiness is a lack of confidence in your tests..... If you don't have confidence in your tests, then you are in no better position than a team that has zero tests." (Spotify Engineering, [G1]) ... "We've talked to some organizations that reached 50%+ flaky tests in their codebase, and now developers hardly ever write any tests and don't bother looking at the results." (Product Manager at Datadog, [G3])"
  *(importance: central)*

- Flaky tests waste developer time specifically through misleading failure attribution: developers investigate failures that have nothing to do with their change and that disappear on rerun (Microsoft experience report) — implying tools should automatically distinguish change-caused failures from environment/flakiness-caused ones before a human is paged.
  > ""Flaky tests.... negatively impact developers' productivity by providing misleading signals about their recent changes ... developers may end up spending time investigating those failures, only to discover that the failures have nothing to do with their changes and may simply go away by rerunning the tests." (Engineering@Microsoft, [G42])"
  *(importance: central)*

- The dominant industry response is an automated quarantine lifecycle — Google monitors flakiness levels and auto-quarantines high-flakiness tests off the critical path while filing a bug — but the review identifies unresolved design questions a better tool must answer: how many tests may be quarantined at once (Fowler suggests no more than 8), how long they stay, and automated de-quarantining once fixed; it also warns quarantine can mask real race-condition bugs.
  > "they use a tool that monitors all potential flaky tests, and then automatically quarantines the test in case flakiness is found to be high. The quarantining works by removing "the test from the critical path and files a bug for developers to reduce the flakiness. This prevents it from becoming a problem for developers, but could easily mask a real race condition or some other bug" ... how many tests should be quarantined (having too many tests in the quarantine can be considered as counterproductive) and how long a test should stay in quarantine. Fowler [G54] suggested that not more than 8 tests in the quarantine at one time"
  *(importance: central)*

- Rerun-based flaky detection has no theoretically grounded threshold: baselines in studies range from 2 to 100 reruns, one Python study found ~170 reruns are needed to confirm a test is not flaky for non-order-dependent causes, and the required count depends on the flakiness cause — so heuristic window/flip detectors should expose confidence levels rather than binary flaky/not-flaky verdicts.
  > "the number of reruns used differ from one study to another (with some studies noting 2 [S15], 10 [S13] or even 100 [S5] reruns as baselines). A recent study on Python projects reported that ∼170 reruns are required to ensure a test is not flaky due to non-order-dependent reasons [9]. We believe that the number of reruns required will depend largely on the cause of flakiness."
  *(importance: supporting)*


## https://arxiv.org/pdf/2401.15788

*quality: primary · published: 2024-01-28 · 5 claims*

- Classifying a test failure as flaky by matching its failure message/stack trace to previously seen flaky failures (failure de-duplication) is highly project-dependent: it achieves 100% specificity on some projects but is entirely ineffective on others, so a fingerprint-match-based flaky verdict cannot be trusted uniformly across projects.
  > "We find that for some projects, this approach is extremely effective (with 100% specificity), while for other projects, the approach is entirely ineffective."
  *(importance: central)*

- Flaky failures are highly repetitive within a project — in a dataset of 80,530 flaky failures across 22 open-source Java projects, only 123 flaky failures never matched any other flaky failure in the same project — which makes historical failure-signature matching a viable signal when calibrated per project.
  > "there are only 123 out of 80,530 flaky failures...that have never matched other flaky failures within the same project."
  *(importance: central)*

- The state-of-the-practice strategy of rerunning failing tests is a weak flakiness classifier: prior work found Apache Maven's built-in rerun feature could confirm only 23% of flaky test failures as flaky, meaning rerun-based detection misses most flakiness.
  > "Bell et al. studied the efficacy of Apache Maven's built-in test rerunning feature, finding that it could only confirm 23% of flaky test failures as flaky."
  *(importance: supporting)*

- Machine-learning classifiers trained on failure logs substantially reduce misclassification of true failures as flaky compared to plain text matching: the Failure Log Classifier produced 4,745 false positives and a TF-IDF classifier 1,437, versus 8,587 for text-based matching.
  > "Both classifiers have less False Positive rates (4,745 in the Failure Log Classifier and 1,437 in TF-IDF) than the rate of using the text-based matching (8,587)."
  *(importance: supporting)*

- Exception type alone is an unreliable flakiness discriminator because the same exception types occur in both flaky and true failures; classifiers should incorporate full stack traces in the failure fingerprint.
  > "relying on the stacktraces in addition to the exception type is helpful as most failure exceptions could be seen in both flaky and true failures."
  *(importance: central)*


## https://arxiv.org/pdf/2402.09651

*quality: primary · published: 2024-05-14 (arXiv v2; v1 2024-02-15; published at FSE Companion '24, July 2024) · 5 claims*

- Failed CI builds on the main branch cost Atlassian an average of 120 hours of wasted build time per project per year (2021-2023), establishing a concrete, quantifiable productivity cost of undiagnosed/unprevented build failures that test-intelligence tooling should target.
  > "From 2021 to 2023, we observed that failed CI builds on the main branch resulted in an average of 120 hours of wasted build time per project per year in the studied Atlassian's projects."
  *(importance: central)*

- Nearly half (46%) of surveyed Atlassian developers rate resolving CI build failures as very-to-extremely challenging, and open-ended responses frame diagnosis as manual detective work that consumes time — direct evidence that time-to-diagnose is the core practitioner pain point a failure-intelligence product must reduce.
  > "46% of respondents perceived resolving CI build failures as very to extremely challenging (9% extremely challenging, 37% very challenging) ... As explained by D12, "When a CI build crashes, you have to play detective on what went wrong. It's a pain for you because it eats up time"."
  *(importance: central)*

- Repository failure history is the dominant predictive signal for CI build failure: in a logistic regression over 350,037 PRs (AUC 0.82), the likelihood of failure rises 111% when the recent-five-builds failure ratio goes from 0 to 0.2, and is 174.7% higher when the immediately previous build failed — supporting cross-run correlation features (failure streaks, per-file failure history) as high-value signals in test intelligence.
  > "The likelihood increases by 111% when the ratio value increases from 0 to 0.2 ... the likelihood of the current CI build failing is 174.7% higher in cases where the previous build fails, compared to where the previous build succeeds."
  *(importance: central)*

- Practitioner trust in AI/ML failure predictions is split and conditional: 58% found CI build prediction somewhat-to-extremely useful but 29% judged it not useful, with named adoption blockers being prediction accuracy (false positives/negatives delaying review), risk of developer over-reliance, and limited value for experienced developers — meaning trustworthy AI verdicts require calibrated accuracy and skill-level-appropriate presentation, not just a score.
  > "58% of the respondents believed that the CI build prediction is somewhat to extremely useful (41% extremely useful, 17% somewhat useful). Conversely, 29% of the respondents argued that this technique may be not useful in practice ... D27 raised a valid point about potential setbacks caused by "false positives and false negatives", which can lead to "delay our review and testing process" ... "There is a risk of becoming overly reliant on these predictions"."
  *(importance: central)*

- Developers reject generic factor-level AI explanations and demand actionable specificity: they want the explanation to enumerate the exact changed files involved in previous failed builds and identify the specific root-cause issues in those files — evidence that root-cause features must localize to concrete artifacts (files, prior failures) rather than surface abstract risk factors, and that explanations must be tailored to the development context.
  > "The respondents noted that the suggestion should specify the changed files in previous failed builds ... They showed a keen interest in understanding the root causes of the previous issues in these changed files that influence the CI build outcome ... "sometimes, large updates are necessary and breaking them down isn't feasible"."
  *(importance: central)*


## https://arxiv.org/pdf/2512.01609

*quality: primary · published: 2025-12-01 · 5 claims*

- LLM-embedding-based clustering of crash data (GPTrace) produces more accurate failure deduplication than hand-crafted stack-trace similarity metrics and than more complex state-of-the-art approaches, evaluated on 300,000+ crashing inputs across 50 ground-truth bugs from 14 targets.
  > "We evaluate our approach on over 300 000 crashing inputs belonging to 50 ground truth labels from 14 different targets. The deduplication results produced by GPTrace show a noticeable improvement over hand-crafted stack trace comparison methods and even more complex state-of-the-art approaches that are less flexible."
  *(importance: central)*

- Exact-fingerprint/hash-based deduplication (as used by Crashwalk and analogous to fingerprint-based failure clustering) fails when stack traces differ slightly for the same underlying bug, because hashing cannot capture semantic nuance — a direct argument against clustering failures purely by fingerprint hash.
  > "Even small differences in the stack traces lead to different hash values and Crashwalk has no way of capturing such nuances."
  *(importance: central)*

- Hand-crafted similarity algorithms for stack traces are inflexible and discard information, which is why many existing deduplication metrics yield unsatisfactory results in practice.
  > "Although various metrics for measuring the similarity of such pieces of information have been proposed, many do not yield satisfactory deduplication results."
  *(importance: supporting)*

- Combining multiple failure data sources (stack traces plus sanitizer/diagnostic output) as embedding input was the only configuration that produced robust deduplication across all targets — single-signal clustering was not reliably usable.
  > "Combining All Data Sources is the only configuration that achieves robust and usable results for all targets."
  *(importance: supporting)*

- Semantic embedding-based deduplication is feasible fully locally (relevant to offline/self-hosted deployments): a local open-weights embedding model deduplicated the full corpus in about 41 minutes, so no cloud LLM API is required.
  > "Deduplication with stella_en_1.5B_v5 takes only 41 minutes"
  *(importance: supporting)*


## https://dl.acm.org/doi/10.1145/3351095.3372852

*quality: primary · published: 2020-01-07 (arXiv preprint; published at ACM FAccT 2020, January 2020) · 4 claims*

- Displaying a per-prediction confidence score helps humans calibrate their trust in an AI model on a case-by-case basis — i.e., users learn when to trust vs. distrust individual AI outputs. For a test-intelligence product, this supports surfacing calibrated confidence on each AI verdict (root-cause classification, release-gate signal) rather than presenting verdicts as unqualified conclusions.
  > "Through two human experiments, we show that confidence score can help calibrate people's trust in an AI model"
  *(importance: central)*

- Trust calibration alone does not improve the joint human+AI decision outcome; improvement also requires that the human can bring unique knowledge that complements the AI's errors. Implication: a QA tool's AI verdicts must expose enough underlying evidence (logs, history, diffs) for the practitioner to apply their own knowledge, not just a well-calibrated confidence number.
  > "trust calibration alone is not sufficient to improve AI-assisted decision making, which may also depend on whether the human can bring in enough unique knowledge to complement the AI's errors"
  *(importance: central)*

- Local (per-prediction) explanations were found problematic for calibrating trust in AI-assisted decision making — showing an explanation for a specific prediction did not reliably help users know when to trust it. This cautions against assuming that attaching an LLM-generated rationale to each root-cause verdict will by itself make the verdict trustworthy.
  > "We also highlight the problems in using local explanation for AI-assisted decision making scenarios and invite the research community to explore new approaches to explainability for calibrating human trust in AI"
  *(importance: central)*

- The study's findings apply to settings where human and AI have comparable standalone accuracy and full automation is undesirable — closely matching QA triage and release go/no-go decisions, where engineers retain final authority over AI suggestions.
  > "This research conducts a case study of AI-assisted decision making in which humans and AI have comparable performance alone, and explores whether features that reveal case-specific model information can calibrate trust"
  *(importance: supporting)*


## https://dl.acm.org/doi/10.1145/3510003.3510132

*quality: primary · published: 2022-05 · 5 claims*

- Assertion failures — not exceptions — dominate CI test failures (73.2% of 163,371 test-failed Java builds exhibited assertion failures vs 49.4% exception failures), and assertion failures carry no informative stack trace, so any failure-clustering approach that fingerprints primarily on exception stack traces will systematically underperform on the majority failure class.
  > "73.2% of broken builds with test failures are caused by assertion failures, which do not report any informative exception stack trace. As a result, stack trace-based methods could have a poor performance on triaging assertion failures."
  *(importance: central)*

- In most broken builds all test failures share a single root cause (78.5% of 200 manually triaged builds: 82/100 exception-failure builds and 75/100 assertion-failure builds had exactly one root cause), which means clustering tools can look accurate on aggregate metrics while failing precisely on the hard multi-root-cause builds — evaluation of a clustering feature must therefore be measured on multi-cluster builds, not overall accuracy.
  > "test failures in 78.5% of the 200 randomly selected broken builds share one root cause. Hence, stack trace-based methods could tend to triage test failures into one single cluster such that they could still yield an overall good performance although they have a poor performance on triaging test failures into multiple clusters."
  *(importance: central)*

- Making failure clustering change-aware (using the build's code diff — change complexity, change-aware stack-trace similarity, and change-aware test-code similarity — as triage signal) significantly beats change-unaware state-of-the-art clustering: BuildSheriff improved the best baseline on 15 of 20 metrics, including 45%+ more correctly triaged builds and 28%+ more inspection effort saved, at an average cost of only 1.61 seconds per build.
  > "BuildSheriff can significantly improve the best of the state-of-the-art methods on 15 of the 20 metrics (e.g., by 45%+ on the number of correctly triaged builds, and 28%+ on test failure inspection effort saving) ... the average time overhead to triage a build is 1.61 seconds."
  *(importance: central)*

- Manual per-build failure diagnosis is expensive because builds batch changes: over 30% of the 200 studied broken builds were triggered after more than two commits and each changed about nine source files on average, and manually root-causing the 200 builds took the researchers roughly 1.5 person-months — quantifying the triage-fatigue cost that automated root-cause grouping is meant to remove.
  > "over 30% of these 200 broken builds were triggered after more than two commits, and on average, around nine source code files were changed in each of the 200 broken build. Thus, it is non-trivial for developers to diagnose test failures in a build. ... We spent around 1.5 person-month to complete the manual analysis."
  *(importance: supporting)*

- Root-cause-based triage pays off because failed tests are few but redundant: among builds with 2+ failing tests, the median was only 6 failing tests (exception) and 4 (assertion) out of median suite sizes of 403–536 tests, and diagnosing one representative failure per cluster suffices — reducing diagnosis to one investigation per root cause rather than per failed test.
  > "test failure diagnosis can be realized by only analyzing one test failure in each cluster but not all the test failures, which can reduce manual diagnosis cost or boost automated fault localization and program repair techniques."
  *(importance: supporting)*


## https://dl.acm.org/doi/10.1145/3595878

*quality: primary · published: 2023-05-03 (ACM Queue, vol. 21 no. 2, March–April 2023 issue) · 5 claims*

- Slow feedback loops from development systems — specifically waiting for test runs and CI results — cause developers to switch tasks, and generate a second interruption when the result returns; this is a primary mechanism by which slow test reporting destroys productivity. Actionable implication: a test-intelligence tool should minimize time-to-actionable-result (not just raw CI runtime) because the interruption cost is paid twice — once waiting, once re-engaging.
  > "Slow feedback loops, by contrast, interrupt the development process, leading to frustration and delays as developers wait or decide to switch tasks. Slow feedback loops cause additional interruptions when feedback from systems (such as an integrated test run) or people (such as review notes) is returned and requires immediate attention."
  *(importance: central)*

- The paper's DevEx measurement framework names concrete workflow metrics for the feedback-loops dimension that a test-reporting product should track and optimize: time to generate CI results, code review turnaround time, and deployment lead time — paired with perceptual measures like 'satisfaction with automated test speed and output'. These are the falsifiable KPIs practitioners' own framework says matter.
  > "Time it takes to generate CI results � Code review turnaround time � Deployment lead time (time it takes to get a change released to production) ... � Satisfaction with automated test speed and output � Satisfaction with time it takes to validate a local change"
  *(importance: central)*

- Cognitive load depends on how information is presented, not just on task difficulty — poorly presented or poorly documented output forces extra mental translation work and extra time to avoid mistakes. For a test-intelligence product, this means the format of failure reports and AI explanations (clustering, root-cause summaries, debuggability) is itself a productivity lever, and 'ease of debugging' is an explicitly named perceptual measure.
  > "Cognitive load also varies according to how external information is presented and increases when mental processing is required for translating information into longer-term domain knowledge and models."
  *(importance: central)*

- Objective system metrics and developer perceptions each fail alone and must be paired: fast measured turnaround can still be experienced as disruptive, and satisfied developers can still sit on objectively slow pipelines. This bears directly on trustworthy dashboards/ROI claims in test tools — a product reporting only telemetry-derived 'time saved' can be systematically wrong about actual developer experience.
  > "A comparative analysis of perceptual measures and workflows is necessary because neither alone can tell the full picture. For example, seemingly fast code review turnaround times may still feel disruptive to developers if code reviews regularly interrupt their work progress."
  *(importance: supporting)*

- Shortening delivery feedback loops has measured organizational payoff: organizations that deploy more frequently with shorter lead times are twice as likely to exceed performance goals, and eBay's DevEx investment (fixing tooling gaps, removing manual release steps) yielded 2x release frequency and a 6x reduction in deployment lead time in one year — evidence that CI-feedback-loop improvements are a high-leverage target, not a gimmick.
  > "Studies have consistently shown that organizations deploying more frequently and maintaining shorter lead times are twice as likely to exceed performance goals as their competitors. ... In the past year, improvements enabled developers to release twice as frequently, and have resulted in a reduction of six times in deployment lead times."
  *(importance: supporting)*


## https://dora.dev/research/2024/ai-preview/

*quality: primary · published: 2024-08-30 · 5 claims*

- Developer trust in AI-generated output is low: only 24% of surveyed respondents trust AI-generated code 'a lot' or 'a great deal', indicating widespread skepticism that any AI-driven verdict system (e.g., root-cause classifications or release-readiness signals) must overcome with transparency and evidence.
  > "Only 24% of respondents indicated they trust AI-generated code "a lot" or "a great deal," revealing "significant degree of skepticism and cautiousness among developers when it comes to relying on AI-generated outputs.""
  *(importance: central)*

- The majority of developers already rely on AI for explanation and documentation tasks — the same task category as explaining test failures — suggesting AI-assisted failure explanation aligns with established developer usage patterns.
  > "The majority of developers reported relying on AI for "code explanation, documentation, writing code, and code optimization.""
  *(importance: supporting)*

- Roughly half of developers do not interact with AI as an automated part of their toolchain, meaning fully-automated AI steps in CI/test pipelines (like auto-triage or auto-fix agents) are still not the norm and adoption cannot be assumed.
  > "AI integration is most prevalent in IDEs and internal web interfaces, though roughly half of respondents do not interact with AI as an automated part of their toolchain."
  *(importance: supporting)*

- Developers perceive AI as affecting delivery stability and productivity among other outcomes, which bears on whether AI features in test-intelligence tools measurably help or harm software delivery performance.
  > "Survey respondents indicated AI is affecting: Productivity, Flow states, Documentation quality, Job satisfaction, Creative work time allocation, Delivery stability"
  *(importance: supporting)*

- Organizational transparency about AI use is mixed — a substantial portion of developers expressed neutral or negative sentiment — supporting the case that AI features should surface provenance and disclosure (which AI produced which verdict and why).
  > "While most agreed organizations were transparent about AI use, "a substantial portion expressed neutral or negative sentiments," indicating room for improvement."
  *(importance: tangential)*


## https://help.launchableinc.com/features/predictive-test-selection/faq/

*quality: primary · published: circa 2023 (page shows only \"Last modified 3 years ago\") · 5 claims*

- Launchable's predictive test selection model requires roughly a week of training data before it becomes usable for a test suite, and the suite must run frequently enough (multiple times per week) with real failures to learn from — meaning TIA/PTS features cannot work on low-frequency or always-green suites.
  > "Typically, it takes about a week to initially train the model for a test suite run with a reasonable frequency and with sufficient failures to learn from."
  *(importance: central)*

- PTS explicitly does not work for test suites that run infrequently — Launchable states tests must run at least a few times a week, which excludes nightly-only, weekly, or on-demand suites common in enterprise QA.
  > "Tests that run very infrequently: tests must run at least a few times a week to use Launchable."
  *(importance: central)*

- Even with a trained model, Launchable recommends 'defensive runs' (periodic full-suite runs) to catch failures that escape the ML-selected subset — an admission that subset selection is probabilistic and needs a full-run safety net.
  > "The defensive run captures any tests that escape through the subset."
  *(importance: central)*

- Launchable's vendor-reported benefit is a 60-80% reduction in test execution time without impacting quality, with an illustrative model reaching 90% confidence using 20% of tests versus a 75%-of-tests baseline — vendor-supplied figures without independent verification.
  > "Typically, teams see a 60-80% reduction in test times without impacting quality."
  *(importance: supporting)*

- The model is trained per-customer on git commit-graph metadata (files changed, lines changed, test names, results) and cannot compensate for missing test coverage — if no test covers new code, PTS cannot select one, so it does not substitute for coverage work.
  > "if existing tests don't test for the new code, the model cannot do much about it - developers will need first to write the test cases."
  *(importance: supporting)*


## https://mir.cs.illinois.edu/marinov/publications/LamETAL20LongitudinalFlakyTests.pdf

*quality: primary · published: 2020-11 · 5 claims*

- 75% of flaky tests (184 of 245 studied across 55 Java projects) are already flaky at the commit that introduces the test, so running flaky-test detectors specifically on newly added tests would catch three-quarters of flaky tests at the earliest, cheapest point.
  > "Of these 245 tests, we find that 184 (75%) are flaky when they are first added to the project. That is, if developers used detectors solely on newly added tests, and not on existing modified or unmodified tests, they could detect 75% of the flaky tests that we study."
  *(importance: central)*

- **[CHECKED]** Extending detection to directly modified tests raises coverage to 85%, but the remaining 15% of flaky tests become flaky due to changes elsewhere (code under test, test suite, dependencies) and can only be caught by periodically running detectors on all tests — so a flaky-detection strategy keyed only to test-file diffs has a hard ceiling.
  > "The percentage of flaky tests that can be detected does increase to 85% when detectors are run on newly added or directly modified tests. The remaining 15% of flaky tests become flaky due to other changes and can be detected only when detectors are always applied to all tests."
  *(importance: central)*

- Tests that are not flaky when added can become flaky at essentially any later point, from the very next commit to tens of thousands of commits later — meaning a one-time 'stability check' at test creation is insufficient and flakiness status must be continuously re-evaluated over a test's lifetime.
  > "We find that tests that are not flaky when added can become flaky at a varying point in the future, from the immediate next commit of the TIC to tens of thousands of commits after the TIC."
  *(importance: central)*

- The paper's actionable tooling guideline: run detectors when tests are added, then re-run them periodically on the whole suite at a budget-driven cadence (roughly every 150 commits, based on median 144 commits / 154 days between test introduction and flakiness introduction), rather than on every change, to get a good detection-to-cost ratio.
  > "Hence, we suggest that detectors should be run when tests are added and later detectors may be suspended for a large range of commits to achieve a good detection-to-cost ratio. ... Excluding wildfly/wildfly tests (being 38% of tests), we find medians of 144 commits and 154 days between TIC and FIC. Thus, one may consider running detectors periodically, say, every 150 commits."
  *(importance: central)*

- Detection effectiveness at add/modify time varies sharply by flakiness category: order-dependent 'victim' tests (broken by pollution from other tests) are detected only 65% of the time at add-or-modify points, versus 94-97% for implementation-dependent, non-deterministic, and order-dependent 'brittle' tests — so cross-test-interaction flakiness specifically needs suite-level, not per-test, detection.
  > "our results show that 65% of OD Vics ..., 95% of OD Brits ..., 97% of NDs ..., and 94% of IDs ... can be detected. ... the comparatively low ratio for OD Vic tests (65%) indicates that this strategy may miss a nontrivial fraction of other flaky tests."
  *(importance: supporting)*


## https://repositum.tuwien.at/bitstream/20.500.12708/215633/1/Aichmann%20Stefan%20-%202025%20-%20Predictive%20Test%20Selection%20A%20Replication%20Study.pdf

*quality: primary · published: 2025 · 5 claims*

- Facebook's ML-based predictive test selection approach (Machalica et al. 2019) replicates successfully outside Big Tech: models trained on code-change features from ~12 open-source Java projects predicted likely-failing tests with high accuracy (XGBoost test-set average precision 0.947, ROC AUC 0.985), validating that predictive test selection is viable without a proprietary monorepo-scale environment.
  > "Despite these obstacles, we successfully reconstructed the approach and were ultimately able to replicate the key findings, thereby validating the results of the original study in an open-source setting."
  *(importance: central)*

- An ML-based test selector outperforms a static-analysis-based regression test selection tool (STARTS) on test recall at every confidence-score cutoff, meaning learned models catch more of the actually-failing tests than dependency-analysis selection for the same selection budget.
  > "our approach consistently outperforms STARTS in the test TestRecall metric across all values of ScoreCutoff(s) in [0, 1]."
  *(importance: central)*

- The most predictive features for whether a test will fail on a change are project identity, change history, historical failure rates, lexical distance between changed files and tests, and number of tests — closely matching Facebook's original feature ranking — implying a test-intelligence product needs per-test failure-history and change-proximity signals, not just coverage maps, to power TIA.
  > "the best performing features are project name, change history, failure rates, lexical distance and number of tests. The best-performing features align closely with those identified in the underlying study, with one notable exception."
  *(importance: supporting)*

- The dominant practical barrier to building predictive test selection is assembling the labeled training corpus, not the modeling: the authors had to rebuild historical commits and test outcomes themselves because the original pipeline was underspecified, and note they would have needed CI-pipeline-level instrumentation to collect data reliably — supporting the position that a product must first make training-data collection (commit ranges + per-test outcomes) a built-in capability before shipping a TIA model.
  > "The key challenge in this thesis was collecting the necessary data and training the models, as the original paper provided only a high-level description of the features and training pipeline."
  *(importance: central)*

- The replication could not validate robustness of predictive test selection under test flakiness because no flaky tests appeared in the collected dataset, even though a de-flaking step was included — flaky-test handling in learned test selection remains an open validation gap in the literature.
  > "Although we applied the de-flaking process as part of our data preparation, we did not encounter any flaky tests during data collection. ... our replication does not validate this aspect of the original findings, and further work with datasets that include flaky tests is needed to assess model robustness in such scenarios."
  *(importance: supporting)*


## https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html

*quality: primary · published: 2016-05-27 · 5 claims*

- Across Google's entire test corpus, about 1.5% of all test runs report a flaky result (pass and fail on the same code), and this rate is stable because the flakiness insertion rate matches the fix rate — so any test-intelligence tool must treat flakiness as a permanent baseline signal to manage, not a bug backlog to eliminate.
  > "Unfortunately, across our entire corpus of tests, we see a continual rate of about 1.5% of all test runs reporting a "flaky" result. ... We have invested a lot of effort in removing flakiness from tests, but overall the insertion rate is about the same as the fix rate."
  *(importance: central)*

- Almost 16% of Google's tests exhibit some level of flakiness — more than 1 in 7 tests occasionally fail without a code change — establishing that flaky-test management must scale to a large fraction of the suite, not a handful of outliers.
  > "Almost 16% of our tests have some level of flakiness associated with them! This is a staggering number; it means that more than 1 in 7 of the tests written by our world-class engineers occasionally fail in a way not caused by changes to the code or tests."
  *(importance: central)*

- About 84% of pass-to-fail transitions Google's CI system observes in post-submit testing involve a flaky test, meaning the vast majority of 'new failure' alerts are false positives — the core reason failure-triage tooling drowns in noise and why cross-run flakiness classification at the moment of transition is the highest-leverage feature.
  > "What we find in practice is that about 84% of the transitions we observe from pass to fail involve a flaky test! This causes extra repetitive work to determine whether a new failure is a flaky result or a legitimate failure."
  *(importance: central)*

- Sustained false-positive noise causes engineers to dismiss legitimate failures as flaky (alarm fatigue), so a reporting tool's flaky verdicts must be trustworthy enough that a 'real failure' signal is still believed — Google explicitly compares this to pilots ignoring cockpit alarms.
  > "In some cases, developers dismiss a failing result as flaky only to later realize that it was a legitimate failure caused by the code. It is human nature to ignore alarms when there is a history of false signals coming from a system."
  *(importance: central)*

- Google's mitigations each carry documented costs a better tool must design around: the 'fail only if it fails 3 times in a row' flaky designation delays detection of real breakage (45 minutes for a 15-minute test) and teaches developers to ignore flakiness, while automatic quarantine removes the test from the critical path and files a bug but 'could easily mask a real race condition'; Google's stated end-goal is classifying a result as flaky accurately WITHOUT re-running the test, using correlated features of the execution.
  > "We even have a way to denote a test as flaky - causing it to report a failure only if it fails 3 times in a row. This reduces false positives, but encourages developers to ignore flakiness in their own tests... A tool that monitors the flakiness of tests and if the flakiness is too high, it automatically quarantines the test... but could easily mask a real race condition or some other bug in the code being tested. ... we are seeing promising correlations with features that should enable us to identify a flaky result accurately without re-running the test."
  *(importance: supporting)*


## https://trunk.io/assets/flaky-tests-waitlist/2022-04-08-a-survey-on-how-test-flakiness-affects-developers.pdf

*quality: primary · published: 2022-04-08 · 5 claims*

- Flaky tests are a frequent, high-severity problem for practitioners: in a survey of 335 professional developers/testers, 51% experience flaky failures at least weekly and 66% rate flakiness a moderate or serious problem — so flaky-test management is a core, not niche, requirement for test-intelligence tooling.
  > "Developers perceive flakiness as a common and severe issue, with 51 % of all participants experiencing it at least weekly and 66 % rating it as a moderate or serious problem."
  *(importance: central)*

- The primary cost of flakiness developers report is loss of trust in test outcomes and wasted developer time, not compute spent on reruns — implying tools should optimize for restoring signal trust and reducing time-to-diagnose rather than merely making reruns cheaper. Trust loss is also asymmetric: developers stop believing failures more than they stop believing passes ('rerun failures without analyzing' mean 2.08 vs 'rerun passes suspecting hidden bugs' 1.86, Wilcoxon p<0.001), which is dangerous because flaky tests can mask real bugs.
  > "Developers are less worried about the computational costs caused by re-running tests and more about the loss of trust in the test outcomes."
  *(importance: central)*

- Developers overwhelmingly cope with flakiness by rerunning tests (mean usage 2.80/4, the top strategy) while automated detection (1.16), automated debugging (1.11), and auto-quarantine (1.03) rank at the bottom — a tooling adoption gap the paper attributes partly to tools being locked to specific ecosystems (Java build-tool plugins), suggesting language/framework-agnostic detection and quarantine is the differentiator, and that rerun-bots and blind test-disabling are themselves viewed by developers as negative consequences.
  > "Rerunning and rewriting test cases are by far the most dominant approaches to deal with flaky tests, while automated techniques are only rarely used."
  *(importance: central)*

- When asked open-endedly what tooling they want, the #1 wish (32 of 153 coded responses) was better visualization of flakiness — specifically dashboards showing per-test outcome history over time with environment/device metadata — ahead of auto-detection (31), auto root-cause debugging (28), and education/best-practice guides (25); one respondent: 'We run tests so often, I often miss the bigger picture.' This directly validates cross-run test-history views as the highest-demand reporting feature.
  > "We find a strong desire for better visualization of flakiness ... They specifically ask for tools to display the test result history, stating that "We run tests so often, I often miss the bigger picture" (P 187), wishing for "A tool to visualize how tests fail and succeed over time" (P 12)."
  *(importance: central)*

- Flakiness materially degrades CI throughput and release decisions: 76% of CI-heavy (automotive) respondents said flaky tests hinder merging pull requests 'very often or always', over 60% of all participants see releases delayed at least sometimes due to flakiness, and use of continuous integration is itself a strong positive predictor of flakiness prevalence — so release-readiness signals must explicitly separate flaky noise from genuine regressions to be trusted at the gate.
  > "automotive participants are specifically affected, with 76 % stating that flaky tests hinder them in merging pull requests very often or always ... Additionally, more than 60 % of all participants see releases getting delayed at least sometimes due to test flakiness."
  *(importance: central)*


## https://www.atlassian.com/blog/atlassian-engineering/taming-test-flakiness-how-we-built-a-scalable-tool-to-detect-and-manage-flaky-tests

*quality: primary · published: 2025-12-08 · 5 claims*

- Flaky tests account for a double-digit share of build failures at major engineering orgs — 21% of Jira Frontend master build failures and ~15% of Jira backend failures at Atlassian, consistent with Microsoft (13%) and Google (16%) — making flaky-test management a core, not peripheral, problem for any test-intelligence product.
  > ""21% of master build failures" in Jira Frontend attributed to flakiness ... "Approximately 15%" of Jira backend failures from flaky tests ... Microsoft Research found "13% of their test failures were flaky"; Google found "16% of test failures" were flaky."
  *(importance: central)*

- Flaky-test failures consume over 150,000 hours of developer time per year at Atlassian (Jira backend alone), quantifying the productivity cost that flaky-test detection and quarantine features exist to recover.
  > "consuming "over 150,000 hours of developer time each year""
  *(importance: central)*

- Manual, file-based flaky-test lists fail at scale: Atlassian's previous system suffered from a complex workflow, no customization, no actionability, a single point of failure, and scaling difficulties — the failure mode a product's quarantine lifecycle must avoid.
  > "The previous system was "manually managed file-based" with "complex workflow, no customisations, no actionability, a single point of failure, and difficulties in scaling.""
  *(importance: central)*

- Effective flaky detection at scale combines in-build retries with statistical/Bayesian scoring over historical windows (duration variability, environment consistency, result patterns), producing a continuous 0-1 flakiness score rather than a binary flaky/not-flaky label; Atlassian reports an 81% detection rate for certain products.
  > "Employs historical analysis via moving windows, signal processors for multiple distributions (duration variability, environment consistency, result patterns), and assigns "a flakiness score between 0 and 1, where higher scores indicate greater flakiness" ... "81% detection rate for certain products"."
  *(importance: central)*

- Closing the loop matters more than detection alone: on detecting a flaky test, the system auto-identifies owners, creates Jira tickets with deadlines, sends Slack notifications, and only reintroduces quarantined tests after they stay healthy for a configured period — a workflow that recovered more than 22,000 builds and identified 7,000 unique flaky tests across 12+ products.
  > "Once detected, the system "identifies its owners, creates Jira tickets with deadlines to resolve them, and sends Slack notifications." Tests remain quarantined until "healthy for a configured period" ... "recovered more than 22,000 builds" and "identified 7,000 unique flaky tests"."
  *(importance: supporting)*


## https://www.sciencedirect.com/science/article/pii/S0164121223002327

*quality: primary · published: 2022-12-01 (arXiv preprint v1; published in Journal of Systems and Software vol. 206, 2023) · 5 claims*

- Flaky tests are a quantified, large-scale drag on CI signal quality: roughly 13% of failed builds in open-source projects are caused by flaky tests, about 16% of Google's tests were flaky (1 in 7 tests occasionally failing for reasons unrelated to code changes), and GitHub reported 9% of commits in 2020 had at least one red build caused by a flaky test — so a test-intelligence product must treat flakiness as a first-class signal-noise problem, not an edge case.
  > "In a study of open source projects, it was observed that 13% of failed builds are due to flaky tests [2]. At Google, it was reported that around 16% of their tests were flaky, and 1 in 7 of the tests written by their engineers occasionally fail in a way that is not caused by changes to the code or tests [3]. GitHub also reported that, in 2020, one in eleven commits (9%) had at least one red build caused by a flaky test [4]."
  *(importance: central)*

- Rerun-based detection dominates both research and practice but is expensive, and the required rerun count is unknown and cause-dependent — baselines in studies range from 2 to 100 reruns, while one Python study found ~170 reruns are needed to confidently rule out non-order-dependent flakiness. This means simple retry/flip heuristics (like TestLookup's window+flip detector) have a known, quantifiable false-negative floor, and cheaper signals (differential coverage, ML/static prediction, environment-noise acceleration) are the researched alternatives.
  > "Rerun (in different forms) is the most common dynamic approach for detecting flaky tests. Approaches that use rerun focus on making flaky tests detection less expensive by accelerating ways to manifest flakiness or running fewer tests. ... the number of reruns used differ from one study to another (with some studies noting 2 [S15], 10 [S13] or even 100 [S5] reruns as baselines). A recent study on Python projects reported that ~170 reruns are required to ensure a test is not flaky due to non-order-dependent reasons [9]."
  *(importance: central)*

- Quarantine-then-investigate is the most commonly recommended practitioner response, but existing tooling leaves the quarantine lifecycle unmanaged: open questions include how many tests may be quarantined at once (Fowler suggests no more than 8), how long tests should stay quarantined, and automated de-quarantining — the review explicitly calls for tools that automate quarantining and de-quarantining, a concrete gap a quarantine-lifecycle feature should fill (caps, SLA/aging, auto-de-quarantine on proven stability, technical-debt tracking of fix time).
  > "However, there remains some open questions about how to deal with quarantined tests, how long those tests should stay in the designated quarantine area, and how many tests can be quarantined at once. A strategy (that can be implemented into tools) on how to process quarantined flaky tests and remove them from the designated quarantine area (i.e., de-quarantining) also needs further investigation."
  *(importance: central)*

- The dominant practitioner-reported cost of flakiness is trust erosion and wasted diagnosis time, not just compute: Microsoft reports developers investigate failures that turn out to be unrelated to their changes, and Datadog reports organizations reaching 50%+ flaky tests where developers stopped writing or reading tests entirely — so a reporting tool's core job is to tell developers quickly and credibly whether a failure is attributable to their change.
  > ""Flaky tests.... negatively impact developers' productivity by providing misleading signals about their recent changes ... developers may end up spending time investigating those failures, only to discover that the failures have nothing to do with their changes and may simply go away by rerunning the tests." (Engineering@Microsoft, [G42]) ... "We've talked to some organizations that reached 50%+ flaky tests in their codebase, and now developers hardly ever write any tests and don't bother looking at the results." (Product Manager at Datadog, [G3])"
  *(importance: central)*

- Blind retry-until-green is identified as a CI smell that hides real bugs, and flakiness invalidates downstream test-dependent techniques (test selection/prioritization/parallelization, fault localization, culprit-commit bisection, program repair) — so features like test-impact analysis, auto-triage, and go/no-go gates that consume raw pass/fail outcomes without a flakiness-adjusted signal inherit the noise; the review also notes it remains theoretically unresolved whether a test that passes only after several reruns provides the same assurance as one that always passes.
  > "Vassallo et al. [S75] identified retrying failure to deal with flakiness as a CI smell, as it has a negative impact on development experience by slowing down progress and hiding bugs. ... Test optimization techniques such as test suite reduction, test prioritization, test selection, and test parallelization rely on this assumption. ... does a test that only passes after several reruns provide the same level of assurance as a test that always passes provides?"
  *(importance: supporting)*


## https://getautonoma.com/blog/flaky-tests-ci-cd-engineering-cost

*quality: blog · published: 2026-04-09 · 5 claims*

- For teams running frequent CI, flaky-test reruns can consume 15-30% of total CI time, with engineering teams spending 5-10 hours per week investigating false failures (vendor estimate; the '20%' in the article title is a rounding of this unsourced range).
  > "the cumulative impact can reach 15-30% of total CI time in reruns, with engineering teams spending 5-10 hours per week investigating false failures"
  *(importance: central)*

- The article's worked cost model claims a 50-person engineering org with 2,000 tests and a 5% flake rate loses $200,000-$400,000 per year, dominated by developer investigation time ($180,000-$270,000 annually at 20-30 minutes per incident and $150/hr loaded cost), with CI rerun compute at 'usually 20-30% of total compute spend' for high-flake teams.
  > "For a 50-person engineering team at a typical US startup, the combined cost of developer time lost, CI compute reruns, and delayed deployments typically ranges from $200,000 to $400,000 per year."
  *(importance: central)*

- The article grounds its model in two primary sources: Google's finding that roughly 1 in 7 encounters flakiness (note: the article paraphrases this as '1 in 7 test runs', while Google's 2016 post actually says ~16% of TESTS have some flakiness and 1.5% of test RUNS report flaky results) and Microsoft Research's measurement of ~30 minutes of developer time per flaky-test investigation.
  > "Google found roughly 1 in 7 test runs encountered a flaky failure; Microsoft measured an average of 30 minutes per investigation."
  *(importance: supporting)*

- Flaky tests destroy CI signal credibility via a 'boy who cried wolf' failure mode — developers learn to rerun red builds without investigating, real regressions ship, and trust cannot be restored by asking for diligence, only by making the failure signal itself reliable (i.e., tooling must reliably distinguish flake from real failure at notification time).
  > "Once developers learn to distrust their CI signal, you cannot rebuild that trust by asking them to be more diligent. You rebuild it by making the signal reliable."
  *(importance: central)*

- Quarantine-based flaky-test management fails in practice unless it enforces ownership, deadlines, and check-ins: quarantined tests accumulate indefinitely instead of being fixed, so a quarantine lifecycle feature needs kill/fix/quarantine triage criteria and resolution-path accountability, not just isolation.
  > "In practice, quarantine is just delay with documentation. The "look at it later" pile accumulates. The tests never get fixed. They get deleted when the feature they cover gets deprecated."
  *(importance: central)*


## https://incident.io/blog/ai-root-cause-analysis-accuracy-testing-guide

*quality: blog · published: 2026-02-20 · 5 claims*

- AI root-cause suggestions should be validated against precision/recall targets, and the article proposes >80% precision as the threshold below which suggestions net-waste engineer time (at 70% precision, 3 of 10 suggestions waste investigation time).
  > "Target greater than 80% precision. At 70% precision, three out of every ten suggestions waste investigation time."
  *(importance: central)*

- For AI RCA assistants, precision matters more than recall because false positives actively waste investigation time while missed detections are recoverable by humans; roughly 60% recall is claimed to be acceptable.
  > "False positives (low precision) actively waste time...False negatives (low recall) are recoverable."
  *(importance: central)*

- A dominant AI RCA failure mode is confusing correlation with causation across co-occurring signals — e.g. flagging a CPU spike as root cause when both the spike and the errors are symptoms of an upstream fault.
  > "AI identifies two simultaneous events and assumes causation. CPU spiked when errors increased, so AI suggests CPU exhaustion as root cause, missing that both are symptoms of an upstream database deadlock."
  *(importance: central)*

- AI RCA quality is bounded by tool/data access ('the wrapper problem'): a model that only sees one data stream (e.g. chat logs) will miss the true cause recorded in adjacent systems, so verdict quality should be stress-tested against context coverage.
  > "A ChatGPT wrapper that only sees Slack messages will miss the database migration that happened in GitLab 5 minutes before the alert fired."
  *(importance: supporting)*

- Practitioner trust in AI verdicts depends on verifiable, evidence-linked reasoning (citing the specific artifact and timing behind a suggestion) rather than bare conclusions, and trustworthy systems should be tested to admit ignorance instead of hallucinating (e.g. inventing details about a nonexistent service).
  > "When AI says 'I suggest looking at PR #4872 because it modified the database connection pool 5 minutes before the alert,' you can verify that reasoning."
  *(importance: central)*


## https://medium.com/@sarah.thoma.456/reportportal-vs-allure-report-a-comprehensive-comparison-5eb0d153ce73

*quality: blog · published: 2024-07-08 · 5 claims*

- ReportPortal provides real-time monitoring of in-flight test executions, whereas Allure Report only produces reports after execution completes — meaning post-hoc-only reporting is a recognized gap that slows failure detection.
  > "Allure Report: "Generates detailed reports after test execution but lacks real-time reporting capabilities""
  *(importance: central)*

- ReportPortal's initial setup is complex and time-consuming, especially for large test suites — a recurring adoption barrier for self-hosted test-intelligence platforms.
  > "Initial setup "can be complex and time-consuming, especially for large test suites""
  *(importance: supporting)*

- ReportPortal automatically creates defects in integrated issue-tracking systems, positioning auto-defect-filing as a baseline productivity feature for test-reporting tools.
  > ""Automatically creates defects in integrated issue-tracking systems""
  *(importance: supporting)*

- ReportPortal's real-time reporting features introduce performance overhead that can impact test execution speed — a trade-off any live-streaming ingestion design must manage.
  > "ReportPortal: Performance overhead from real-time features may impact execution speed"
  *(importance: supporting)*

- Customizing Allure reports demands significant effort and technical knowledge, indicating that out-of-the-box report flexibility without engineering work is an unmet practitioner need.
  > "Customization requires "significant effort and technical knowledge""
  *(importance: tangential)*


## https://news.ycombinator.com/item?id=20028057

*quality: forum · published: 2019-05-28 · 5 claims*

- Blind retry policies only mask flakiness up to a scale limit: with retries a suite tolerates far more flaky tests before red builds dominate, but the suite slows down and the underlying problem grows — so retry-count alone is a poor flakiness-management strategy compared to per-test failure-rate tracking.
  > "A test suite with 100 flaky tests? Now half your test runs fail... retrying three times? Now your test suite is slow, but you can have up to 2,000 flaky tests before it starts becoming a real problem."
  *(importance: central)*

- Normalized flaky failures cause a measurable behavioral pathology: developers learn to click 'rebuild' on red builds without investigating, which increases time-to-detect genuinely broken builds — implying a reporting tool must distinguish 'known-flaky failure' from 'new failure' at first glance.
  > "training developers to just click 'rebuild' the first one or two times a build fails, which can drastically increase the time before realizing the build is actually broken"
  *(importance: central)*

- Practitioners use quantitative per-test failure-rate thresholds (e.g. >10%) to decide when a flaky test must be fixed rather than tolerated, implying tooling should compute and surface per-test historical failure rates to drive triage.
  > "If a test has a failure rate of > 10%, it indicates an issue in that test that should be fixed."
  *(importance: supporting)*

- A major systemic cause of flakiness is concurrency behavior differing between developer machines and CI hardware (e.g. 4-6 cores locally vs 50-80 on CI), meaning cross-environment correlation (same test, different runner profiles) is a genuinely useful signal for root-cause classification.
  > "concurrency is by far the biggest cause for problematic tests... difference in concurrent execution on developer machines with maybe 4-6 cores and the CI server with 50-80"
  *(importance: supporting)*

- Rarely-failing tests are not safely ignorable: the same intermittent conditions they detect also occur rarely in production and can cause outages, arguing against tooling defaults that auto-suppress or auto-quarantine low-frequency failures without root-cause follow-up.
  > "these rarely-failing tests also rarely-fail in production, and occasionally break things badly and cause outages"
  *(importance: supporting)*


## https://testdino.com/blog/allure-report-limitations/

*quality: blog · published: 2026-01-10 · 5 claims*

- Allure Report's architecture is stateless — every test run produces a fresh standalone HTML bundle with no built-in persistent history, so teams must write and maintain custom scripts for artifact retention to keep history across branches and pipelines.
  > "Allure reports are stateless, meaning each test run generates a fresh HTML bundle without built-in persistent history. ... Manual artifact retention becomes unavoidable, requiring teams to write scripts to store histories across branches and pipelines."
  *(importance: central)*

- At large scale, Allure report generation can take longer than the test execution itself, materially slowing CI pipelines and weakening the developer feedback loop.
  > "CI pipelines slow down, because Allure Report generation can take longer than test execution itself in large environments. These delays reduce developer productivity and weaken feedback loops across engineering teams."
  *(importance: central)*

- Flaky-test tracking in HTML-only report tools is unreliable because they cannot maintain cross-run analytics without external tooling — teams end up spending more time fixing report history than analyzing test failures.
  > "Flaky test tracking often breaks, since HTML-only reports cannot reliably maintain cross-run analytics without external tooling. Teams eventually spend more time fixing report history than analyzing test failures."
  *(importance: central)*

- Roughly 43% of QA teams report spending extra time maintaining reporting scripts and manually tracking test history (statistic is presented without a named source, so it should be independently verified before reuse).
  > "Many teams (≈ 43%) report spending extra time maintaining reporting scripts and manually tracking test history, showing Allure Report limitations at scale."
  *(importance: supporting)*

- Allure's dashboards lack failure clustering, flaky-test detection, and deeper analytics, and non-engineering stakeholders (e.g., project managers) struggle to interpret its reports for decision-making — the gap modern platforms fill with centralized history, RBAC, comments, and trend dashboards.
  > "Allure’s HTML dashboards lack deeper test analytics, failure clustering, and flaky-test detection. Stakeholders, especially project managers as key stakeholders, struggle to interpret reports or use them effectively in decision-making."
  *(importance: supporting)*


## https://www.capterra.com/p/227555/Allure-TestOps/

*quality: forum · published: 2026-07-21 · 5 claims*

- Allure TestOps users rate the product highly overall (4.7/5 across 18 Capterra reviews), indicating its core reporting proposition largely satisfies practitioners and sets a competitive bar for test-reporting depth.
  > "the reporting of this tool is just magnificent"
  *(importance: supporting)*

- Even in a well-rated test-reporting tool, report customizability is a recurring practitioner complaint — reviewers state Allure TestOps reports cannot be fully tailored to team needs.
  > "The reports are not fully customizable"
  *(importance: central)*

- Setup and dashboard configuration complexity is a concrete adoption barrier for Allure TestOps: reviewers report the initial configuration and dashboard setup are hard, especially for newcomers.
  > "Configuring dashboards quite challenging"
  *(importance: central)*

- Practitioners specifically value built-in flaky-test workflows: Allure TestOps reviewers cite ease of working with flaky tests and tracking flakiness as a strength, implying flaky-test triage is a valued table-stakes capability.
  > "It is easy to work with flaky tests and issues"
  *(importance: supporting)*

- Reporting value scales with test volume — reviewers say the visibility benefit materializes mainly at large test-suite scale, and separately note limited support for manual testing workflows.
  > "Outstanding visibility into everything test related once you reach a large volume of tests"
  *(importance: tangential)*


---

# Recovered verifier verdicts

Each entry is one adversarial verifier's independent judgement.

### not refuted (confidence: high)
**Claim:** At Atlassian, flaky tests were responsible for up to 21% of master build failures in the Jira Frontend repository and roughly 15% of Jira backend repo failures, with the resulting reruns wasting over 150,000 developer hours per year — quantifying why flaky-test management is a first-order productivity problem, not a nice-to-have.

**Evidence:** Fetched the cited Atlassian engineering blog post (published Dec 8, 2025) directly and verified all three figures verbatim: (1) Jira Frontend — "Test flakiness has been a significant contributor to build reliability issues in the past, responsible for as much as 21% of master build failures"; (2) Jira backend — "Approximately 15% of Jira backend repo failures are attributed to flaky tests, necessitating reruns that ultimately waste over 150,000 hours of developer time each year." The claim's "up to 21%" faithfully renders "as much as 21%", and the 150,000-hour figure appears exactly as quoted. Source is a first-party primary source (Atlassian's own engineering blog describing its internal Flakinator platform), which is the appropriate authority for company-internal metrics; it is recent (Dec 2025) and consistent with independent industry figures the article itself cites (Microsoft ~13%, Google ~16% flakiness). A web search found no contradicting or qualifying sources. Minor caveats that do not rise to refutation: the numbers are self-reported and not independently audited, and the 150,000-hour figure is stated in the sentence about the Jira backend repo (the claim's phrasing could be read as covering both repos, but the supporting quote correctly scopes it). One motivated-source note: the post promotes Atlassian's internal tooling, but the statistics are engineering telemetry, not a product benchmark.

**Counter-source:** None found — WebSearch for the figures surfaced only the original Atlassian post, its AMP mirror, and third-party blogs repeating it; no source disputes or qualifies the numbers.

### not refuted (confidence: high)
**Claim:** At Atlassian, flaky tests were responsible for up to 21% of master build failures in the Jira Frontend repository and roughly 15% of Jira backend repo failures, with the resulting reruns wasting over 150,000 developer hours per year — quantifying why flaky-test management is a first-order productivity problem, not a nice-to-have.

**Evidence:** Direct fetch of the primary source (Atlassian Engineering blog, published December 8, 2025) confirms both statistics verbatim: "Test flakiness has been a significant contributor to build reliability issues in the past, responsible for as much as 21% of master build failures in the Jira Frontend repository" and "Approximately 15% of Jira backend repo failures are attributed to flaky tests, necessitating reruns that ultimately waste over 150,000 hours of developer time each year." The claim's numbers (up to 21% frontend, ~15% backend, >150,000 developer hours/year) match the source exactly, with faithful hedging ("up to" mirrors "as much as", "roughly" mirrors "approximately"). Web search found no contradicting or qualifying coverage; the figures are independently re-cited by third parties (edgedelta.com, pie.inc, etc.). Source-quality check: this is Atlassian's own first-party engineering blog reporting internal telemetry — the strongest available source for a claim explicitly scoped to Atlassian's own repos; the numbers motivate an internal tool rather than a product being sold, so it is not vendor marketing for an external audience. Recency check: December 2025 publication, well within currency for a 2026 roadmap. One minor caveat that does not rise to refutation: in the source, the 150,000-hour figure appears in the backend-repo sentence, so attributing it to Atlassian-wide rerun waste (as the claim's phrasing loosely does) is a slight scope ambiguity inherited from the source itself; the claim does not overstate beyond what the source says. The interpretive conclusion ("

### not refuted (confidence: high)
**Claim:** At Atlassian, flaky tests were responsible for up to 21% of master build failures in the Jira Frontend repository and roughly 15% of Jira backend repo failures, with the resulting reruns wasting over 150,000 developer hours per year — quantifying why flaky-test management is a first-order productivity problem, not a nice-to-have.

**Evidence:** Verified against the primary source (fetched directly). The Atlassian Engineering blog post "Taming Test Flakiness: How We Built a Scalable Tool to Detect and Manage Flaky Tests" (published December 8, 2025, by Nitish Malik, Senior Engineering Manager at Atlassian) contains both figures verbatim: (1) "Test flakiness has been a significant contributor to build reliability issues in the past, responsible for as much as 21% of master build failures in the Jira Frontend repository" — the claim's "up to 21%" faithfully renders "as much as 21%"; (2) "Approximately 15% of Jira backend repo failures are attributed to flaky tests, necessitating reruns that ultimately waste over 150,000 hours of developer time each year" — the claim correctly ties the 150,000-hour figure to backend-repo reruns, as the source does. Checklist results: the quote fully supports the claim with no overreach; a web search found no contradicting or qualifying coverage, and the magnitudes are consistent with peer-company reports (Google ~16% of tests exhibiting flakiness, Microsoft flaky-test cost studies), so this is not an outlier; the source is the primary first-party account for a claim explicitly framed as "At Atlassian..." (self-reported internal metrics are the only possible source for such a claim, and the claim attributes them rather than presenting them as independently audited); it is recent (Dec 2025). Caveats that do not rise to refutation: the figures are self-reported and unaudited, the 21% is framed as historical ("in the past", before their Flakinator tooling), and the post has a promotional 

### not refuted (confidence: high)
**Claim:** Developers' primary concern with flaky tests is loss of trust in test outcomes (and wasted developer time), not the compute cost of reruns — so a test-reporting tool should prioritize restoring confidence in verdicts over merely optimizing rerun efficiency.

**Evidence:** Quote verified verbatim in the abstract of Gruber & Fraser, ICST 2022 (arXiv:2203.00483), a peer-reviewed survey of 335 professional developers/testers: "Developers are less worried about the computational costs caused by re-running tests and more about the loss of trust in the test outcomes." The same paper reports losing trust and wasted developer time as the most commonly stated effects, and recommends better visualization/dashboards — directly supporting the claim's prescriptive half. Refutation search found corroboration, not contradiction: an ICST 2024 five-year industrial case study found rerun compute cost negligible (~$0.0002 per automated rerun vs $5.67 per manual investigation of a false failure), and Habchi et al. (arXiv:2112.04919) independently identify trust erosion as a primary impact. Sources quantifying CI compute overhead (15-30% extra compute; Google spending 2-16% of testing budget on reruns) establish that rerun cost is nonzero but do not dispute developers' stated priority of trust over compute cost. Source quality matches claim strength; findings from 2021-2024 are consistent, so the 2022 result is not stale.

### not refuted (confidence: high)
**Claim:** Atlassian's flakiness scoring goes beyond simple flip-count heuristics: it applies Bayesian inference over a moving window of historical runs and fuses multiple signal distributions (duration variability, environment consistency, result patterns, retry frequency) into a single 0-1 flakiness score — a concrete blueprint for upgrading window+flip heuristics to probabilistic multi-signal scoring.

**Evidence:** The claim survives adversarial checking against the primary source. Direct fetch of the Atlassian engineering blog post confirms every element of the claim with explicit text: (1) Bayesian inference — the article has a dedicated section "Bayesian Inference for Flakiness Detection" and states "we use the prior probability distribution of a test case's historic runs and create the posterior probability from it"; (2) moving window — "Utilise a moving window approach to analyse historical test run data, applying Bayesian inference to calculate the probability of a test being flaky"; (3) multi-signal fusion — the "Signal Processors" component with the quoted text "consider multiple signal distributions (e.g., duration variability, environment consistency, result patterns, retry frequency)"; (4) 0-1 score — "Assign a flakiness score between 0 and 1, where higher scores indicate greater flakiness." The system (Flakinator) is described as an implemented production system used by 12+ Atlassian products, processing 350M+ test executions daily, with 22,000+ builds recovered and 7,000 flaky tests identified — not a conceptual proposal. A web search found no contradicting sources; Meta's "Probabilistic flakiness" (engineering.fb.com, 2020) independently corroborates that probabilistic flakiness scoring is established industry practice, strengthening the "blueprint" framing. One minor caveat that does not rise to refutation: the signal-distributions sentence is phrased prescriptively ("consider..."), so the exact set of four signals reads as design guidance within the architecture descri

**Counter-source:** https://engineering.fb.com/2020/12/10/developer-tools/probabilistic-flakiness/ (corroborating, not contradicting — no refuting source found)

### not refuted (confidence: medium)
**Claim:** An in-pipeline retry-based detector — a CLI in the CI pipeline that checks failing tests against the known-flaky list, implicitly retries unknown failures, and circuit-breaks on the first pass/fail flip signal — achieved an 81% flaky-test detection rate for some Atlassian products, validating rerun/flip-signal detection at ingest time (rather than only post-hoc window analysis) as a highly effecti

**Evidence:** Direct fetch of the Atlassian engineering blog (published 2025-12-08) confirms the quote verbatim: the Flakinator CLI "checks whether failing test cases are already designated as flaky... an implicit retry mechanism is employed to collect flaky signals, with the circuit breaking at the first occurrence of a flip signal" and "This approach has enabled us to achieve an impressive 81% detection rate for certain products." The claim faithfully preserves the "certain products" scoping and correctly describes the mechanism. Source is a recent first-party engineering case study (system used across 12+ Atlassian products), the exact evidence class the research brief requests. Caveats that survive but don't refute: "detection rate" is undefined (no methodology/denominator disclosed) and self-reported, and academic work (iDFlakies; DeFlaker; ~100 reruns needed to detect half of flaky tests) shows first-flip rerun detection is not a complete detector in general — so the figure should be presented as one company's self-reported result, not an independently validated benchmark of the mechanism's universal effectiveness.

**Counter-source:** https://www.sciencedirect.com/science/article/pii/S0164121223002327 (multivocal review: rerun-based detection is expensive and often needs ~100 reruns to catch half of flaky tests — qualifies, does not contradict, the scoped Atlassian self-report)

### not refuted (confidence: high)
**Claim:** Quarantine only works as a closed loop with accountability: detection auto-creates Jira tickets with resolution deadlines routed via code ownership plus Slack notifications, quarantined tests keep running in branch/scheduled/quarantine pipelines to collect health signals, and tests that stay healthy for a configured period are automatically un-quarantined — meaning a quarantine lifecycle without o

**Evidence:** Attempted refutation failed on all checklist points. (1) Quote-support: the supplied quote covers only two of the four elements (continued execution in branch/scheduled/quarantine pipelines; auto-unquarantine after a healthy period), but I fetched the full Atlassian article and it explicitly confirms the rest verbatim: "Once a flaky test is detected, a Jira ticket is created for the owning team with pre-decided due dates to provide resolution", "the code ownership system identifies its owners, creates Jira tickets with deadlines to resolve them", and "the Flakinator Bot sends out Slack notifications to keep everyone informed." So every descriptive component of the claim is in the cited primary source. (2) Contradiction search: found none — instead, independent practitioner sources corroborate the normative half of the claim (that quarantine without these elements is incomplete): Mergify, DeFlaky, minware, and FlakyGuard all describe the "quarantine graveyard" failure mode and prescribe the same remedies — ticket + owner (via CODEOWNERS) + maximum-stay SLA of 2-4 weeks + continued signal collection + explicit unquarantine path ("detect → tag → isolate → fix → unquarantine"). (3) Source quality: Atlassian engineering blog is a primary first-party case study of a production internal tool (Flakinator), not a product marketing page; appropriate for a design-pattern claim. (4) Not outdated — quarantine-lifecycle practice in 2026 sources matches it. (5) Not marketing/benchmark fluff. Caveat that keeps this from being a slam-dunk: the "only works" / "is incomplete" framing is the c

**Counter-source:** https://mergify.com/learn/test-quarantine (corroborates rather than contradicts: quarantine SLA, CODEOWNERS-based ownership, max-stay deadlines, unquarantine path)

### not refuted (confidence: medium)
**Claim:** An in-pipeline retry-based detector — a CLI in the CI pipeline that checks failing tests against the known-flaky list, implicitly retries unknown failures, and circuit-breaks on the first pass/fail flip signal — achieved an 81% flaky-test detection rate for some Atlassian products, validating rerun/flip-signal detection at ingest time (rather than only post-hoc window analysis) as a highly effecti

**Evidence:** Quote verified verbatim in the primary source (Atlassian engineering blog, published 2025-12-08): the article describes the Flakinator CLI in CI pipelines checking failures against a known-flaky list, implicitly retrying unlisted failures with a configurable retry count, circuit-breaking at the first flip signal, and logging newly identified flaky tests — and states "This approach has enabled us to achieve an impressive 81% detection rate for certain products." The claim faithfully preserves the source's own qualifier ("for some/certain products") and does not inflate the mechanism description. Context in the same article (flaky tests caused 21% of master-branch build failures, ~150,000 developer hours/year wasted) shows this is a substantive internal-tooling case study, not a product sales pitch. Adversarial cross-check against academic literature (DeFlaker ICSE 2018, FlakeFlagger ICSE 2021) found no contradiction: rerun/flip detection is the established ground-truth mechanism for flakiness (a pass/fail flip on identical code is near-zero-false-positive evidence), and the known limitation — reruns miss low-frequency flakes, so recall is below 100% — is consistent with, not contrary to, an 81% (rather than higher) detection rate. Caveats that keep this from high confidence: the figure is self-reported with no published methodology (the denominator/ground truth for "detection rate" is undefined), "certain products" implies other products did worse (mild cherry-picking within a first-party blog), and DeFlaker's data (Maven-style immediate reruns surfaced only 23% of eventuall

**Counter-source:** https://www.cs.cornell.edu/~legunsen/pubs/BellETAL18DeFlaker.pdf (qualifies but does not contradict: rerun-based detection misses low-frequency flakes and rerun strategy strongly affects yield)

### not refuted (confidence: medium)
**Claim:** An in-pipeline retry-based detector — a CLI in the CI pipeline that checks failing tests against the known-flaky list, implicitly retries unknown failures, and circuit-breaks on the first pass/fail flip signal — achieved an 81% flaky-test detection rate for some Atlassian products, validating rerun/flip-signal detection at ingest time (rather than only post-hoc window analysis) as a highly effecti

**Evidence:** Verified against the primary source (Atlassian engineering blog, "Taming Test Flakiness," published 2025-12-08). The quote is verbatim and the claim's mechanism description is accurate: "The Flakinator CLI, integrated into the pipelines, checks whether failing test cases are already designated as flaky. If a test is not included in the flaky list, an implicit retry mechanism is employed to collect flaky signals, with the circuit breaking at the first occurrence of a flip signal" and "This approach has enabled us to achieve an impressive 81% detection rate for certain products." The claim preserves the source's own hedge ("for some Atlassian products" mirrors "certain products"). The source is a first-party engineering case study — exactly the source class the research brief prioritizes — is recent, and no contradicting or disputing coverage was found in searches. Supporting scale context from the same article: 350M+ test executions/day, ~7,000 unique flaky tests detected, 22,000+ builds recovered per quarter. CAVEATS that survive but do not kill the claim: (1) "81% detection rate" is undefined in the article — no denominator, no precision/recall definition, no ground-truth methodology — and "certain products" is an unspecified, likely favorable subset, so the number is self-reported and unverifiable; (2) Atlassian's system is hybrid (in-pipeline retry CLI PLUS post-hoc Bayesian analysis over historical runs), so ingest-time flip detection is not the sole mechanism; (3) academic literature (DeFlaker, ICSE 2018) shows rerun effectiveness is highly configuration-dependent — Ma

**Counter-source:** https://www.cs.cornell.edu/~legunsen/pubs/BellETAL18DeFlaker.pdf (DeFlaker: rerun-based detection found only 23% of failures flaky under Maven's same-JVM rerun vs 95% with isolated reruns — qualifies, but does not contradict, the effectiveness of retry/flip-signal detection)

### not refuted (confidence: medium)
**Claim:** Quarantine only works as a closed loop with accountability: detection auto-creates Jira tickets with resolution deadlines routed via code ownership plus Slack notifications, quarantined tests keep running in branch/scheduled/quarantine pipelines to collect health signals, and tests that stay healthy for a configured period are automatically un-quarantined — meaning a quarantine lifecycle without o

**Evidence:** Direct fetch of the Atlassian article confirms every component the claim bundles, not just the two in the supporting quote: auto-created Jira tickets ("Once a flaky test is detected, a Jira ticket is created for the owning team with pre-decided due dates"), code-ownership routing ("the code ownership system identifies its owners"), Slack notifications ("the Flakinator Bot sends out Slack notifications"), continued execution of quarantined tests in branch/scheduled/quarantine pipelines, and automatic un-quarantine after a configured healthy period. Independent corroboration for the closed-loop principle exists across Google Testing Blog (2016), Trunk, Mergify, minware, and DeFlaky, which all warn that quarantine without ownership, deadlines, continued signal collection, and an exit path degrades into a permanent test graveyard. Caveat that keeps this from full confidence: the "ONLY works / otherwise incomplete" necessity framing is the synthesizer's generalization — Atlassian presents this descriptively as their approach — and specifically automatic re-instatement has credible alternatives (fix-or-delete within an SLA, e.g. 30-day escalation per minware/DeFlaky) rather than being a universal requirement; though tools like Trunk and currents.dev do implement health-based auto-restore, so it is mainstream practice rather than idiosyncratic.

**Counter-source:** https://www.minware.com/guide/best-practices/flaky-test-quarantine (prefers SLA-based fix-or-delete escalation over automatic re-instatement as the quarantine exit path — qualifies, but does not contradict, the closed-loop claim)

### not refuted (confidence: high)
**Claim:** Quarantine only works as a closed loop with accountability: detection auto-creates Jira tickets with resolution deadlines routed via code ownership plus Slack notifications, quarantined tests keep running in branch/scheduled/quarantine pipelines to collect health signals, and tests that stay healthy for a configured period are automatically un-quarantined — meaning a quarantine lifecycle without o

**Evidence:** Attempted refutation failed on all checks. (1) Quote-support: the supplied quote covers only continued execution + auto-un-quarantine, but I fetched the full Atlassian article and every other element of the claim is explicitly there: "Once a flaky test is detected, a Jira ticket is created for the owning team with pre-decided due dates", "the code ownership system identifies its owners, creates Jira tickets with deadlines", and "the Flakinator Bot sends out Slack notifications to keep everyone informed" — so ownership routing, deadlines, Slack, continued execution, and auto-reinstatement are all first-party, not invented. (2) Contradiction search found the opposite of a rebuttal: independent sources converge on the same closed-loop principle — Martin Fowler's classic warning that unaccountable quarantine loses regression coverage and becomes a dump; minware's quarantine guide ("quarantine with no deadline is just deletion with extra steps"); Trunk.io ("if there is no defined process for returning tests to the main pipeline, quarantine is a one-way trip"; auto-quarantine/re-qualification needed at scale); Parry et al.'s developer-experience survey noting quarantined tests get forgotten. (3) Source quality is appropriate: a first-party engineering case study of internal tooling (Flakinator), not a product pitch or press release, corroborated by vendor-neutral and academic sources. (4) Current-era practice, not outdated. One honest caveat, insufficient to refute: the normative framing "ONLY works" is the researcher's synthesis — Atlassian describes its own implementation witho

**Counter-source:** https://marklapierre.net/pros-cons-quarantined-tests/ (notes trade-offs but affirms the forgotten-test failure mode); https://trunk.io/blog/eradicating-flaky-tests and https://www.minware.com/guide/best-practices/flaky-test-quarantine (corroborate rather than contradict)

### not refuted (confidence: high)
**Claim:** Atlassian's flakiness scoring goes beyond simple flip-count heuristics: it applies Bayesian inference over a moving window of historical runs and fuses multiple signal distributions (duration variability, environment consistency, result patterns, retry frequency) into a single 0-1 flakiness score — a concrete blueprint for upgrading window+flip heuristics to probabilistic multi-signal scoring.

**Evidence:** Verbatim extraction from the primary source (Atlassian engineering blog, Dec 8 2025, by Nitish Malik) confirms every element of the claim: a first-person statement "we use the prior probability distribution of a test case's historic runs and create the posterior probability from it"; a "Historical Analysis" bullet — "Utilise a moving window approach to analyse historical test run data, applying Bayesian inference to calculate the probability of a test being flaky"; the "Signal Processors" bullet exactly as quoted (duration variability, environment consistency, result patterns, retry frequency); and a "Scoring" bullet — "Assign a flakiness score between 0 and 1, where higher scores indicate greater flakiness." The system (Flakinator) is described as deployed across 12+ Atlassian products at 350M+ test executions/day, with no future-work framing. Web search found no contradicting source; third-party coverage (TestDino 2026 flaky-test benchmark, Wopee) corroborates the Bayesian approach. Minor non-fatal caveats: the bullets use imperative phrasing ("consider..."), leaving slight ambiguity on whether all four signals are fused in production vs. recommended, and the tool's headline 81% detection rate comes from its retry/flip circuit-breaker mechanism, with Bayesian scoring as one component — but the claim asserts neither exclusivity nor the benchmark, only the scoring architecture, which the source supports.

**Counter-source:** none found — search surfaced only corroborating coverage (testdino.com/blog/flaky-test-benchmark, wopee.io/blog/flaky-tests-complete-guide)

### not refuted (confidence: medium)
**Claim:** Atlassian's flakiness scoring goes beyond simple flip-count heuristics: it applies Bayesian inference over a moving window of historical runs and fuses multiple signal distributions (duration variability, environment consistency, result patterns, retry frequency) into a single 0-1 flakiness score — a concrete blueprint for upgrading window+flip heuristics to probabilistic multi-signal scoring.

**Evidence:** Verified against the primary source (Atlassian engineering blog, "Taming Test Flakiness," published Dec 8, 2025, describing Flakinator — a production system used by 12+ Atlassian products, 350M+ test executions/day, 22,000+ builds recovered). Each element of the claim maps to article text: Bayesian inference — "we use the prior probability distribution of a test case's historic runs and create the posterior probability from it"; moving window — "Utilise a moving window approach to analyse historical test run data, applying Bayesian inference to calculate the probability of a test being flaky"; multi-signal — "Signal Processors: To derive a comprehensive flakiness score, consider multiple signal distributions (e.g., duration variability, environment consistency, result patterns, retry frequency)"; 0-1 score — "Assign a flakiness score between 0 and 1, where higher scores indicate greater flakiness." These are presented as the 3 modules of Flakinator's analysis/inference component. One qualification survived scrutiny: the four named signals appear in prescriptive "consider... (e.g., ...)" language, so the article does not explicitly confirm all four are fused in production — but the claim's own framing ("a concrete blueprint for upgrading window+flip heuristics") matches that blueprint character exactly, so this does not invalidate it for roadmap use. Also note Atlassian's shipped Bitbucket Pipelines flaky-test feature uses a simpler recency-weighted flip-frequency score, so the Bayesian multi-signal design describes the internal Flakinator, not every Atlassian product surfac

**Counter-source:** https://support.atlassian.com/bitbucket-cloud/docs/understand-and-manage-flaky-tests-in-bitbucket-pipelines/ (Atlassian's shipped Bitbucket flaky score is flip-frequency-based with recency weighting — a flip heuristic, not the Bayesian multi-signal design; and the four fused signals in the blog are "consider (e.g., ...)" examples, not confirmed production implementation)

### not refuted (confidence: high)
**Claim:** Flaky tests are a frequent, serious problem for practitioners: in a survey of 335 professional developers/testers, 51% experience flakiness at least weekly and 66% rate it a moderate or serious problem — justifying flaky-test management as a core capability of any test-intelligence product.

**Evidence:** Verified against the primary source's full text (arXiv 2203.00483v4, Gruber & Fraser, ICST 2022, peer-reviewed IEEE). The paper states verbatim: "We surveyed 335 professional software developers and testers in different domains" and "51 % of all participants experiencing it at least weekly and 66 % rating it as a moderate or serious problem." The claim reproduces sample size, population, and both statistics exactly, including the correct 'moderate or serious' pooling. Independent corroboration exists: Eck et al. 2019 (121 developers, 58% at least monthly; 79% of those experiencing it rated it moderate/serious) and Google internal data (~16% of tests flaky; 84% of pass-to-fail transitions involve a flaky test). No contradicting or heavily qualifying source found. Minor non-refuting caveat: 102/335 respondents were BMW employees (~30% automotive skew), though the paper analyzes the general-public (233) and BMW subsamples separately and both confirm the severity finding.

**Counter-source:** None found — searched for criticism/replication disputes; corroborating sources only (arxiv.org/pdf/1907.01466 Eck et al.; sciencedirect.com/science/article/pii/S0164121223002327 multivocal review)

### not refuted (confidence: high)
**Claim:** Flaky tests are prevalent at industrial scale: Google reported ~16% of its tests were flaky, and GitHub reported that in 2020 roughly one in eleven commits had a red build caused by a flaky test — meaning flaky-test management is a first-order problem for any test-intelligence product, not an edge case.

**Evidence:** The claim survives all refutation attempts. (1) Quote-vs-claim: the claim accurately restates the quote — ~16% Google tests flaky, GitHub 2020 one-in-eleven (9%) commits with a flaky-red build — with no overreach; the interpretive conclusion ("first-order problem") is a reasonable inference from those magnitudes. (2) Primary sources verified independently: the Google figure traces to the Google Testing Blog post "Flaky Tests at Google and How We Mitigate Them" (testing.googleblog.com, May 2016, John Micco: "almost 16% of our tests have some level of flakiness") and Micco's ICST 2017 keynote (16% of 4.2M tests); the GitHub figure traces to GitHub's own engineering blog "Reducing flaky builds by 18x" (github.blog, Dec 2020: 1 in 11 commits, ~9%, dropped to 1 in 200 after their flaky-management system). Both are first-party engineering reports, not vendor marketing. (3) Source quality: the citing source is a peer-reviewed Journal of Systems and Software multivocal review (Parry et al., 2023, S0164121223002327) — appropriate strength for this claim. (4) Contradiction search: found no credible source disputing industrial flakiness prevalence; the surrounding literature (Microsoft studies, Lam et al. root-causing work at Microsoft, practitioner surveys, Trunk/BuildPulse corroboration) uniformly reinforces it. (5) Datedness: the Google number is from 2016 and GitHub's from 2020, but the claim correctly time-stamps the GitHub figure, and no newer evidence suggests flakiness has since become an edge case — GitHub's own 18x reduction required exactly the kind of dedicated flaky-manag

**Counter-source:** https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html and https://github.blog/2020-12-16-reducing-flaky-builds-by-18x/ (both confirm rather than contradict)

### not refuted (confidence: high)
**Claim:** Flaky tests are prevalent at industrial scale: Google reported ~16% of its tests were flaky, and GitHub reported that in 2020 roughly one in eleven commits had a red build caused by a flaky test — meaning flaky-test management is a first-order problem for any test-intelligence product, not an edge case.

**Evidence:** Both figures verified against primary sources. Google: John Micco, Google Testing Blog, May 2016, "Flaky Tests at Google and How We Mitigate Them" — "almost 16% of our tests have some level of flakiness" (confirmed via googblogs.com mirror and Wikipedia's flaky-test article). GitHub: primary source github.blog "Reducing flaky builds by 18x" (Dec 2020) states exactly that 1 in 11 commits (9%) had at least one red build caused by a flaky test, later reduced 18x to ~1 in 200. The citing source is a peer-reviewed multivocal review (Journal of Systems and Software, 2023). No contradicting evidence found; corroborating industrial data exists (Trunk's 202M CI-job analysis, Microsoft/Meta flakiness studies). Caveats that do not refute: (a) the Google figure dates to 2016 and the claim omits the date; (b) 16% means tests with *some* flakiness — only ~1.5% of test runs were flaky — but the claim uses the correct tests-not-runs framing. The inference that flaky-test management is a first-order problem is proportionate to this evidence.

**Counter-source:** https://github.blog/engineering/engineering-principles/reducing-flaky-builds-by-18x/ (primary source confirms, not contradicts; Google 2016 figure's age and its "some level of flakiness" definition are the only qualifications found)

### not refuted (confidence: high)
**Claim:** Atlassian's flakiness scoring goes beyond simple flip-count heuristics: it applies Bayesian inference over a moving window of historical runs and fuses multiple signal distributions (duration variability, environment consistency, result patterns, retry frequency) into a single 0-1 flakiness score — a concrete blueprint for upgrading window+flip heuristics to probabilistic multi-signal scoring.

**Evidence:** Verified against the primary source directly. The Atlassian engineering blog post explicitly states all elements of the claim, not just the single quoted sentence: (1) "For the use case of creating a flakiness score for a test case, we use the prior probability distribution of a test case's historic runs and create the posterior probability from it" — confirms Bayesian inference; (2) "Utilise a moving window approach to analyse historical test run data, applying Bayesian inference to calculate the probability of a test being flaky" — confirms the moving window; (3) the Signal Processors sentence confirms fusing duration variability, environment consistency, result patterns, and retry frequency; (4) "Assign a flakiness score between 0 and 1, where higher scores indicate greater flakiness" — confirms the 0-1 score. This is not a design sketch: the tool (Flakinator) is described as deployed across 12+ Atlassian products with a claimed 81% detection rate for some products, so it goes beyond aspirational architecture. Web search found no contradicting or qualifying sources; Meta's "Probabilistic flakiness" (engineering.fb.com, 2020) independently corroborates that probabilistic multi-signal flakiness scoring is an established industry pattern, strengthening the "blueprint" framing. Caveats that fall short of refutation: it is a self-reported first-party engineering blog with no external validation of effectiveness, the Signal Processors sentence is phrased as guidance ("consider multiple signal distributions") rather than a hard implementation statement, and "concrete blueprint"

### not refuted (confidence: high)
**Claim:** At Atlassian, flaky tests were responsible for up to 21% of master build failures in the Jira Frontend repository and roughly 15% of Jira backend repo failures, with the resulting reruns wasting over 150,000 developer hours per year — quantifying why flaky-test management is a first-order productivity problem, not a nice-to-have.

**Evidence:** Direct fetch of the cited Atlassian engineering blog post (published December 8, 2025, by Nitish Malik, Senior Engineering Manager) confirms all three figures verbatim: "Test flakiness has been a significant contributor to build reliability issues in the past, responsible for as much as 21% of master build failures in the Jira Frontend repository" and "Approximately 15% of Jira backend repo failures are attributed to flaky tests, necessitating reruns that ultimately waste over 150,000 hours of developer time each year." The claim's "up to 21%" faithfully renders the source's "as much as 21%", and the 15% / 150,000-hour pairing matches the source sentence exactly. A web search found no contradicting or qualifying coverage — only third-party articles (edgedelta.com, pie.inc, etc.) repeating the same figures. The source is primary (Atlassian reporting its own internal CI metrics), recent (Dec 2025), and the post is a technical write-up of an internal flaky-test tool rather than a product sales page. One residual caveat, not rising to refutation: the 150,000-hour figure is self-reported and not externally auditable, and the source sentence ties it to backend reruns while the claim's phrasing could be read as covering both repos — a minor ambiguity present in the original text itself (Atlassian's own summary elsewhere also pairs the 150k figure with the frontend 21% stat, suggesting the figure is company-wide/loosely attributed).

**Counter-source:** https://www.atlassian.com/blog/atlassian-engineering/taming-test-flakiness-how-we-built-a-scalable-tool-to-detect-and-manage-flaky-tests

### not refuted (confidence: high)
**Claim:** Atlassian's flakiness scoring goes beyond simple flip-count heuristics: it applies Bayesian inference over a moving window of historical runs and fuses multiple signal distributions (duration variability, environment consistency, result patterns, retry frequency) into a single 0-1 flakiness score — a concrete blueprint for upgrading window+flip heuristics to probabilistic multi-signal scoring.

**Evidence:** Verified against the primary source (fetched the Atlassian Engineering post directly, published Dec 2025 — current). Every element of the claim appears verbatim or near-verbatim in the article: (1) Bayesian inference over a moving window — "Utilise a moving window approach to analyse historical test run data, applying Bayesian inference to calculate the probability of a test being flaky" and "we use the prior probability distribution of a test case's historic runs and create the posterior probability from it"; (2) the 0-1 score — "Assign a flakiness score between 0 and 1, where higher scores indicate greater flakiness"; (3) multi-signal input — the "Signal Processors" module listing duration variability, environment consistency, result patterns, retry frequency, described as part of the built system's analysis component, not a hypothetical suggestion. This is a first-party engineering blog about a production system (Flakinator: 350M+ test executions/day, ~7,000 flaky tests detected, 22,000+ builds recovered per quarter), which is adequate source strength for a descriptive claim about that system, and the approach is corroborated as credible industry practice by Meta's independent "Probabilistic flakiness" work (engineering.fb.com, 2020). One caveat that qualifies but does not refute: the article does NOT disclose the fusion mechanics — how the multiple signal distributions are mathematically combined into the single score — so the word "blueprint" in the claim overstates the level of implementation detail available (it is a design pattern to emulate, not a reproducible algo

**Counter-source:** None found. Closest independent point of comparison, Meta's probabilistic-flakiness post (https://engineering.fb.com/2020/12/10/developer-tools/probabilistic-flakiness/), corroborates rather than contradicts the multi-signal probabilistic-scoring approach.

### not refuted (confidence: high)
**Claim:** At Atlassian, flaky tests were responsible for up to 21% of master build failures in the Jira Frontend repository and roughly 15% of Jira backend repo failures, with the resulting reruns wasting over 150,000 developer hours per year — quantifying why flaky-test management is a first-order productivity problem, not a nice-to-have.

**Evidence:** Verified directly against the primary source. The Atlassian engineering blog post (https://www.atlassian.com/blog/atlassian-engineering/taming-test-flakiness-how-we-built-a-scalable-tool-to-detect-and-manage-flaky-tests, by Nitish Malik, Senior Engineering Manager, published December 8, 2025) states verbatim: "Test flakiness has been a significant contributor to build reliability issues in the past, responsible for as much as 21% of master build failures" in the Jira Frontend repository, and "Approximately 15% of Jira backend repo failures are attributed to flaky tests, necessitating reruns that ultimately waste over 150,000 hours of developer time each year." The claim's numbers ("up to 21%", "roughly 15%", "over 150,000 developer hours per year") match the source exactly, with appropriate hedging preserved ("up to" mirrors "as much as"; "roughly" mirrors "approximately"). One minor precision note that does not rise to refutation: the 150,000-hours figure is attributed in the source specifically to Jira backend reruns, and the claim's sentence structure preserves that linkage ("with the resulting reruns wasting..."). Source quality matches claim strength: this is a first-party internal metric that only Atlassian could report, the claim explicitly frames it as Atlassian's own figure ("At Atlassian..."), and it is recent (Dec 2025, ~8 months old). Web search found no contradicting or qualifying sources; the same article cites corroborating industry figures (Microsoft Research: 13% of test failures flaky; Google: 16%), which are consistent in magnitude with the 15-21% range c

**Counter-source:** None found — searches surfaced only the original Atlassian post, its AMP mirror, and third-party blogs (edgedelta.com, pie.inc, testmuai.com) that repeat the figures uncritically; no source disputes or qualifies them.

### not refuted (confidence: high)
**Claim:** At Atlassian, flaky tests were responsible for up to 21% of master build failures in the Jira Frontend repository and roughly 15% of Jira backend repo failures, with the resulting reruns wasting over 150,000 developer hours per year — quantifying why flaky-test management is a first-order productivity problem, not a nice-to-have.

**Evidence:** Verified against the primary source (Atlassian engineering blog, "Taming Test Flakiness," published December 8, 2025). All three figures appear verbatim in the "Why is it a Big Deal?" section: (1) "responsible for as much as 21% of master build failures in the Jira Frontend repository" — matches the claim's "up to 21%"; (2) "Approximately 15% of Jira backend repo failures are attributed to flaky tests, necessitating reruns that ultimately waste over 150,000 hours of developer time each year" — matches the 15% and 150,000-hour figures. A web search found no contradicting or qualifying coverage; third-party articles (edgedelta.com, pie.inc, etc.) cite the same numbers without dispute. Source quality is appropriate: a first-party engineering blog is the correct primary source for a company's own internal metrics, and the December 2025 date is current. Two minor caveats that do not rise to refutation: (a) in the source, the 150,000-hour figure is grammatically attached specifically to the Jira *backend* rerun sentence, whereas the claim's phrasing ("the resulting reruns") could be read as covering both repos — a slight aggregation but not a material misread; (b) the numbers are self-reported and unaudited, published in a post promoting Atlassian's internal flaky-test tool, so they carry the usual first-party-metric caveat. The claim's interpretive framing (flaky-test management as a first-order productivity problem) is proportionate to the evidence.

**Counter-source:** None found — search surfaced only corroborating citations of the same Atlassian figures (e.g., edgedelta.com, pie.inc knowledge-base articles); no source disputes or qualifies them.

### not refuted (confidence: medium)
**Claim:** An in-pipeline retry-based detector — a CLI in the CI pipeline that checks failing tests against the known-flaky list, implicitly retries unknown failures, and circuit-breaks on the first pass/fail flip signal — achieved an 81% flaky-test detection rate for some Atlassian products, validating rerun/flip-signal detection at ingest time (rather than only post-hoc window analysis) as a highly effecti

**Evidence:** Primary source verified directly (WebFetch of the Atlassian engineering blog, published Dec 8, 2025 — current). The article states verbatim: "The Flakinator CLI, integrated into the pipelines, checks whether failing test cases are already designated as flaky. If a test is not included in the flaky list, an implicit retry mechanism is employed to collect flaky signals, with the circuit breaking at the first occurrence of a flip signal" and "This approach has enabled us to achieve an impressive 81% detection rate for certain products." The claim reproduces both the mechanism and the figure faithfully and preserves the load-bearing hedge ("for some Atlassian products"). Adversarial search for contradicting evidence found the strongest counterpoint in DeFlaker (Bell et al., ICSE 2018): Maven's naive same-JVM rerun-on-failure confirmed only 23% of 5,328 failures as flaky, and rerun-based detection misses low-frequency flakes (a 1-in-300 flake won't flip in a few retries). This qualifies but does not contradict the claim: DeFlaker measured a different, cruder mechanism (single same-JVM rerun) with a different denominator, whereas Flakinator combines a known-flaky list, multiple implicit retries, and flip-signal circuit-breaking. Caveats a roadmap consumer must carry: the 81% is self-reported with zero published methodology (no denominator definition, no ground truth, no sample size), "certain products" implies the other products among Atlassian's 12 did worse (selection of best result), and the promotional tone ("impressive") is typical vendor-blog framing. So the figure is an ex

**Counter-source:** DeFlaker: Automatically Detecting Flaky Tests (Bell et al., ICSE 2018), https://www.cs.cornell.edu/~legunsen/pubs/BellETAL18DeFlaker.pdf — reports Maven's rerun-on-failure marked only 23% of 5,328 failures as flaky, showing rerun-based detection rates are highly variable and can be far below 81% for naive implementations; qualifies the "highly effective mechanism" generalization without contradict

### not refuted (confidence: high)
**Claim:** Quarantine only works as a closed loop with accountability: detection auto-creates Jira tickets with resolution deadlines routed via code ownership plus Slack notifications, quarantined tests keep running in branch/scheduled/quarantine pipelines to collect health signals, and tests that stay healthy for a configured period are automatically un-quarantined — meaning a quarantine lifecycle without o

**Evidence:** Attempted refutation failed on all checklist items. (1) Quote support: the supplied quote only covers continued execution and auto-un-quarantine, but a direct fetch of the Atlassian article (Flakinator, published 2025-12-08) confirmed the remaining components verbatim: "creates Jira tickets with deadlines to resolve them", "a Jira ticket is created for the owning team with pre-decided due dates", "the code ownership system identifies its owners" with "Slack notifications if configured", plus the quoted signal-collection and health-based un-quarantine passages — so every descriptive element of the claim is in the primary source. (2) The normative wrapper ("a lifecycle without these is incomplete") is the researcher's synthesis, not an Atlassian sentence, but a contradiction search found only corroboration: multiple independent practitioner sources (Mergify, minware, DeFlaky, FlakyGuard, GitLab quality-engineering issue tracker) independently describe the same failure mode — quarantine becomes a "graveyard" without a ticket, an owner, a maximum-stay SLA (2-4 weeks), and monitored re-instatement — which is exactly the accountability loop the claim asserts. No credible source argues quarantine works fine without ownership/deadlines/re-instatement. (3) Source quality: first-party engineering case study from Atlassian's engineering blog, appropriate for a practitioner-practice claim; the mechanics described are implementation detail, not marketing benchmark numbers. (4) Recency: Dec 2025, current. One caveat kept me from calling it perfect: "only works" is stronger than any sourc

**Counter-source:** Searched for contradicting evidence (quarantine graveyard / auto-unquarantine best practices); found only corroborating sources: mergify.com/learn/test-quarantine, minware.com/guide/best-practices/flaky-test-quarantine, deflaky.com/blog/test-quarantine-strategy-guide, flakyguard.com/blog/how-to-quarantine-flaky-tests — none dispute the claim.

### REFUTED (confidence: medium)
**Claim:** An in-pipeline retry-based detector — a CLI in the CI pipeline that checks failing tests against the known-flaky list, implicitly retries unknown failures, and circuit-breaks on the first pass/fail flip signal — achieved an 81% flaky-test detection rate for some Atlassian products, validating rerun/flip-signal detection at ingest time (rather than only post-hoc window analysis) as a highly effecti

**Evidence:** The narrow factual core checks out: I fetched the Atlassian blog (published 2025-12-08) and confirmed the exact quote — "The Flakinator CLI, integrated into the pipelines... implicit retry mechanism... circuit breaking at the first occurrence of a flip signal... an impressive 81% detection rate for certain products." However, the claim as written overreaches in its validating clause. (1) The blog never defines "detection rate" — no denominator, no ground truth, no methodology for how flakiness was independently confirmed — so 81% is an unverifiable self-reported vanity metric; the blog itself hedges with "certain products," implying materially worse rates elsewhere (cherry-picking acknowledged in-source). (2) The generalization "validating rerun/flip-signal detection at ingest time as a highly effective mechanism" is contradicted by peer-reviewed evidence: the DeFlaker study (Bell et al., ICSE 2018) found rerun-based detection (Maven Surefire, up to 5 reruns) flagged only ~23% of confirmed flaky failures, and low-frequency flakes (e.g., 1-in-300) fundamentally escape single-flip retry budgets — meaning the mechanism's recall ceiling is heavily workload-dependent, not "validated" by one undefined first-party number. (3) The adjective "impressive" and absence of any limitations discussion mark this as promotional engineering-blog content, adequate for "Atlassian reports X" but not for the strength of the claim's conclusion. A defensible rewrite would be: "Atlassian self-reports 81% detection (methodology undisclosed) for some products using in-pipeline retry/flip detection — 

**Counter-source:** https://www.cs.cornell.edu/~legunsen/pubs/BellETAL18DeFlaker.pdf (DeFlaker, ICSE 2018: reruns detected only ~23% of confirmed flaky failures vs 95.5% for coverage-differential detection)

### not refuted (confidence: high)
**Claim:** Atlassian's flakiness scoring goes beyond simple flip-count heuristics: it applies Bayesian inference over a moving window of historical runs and fuses multiple signal distributions (duration variability, environment consistency, result patterns, retry frequency) into a single 0-1 flakiness score — a concrete blueprint for upgrading window+flip heuristics to probabilistic multi-signal scoring.

**Evidence:** Verified against the primary source (Atlassian engineering blog, "Taming test flakiness" / Flakinator, published 2025-12-08). The article's "Bayesian Inference for Flakiness Detection" section states in first person: "we use the prior probability distribution of a test case's historic runs and create the posterior probability from it," and enumerates the analysis/inference component's 3 modules verbatim: Historical Analysis ("Utilise a moving window approach to analyse historical test run data, applying Bayesian inference to calculate the probability of a test being flaky"), Signal Processors ("consider multiple signal distributions (e.g., duration variability, environment consistency, result patterns, retry frequency)"), and Scoring ("Assign a flakiness score between 0 and 1, where higher scores indicate greater flakiness"). Production status is evidenced by concrete metrics: used by 12+ Atlassian products, 22,000+ builds recovered, 7,000 unique flaky tests identified. Every element of the claim (Bayesian inference, moving window, multi-signal distributions, single 0-1 score) maps to explicit article text; source is primary and recent. Minor caveat for the roadmap consumer: the blog does not publish the fusion math, so it is a directional architecture blueprint rather than a full algorithm spec — this qualifies "concrete blueprint" slightly but does not refute the claim.

### not refuted (confidence: high)
**Claim:** Quarantine only works as a closed loop with accountability: detection auto-creates Jira tickets with resolution deadlines routed via code ownership plus Slack notifications, quarantined tests keep running in branch/scheduled/quarantine pipelines to collect health signals, and tests that stay healthy for a configured period are automatically un-quarantined — meaning a quarantine lifecycle without o

**Evidence:** All four components are verbatim in the primary source: fetched the Atlassian article and confirmed, beyond the supplied quote (continued execution in branch/scheduled/quarantine pipelines + auto-unquarantine after a healthy period), it also states "After detecting a flaky test, the code ownership system identifies its owners, creates Jira tickets with deadlines to resolve them" and "sends Slack notifications if configured." Adversarial search for contradiction found none: independent practitioner sources (Mergify, DeFlaky, minware, FlakyGuard, descriptions of Google's auto-quarantine + auto-filed bugs) converge on the same closed loop, and the documented failure mode of quarantine WITHOUT ownership/deadlines/re-instatement — the "quarantine graveyard" where tests never return and coverage silently erodes — directly supports the "incomplete without" framing. Only qualification found: re-instatement must require sustained stability, not one pass, which the claim's "healthy for a configured period" already encodes. Caveat: the universal "only works" phrasing is the claim author's synthesis, not asserted by Atlassian itself, but it is corroborated rather than contradicted.

**Counter-source:** Searched for counter-evidence (quarantine criticism, auto-unquarantine risk); none contradicted the claim — closest qualifier is guidance that one passing run is insufficient for re-qualification (e.g. https://deflaky.com/blog/test-quarantine-pattern, https://mergify.com/learn/test-quarantine), which the claim's "configured period" wording already accommodates.

### not refuted (confidence: high)
**Claim:** Quarantine only works as a closed loop with accountability: detection auto-creates Jira tickets with resolution deadlines routed via code ownership plus Slack notifications, quarantined tests keep running in branch/scheduled/quarantine pipelines to collect health signals, and tests that stay healthy for a configured period are automatically un-quarantined — meaning a quarantine lifecycle without o

**Evidence:** Verified directly against the primary source (Atlassian engineering blog, "Flakinator", published 2025-12-08). Every descriptive component of the claim appears verbatim in the article, not just the supplied quote: (a) ownership + deadlines + Slack — "Once a flaky test is detected, a Jira ticket is created for the owning team with pre-decided due dates to provide resolution. Additionally, the Flakinator Bot sends out Slack notifications" and "the code ownership system identifies its owners, creates Jira tickets with deadlines to resolve them"; (b) continued execution — quarantined tests run "in branch builds, scheduled jobs, or quarantine pipelines to gather results with the latest code changes"; (c) auto re-instatement — "If a test remains healthy for a configured period, we remove it from quarantine and reintroduce it into the system." So the supporting quote understates the source: the article covers the full closed loop, including the ticket/ownership/Slack elements the quote omits. The normative tail ("a quarantine lifecycle without these is incomplete") is a synthesis beyond one company's practice, but it is independently corroborated rather than contradicted: Dropbox's Athena (dropbox.tech, first-party) auto-quarantines, notifies owners, and tracks health; Trunk and Mergify practitioner guides prescribe the same loop (auto-quarantine + owner assignment + SLA deadlines, typically 14 days/2-4 weeks + scheduled promotion of recovered tests back to the main pipeline); this echoes Fowler's long-standing warning that unmanaged quarantine becomes "where tests go to die." I f

### not refuted (confidence: high)
**Claim:** Flaky tests are prevalent at industrial scale: Google reported ~16% of its tests were flaky, and GitHub reported that in 2020 roughly one in eleven commits had a red build caused by a flaky test — meaning flaky-test management is a first-order problem for any test-intelligence product, not an edge case.

**Evidence:** Both figures verified against primary sources, not just the citing survey. (1) Google: John Micco, Google Testing Blog, "Flaky Tests at Google and How We Mitigate Them" (May 27, 2016) states verbatim: "Almost 16% of our tests have some level of flakiness associated with them!" and adds "about 84% of the transitions we observe from pass to fail involve a flaky test" — the claim's phrasing matches the source exactly. (2) GitHub: Jordan Raine, github.blog "Reducing flaky builds by 18x" (Dec 16, 2020) states verbatim: "1 in 11 commits had at least one red build caused by a flaky test, or about 9 percent of commits." The intermediate source (Journal of Systems and Software 2023 survey, peer-reviewed) quotes both accurately. Caveats checked and found non-fatal: (a) Google's 16% is tests with *some* flakiness while only ~1.5% of test *runs* are flaky — but the claim's wording mirrors Micco's own; (b) GitHub's 1-in-11 was the pre-mitigation 2020 rate, later reduced to 1-in-200 via dedicated flaky-test management — which strengthens, not weakens, the claim that flaky management is a first-order product concern; (c) figures are 2016/2020 but the claim explicitly dates them, and newer corroboration exists (Trunk's 2024 analysis of 202M CI jobs, GitLab flaky-test handbook, Microsoft studies). No credible contradicting evidence found.

**Counter-source:** https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html and https://github.blog/engineering/reducing-flaky-builds-by-18x/ (both primary sources confirm rather than contradict; GitHub post adds the post-mitigation 1-in-200 figure as context)

### not refuted (confidence: high)
**Claim:** Rerunning is the dominant flaky-detection technique in practice, but the review flags it as expensive (organizations only rerun new/changed tests to control cost) and cites Vassallo et al. classifying retry-on-failure as a CI smell that slows progress and hides real bugs — implying better tools need detection signals beyond blind reruns (e.g., ML/static classification, flip-history heuristics).

**Evidence:** Verified against the full text of the primary source (Tahir et al., "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review", Journal of Systems & Software 206, 2023; arXiv:2212.00908 — the paper behind ScienceDirect PII S0164121223002327). All three load-bearing elements of the claim appear nearly verbatim in the extracted PDF text: (1) RQ2 summary: "Rerun (in different forms) is the most common dynamic approach for detecting flaky tests. Approaches that use rerun focus on making flaky tests detection less expensive by accelerating ways to manifest flakiness or running fewer tests." (2) Cost point: "Pinto et al. [S36] pointed out that it can be costly to run detectors after each change and hence organizations run them only on new or changed tests, which might not be the best approach as this would affect the recall." (3) Vassallo point: "Vassallo et al. [S75] identified retrying failure to deal with flakiness as a CI smell, as it has a negative impact on development experience by slowing down progress and hiding bugs" — [S75] confirmed in the bibliography as Vassallo et al., ESEC/FSE 2020 ("Configuration smells in continuous delivery pipelines"). Source quality is strong: a peer-reviewed 2023 multivocal review of 200 articles (109 academic + 91 grey literature) in a top software-engineering journal, appropriate for a claim of this strength, and not outdated. Minor caveats that do not rise to refutation: the paper says "most common DYNAMIC approach" while the claim says "dominant technique in practice" (a slight generalization, though the review's grey

### not refuted (confidence: high)
**Claim:** In a survey of 335 professional developers and testers (233 global via Prolific + 102 BMW), 51% experience flaky test failures at least weekly and 66% rate flakiness a moderate or serious problem, with continuous integration and automated testing usage being strong positive predictors of flakiness prevalence — meaning a CI-focused test-intelligence product serves exactly the population hit hardest

**Evidence:** VERIFIED against the primary source (Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers and What Support They Need To Address It", arXiv:2203.00483, peer-reviewed at IEEE ICST 2022). I downloaded the PDF and extracted its text (67,884 chars) rather than relying on the abstract, and grepped each claim component:

1) Quoted stat is verbatim, not paraphrase: "Developers perceive flakiness as a common and severe issue, with 51 % of all participants experiencing it at least weekly and 66 % rating it as a moderate or serious problem." Repeated in the results section: "Almost every participant has to deal with flaky failures at least a few times a year (only 13 never experience the problem), a majority experiences the issue at least weekly, and 66 % rate it a moderate or serious issue!" — so "weekly" (not "monthly") is correct; no misread.

2) Sample composition is exact: "Our sample population consists of 233 professionals from the general public all over the globe, as well as 102 employees of the BMW group" (= 335). Prolific confirmed in Threats to Validity: "The 233 global participants hired via Prolific [33]…". BMW recruitment was direct outreach/convenience sampling. Claim's "233 global via Prolific + 102 BMW" is accurate.

3) The CI/automation predictor wording is the paper's own, not the claimant's inflation: "The usage of automated testing and continuous integration (bottom two bars) are both strong positive predictors for the prevalence (and partially severity) of flaky failures". Method was per-variable regression coefficients with 95% CIs ("We considere

**Counter-source:** No refuting source found. Nearest independent check corroborates: Parry, Kapfhammer, Hilton, McMinn, "Surveying the Developer Experience of Flaky Tests" (ICSE-SEIP 2022, n=170) — 59% deal with flaky tests monthly/weekly/daily (https://philmcminn.com/publications/parry2022a.pdf). Main qualifications come from the source paper's own Threats to Validity section (self-selection in the BMW convenience 

### REFUTED (confidence: high)
**Claim:** Unmanaged flakiness destroys trust in the entire test signal: the review quotes practitioners reporting organizations where 50%+ of tests were flaky and developers stopped writing tests and stopped looking at results — the core justification for trustworthy flaky-classification, quarantine lifecycles, and honest release-gate signals.

**Evidence:** REFUTED on provenance, source quality, and overreach — though the weaker underlying premise survives.

1) MISATTRIBUTION / PROVENANCE LAUNDERING. The claim says "the review quotes practitioners reporting organizations where 50%+ of tests were flaky." The quote is not a research finding of Tahir, Rasheed, Dietrich, Hashemi & Zhang, "Test flakiness' causes, detection, impact and responses: A multivocal review," JSS 206:111837 (2023) (= arXiv 2212.00908). It is a single grey-literature item the review ingested. Original source: Bryan Lee, "We Have A Flaky Test Problem," Medium/scopedev, 13 Nov 2019 — Lee worked at Undefined Labs, and the post extensively promotes their product Scope (test-visibility dashboards), closing with product messaging. Undefined Labs was acquired by Datadog; Scope became Datadog Test Optimization — i.e. a direct competitor in TestLookup's own comparison set. The "primary source" cited is therefore a vendor marketing post, not a primary study. A multivocal review by design mixes 560 academic articles with 91 grey-literature blog posts; being cited in it is inclusion, not validation, and the arXiv abstract carries no quality caveat that would upgrade it.

2) MARKETING FLUFF / NO METHODOLOGY. "We've talked to some organizations that reached 50%+..." names no organization, gives no count, no measurement definition of "flaky," no time window, no data. It is unverified secondhand anecdote from a party with a commercial interest in flakiness being framed as an existential crisis. Fails the "extraordinary claims need primary sources" test outright.

3) CONTRAD

**Counter-source:** Original quote source: Bryan Lee (Undefined Labs/Scope, now Datadog Test Optimization), "We Have A Flaky Test Problem," Medium/scopedev, 2019-11-13 — vendor marketing, anecdotal, no data (https://medium.com/scopedev/how-can-we-peacefully-co-exist-with-flaky-tests-3c8f94fba166). Contradicting measurements: Google ~1.5% of test runs / ~16% of tests flaky; Microsoft ~26% of sampled builds. Peer-revie

### not refuted (confidence: high)
**Claim:** Rerunning is the dominant flaky-detection technique in practice, but the review flags it as expensive (organizations only rerun new/changed tests to control cost) and cites Vassallo et al. classifying retry-on-failure as a CI smell that slows progress and hides real bugs — implying better tools need detection signals beyond blind reruns (e.g., ML/static classification, flip-history heuristics).

**Evidence:** VERIFIED AGAINST PRIMARY TEXT. ScienceDirect blocked WebFetch (403), so I pulled the author preprint (arXiv 2212.00908, identical paper: Tahir, Rasheed, Dietrich, Hashemi, Zhang, "Test flakiness' causes, detection, impact and responses: A multivocal review", Journal of Systems and Software vol. 206, 2023; dblp journals/jss/TahirRDHZ23), extracted all 65 pages to text, and grepped it. Every component of the claim is verbatim in the source:

(1) Rerun dominance — RQ2 summary box: "Most static approaches use machine learning. Rerun (in different forms) is the most common dynamic approach for detecting flaky tests. Approaches that use rerun focus on making flaky tests detection less expensive by accelerating ways to manifest flakiness or running fewer tests."

(2) Cost / new-or-changed-tests — "Pinto et al. [S36] pointed out that it can be costly to run detectors after each change and hence organizations run them only on new or changed tests, which might not be the best approach as this would affect the recall."

(3) Vassallo CI smell — the very next sentence in the same paragraph: "Vassallo et al. [S75] identified retrying failure to deal with flakiness as a CI smell, as it has a negative impact on development experience by slowing down progress and hiding bugs." Independently corroborated: Vassallo et al. (2020) "Configuration Smells in Continuous Delivery Pipelines" (ACM) ships a CI-linter detecting a "retry failure" smell. Table 8 of the review also lists "Delays CI workflow [S75, S63]" and "Costly to detect [S6, S36, S81]".

Source quality matches claim strength: peer-revi

**Counter-source:** https://dl.acm.org/doi/10.1145/3763098 (OOPSLA 2025, "Understanding and Improving Flaky Test Classification" — ML flaky classifiers over-estimated: F1 85.38%→56.62% on realistic data; qualifies the "use ML instead of reruns" implication but does not refute rerun dominance) and https://link.springer.com/article/10.1007/s10664-023-10307-w (Parry et al., EMSE 2023 — CANNIER combines ML WITH reruns ra

### not refuted (confidence: high)
**Claim:** Developers overwhelmingly fall back to manual mitigations — rerunning tests is by far the most used strategy (mean 2.80/4) while automated techniques rank lowest (auto detect 1.16, auto debug 1.11, auto disable/quarantine 1.03) — partly because existing research tools are Java-build-tool plugins unusable in many industrial stacks; this is the adoption gap a multi-framework platform's flaky detecti

**Evidence:** Every number checks out verbatim against the primary source. I downloaded arXiv:2203.00483v4 (Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers and What Support They Need To Address It", ICST 2022, n=335) and extracted the text locally with pypdf (saved at C:\Users\anand\AppData\Local\Temp\claude\C--Users-anand-Downloads-Projects-testlookup-new\9f99041b-e32b-43ea-91ba-ad86057aa3a9\scratchpad\gruber.txt) rather than relying on a summarizer.

(1) Numbers: Table VI is captioned "Mean rating per mitigation strategy; never (0) - always (4)" with columns Global / Auto[motive] / Both. The pooled ("Both") column reads: Rerun 2.80, Rerun in different environment 1.89, Auto report 1.51, Flag 1.51, Shuffle test order 1.43, Incentives & Penalties 1.40, Disable 1.29, Quantify 1.27, Visualize 1.26, Auto detect 1.16, Auto debug 1.11, Auto disable 1.03. So 2.80 / 1.16 / 1.11 / 1.03 are exact, the scale is 0-4 as the claim states, and auto detect/debug/disable are literally the bottom three of twelve strategies.

(2) The framing quote is verbatim in Section V: "Rerunning tests is by far the most common reaction, while automated techniques rank lowest." The Java clause is also verbatim, immediately following: "This might be the case because many sophisticated mitigation tools are implemented as plugins to Java build tools [3],[22],[26], however, many industrial projects—especially in the automotive industry—do not use Java, preventing developers from applying these techniques without re-implementing them." The discussion section repeats it as "in part because there is a lac

**Counter-source:** No refuting source found. Closest qualifiers: the 2025-26 commercial flaky-management landscape (Trunk Flaky Tests, BuildPulse, Buildkite Test Engine, CircleCI Test Insights, Datadog Test Optimization, Gradle Develocity) means automated quarantine is no longer research-only Java tooling, which dates the claim's "adoption gap" inference though not its 2022 survey data; and the paper's own Threats t

### not refuted (confidence: high)
**Claim:** The single most-wished-for capability (32 of 153 coded wishes) is visualization of flakiness — specifically dashboards showing a test's outcome history over time with environment metadata — ahead of automatic flaky detection (31), automated root-cause debugging (28), and education/best-practice guidance (25); quoted participant rationale: developers lose the cross-run 'bigger picture'.

**Evidence:** VERIFIED VERBATIM against the primary source. Source is Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers and What Support They Need To Address It," ICST 2022 (arXiv 2203.00483) — peer-reviewed primary empirical study, 335 professional developers/testers. I extracted the PDF text rather than relying on summaries.

(1) Numbers — exact match. Table VII ("Wishes for tools or information to better handle test flakiness expressed by our participants") reads: "Visualization 32 / test result history 14 / which tests are flaky 4 / program behavior 3 / Auto Detection 31 / via static analysis 12 / Auto Debug 28 / find root cause 6 / find location 5 / find failure cause 4 / Education 25 / guides, examples, best practices 16 / help from colleagues 3 / training 2". All four numbers in the claim (32/31/28/25) and their category labels are correct.

(2) Denominator — correct. Section IV-G: "We received 187 suggestions and wishes... we discarded 30 responses... Table VII summarizes the 153 meaningful, on-topic answers."

(3) Quote — verbatim and in the exact context claimed. "They specifically ask for tools to display the test result history, stating that 'We run tests so often, I often miss the bigger picture' (P 187), wishing for 'A tool to visualize how tests fail and succeed over time' (P 12). Such a tool should also include meta information like 'on which device / environment [the test was executed]' (P 292)." This supports both the "outcome history over time" and the "environment metadata" halves of the claim — not an overreach.

(4) The paper itself endorses the ra

**Counter-source:** No refuting source found. Nearest tension is internal to the same paper: its own abstract orders "IDE plugins that detect flaky code as well as better visualizations," de-emphasizing the 32-vs-31 gap the claim leans on; and Section IV-G shows the visualization lead depends on an automotive cohort with a 23/102 response rate.

### REFUTED (confidence: medium)
**Claim:** The most severe perceived consequences of flaky tests are wasted developer time (mean 2.44/4) and loss of trust in test outcomes — not wasted compute from reruns (1.91/4); trust loss is asymmetric (Wilcoxon p<0.001): developers stop believing failing tests ('rerun failures without analyzing', 2.08/4) more than passing ones, so tooling that reduces reruns' machine cost misses the real pain, which i

**Evidence:** PARTIALLY SUPPORTED BUT THE LOAD-BEARING STATISTIC IS MISATTRIBUTED. Verified against the paper's own text (ar5iv HTML of arXiv:2203.00483v4, Gruber & Fraser, submitted 2022-03-01, ICSE-SEIP 2022, n=335).

WHAT CHECKS OUT: Table V values match the claim exactly — "wasting developer time" 2.44, "rerun failures without analyzing" 2.08, "wasting computational resources" 1.91. Caption verbatim: "TABLE V: Mean rating per consequence; never (0) - always (4)" — so "/4" is the correct denominator. The abstract also states verbatim: "Developers are less worried about the computational costs caused by re-running tests and more about the loss of trust in the test outcomes." An independent ICST 2024 industrial case study corroborates the direction (rerun = $0.0002 vs $5.67 for a manual investigation), so the headline is NOT contradicted.

REFUTATION 1 — the "(Wilcoxon p<0.001)" asymmetry does not generalize to "developers"; the paper says the opposite for 70% of its sample. Verbatim: "While this effect is extremely strong for automotive participants, it is not significant when looking only at global participants (p=0.13)." And: "While global participants claim to suffer very similar amounts of trust loss towards both failing and passing test cases, automotive participants retain confidence in passing tests at a far higher rate while mistrusting failures much more frequently." The p<0.001 is driven by the 102-person single-company BMW automotive subgroup; the 233-person global sample (the majority) shows NO significant asymmetry. The claim presents a subgroup effect from one automotive 

**Counter-source:** https://ar5iv.labs.arxiv.org/html/2203.00483 (the claim's own primary source, full text — verbatim: "it is not significant when looking only at global participants (p=0.13)"); Table V caption "never (0) - always (4)"; threats-to-validity on Prolific bias. Corroborating-not-contradicting: Cost of Flaky Tests in CI: An Industrial Case Study, ICST 2024 (https://mediatum.ub.tum.de/doc/1730194/gbm0plj5

### REFUTED (confidence: high)
**Claim:** Unmanaged flakiness destroys trust in the entire test signal: the review quotes practitioners reporting organizations where 50%+ of tests were flaky and developers stopped writing tests and stopped looking at results — the core justification for trustworthy flaky-classification, quarantine lifecycles, and honest release-gate signals.

**Evidence:** REFUTED on sourcing and strength, not on direction.

1) The load-bearing quote is VENDOR MARKETING, not research. The exact sentence ("We've talked to some organizations that reached 50%+ flaky tests in their codebase, and now developers hardly ever write any tests and don't bother looking at the results") originates in a blog post, "We Have A Flaky Test Problem," by Bryan Lee, Product Manager at Undefined Labs, published on Medium/dev.to on 2019-12-09. Undefined Labs was acquired by Datadog and became Datadog Test Optimization — i.e., a direct competitor named in this very research brief. It is a product-marketing post whose thesis is that you need test-observability tooling.

2) The cited "primary" source is secondary. S0164121223002327 is Tahir et al., "Test flakiness' causes, detection, impact and responses: A multivocal review," JSS 206 (2023) 111837. A *multivocal* review deliberately ingests grey literature (blogs, vendor posts) alongside peer-reviewed work. Quoting a blog is not the review's finding; presenting a multivocal review as the "primary source" for a vendor anecdote inverts the evidence hierarchy.

3) "Practitioners reporting" is a misread of the quote. It is one vendor PM's second-hand paraphrase of unnamed, uncounted organizations ("some organizations"), with zero data, no sample, no measurement method. Not a survey, not a case study, not multiple practitioners.

4) The 50%+ figure is contradicted by every measured dataset — including one cited in the same blog post. Google reports ~16% of tests exhibit some flakiness across 4.2M tests; the 2021 Google/M

**Counter-source:** Quote origin: Bryan Lee (PM, Undefined Labs → Datadog), "We Have A Flaky Test Problem," Medium/dev.to, 2019-12-09 — vendor blog, no data. Cited "primary": Tahir et al., JSS 206 (2023) 111837, a MULTIVOCAL review that ingests grey literature. Contradicting prevalence: Google ~16% of 4.2M tests flaky; 2021 survey 41% Google / 26% Microsoft on a narrower denominator. Nuanced peer-reviewed impact: Hab

### REFUTED (confidence: high)
**Claim:** The most severe perceived consequences of flaky tests are wasted developer time (mean 2.44/4) and loss of trust in test outcomes — not wasted compute from reruns (1.91/4); trust loss is asymmetric (Wilcoxon p<0.001): developers stop believing failing tests ('rerun failures without analyzing', 2.08/4) more than passing ones, so tooling that reduces reruns' machine cost misses the real pain, which i

**Evidence:** Verified against the primary source (Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers and What Support They Need To Address It," ICST 2022, arXiv:2203.00483, n=335). Two load-bearing parts of the claim fail.

(1) METRIC MISREAD — these are FREQUENCY means, not severity. Table V (Sec. IV-E) reports answers to "Which negative effects of flaky tests have you experienced?" on a never(0)–always(4) scale. So 2.44 / 2.08 / 1.91 measure how OFTEN a consequence was experienced, not how severe it is judged. The paper's own RQ3 box says: "Flaky tests inhibit project development by wasting developer time more than computational resources and blocking pull requests" — a frequency comparison. The claim's "most severe perceived consequences (mean 2.44/4)" relabels a frequency scale as a severity scale. Relatedly, the cited "supporting quote" ("Losing trust and wasting developer time are perceived as the most severe impact…") does not appear verbatim in this paper; the abstract's actual wording is "less worried about the computational costs caused by re-running tests and more about the loss of trust in the test outcomes" — i.e. the quote is a secondary-source paraphrase attributed to a primary source.

(2) THE ASYMMETRY IS CONTRADICTED BY THE PAPER'S OWN SUBGROUP DATA. The claim states flatly that "trust loss is asymmetric (Wilcoxon p<0.001): developers stop believing failing tests … more than passing ones." The pooled p<0.001 is driven entirely by the 102 BMW/automotive respondents; for the 233-person global/public sample the paper reports p=0.13 (NOT significant), and 

**Counter-source:** Gruber & Fraser, arXiv:2203.00483 §IV-E / Table V and RQ3 summary (via https://ar5iv.labs.arxiv.org/html/2203.00483) — the paper's own subsample breakdown reporting p=0.13 for the 233 global respondents and reversed means (1.89 distrust-failures vs 2.00 distrust-passes), plus the never(0)–always(4) frequency question wording "Which negative effects of flaky tests have you experienced?"

### not refuted (confidence: high)
**Claim:** In a survey of 335 professional developers and testers (233 global via Prolific + 102 BMW), 51% experience flaky test failures at least weekly and 66% rate flakiness a moderate or serious problem, with continuous integration and automated testing usage being strong positive predictors of flakiness prevalence — meaning a CI-focused test-intelligence product serves exactly the population hit hardest

**Evidence:** Verified against the primary source. arXiv 2203.00483 = Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers and What Support They Need To Address It," IEEE ICST 2022 (submitted 2022-03-01, v2 2022-04-08; comment field confirms "to be published in the Proceedings of ... ICST 2022"). Every load-bearing element checks out against the full text (ar5iv HTML, two independent extraction passes):

1. Sample: abstract states "we surveyed 335 professional software developers and testers in different domains." Body: "233 professionals from the general public all over the globe" (recruited via Prolific with a multi-stage filtering process) "as well as 102 employees of the BMW group." Claim's 335/233/102 split is exact.
2. Prevalence/severity: RQ1 reports "51 % of all participants experiencing it at least weekly" and "66 % rating it as a moderate or serious problem" (second pass rendered the same sentence as "a majority experiences the issue at least weekly, and 66% rate it a moderate or serious issue"). Also "only 13 [participants] never experience the problem." The supporting quote is the RQ1 summary box, NOT the abstract (the arXiv abstract contains no percentages) — a citation-precision nit, not a misquote.
3. CI predictor: the paper's own wording is "The usage of automated testing and continuous integration (bottom two bars) are both strong positive predictors for the prevalence (and partially severity) of flaky failures," from an ordinal logistic regression where significance = 95% CI not crossing the line of no effect. The claim reproduces this almost verbatim and

**Counter-source:** No refuting source found. Closest tension: Parry et al., "Surveying the Developer Experience of Flaky Tests" (ICSE-SEIP 2022, n=170) reports 39% weekly-or-more (15% daily + 24% weekly) vs. Gruber & Fraser's 51% — a sample-dependent difference, not a contradiction. Generalizability caveat corroborated by Baltes & Ralph, "Sampling in software engineering research" (EMSE 2022), which documents that n

### REFUTED (confidence: medium)
**Claim:** Unmanaged flakiness destroys trust in the entire test signal: the review quotes practitioners reporting organizations where 50%+ of tests were flaky and developers stopped writing tests and stopped looking at results — the core justification for trustworthy flaky-classification, quarantine lifecycles, and honest release-gate signals.

**Evidence:** Quote verified verbatim in the primary source (extracted JSS PDF text, p.18): Tahir, Rasheed, Dietrich et al., "Test flakiness' causes, detection, impact and responses: A multivocal review," J. Systems & Software 206 (2023) 111837. BUT the paper's own attribution line immediately after the quote reads "(Product Manager at Datadog, Lee (2020))". REFUTATION GROUNDS: (1) SOURCE MISREPRESENTATION — the claim says "practitioners reporting"; it is ONE vendor employee relaying unverifiable secondhand hearsay about unnamed "some organizations." Bryan Lee was founder/PM at Undefined Labs (Scope), a flaky-test-management product later acquired by Datadog; the quote originates in his ~Dec 2019 Medium/DEV post "We Have A Flaky Test Problem," the launch-narrative blog for the product he sells. Datadog Test Optimization is one of the competitor tools this very research brief benchmarks against. A multivocal literature review ingests grey literature by design and does NOT validate its numbers — the paper cites it illustratively under developer perceptions of impact, not as an empirical finding. This is checklist item 5 (marketing claim) wrapped in an academic citation. (2) THE 50%+ NUMBER IS CONTRADICTED BY ALL MEASURED DATA — Google (Micco, ICST 2017 keynote): ~1.5% of test RUNS flaky, ~16% of 4.2M tests show some flakiness over time. Microsoft (5 projects/30 days): 14-52% of BUILDS contained a flaky test — a builds-affected rate, not 50% of a codebase's tests. No empirical study reports a codebase at 50%+ flaky tests; the figure is an unverified outlier. (3) "DEVELOPERS STOPPED WRITING 

**Counter-source:** https://www.sciencedirect.com/science/article/pii/S0164121223002327 (own attribution: "Product Manager at Datadog, Lee (2020)"); origin blog https://medium.com/scopedev/how-can-we-peacefully-co-exist-with-flaky-tests-3c8f94fba166 (Undefined Labs/Scope vendor post, Dec 2019); prevalence counter-data: Micco ICST 2017 Google keynote (1.5% of runs, 16% of tests) and Microsoft 5-project study (14-52% o

### not refuted (confidence: high)
**Claim:** The single most-wished-for capability (32 of 153 coded wishes) is visualization of flakiness — specifically dashboards showing a test's outcome history over time with environment metadata — ahead of automatic flaky detection (31), automated root-cause debugging (28), and education/best-practice guidance (25); quoted participant rationale: developers lose the cross-run 'bigger picture'.

**Evidence:** VERIFIED against the primary source, not a summarizer. I downloaded arXiv 2203.00483 (Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers and What Support They Need To Address It," accepted at IEEE ICST 2022, N=335) and extracted the PDF text locally with pypdf, because an initial ar5iv fetch merely echoed the categories I had named in my prompt and was therefore untrustworthy.

Table VII ("Wishes for tools or information to better handle test flakiness expressed by our participants") reads verbatim: "Visualization 32 / test result history 14 / which tests are flaky 4 / program behavior 3 / Auto Detection 31 / via static analysis 12 / Auto Debug 28 / find root cause 6 / find location 5 / find failure cause 4 / Education 25 / guides, examples, best practices 16 / help from colleagues 3 / training 2 / Rerun 17 / Manual debugging tools 16 / Stable infrastructure 16 / Logging 13 / Management 11 / Reproducibility 9." Every one of the four claimed counts (32/31/28/25) and their ordering is exact.

Denominator verified: "Table VII summarizes the 153 meaningful, on-topic answers" (from 187 raw suggestions; 30 discarded as uninterpretable/generic, 4 re-filed to other RQs).

Quote verified verbatim in Section IV-G (RQ5: Wishes): "We find a strong desire for better visualization of flakiness... They specifically ask for tools to display the test result history, stating that 'We run tests so often, I often miss the bigger picture' (P 187), wishing for 'A tool to visualize how tests fail and succeed over time' (P 12). Such a tool should also include meta information like

**Counter-source:** No contradicting source found. Searches for critiques/replications surfaced only concordant work (ACM TOSEM "A Survey of Flaky Tests"; JSS multivocal review; ScienceDirect mobile-app flakiness developer study). The nearest thing to a counterweight is the paper's OWN abstract, which orders the wishes as "IDE plugins to detect flaky code as well as better visualizations" — putting detection first rh

### not refuted (confidence: high)
**Claim:** In a survey of 335 professional developers and testers (233 global via Prolific + 102 BMW), 51% experience flaky test failures at least weekly and 66% rate flakiness a moderate or serious problem, with continuous integration and automated testing usage being strong positive predictors of flakiness prevalence — meaning a CI-focused test-intelligence product serves exactly the population hit hardest

**Evidence:** VERIFIED AGAINST PRIMARY PDF (downloaded arxiv.org/pdf/2203.00483, extracted with pypdf, 11pp). Every component of the claim is verbatim, not paraphrase:

(1) SAMPLE — p.1: "We surveyed 335 professional developers on their experiences with flaky tests... Our sample population consists of 233 professionals from the general public all over the globe, as well as 102 employees of the BMW group." Confirmed again in Results: "we collected 335 at least partially complete submissions, 102 from automotive participants and 233 from global participants." Prolific recruitment confirmed: "To retrieve a global sample of our target audience, we used Prolific [33], an online service for recruiting subjects" — with a documented multi-stage filter (100% approval rating; 604-person prescreening; 302 passed all three professional-developer filter questions; 233 took the main survey).

(2) THE 51%/66% FIGURES — p.1, verbatim: "Developers perceive flakiness as a common and severe issue, with 51 % of all participants experiencing it at least weekly and 66 % rating it as a moderate or serious problem." Independently restated in Results §IV-C: "a majority experiences the issue at least weekly, and 66 % rate it a moderate or serious issue!"

(3) THE CI/AUTOMATED-TESTING PREDICTOR CLAIM — this is the paper's own wording, not the claimant's gloss. §IV-C: "The usage of automated testing and continuous integration (bottom two bars) are both strong positive predictors for the prevalence (and partially severity) of flaky failures, and both testing practices are used by automotive participants at a far hig

**Counter-source:** No credible contradicting source found. The one apparent conflict — a web-search summary reporting "20% monthly, 24% weekly, 15% daily" and "of the 91%... 56% moderate, 23% serious" — was traced to a MISATTRIBUTION: those figures are Eck et al. 2019 (n=121), which Gruber & Fraser themselves cite on p.2: "there was only one other survey... which involved 121 developers and was conducted by Eck et a

### not refuted (confidence: high)
**Claim:** Developers overwhelmingly fall back to manual mitigations — rerunning tests is by far the most used strategy (mean 2.80/4) while automated techniques rank lowest (auto detect 1.16, auto debug 1.11, auto disable/quarantine 1.03) — partly because existing research tools are Java-build-tool plugins unusable in many industrial stacks; this is the adoption gap a multi-framework platform's flaky detecti

**Evidence:** VERIFIED AGAINST PRIMARY TEXT (extracted the PDF locally with pypdf rather than relying on summaries; text saved at C:\Users\anand\AppData\Local\Temp\claude\C--Users-anand-Downloads-Projects-testlookup-new\9f99041b-e32b-43ea-91ba-ad86057aa3a9\scratchpad\gruber.txt).

Source = Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers and What Support They Need To Address It," IEEE ICST 2022, n=335 (233 general/global professionals via Prolific + 102 BMW automotive). Peer-reviewed primary human-subjects study — appropriate strength for the claim.

1) NUMBERS ARE EXACT, NOT PARAPHRASED. Table VI ("Mean rating per mitigation strategy; never (0) - always (4)"), Global/Auto/Both columns. The "Both" column reads: Rerun 2.70/3.05/**2.80**; Rerun in different environment 1.89; Auto report 1.51; Flag 1.51; Shuffle test order 1.43; Incentives & Penalties 1.40; Disable 1.29; Quantify 1.27; Visualize 1.26; Auto detect 1.21/1.02/**1.16**; Auto debug 1.34/0.53/**1.11**; Auto disable 1.04/1.01/**1.03**. All four cited means (2.80, 1.16, 1.11, 1.03) match exactly, the scale really is 0–4 ("/4" is correct), and the three "Auto *" rows really are the bottom three of eleven. Rerun at 2.80 vs. next-highest 1.89 justifies "by far."

2) THE SUPPORTING QUOTE AND THE JAVA-PLUGIN CLAUSE ARE VERBATIM, NOT INFERRED. Discussion section, lines 984-990 of the extracted text: "Rerunning tests is by far the most common reaction, while automated techniques rank lowest. This might be the case because many sophisticated mitigation tools are implemented as plugins to Java build tools [3], [22], [26],

**Counter-source:** No refuting source found. Closest qualifiers, both of which survive as caveats rather than refutations: (1) JSS multivocal review "Test flakiness' causes, detection, impact and responses" (2023, https://www.sciencedirect.com/science/article/pii/S0164121223002327) reports quarantining as one of the most common practitioner measures — reconciled by Gruber's split between manual "Disable" (1.29, mid-

### REFUTED (confidence: high)
**Claim:** The most severe perceived consequences of flaky tests are wasted developer time (mean 2.44/4) and loss of trust in test outcomes — not wasted compute from reruns (1.91/4); trust loss is asymmetric (Wilcoxon p<0.001): developers stop believing failing tests ('rerun failures without analyzing', 2.08/4) more than passing ones, so tooling that reduces reruns' machine cost misses the real pain, which i

**Evidence:** VERIFIED PORTION: I extracted the full text of arXiv:2203.00483v4 (Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers...", ICST 2022, n=335). Table V ("Mean rating per consequence; never (0) - always (4)"), "Both" column reproduces the cited numbers EXACTLY: Wasting developer time 2.44; Rerun failures without analyzing 2.08; Wasting computational resources 1.91. The Wilcoxon signed-rank p<0.001 is real (p.~8: "we used a Wilcoxon signed-rank test, which resulted in a p-value of less than 0.001"). Source quality is high (peer-reviewed primary survey, 2022, not outdated for this topic, not marketing). So the headline "time+trust outrank compute" survives.

REFUTATION 1 — the asymmetry clause is contradicted BY THE SOURCE ITSELF, and reverses for the relevant population. The claim states flatly that "trust loss is asymmetric (Wilcoxon p<0.001): developers stop believing failing tests more than passing ones." The paper immediately qualifies: "there is a large difference between the two groups: While this effect is extremely strong for automotive participants, it is not significant when looking only at global participants (p = 0.13)." The sample is 233 global (Prolific-recruited) + ~102 automotive, ALL of whom "work with the BMW group" — one company. The pooled p<0.001 is driven entirely by that single-company automotive subsample. Worse, in the Global column the direction FLIPS: "Rerun failures without analyzing" = 1.89 vs "Rerun passes suspecting hidden bugs" = 2.00 — general-industry developers rerun PASSING tests suspecting hidden bugs MORE than they blind-re

**Counter-source:** Self-contradiction within the cited source: Gruber & Fraser, arXiv:2203.00483v4, RQ3/RQ4 subgroup analysis ("not significant when looking only at global participants (p = 0.13)"; Table V Global column: Rerun failures 1.89 < Rerun passes 2.00). Independent corroboration/qualification: Parry, Kapfhammer, Hilton, McMinn, "Surveying the Developer Experience of Flaky Tests," ICSE-SEIP 2022, https://phi

### not refuted (confidence: medium)
**Claim:** Rerunning is the dominant flaky-detection technique in practice, but the review flags it as expensive (organizations only rerun new/changed tests to control cost) and cites Vassallo et al. classifying retry-on-failure as a CI smell that slows progress and hides real bugs — implying better tools need detection signals beyond blind reruns (e.g., ML/static classification, flip-history heuristics).

**Evidence:** VERIFIED AGAINST PRIMARY SOURCE (full PDF text extracted, not just the snippet). Source = Tahir, Rasheed, Dietrich, Hashemi, Zhang, "Test flakiness' causes, detection, impact and responses: A multivocal review", Journal of Systems & Software 206 (2023) 111837 — peer-reviewed, accepted 4 Sep 2023. All three claim components are verbatim in the paper: (1) RQ2 summary box: "Rerun (in different forms) is the most common dynamic approach for detecting flaky tests. Approaches that use rerun focus on making flaky test detection less expensive by accelerating ways to manifest flakiness or running fewer tests." Reinforced in the tools section: "In terms of detection techniques, the most common approach used in these tools is rerun — in particular, retrying the failing tests multiple times to see if their outcomes will change (i.e., from FAIL to PASS)" — so "dominant in practice" is supported, not just in-literature. (2) Cost: "Several articles mention the issue of cost in detecting flaky tests; Pinto et al. (2020) pointed out that it can be costly to run detectors after each change and hence organizations run them only on new or changed tests, which might not be the best approach as this would affect the recall." (3) "Vassallo et al. (2020) identified retrying failure to deal with flakiness as a CI smell, as it has a negative impact on the development experience by slowing down progress and hiding bugs." Vassallo et al. 2020 resolves in the reference list to ESEC/FSE 2020 "Configuration smells in continuous delivery pipelines: A linter and a six-month study on GitLab" (doi 10.1145/3

**Counter-source:** https://link.springer.com/article/10.1007/s10664-023-10307-w (Parry et al., EMSE 2023 — ML-alone underperforms rerunning; hybrid rerun+ML is what actually cuts cost, qualifying the claim's implied "beyond reruns" remedy)

### not refuted (confidence: high)
**Claim:** Developers overwhelmingly fall back to manual mitigations — rerunning tests is by far the most used strategy (mean 2.80/4) while automated techniques rank lowest (auto detect 1.16, auto debug 1.11, auto disable/quarantine 1.03) — partly because existing research tools are Java-build-tool plugins unusable in many industrial stacks; this is the adoption gap a multi-framework platform's flaky detecti

**Evidence:** VERIFIED AGAINST PRIMARY PDF (text-extracted, not search summary). Source = Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers and What Support They Need To Address It," ICST 2022 (arXiv 2203.00483, v4 8 Apr 2022), n=335 (233 global + 102 BMW Group).

(1) NUMBERS EXACT. Table VI ("Mean rating per mitigation strategy; never (0) - always (4)"), pooled "Both" column, verbatim: Rerun 2.80; Rerun in different environment 1.89; Auto report 1.51; Flag 1.51; Shuffle test order 1.43; Incentives & Penalties 1.40; Disable 1.29; Quantify 1.27; Visualize 1.26; Auto detect 1.16; Auto debug 1.11; Auto disable 1.03. All four cited values are correct and are precisely the top-1 and bottom-3 of the 12 strategies. Scale is 0-4 (Never->0 ... Always->4, 5-point Likert), so the claim's "2.80/4" correctly states the maximum; no scale misread.

(2) CAUSAL HALF IS NEAR-VERBATIM SOURCE TEXT, not an inference: "Rerunning tests is by far the most common reaction, while automated techniques rank lowest. This might be the case because many sophisticated mitigation tools are implemented as plugins to Java build tools [3], [22], [26], however, many industrial projects-especially in the automotive industry-do not use Java, preventing developers from applying these techniques without re-implementing them." Paper's RQ4 summary: "Rerunning and rewriting test cases are by far the most dominant approaches... while automated techniques are only rarely used."

(3) INDEPENDENTLY REPLICATED by a different team/sample: Parry, Kapfhammer, Hilton & McMinn, "Surveying the Developer Experience of Flaky 

**Counter-source:** No refuting source found. Nearest independent check (Parry et al., ICSE-SEIP 2022, https://philmcminn.com/publications/parry2022a.pdf) replicates rather than contradicts: Re-run 1st (2.67), Quarantine last 8/8 (0.77). Only genuine tension is recency: 2023-2025 commercial CI-level auto-quarantine tooling (Datadog Flaky Test Management docs, GitLab flaky-test intervention issue, Slack Engineering "H

### not refuted (confidence: high)
**Claim:** Google's own mitigation mechanisms have documented failure modes: marking a test flaky (report failure only after 3 consecutive fails) delays detection of a real breakage by multiple test cycles (e.g., 45 minutes for a 15-minute test) and encourages developers to tolerate flakiness, while automatic quarantine removes the test from the critical path but can mask real race conditions — so a quaranti

**Evidence:** Verified against the primary source (John Micco, Google Testing Blog, 28 May 2016) and a full-text mirror (googblogs.com), because the testing.googleblog.com fetch rendered only comments. Every load-bearing element of the claim appears verbatim:

1) Retry-suppression + delayed detection: "causing it to report a failure only if it fails 3 times in a row. This reduces false positives, but encourages developers to ignore flakiness in their own tests unless their tests start failing 3 times in a row, which is hardly a perfect solution." And the exact 45-minute figure the claim cites: "Imagine a 15 minute integration test marked as flaky that is broken by my code submission. The breakage will not be discovered until 3 executions of the test complete, or 45 minutes, after which it will need to be determined if the test is broken (and needs to be fixed) or if the test just flaked three times in a row." The claim's "45 minutes for a 15-minute test" is not an extrapolation — it is Google's own worked example.

2) Quarantine trade-off: "if the flakiness is too high, it automatically quarantines the test. Quarantining removes the test from the critical path and files a bug for developers to reduce the flakiness. ... This prevents it from becoming a problem for developers, but could easily mask a real race condition or some other bug in the code being tested." Exactly as claimed, including "mask a real race condition."

Checklist results: (1) No overreach on the descriptive half — it is a near-quote. The normative half ("needs SLAs, bug-filing, and unmasking safeguards, not just suppre

**Counter-source:** None found. Searches for critiques of quarantine (arXiv 2212.00908 multivocal review; arXiv 2112.04919 qualitative study; practitioner guides from Mergify, DeFlaky, FlakyGuard) all corroborate the masking risk rather than dispute it. Nearest thing to a qualification: the 2016 post already includes automatic bug-filing on quarantine, so that portion of the claim's prescription is not a gap in Googl

### not refuted (confidence: medium)
**Claim:** Across Google's entire test corpus, about 1.5% of all test runs report a flaky result (a test that both passes and fails against the same code), meaning even a modest per-run flake rate makes fully-green runs rare at scale — an average 1000-test project expects ~15 flaky failures per release-gating cycle, each requiring expensive human investigation.

**Evidence:** ATTEMPTED REFUTATION FAILED — claim survives, with two qualifications.

1) Quote fidelity (checked verbatim). Direct fetch of the canonical URL (testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html) returned only the title, author "John Micco", date "Friday, May 27, 2016" and the comment thread — the body did not render. Fetching the full-text mirror googblogs.com/flaky-tests-at-google-and-how-we-mitigate-them/ returned the body verbatim: "Across our entire corpus of tests, we see a continual rate of about 1.5% of all test runs reporting a 'flaky' result." AND, critically, the second half of the claim is ALSO the source's own arithmetic, not the claimant's extrapolation: "The average project contains 1000 or so individual tests. To release a project, we require that all these tests pass with the latest code changes. If 1.5% of test results are flaky, 15 tests will likely fail." The post also uses "build cop" for the person doing the investigation. So the checklist-1 attack (overreach/misread) fails — the 1000→15→"expensive investigation by a build cop or developer" chain is Google's sentence, not a derived inference.

2) Contradicting evidence search found none in the refuting direction. Every comparable data point I found reports flakiness EQUAL OR WORSE, which reinforces rather than disputes: Microsoft study — flaky failures in ~26% of sampled builds; Slack (2022) — 57% of build failures caused by flaky test jobs (dropped to <4% after automated suppression); Meta — E2E suites ~10% flaky vs well under 1% for unit tests; SAP HANA timeout study (arXiv 2402.05

**Counter-source:** https://www.googblogs.com/flaky-tests-at-google-and-how-we-mitigate-them/ (full-text mirror confirming the verbatim quote); https://research.google.com/pubs/archive/45861.pdf (Memon et al., ICSE-SEIP 2017, peer-reviewed corroboration); https://arxiv.org/pdf/2402.05223 (SAP HANA, far higher rates); https://arxiv.org/html/2504.16777v1 (systemic flakiness, 2025) — all consistent with or worse than th

### not refuted (confidence: high)
**Claim:** Across Google's entire test corpus, about 1.5% of all test runs report a flaky result (a test that both passes and fails against the same code), meaning even a modest per-run flake rate makes fully-green runs rare at scale — an average 1000-test project expects ~15 flaky failures per release-gating cycle, each requiring expensive human investigation.

**Evidence:** Attempted refutation on all five checklist axes; the claim survives.

1) QUOTE FIDELITY — VERIFIED VERBATIM, not an overreach. The live testing.googleblog.com page renders only the comments section via WebFetch, so I verified the body text against the googblogs.com mirror (https://www.googblogs.com/flaky-tests-at-google-and-how-we-mitigate-them/), which reproduces two paragraphs: (a) "Unfortunately, across our entire corpus of tests, we see a continual rate of about 1.5% of all test runs reporting a 'flaky' result. We define a 'flaky' test result as a test that exhibits both a passing and a failing result with the same code." — this matches the claim's parenthetical definition exactly; and (b) "In addition to the cost of build monitoring, consider that the average project contains 1000 or so individual tests. To release a project, we require that all these tests pass with the latest code changes. If 1.5% of test results are flaky, 15 tests will likely fail, requiring expensive investigation by a build cop or developer." The claim's most attackable-looking element — framing it as a "release-gating cycle" for a "1000-test project" — is the SOURCE's own framing ("To release a project, we require that all these tests pass"), not the claim author's embellishment. The 1000-test figure and the ~15 arithmetic are Google's, not extrapolated by the claimant.

2) CONTRADICTING EVIDENCE — none found. Searches for critiques/misinterpretation of the 1.5% figure surfaced only qualifications that CUT IN THE CLAIM'S FAVOR or are orthogonal: academic corpora report lower base rates (0.5–1% o

**Counter-source:** https://www.sciencedirect.com/science/article/pii/S0164121223002327 (multivocal review: independent studies find 0.5–1% of tests flaky, a different metric than Google's 1.5% of executions — qualifies generalizability, does not contradict); https://research.google/pubs/flake-aware-culprit-finding/ (ICST 2023, no updated corpus-wide rate published to supersede the 2016 figure)

### not refuted (confidence: high)
**Claim:** Almost 16% of Google's tests exhibit some level of flakiness, and despite heavy investment in fixing flaky tests, the rate at which new flaky tests are introduced roughly equals the fix rate — implying that one-off fix campaigns cannot eliminate flakiness and tooling must treat it as a permanent steady-state condition to manage, not a backlog to burn down.

**Evidence:** VERBATIM CHECK — PASSED. Direct WebFetch of testing.googleblog.com returned only the comments section, so I confirmed the body via the googblogs.com mirror, which reproduces the exact strings and attributes them to John Micco, Google Testing Blog, May 2016: "Almost 16% of our tests have some level of flakiness associated with them!", "overall the insertion rate is about the same as the fix rate, meaning we are stuck with a certain rate of tests", "a continual rate of about 1.5% of all test runs reporting a 'flaky' result", and "about 84% of the transitions we observe from pass to fail involve a flaky test!". A search-engine read of the primary URL returned the same figures independently. Both numbers in the claim are quoted, not paraphrased.

INFERENCE CHECK — NOT AN OVERREACH. The claim's design conclusion ("permanent steady-state condition to manage, not a backlog to burn down") is essentially the source's own wording: Google says it "invested a lot of effort in removing flakiness" yet is "stuck with a certain rate of tests." The claim adds no causal leap beyond what Micco states.

REFUTATION ATTEMPT 1 — later Google data showing improvement: found none. Google's April 2017 follow-up ("Where do our flaky tests come from?") reports ~4.2M tests with ~63K having a flaky run in one week (<2% of tests), but that is a one-week window measurement, not a cumulative-ever measurement — it is the analogue of the 1.5% per-run figure, not a contradiction of the 16% cumulative figure. That post makes no claim that flakiness declined; it studies size/RAM correlation. Google's continued 

**Counter-source:** https://testing.googleblog.com/2017/04/where-do-our-flaky-tests-come-from.html — the closest thing to a counter-datum (~63K of 4.2M tests, <2% flaky in a one-week window), but it measures a different window than the 16% cumulative figure and so qualifies rather than refutes. Corroborating counterweights checked: https://www.sciencedirect.com/science/article/pii/S0164121223002327 (2023 multivocal r

### not refuted (confidence: high)
**Claim:** Google's own mitigation mechanisms have documented failure modes: marking a test flaky (report failure only after 3 consecutive fails) delays detection of a real breakage by multiple test cycles (e.g., 45 minutes for a 15-minute test) and encourages developers to tolerate flakiness, while automatic quarantine removes the test from the critical path but can mask real race conditions — so a quaranti

**Evidence:** Attempted refutation on all five checklist axes; the claim survives.

1) QUOTE SUPPORT — stronger than the claim's own citation. The most "suspicious" element (the specific "45 minutes for a 15-minute test" figure, which looked like an unsourced 3x15 inference by the researcher) turns out to be VERBATIM in the primary post. Full-text retrieval via the verbatim mirror https://www.googblogs.com/flaky-tests-at-google-and-how-we-mitigate-them/ returns: "We even have a way to denote a test as flaky - causing it to report a failure only if it fails 3 times in a row." and "Imagine a 15 minute integration test marked as flaky that is broken by my code submission. The breakage will not be discovered until 3 executions of the test complete, or 45 minutes." Quarantine text also confirmed verbatim: "Quarantining removes the test from the critical path and files a bug" and quarantining "could easily mask a real race condition or some other bug in the code being tested." So both failure modes (delayed detection with the exact time example; quarantine masking real races) are stated by Google itself, not inferred. Note: direct WebFetch of testing.googleblog.com returned only chrome/comments (the summarizer missed the body); the mirror is what confirms verbatim text — this is the one soft spot, but corroborated independently below.

2) CONTRADICTING EVIDENCE — none found. Searches for disputes surfaced only corroboration: the 2023 multivocal review in Information & Software Technology (ScienceDirect S0164121223002327) on flakiness detection/response, Wikipedia's flaky-test entry, and 2024-2

**Counter-source:** No refuting source found. Corroborating checks: https://www.googblogs.com/flaky-tests-at-google-and-how-we-mitigate-them/ (verbatim mirror confirming the 15-min/45-min and race-condition sentences), https://www.sciencedirect.com/science/article/pii/S0164121223002327 (2023 multivocal review of flakiness responses), https://en.wikipedia.org/wiki/Flaky_test, and 2024-25 practitioner sources on quaran

### not refuted (confidence: high)
**Claim:** Google's own mitigation mechanisms have documented failure modes: marking a test flaky (report failure only after 3 consecutive fails) delays detection of a real breakage by multiple test cycles (e.g., 45 minutes for a 15-minute test) and encourages developers to tolerate flakiness, while automatic quarantine removes the test from the critical path but can mask real race conditions — so a quaranti

**Evidence:** Attempted refutation on all five checklist axes; the claim survived.

(1) Quote support — STRONGER than the claim asserts. The supporting quote is verbatim, and the "45 minutes for a 15-minute test" parenthetical, which I expected to be an unsupported arithmetic inference (3 x 15), is itself verbatim in the source: "Imagine a 15 minute integration test marked as flaky that is broken by my code submission. The breakage will not be discovered until 3 executions of the test complete, or 45 minutes, after which it will need to be determined if the test is broken (and needs to be fixed) or if the test just flaked three times in a row." Both failure modes the claim names (delayed detection + tolerating flakiness; quarantine masking a real race condition) are stated by Google, not extrapolated.

(2) Contradicting evidence — none found. Independent practitioner and research sources corroborate rather than dispute: rerun/retry mitigation "masks the root cause," teams "stop investigating failures and real bugs slip through" (Mergify, Mill Build Tool, Pie, DeFlaky, 2024-2025); quarantine loses coverage and "quarantine grows... eventually teams run half their test suite." The 2023 multivocal review on test flakiness (ScienceDirect S0164121223002327) treats these as established response trade-offs.

(3) Source quality — primary. John Micco, Google Testing Blog, May 27 2016, first-party description of Google's own tooling. Caveat: WebFetch against the canonical testing.googleblog.com URL returned only title/comments/nav (body not rendered through the fetch pipeline, twice). The verbatim 

**Counter-source:** No contradicting source found. Nearest qualifications: (a) the 3-strike rule is 2016-vintage and superseded by later Google flakiness work (research.google/pubs/de-flake-your-tests..., ICSME 2020; testing.googleblog.com "Where do our flaky tests come from?" 2017), so it should not be presented as current Google policy; (b) canonical testing.googleblog.com URL body did not render via WebFetch — ver

### not refuted (confidence: high)
**Claim:** The single most-wished-for capability (32 of 153 coded wishes) is visualization of flakiness — specifically dashboards showing a test's outcome history over time with environment metadata — ahead of automatic flaky detection (31), automated root-cause debugging (28), and education/best-practice guidance (25); quoted participant rationale: developers lose the cross-run 'bigger picture'.

**Evidence:** VERIFIED AGAINST PRIMARY SOURCE. I downloaded the PDF (arXiv 2203.00483v4, 11 pages) and extracted text with pypdf rather than relying on search snippets. Every figure in the claim matches Table VII ("Wishes for tools or information to better handle test flakiness expressed by our participants") verbatim: Visualization 32 (sub-codes: test result history 14, which tests are flaky 4, program behavior 3); Auto Detection 31 (via static analysis 12); Auto Debug 28 (find root cause 6, find location 5, find failure cause 4); Education 25 (guides/examples/best practices 16, help from colleagues 3, training 2). Remaining categories rank below: Rerun 17, Manual debugging tools 16, Stable infrastructure 16, Logging 13, Management 11, Reproducibility 9. Ordering 32>31>28>25 is exactly as claimed.

DENOMINATOR: Sec. IV-G states 187 suggestions received; 30 discarded (14 uninterpretable, 7 "Don't know", 5 "Nothing", 3 "Anything") plus 4 off-topic; "Table VII summarizes the 153 meaningful, on-topic answers." So 153 is confirmed.

QUOTE: verbatim in the paper, correctly attributed: participants "specifically ask for tools to display the test result history, stating that 'We run tests so often, I often miss the bigger picture' (P 187), wishing for 'A tool to visualize how tests fail and succeed over time' (P 12)."

ENVIRONMENT METADATA: also directly supported — "Such a tool should also include meta information like 'on which device / environment [the test was executed]' (P 292)."

SOURCE QUALITY: peer-reviewed IEEE ICST 2022, n=335 professional developers/testers, two populations (233 glob

**Counter-source:** No contradicting source found. Searched for competing practitioner surveys (Parry/Kapfhammer/McMinn "Surveying the Developer Experience of Flaky Tests", ICSE-SEIP 2022, n=170; Eck et al. 2019, n=121; Parry et al. TOSEM "A Survey of Flaky Tests"; multivocal review JSS 2023). None rank developer tooling wishes in a way that contradicts this ordering; Eck et al. is cited by Gruber & Fraser as corrobo

### not refuted (confidence: high)
**Claim:** Almost 16% of Google's tests exhibit some level of flakiness, and despite heavy investment in fixing flaky tests, the rate at which new flaky tests are introduced roughly equals the fix rate — implying that one-off fix campaigns cannot eliminate flakiness and tooling must treat it as a permanent steady-state condition to manage, not a backlog to burn down.

**Evidence:** SURVIVES REFUTATION. (1) Quote authenticity confirmed verbatim. The live testing.googleblog.com URL returns only title/comments to the fetcher (JS-rendered body), but a full mirror at googblogs.com/flaky-tests-at-google-and-how-we-mitigate-them reproduces all three sentences exactly: "Almost 16% of our tests have some level of flakiness associated with them!", "We have invested a lot of effort in removing flakiness from tests, but overall the insertion rate is about the same as the fix rate...", and "across our entire corpus of tests, we see a continual rate of about 1.5% of all test runs reporting a 'flaky' result." Author John Micco, Google Testing Blog, May 27-28 2016 — primary engineering source, not marketing, no product being sold. (2) Independently corroborated: Google's "State of Continuous Integration Testing @Google" data (16% of 4.2M tests flaky; 84% of pass->fail transitions caused by flakes; only 1.23% of tests ever caught a real breakage) and Jeff Listfield's size-stratified Google numbers (0.5% small / 1.6% medium / 14% large tests flaky over a week) are arithmetically consistent with a ~16% aggregate. (3) No contradicting source found. Searched for later Google data showing flakiness was burned down — none exists; the 2017 follow-up "Where do our flaky tests come from?" reports the size/flakiness correlation and neither revises nor contradicts the figures. Newer independent evidence points the SAME direction, not against: Uber's FlakyGuard (arXiv 2511.14002, 2025) achieves only a 47.6% repair rate on reproducible flaky tests with 51.8% developer acceptance —

**Counter-source:** No refuting source found. Strongest near-counters checked and rejected: (a) the live testing.googleblog.com fetch appearing to lack the text — rejected, it is a JS-rendering artifact, the googblogs.com mirror carries the full body; (b) Google's 2017 "Where do our flaky tests come from?" post — checked as a potential revision, it neither restates nor contradicts the 16%/1.5% figures; (c) Uber/UT-Au

### not refuted (confidence: medium)
**Claim:** Across Google's entire test corpus, about 1.5% of all test runs report a flaky result (a test that both passes and fails against the same code), meaning even a modest per-run flake rate makes fully-green runs rare at scale — an average 1000-test project expects ~15 flaky failures per release-gating cycle, each requiring expensive human investigation.

**Evidence:** QUOTE CHECK — the claim is not an overreach; the inferential arithmetic is Google's own words. Fetched the post body (via the googblogs mirror, since testing.googleblog.com returned only chrome/comments to WebFetch). Verbatim from John Micco, "Flaky Tests at Google and How We Mitigate Them," Google Testing Blog, May 27 2016: (1) "Unfortunately, across our entire corpus of tests, we see a continual rate of about 1.5% of all test runs reporting a 'flaky' result. We define a 'flaky' test result as a test that exhibits both a passing and a failing result with the same code." (2) "In addition to the cost of build monitoring, consider that the average project contains 1000 or so individual tests. If 1.5% of test results are flaky, 15 tests will likely fail, requiring expensive investigation by a build cop or developer." (3) "Almost 16% of our tests have some level of flakiness associated with them!" (4) "about 84% of the transitions we observe from pass to fail involve a flaky test!" So the "1000-test project → ~15 flaky failures, each requiring expensive investigation" is a direct restatement of Google's own sentence, not a derived extrapolation by the claimant; and the "fully-green runs rare at scale" framing is backed by the 84% pass-to-fail statistic in the same post.

SOURCE QUALITY — primary, attributed, first-party engineering data from the org that owns the corpus. Matches the claim's strength.

CONTRADICTION SEARCH — found none. Four searches (direct dispute of the 1.5% figure; Google post-2016 revisions; academic multivocal review; flakiness-distribution studies) surfac

**Counter-source:** Memon et al., "Taming Google-Scale Continuous Testing" (https://research.google.com/pubs/archive/45861.pdf) — reports 2.07% of test *targets* ever passed and failed, a different denominator that qualifies but does not contradict the 1.5%-of-runs figure; plus flakiness-distribution work (e.g. arxiv.org/pdf/2409.10062 SAP HANA, arxiv.org/pdf/2101.09077 Python) showing bimodal/long-tailed flake proba

### not refuted (confidence: medium)
**Claim:** About 84% of pass-to-fail transitions observed by Google's CI system involve a flaky test, and this false-positive flood trains developers to dismiss real failures — the core trust/alert-noise problem a test-intelligence product must solve (a pass-to-fail transition alone is a very weak signal of a real regression).

**Evidence:** VERIFIED VERBATIM. The direct testing.googleblog.com fetch returned only nav/comments (JS-rendered body), but the full-text mirror at googblogs.com/flaky-tests-at-google-and-how-we-mitigate-them/ confirms all quoted strings exactly, attributed to John Micco, Google Testing Blog, May 27-28 2016: "about 84% of the transitions we observe from pass to fail involve a flaky test", "Almost 16% of our tests have some level of flakiness associated with them", "a continual rate of about 1.5% of all test runs reporting a 'flaky' result".

(1) NO OVERREACH ON THE QUOTE. Both halves of the claim are literally in the source. The claim says "84% of pass-to-fail transitions" — it correctly uses the transitions denominator and does NOT commit the common misquote ("84% of all test failures are flaky"), which is the failure mode I specifically searched for. The "trains developers to dismiss real failures" half is a paraphrase of Micco's own sentence: "It is quite common to ignore legitimate failures in flaky tests due to the high number of false-positives."

(2) NO CONTRADICTING SOURCE FOUND. Four searches for critiques, misquote debunks, or revised Google figures turned up nothing disputing the number. Corroborating instead: Memon, Gao, Nguyen, Dhanda, Nickell, Siemborski, Micco, "Taming Google-Scale Continuous Testing," ICSE-SEIP 2017 (peer-reviewed follow-up by the same CI team) reports 2.07% of test targets both PASSED and FAILED at least once, and only 1.23% actually found a real breakage after filtering flakes — i.e. ~41% of alternating targets were flaky at target level. That is compat

**Counter-source:** No refuting source found. Nearest qualifiers: Memon et al., "Taming Google-Scale Continuous Testing," ICSE-SEIP 2017 (research.google.com/pubs/archive/45861.pdf) — 2.07% of targets pass-and-fail, 1.23% find real breakage, i.e. ~41% of alternating targets flaky at target level vs 84% at transition level (different denominator, not a contradiction); and testing.googleblog.com/2017/04/where-do-our-fl

### not refuted (confidence: medium)
**Claim:** About 84% of pass-to-fail transitions observed by Google's CI system involve a flaky test, and this false-positive flood trains developers to dismiss real failures — the core trust/alert-noise problem a test-intelligence product must solve (a pass-to-fail transition alone is a very weak signal of a real regression).

**Evidence:** Primary source verified verbatim. Google Testing Blog, "Flaky Tests at Google and How We Mitigate Them," John Micco, 2016-05-27, states: "About 84% of the transitions we observe from pass to fail involve a flaky test!" plus "Across our entire corpus of tests, we see a continual rate of about 1.5% of all test runs reporting a 'flaky' result" and "Almost 16% of our tests have some level of flakiness associated with them!" The trust-erosion half of the claim is also the source's own language, not the claimer's gloss: "It is human nature to ignore alarms when there is a history of false signals coming from a system" and "It is quite common to ignore legitimate failures in flaky tests due to the high number of false-positives." (testing.googleblog.com served only its comments section; body retrieved via the googblogs.com mirror.)

INDEPENDENT CORROBORATION (not a lone blog assertion): the same 84% figure appears in peer-reviewed Google work — Memon et al., "Taming Google-Scale Continuous Testing," ICSE-SEIP 2017 (dl.acm.org/doi/10.1109/ICSE-SEIP.2017.16) — and in Google's research deck "The State of Continuous Integration Testing @Google" (research.google.com/pubs/archive/45880.pdf), reported as "84% of transitions from Pass -> Fail are from 'flaky' tests" over a one-month sample.

REFUTATION ATTEMPTS THAT FAILED TO KILL IT: (a) No credible source found disputing the figure; searches for critiques/misinterpretation returned only sources repeating it as fact. (b) Not marketing — non-commercial engineering blog by the operator of the CI system, plus an academic venue.

QUALIFICATI

**Counter-source:** Atlassian engineering: flaky tests caused ~21% of master-branch build failures (~150,000 dev hours/yr). GitLab handbook (handbook.gitlab.com/handbook/engineering/infrastructure/engineering-productivity/flaky-tests-management-and-processes): flaky tests contribute >=30% of master pipeline failures per month. Both are an order of magnitude below 84%, showing the figure is Google-monorepo-specific an

### not refuted (confidence: high)
**Claim:** Almost 16% of Google's tests exhibit some level of flakiness, and despite heavy investment in fixing flaky tests, the rate at which new flaky tests are introduced roughly equals the fix rate — implying that one-off fix campaigns cannot eliminate flakiness and tooling must treat it as a permanent steady-state condition to manage, not a backlog to burn down.

**Evidence:** CLAIM SURVIVES ADVERSARIAL REVIEW. Verdict: not refuted, with two qualifications to carry forward.

(1) QUOTE FIDELITY — CONFIRMED VERBATIM. Direct fetch of testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html returned only comment-section text (extraction artifact — it surfaced a Wayne Roseberry comment but not the body), so I verified via the googblogs.com mirror across two independent extractions. Both reproduce: "Almost 16% of our tests have some level of flakiness associated with them"; "the insertion rate is about the same as the fix rate"; plus adjacent figures "1.5% of all test runs reporting a 'flaky' result" and "about 84% of the transitions we observe from pass to fail involve a flaky test." Author John Micco, Google Testing Blog, May 27/28 2016. A third independent WebSearch extraction returned the same sentences. web.archive.org was unfetchable in this environment, so archival confirmation is absent — but three independent extractions agreeing is sufficient.

(2) NO OVERREACH. The interpretive clause ("one-off fix campaigns cannot eliminate flakiness; must be managed as steady state") is not an editorial leap — it restates Micco's own words: "we are STUCK WITH a certain rate of tests that provide value, but occasionally produce a flaky result." The claim's "despite heavy investment" maps to "We have invested a lot of effort in removing flakiness from tests." Supported.

(3) NO CONTRADICTING SOURCE FOUND. Searched explicitly for disputes/misquote criticism — none surfaced.

(4) INDEPENDENT CORROBORATION OF THE ACTIONABLE CONCLUSION (mitigates the

**Counter-source:** Google's own follow-up qualifies (does not contradict) the headline number: Jeff Listfield, "Where do our flaky tests come from?", Google Testing Blog, 2017-04-17 (https://testing.googleblog.com/2017/04/where-do-our-flaky-tests-come-from.html) — ~63k of 4.2M tests flaky over a one-week window (<2%), 0.5%/1.6%/14% by small/medium/large test size, showing the 16% figure is window- and definition-dep

### not refuted (confidence: medium)
**Claim:** About 84% of pass-to-fail transitions observed by Google's CI system involve a flaky test, and this false-positive flood trains developers to dismiss real failures — the core trust/alert-noise problem a test-intelligence product must solve (a pass-to-fail transition alone is a very weak signal of a real regression).

**Evidence:** VERIFIED, with two caveats that qualify but do not refute.

(1) Quote fidelity — confirmed verbatim against the primary source. Google Testing Blog, "Flaky Tests at Google and How We Mitigate Them," John Micco, published 27 May 2016 (the canonical URL renders only comments to the fetcher; the full text was confirmed via the googblogs.com mirror). Exact text: "What we find in practice is that about 84% of the transitions we observe from pass to fail involve a flaky test!" and "It is quite common to ignore legitimate failures in flaky tests due to the high number of false-positives." The claim's two assertions (84% figure; false-positive flood → developers dismiss real failures) are stated by the source, not inferred. Same post also gives 1.5% of test *runs* report flaky results and ~16% of tests show some flakiness — the claim correctly does not conflate these.

(2) Independent corroboration of the same measurement. John Micco, "The State of Continuous Integration Testing @Google," ICST 2017 keynote (research.google.com/pubs/archive/45880.pdf), and Memon et al., "Taming Google-Scale Continuous Testing," ICSE-SEIP 2017 (archive/45861.pdf) report the same 84% from a ~1-month sample of Google's post-submit CI, alongside the complementary figure that only ~1.23% of transitions found an actual developer-introduced breakage. Peer-reviewed venue + first-party operator data = source quality matches claim strength. This is engineering-blog/academic material, not vendor marketing.

(3) No contradicting source found. Targeted searches for critiques of the 84% figure, methodological-cir

**Counter-source:** https://arxiv.org/abs/2302.10594 (Cordy et al., Chromium CI) — qualifies rather than refutes: flakiness prediction at 99.2% precision still misses ~76.2% of regression faults, so "P→F is weak signal" must not become "suppress flaky-test failures." Scope qualifiers: https://slack.engineering/handling-flaky-tests-at-scale-auto-detection-suppression/ (~56.8% of CI failures flaky) shows 84% is Google-

### not refuted (confidence: medium)
**Claim:** 75% of flaky tests (184 of 245 studied) are already flaky at the commit that introduces the test, so running flaky-test detectors on newly added tests catches the large majority of flakiness at the moment it is cheapest to fix — a test-intelligence platform should therefore flag flakiness at test-introduction time (PR/first-run), not only after longitudinal history accumulates.

**Evidence:** VERIFICATION OF THE FACTUAL CORE — HOLDS.

1. Quote and numbers are exact and correctly attributed. The source is Lam, Winter, Wei, Xie, Marinov, Bell, "A Large-Scale Longitudinal Study of Flaky Tests," OOPSLA 2020 / Proc. ACM Program. Lang. 4, OOPSLA (DOI 10.1145/3428270) — a top-tier peer-reviewed venue, primary empirical source, from the Illinois MIR group (Marinov) plus Jonathan Bell. I confirmed the "75% of flaky tests (184 out of 245) are flaky when added, indicating substantial potential value for developers to run detectors specifically on newly added tests" sentence against both the authors' PDF (mir.cs.illinois.edu/marinov/publications/LamETAL20LongitudinalFlakyTests.pdf) and the SPLASH/OOPSLA 2020 abstract page. Methodology confirmed: two detectors applied to 55 Java projects, yielding 245 flaky tests that could be compiled and run at the commit where each test was added. Source quality clearly matches claim strength; this is not marketing, not a vendor benchmark, not forum speculation.

2. The prescriptive half of the claim is the authors' OWN stated implication, not an invented extrapolation. "Substantial potential value for developers to run detectors specifically on newly added tests" is verbatim. The claim's hedge — "not only after longitudinal history accumulates" — is also faithful to the paper, which explicitly states that running detectors solely on newly added tests would MISS 25%, rises to 85% when newly-added-or-directly-modified tests are covered, and that the residual 15% become flaky from unrelated changes and are only findable by always running de

**Counter-source:** No source contradicts the finding. The strongest qualifying evidence is detector-coverage scope: iDFlakies detects only order-dependent flakiness and NonDex only implementation-dependent flakiness from underdetermined Java-stdlib APIs (https://taoxie.cs.illinois.edu/publications/icst19-idflakies.pdf), so the 245-test sample excludes async-wait/concurrency/network/timing flakiness — the categories 

### not refuted (confidence: medium)
**Claim:** Extending detection to directly modified tests raises coverage to 85%, but the remaining 15% of flaky tests become flaky due to changes made elsewhere (code under test, other tests, dependencies) and can only be caught by continuously applying detection to all tests — meaning new/modified-test-only strategies (as used at Mozilla and Netflix) have a measurable, quantified blind spot.

**Evidence:** VERIFICATION ATTEMPTS (adversarial):

1. Quote fidelity — CONFIRMED, not an overreach. Direct PDF fetch failed (compressed content streams), but two independent retrievals of the paper's content reproduced the finding verbatim: "The percentage of flaky tests that can be detected does increase to 85% when detectors are run on newly added or directly modified tests. The remaining 15% of flaky tests become flaky due to other changes and can be detected only when detectors are always applied to all tests." The baseline the 85% extends is also confirmed: 184/245 (75%) of flaky tests were already flaky when added. The claim's paraphrase ("raises coverage to 85%") matches; "can only be caught by continuously applying detection to all tests" is near-verbatim.

2. The Mozilla/Netflix attribution — CONFIRMED as the PAPER'S OWN framing, not an inference bolted on by the claim: "Some software organizations, e.g., Mozilla and Netflix, run some tools—detectors—to detect flaky tests as soon as possible. However, these organizations typically run a detector solely on newly added or directly modified tests, i.e., not on unmodified tests or when other changes occur (including changes to the test suite, the code under test, and library dependencies)." So the "blind spot" conclusion is the paper's stated motivation, not the claim's editorializing.

3. Source strength — MATCHES claim strength. Lam, Winter, Wei, Xie, Marinov, Bell; Proc. ACM Program. Lang. Vol 4, OOPSLA, Nov 2020 (DOI 10.1145/3428270); peer-reviewed top-tier venue, vendor-neutral academic, not a press release or benchmark.

4. C

**Counter-source:** No refuting source found. Nearest qualifiers (which amplify rather than contradict the blind spot): "Do Test and Environmental Complexity Increase Flakiness? An Empirical Study of SAP HANA" (arXiv 2409.10062) and "A Systematic Evaluation of Environmental Flakiness in JavaScript Tests" (arXiv 2602.19098), both documenting flakiness arising with no test-code change. The only real weakness is scope: 

### not refuted (confidence: high)
**Claim:** Extending detection to directly modified tests raises coverage to 85%, but the remaining 15% of flaky tests become flaky due to changes made elsewhere (code under test, other tests, dependencies) and can only be caught by continuously applying detection to all tests — meaning new/modified-test-only strategies (as used at Mozilla and Netflix) have a measurable, quantified blind spot.

**Evidence:** VERIFIED AGAINST PRIMARY SOURCE. I downloaded the PDF (Lam, Winter, Wei, Xie, Marinov, Bell — "A Large-Scale Longitudinal Study of Flaky Tests," Proc. ACM Program. Lang. Vol 4, OOPSLA, Article 202, Nov 2020) and extracted its text rather than relying on search summaries.

1. QUOTE IS VERBATIM AND CORRECTLY READ. Abstract, lines 25-28 of extracted text: "running detectors solely on newly added tests would still miss detecting 25% of flaky tests. The percentage of flaky tests that can be detected does increase to 85% when detectors are run on newly added or directly modified tests. The remaining 15% of flaky tests become flaky due to other changes and can be detected only when detectors are always applied to all tests." No paraphrase drift.

2. THE UNDERLYING ARITHMETIC CHECKS OUT IN THE BODY (Section 6, lines 972-979): "we find that 75% of flaky tests in our study are detected as flaky on their TIC [test introduction commit]... we further find that 24 of 61 (39%) tests not flaky on their TIC become flaky in a commit that changes the code of the class containing the flaky test. Combining these 24 tests and the 184 tests that are flaky when they are added, we find that 208 of 245 (85%) tests can be detected by running flaky-test detectors on newly added or existing, but directly modified tests. However, it still leaves 15% of flaky tests that become flaky due to changes not being directly in the test class itself but rather elsewhere in the test suite, code under test, or library dependencies." 184+24=208; 208/245=84.9%. The claim's characterization of the 15% blind spot ("cod

**Counter-source:** No contradicting source found. Nearest adjacent challenge: Haben et al., "The Importance of Accounting for Execution Failures when Predicting Test Flakiness," ASE 2024 (https://dl.acm.org/doi/10.1145/3691620.3695261) — disputes flaky-vs-fault-revealing failure classification, not the introduction-timing split; does not contradict this claim. Parry et al., "Systemic Flakiness," EASE 2025 (https://a

### REFUTED (confidence: high)
**Claim:** 75% of flaky tests (184 of 245 studied) are already flaky at the commit that introduces the test, so running flaky-test detectors on newly added tests catches the large majority of flakiness at the moment it is cheapest to fix — a test-intelligence platform should therefore flag flakiness at test-introduction time (PR/first-run), not only after longitudinal history accumulates.

**Evidence:** The NUMBER is real; the ACTIONABLE CONCLUSION is refuted. I extracted the primary PDF (29pp, Lam/Winter/Wei/Xie/Marinov/Bell, OOPSLA 2020, PACMPL 4:202) and confirmed the quote verbatim at line 742: "Of the 245 flaky tests, 184 tests (75%) are detected as flaky tests on their TIC." So clause 1 is accurate. Four independent grounds refute the inference drawn from it:

(1) DENOMINATOR: the paper detected 684 potentially flaky tests, confirmed 432, and could only compile/run 245 at the test-introducing commit — "we are able to run only 245 of the total 432 (684-252) confirmed flaky tests on their TIC." So 184/684 = 27% of all detected flaky tests, not "the large majority of flakiness." The 252 excluded UD tests are explicitly those whose flakiness "was not possible to ever reproduce ... outside of the full test suite" — i.e. precisely the environment-sensitive kind that dominates real CI. That is survivorship bias toward reproducible-in-isolation tests.

(2) CATEGORY BIAS DRIVES THE 75%: of 432 categorized tests, 190 ID + 155 OD-Vic + 23 OD-Brit = 85%, while non-deterministic (ND) is only 64 (15%). The paper's own causal explanation (line 874): "OD Brits, NDs, and IDs, which are all tests that, when each is run by itself, can result in a flaky-test failure, do have a high likelihood to be detected in their TICs." The high rate is an artifact of a population dominated by intrinsic, self-contained, deterministically-reproducible flakiness (e.g. HashSet iteration order). Luo et al. FSE 2014 found real-world flakiness is 45% async-wait and 20% concurrency — categories structurally

**Counter-source:** https://mill-build.org/blog/4-flaky-tests.html (Li Haoyi, Databricks/Dropbox CI); Luo et al. FSE 2014 https://mir.cs.illinois.edu/lamyaa/publications/fse14.pdf; plus the primary paper's own Section 5 / RQ2 text

### not refuted (confidence: high)
**Claim:** Flaky-test prevalence at scale is high enough to be a first-class product problem: Google reported roughly 16% of its tests were flaky (with about 1 in 7 tests written by engineers occasionally failing), and GitHub reported in 2020 that about 9% of commits (one in eleven) had at least one red build caused by a flaky test — so a test-intelligence platform must treat flakiness as a routine, high-vol

**Evidence:** Both numbers trace to verifiable PRIMARY sources, not just the arXiv survey (2212.00908), which is a peer-reviewed multivocal review (published in Information & Software Technology / ScienceDirect) merely relaying them.

(1) GOOGLE — verified verbatim at testing.googleblog.com "Flaky Tests at Google and How We Mitigate Them" (John Micco, May 27, 2016), fetched via the googblogs mirror: "Almost 16% of our tests have some level of flakiness associated with them!" and "it means that more than 1 in 7 of the tests written by our world-class engineers occasionally fail in a way not caused by changes". Same post: "about 1.5% of all test runs reporting a 'flaky' result" and "about 84% of the transitions we observe from pass to fail involve a flaky test". The survey's paraphrase is faithful (if anything Google said "more than 1 in 7").

(2) GITHUB — verified verbatim at github.blog/2020-12-16-reducing-flaky-builds-by-18x (Dec 16, 2020): "Earlier this year in our monolith, 1 in 11 commits had at least one red build caused by a flaky test, or about 9 percent of commits." Exactly as claimed, correctly dated.

Adversarial checks that did NOT refute it: I searched for contradicting/qualifying sources and found no credible source disputing either figure. Independent corroboration of the "routine, not edge case" conclusion exists (Google 2017 "Where do our flaky tests come from?"; the Chromium CI study arXiv 2302.10594; ~13% of GitHub-project build failures attributed to flaky tests).

Three QUALIFICATIONS the roadmap should carry, none of which falsify the claim:
(a) SEMANTICS — 16% is a 

**Counter-source:** https://github.blog/2020-12-16-reducing-flaky-builds-by-18x/ (same post reports the 9% dropped to <0.5% / 1-in-200 after mitigation — partial counter to the "routine, inevitable" framing); https://arxiv.org/abs/2101.09077 (open-source flaky rates ~0.5–1%, far below 16%, limiting generalization beyond hyperscale monorepos)

### REFUTED (confidence: high)
**Claim:** The dominant cost of flakiness is erosion of trust in the test signal itself — practitioners report that once confidence is lost, the test suite loses its value entirely, which is why a reporting tool's core job is preserving signal trustworthiness (clearly separating flaky from real failures) rather than just displaying results.

**Evidence:** REFUTED on three independent grounds: source misattribution, contradiction by the cited paper itself, and contradiction by primary empirical work.

1) SOURCE MISATTRIBUTION — the "primary source" is a secondary citation of a vendor blog. arXiv 2212.00908 is Rasheed, Tahir, Dietrich, Hashemi & Zhang, "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review" (Dec 2022, JSS 2023) — a literature review over 651 articles (560 academic + 91 grey-literature posts). Fetching the HTML (ar5iv) confirms the supporting quote sits in Section 4.5.2, the GREY-LITERATURE impact analysis, attributed to Spotify Engineering (Table 9, ref [1]) — i.e. the 2019 Spotify blog post "Test Flakiness – Methods for identifying and dealing with flaky tests." Web search independently confirms the exact wording originates from that Spotify post. So the claim is built on one engineering-blog rhetorical assertion, quoted verbatim inside a review, not on primary empirical measurement. Calling it "primary" is false, and "practitioners report" (plural, survey-like) misrepresents a single blog author's opinion.

2) THE CITED PAPER ITSELF DOES NOT SUPPORT "DOMINANT." The review does NOT rank trust erosion as the dominant cost. It organizes impacts into six unranked categories (academic: product quality 10, testing techniques 14, debugging/maintenance 6; grey: product/codebase 5, developers 6, delivery 8), with productivity loss, time waste and deployment slowdown cited alongside lost trust. Direct answer from the paper's impact section: it "does not claim trust erosion is the dominant cost… 

**Counter-source:** Habchi et al., "A Qualitative Study on the Sources, Impacts, and Mitigation Strategies of Flaky Tests," ICST 2022 (https://arxiv.org/abs/2112.04919) — ranks wasted developer time, not trust loss, as the most severe consequence; plus the cited review's own Section 4.5.2 / Tables 8-9 (https://ar5iv.labs.arxiv.org/html/2212.00908), which attributes the quote to the Spotify Engineering blog (https://e

### REFUTED (confidence: high)
**Claim:** The dominant cost of flakiness is erosion of trust in the test signal itself — practitioners report that once confidence is lost, the test suite loses its value entirely, which is why a reporting tool's core job is preserving signal trustworthiness (clearly separating flaky from real failures) rather than just displaying results.

**Evidence:** REFUTED on four independent grounds.

1) SOURCE MISATTRIBUTION — the cited "primary" source is not primary. arXiv 2212.00908 (Rasheed, Tahir, Dietrich, Hashemi, Zhang, "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review", Dec 2022) is a literature review, not an empirical study. The supporting quote appears in §4.5.2 as a verbatim block quotation attributed to **Spotify Engineering** and catalogued in the paper's **grey-literature** table (Table 9), i.e. a company engineering blog post ("Test Flakiness — Methods for identifying and dealing with flaky tests", Spotify Labs, Nov 2019). The review reports it as one voice among many documented impacts and supplies **no frequency counts**; it never endorses confidence loss as the dominant cost. Presenting a 2019 vendor blog aphorism as an arXiv primary finding is exactly the source-strength mismatch the checklist targets.

2) "DOMINANT COST" CONTRADICTED BY THE PRACTITIONER DATA. Habchi et al., "A Qualitative Study on the Sources, Impacts, and Mitigation Strategies of Flaky Tests" (arXiv 2112.04919): impact ranking across practitioners is (1) wasted developer time 10/14 = 71%, (2) CI disruption 7/14 = 50%, (3) affects testing practices 6/14 = 43%, (4) undermines system reliability/distrust 5/14 = 36%, (5) disguises bugs 2/14 = 14%. Trust erosion ranks BELOW time/CI cost. Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers…" (arXiv 2203.00483, n=335 professionals) independently reproduces this: highest-rated consequence is "wasting developer time" (mean 2.44/4), ahead of rerunning-without-

**Counter-source:** https://arxiv.org/abs/2203.00483 (Gruber & Fraser, n=335: top consequence is wasted developer time, mean 2.44/4; trust loss is asymmetric, p<0.001, and developers keep using suites); https://arxiv.org/abs/2112.04919 (Habchi et al.: wasted time 71% and CI disruption 50% outrank trust-related impacts 43%/36%); https://testing.googleblog.com/2017/04/where-do-our-flaky-tests-come-from.html and Google 

### REFUTED (confidence: high)
**Claim:** Unmanaged flakiness compounds into organizational failure: the review cites organizations that reached 50%+ flaky tests, at which point developers stopped writing tests and stopped looking at results — evidence that flaky-test management (detection + quarantine lifecycle + burn-down) is a productivity feature, not a nice-to-have.

**Evidence:** MISATTRIBUTED VENDOR MARKETING, NOT A PRIMARY FINDING. (1) Provenance: arXiv 2212.00908 (Rasheed, Tahir, Dietrich, Hashemi, Zhang, "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review", Dec 2022) is a literature review of 651 articles (560 academic + 91 GREY literature). The ar5iv full text confirms the quote appears there ONLY as a quoted grey-literature datapoint, attributed in its Table 9 grey-lit set to a Datadog product manager — i.e. the review reports the statement, it does not measure or validate it. Labelling arXiv 2212.00908 as the "primary" source for this claim is a provenance error. (2) The real origin is a product-marketing blog: "We Have A Flaky Test Problem" by Bryan Lee, Product Manager at Undefined Labs (maker of Scope, later acquired by Datadog), published 9 Dec 2019 on Medium/dev.to. It closes with the CTA "Your journey to better engineering through better testing starts with Scope" — i.e. a vendor selling flaky-test tooling asserting that flaky-test tooling is essential. Fails checklist item 5. (3) Anecdotal with zero method: "We've talked to some organizations" — no N, no sampling, no definition of "50%+ flaky tests in their codebase" (50% of tests? of runs? of failures?), no measurement of the alleged behavioral outcome. Fails checklist item 3: an extraordinary "half the suite is flaky, developers abandoned testing" claim is carried by a second-hand sales anecdote. (4) Magnitude contradicted by measured industrial data: Google reports ~1.5% of test EXECUTIONS flaky (16% of tests show some flakiness over their lifetime, per the

**Counter-source:** Origin of quote: Bryan Lee (PM, Undefined Labs/Scope, now Datadog), "We Have A Flaky Test Problem", 2019-12-09, https://dev.to/kickingthetv/we-have-a-flaky-test-problem-11ol (vendor blog with Scope CTA). Attribution inside the cited paper: https://ar5iv.labs.arxiv.org/html/2212.00908 Table 9 grey literature. Contradicting measured rates: Google Testing Blog "Flaky Tests at Google and How We Mitiga

### REFUTED (confidence: high)
**Claim:** Unmanaged flakiness compounds into organizational failure: the review cites organizations that reached 50%+ flaky tests, at which point developers stopped writing tests and stopped looking at results — evidence that flaky-test management (detection + quarantine lifecycle + burn-down) is a productivity feature, not a nice-to-have.

**Evidence:** MISLABELED SOURCE + MARKETING ORIGIN. (1) The quote IS in arXiv 2212.00908 (Rasheed/Tahir/Dietrich, "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review", JSS 206:111837, 2023) — I extracted the PDF text and confirmed it verbatim. But the paper does NOT assert it; it quotes it as grey literature: "...as this is the case with some organisations: 'We've talked to some organizations that reached 50%+ flaky tests...' (Product Manager at Datadog, [G3])". [G3] is a grey-lit entry. The claim labels arXiv 2212.00908 "(primary)" — it is not primary for this fact; it is a secondary report of a vendor blog. Multivocal reviews include grey literature by design WITHOUT validating its empirical basis. (2) The actual origin is "We Have A Flaky Test Problem" by Bryan Lee of Undefined Labs (dev.to/kickingthetv/we-have-a-flaky-test-problem-11ol, 9 Dec 2019; also on Medium/@scopedev). Undefined Labs sold CI test observability and was acquired by Datadog — the speaker's company sells the flaky-test-management product the claim concludes is essential. I fetched the post: it cites specific Google research for other stats (84% of test transitions from flaky tests, 16% of tests have some flakiness) but supplies ZERO data, no named organizations, and no definition of how "50%+ flaky" was measured for this anecdote. Textbook self-serving vendor anecdote. (3) The 50%+ figure is an unreplicated extreme outlier vs. every published measurement: Google ~16% of tests exhibit some flakiness (~1.5% flaky), Microsoft ~26% of pass/fail transitions, and empirical studies (FlakeFlagger,

**Counter-source:** https://dev.to/kickingthetv/we-have-a-flaky-test-problem-11ol (Bryan Lee, Undefined Labs, 9 Dec 2019 — the unsourced vendor-blog origin of the 50%+ quote, cited as grey-lit [G3] in arXiv 2212.00908); contrast measured prevalence in Google/Microsoft data (~16% / ~26%) and arXiv 2203.00483 (Parry et al. flakiness survey)

### REFUTED (confidence: medium)
**Claim:** Flaky-test prevalence at scale is high enough to be a first-class product problem: Google reported roughly 16% of its tests were flaky (with about 1 in 7 tests written by engineers occasionally failing), and GitHub reported in 2020 that about 9% of commits (one in eleven) had at least one red build caused by a flaky test — so a test-intelligence platform must treat flakiness as a routine, high-vol

**Evidence:** The two numbers exist verbatim, but the claim's construction of them is materially misleading on three counts, two of which are self-contradicted by the claim's own cited primary sources.

1) THE GITHUB HALF IS A PRE-FIX BASELINE THAT THE SAME ARTICLE REPORTS WAS ELIMINATED. I fetched the cited source (https://github.blog/2020-12-16-reducing-flaky-builds-by-18x/). It says "1 in 11 commits had at least one red build caused by a flaky test, or about 9 percent of commits" — but that is explicitly the BEFORE state ("earlier in 2020"), and the same post continues: "the percentage of commits with flaky builds dropped to less than half a percent, or 1 in 200 commits" about six weeks after they shipped their flaky-test management system, calling it "an 18x improvement and the lowest rate of flaky builds since we began tracking flaky tests in 2016." The article's conclusion is that 9% was fixable down to 0.5%; the claim cites the 9% as if it characterized GitHub's steady state. The title of the source is literally "Reducing flaky builds by 18x."

2) THE "16%" AND "1 IN 7" ARE THE SAME NUMBER RESTATED, NOT TWO DATA POINTS, AND ARE 10-YEAR-OLD GREY LITERATURE. Traced to the actual primary source (John Micco, Google Testing Blog, MAY 2016): "Almost 16% of our tests have some level of flakiness associated with them!" and, in the very next sentence, "it means that more than 1 in 7 of the tests written by our world-class engineers occasionally fail." 1/7 = 14.3% — it is the same measurement paraphrased, so the claim's "16% ... with about 1 in 7 ..." presents no independent corroboration. 

**Counter-source:** https://github.blog/2020-12-16-reducing-flaky-builds-by-18x/ (same source the claim cites — reports the 9% dropped to <0.5%, 1 in 200 commits, an 18x improvement, six weeks later); https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html (primary source, May 2016; "Almost 16% of our tests have some level of flakiness associated with them"; companion figure "about 1.5% of all te

### REFUTED (confidence: high)
**Claim:** Unmanaged flakiness compounds into organizational failure: the review cites organizations that reached 50%+ flaky tests, at which point developers stopped writing tests and stopped looking at results — evidence that flaky-test management (detection + quarantine lifecycle + burn-down) is a productivity feature, not a nice-to-have.

**Evidence:** CITATION LAUNDERING — the quote is not a finding of the arXiv review. Verified via ar5iv (https://ar5iv.labs.arxiv.org/html/2212.00908): the sentence appears in Rasheed et al., "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review," Section 4.5.2 "Impact noted in grey literature" (Table 9, ref [3]) — a section whose explicit purpose is to CATALOGUE practitioner claims separately from academic evidence, not to endorse them. The claim says "the review cites organizations that reached 50%+ flaky tests"; the review cites a blog post that claims to have talked to unnamed organizations. That is a second-hand, unverified anecdote re-presented as an academic citation.

THE UNDERLYING SOURCE IS VENDOR MARKETING. Verified via https://dev.to/kickingthetv/we-have-a-flaky-test-problem-11ol (Dec 2019, mirrored at medium.com/scopedev): author is Bryan Lee, Product Manager at Undefined Labs, and the post is a product ad for Scope — it closes with "Scope gives engineering teams production-level visibility" and the CTA "Your journey to better engineering through better testing starts with Scope." Undefined Labs was acquired by Datadog (Scope became Datadog Test Optimization) — i.e. the source is one of the exact competitor vendors named in the research brief, selling the precise remedy ("flaky-test management ... detection + quarantine lifecycle") that the claim concludes is essential. Self-serving marketing anecdote in support of buying/building the thing the author sells.

NO DATA BEHIND THE 50% FIGURE. The post gives no named organization, no count of organizations

**Counter-source:** Original source is vendor marketing: https://dev.to/kickingthetv/we-have-a-flaky-test-problem-11ol (Bryan Lee, PM at Undefined Labs/Scope→Datadog Test Optimization, Dec 2019, with product CTA). Review context showing it is grey literature only, §4.5.2/Table 9 ref [3]: https://ar5iv.labs.arxiv.org/html/2212.00908. Contradicting measured prevalence: Google (~16% of 4.2M tests with any flakiness; 0.5

### REFUTED (confidence: medium)
**Claim:** 75% of flaky tests (184 of 245 studied) are already flaky at the commit that introduces the test, so running flaky-test detectors on newly added tests catches the large majority of flakiness at the moment it is cheapest to fix — a test-intelligence platform should therefore flag flakiness at test-introduction time (PR/first-run), not only after longitudinal history accumulates.

**Evidence:** VERDICT: the raw statistic is verbatim-accurate, but the claim's actionable prescription is an overreach that the source itself contradicts, and the 245-test population is selected in a way that does not transfer to a CI-ingesting test-intelligence platform.

1) STATISTIC IS REAL (not disputed). I extracted the full text of the PDF. Abstract: "We apply two state-of-the-art detectors to 55 Java projects, identifying a total of 245 flaky tests that can be compiled and run in the code version where each test was added. We find that 75% of flaky tests (184 out of 245) are flaky when added." Peer-reviewed, Proc. ACM Program. Lang. Vol 4, OOPSLA, Article 202, Nov 2020 (Lam, Winter, Wei, Xie, Marinov, Bell). Sec. 10 repeats it. So checklist items 3 and 5 pass.

2) THE SOURCE EXPLICITLY CONTRADICTS "not only after longitudinal history accumulates." Sec. 8: "we suggest that developers run flaky-test detectors not only when tests are added or modified, but also with a minimum regular frequency (e.g., monthly)." Abstract: "running detectors solely on newly added tests would still miss detecting 25% of flaky tests." Sec. 5: "in some projects, all flaky tests were flaky on their TIC, and in others, none were" — wildfly/wildfly had 23 of 39 NOT flaky at introduction, and "7 projects with at least one test not detected actually have none of their tests detected in their TIC." The per-project rate ranges 0%-100%, so 75% is not a rate any single team can plan against.

3) SELECTION BIAS INFLATES THE 75% AND MAKES IT NON-TRANSFERABLE. Of 684 detected flaky tests only 432 were confirmed and o

**Counter-source:** Primary source's own Sec. 8 Discussion + Threats to Validity and Table 2 category breakdown (https://mir.cs.illinois.edu/marinov/publications/LamETAL20LongitudinalFlakyTests.pdf); Gruber et al., An Empirical Study of Flaky Tests in Python, ICST 2021 (https://arxiv.org/abs/2101.09077) on ~170 reruns for 95% confidence; Systemic Flakiness: An Empirical Analysis of Co-Occurring Flaky Test Failures, 2

### REFUTED (confidence: medium)
**Claim:** Extending detection to directly modified tests raises coverage to 85%, but the remaining 15% of flaky tests become flaky due to changes made elsewhere (code under test, other tests, dependencies) and can only be caught by continuously applying detection to all tests — meaning new/modified-test-only strategies (as used at Mozilla and Netflix) have a measurable, quantified blind spot.

**Evidence:** VERDICT: Quote is verbatim and correctly attributed, but the claim strips every scope qualifier that makes the 85/15 numbers meaningful. Refuted as an over-generalization, not as a fabrication.

WHAT CHECKS OUT (verified against the actual PDF, extracted via pypdf, 29pp):
- The supporting quote is verbatim from the abstract (p.202:1, lines 25-28): "The percentage of flaky tests that can be detected does increase to 85% when detectors are run on newly added or directly modified tests. The remaining 15% of flaky tests become flaky due to other changes and can be detected only when detectors are always applied to all tests."
- The Mozilla/Netflix attribution is the paper's own framing, not the claim's invention. Abstract: "Some software organizations, e.g., Mozilla and Netflix, run some tools—detectors—to detect flaky tests as soon as possible... these organizations typically run a detector solely on newly added or directly modified tests."
- Body derivation is explicit (Sec. 6): 184 flaky-when-added + 24 of 61 that became flaky via a change to the test class = "208 of 245 (85%)". The 15% "become flaky due to changes not being directly in the test class itself but rather elsewhere in the test suite, code under test, or library dependencies" — matching the claim's parenthetical exactly.
- Source quality is strong: OOPSLA/PACMPL 2020 (Lam, Winter, Wei, Xie, Marinov, Bell), top-tier peer-reviewed primary source. Not marketing, not a vendor benchmark. Luo et al. FSE 2014 independently found 78% flaky-when-added, corroborating rather than contradicting. I found no source disputing 

**Counter-source:** Primary source self-refutation (Lam et al., OOPSLA 2020, https://mir.cs.illinois.edu/marinov/publications/LamETAL20LongitudinalFlakyTests.pdf): Sec. 5 "We did not attempt to run unknown-dependent tests on their TIC" (252 of 684 tests, 37%, excluded); Sec. 8.1.3 external validity "our results may be biased the same way that our previous results may be biased (e.g., finding OD tests more frequent th

### not refuted (confidence: medium)
**Claim:** Flaky-test prevalence at scale is high enough to be a first-class product problem: Google reported roughly 16% of its tests were flaky (with about 1 in 7 tests written by engineers occasionally failing), and GitHub reported in 2020 that about 9% of commits (one in eleven) had at least one red build caused by a flaky test — so a test-intelligence platform must treat flakiness as a routine, high-vol

**Evidence:** ATTEMPTED REFUTATION FAILED — both figures verify verbatim against PRIMARY sources, not just the arXiv secondary.

(1) Google figure — traced past arXiv 2212.00908 to the original: Google Testing Blog, "Flaky Tests at Google and How We Mitigate Them," John Micco, May 27 2016. Exact text: "Almost 16% of our tests have some level of flakiness associated with them!" and "more than 1 in 7 of the tests written by our world-class engineers occasionally fail in a way not caused by changes to the code or tests." Also in-post: "about 1.5% of all test runs reporting a 'flaky' result" and "about 84% of the transitions we observe from pass to fail involve a flaky test." The arXiv paraphrase is faithful. NOTE: 16% and "1 in 7" are the SAME measurement restated by Google itself (16% ≈ 1/6.25), not two independent data points — the claim presents them as one figure plus its restatement, which is acceptable, but a roadmap must not count them as corroborating each other.

(2) GitHub figure — traced to primary: github.blog "Reducing flaky builds by 18x," Dec 16 2020. Exact text: "Earlier this year in our monolith, 1 in 11 commits had at least one red build caused by a flaky test, or about 9 percent of commits." Verbatim match to the claim.

REFUTATION ATTEMPTS AND WHY THEY FAILED:
- Selective quoting (strongest attack): the same GitHub post continues "Six weeks ago, after introducing a system to manage flaky tests, the percentage of commits with flaky builds dropped to less than half a percent, or 1 in 200 commits." The 9% is a PRE-REMEDIATION baseline, not a steady state — the claim omits t

**Counter-source:** https://arxiv.org/abs/2101.09077 (Gruber et al., "An Empirical Study of Flaky Tests in Python" — 0.86% of 876,186 tests flaky, ~19x lower than Google's 16%, though rerun-budget-limited and self-described as needing ~170 reruns for confidence); https://github.blog/2020-12-16-reducing-flaky-builds-by-18x/ (same source shows the cited 9% fell to <0.5% after remediation — the claim quotes only the pre

### REFUTED (confidence: high)
**Claim:** The dominant cost of flakiness is erosion of trust in the test signal itself — practitioners report that once confidence is lost, the test suite loses its value entirely, which is why a reporting tool's core job is preserving signal trustworthiness (clearly separating flaky from real failures) rather than just displaying results.

**Evidence:** REFUTED on three independent grounds.

(1) SOURCE MISATTRIBUTION — the quote is grey literature, not a research finding. I extracted the full text of arXiv 2212.00908 (Rasheed, Tahir, Dietrich, Hashemi, Zhang, "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review", Dec 2022). At p.31 the supporting quote appears verbatim and is attributed by the authors to "(Spotify Engineering, [G1])" — one of the 91 grey-literature blog posts the review catalogues alongside 560 academic papers. The review authors report it as a practitioner opinion they collected, NOT as a finding they establish. Labeling this "primary" is wrong: it is a secondary source quoting one vendor engineering blog's rhetorical framing.

(2) THE "DOMINANT COST" RANKING IS DIRECTLY CONTRADICTED by the two largest primary practitioner surveys on exactly this question:
- Gruber & Fraser, ICST 2022 (arXiv 2203.00483), n=335 professional developers/testers, Section E (RQ3: Consequences), verbatim: "Wasting developer time is perceived as the most severe consequence of test flakiness." Free-text consequences coded: wasting developer time (8x) ranked above losing trust in testing (7x). The abstract sentence the claim leans on ("less worried about the computational costs ... and more about the loss of trust") compares trust against COMPUTE COST only — it does not rank trust above wasted time, and the paper's own results section ranks it below.
- Parry, Kapfhammer, Hilton & McMinn, ICSE-SEIP 2022 ("Surveying the Developer Experience of Flaky Tests"), n=170, Table 2 mean impact scores: SQ4.5 CI hindra

**Counter-source:** Gruber & Fraser, "A Survey on How Test Flakiness Affects Developers and What Support They Need To Address It", ICST 2022, n=335 (https://arxiv.org/pdf/2203.00483) — "Wasting developer time is perceived as the most severe consequence of test flakiness"; and Parry, Kapfhammer, Hilton & McMinn, "Surveying the Developer Experience of Flaky Tests", ICSE-SEIP 2022, n=170 (https://philmcminn.com/publicat

### REFUTED (confidence: medium)
**Claim:** The most common industry response — automatic retry/rerun of failures in CI — is characterized in the literature as an anti-pattern ('CI smell') because it slows feedback and hides real bugs; a better implementation should surface flakiness explicitly (verdicts with history and root-cause categories) instead of silently retrying, and should account for retry cost.

**Evidence:** VERBATIM CHECK PASSES, FRAMING FAILS. The quote is real and exact — arXiv 2212.00908 (Rasheed, Tahir, Dietrich, Hashemi, Zhang, "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review", published JSS vol. 206, 2023) contains: "Vassallo et al. identified retrying failure to deal with flakiness as a CI smell, as it has a negative impact on development experience by slowing down progress and hiding bugs." Source quality is fine (peer-reviewed review; primary source is Vassallo/Proksch/Jancso/Gall/Di Penta, ESEC/FSE 2020, "Configuration smells in continuous delivery pipelines", where "Retry Failure" is one of four CD-Linter smells alongside fake success, manual execution, fuzzy version; a 2023 follow-up "Do Developers Fix Continuous Integration Smells?" found Retry Failure pervasive and near-continuous in GitHub Actions projects). Not marketing, not outdated.

REFUTED ON FOUR GROUNDS:

(1) OVERREACH FROM ONE CITATION TO "THE LITERATURE." The claim says retry "is characterized in the literature as an anti-pattern." The evidence is ONE sentence in 2212.00908 attributing the view to ONE paper (Vassallo et al. 2020). The review states it as a secondary attribution, not as its own synthesized conclusion. A single smell catalog is not a literature consensus.

(2) CHERRY-PICKED AGAINST THE SAME SOURCE. The identical paper simultaneously establishes reruns as the foundational, dominant technique: "Rerun (in different forms) is the most common dynamic approach for detecting flaky tests," and lists "Rerun tests" among legitimate process-level response strategies wit

**Counter-source:** https://engineering.fb.com/2020/12/10/developer-tools/probabilistic-flakiness/ (Meta Probabilistic Flakiness Score — retries as the measurement instrument); https://ieeexplore.ieee.org/document/9276599/ (Kowalczyk et al., "Modeling and Ranking Flaky Tests at Apple" — flakiness unavoidable, measured over repeated runs); and arXiv 2212.00908 itself: "Rerun (in different forms) is the most common dyn

### not refuted (confidence: high)
**Claim:** Failure de-duplication — classifying a new test failure as flaky by matching its failure message/stack trace against previously witnessed flaky failures (the fingerprint approach TestLookup uses for clustering) — has wildly project-dependent accuracy: perfect on some projects and useless on others, so a tool cannot ship one global matching strategy and must expose per-project tuning/calibration.

**Evidence:** Attempted refutation on all five checklist axes; the claim survives.

1) QUOTE FIDELITY — supported, near-verbatim. The source is Alshammari, Ammann, Hilton, Bell, "230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers" (arXiv 2401.15788). Its abstract states the study of failure de-duplication (matching a new failure against previously witnessed flaky and true failures) found "for some projects, this approach is extremely effective (with 100% specificity), while for other projects, the approach is entirely ineffective," and warns "because flaky test failure symptoms might resemble those of true failures, there is a risk of misclassifying a true test failure as a flaky failure to be ignored." That is exactly the mechanism the claim describes.

2) SOURCE QUALITY — high, exceeds what the claim needs. Peer-reviewed at IEEE ICST 2024 (DOI 10.1109/ICST60714.2024.00031, IEEE Xplore 10638563; ~25% acceptance rate), not a preprint-only or vendor artifact. Scale: 498 flaky tests across 22 open-source Java projects, 230,439 failure messages (≈80.5k flaky, ≈149.9k true).

3) PER-PROJECT SPREAD IS IN THE DATA, NOT JUST THE ABSTRACT — the paper's Table III reports text-matching specificity per project: 100% for e.g. wildfly and spring-boot, down to roughly 59-73% for e.g. apache httpcore and undertow. So the variance is measured across 22 projects under ONE uniform matching method, which is the strongest form of this claim: the variance is attributable to project failure-symptom characteristics, not to differing tool configurations. The paper further explains

**Counter-source:** arXiv 2310.06298 (SAP HANA, abstracted failure-symptom matching, >=96% precision) — a partial qualifier, not a refutation: single-system industrial result, and the authors state it depends on "descriptive and informative failure symptoms," which is the same project-dependent property the primary source measures. It indicates symptom abstraction quality is a second lever alongside per-project calib

### REFUTED (confidence: high)
**Claim:** Rerun-based flaky detection is both costly and weak as a primary mechanism: organizations spend 2-16% of compute just re-running flaky tests, and Maven's built-in rerun feature could only confirm 23% of flaky test failures — evidence that a test-intelligence product should lean on history/fingerprint-based triage rather than reruns alone.

**Evidence:** The two NUMBERS check out verbatim in the cited source (arXiv 2401.15788, "230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers", Alshammari/Ammann/Hilton/Bell, Jan 2024): the paper states "Bell et al. studied the efficacy of Apache Maven's built-in test rerunning feature, finding that it could only confirm 23% of flaky test failures as flaky" and "A report from Google indicates that 2-16% of computing resources are regularly dedicated just to re-running flaky tests." But the INFERENCE ("evidence that a product should lean on history/fingerprint-based triage rather than reruns alone") is an overreach on three counts.

(1) MISREAD OF THE 23%. In the original DeFlaker paper (Bell et al., ICSE 2018, cs.cornell.edu/~legunsen/pubs/BellETAL18DeFlaker.pdf), the 23% is specific to Maven's DEFAULT rerun configuration — reruns fired immediately, in the SAME JVM that just failed. The same study reports that by "isolating each rerun in its own JVM, and further rebooting the build system to clean the state between reruns, they confirmed that in fact, at least 95% of those failing tests were flaky." So the ground truth the 23% is measured against was ITSELF produced by reruns. The evidence shows naive rerun *configuration* is weak, not that rerunning is a weak mechanism — properly isolated reruns hit ~95% recall. The claim converts a config bug into an indictment of the whole technique.

(2) THE CITED PAPER DOES NOT ENDORSE THE RECOMMENDED ALTERNATIVE. 2401.15788 evaluates exactly the history/failure-message-dedup approach the claim advocates, and its result 

**Counter-source:** https://www.cs.cornell.edu/~legunsen/pubs/BellETAL18DeFlaker.pdf (DeFlaker, ICSE 2018 — same failures, ≥95% confirmed flaky once reruns were JVM-isolated with build-state reset); and the results/limitations section of the cited paper itself, https://ar5iv.labs.arxiv.org/html/2401.15788 ("entirely ineffective" for some projects; recommends richer failure logs, not replacing reruns)

### REFUTED (confidence: high)
**Claim:** Rerun-based flaky detection is both costly and weak as a primary mechanism: organizations spend 2-16% of compute just re-running flaky tests, and Maven's built-in rerun feature could only confirm 23% of flaky test failures — evidence that a test-intelligence product should lean on history/fingerprint-based triage rather than reruns alone.

**Evidence:** The two raw numbers survive, but the inference drawn from them does not — and the cited primary source actually argues against the claim's conclusion.

1) THE PRIMARY SOURCE CONTRADICTS THE CONCLUSION IT IS CITED FOR. arXiv:2401.15788 is "230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers" (Alshammari, Ammann, Hilton, Bell; ICST 2024, DOI 10.1109/ICST60714.2024.00031) — verified via arxiv.org/abs/2401.15788. Its subject IS failure de-duplication, i.e. exactly the "history/fingerprint-based triage" the claim recommends. Its headline finding is ambivalent, not endorsing: on 498 flaky tests / 22 Java projects / 230,439 failure messages, "for some projects, failure de-duplication is extremely effective (with 100% specificity), while for other projects, the approach is entirely ineffective," and the authors explicitly warn that "because flaky test failure symptoms might resemble those of true failures, there is a risk of misclassifying a true test failure as a flaky failure to be ignored." A paper whose result is "fingerprint matching works in some repos and not at all in others, and its failure mode is silently discarding real bugs" cannot be cited as evidence for "lean on history/fingerprint-based triage rather than reruns." That is the opposite of its guidance.

2) THE 23% FIGURE IS MISREAD. It is genuine (Bell et al., DeFlaker, ICSE 2018) but it measures one specific *cheap* rerun configuration: Maven Surefire's rerunFailingTestsCount, which reruns immediately and in the SAME JVM. DeFlaker reports that Maven marked only 23% of 5,328 failures fl

**Counter-source:** arXiv:2401.15788 / ICST 2024 abstract (the claim's own primary source): failure de-duplication is "extremely effective (with 100% specificity)" for some projects but "entirely ineffective" for others, with explicit risk of "misclassifying a true test failure as a flaky failure to be ignored." Plus Bell et al., DeFlaker (ICSE 2018): Maven's same-JVM rerun confirmed 23% of 5,328 failures, but isolat

### REFUTED (confidence: high)
**Claim:** The most common industry response — automatic retry/rerun of failures in CI — is characterized in the literature as an anti-pattern ('CI smell') because it slows feedback and hides real bugs; a better implementation should surface flakiness explicitly (verdicts with history and root-cause categories) instead of silently retrying, and should account for retry cost.

**Evidence:** I extracted the full text of the cited PDF (arXiv:2212.00908, Rasheed, Tahir, Dietrich, Hashemi, Zhang, "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review", Dec 2022; published JSS 2023) and searched it. Three defects:

(1) MISATTRIBUTED STRENGTH. The quote is verbatim but is a single sentence of secondary reporting, not a finding of this review: "Vassallo et al. [S75] identified retrying failure to deal with flakiness as a CI smell, as it has a negative impact on development experience by slowing down progress and hiding bugs." It sits in a list-of-related-work paragraph on p.28. "Characterized in the literature" = one cited config-smell linter paper (Vassallo et al., "Configuration Smells in Continuous Delivery Pipelines", where "Retry Failure" is one of four detected smells). The review does not adopt, test, or generalize the position. The strings "anti-pattern"/"antipattern" appear ZERO times in the paper.

(2) THE CITED SOURCE CONTRADICTS THE CLAIM'S PREMISE. The claim asserts automatic retry is "the most common industry response." The same paper says the opposite, twice: RQ4 summary — "In terms of responses to flaky tests, it seems that the most common approach is to quarantine flaky tests once they are detected"; and "The most common strategy that has been discussed is to quarantine and then fix flaky tests" (citing Fowler's Quarantine → Determine cause → Report → Isolate loop). So the claim's factual premise is refuted by its own primary source.

(3) CHERRY-PICK — the same paper endorses reruns as mainstream. RQ2 summary: "Rerun (in differ

**Counter-source:** Same source, arXiv:2212.00908 / JSS 2023 (RQ4 summary: quarantine is the most common response; RQ2 summary: "Rerun (in different forms) is the most common dynamic approach for detecting flaky tests"); Vassallo et al., "Configuration Smells in Continuous Delivery Pipelines" (the actual origin of the CI-smell label, scoped to pipeline config); Datadog engineering, "Flaky tests: their hidden costs an

### REFUTED (confidence: medium)
**Claim:** Failure de-duplication — classifying a new test failure as flaky by matching its failure message/stack trace against previously witnessed flaky failures (the fingerprint approach TestLookup uses for clustering) — has wildly project-dependent accuracy: perfect on some projects and useless on others, so a tool cannot ship one global matching strategy and must expose per-project tuning/calibration.

**Evidence:** PARTIAL REFUTATION — the empirical half is solid, the prescriptive half is an unsupported leap presented as a consequence ("so a tool cannot ship one global strategy and must expose per-project tuning/calibration").

SUPPORTED: Source is strong and current — arXiv 2401.15788 is Alshammari, Ammann, Hilton, Bell, "230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers", ICST 2024 (peer-reviewed, 25% acceptance), 498 flaky tests / 22 Java projects / 230,439 failure messages. Its own text: "The performance of the approach varies across projects. For example, there are projects with at least 95% precision (10 out of 22) while some projects with 0%", and "it will be challenging to create general-purpose solutions for determining whether a failure is flaky or not." So variability is real and well-evidenced.

REFUTED (why the claim overreaches):
1. The paper makes NO recommendation for per-project tuning, calibration, or project-specific configuration. That prescription is the claim author's inference, not a finding.
2. The paper CANNOT support it: the evaluated matching is exact stacktrace/message matching with no adjustable similarity threshold or parameters. Tuning was never varied, so the study provides zero evidence that per-project knobs recover the 0%-precision projects.
3. The paper attributes the variance to a different mechanism entirely — failure-message specificity: "the approach performs best when failure messages are specific", and it fails where failures are generic ("assertion exceptions", "general exceptions such as assertion and NullPoin

**Counter-source:** https://arxiv.org/abs/2310.06298 (An et al., ICSME 2024 Industry Track — SAP HANA, >=96% precision from a fixed abstracted-symptom-matching pipeline, no per-project tuning reported); plus the claimed source itself, https://arxiv.org/abs/2401.15788, which uses parameterless exact stacktrace matching, never evaluates tuning, attributes variance to failure-message specificity, and reports the same ef

### not refuted (confidence: medium)
**Claim:** Fuzzy similarity matching (TF-IDF over failure logs) beat a simpler failure-log classifier on error rate — 1,437 false positives vs 4,745 — but no approach was uniformly reliable, and the authors conclude general-purpose flaky-vs-real-failure classification is inherently hard, arguing for per-project evaluation and confidence reporting rather than a single universal verdict.

**Evidence:** VERIFIED AGAINST PRIMARY SOURCE. arXiv:2401.15788 = "230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers", Alshammari, Ammann, Hilton, Bell (submitted 2024-01-28, cs.SE). Method: 498 flaky tests / 22 open-source Java projects / 230,439 failure messages, evaluating failure de-duplication as a flaky-vs-true-failure triage signal.

(1) NUMBERS CONFIRMED. Independent confirmation from two retrievals (search snippet of the PDF + ar5iv HTML render): TF-IDF = 1,437 false positives, Failure Log Classifier = 4,745, and a third baseline the claim omits — text-based (exact stack-trace/exception) matching = 8,587. So the 1,437-vs-4,745 comparison is real and in the right direction.

(2) "NO APPROACH UNIFORMLY RELIABLE" CONFIRMED, and is if anything understated. The abstract itself: "for some projects, this approach is extremely effective (with 100% specificity), while for other projects, the approach is entirely ineffective." The discussion notes performance "can vary widely between projects," with some projects "no better than random guessing."

(3) THE SUPPORTING QUOTE IS GENUINE AND SUPPORTS THE "GENERAL-PURPOSE IS HARD" CLAUSE. Also confirmed: the authors recommend future work focus on "case studies in single projects" — which directly backs the "per-project evaluation" clause.

TWO CAVEATS THE PARENT SHOULD FIX BEFORE USING THIS:
(a) OVERREACH ON ONE CLAUSE — "arguing for ... confidence reporting" is NOT something I could locate in the paper. The authors' stated recommendations are per-project case studies and increasing the information content of fai

**Counter-source:** No contradicting source found; the strongest internal qualification is that the paper's own recommendations are per-project case studies + richer failure logs, not "confidence reporting" as the claim asserts. Unchecked risk: later LLM-based flaky classifiers (arXiv 2605.11482 NeuroFlake) may weaken the general-purpose-is-hard conclusion.

### not refuted (confidence: high)
**Claim:** The most common industry response — automatic retry/rerun of failures in CI — is characterized in the literature as an anti-pattern ('CI smell') because it slows feedback and hides real bugs; a better implementation should surface flakiness explicitly (verdicts with history and root-cause categories) instead of silently retrying, and should account for retry cost.

**Evidence:** SURVIVES REFUTATION, with one scoping caveat.

(1) Quote fidelity — verbatim confirmed. ar5iv HTML of arXiv:2212.00908 contains exactly: "Vassallo et al. [75] identified retrying failure to deal with flakiness as a CI smell, as it has a negative impact on development experience by slowing down progress and hiding bugs." The claim reports this as how the literature CHARACTERIZES retry, not as measured fact — so no overreach in the first half.

(2) Source quality exceeds what the arXiv link implies. The paper is Rasheed, Tahir, Dietrich, Hashemi & Zhang, "Test flakiness' causes, detection, impact and responses: A multivocal review," peer-reviewed in Information & Software Technology (ScienceDirect S0164121223002327), synthesizing 651 sources (560 academic + 91 grey). The "CI smell" label is attributed to a peer-reviewed PRIMARY source, Vassallo et al., "Configuration Smells in Continuous Delivery Pipelines: A Linter and a Six-Month Study on GitLab" (ESEC/FSE 2020, DOI 10.1145/3368089.3409709), whose CD-Linter catalog includes "retry failure" as one of four detected smells; the same group's CI-Odor (ICSE 2019) detects related antipatterns (slow build, broken master, skip failed tests, late merging) and surveyed 124 developers. Not marketing, not a blog, not a press release.

(3) Contradiction hunt found no kill shot. No credible vendor-neutral source defends SILENT retry-to-green. The strongest apparent counter-evidence — Google auto-reruns on presubmit (testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html), and Cypress / pytest-rerunfailures / rspec-retry ship

**Counter-source:** Counter-evidence examined and found NOT to refute: (a) Google Testing Blog, "Flaky Tests at Google and How We Mitigate Them" (testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html) — Google does auto-rerun on presubmit, but gates on 3 consecutive failures and auto-quarantines above a flakiness threshold, i.e. tracked not silent, which supports the claim's prescription. (b) The same 

### not refuted (confidence: medium)
**Claim:** 75% of flaky tests (606 of 810 across 24 Java projects) fail in co-occurring clusters rather than in isolation, meaning most flaky failures are systemic — so tooling that treats each flaky test as an independent unit (per-test quarantine, per-test triage) mismatches the dominant failure mode.

**Evidence:** VERIFIED AGAINST PRIMARY SOURCE, WITH TWO MATERIAL QUALIFICATIONS THAT MUST TRAVEL WITH THE CLAIM.

1) Numbers are exact, not paraphrased. Fetched https://arxiv.org/html/2504.16777 — "Systemic Flakiness: An Empirical Analysis of Co-Occurring Flaky Test Failures," EASE 2025 (29th Intl. Conf. on Evaluation and Assessment in SE). Peer-reviewed venue, published 2025, so neither outdated nor marketing/press-release material. The paper states verbatim: "Of the 810 flaky tests, 606 (75%) belong to a cluster... The mean size over the 45 clusters is 13.5 flaky tests." Arithmetic is internally consistent (45 x 13.5 = 607.5 ~ 606). 24 Java projects confirmed; 10,000 test-suite runs per project, >5 years of compute, built on the Alshammari et al. JUnit rerun dataset.

2) "Cluster" genuinely means failure co-occurrence, not a looser notion. Paper: "a data point is a flaky test and the distance metric needs to capture the extent to which the failures of two flaky tests co-occur" — agglomerative clustering over Jaccard distance on the set of failing run IDs, threshold auto-selected to maximize mean silhouette. Singletons are explicitly excluded as non-evidence of systemic flakiness ("they do not identify any failure co-occurrence by definition"), so the 75% is not inflated by counting isolated tests as trivial clusters. The word "systemic" is the paper's own framing (it is the title), so the claim is not editorializing there. The tooling inference is also close to the authors' own: they call for "automated techniques to detect and triage systemic flakiness in continuous integration pipeli

**Counter-source:** No external contradicting source found (WebSearch budget exhausted at 200/200); strongest counter-evidence is internal to the source itself — its own Table 1 and Threats to Validity section, showing only 10/22 projects had any cluster, heavy skew from Alluxio's 113-test cluster, and a controlled single-commit rerun harness rather than production CI: https://arxiv.org/html/2504.16777

### not refuted (confidence: high)
**Claim:** 75% of flaky tests (606 of 810 across 24 Java projects) fail in co-occurring clusters rather than in isolation, meaning most flaky failures are systemic — so tooling that treats each flaky test as an independent unit (per-test quarantine, per-test triage) mismatches the dominant failure mode.

**Evidence:** VERIFIED AGAINST PRIMARY SOURCE. Paper: Parry, Kapfhammer, Hilton, McMinn, "Systemic Flakiness: An Empirical Analysis of Co-Occurring Flaky Test Failures," arXiv:2504.16777, EASE 2025 (peer-reviewed, June 2025, Istanbul). Authors are established flaky-test researchers.

1) QUOTE IS VERBATIM AND CORRECTLY READ. Sec 3.1: "Of the 810 flaky tests, 606 (75%) belong to a cluster." Arithmetic is internally consistent: 45 clusters x mean size 13.5 = 607.5 ~ 606.

2) NOT AN OVERREACH — the actionable inference is the AUTHORS' OWN. Sec 4.3: developers "can reduce the cost of repairing flaky tests by targeting shared root causes, allowing them to simultaneously fix numerous flaky tests instead of addressing them in isolation." The claim's "per-test quarantine/triage mismatches the dominant failure mode" is a faithful restatement.

3) "CO-OCCURRING" IS LITERAL, NOT LOOSE CORRELATION. Clusters built by agglomerative clustering on Jaccard distance over sets of identical FAILING RUN IDs — same-run simultaneous failures. Cluster acceptance gated on mean silhouette score >= 0.6.

4) SHARED ROOT CAUSE WAS VALIDATED, NOT ASSUMED. Four authors independently inspected stack traces for all 45 clusters, negotiated agreement: networking (25 clusters), external-dependency instability (14), filesystem pollution (5), timeouts (4), unknown (2), system clock (1).

5) SOURCE QUALITY MATCHES CLAIM STRENGTH: primary empirical study on the established FlakeFlagger dataset (10,000 test-suite runs per project, ~5 years of compute). Not marketing, not a press release, not forum speculation, not a vendor bench

**Counter-source:** Strongest counter-evidence is internal to the same paper (Sec 3.1): "Of the 22 projects in the FlakeFlagger dataset that contain at least one flaky test, 10 (45%) contain at least one cluster" — i.e. 12 of 22 projects exhibit NO systemic flakiness, so the 75% is concentrated in a minority of projects and is a pooled per-test rather than per-project result. Secondary: authors' own threats to validi

### not refuted (confidence: medium)
**Claim:** Fuzzy similarity matching (TF-IDF over failure logs) beat a simpler failure-log classifier on error rate — 1,437 false positives vs 4,745 — but no approach was uniformly reliable, and the authors conclude general-purpose flaky-vs-real-failure classification is inherently hard, arguing for per-project evaluation and confidence reporting rather than a single universal verdict.

**Evidence:** CORE VERIFIED against the primary source (arXiv 2401.15788, "230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers", Alshammari/Ammann/Hilton/Bell, Jan 2024; 498 flaky tests, 22 OSS Java projects, 230,439 failure messages). (1) The FP figures are exact and correctly attributed: the paper states "Both classifiers have less False Positive rates (4,745 in the Failure Log Classifier and 1,437 in TF-IDF) than the rate of using the text-based matching (8,587)." (2) "Beat on error rate" is not cherry-picked — TF-IDF also has fewer FNs (1,095 vs 2,340 for the Failure Log Classifier; text-based matching 43,326), so it wins on both error types, i.e. the claim understates rather than overstates. (3) "No approach uniformly reliable" is supported: per-project results range from 100% specificity to entirely ineffective. (4) The generalization quote is verbatim from the discussion/conclusion. (5) Per-project evaluation is genuinely the authors' position — they recommend future work "continue to study the application of these approaches as case studies in single projects."

DEFECTS (correct the claim, do not adopt verbatim): (a) "confidence reporting" is NOT in the paper — two independent full-text fetches found no recommendation to report confidence scores/levels with a flakiness verdict; this clause is the researcher's extrapolation attributed to the authors. The paper's actual actionable recommendation is different and more specific: "Increasing the amount of information in test failure logs can greatly improve the performance of automated approaches for de-d

**Counter-source:** No contradicting source located. The contradiction sweep could not be performed — WebSearch budget exhausted (200/200 calls) before this task; verification was limited to the primary source itself via https://arxiv.org/abs/2401.15788 and https://ar5iv.labs.arxiv.org/html/2401.15788.

### REFUTED (confidence: medium)
**Claim:** Fuzzy similarity matching (TF-IDF over failure logs) beat a simpler failure-log classifier on error rate — 1,437 false positives vs 4,745 — but no approach was uniformly reliable, and the authors conclude general-purpose flaky-vs-real-failure classification is inherently hard, arguing for per-project evaluation and confidence reporting rather than a single universal verdict.

**Evidence:** PARTIALLY VERIFIED, BUT REFUTED ON THE LOAD-BEARING CLAUSE.

WHAT CHECKS OUT (verified against extracted PDF text):
1. The numbers are exact. Table V total row (21 projects, 229,802 failures): Failure Log Classifier = 78,181 TP / 2,340 FN / 4,745 FP; TF-IDF = 79,428 TP / 1,095 FN / 1,437 FP. Body text: "Both classifiers have less False Positive rates (4,745 in the Failure Log Classifier and 1,437 in TF-IDF) than the rate of using the text-based matching (8,587)."
2. "No approach uniformly reliable" is supported: "in some projects they have at least 90% F1 scores with zero FN failures while in few projects it is worse than being randomly guessing"; conclusion says "performance can vary widely between projects."
3. The supporting quote is verbatim from the Conclusion (Section VIII).

WHY IT IS REFUTED:
(a) FABRICATED AUTHORIAL RECOMMENDATION. The claim states the authors argue "for per-project evaluation and confidence reporting rather than a single universal verdict." The string "confidence" appears ZERO times anywhere in the 12-page paper (grep for `confidence|confiden` returned "No matches found"). The authors never recommend confidence reporting, never discuss calibrated verdicts, and never contrast against a "single universal verdict." What they actually recommend after the quoted sentence is entirely different: instrumentation-based approaches (DeFlaker), richer failure logs ("Increasing the amount of information in test failure logs can greatly improve the performance"), and future *research* as "case studies in single projects." The per-project clause is a stretch of 

**Counter-source:** Primary source itself: Alshammari, Ammann, Hilton, Bell, "230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers" (arXiv:2401.15788, submitted 2024-01-28). Full text extracted from the PDF and searched directly; https://arxiv.org/abs/2401.15788

### not refuted (confidence: high)
**Claim:** Failure de-duplication — classifying a new test failure as flaky by matching its failure message/stack trace against previously witnessed flaky failures (the fingerprint approach TestLookup uses for clustering) — has wildly project-dependent accuracy: perfect on some projects and useless on others, so a tool cannot ship one global matching strategy and must expose per-project tuning/calibration.

**Evidence:** VERDICT: Not refuted. I extracted the full text of arXiv:2401.15788v1 ("230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers", Alshammari/Ammann/Hilton/Bell, GMU+CMU+Northeastern, 28 Jan 2024) with pypdf and read the actual sections rather than trusting summaries.

1. QUOTE IS VERBATIM AND NOT CHERRY-PICKED. Paper lines 39-43: "We find that for some projects, this approach is extremely effective (with 100% specificity), while for other projects, the approach is entirely ineffective. By analyzing the characteristics of these flaky and non-flaky failures, we provide useful guidance on how developers should rely on this approach." The paper defines exactly the task the claim describes (line 121-123): "A large-scale evaluation of failure de-duplication using failure messages and stacktraces to determine if a failure is flaky or true failure."

2. THE VARIANCE IS QUANTIFIED IN-BODY, NOT JUST THE ABSTRACT. Lines 584-587: "The performance of the approach varies across projects. For example, there are projects with at least 95% precision (10 out of 22) while some projects with 0% (5 out 10 projects)." And critically, the variance survives the smarter techniques — lines 720-723: "The relative performance of the Failure Log Classifier and the TF-IDF varies as in some projects they have at least 90% F1 scores with zero FN failures while in few projects it is worse than being randomly guessing." Mechanism is given too (lines 589-592): projects where matching fails are ones whose failures surface as bare assertion exceptions, i.e. low-information logs.

3. T

**Counter-source:** https://arxiv.org/abs/2310.06298 (Just-in-Time Flaky Test Detection via Abstracted Failure Symptom Matching, ICSME 2024 industry track) — reports 96% precision / 76% recall for failure-symptom matching at SAP HANA. Examined as a potential counterexample but does NOT refute: it is a single-codebase industrial deployment, consistent with the "extremely effective on some projects" half of the claim, 

### not refuted (confidence: medium)
**Claim:** 75% of flaky tests (606 of 810 across 24 Java projects) fail in co-occurring clusters rather than in isolation, meaning most flaky failures are systemic — so tooling that treats each flaky test as an independent unit (per-test quarantine, per-test triage) mismatches the dominant failure mode.

**Evidence:** VERIFIED against primary source (arXiv 2504.16777, "Systemic Flakiness: An Empirical Analysis of Co-Occurring Flaky Test Failures", Parry, Kapfhammer, Hilton, McMinn, 23 Apr 2025).

NUMBERS EXACT. Abstract states verbatim: "We performed agglomerative clustering of flaky tests based on their failure co-occurrence, finding that 75% of flaky tests across all projects belong to a cluster, with a mean cluster size of 13.5 flaky tests." Body confirms: "Of the 810 flaky tests, 606 (75%) belong to a cluster... The mean size over the 45 clusters is 13.5 flaky tests." Dataset = "10,000 test suite runs from 24 Java projects on GitHub." Claim's 606/810/24-projects/75% all match.

INTERPRETATION IS THE PAPER'S OWN, NOT AN OVERREACH. The paper coins "systemic flakiness" and states it "represents an inflection point by challenging the deep-seated assumption that flaky test failures are isolated occurrences." Clustering criterion is genuine failure co-occurrence (Jaccard distance over sets of failing test-suite run IDs), computed per-project, which is exactly what the claim asserts.

THREE REFUTATION ATTEMPTS FAILED: (1) Singleton inflation — refuted; singletons are EXCLUDED by design ("We also did not consider clusters containing only a single flaky test...as evidence of systemic flakiness"), making 75% conservative. 204 (25%) were non-clustered. (2) Pure statistical artifact — refuted; four authors independently manually inspected stack traces, resolved by negotiated agreement, identifying "intermittent networking issues and instabilities in external dependencies as the predominant cause

**Counter-source:** No contradicting source located. IMPORTANT LIMITATION ON THIS VERIFICATION: the session's WebSearch budget was fully exhausted (200/200) before I could run independent searches for disputing or replicating literature, so the refutation attempt was confined to adversarial interrogation of the primary source itself (two targeted WebFetch passes on the HTML full text and the abs page, probing cluster

### REFUTED (confidence: high)
**Claim:** Rerun-based flaky detection is both costly and weak as a primary mechanism: organizations spend 2-16% of compute just re-running flaky tests, and Maven's built-in rerun feature could only confirm 23% of flaky test failures — evidence that a test-intelligence product should lean on history/fingerprint-based triage rather than reruns alone.

**Evidence:** The two numbers are real, but the claim's inference is an inversion of both sources, and one number is misattributed.

(1) QUOTE VERIFIED BUT MISREAD. The verbatim sentence does exist in arXiv 2401.15788 ("230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers," Alshammari/Ammann/Hilton/Bell, ICST 2024) — confirmed via https://arxiv.org/html/2401.15788v1. But the underlying DeFlaker finding (Bell et al., ICSE 2018) is a critique of Maven Surefire's SPECIFIC configuration — rerunning a failed test immediately, in the SAME JVM — not of reruns as a mechanism. In the same DeFlaker study, when each rerun was isolated in its own JVM with the build system rebooted to clean state between reruns, at least 95% of those same failing tests were confirmed flaky (DeFlaker reports 95.5% vs Maven's 23% recall). So the evidence shows reruns work well when properly isolated; the 23% measures a bad rerun configuration, not rerun-based detection. Citing 23% as proof that "reruns are weak as a primary mechanism" reverses the paper's actual lesson.

(2) THE CITED SOURCE'S OWN CONCLUSION CONTRADICTS THE PRESCRIPTION. The claim uses 2401.15788 to argue for "history/fingerprint-based triage rather than reruns." That paper evaluates exactly that alternative (text matching, TF-IDF, Failure Log Classifier over failure messages) and concludes it is NOT generalizable: extremely effective with 100% specificity on some projects, and on others "the approach is entirely ineffective" — with the authors noting it will be challenging to build general-purpose flaky/not-flaky determina

**Counter-source:** DeFlaker: Automatically Detecting Flaky Tests (Bell et al., ICSE 2018) — https://www.cs.cornell.edu/~legunsen/pubs/BellETAL18DeFlaker.pdf (isolated-JVM reruns confirmed ~95% of the failures Maven's same-JVM rerun caught only 23% of); and the cited paper's own conclusion at https://arxiv.org/html/2401.15788v1 (failure de-duplication ranges from 100% specificity to entirely ineffective across projec
