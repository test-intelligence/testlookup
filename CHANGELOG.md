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

### Fixed (2026-06-06 — CI test-ordering pollution from the PR #164 regression tests)

- **CORRECTION to the entry below:** the 13 failures previously written off as "stale clobber" were **not stale** — they fail only in the **full-suite run** (`pytest tests/`), not in isolation, which is why the earlier isolated reproduction looked green. Root cause: two of the PR #164 regression tests leak global state into the rest of the suite. Both product code paths are correct; the fixes are test-isolation only. Reproduced + verified on a Python 3.11 venv (full suite minus integration): **3000 passed, the 13 dump failures gone.**
- **`tests/regression/test_chromadb_telemetry_disabled.py` left `app.core.config` reloaded.** It calls `importlib.reload(config)` to re-exercise the import-time telemetry guard, which rebinds the module's `settings`/`get_settings` to brand-new objects. Every module that already did `from app.core.config import settings` keeps the ORIGINAL instance, so downstream tests that `monkeypatch.setattr(config.settings, <flag>, ...)` patch a dead object while the app reads defaults — silently breaking ~12 "feature-disabled / offline" assertions (ingestion buffer-cap/routing/rate-limit/backpressure, ai_pipeline_debouncer, github_checks ×3, webhook, integration-probe). Fix: an autouse fixture snapshots and restores `config.settings`/`config.get_settings` so the reload can't escape the test.
- **`tests/test_db_postgres_lazy_engine.py` tripped over a `monkeypatch.setattr` leak.** `engine`/`AsyncSessionLocal` are served by PEP 562 `__getattr__` (not real attributes). Pytest's `monkeypatch.setattr` (used across ~20 files to inject a fake session factory) restores by *setattr*, not delattr, leaving a **real** stale attribute behind that shadows the lazy hook; combined with the BUG-003 `dispose_engine_for_loop` engine rebuild, `pg.AsyncSessionLocal` then no longer matched the live `get_session_factory()`. Fix: the two lazy-hook tests now drop any leaked `engine`/`AsyncSessionLocal` from `pg.__dict__` before asserting, so they exercise the hook itself.

### Fixed (2026-06-06 — CI test failures: stale dump triage + 2 real fixes)

- **Triaged a CI `pytest` dump reporting 15 failures.** Reproduced the full set on a Python 3.11.9 venv (matching CI). **13 were stale** — they came from the documented RAG/knowledge service-clobber state and pass on current `main` once the services were restored (the entire "feature-disable / offline-off" cluster: ingestion buffer-cap/routing/rate-limit/backpressure, ai_pipeline_debouncer, github_checks ×3, webhook, plus the lazy-engine and integration-probe tests). **2 were real** and are fixed below; both were self-inflicted by the 2026-06-06 autonomous-loop fixes (PR #164).
- **`test_run_async_no_event_loop_closed_error_on_repeated_calls` (BUG-003 regression test) asserted `loop.is_closed()` after the loops were already closed.** `_run_async` closes each event loop once its coroutine completes, so checking the captured loop objects *after* the call block always read `True`. Fixed to capture the closed-state **at dispose time** (inside the patched `dispose_engine_for_loop`), matching the sibling test — proving dispose runs before close without depending on post-hoc loop state. Product code unchanged (the BUG-003 fix is correct).
- **`test_chroma_dedup_collection_is_per_project` patched the wrong seam after the BUG-001 refactor.** The test patched `sys.modules["chromadb"]`, but `_find_duplicate_semantic` now builds its client via `app.db.chroma.get_chroma_client()` (which binds `chromadb` at its own import), so the patch never intercepted the call and the captured collection name was empty. Fixed to patch `app.db.chroma.get_chroma_client`. The product code still correctly namespaces the collection per project (`open_defects_{project_id}`).
- **Quality gate (`scripts/quality_gate.py`) refreshed two stale line-number baselines** (`backend.analysis-router`, `backend.structlog-positional-args`) that drifted after the service restore; gate now passes 15/15.

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
