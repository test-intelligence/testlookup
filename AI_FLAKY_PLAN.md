# AI Agents & Flaky-Test Intelligence — Build Plan

Branch: `feat/ai-agents-and-flaky-intelligence` (off origin/main, independent).
CI: Python 3.11; `pytest backend/tests --ignore=tests/integration`.
Two features, ONE pull request. Fully autonomous across recurring sessions.

## FEATURE 1 — AIQ (AI-Agent Quality)
Make the agents that do test reporting / gap-finding / report analysis /
refined-report + engineering-intelligence solid, stable, high-quality.

- **AIQ-P1 Structured agent contracts**: Pydantic schemas for EVERY agent
  output (esp. SummaryAgent, RegressionWatchman, LogIntelligence, RunCompare);
  validate-at-return with deterministic fallback; required fields
  `confidence_score(0-100)`, `evidence_count`, `decision_reason`. New ratchet
  `backend/tests/test_architectural_agent_contracts.py`. No behavior change.
- **AIQ-P2 Self-critique/verification pass**: cross-layer consistency in
  SummaryAgent (exec summary ↔ incident view ↔ evidence ↔ action plan;
  referential integrity of cited test ids); narrative-vs-deterministic-score in
  ReleaseRiskAgent; expanded confidence rules in AnalysisAgent. Log failures as
  `consistency_check_failed` decisions, never silently drop.
- **AIQ-P3 Evidence+confidence scoring**: `EvidenceRef{source, ref_id, excerpt,
  strength weak|medium|strong, contribution 0-100}`; final confidence =
  weighted aggregate, capped; rule conf>70 requires >=2 medium or 1 strong
  source; surface the breakdown.
- **AIQ-P4 Gap-detection + report-refinement agents**: new `gap_detection_agent`
  (what was NOT analyzed: unanalyzed/inconclusive/no-evidence + reason;
  referential integrity analyzed+skipped+errored==failed_count) and
  `report_refinement_agent` (resolve contradictions across parallel
  anomaly/analysis/cluster signals; dedup a test analyzed by two routes). Wire
  optionally into workflow.py.
- **AIQ-P5 Report-quality eval harness**: extend
  ai_eval_service/eval_gate_service with a small golden set + metrics coherence,
  completeness (evidence when conf>=60), actionability (fix when non-flaky),
  calibration (Brier/ECE); CI gate for agent changes.

## FEATURE 2 — FLK (Flaky-Test Intelligence)
Make flaky-test identification a SOLID signal via data-analysis + ML + Agentic
AI. UPDATE 2026-06-12 (run 2): the granular stack IS NOW MERGED to origin/main
(PR #169). Columns retry_count, is_flaky_run, stack_trace, step_count and the
test_steps table ARE available on the baseline (verified via git grep on
origin/main:backend/app/models/postgres.py). USE these granular signals wherever
they strengthen the flaky verdict; FLK-P5 is no longer deferred.

- **FLK-P1 Intermittency + error-signature analysis** (no migration): add
  `status_volatility = flips/(runs-1)` and `error_signature_diversity = unique
  error prefixes/fail_count` to refresh_flaky_coach; discriminate
  high-volatility flakiness from low-volatility regression and single-error
  breakage. Also fold in retry_count (framework self-retry within a run = strong
  in-run flaky signal) and stack_trace-signature variety now that they exist.
- **FLK-P2 Statistical confidence** (migration): binomial/Wilson 95% CI on the
  failure ratio; store `flaky_confidence_low/high`; rank by statistical strength
  (30/100 >> 3/10). Closed-form Wilson (no scipy dep); small Alembic migration
  (fresh down_revision, real downgrade).
- **FLK-P3 ML flakiness-confidence classifier**: train HistGradientBoosting
  (`is_flaky_confidence` in [0,1]) on features {failure_rate, status_volatility,
  flip_count, error_signature_diversity, failure_rate_trend,
  run_interval_variance, retry_count, stack_trace_signature_diversity}; ground
  truth from quarantine approvals/rejections;
  reuse existing ML infra; rank/quarantine only high-confidence flakes.
- **FLK-P4 Agentic flaky investigator**: upgrade `flaky_sentinel_agent` to a
  multi-step agent that confirms the verdict and EXPLAINS it: cluster recent
  failure messages + stack traces, inspect build/run metadata, emit structured `{is_flaky,
  confidence, likely_cause, evidence[]}` (per AIQ-P1/P3). Surface the new signal
  on /flaky-coach and /failures.
- **FLK-P5 ACTIVE** (granular IS on main now — BUILD it): granular step-level
  intermittency + stack-trace fingerprinting. Step-level flip analysis over the
  test_steps table (which assertion/step flips across runs => surgical fix vs
  whole-test quarantine); stack_trace fingerprinting (SHA over first ~500 chars
  => many unique traces per flaky test = environmental/race, one trace =
  deterministic code bug). Surface on the test-case / flaky views. Migration only
  if a new stored column is genuinely needed (fresh down_revision, real
  downgrade).

## FINAL — cross-feature delivery review
Confirm every acceptance criterion; full backend + frontend suites green;
scripts/quality_gate.py + test_architectural_* ratchets green; AI_OFFLINE_MODE
default still short-circuits outbound; CHANGELOG + docs complete; open ONE PR;
set cursor DONE.

## ACCEPTANCE CRITERIA
- **AIQ**: every agent returns a validated Pydantic contract (ratchet enforces);
  Summary/ReleaseRisk/Analysis run a self-consistency check with logged
  failures; confidence scores carry an evidence-strength breakdown; a
  gap-detection report exists per analyzed run with referential integrity;
  report-quality eval harness runs in CI with passing thresholds.
- **FLK**: flaky verdicts carry status_volatility + error_signature_diversity +
  a statistical-confidence interval; an ML flakiness-confidence score ranks
  candidates; the investigator agent emits a structured confirm+explain verdict
  with evidence; /flaky-coach + /failures show the new signal; no regression to
  the quarantine state machine.
- **BOTH**: full backend pytest + frontend suites green; quality_gate +
  architectural ratchets green; AI_OFFLINE_MODE default still gates outbound;
  CHANGELOG + docs updated; ONE PR.

## CONVENTIONS (authoritative)
- Transaction boundary: services db.add/flush and RETURN; the ROUTER owns
  `await db.commit()`; services never db.rollback() an injected session. A new
  service commit requires an allowlist entry + bumping the cap in
  backend/tests/test_architectural_transaction_boundaries.py.
- Authorization/IDOR: a router with {project_id}/{run_id}/{release_id} depends on
  the matching require_*_access guard and verifies the PROVIDED id.
- Offline gate: AI_OFFLINE_MODE=True (default) short-circuits EVERY outbound
  integration BEFORE any feature-flag check; local ONNX/Ollama fine; no cloud
  embedding/LLM without a gate.
- Effective-suite: suite filters/groups consider BOTH tc.suite_name AND
  tr.primary_suite_name.
- Migrations: single Alembic head — re-check latest revision, fresh
  down_revision, real downgrade().
- Pydantic v2 only (no @validator/class Config); async everywhere; structlog
  kwargs not %s; no print in app code; PII redacted at boundaries;
  producer/consumer enum vocab must match; per-(entity,run) writes idempotent.
- Test env: DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test
  MONGODB_URL=mongodb://localhost:27017 REDIS_URL=redis://localhost:6379/0
  JWT_SECRET_KEY=testkey STORAGE_BACKEND=local AI_OFFLINE_MODE=True.
- Every code change = branch + regression test + CHANGELOG.md entry. Commit AND
  push after EVERY phase; verify push landed; never leave work unpushed.
