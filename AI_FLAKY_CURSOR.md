# AI Flaky / AI-Agent-Quality — Phase Cursor

State of record across recurring headless sessions. Updated + pushed every run.

## Legend
`pending` · `in-progress` · `done` · `no-go (rationale)`

## Push proof
- [x] Branch created off origin/main
- [x] Bootstrap commit pushed + verified on origin (run 1)

## Phases

| Phase | Status | Notes |
|-------|--------|-------|
| AIQ-P1 Structured agent contracts | done | run 1: contracts model + RunCompare/LogIntelligence/RegressionWatchman wrapped; ratchet + behavioral tests green |
| AIQ-P2 Self-critique / verification pass | done | run 2: consistency.py self-critique layer wired into Summary/ReleaseRisk/Analysis before validate_agent_contract; full SDLC (design→code→review+adversarial→bugfix→QA→verify→docs). 1 Blocker (never-raise) + 3 Majors fixed & regression-tested. 92 targeted tests green; 10 architectural ratchets + 15-guard quality gate green. |
| AIQ-P3 Evidence + confidence scoring | done | run 3: EvidenceRef{source,ref_id,excerpt(redact+trunc 240),strength,contribution} + aggregate_confidence (weighted mean weak1/med2/strong3, cap>70 needs >=1 strong or >=2 medium). Optional confidence_breakdown on AgentContractMetadata; opt-in structured_evidence param (backward compatible, never-raises). Adopters: log_intelligence (2 medium@80) + release_risk (strong score_model + weak consistency). Full SDLC: design→code→review+adversarial(2 Majors fixed: non-iterable + inf never-raise)→QA(46 tests)→regression(log_intel 80) →smoke→docs. 81 tests + quality gate(15 guards) green. Pushed f013d48 + this. |
| AIQ-P4 Gap-detection + report-refinement agents | done | run 4: GapDetectionAgent (coverage/integrity audit: analyzed+skipped+errored==failed_count, GapItem reasons) + ReportRefinementAgent (cross-route dedup analysis>anomaly>cluster + contradiction taxonomy/resolution) as pure-local, offline-safe, never-raise agents. Nested GapReport/RefinedReport contracts; opt-in via AIQ_GAP_REFINEMENT_ENABLED (default off, identical graph topology). Full SDLC: design(go)→code→review+adversarial(2 Blockers: nesting drops data + enum str(member) collapse; 1 Major never-raise on non-dict state — all fixed)→re-review CLEAN→QA(29 tests)→deploy-smoke→docs. quality-gate caught missing log_decision → added. 15/15 guards + 6+ ratchets green; 464 passed wider net (only pre-existing tokenizers env fail). Commits c81c8f5,1403a35,a12a8b8,23699bd,fd46091. |
| AIQ-P5 Report-quality eval harness | pending | |
| FLK-P1 Intermittency + error-signature analysis | pending | |
| FLK-P2 Statistical confidence (Wilson CI, migration) | pending | |
| FLK-P3 ML flakiness-confidence classifier | pending | |
| FLK-P4 Agentic flaky investigator | pending | |
| FLK-P5 Granular step-level intermittency + stack-trace fingerprinting | pending | CORRECTED run 2: granular stack IS on origin/main (PR #169) — columns retry_count/is_flaky_run/stack_trace/step_count + test_steps table verified present. No-go constraint removed; phase is ACTIVE. |
| FINAL delivery review + PR | pending | |

## Last done
Run 4: AIQ-P4 (Gap-detection + report-refinement agents) full SDLC complete —
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
AIQ-P5 Report-quality eval harness: extend ai_eval_service/eval_gate_service
with a small golden set + metrics coherence, completeness (evidence when
conf>=60), actionability (fix when non-flaky), calibration (Brier/ECE); CI gate
for agent changes. (After AIQ-P5, the FLK-P1..P5 flaky-intelligence phases
remain, then FINAL delivery review + open the ONE PR and set cursor DONE.)

## No-go rationale log
- FLK-P5 (RESOLVED 2026-06-12, run 2): granular step-level intermittency +
  stack-trace fingerprinting were deferred when granular columns were not on
  main. The granular stack is NOW merged to origin/main (PR #169). Verified via
  git grep on origin/main:backend/app/models/postgres.py — retry_count,
  is_flaky_run, stack_trace, step_count columns (lines 374-377) and the
  test_steps table (class TestStep, line 654) all exist. The no-go constraint is
  lifted; FLK-P5 is now pending/ACTIVE and will use these granular signals.
