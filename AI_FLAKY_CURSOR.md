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
| AIQ-P4 Gap-detection + report-refinement agents | pending | |
| AIQ-P5 Report-quality eval harness | pending | |
| FLK-P1 Intermittency + error-signature analysis | pending | |
| FLK-P2 Statistical confidence (Wilson CI, migration) | pending | |
| FLK-P3 ML flakiness-confidence classifier | pending | |
| FLK-P4 Agentic flaky investigator | pending | |
| FLK-P5 Granular step-level intermittency + stack-trace fingerprinting | pending | CORRECTED run 2: granular stack IS on origin/main (PR #169) — columns retry_count/is_flaky_run/stack_trace/step_count + test_steps table verified present. No-go constraint removed; phase is ACTIVE. |
| FINAL delivery review + PR | pending | |

## Last done
Run 3: AIQ-P3 (Evidence + confidence scoring) full SDLC complete —
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
AIQ-P4 Gap-detection + report-refinement agents (new gap_detection_agent: what
was NOT analyzed — unanalyzed/inconclusive/no-evidence + reason; referential
integrity analyzed+skipped+errored==failed_count. New report_refinement_agent:
resolve contradictions across parallel anomaly/analysis/cluster signals; dedup a
test analyzed by two routes. Wire optionally into workflow.py).

## No-go rationale log
- FLK-P5 (RESOLVED 2026-06-12, run 2): granular step-level intermittency +
  stack-trace fingerprinting were deferred when granular columns were not on
  main. The granular stack is NOW merged to origin/main (PR #169). Verified via
  git grep on origin/main:backend/app/models/postgres.py — retry_count,
  is_flaky_run, stack_trace, step_count columns (lines 374-377) and the
  test_steps table (class TestStep, line 654) all exist. The no-go constraint is
  lifted; FLK-P5 is now pending/ACTIVE and will use these granular signals.
