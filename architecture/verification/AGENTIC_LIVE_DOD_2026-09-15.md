# Agentic live definition-of-done verification — 2026-09-15

## Environment and scope

T22 ran from `codex/t22-live-dod` against a clean export of the tracked tree on
Podman. The seeded lite stack used PostgreSQL, MongoDB, Redis, MinIO, backend,
frontend, and worker services. `REVIEW_GATE_ENFORCED=true` was applied to the
backend and workers for the review tests.

The local Podman machine has 2 GiB of memory, so it could not run the full
`make dev-llm` SLM+LLM pair. The live runtime used `AI_OFFLINE_MODE=true`. This
record separates live runtime evidence from static eval checks and does not
claim inference evidence that was not produced.

## Results

| Criterion | Result | Evidence |
|---|---|---|
| Generated collection and agent invocation | Pass | `scripts/verify_agentic_live_dod.py` validated the tracked Postman request and invoked the eight independently invokable sync/report capabilities. All initial responses were 202; four non-report agents reached `passed/not_applicable` and four report agents reached `completed/pending_review`. The sanitized output is `agentic-live-api-2026-09-15.json`. |
| Internal and public states | Pass | Live SQL returned only `completed` and `passed`, a subset of the six internal states. API polling returned only `completed` and `passed`, a subset of the four public states. |
| Worker interruption, reap, fence, retry | Pass after fix | A summary worker was paused during attempt 1, its lease was expired, and the reaper rotated the token and moved the run to `retry_wait`. A write using the old token updated zero rows. After unpause the run moved `retry_wait(1) → running(2) → completed(2)`. |
| Review enforcement | Pass after fix | A pending decision report produced `PENDING_REVIEW` from release readiness; notification gating returned `refused_unreviewed` with substituted notice text and no AI narrative. Intelligence export now returns an audited 409 while pending and 200 after acceptance. A different non-synthetic QA lead accepted the review; the pipeline reached `passed`, `ai_review.accepted` recorded the actor, and reviewer identity was absent from the API payload. |
| Overdue alert | Pass, corrected premise | During the injected old active run, `testlookup_agent_in_progress_overdue_seconds` was positive (`171002.1638`); after recovery terminalized the run it was `0.0`. The metric is based on active-run age beyond deadline plus grace. Lease expiry drives reaping, while `retry_wait` intentionally remains active, so reaping alone does not clear the alert. |
| Eval attestation guard | Static guard passes; release evidence incomplete | Tests prove that prompt-manifest drift and changes to the watched model/router/registry/reviewer sources require a matching attestation. The bundled attestation resolves, but it is `mode=source_review` and explicitly says candidate-inference recordings for reviewer prompts are absent. A fresh inference-backed attestation was not produced on the offline stack. |
| Eval coverage | Pass | The version-controlled coverage test requires `root_cause_analysis` and AnalysisAgent to have at least 100 samples and every other measured capability at least 20; the sole runtime bookkeeping exemption has a non-empty reason. |
| Summary SLM non-inferiority | Static rule passes; live inference comparison not repeated | The G2 test evaluates all 20 summary golden samples, returns `pass`, and reports paired confidence, cost, and latency values. T22 could not repeat this with live SLM and LLM inference on the 2 GiB VM. |
| Reviewer semantic recall | Pass | Reviewer quality tests report every mutation-class × check-family row with `n` and confidence bounds, require 20 samples for gating, and require 30 per semantic class before second-model auto-disable. |
| Recent-run manifest provenance | Pass for T22 runs | Live pipeline logs and stored execution metadata used checksum `9bece1975240dda17d53dd7305cf47afd64e91a4aaea746fb4b2f9a7dd06e7f5`, which resolves to the bundled attestation. Static service tests fail health for missing or unknown checksums. |
| Workflow regression refusal | Pass | The workflow evaluation test refuses a failed G4 publish unless `accept_regression=true` and a reason and accepting actor are recorded. |

## Code-truth corrections

Two catalog capabilities, cluster investigation dispatch and join, were marked
sync-eligible but correctly reject direct invocation because they require a
surrounding workflow. The catalog now exposes the additive `invokable` field,
and the acceptance criterion covers independently invokable capabilities.
`CapabilitySpecV1` remains unchanged.

The handover said the overdue alert fires on lease expiry and clears when the
reaper acts. The implementation measures active-run age beyond deadline plus
grace; `retry_wait` remains active. The corrected criterion requires the alert
to clear after the recovered run reaches a terminal state.

## Defects found and fixed

`_mark_pipeline_done` was the only pipeline write that did not carry a fencing
token. A paused attempt could wake after reaping, see `retry_wait`, then let its
error handler fail the newer attempt. Finalization now locks and selects by run
ID plus fencing token and raises `LeaseLost` on a stale token.

`GET /api/v1/runs/{run_id}/export` carried a review envelope but did not enforce
the distribution decision. It now uses the shared report distribution policy,
records the decision, commits a refusal audit, and returns the standard 409 for
pending AI reports when enforcement is enabled.

## Repeatability

Run the API slice after seeding a local stack:

```powershell
.\.venv311\Scripts\python.exe scripts\verify_agentic_live_dod.py `
  --base-url http://localhost:8000 `
  --output architecture\verification\agentic-live-api-2026-09-15.json
```

The focused mutation harness asserts that all seven mutations apply and that
each wrong behavior fails the regression suite:

```powershell
.\.venv311\Scripts\python.exe scripts\mutation_check_t22.py
```
