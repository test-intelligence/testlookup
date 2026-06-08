# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Why we built this

Engineering teams running automated tests get fragmented artifacts: JUnit XML, Allure outputs, flaky failures, pipeline status. Existing tools help visualise results, but teams still burn hours on manual triage, clustering, root-cause analysis, and release decisions. The problem is worse in regulated or private environments that can't depend on cloud-only AI services.

TestLookup is our answer: a local-first test failure intelligence engine that ingests results, clusters failures, explains probable root causes, and produces release-risk signals -- all offline-capable, and all accessible through a dashboard, REST API, CLI, and MCP server so AI assistants can query test health directly.

### Added

- **Multi-framework ingestion** -- JUnit XML, TestNG, Allure JSON, Cypress, Playwright, pytest
- **Three analysis modes** -- rules (pattern match), ML (scikit-learn HistGradientBoosting), LLM (Ollama ReAct agent), auto (smart fallback chain)
- **Run Intelligence** -- single-pane summary with failure clusters, regression diff, risk score
- **Release gate** -- GO / CONDITIONAL_GO / NO_GO with explainable reasons and QA Lead override audit trail
- **Decision trail** -- per-run "why did the AI do that" drawer with stage filter and full-text search
- **Two-run compare** -- side-by-side diff with classification (new failures, regressions, duration spikes, renamed tests via fuzzy pairing)
- **Flaky quarantine** -- detection, QA Lead approval, active quarantine, nightly recheck, release/re-quarantine state machine
- **Perf regression detection** -- per-test duration baselines (Welford algorithm) with 3-sigma spike detection
- **Feature flags** -- per-project / per-role / rollout-percent gates with audit history
- **MCP server** -- 36 tools, 9 resources, 6 prompt workflows for AI assistant integration
- **CLI** -- 11 command groups, multi-profile auth, table/JSON/YAML output
- **Dashboards** -- 30+ customizable analytics widgets with drag-and-drop layout
- **Live streaming** -- real-time WebSocket dashboard during test execution
- **Jira integration** -- auto-promote failure clusters with 7-dimension severity scoring
- **PII redaction** -- auto-scrub at all system boundaries
- **Email notifications** -- async SMTP with daily/weekly digest subscriptions
- **Observability** -- OpenTelemetry traces, Prometheus metrics (including 7 Tier 0-2 counters), 5 alerting rules
- **Outbound webhooks** -- HMAC-signed event fan-out with retry, DLQ, and replay
- **Sample datasets** -- JUnit, Allure, Cypress, Playwright test result fixtures under `samples/`
- **Benchmark harness** -- classification accuracy + throughput benchmarks with methodology doc
- **Threat model** -- data flow, offline guarantees, auth boundaries, PII scope

### Experimental (flag-off by default)

- Deep investigation multi-agent pipeline (LangGraph)
- RAG knowledge-grounded test case generation
- RAG faithfulness guardrails (Ollama/Ragas pluggable evaluator)
- LLM cost budget with auto-downgrade enforcement
- GitHub Checks integration (PAT-based check-run posting)
- Release compliance pack (SOX/HIPAA/SOC 2 audit ZIP)
- Weekly retro digest (Monday-morning automated retrospective)
- Team value metrics split via service ownership rules
- Continuous fine-tuning pipeline
- Semantic/hybrid search (ChromaDB)

### Tested (2026-06-08 — /releases delete: cascade contract regression)

Pinned the cascade contract behind the `/releases` **Delete** button (backend `DELETE /api/v1/releases/{id}` → `release_service.delete_release` → bare `db.delete(release)`). Audit confirmed the end-to-end delete path already exists (ADMIN-gated, project-scoped endpoint; UI button + confirm + toast + SWR refetch), so no behaviour change — added the missing guard tests:
- `Release.phases` and `Release.test_run_links` must stay `delete-orphan` (dropping it would 500 any delete of a release that has phases or linked runs via an FK `IntegrityError`).
- Deleting a release must **not** cascade into `TestRun` — `Release` holds no relationship to `TestRun`, and the link's `test_run_id`/`release_id` FKs are `ON DELETE CASCADE` (run-removal clears the link, never the reverse). Runs survive release deletion.
- `backend/tests/regression/test_release_delete_cascade.py` (4 tests, mapper-introspection — no DB needed).

### Fixed (2026-06-08 — /failures vs /flaky-coach disagreed on what's flaky)

`/failures` rendered *"Not a flake — flake detector found zero intermittents. Treat as a hard regression, not a re-run candidate."* for projects where `/flaky-coach` **did** list flaky tests. Root cause: two endpoints with two different flaky definitions. `/flaky-coach` (`test_health_coach_service`) has merged human-triaged `FLAKY_TEST` rows (from `/my-failures`) on top of auto-detection since 2026-05-18; `/failures` (`analytics_service.flaky_tests`) was auto-detect **only** (intermittent `test_case_history`, ≥3 runs, both pass+fail) and ignored manual triage entirely — so `flakyCount` came back 0 and the page declared a hard regression, contradicting `/flaky-coach`.

- `analytics_service.flaky_tests` now merges the same manually-triaged `FLAKY_TEST` fingerprints (same tenant + suite scoping; deduped by fingerprint with auto winning since it carries a real ratio; capped at `limit`). Each row is tagged `source` (`auto`/`manual`); manual entries use `failure_rate_pct=100` as the "human-flagged" marker, mirroring flaky-coach's `failure_rate=1.0`. Additive field — existing consumers (dashboard widgets) are unaffected.
- Frontend `FlakyTestItem` type gains optional `source`/`class_name` so the UI can render manual entries distinctly.
- **Multi-pass adversarial review fixes (UX):** the `/failures` Flakiness card no longer (a) renders manual entries as a misleading "100% flake" — they show a blue **"Flagged"** badge ("Manually triaged as flaky on /my-failures"); (b) tanks the *Flake-free* dimension score to 0 because of the 100 marker — manual entries are excluded from the measured score while still counting toward `flakyCount`/verdict; (c) describes manual entries as "intermittent pass/fail patterns" — the lede now describes each source present. Backend merge also skips NULL/empty fingerprints defensively.
- Tests: `backend/tests/regression/test_flaky_failures_consistency.py` (merge, auto-wins dedup, limit-skips-manual, empty-stays-empty, combined-limit, **tenant-scope pass-through, suite-filter pass-through, null-fingerprint skip**) + a `FailureAnalysisPage.test.tsx` case (verdict flips off "hard regression"; renders "Flagged" not "100% flake") + updated the existing `flaky_tests` service test for the new second query.
- **Known follow-ups (documented, not in this change):** (1) the auto-detector *band* still differs — `analytics_service` excludes <5%/>95% rates as deterministic while `test_health_coach_service` includes them; this only diverges at extreme tails needing many runs, and at >95% the `/failures` "hard regression" wording is arguably the more correct one, so aligning the cached quarantine detector is deferred for product sign-off. (2) `/flaky-coach` reads a project-scoped `FlakyCoachResult` cache and its endpoint takes no `suite_name`, so it can't be suite-filtered like `/failures` — a separate enhancement.
### Added (2026-06-08 — /agents: run/suite context on each pipeline card)

Each AI-pipeline card on `/agents` now shows **which run/suite it analysed** — previously it showed only a workflow type, so users couldn't tell what a pipeline was for. The `GET /api/v1/agents/pipelines` (+ `/pipelines/{id}`) responses gained `build_number`, `run_seq`, and `suite_name`, attached from the owning `TestRun` on the read path (`_attach_run_context`, in-place like `_apply_effective_status`); `run_seq` reuses the shared per-(project, primary_suite_name) "Run #N" numbering (`runs_service.fetch_run_seq_map`) so the label matches `/runs` and `/live`. The card renders `Run #N · <suite>`, falling back to `Build <n>` then a short run-id when `run_seq`/suite are absent (legacy rows stay `null`, never error). The TestRun lookup is bounded to the already-tenant-scoped pipeline ids. Tests: backend `_attach_run_context` (attach, legacy-null, run_seq-independent-of-suite, empty-no-query, TestRun-without-run_seq) + a schema-serialization test proving the non-mapped ORM attrs flow through `AgentPipelineResponse` (`from_attributes`) + two `AgentStatusPage` card-render cases. A 4-lens adversarial review (correctness / security / UX / coverage) found **no code defects** — security confirmed the context lookup is tenant-safe (bounded to scoped ids; `get_pipeline` checks access first); the only gaps were the extra tests now added (endpoint-level integration tests remain a CI follow-up).

### Changed (2026-06-08 — /agents: AI report expanded by default)

The `/agents` (AI Pipelines) page now shows the **AI report by default** once a pipeline run is selected — it's the headline output, so users no longer have to click "View AI report" to see it. `showSummary` defaults to `true` (and stays expanded when switching between runs); the toggle still collapses it ("Hide report"). The report is still lazy-fetched, now triggered as soon as a pipeline is picked. Tests: a new `AgentStatusPage.test.tsx` case asserts the report content renders without a click and the toggle reads "Hide report"; updated three existing tests that previously had to click "View AI report". (Per-run/suite *context* on each pipeline card is a follow-up in the same branch.)
### Fixed (2026-06-07 — Manual upload: MRU-14 review)

Review found 2 high + 1 low; fixed before they shipped:
- **Auto-detect no longer misses real CI reports:** the pytest sniffer required `"summary"`, which sits *after* pytest-json-report's unbounded `environment` block and is pushed past the 4 KB detection window on package-heavy CI images (→ silently mis-parsed as Allure → empty run). Now keys on `exitcode`+`root` alone (both top-of-doc + unique).
- **No more cross-file fingerprint collisions:** function/class tests folded the file only into `suite_name`, but the dedup fingerprint is `class_name::test_name` — so `test_a.py::test_smoke` and `test_b.py::test_smoke` collided and one silently overwrote the other. The file is now folded into `class_name` (matching the playwright/cypress parsers); `suite_name` stays the bare file for grouping.
- Parametrized nodeids with `::` inside the `[...]` id (`test_q[a::b]`) split correctly. Tests added for all three.

### Added (2026-06-07 — Manual upload: pytest-json-report parser (MRU-14))

- **Native `pytest --json-report` support.** A new `pytest_parser` ingests the pytest-json-report plugin's JSON: each `tests[]` entry's `nodeid` → suite (file) + class + name, `outcome` → status (passed→PASSED, failed→FAILED, error→BROKEN, skipped/xfailed→SKIPPED, xpassed→PASSED), duration = setup+call+teardown (s→ms), and the failing phase's `longrepr` → error message. Auto-detected by the distinctive top-level `exitcode`+`root`+`summary` markers; also selectable as "pytest JSON" in the modal and accepted via `format=pytest`. (pytest's JUnit XML continues to work via the JUnit parser.) Works inside a zip too (tier-2). Tests: parser status/nodeid/duration mapping, malformed→empty, and format detection.

### Fixed (2026-06-07 — Manual upload: MRU-15/16/17 review)

- **Failure metrics no longer undercount:** the retry-exhausted terminal (outer task handler) set a FAILED status but emitted no metric (the `_emit_failed` closure is scoped to the inner coroutine). It now emits `uploads_total{state=failed}` + `upload_failures_total{code=infra_error}` (distinct from the parse-time `ingest_error`), so the counters balance the terminal-status writes.
- Clarified `upload_processing_seconds` help (success-only by design) and added the backend tests the prior entry referenced (migration 0092 chain/downgrade + metrics label sets).

### Added (2026-06-07 — Manual upload: metrics, in-app help, rollout flag (MRU-15/16/17))

- **MRU-17 — rollout flag.** The upload UI (the `/runs` "Upload report" button, the sidebar "Upload Report" item, and the `/runs?upload=1` deep-link) is gated behind a new `manual_upload` feature flag (migration 0092, **default OFF**) — an ADMIN enables it per environment/project/role from Settings › Feature Flags, mirroring `cypress_ingest`/`playwright_ingest`. A new `useFeatureEnabled(key)` hook resolves the flag for the active project. The `POST /api/v1/ingest/file` endpoint itself is not gated (API/CI clients unaffected).
- **MRU-15 — metrics.** Prometheus counters/histogram for uploads: `testlookup_uploads_total{state,format}`, `testlookup_upload_failures_total{code}`, and `testlookup_upload_processing_seconds`, emitted at each worker terminal (succeeded / parse_error / empty_report / ingest_error / zip safety codes).
- **MRU-16 — in-app help.** The upload modal has a "Supported formats & how to export" expandable listing each framework's export (JUnit/TestNG XML, Allure JSON or zip, Playwright `--reporter=json`, Cypress Mochawesome, multi-file). (Kept in-app since `docs/` is gitignored.)
- Tests: Sidebar flag-gate (hidden off / shown on); migration 0092 chains + downgrade; metrics import.

### Added (2026-06-07 — Manual upload: raw archival + skip-AI toggle (MRU-9 / MRU-8))

- **MRU-9 — Raw uploaded files are archived** (byte-exact) to the storage backend under `uploads/{project_id}/{run_id}/{filename}` for audit/replay; the key is linked onto the run's `minio_prefix`. Best-effort — a storage hiccup never fails the ingest.
- **MRU-8 — "Skip AI analysis" toggle** in the upload modal. When checked, `finalize_run(run_ai=False)` skips the agent-pipeline enqueue (faster ingest, no LLM cost) while everything else (clustering inputs, owners, aggregates) still runs; the user can trigger AI later from the run page. `run_ai` defaults to true and threads endpoint → task → `finalize_run` (the SDK-batch and webhook paths are unaffected — they keep the default).
- Tests: router asserts `run_ai` flows + the raw file is archived (`put_object` called, key passed); service asserts the `run_ai` field; modal toggle wired.

### Added (2026-06-07 — Manual upload: multi-file selection (MRU-13))

- **Upload several report files at once.** The modal now accepts multiple files; when more than one is selected they're **zipped client-side** (via `fflate`) into a single `reports-bundle.zip` and sent through the existing archive path (the backend tier-2 detects + parses each entry), so "N JUnit XMLs" or a mixed set ingest as one run. A single file still uploads as-is. Duplicate filenames in a bundle are de-duplicated; total selection is size-capped client-side. Test: selecting 2 files produces one `application/zip` upload.

### Fixed (2026-06-06 — Manual upload: MRU-12 review hardening)

Multi-pass review (3 lenses, adversarially verified) of the zip wiring found 9 issues; the high + quick wins are fixed:

- **Closed a feature-flag bypass** (was: high — all 3 review highs). Zipping a Cypress/Playwright report skipped the admin-gated `cypress_ingest`/`playwright_ingest` flag (the router only gated single files). The router now resolves the disabled gated formats and passes `disabled_formats` to the worker, which **skips matching tier-2 entries** — a zip can't re-enable a disabled parser. Covered by a test (gated → skipped, allowed → parsed) and a parametrized router test (`format=cypress` + zip → `archive`, never 503).
- **Fixed a latent crash** — several worker `logger.warning(..., key=val)` calls used structlog-style kwargs, but `worker/tasks.py`'s `logger` is **stdlib** (`%s` positional), so the parse-error/archive paths would have raised `TypeError` mid-handler. Converted to `%s` style.
- Freed the compressed `raw` bytes after extraction (memory peak); added tests for noise-only zip → empty, and `UnsafeZipError` propagating through the `archive` dispatch.

### Added (2026-06-06 — Manual upload: Allure-zip ingestion wired (MRU-12))

- **Upload an Allure results ZIP (or a zip of several reports) from the UI.** The endpoint now detects a zip by `PK` magic / `.zip` **before** the utf-8 decode (binary-safe), base64-encodes it for the Celery JSON transport, and dispatches `file_format="archive"`. The worker's `_parse_archive_to_results` safe-extracts in memory (the MRU-11 `safe_extract_zip` with config-driven limits) then dispatches **tier-1** (any `*-result.json` → `parse_allure_zip`) or **tier-2** (heterogeneous, e.g. N JUnit XMLs → per-entry detect + existing parsers), filtering `__MACOSX`/dotfile noise. A zip-safety violation surfaces as the specific `error.code` (`zip_bomb`/`unsafe_path`/…) in the upload status, not a generic failure.
- Archive limits are configurable: `MAX_ARCHIVE_UNCOMPRESSED_BYTES` (200 MB), `MAX_ARCHIVE_ENTRIES` (5 000), `MAX_ARCHIVE_ENTRY_BYTES` (50 MB), `MAX_ARCHIVE_RATIO` (100×). The modal now accepts `.zip`. Tests: `test_archive_upload.py` (tier-1/tier-2 dispatch, noise filtering, zip-bomb propagation) + a router test asserting zip → `archive` + base64. Green on py3.11.

### Added (2026-06-06 — Manual upload: Allure-zip spike PoC (MRU-11))

- **Spike for Allure ZIP upload resolved (GO)** with a proof-of-concept (not yet wired to the endpoint — that's MRU-12). `app/services/safe_archive.py` adds a hardened, in-memory `safe_extract_zip` that enforces concrete limits — 200 MB uncompressed total, 5 000 entries, 50 MB/entry, 100× ratio — and rejects path traversal/absolute/UNC, symlinks, and nested archives, streaming each entry with a running byte cap (never trusting `ZipInfo.file_size`); each violation raises `UnsafeZipError(code)` (`zip_bomb`/`zip_too_large`/`too_many_entries`/`unsafe_path`/`nested_zip`/`bad_zip`).
- `allure_parser.parse_allure_zip(files, run_id, s3_prefix)` reuses `parse_allure_result` per `*-result.json`, backfills `suite_name` from `*-container.json` hierarchy (only when no suite label), and collapses retries by `historyId` to the latest attempt with `is_flaky`/`retry_count`. Decision note + concrete limits table recorded in `docs/PRD-manual-report-upload.md` §9.1. Tests: `test_allure_zip_spike.py` (12 — parse + every modelled attack). Green on py3.11.

### Fixed (2026-06-06 — Manual upload: MRU-5/6 review hardening)

Multi-pass review (4 lenses, adversarially verified) of the status/parse-error slice found 15 issues; the highs/mediums + quick wins are fixed:

- **Success count is no longer always "0 passed · 0 failed"** (was: high). The per-status summary compared UPPERCASE while the JUnit/TestNG/Allure parsers emit lowercase — fixed with a case-insensitive `_summarize_upload` helper. `total` now reflects rows **actually ingested** (not parsed), and an all-rows-failed ingest reports `failed` instead of "succeeded, 0 tests".
- **The status endpoint's IDOR/auth surface is now tested** (was: high coverage gap) — 404 unknown, 403 cross-project, 200 member (+ asserts `project_id` isn't leaked in the response).
- **No spurious 403 on the status poll** — the worker is now enqueued with the canonical UUID so its status writes match the seeded `pending` record and the project-scoped-key check.
- **`get_status` is now best-effort** (symmetric with `set_status`): a Redis outage degrades to a clean 404/"still processing" instead of a 500; non-dict payloads return None.
- **Frontend poll** now treats a 403 as a real error (stops, surfaces it) instead of masking it as success after timeout, and the ~60s timeout fallback renders a neutral "still processing" (clock) state rather than a green check.
- Annotation fix on the new endpoint (`tuple[User, uuid.UUID | None]`). Tests added: casing/total summary, malformed-Allure-raises, endpoint 404/403/200.

### Added (2026-06-06 — Manual upload: async status + parse-error feedback)

- **Upload no longer fails silently** (PRD MRU-5/6). A new Redis-backed status record (`app/services/upload_status.py`, 24h TTL) is updated by the `ingest_uploaded_file` task at each stage (`pending → parsing → ingesting → succeeded | failed`) and exposed via **`GET /api/v1/ingest/uploads/{task_id}`** (project-scoped — 404 if unknown/expired, 403 if the caller can't access the run's project, so a guessed task_id can't leak another tenant's run).
- **Parse failures and empty reports are now surfaced, not swallowed.** A parser exception → `failed` with `error.code=parse_error`; a valid-but-zero-tests file → `failed` with `empty_report`; malformed Allure JSON now raises instead of silently producing an empty run. Parse/empty failures are **not retried** (the file won't parse on retry); only transient infra errors retry, surfacing `ingest_error` once exhausted.
- **The upload modal polls the status** after the 202 and shows a real outcome: a "Processing…" spinner, then **"Processed N tests (P passed, F failed)"** with **View run**, or the parse error inline with retry. Falls back to "still processing" after ~60s.
- Tests: `test_upload_status.py` (roundtrip, TTL, missing→None, error-swallowing) + modal polling success/parse-failure. All green on the py3.11 venv.

### Fixed (2026-06-06 — Manual upload: review-driven hardening + collision policy)

A multi-pass review (5 lenses, adversarially verified) of the upload feature surfaced 16 confirmed issues; the highs/mediums + quick wins are fixed here (pulling MRU-7's collision policy forward):

- **Uploads no longer merge into an unrelated run on a build-label collision** (was: high — silent data corruption). `create_run_from_payload` gained `reuse_existing` (default True keeps SDK/CI retry-idempotency); the manual-upload task passes `reuse_existing=False`, so an upload **always creates a fresh run**, auto-suffixing the build label (`-2`, `-3`, … then a random token) via `_unique_build_number` when it collides. This also makes the 202 `run_id` authoritative, fixing the **"View run" → 404** where a merged run discarded the response id.
- **SDK/CI reuse no longer risks clobbering `ingestion_source`** — the reuse branch returns the existing run untouched (pinned by a new test: a `live` run reused by an `sdk` ingest stays `live`).
- **Default build label is now millisecond + random** (was per-second), so back-to-back / concurrent blank-label uploads don't collide.
- **Modal: a rejected file now clears any prior valid selection** (was: could submit a stale file under a new file's error) and resets the input; **422 `detail` arrays no longer render as `[object Object]`** (string-guarded); **Space on the dropzone no longer scrolls the modal** (`preventDefault`).
- Tests: backend collision/reuse/suffix cases + `UploadReportModal.test.tsx` (gating, validation rejection + clear, success → `onSuccess`). All green on the py3.11 venv.

### Added (2026-06-06 — Manual report upload UI)

- **Upload a test report from the UI** (PRD MRU-4). New `UploadReportModal` + `reportUploadService` wire the existing `POST /api/v1/ingest/file` endpoint to a drag-and-drop modal: pick a JUnit/TestNG `.xml` or Allure/Playwright/Cypress `.json` file, choose a format (default auto-detect), optionally set build label / release / branch / commit, and upload with a live progress bar. On accept (202) the user is deep-linked to the new run; uploaded runs carry `ingestion_source='upload'` and show the "Uploaded" badge. Client-side guards: 50 MB cap (matches backend), extension allow-list, and All-Projects mode disables upload (a run must target one project). Entry points: an "Upload report" button on `/runs` and an "Upload Report" item in the Testing sidebar group (`/runs?upload=1` deep-link). Gated to QA-engineer+. Errors (incl. a 503 for a disabled Cypress/Playwright feature flag) surface inline. Async parse status feedback is the next slice (MRU-5).

### Added (2026-06-06 — Run source tracking for manual report upload)

- **`TestRun.ingestion_source`** (`live | sdk | upload | file | unknown`) records how a run's results entered TestLookup (migration `0091`, new `IngestionSource` enum). It's set at every run-creation site — live-stream stub/upsert/drainer/persist → `live`, SDK batch (`/api/v1/ingest`) → `sdk`, manual file upload (`/api/v1/ingest/file`) → `upload`, MinIO/sentinel webhook → `file` — and `create_run_from_payload` now threads a caller-supplied `ingestion_source`. Existing rows are backfilled by a best-effort heuristic (event_archive/live_stream → `live`; trigger_source `api` → `sdk`; minio_prefix → `file`; else `unknown`). The column is `NOT NULL` with `server_default='unknown'`; downgrade drops it.
- **API + UI expose the source:** `TestRunSummary` carries `ingestion_source`, and the `/runs` table renders an **"Uploaded"** badge for `ingestion_source='upload'`. This is the first slice of the Manual Test Report Upload feature (see `docs/PRD-manual-report-upload.md`, tickets MRU-1/MRU-2/MRU-3); the backend file-ingestion path + parsers already existed and are unchanged.

### Added (2026-04-25/26 — Phase OS-Deploy)

- **Multi-cloud Kubernetes overlays** -- `k8s/overlays/{aws-eks,gcp-gke,azure-aks,self-hosted}/` cover the four major deployment targets. All inherit the existing `prod` overlay so HPA tuning and CORS config stay shared; each only patches what's actually cloud-specific (Ingress class, StorageClass, image registry).
- **Single-command multi-cloud deploy script** -- `scripts/deploy-k8s.sh --cloud=<aws|gcp|azure|self-hosted> [--registry --image-tag --dry-run]` validates kubectl context, pre-checks the `testlookup-secrets` Secret with a copy-paste-ready create command, supports image rewrite via `kustomize edit set image`, and blocks on backend + frontend rollout.
- **Deployment guides** -- `docs/deployment/README.md` (index + decision matrix) plus `aws-eks.md`, `gcp-gke.md`, `azure-aks.md`, `self-hosted-k8s.md` -- end-to-end walkthroughs covering cluster bootstrap, registry push, managed-services choice, secrets, deploy, DNS, verify, cleanup, and a per-cloud common-issues section.
- **WebSocket authentication handshake** -- `/ws/live/{project_id}` now requires `{"type":"auth","token":"<JWT>"}` within 10 seconds of connect. Uses the same `decode_token` + `get_accessible_project_ids` chain as REST, so WS and HTTP share one membership truth.
- **Per-connection audit trail** -- `ws_connect` and `ws_disconnect` events flow into the existing `access_audit_logs` table with reason codes (`client_disconnect`, `token_expired`, `refresh_failed`, etc.). Audit failures never break the WebSocket.
- **In-place WS token refresh** -- clients can send `{"type":"refresh","token":"<new>"}` to extend the session without dropping the connection. Server replies `{"type":"refreshed","exp":<epoch>}`. Avoids the 5-second reconnect gap on token rotation.
- **Project-existence guard on entity creation** -- `create_managed_test_case` and `create_test_plan` now verify the project exists before insert and return a clean `404` instead of letting `asyncpg.ForeignKeyViolationError` bubble up as an opaque `500`. Pattern can be applied to other entity-create services.

### Changed (2026-04-25/26 — Phase OS-Deploy)

- **Empty-state synthetic workflow stages now render as `'skipped'`** with a `skipped_reason` (was `'pending'`, which looked active). Affects `/runs`, `/intelligence`, `/failures`, `/coverage`, `/overview`. Also fixed `coverage_risk` misuse of `'running'` (showed a spinner when failing suites existed even though no work was running).
- **Frontend project store self-heals** -- `refreshProjects()` validates the persisted `activeProjectId` against the fresh project list and clears stale IDs. Prevents cross-deployment localStorage from making every mutation 500.
- **Vite dev-server proxy is in-container friendly** -- `/api`, `/webhooks`, `/ws` now read `process.env.VITE_PROXY_TARGET` (defaults to `localhost:8000`). Docker Compose sets it to `http://backend:8000` so the browser-host can reach the in-network backend.
- **Frontend bundle uses same-origin URLs in dev** -- `VITE_API_BASE_URL=` (empty) so the bundle uses relative paths through the working Vite proxy. Eliminates cross-origin CORS/CSP issues.
- **`Makefile` pins `SHELL := bash`** -- prevents `make` from silently falling back to `cmd.exe` on Windows, which can't parse `until`/`do`/`done` in the demo target's health-wait loop.
- **`live.router` moved to `PUBLIC_ROUTERS`** -- the WebSocket scope can't run HTTP-only `OAuth2PasswordBearer`, so the auto-applied protected-router auth crashed every connect. WS now does its own auth via the handshake; the router's HTTP route still has its own `verify_webhook_secret`.
- **Frontend live-execution hook holds token in a ref** -- token rotation no longer recreates the connect callback (which would tear down the socket). New effect sends a `refresh` frame to the open WS instead.
- **`CLAUDE.md` cleanup** -- fixed stale MCP/CLI counts (24 → 48 tools), slimmed Coding Conventions to 9 cross-cutting rules (was 24+7 bullets duplicated from subdir CLAUDE.md files), trimmed Known Pitfalls 31 → 14, replaced static feature-flag table with pointer to live inventory. 324 → 267 lines.
- **Homelab K3s overlay** -- now ships `netpol-homelab.yaml` (allows colocated data stores + Traefik ingress) and `frontend-nginx-configmap.yaml` (pre-rendered nginx config, mounted via subPath, with `command: [nginx, -g, "daemon off;"]` to bypass `/docker-entrypoint.sh`).

### Fixed (2026-06-06 — BUG-001 ChromaDB telemetry log spam)

- **ChromaDB anonymous telemetry flooded the AI-worker logs** with `Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given` (~7x per pipeline). The prior attempt — `os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")` plus the k8s configmap `ANONYMIZED_TELEMETRY: "False"` — was **insufficient**: the env var was verified present in the worker pod (`printenv` → `False`) yet chromadb 0.5.20's `HttpClient` ignores it and still emits the telemetry error. Authoritative fix: a shared `app/db/chroma.get_chroma_client()` helper that passes an explicit `Settings(anonymized_telemetry=False)` to every client. All 8 `chromadb.HttpClient(...)` call sites (agent_memory_service, semantic_cache, semantic_search, knowledge_chunking_service, defect_promotion_service, conversation agent, embed_and_cluster tool) now route through it. The env `setdefault` is retained in `config.py` as a harmless additional safeguard. Regression test: `tests/regression/test_chromadb_client_telemetry_off.py`.

### Fixed (2026-04-25/26 — Phase OS-Deploy)

- **WebSocket connection failed immediately after handshake** -- `OAuth2PasswordBearer.__call__() missing 1 required positional argument: 'request'` from chained dependencies. Root cause: HTTP-only auth dep applied to a WebSocket route at the router level.
- **`POST /api/v1/test-management/cases` returned 500** for stale `project_id` from cross-deployment localStorage -- now a clean 404 plus the frontend self-heals to prevent recurrence.
- **`GET /api/v1/analytics/defects` returned 500** -- earlier image had stale `tr.release_name` SQL; the source already had the fix (`r.name AS release_name` joining `releases r`), but the deployed image predated it. Documented the fix-and-redeploy flow in `homelabsetup/DEPLOY_TESTLOOKUP.md` with the K3s `:latest` cache trap and the `docker save | k3s ctr images import` workaround.
- **Frontend `nginx:alpine` failed to start** with `mkdir(/var/cache/nginx/client_temp) Permission denied` -- the homelab overlay needed `runAsUser: 0` AND default capabilities (dropping all caps blocks `chown` even as root). Fixed via JSON-patch `op: replace` on the full securityContext.
- **Backend `Connection refused` to colocated PostgreSQL** in homelab K3s -- `default-deny-all` NetworkPolicy blocked ingress to data stores. Now patched via `netpol-homelab.yaml` with `allow-data-stores`.
- **Traefik couldn't reach frontend on K3s** -- base `allow-frontend` NetworkPolicy referenced `ingress-nginx` namespace, but K3s uses Traefik in `kube-system`. Added `allow-traefik-ingress` to the homelab overlay.
- **Local Compose frontend showed "500 Internal Server Error" on every page** -- bundle was hardcoded to `http://localhost:8000` and CSP blocked it; some requests fell back to the Vite proxy at `localhost:3000`, which couldn't reach `localhost:8000` from inside the container. Now both paths converge: empty `VITE_API_BASE_URL` → same-origin → Vite proxy → `backend:8000`.

### Security (2026-04-25/26 — Phase OS-Deploy)

- WebSocket connections now require an authenticated user with project membership -- previously the `/ws/live/{project_id}` endpoint accepted anonymous connections and the in-flow auth message was never validated.
- WebSocket sessions enforce JWT expiry mid-connection (5-second grace) -- long-lived sockets no longer outlive their access token.
- Per-connection audit trail (`access_audit_logs`) lets compliance reports answer "who connected to which project channel, when, and why did it disconnect."

### Performance (2026-06-03 — Phase AUTO, autonomous loop)

Changes below are produced by the autonomous hourly performance loop (one focused, reviewed
branch per fix; see the per-entry branch for the full diff + regression test).

- **`agent_cost_service.check_alerts` N+1 removed** (`auto/perf-20260603-0745`) — the
  consecutive-failure check fired one `COUNT` query per failed stage in a pipeline; it now
  batches all per-stage 24h failure counts into a single `GROUP BY` query. Constant DB
  round-trips regardless of failed-stage count; alert output unchanged. Pinned by
  `tests/regression/test_agent_cost_check_alerts_no_n_plus_1.py`.
- **MinIO/sentinel ingest N+1 removed** (`auto/perf-20260603-0830`) — `ingestion.process_sentinel`
  upserted parsed cases without prefetching, so `_upsert_test_case` issued one SELECT per case
  (a 1000-test upload = 1000 extra round trips). It now prefetches existing rows in a single
  batched query and passes `existing`/`fingerprint` through, mirroring
  `ingestion_pipeline.ingest_test_results`. Pinned by
  `tests/regression/test_ingestion_sentinel_prefetch_no_n_plus_1.py`.
- **`run_diff_service.get_baseline_diff` redundant query removed** (`auto/perf-20260603-1531`) —
  it ran two SELECTs against `test_cases` with identical WHERE clauses for the baseline run's
  failures (one projecting `test_fingerprint`, one projecting `fingerprint`+`name`). Now fetches
  both columns once and reuses the rows for new-failure detection and resolved-failures — one
  fewer round trip per baseline diff; output unchanged. Pinned by
  `tests/regression/test_run_diff_baseline_single_fetch.py`.
- **`ai_eval_service.compute_agreement_rate` 3 COUNTs → 1** (`auto/perf-20260603-1830`) — it ran
  three sequential COUNT queries over the same `created_at >= cutoff` window (total / correct /
  partially_correct). Now one aggregate query with conditional counts
  (`count(case((rating == X, 1)))`); 3 round trips → 1, output identical. Pinned by
  `tests/regression/test_ai_eval_agreement_single_query.py`.
- **`release_service.update_phase` all-phases-done check → COUNT** (`auto/perf-20260603-1837`) —
  it fetched every `ReleasePhase` row for the release and scanned in Python
  (`all(p.status in ('completed','skipped'))`) on each phase update. Now a single COUNT of
  NOT-done phases (`status NOT IN ('completed','skipped') OR status IS NULL`) — `all_done` is
  True iff the count is 0. O(N) row fetch → O(1) aggregate; behavior identical incl. the
  NULL-status-is-incomplete and zero-phases (`all([]) is True`) edges. Pinned by
  `tests/regression/test_release_phase_all_done_count.py`.
- **`audit_dashboard_service.get_tenant_observability` merges failed-runs COUNT** (`auto/perf-20260603-2018`)
  — it ran a separate `COUNT WHERE status='FAILED'` in addition to the total/avg/sum aggregate
  over the same `(project_id, created_at >= cutoff)` window. The FAILED count is now a conditional
  `count(...).filter(status == 'FAILED')` in that same query; the function drops from 5 DB round
  trips to 4 with identical output (FILTER excludes NULL status just as the `status == 'FAILED'`
  WHERE did). Pinned by `tests/regression/test_audit_observability_single_runs_query.py`.
- **`test_management_service.recompute_plan_counts` aggregates in SQL** (`auto/perf-20260603-2120`)
  — it materialised every `TestPlanItem` row and ran five Python passes (len + 4 conditional
  sums). Now one aggregate query with conditional counts. ``executed`` is derived as
  ``total - count(status == 'not_run')`` (not `count(status != 'not_run')`) so a NULL
  `execution_status` counts as executed, exactly matching the original
  ``status not in ('not_run',)`` (the column is nullable); passed/failed/blocked use `== X`,
  which excludes NULL in both. Constant one round trip, no row materialisation. Pinned by
  `tests/regression/test_plan_counts_aggregate_query.py`.
- **`retro_digest_service._count_new_regressions` counts in SQL** (`auto/perf-20260603-2212`)
  — the current-week regression count fetched the distinct fingerprints
  (`SELECT DISTINCT test_fingerprint`) and did `len({row[0] ... if row[0]})` in Python. Now a
  `COUNT(DISTINCT test_fingerprint)` consumed via `.scalar()` — no row transfer / Python dedup.
  Identical result: the IN-list (`prev_passed_fps`) already holds only truthy fingerprints and
  COUNT(DISTINCT) skips NULL, so the `if row[0]` filter was redundant. Pinned by
  `tests/regression/test_retro_count_new_regressions_scalar.py`.
- **`feedback_service` total/unexported COUNTs collapsed** (`auto/perf-20260603-2300`) —
  `get_feedback_stats` ran 3 COUNTs on AIFeedback (group-by ratings, total, unexported) and
  `get_training_status` ran 2 (unexported, total). The total + unexported pair is now one
  aggregate query with a conditional `count(...).filter(exported.is_(False))`: get_feedback_stats
  3→2 round trips, get_training_status 2→1. Identical output — `FILTER (exported IS FALSE)`
  matches the prior `WHERE exported.is_(False)` (NULL excluded by both). Pinned by
  `tests/regression/test_feedback_stats_single_aggregate.py`.

### Performance (2026-06-03 — query batching, human-directed)

- **`perf_regression_service.refresh_baselines` per-row baseline SELECT batched**
  (`perf/refresh-baselines-batch`) — the nightly sweep called `record_observation` per swept
  `TestCase` row, each issuing a `SELECT PerfBaseline WHERE (project_id, test_fingerprint)`
  (`1 + N` queries, up to `_REFRESH_BATCH_SIZE` rows). It now prefetches the batch's baselines in
  one `IN`×`IN` query and passes each through `record_observation(existing=...)`, caching the
  returned (new or existing) baseline per pair. Behavior-preserving incl. the critical
  repeated-`(project, fingerprint)` case: multiple rows for the same test still accumulate into
  ONE baseline via Welford (the cache stands in for the old autoflush-visible just-created row),
  so no duplicate row / `uq_perf_baseline_fingerprint` violation. Plain `IN` (no window /
  composite-IN). Pinned by `tests/regression/test_refresh_baselines_batch.py` (repeated-fp → one
  baseline n=2; existing reused not recreated; sweep + one prefetch only).
- **Two perf indexes added (migration 0090)** (`perf/index-agent-stage-results`) —
  `ix_agent_stage_results_stage_status` on `agent_stage_results (stage_name, status)` (backs the
  24h repeated-failure count in `agent_cost_service.check_alerts`) and `ix_test_cases_run_suite`
  on `test_cases (test_run_id, suite_name)` (backs run-scoped suite-breakdown reads —
  coverage/summary/suite_history — which previously had only a GIN trigram index on `suite_name`).
  Both built `CREATE INDEX CONCURRENTLY` + `IF NOT EXISTS` (the migration-0082 pattern) so no
  write lock on the hot `test_cases` table. The analyst's headline `agent_stage_results
  (pipeline_run_id)` finding was a false positive — that index already exists (`ix_stage_results_pipeline`,
  migration 0004); it was just absent from the ORM `__table_args__`, now declared for parity.
  Pinned by `tests/test_migration_0090_indexes.py`.
- **`suite_sync_service` per-suite query fan-out collapsed** (`perf/suite-sync-batch-membership`)
  — `sync_suite_membership` called `_sync_one_suite` per suite, and each call issued 3 SELECTs
  (managed cases, existing memberships, `<suite>-deleted` bucket), i.e. `1 + 3N` queries on the
  ingestion critical path. The three lookups are now batched across all suites in one query each
  (`4` total, constant in suite count) and sliced per suite; `_sync_one_suite` performs no DB
  reads. Behavior-preserving — suites are processed independently (disjoint `suite_name` /
  `<suite>-deleted` rows), so prefetching the whole set up front matches the prior sequential
  fetches. Pinned by `tests/regression/test_suite_sync_batched_queries.py` (constant 4 round
  trips for N suites + add/unchanged/delete behavior across a multi-suite run).
- **`flaky_quarantine_service.run_recheck_cycle` per-row count batched** (`perf/run-recheck-batch-counts`)
  — the recheck beat task issued one grouped `COUNT` query per `RECHECK_SCHEDULED` row (N
  queries) to compute each test's post-quarantine flip rate. The counts are now computed in a
  single grouped query that OR-chains per-`(project, fingerprint, since)` conditions and groups
  by `(project, fingerprint, status)`, mapping results back per row. Each row's individual
  `since` cutoff and the per-project scope are preserved; the partial unique index
  `ux_fqr_live_per_fingerprint` guarantees `(project, fingerprint)` is unique among live rows so
  the mapping is exact. Shorter DB-session hold on the beat worker. Pinned by
  `tests/regression/test_flaky_quarantine_recheck_project_scope.py` (one batched count for N
  rows; release/re-quarantine/insufficient decisions unchanged; query still project-scoped).
- **`test_health_coach_service.refresh_flaky_coach` per-fingerprint N+1 batched** (`perf/flaky-coach-batch`)
  — the flaky-coach refresh (request path: `POST /flaky-coach/refresh`, plus a scheduled task)
  ran two queries per candidate fingerprint (`status_q` top-30 history + `tc_q` latest name), i.e.
  `1 + 1 + 2N`. The per-fingerprint lookups are now two batched queries: a windowed
  `ROW_NUMBER() OVER (PARTITION BY test_fingerprint ORDER BY created_at DESC) <= 30` (preserves
  the per-row top-30 that drives `failure_rate`, `flaky_since`/`last_failure_at`, and
  `status_history[:10]`) and a `DISTINCT ON (test_fingerprint)` for the latest name/suite. Round
  trips drop to a constant `4`; classification unchanged. Both keep the project scope. Pinned by
  `tests/regression/test_flaky_coach_batched_queries.py` (constant 4 round trips + failure-rate /
  status-history-order / flaky-since / both-pass-and-fail-filter behavior).

### Security (2026-06-03)

- **Tier-3 hardening — auth refresh rate-limit, allowlist validation, CORS wildcard warning**
  (`sec/tier3-hardening`, audit items S7/S8/S11) — three small defense-in-depth fixes:
  - **S7:** added `/api/v1/auth/refresh` to the `_AUTH_RATE_LIMITS` middleware (30/min prod) — the
    token-mint endpoint was previously unthrottled (refresh-token grinding / token amplification).
    `/login` (10/min) and `/register` (5/min) were already covered.
  - **S8:** `KnowledgeDomainAllowlistUpdate.domains` now validates each entry is a real FQDN —
    rejects wildcards (`*`), schemes, ports, paths, and IP literals. The allowlist is the domain
    gate that complements the S1 SSRF guard, and the `hostname == d or endswith('.'+d)` matcher
    never honoured those forms anyway, so rejecting them is behaviour-preserving. Empty list still
    clears the allowlist (permissive), preserving existing semantics.
  - **S11:** `Settings.validate_production_secrets()` now emits a startup WARNING when
    `CORS_ORIGINS` contains a wildcard in production/staging (credentialed any-origin access).
    `CORS_ORIGINS` never defaults to `*`, so this only fires on explicit operator misconfig.
  - **Verified no-change:** S9 (ingest `status` already constrained by a regex pattern) and S10
    (debug router already ADMIN-gated, no secret-bearing Celery args).
  Regression: `tests/regression/test_tier3_hardening.py`.
- **SSRF guard on the URL knowledge connector** (`sec/url-connector-ssrf-guard`, audit item S1) —
  `connectors/url_connector.fetch_content` validated only the URL *scheme* and an opt-in
  knowledge-source domain allowlist; neither blocks a host that resolves to a private / loopback /
  link-local / cloud-metadata address. A QA_ENGINEER+ creating an `external_url` knowledge source
  could point sync at `http://169.254.169.254/...` (cloud metadata), `http://127.0.0.1:6379`
  (Redis), or any RFC1918 host — a server-side fetch + response-exfiltration SSRF. Extracted the
  existing webhook SSRF check into a shared `services/url_safety.is_safe_public_url` (resolve-then-
  classify; unresolvable hosts allowed since they aren't reachable) and applied it in the connector
  on the **initial URL and on every redirect hop** — auto-redirect following is now disabled and
  the chain is walked by hand so a public host can't 30x the fetch into a private target. Redirect
  cap unchanged (5). `webhook_service` now imports the shared guard (behaviour identical; the
  `_is_safe_public_url` name is retained as an alias). Regression pins:
  `tests/regression/test_url_connector_ssrf_guard.py` (private/metadata/loopback initial target
  blocked with no GET; public→private redirect blocked on the hop; scheme block still first;
  public single-hop still fetches).
- **Tenant-scoped `GET /api/v1/agents/active-runs`** (`sec/audit-remediation-2026-06`, audit item
  S2 — IDOR) — the active-runs *list* endpoint had only `Depends(get_current_active_user)` and
  returned `RedisLiveRunState.get_all_active()` verbatim, so any authenticated user (even a VIEWER
  in one project) saw every other project's live runs: slug, build number, pass/fail counts,
  timing, `project_id`. The `/active-runs/{run_id}` sibling already gated on `require_run_access`;
  the list did not. Now filtered by `get_accessible_project_ids` (ADMIN → unrestricted) using the
  `project_id` already on each Redis state row — in-memory, no DB round trip; rows without a
  resolvable project are dropped for non-admins. Regression:
  `tests/regression/test_active_runs_idor.py`.
- **PII redaction in the reasoning-track fine-tune export** (`sec/audit-remediation-2026-06`, audit
  item S3) — `training/exporter._export_reasoning` copied raw Mongo ReAct traces (`prompt` +
  `intermediate_steps` + `analysis`) straight into the MinIO fine-tune corpus with no redaction;
  those traces carry test names, stack traces, env URLs, emails, and secrets. Every free-text field
  on a reasoning example now passes through `privacy_service.sanitize_for_persistence` (the
  `[REDACTED]` boundary) — the prompt, each ReAct step in `_format_reasoning_chain`, and the
  serialized analysis payload. Idempotent; structured labels (e.g. `PRODUCT_BUG`) are preserved.
  Regression: `tests/regression/test_training_exporter_pii_redaction.py`.
- **Deferred — S6 (connector base-URL SSRF):** verified and intentionally NOT applied. Jira /
  Confluence base URLs come from `JIRA_DOMAIN` / `CONFLUENCE_DOMAIN`, settable only by ADMIN
  (`PUT /api/v1/settings/integrations`, `require_role(ADMIN)`) — an ADMIN trust boundary, so SSRF
  exploitability is negligible. More importantly, a private-range block would break legitimate
  on-prem Jira/Confluence **Server** deployments (which routinely run on private IPs). Left as a
  documented non-action rather than a regression.
- **Input-length caps on persisted request models** (`sec/input-length-caps`, audit item S4 — OWASP
  A03) — several `*Create`/`*Update` Pydantic models accepted arbitrarily long strings for fields
  written to the DB, so a QA_ENGINEER+ could POST a multi-MB/GB value (test-case body, plan /
  strategy free-text, descriptions) and exhaust memory / DB write capacity; `UserCreate.password`
  was the sharpest — an unbounded password is hashed on the bcrypt path (CPU/memory DoS). Added
  `Field(max_length=...)`: long-form `Text`-backed fields use a shared `MAX_LONG_TEXT` (50 000 —
  generous, so realistic content is never rejected); `String(N)`-backed fields match `N` exactly
  (an over-long value now returns a clean 422 instead of a DB-overflow 500); `password` capped at
  128. Covers `ManagedTestCase`, `TestPlan`, `TestStrategy`, `TestSuite`, `TestCaseComment`,
  `ChatSession`, `QualityGate`, `ReleaseGatePolicy`, `SavedView`, `AIEvalDataset`, `KnowledgeSource`,
  `UserCreate`. Only `max_length` was added (no new `min_length`), so the change is
  behaviour-preserving. Regression: `tests/regression/test_input_length_caps.py`.

### Fixed (2026-06-04 — RAG/knowledge service stubs restore)

- **`rag_generation_service` + `knowledge_sync_service` clobbered to stubs — RAG generation
  and knowledge sync disabled, 47 backend tests red** (`fix/restore-rag-knowledge-services`) —
  commit `51dd4bc` ("Fix structlog logger calls to use keyword args") replaced both full
  modules with ~20-line placeholder stubs (rag 383→21 lines, knowledge_sync 424→35),
  deleting `grounded_generate`, `_build_grounded_prompt`, `_stub_generated_cases`,
  `_build_citations`, `_call_llm_generate`, `_map_coverage`, `MAX_CITATIONS_PER_CASE`,
  `GroundedGenerationResult` (rag) and `get_connector`, `compute_staleness`,
  `_effective_threshold` (knowledge_sync). Same incident class as the `secret_service`
  clobber below. Restored the full modules from their last-good commits (`0804a22` /
  `e0dc759`) and re-applied the intended structlog `%s`→kwargs cleanup the bad commit was
  supposed to do (9 positional calls across the two files, plus 4 in
  `knowledge_chunking_service.py:305/307/327/349` that were also raising
  `BoundLoggerBase._proxy_to_logger()` TypeErrors). Reconciled five stale tests against
  evolved code: secret-mask format now `****{last2}` (hardened in `e0dc759`/`e0d4fea`,
  secrets-at-rest review — tests updated, impl unchanged); `get_pipeline_timeline()` param
  `_`→`current_user` + new IDOR access gate; `_upsert_test_case` history-dedup probe changed
  the prefetch-path execute count (1 history vs 2 for the legacy lookup+history path);
  suite-trend routing pin granted project access for its random `project_id` (post-IDOR gate).
  Regression coverage: `test_rag_services.py` + `test_rag_generation.py` +
  `test_knowledge_sync_offline_gate.py` (158 pass locally).

### Fixed (2026-06-03)

- **`secret_service` clobbered to a stub — whole-app import break (CI exit 2)** (`fix/secret-service-restore`) —
  commit `e6c0348` ("Align secret masking implementation with tests") replaced the entire
  `services/secret_service.py` module with a masking-only stub, deleting `store_secret`,
  `read_secret`, `has_secret`, `get_masked`, `extract_secrets_from_config`,
  `strip_secrets_from_config`, `is_secret_field`, `SECRET_FIELDS`, and the Fernet encryption
  helpers, and renaming `mask_value` → `mask_secret`/`mask_api_key`. But `routers/app_settings.py`
  imports those names at module top, and `bootstrap.py` imports `app_settings`, so the **whole app
  failed to import** — pytest collection errored on `test_architectural_authorization`,
  `test_llm_connectivity`, `test_route_ordering` (CI exit 2) and the app couldn't start. (The stub's
  `mask_secret`/`mask_api_key` were dead code — no caller or test referenced them; the tests import
  `mask_value`.) Restored the full module (the pre-clobber `e0d4fea` version), which already
  satisfies the `mask_value` masking tests. Other callers restored too: `ai_config_resolver`,
  `github_checks_service`, `webhook_service`. Regression:
  `tests/regression/test_secret_service_public_api.py` pins the public surface + `mask_value` +
  encrypt/decrypt round-trip so a future stub breaks loudly instead of taking down the import graph.
- **Stale commit-allowlist caps (failing CI gate)** (`fix/stale-commit-allowlist-caps`) —
  `test_commit_allowlist_caps_are_accurate` was red on `main`: `knowledge_sync_service` (cap=6)
  and `rag_generation_service` (cap=1) had been converted to stage-only (0 service-level
  `commit()` calls) without updating the architectural allowlist. Removed both now-zero entries
  (a 0-commit service needs no allowlist entry; the `count==0` files are already skipped by the
  "commits must be allowlisted" guard). Test-only change; the four
  `test_architectural_transaction_boundaries` tests pass again.
- **Incomplete `app.core.deps` test stub (failing CI gate)** (`fix/release-phases-deps-stub`) —
  `test_release_phases.py` and `test_project_and_release.py` stub `app.core.deps` in `sys.modules`
  but the stub omitted `require_release_access` and `require_run_access`, which
  `routers/releases.py` imports — so every test in those files that imported the router failed at
  collection with `ImportError: cannot import name 'require_release_access'` (19 failures across
  the two files). Added both names to each stub. Test-only; both files now pass (33 + 22).

## [0.0.1] - 2026-04-15

### Added

- Repository hygiene: SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, ROADMAP.md, LICENSE (Apache 2.0)
- Fixed quickstart: three labelled run modes (core / full / demo)
- Cleaned generated artifacts from the tree
- Removed placeholder root `pyproject.toml`
