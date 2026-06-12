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
| AIQ-P1 Structured agent contracts | pending | |
| AIQ-P2 Self-critique / verification pass | pending | |
| AIQ-P3 Evidence + confidence scoring | pending | |
| AIQ-P4 Gap-detection + report-refinement agents | pending | |
| AIQ-P5 Report-quality eval harness | pending | |
| FLK-P1 Intermittency + error-signature analysis | pending | |
| FLK-P2 Statistical confidence (Wilson CI, migration) | pending | |
| FLK-P3 ML flakiness-confidence classifier | pending | |
| FLK-P4 Agentic flaky investigator | pending | |
| FLK-P5 Granular step-level (DEFERRED) | no-go (needs granular stack on main; record as future) | |
| FINAL delivery review + PR | pending | |

## Last done
Run 1: bootstrap — branch + plan + cursor files committed & pushed.

## Next up
AIQ-P1 Structured agent contracts.

## No-go rationale log
- FLK-P5: granular step-level intermittency + stack-trace fingerprinting depend
  on granular columns (retry_count, is_flaky_run, stack_trace, step_count,
  test_steps) that are NOT in origin/main. Deferred per plan constraint until
  the granular stack is merged to main.
