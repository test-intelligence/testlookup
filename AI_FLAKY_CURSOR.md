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
| AIQ-P3 Evidence + confidence scoring | pending | |
| AIQ-P4 Gap-detection + report-refinement agents | pending | |
| AIQ-P5 Report-quality eval harness | pending | |
| FLK-P1 Intermittency + error-signature analysis | pending | |
| FLK-P2 Statistical confidence (Wilson CI, migration) | pending | |
| FLK-P3 ML flakiness-confidence classifier | pending | |
| FLK-P4 Agentic flaky investigator | pending | |
| FLK-P5 Granular step-level intermittency + stack-trace fingerprinting | pending | CORRECTED run 2: granular stack IS on origin/main (PR #169) — columns retry_count/is_flaky_run/stack_trace/step_count + test_steps table verified present. No-go constraint removed; phase is ACTIVE. |
| FINAL delivery review + PR | pending | |

## Last done
Run 2: (a) CORRECTED FLK-P5 from no-go→pending (granular stack confirmed on
origin/main, PR #169) and updated plan; committed+pushed (a77c6e6). (b) AIQ-P2
(self-critique / verification pass) full SDLC complete — DESIGN/CODE/REVIEW+
adversarial/BUGFIX/QA/VERIFY/DOCS. New consistency.py + wiring in 3 agents, 4 new
confidence rules, 92 targeted tests green, architectural ratchets + quality gate
green. Committed+pushed (4da838b), verified on origin.

## Next up
AIQ-P3 Evidence + confidence scoring (EvidenceRef{source,ref_id,excerpt,strength,
contribution}; weighted aggregate confidence with cap; conf>70 requires >=2
medium or 1 strong source; surface the breakdown).

## No-go rationale log
- FLK-P5 (RESOLVED 2026-06-12, run 2): granular step-level intermittency +
  stack-trace fingerprinting were deferred when granular columns were not on
  main. The granular stack is NOW merged to origin/main (PR #169). Verified via
  git grep on origin/main:backend/app/models/postgres.py — retry_count,
  is_flaky_run, stack_trace, step_count columns (lines 374-377) and the
  test_steps table (class TestStep, line 654) all exist. The no-go constraint is
  lifted; FLK-P5 is now pending/ACTIVE and will use these granular signals.
