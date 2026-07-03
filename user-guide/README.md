# TestLookup User Guide

Documentation for people **using** TestLookup — QA engineers, SDETs, team leads, and release managers. (Looking to install or operate the stack instead? Start with [GETTING_STARTED.md](../GETTING_STARTED.md); for internals, see [architecture/](../architecture/README.md).)

TestLookup ingests your test results (from CI or local runs), groups the failures that share a root cause, explains *why* tests failed, tracks which tests are flaky, and turns all of that into a release-readiness signal — fully offline, on your own infrastructure.

## Guides

| Guide | What it covers |
|---|---|
| [Getting results in](getting-results-in.md) | The four ways to feed TestLookup: dashboard upload, REST API, CLI, and live-streaming SDKs |
| [Triaging failures](triaging-failures.md) | Failure Analysis, clusters, the My Failures inbox, resolution statuses, correcting the AI |
| [Flaky tests & quarantine](flaky-tests.md) | The flaky verdict, the Flaky Coach, step-level flip detection, the quarantine review workflow |
| [Release gates & policies](release-gates.md) | GO / CONDITIONAL_GO / NO_GO verdicts, how they're computed, the policy editor, overrides, gating CI |
| [CLI, SDKs & MCP](cli-sdk-mcp.md) | `testlookup` CLI command groups, the shared SDK config, live-streaming reporters, the 49-tool MCP server |
| [Test management & ownership](test-management.md) | The test-case catalog, plans, reviews, suite owners, ownership rules, and the auto-assignment chain |
| [AI features](ai-features.md) | Run Intelligence, Deep Investigation, the Agent Pipeline, Ask AI chat, and configuring the AI tier |
| [Administration & settings](administration.md) | Projects, users, API keys, integrations & webhooks, flags, audit, storage, and the admin checklists |
| [Dashboards & analytics](dashboards.md) | Overview, Trends, Coverage, the Summary Report's aggregation modes, Value Metrics, Search — and how their counts relate |
| [Working with runs](working-with-runs.md) | The run list and detail pages, comparing runs, bisect-from-green, and a red-build routine |

## Core concepts

- **Project** — the top-level container everything is scoped to. The project selector in the top bar controls what every page shows; admins also get an "All Projects" view. Nothing renders until a project is selected.
- **Run** — one execution of a test suite (a CI job, a local `pytest`, a TestNG session). Runs arrive by file upload, API ingest, or live streaming, and carry pass/fail/skip totals plus per-test rows.
- **Test case** — a single test's result inside a run. TestLookup assigns each logical test a stable **fingerprint** across runs, which is what powers history, flakiness scoring, and "has this failed before?"
- **Suite** — a named grouping of test cases (from your framework's suite concept). Pages that filter by suite consider both the per-test suite name and the run's primary suite.
- **Failure cluster** — failures whose error signatures point at the same root cause are grouped so a 30-test outage reads as *one* problem, not thirty.
- **Flaky test** — a test that changes verdict without a code change. TestLookup scores flaky confidence per fingerprint, down to individual test **steps**, and can recommend quarantine.
- **Quarantine** — a holding state that keeps a known-flaky test from failing your release signal while it's being fixed.
- **Release gate** — the readiness verdict for a run or release: **GO**, **CONDITIONAL_GO**, or **NO_GO**, computed from pass rates, failure clusters, flakiness, and the policies you configure.
- **Ownership / My Failures** — failures are auto-assigned to suite owners and QA engineers; **My Failures** is each user's personal triage queue.

## Finding your way around

The sidebar groups the app into these areas (paths are relative to the dashboard, default `http://localhost:3000`):

**Daily work**
- `/overview` — the landing dashboard: pass rate, verdict, trend, and what needs attention.
- `/runs` — all runs; click one for per-test detail, or `/runs/compare` to diff two runs.
- `/live` — runs currently streaming in from the SDKs, updating in real time.
- `/my-failures` — your personal queue of assigned failures.
- `/failures` — Failure Analysis: clusters, error signatures, AI root-cause explanations.

**Test health**
- `/suites` and `/coverage` — suite-level health, coverage, and drill-downs.
- `/flaky-coach` — flaky-test triage with per-test history and step-level flip detail.
- `/quarantine` — what's quarantined and what the detector recommends quarantining.
- `/test-management` — test cases, plans, suites, review workflow, and defect decisions.
- `/trends` — pass-rate and volume trends over time.

**Release & intelligence**
- `/release-gate` — the GO / CONDITIONAL_GO / NO_GO verdict and the evidence behind it.
- `/releases` — release tracking with gate status per release.
- `/policies` — the policy editor that configures how verdicts are computed.
- `/intelligence`, `/deep-investigate`, `/agents`, `/chat` — the AI layer: run intelligence reports, deep investigations, agent pipelines, and a chat interface over your test data.
- `/reports/summary` — the run/window summary report.
- `/defects`, `/value-metrics`, `/search` — defect promotion, ROI metrics, global search.

**Administration**
- `/projects`, `/users`, `/ownership` — projects, people, and suite-ownership rules.
- `/settings/*` — profile, API keys, integrations (GitHub, SSO, webhooks, digests), AI configuration, feature flags, audit, storage, and data management.

> Time windows are shared: the window you pick on one page (7 days, 30 days, …) follows you across pages.

## Conventions in these guides

- The dashboard runs at `http://localhost:3000` and the API at `http://localhost:8000` in a default self-host install — substitute your deployment's URLs.
- API examples use `curl` with an API key created under **Settings → API Keys**.
- Everything documented here works fully offline (`AI_OFFLINE_MODE=true`, the default); AI features that need a local LLM say so explicitly.
