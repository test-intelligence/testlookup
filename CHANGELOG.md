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

### Performance (2026-06-03 — query batching, human-directed)

- **`suite_sync_service` per-suite query fan-out collapsed** (`perf/suite-sync-batch-membership`)
  — `sync_suite_membership` called `_sync_one_suite` per suite, and each call issued 3 SELECTs
  (managed cases, existing memberships, `<suite>-deleted` bucket), i.e. `1 + 3N` queries on the
  ingestion critical path. The three lookups are now batched across all suites in one query each
  (`4` total, constant in suite count) and sliced per suite; `_sync_one_suite` performs no DB
  reads. Behavior-preserving — suites are processed independently (disjoint `suite_name` /
  `<suite>-deleted` rows), so prefetching the whole set up front matches the prior sequential
  fetches. Pinned by `tests/regression/test_suite_sync_batched_queries.py` (constant 4 round
  trips for N suites + add/unchanged/delete behavior across a multi-suite run).

## [0.0.1] - 2026-04-15

### Added

- Repository hygiene: SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, ROADMAP.md, LICENSE (Apache 2.0)
- Fixed quickstart: three labelled run modes (core / full / demo)
- Cleaned generated artifacts from the tree
- Removed placeholder root `pyproject.toml`
