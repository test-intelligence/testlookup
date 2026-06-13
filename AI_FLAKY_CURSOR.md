# AI Flaky / AI-Agent-Quality — Phase Cursor

State of record across recurring headless sessions. Updated + pushed every run.

## Legend
`pending` · `in-progress` · `done` · `no-go (rationale)`

## Push proof
- [x] Branch created off origin/main
- [x] Bootstrap commit pushed + verified on origin (run 1)

## Queued cleanup (apply before next pending phase)

| Item | Status | Notes |
|------|--------|-------|
| CLEANUP-1 RegressionWatchman success-path never-raise | done | run 6: `_summarize_classification(...)` guarded helper (skip non-dict; per-value `int()` try/except→0; empty→100). run()-level regression test feeds non-dict value + non-numeric 'confidence' string → no raise, valid contract. Reproduced defect first (ValueError on int('high')). |
| CLEANUP-2 LogIntelligence/RegressionWatchman extra='allow' parity | done | run 6: added `model_config = ConfigDict(extra='allow')` to both contracts (parity w/ RunCompare); undeclared-key-survival tests added for both. |

## Phases

| Phase | Status | Notes |
|-------|--------|-------|
| AIQ-P1 Structured agent contracts | done | run 1: contracts model + RunCompare/LogIntelligence/RegressionWatchman wrapped; ratchet + behavioral tests green |
| AIQ-P2 Self-critique / verification pass | done | run 2: consistency.py self-critique layer wired into Summary/ReleaseRisk/Analysis before validate_agent_contract; full SDLC (design→code→review+adversarial→bugfix→QA→verify→docs). 1 Blocker (never-raise) + 3 Majors fixed & regression-tested. 92 targeted tests green; 10 architectural ratchets + 15-guard quality gate green. |
| AIQ-P3 Evidence + confidence scoring | done | run 3: EvidenceRef{source,ref_id,excerpt(redact+trunc 240),strength,contribution} + aggregate_confidence (weighted mean weak1/med2/strong3, cap>70 needs >=1 strong or >=2 medium). Optional confidence_breakdown on AgentContractMetadata; opt-in structured_evidence param (backward compatible, never-raises). Adopters: log_intelligence (2 medium@80) + release_risk (strong score_model + weak consistency). Full SDLC: design→code→review+adversarial(2 Majors fixed: non-iterable + inf never-raise)→QA(46 tests)→regression(log_intel 80) →smoke→docs. 81 tests + quality gate(15 guards) green. Pushed f013d48 + this. |
| AIQ-P4 Gap-detection + report-refinement agents | done | run 4: GapDetectionAgent (coverage/integrity audit: analyzed+skipped+errored==failed_count, GapItem reasons) + ReportRefinementAgent (cross-route dedup analysis>anomaly>cluster + contradiction taxonomy/resolution) as pure-local, offline-safe, never-raise agents. Nested GapReport/RefinedReport contracts; opt-in via AIQ_GAP_REFINEMENT_ENABLED (default off, identical graph topology). Full SDLC: design(go)→code→review+adversarial(2 Blockers: nesting drops data + enum str(member) collapse; 1 Major never-raise on non-dict state — all fixed)→re-review CLEAN→QA(29 tests)→deploy-smoke→docs. quality-gate caught missing log_decision → added. 15/15 guards + 6+ ratchets green; 464 passed wider net (only pre-existing tokenizers env fail). Commits c81c8f5,1403a35,a12a8b8,23699bd,fd46091. |
| AIQ-P5 Report-quality eval harness | done | run 5: pure-local/offline/never-raise agent_eval_harness.py scoring RECORDED agent outputs on 5 metrics — coherence, completeness(evidence@conf>=60), actionability(fix when non-flaky), accuracy(verdict==truth), calibration(Brier mean((conf/100-outcome)^2) + ECE 10-bin). evaluate_agent_outputs→AgentEvalReport(per_metric_pass+passed); thresholds coh/comp .90, act .85, acc .70, brier<=.20, ece<=.15, MIN_SAMPLES=5. golden_agent_outputs.py (9 passing recorded outputs, acc .889 + negative/wrong-agent fixtures). Additive helpers compute_agent_report_quality (ai_eval_service) + evaluate_agent_report_quality_rules (eval_gate_service, 6 rules + overall). CI gate test_architectural_agent_eval_harness.py runs in backend-test job. Full SDLC: design(go)→code→review CLEAN→adversarial(1 Major: calibration-only gate let under-confident always-wrong agent PASS → added accuracy metric to close bypass; 1 Minor coercion '90.5'→90 hardened)→bugfix→QA(34 new + 50 regression green)→deploy(quality-gate 15/15 + runtime smoke: good passes all 7 rules, wrong fails accuracy+overall, offline verified)→docs. No DB/migration/outbound. Commits 7be5f22,5261a2c,17605b7 + docs + this. |
| FLK-P1 Intermittency + error-signature analysis | done | run 6: new pure no-DB never-raise scorer flaky_signals.py (compute_intermittency_signals → IntermittencySignals: status_volatility flips/(runs-1), error_signature_diversity unique denoised err-prefix/fail_count, stack_trace_diversity unique SHA fp/fail_count, in_run_retry_rate from granular retry_count/is_flaky_run, intermittency_label). refresh_flaky_coach joins TestCase meta into the windowed query (no new round-trip) + downgrades persistent_regression QUARANTINE→INVESTIGATE (state machine untouched) + signal-aware actions. get_flaky_coach surfaces numeric signals at read time (one batched query) on new optional FlakyCoachEntry fields → /flaky-coach shows the signal. NO migration/new-commit/outbound. 16 new tests + existing batched-query pins green; quality_gate 15/15 + 24 ratchets green. |
| FLK-P2 Statistical confidence (Wilson CI, migration) | pending | |
| FLK-P3 ML flakiness-confidence classifier | pending | |
| FLK-P4 Agentic flaky investigator | pending | |
| FLK-P5 Granular step-level intermittency + stack-trace fingerprinting | pending | CORRECTED run 2: granular stack IS on origin/main (PR #169) — columns retry_count/is_flaky_run/stack_trace/step_count + test_steps table verified present. No-go constraint removed; phase is ACTIVE. |
| FINAL delivery review + PR | pending | |

## Last done
Run 6: QUEUED CLEANUP-1/CLEANUP-2 (AIQ-P1 surface) + FLK-P1 (intermittency +
error-signature analysis). CLEANUP-1: RegressionWatchman.run() success path moved
confidence/evidence derivation into a guarded `_summarize_classification` so a
non-dict classification value or non-numeric 'confidence' string can no longer
raise into the graph wrapper (reproduced the ValueError first); run()-level
regression test added. CLEANUP-2: `extra='allow'` added to LogIntelligence/
RegressionWatchman contracts for parity with RunCompare; undeclared-key-survival
tests added. Then FLK-P1: pure no-DB never-raise `flaky_signals.py`
(compute_intermittency_signals) discriminating high-volatility flakiness from
low-volatility regression via status_volatility, error_signature_diversity,
stack_trace_diversity, in_run_retry_rate (granular PR#169 cols) + an
intermittency_label; refresh_flaky_coach joins TestCase meta into its windowed
query and downgrades persistent_regression QUARANTINE→INVESTIGATE (state machine
untouched); get_flaky_coach surfaces the numeric signals at read time on new
optional FlakyCoachEntry fields. No migration / no new service commit / no
outbound. Adversarial reviewer run on the FLK-P1 diff. 16 new + existing batched
pins + 62 contract-suite tests green; quality_gate 15/15 + 24 ratchets green.
Commits 643f27a (cleanup) + FLK-P1 commit; verified on origin.

## Prior — Run 5: AIQ-P5 (Report-quality eval harness) full SDLC complete —
DESIGN(go)/CODE/REVIEW(clean)/ADVERSARIAL/BUGFIX/QA/DEPLOY-smoke/DOCS. New
backend/app/services/agent_eval_harness.py: pure-local, offline-safe, never-raise
scorer for RECORDED agent outputs (AgentEvalSample + from_recorded_output). Five
report-quality metrics: coherence (per-sample invariants), completeness (evidence
present when confidence>=60), actionability (fix/action present when verdict
non-flaky), accuracy (verdict==ground_truth), calibration — Brier =
mean((conf/100-outcome)^2) and ECE = sum over 10 equal-width bins of
(|S_b|/N)*|acc_b-conf_b|. evaluate_agent_outputs→AgentEvalReport{per_metric_pass,
passed}; PASS_THRESHOLDS coh/comp>=0.90, act>=0.85, acc>=0.70, brier<=0.20,
ece<=0.15; MIN_SAMPLES=5 (insufficient data fails). golden_agent_outputs.py: 9
recorded outputs+ground truth passing all thresholds (accuracy 0.889) + negative
fixtures + always-wrong-under-confident fixture. Additive helpers
compute_agent_report_quality (ai_eval_service.py) and
evaluate_agent_report_quality_rules (eval_gate_service.py — 6 metric rules +
overall agent_report_quality). CI GATE backend/tests/
test_architectural_agent_eval_harness.py (12 tests) runs in the existing
backend-test pytest job and fails CI if an agent change regresses
calibration/evidence/actions/accuracy on the golden set; +
test_agent_eval_harness.py (22 unit tests). ADVERSARIAL verifier found 1 Major:
the gate scored calibration but NOT accuracy, so an under-confident always-wrong
agent (verdict mismatched, confidence 15) yielded passed=True — closed by adding
the accuracy metric (always-wrong fixture now passed=False on accuracy). 1 Minor:
confidence coercion of stringified float "90.5" degraded to 0 → hardened to
int(float(value)) (still never-raise on inf/nan/non-numeric). QA 34 new + 50
regression green; quality_gate 15/15; runtime smoke via public service
entrypoints (good agent passes all 7 gate rules; wrong agent fails agent_accuracy
+ agent_report_quality; offline no-outbound-imports asserted). No DB / migration /
new service commit / outbound call — AI_OFFLINE_MODE semantics untouched. Commits
7be5f22 (impl), 5261a2c (accuracy fix), 17605b7 (tests), docs + this; verified on
origin.

## Prior — Run 4: AIQ-P4 (Gap-detection + report-refinement agents) full SDLC complete —
DESIGN(go)/CODE/REVIEW+adversarial/BUGFIX/RE-REVIEW(CLEAN)/QA/DEPLOY-smoke/DOCS.
New backend/app/agents/gap_detection_agent.py (GapDetectionAgent: per-failed-test
analyzed/skipped/errored audit, referential integrity analyzed+skipped+errored==
failed_count, GapItem{reason,bucket,detail}) and report_refinement_agent.py
(ReportRefinementAgent: dedup multi-route tests precedence analysis>anomaly>
cluster, detect+resolve contradictions flaky_vs_regression/category_disagreement
→ prefer_analysis|prefer_anomaly|merge|flag_for_review). Both pure-local,
offline-safe, never-raise; validated nested GapReport/RefinedReport contracts
(self-recomputing @model_validators). Wired optionally into the DEEP graph behind
AIQ_GAP_REFINEMENT_ENABLED (default off; topology identical on/off; flag-off nodes
early-return skip delta). Models in agent_contracts.py; contracted-model ratchet
12→14. Reviewers found 2 Blockers — (1) agents nested payload under gap_report/
refined_report while models declared flat fields → validation dropped ALL data +
stage keys (feature inert); fixed by nesting GapReport/RefinedReport sub-models.
(2) enum field_validators used str(member) which on 3.11 collapsed every
GapReason/ContradictionType/ResolutionStrategy to its default; fixed via
getattr(value,"value",value). 1 Major: state.get above try broke never-raise on
non-dict state; moved inside try. Re-review CLEAN (74/74 repro). QA added
test_gap_detection_agent/test_report_refinement_agent/test_aiq_p4_workflow_wiring
(29 tests). Deploy-smoke confirmed runtime gap_report/refined_report populated,
contracts stamped, offline honored. quality_gate caught missing log_decision in
both agents → added (success + guarded fallback). 15/15 guards green; 464 passed
wider net (only pre-existing tokenizers env failure). CHANGELOG + AIQ_FLK_FEATURES
updated. Commits c81c8f5,1403a35,a12a8b8,23699bd,fd46091 + this; verified on origin.

## Prior — Run 3: AIQ-P3 (Evidence + confidence scoring) full SDLC complete —
DESIGN(go)/CODE/REVIEW+adversarial/BUGFIX/QA/VERIFY/DOCS. New
backend/app/agents/evidence.py (EvidenceRef + aggregate_confidence, pure-local,
never-raises), confidence_breakdown on AgentContractMetadata, opt-in
structured_evidence on validate_agent_contract (backward compatible). Adopters:
log_intelligence + release_risk. Reviewer found 2 Majors (non-iterable arg, inf
OverflowError) — both fixed + regression-tested. QA added 46-test
test_evidence_confidence.py; log_intelligence contribution set to 80 to preserve
prior confidence-80 contract. Runtime smoke confirmed cap/redaction/back-compat.
evidence.py added to quality_gate support-files + ratchet INFRA_ALLOWLIST. 81
tests + 15-guard quality gate green. Commits 5e1b777, 4c2aca6, f013d48 + this;
all verified on origin.

## Next up
FLK-P2 Statistical confidence (migration): binomial/Wilson 95% CI on the failure
ratio; store flaky_confidence_low/high on FlakyCoachResult; rank by statistical
strength (30/100 >> 3/10). Prefer closed-form Wilson (no scipy dep). Small
Alembic migration — RE-CHECK the latest single head first, fresh down_revision,
real downgrade(). NOTE: FLK-P1 added two new optional FlakyCoachEntry schema
fields + a flaky_signals.py module (added to no allowlist — pure, no commit). When
FLK-P2 persists confidence cols, surface them alongside the FLK-P1 signals on
/flaky-coach. (After FLK-P2: FLK-P3 ML classifier, FLK-P4 agentic investigator +
/failures surfacing, FLK-P5 granular step-level + stack-trace fingerprinting, then
FINAL delivery review + open the ONE PR and set cursor DONE.) AIQ-P1..P5 + FLK-P1
+ CLEANUP-1/2 all done.

## No-go rationale log
- FLK-P5 (RESOLVED 2026-06-12, run 2): granular step-level intermittency +
  stack-trace fingerprinting were deferred when granular columns were not on
  main. The granular stack is NOW merged to origin/main (PR #169). Verified via
  git grep on origin/main:backend/app/models/postgres.py — retry_count,
  is_flaky_run, stack_trace, step_count columns (lines 374-377) and the
  test_steps table (class TestStep, line 654) all exist. The no-go constraint is
  lifted; FLK-P5 is now pending/ACTIVE and will use these granular signals.
