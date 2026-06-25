# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### 2026-06-25 — CI check: validate Mermaid diagrams render

- **New CI job "Docs — Mermaid diagrams"** -- parses every `` ```mermaid `` block in tracked markdown with the Mermaid grammar (`mermaid.parse()` under jsdom, the same parse GitHub runs before rendering), so a broken diagram fails the build instead of silently shipping an "Unable to render rich display" box. Self-contained validator in `scripts/mermaid-check/` (pinned `mermaid` + `jsdom`, committed lockfile); enumerates files via `git ls-files` so it checks exactly the tracked docs GitHub renders. Run locally with `cd scripts/mermaid-check && npm ci && node validate.mjs`. Currently 18/18 blocks across 37 markdown files parse cleanly. (Caught and motivated by a real "Unable to render" bug in the Celery topology diagram, fixed in the same day's docs.)

### 2026-06-25 — set-state-in-effect cleared everywhere → rule promoted to error

- **Frontend hooks lint hardening (ratchet complete)** -- clears the final 20 `react-hooks/set-state-in-effect` sites across 14 files and flips the rule from `warn` to `error` in `frontend/eslint.config.js`, so any reintroduction now fails CI. Three mechanical patterns were applied: (1) pagination/selection resets and default-selection syncs now run **during render via previous-value tracking** (the React-recommended way to reset state on a dependency change) instead of a cascading effect -- `RunsPage`, `IntelligenceHubPage`, `LiveExecutionPage`, `WorkflowTimeline`, `AgentStatusPage`, `AgentWorkflowPage`, `DecisionTrailDrawer`, `DefectPromotionModal`, `RunIntelligencePage`, `useAnalyticsView`; (2) data-fetch effects moved to **SWR hooks** -- `ProjectMembersTab` and `TestManagementPage` reuse `useProjectMembers` / inline `useSWR` (with optimistic `mutate` for local edits), `AIEvalDashboardPage` swaps its tab-driven `loadX` effects for SWR; (3) two genuine effects that must stay -- a network token re-verification in `ProtectedRoute` and a coordinated one-time deep-link expand+scroll in `TestManagementPage` -- carry scoped `eslint-disable` lines with justification. Regression coverage: new `WorkflowTimeline` test pinning the default-stage-follow behaviour, plus `ProjectMembersTab.test.tsx` updated for the SWR data path. Lint/type-check/build/411 vitest tests all green.

### 2026-06-25 — DigestsPage off set-state-in-effect (SWR hooks)

- **Frontend hooks lint hardening (slice)** -- clears the `react-hooks/set-state-in-effect` warning on `DigestsPage` (63 -> 62) by replacing its single tab-driven `loadData` effect with two SWR hooks (`useDigestData.ts`: `useDigestSubscriptions` / `useDigestSavedViews`). SWR now owns the loading/data/error state declaratively, gating each query on the active tab and re-keying saved views on the selected project; the page refreshes after a create/pause/resume/delete via the hooks' `mutate` instead of an imperative loader. Adds a regression test (`useDigestData.test.ts`). The rule stays at `warn` until the remaining sites are cleared.

### 2026-06-24 — Semver release workflow (versioned GHCR images)

- **Tag-driven release pipeline** -- new `.github/workflows/release.yml` fires on a `v*.*.*` tag push and builds + pushes the three app images (`backend`, `frontend`, `mcp`) to `ghcr.io/anandtopu/testlookup/*` tagged with the full semver and its moving aliases: `v1.2.3` -> `:v1.2.3 :1.2.3 :1.2 :1 :sha-<sha>`. This makes `TESTLOOKUP_VERSION` real -- `docker-compose.release.yml` self-hosters can now pin a published release tag instead of the moving `latest` (still published by the main-push CI). Reuses the proven build/login/SDK-staging steps from `ci.yml`, derives tags via `docker/metadata-action` semver patterns, attaches an SBOM + max provenance per image, and warns when the pushed tag disagrees with the tracked `VERSION` file. Contract pinned by `backend/tests/test_release_workflow.py` (semver-tag trigger, all three images, semver tag derivation, GHCR push, `packages:write`, GITHUB_TOKEN login).

### Why we built this

Engineering teams running automated tests get fragmented artifacts: JUnit XML, Allure outputs, flaky failures, pipeline status. Existing tools help visualise results, but teams still burn hours on manual triage, clustering, root-cause analysis, and release decisions. The problem is worse in regulated or private environments that can't depend on cloud-only AI services.

TestLookup is our answer: a local-first test failure intelligence engine that ingests results, clusters failures, explains probable root causes, and produces release-risk signals -- all offline-capable, and all accessible through a dashboard, REST API, CLI, and MCP server so AI assistants can query test health directly.

### Added

- **No-clone self-host release artifacts** -- `docker-compose.release.yml` pulls pinned pre-built images (`ghcr.io/anandtopu/testlookup/{backend,frontend,mcp}:${TESTLOOKUP_VERSION:-latest}`) instead of building from source, with demo/local-LLM profiles; `install.sh` is a remote one-liner (`curl ... | bash`) that downloads the stack, generates a local `.env`, and brings it up; a `VERSION` file anchors the release tag. Contract pinned by `backend/tests/test_release_artifacts.py` (image-not-build, no host-source bind-mounts, dev/release service parity, `install.sh` bash-syntax + fetch list).
- **Multi-framework ingestion** -- JUnit XML, TestNG, Allure JSON, Cypress, Playwright, pytest
- **Three analysis modes** -- rules (pattern match), ML (scikit-learn HistGradientBoosting), LLM (Ollama ReAct agent), auto (smart fallback chain)
- **Run Intelligence** -- single-pane summary with failure clusters, regression diff, risk score
- **Release gate** -- GO / CONDITIONAL_GO / NO_GO with explainable reasons and QA Lead override audit trail
- **Decision trail** -- per-run "why did the AI do that" drawer with stage filter and full-text search
- **Two-run compare** -- side-by-side diff with classification (new failures, regressions, duration spikes, renamed tests via fuzzy pairing)
- **Flaky quarantine** -- detection, QA Lead approval, active quarantine, nightly recheck, release/re-quarantine state machine
- **Perf regression detection** -- per-test duration baselines (Welford algorithm) with 3-sigma spike detection
- **Feature flags** -- per-project / per-role / rollout-percent gates with audit history
- **MCP server** -- 48 tools, 9 resources, 6 prompt workflows for AI assistant integration
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

### Added (2026-06-24 — FLK-P6 slice 4: cross-run step-flip surfaced on the test-case detail page)

Surfaces the previously backend-only cross-run step-flip intelligence (FLK-P6) in the UI. Earlier slices retained per-run step outcomes (`test_step_runs`, migration 0097), added the pure `compute_step_flips`, and the project-scoped DB read `step_flip_report_by_fingerprint`; this slice wires a read API and a per-test panel so a QA engineer can see *which step* oscillated PASSED↔FAILED across runs — pointing at one flickering step rather than a whole-test verdict.

- `GET /api/v1/runs/{run_id}/tests/{test_id}/step-flips` (new, `backend/app/routers/runs.py`) — READ-ONLY. Resolves the test's `test_fingerprint` + `project_id` from the **provided** `run_id` (guarded by `require_run_access` — IDOR ratchet), then returns the cross-run step-flip report. 404 when the `test_id` doesn't belong to the run; an empty-window "insufficient history" report otherwise. No DB writes (router owns the no-op transaction).
- `runs_service.step_flip_report_for_test()` (new) — thin per-test wrapper that resolves `(run_id, test_id)` → `(fingerprint, project_id)` and defers to the existing batched, project-scoped `step_flip_report_by_fingerprint`. Pure read; never N+1 (resolve + anchor + step-runs). Covered by `backend/tests/test_step_flip_for_test.py`.
- `src/components/runs/StepFlipPanel.tsx` (new) + `useTestStepFlips` SWR hook (`src/hooks/useRuns.ts`) + `runsService.getTestStepFlips` + `TestStepFlips`/`StepFlipReport` types — a "Cross-Run Step Flakiness" section on the test-case detail page (`src/pages/TestCasePage.tsx`), beneath the existing History/Steps panels. Renders the per-step flip roll-up (flip count, current PASSED/FAILED status, regression vs recovery) with distinct empty states for "no history yet" vs "stable across N runs". Lazy SWR fetch, no set-state-in-effect. Covered by `src/components/runs/StepFlipPanel.test.tsx`.
### Changed (2026-06-24 — Frontend lint: PolicyEditorPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site (23 → 21 warnings):

- `src/pages/PolicyEditorPage.tsx` — replaced the list-mode `loadPolicies` effect and the edit-mode `getPolicy` effect with SWR hooks. The edit-mode case seeds *editable* form fields (name/description/projectId/doc/isDraft) from the loaded policy, so instead of an effect it uses the render-phase "adjust state during render" pattern guarded by a `seededPolicyId` tracker (runs once per loaded policy). Dropped the now-unused local `error` state; load failures derive from the hooks' `isError`. Behaviour unchanged.
- `src/hooks/usePolicyEditor.ts` (new) — `usePolicies(enabled)` (list mode; exposes `mutate` for post-deactivate refresh) and `usePolicy(policyId, enabled)` (edit mode; re-keys on the id). `revalidateOnFocus: false` and `shouldRetryOnError: false` preserve the prior single-shot fetch semantics.

### Changed (2026-06-24 — Frontend lint: AuditDashboardPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site (24 → 23 warnings):

- `src/pages/settings/AuditDashboardPage.tsx` — replaced the tab-keyed load-on-mount effects (one fetching audit categories on mount, one driving `events`/`total`/`obs`/`loading` off the active tab and filter selection) with three SWR hooks, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the loading/data/error state declaratively and re-keys on the filter params (project, category, day window), so the page no longer threads a `loadEvents` callback through an effect dependency array. Behaviour unchanged.
- `src/hooks/useAuditDashboard.ts` (new) — `useAuditCategories()` always fetches (it feeds the events-tab filter dropdown); `useAuditEvents(params, enabled)` is gated by the active events tab and re-fetches when project/category/days change; `useProjectObservability(projectId, enabled)` is gated by the active observability tab plus a selected project, mirroring the old `if (tab === ...)` / `projectId` branches. `revalidateOnFocus: false` and `shouldRetryOnError: false` preserve the prior single-shot fetch semantics.

Added `src/hooks/useAuditDashboard.test.ts` (categories fetch and surface data; a failed load reports `isError` with an empty list; events re-key on filters and omit empty project/category params; events and observability skip the fetch when their tab is inactive; observability additionally skips without a selected project). Each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, `AuditDashboardPage` no longer flags the rule), `type-check`, `build`, and the new vitest suite all green. The rule stays at `warn` until the remaining sites are cleared in later slices.

### Changed (2026-06-23 — Frontend lint: IntegrationHealthPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site:

- `src/pages/settings/IntegrationHealthPage.tsx` — replaced the tab-keyed load-on-mount `useEffect(() => { load() }, [load])` (which drove `statuses`/`trends`/`history`/`loading` from inside the effect) with three SWR hooks, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the loading/data/error state declaratively. The post-probe `load()` call becomes `refreshIntegrationHealth()` (SWR `mutate`), revalidating whichever dataset is mounted exactly as before. Behaviour unchanged.
- `src/hooks/useIntegrationHealth.ts` (new) — `useIntegrationStatus()` always fetches (it feeds the status tab, the history-tab provider dropdown, and the always-rendered workflow timeline); `useHealthTrends(enabled)` and `useProviderHistory(provider, enabled)` are gated by the active tab (and history additionally by a selected provider), mirroring the old `if (tab === ...)` branches. `revalidateOnFocus: false` and `shouldRetryOnError: false` preserve the prior single-shot fetch semantics. `refreshIntegrationHealth()` revalidates all three keys via a global mutate predicate.

Added `src/hooks/useIntegrationHealth.test.ts` (status fetches and surfaces data; a failed load reports `isError` with an empty list; trends/history skip the fetch when their tab is inactive; history additionally skips without a selected provider). Each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, `IntegrationHealthPage` no longer flags the rule), `type-check`, `build`, and the new vitest suite all green. The rule stays at `warn` until the remaining sites are cleared in later slices.

### Changed (2026-06-23 — Deps: recharts 2 → 3)

Bumped `recharts` from `^2.13.3` to `^3.9.0` (supersedes Dependabot #211, which failed `tsc`). recharts 3 tightened the `Tooltip` `formatter` type to `Formatter<ValueType, NameType>`, so the two call sites that annotated the value param as `number` no longer compiled (`DefectDonut.tsx`, `SuiteDetailPage.tsx`). Dropped the manual annotations so recharts' own param types flow through; behaviour is unchanged (the donut tooltip still shows `[value, name]`, the pass-rate area still shows `"<v>% / Pass Rate"`). recharts 3 also restructured its internals (now backed by a redux/immer store instead of lodash/prop-types/react-smooth), which is reflected in the lockfile. Validated locally: `type-check`, `lint` (0 errors), `build`, and the chart/SuiteDetail vitest suites all green.

> **Manual smoke required before relying on this in prod:** recharts 3 changes some default rendering (animations, axis/category defaults, responsive sizing). The build and unit tests pass, but the actual chart visuals (Overview trend, DefectDonut, PassRateGauge, TrendChart, SuiteDetail pass-rate area) should be eyeballed in the running app — same caveat class as the tailwind v4 bump.

### Changed (2026-06-23 — Deps: web-vitals 4 → 5)

Bumped `web-vitals` from `^4.2.4` to `^5.3.0` (supersedes Dependabot #219, which failed type-check on the removed export). v5 retired FID (First Input Delay) in favour of INP (Interaction to Next Paint) and removed the `onFID` export, so `src/hooks/useWebVitals.ts` no longer registers it — the remaining five Core Web Vitals (CLS, LCP, FCP, TTFB, INP) are unchanged. Added `src/hooks/useWebVitals.test.ts` to guard the registered metric set. Validated locally: `type-check`, `lint` (0 errors), `build`, and the new vitest suite all green.

### Changed (2026-06-23 — Frontend lint: ProfilePage off set-state-in-effect (render-phase sync, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set: `react-hooks/set-state-in-effect` is cleared one site per PR by real refactors (not disables) before the rule is promoted to `error`.

- **`settings/ProfilePage`** — replaced the form-sync `useEffect` (which re-seeded `fullName`/`avatarColor` from the auth-store `user` whenever it changed) with React's documented "adjust state during render" pattern. The component now tracks the last-synced `full_name`/`avatar_color` and resets the editable fields during render when the canonical user changes (after a save here, a save elsewhere, or re-login), with no effect. Local edits are preserved because the user object is unchanged while typing. Regression test `frontend/src/pages/settings/ProfilePage.test.tsx` covers initial seeding, the re-seed on user change, and the empty-name fallback.

### Changed (2026-06-23 — Frontend lint: SSOSettingsPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site:

- `src/pages/settings/SSOSettingsPage.tsx` — replaced the tab-keyed load-on-mount `useEffect(() => { loadData() }, [loadData])` (which drove `configs`/`scimTokens`/`events`/`syncStatus`/`loading`/`error` from inside the effect) with a new SWR hook `useSSOTabData(tab)`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the loading/data/error state declaratively, keyed on the active tab so switching tabs fetches exactly the slice the old effect did. The post-mutation `loadData()` calls (after create/toggle/delete config and create/revoke SCIM token) become `refresh()` (SWR `mutate`), revalidating the active tab exactly as before; mutation errors still set a local `error` that is merged with the load error for the inline banner. Behaviour unchanged.
- `src/hooks/useSSOTabData.ts` (new) — `useSWR` keyed on `['sso-tab', tab]`, mirroring the old `tab` dependency. The fetcher switches on tab and populates only that tab's slice (the others stay empty, matching the original per-tab render guards); `revalidateOnFocus: false` and `shouldRetryOnError: false` preserve the prior fetch semantics, and a failed load is surfaced as a string `error` for the banner the old `catch` raised.

Added `src/hooks/useSSOTabData.test.ts` (config-tab fetches only the config slice; the events tab unwraps the `{ total, items }` envelope; a failed load surfaces a string error with a null `syncStatus`; `refresh` revalidates). Each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, the rule's count drops 27 → 26 and `SSOSettingsPage` no longer flags it), `type-check`, `build`, and the new vitest suite all green. The rule stays at `warn` until the remaining 26 sites are cleared in later slices.

### Added (2026-06-22 — Frontend: ReassignModal off set-state-in-effect via SWR hook)

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set: `react-hooks/set-state-in-effect` is cleared one site per PR by real refactors (not disables) before the rule is promoted to `error`.

- **`MyFailuresPage` ReassignModal** — replaced the load-on-mount `useEffect` (which drove `setOptions`/`setLoading`/`setError` and a derived default selection) with a new SWR hook `useReassignOptions(testCaseId)`. SWR now owns loading/data/error declaratively; the default assignee (suite owner → first QA Engineer → none) is derived during render with an explicit pick taking precedence, so the picker behaves identically without driving state from an effect. Regression test `frontend/src/hooks/useReassignOptions.test.ts` covers the fetch, the null-id skip, and the error path.

### Changed (2026-06-22 — Frontend lint: OnboardingPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site:

- `src/pages/OnboardingPage.tsx` — replaced the load-on-mount `useEffect(() => { … onboardingService.detectProgress(projectId).then(setStatus)… }, [projectId])` (which drove `status`/`loading` from inside the effect) with a new SWR hook `useOnboardingStatus`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the loading/data state declaratively, so the page no longer drives state from an effect. The post-skip `setStatus(updated)` becomes `mutate(updated, { revalidate: false })` (applies the server response to the SWR cache without a refetch, exactly as before).
- `src/hooks/useOnboardingStatus.ts` (new) — `useSWR` keyed on `['onboarding-status', projectId]`, mirroring the old `projectId` dependency. The key is `null` (no fetch) when there is no resolved project — exactly the old `if (!projectId) { setStatus(null); setLoading(false); return }` guard, where the all-projects view (`null` projectId) never fetched and showed the 0%/no-steps workspace view. `status` is `null` while loading or before a project is selected (preserving the prior `useState<OnboardingStatus | null>(null)` semantics); `shouldRetryOnError: false` surfaces a failed load immediately, toasting the same "Failed to load onboarding status" the old `.catch` raised. Behaviour unchanged.

Added `src/hooks/useOnboardingStatus.test.ts` (project-scoped fetch surfacing status; the no-project case skipping the fetch entirely; the error path toasting and leaving `status` null); each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, the rule's count drops 32 → 31 and `OnboardingPage` no longer flags it), `type-check`, `build`, and the full vitest suite all green. The rule stays at `warn` until the remaining 31 sites are cleared in later slices.
### Changed (2026-06-21 — Frontend lint: OwnershipEditorPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (31 sites remain after this slice); the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site:

- `src/pages/OwnershipEditorPage.tsx` — replaced the load-on-mount `useEffect(() => { loadRules() }, [loadRules])` (the `loadRules` callback drove `rules`/`loading`/`error` state synchronously) with a new SWR hook `useOwnershipRules`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns loading/data state declaratively, so the page no longer drives state from an effect; the mutation handlers (create/toggle/delete) call the hook's `refresh()` (SWR `mutate`) instead of re-invoking `loadRules()`.
- `src/hooks/useOwnershipRules.ts` (new) — `useSWR` keyed on `['ownership-rules', projectId]`, mirroring the old effect's `projectId` dependency. The key is `null` (no fetch) when there is no resolved project — matching the old `if (!projectId) return` guard, where the all-projects view never fetched and showed the "select a project" empty state. `shouldRetryOnError: false` surfaces a failed load immediately as `isError` (rendered as the same red "Failed to load ownership rules" banner), and `rules` is `[]` while loading or on error, preserving the prior `useState([])` semantics. Behaviour unchanged.

Added `src/hooks/useOwnershipRules.test.ts` (regression guard per repo convention): project-scoped fetch surfacing rules, the no-project case skipping the fetch entirely, and the error path reporting `isError` with an empty rules list. Each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, 73→72 warnings; the rule's count drops 32→31 and `OwnershipEditorPage` no longer flags it), `type-check`, `build`, and the full vitest suite (351 passed) all green. The rule stays at `warn` until the remaining 31 sites are cleared in later slices.
### Changed (2026-06-21 — Frontend lint: SuiteDetailPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (32 sites remain after the PerformancePage slice); the sites are cleared in reviewable slices before the rule is promoted to `error`. This slice clears one more site by a real refactor (not a disable):

- `src/pages/SuiteDetailPage.tsx` — replaced the `(suiteName, activeProjectId, days)`-keyed load effect `useEffect(() => { setTrendLoading(true); getSuiteTrend(suiteName, pid, days).then(res => setTrendPoints(res.points || [])).catch(() => setTrendPoints([]))… }, [suiteName, activeProjectId, days])` with a new SWR hook `useSuiteTrend`, matching the codebase's "pages fetch via SWR hooks" convention (the page already reads `useSuiteDetail`/`useSuites`). SWR now owns loading/data state declaratively, so the page no longer drives state from an effect. Dropped the now-unused inline `SuiteTrendPoint` type, `useState`/`useEffect`, and `testManagementService` imports.
- `src/hooks/useSuiteTrend.ts` (new) — `useSWR` keyed on `['suite-trend', activeProjectId, suiteName, days]`, mirroring the old effect's dependency array. The key is null only when there is no `suiteName` (so no fetch then, exactly as the old `if (!suiteName) { setTrendPoints([]); return }` guard); the "all projects" view maps `ALL_PROJECTS_ID` to a `null` project id as the old effect did. `shouldRetryOnError: false` makes a failed load surface as `[]` immediately, matching the old `.catch(() => setTrendPoints([]))`. `points` is `[]` while loading or on error (preserving the old `useState<SuiteTrendPoint[]>([])` semantics). Behaviour unchanged.

Added `src/hooks/useSuiteTrend.test.ts` (project-scoped fetch surfacing points, all-projects view mapping to a `null` project id, no-suite-name skipping the fetch, and the error path surfacing `[]`); each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, 73→72 warnings; the rule's count drops 32→31 and `SuiteDetailPage` no longer flags it), `type-check`, `build`, and the full vitest suite (352 passed) all green. The rule stays at `warn` until the remaining 31 sites are cleared in later slices.

### Changed (2026-06-22 — Frontend lint: DeepInvestigationPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks **v7 (React Compiler)** rule set: `react-hooks/set-state-in-effect` is the last named rule still at `warn`. Sites are cleared one page per slice by real refactors (not disables) before the rule is promoted to `error`.

- **`DeepInvestigationPage`** — replaced the load-on-mount `useEffect` that fetched WF-1 pipeline status into a `useState` (`getPipelineStatus(runId, 'deep')` with an `alive` guard) with a new SWR hook **`usePipelineStatus`** in `useDeepInvestigation.ts`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the fetch declaratively; the key is `null` (no fetch) when there is no run — exactly the old `if (!runId) { setPipelineStatus(null); return }` guard — and `revalidateOnFocus` is left at the SWR default to honour the page's "refresh on focus" intent the bare effect never actually delivered. `shouldRetryOnError: false` surfaces a failed load as `null`, matching the old `.catch`. Behaviour unchanged.
- **Regression test** `src/hooks/useDeepInvestigation.test.ts` covers the run-scoped fetch, the no-run case skipping the fetch, and the error path leaving data undefined (rendered as `null`).
- `npm run lint` 0 errors; `set-state-in-effect` count drops **32 → 31**; type-check, build, and full vitest (358) green. The rule stays at `warn` until the remaining 31 sites are cleared.

### Added (2026-06-21 — Adoption: `make smoke` health verifier + fixed demo health-wait)

Slice 4 of frictionless self-host adoption — give newcomers confidence the stack actually came up, and fix a real hang.

- **Fixed a `make demo` hang**: it waited on `curl http://localhost:8000/health`, but the bare `GET /health` shim was retired — the probes are `/health/live` and `/health/ready` (the Compose healthcheck already uses `/health/live`). The retired path 404s, so `curl -sf` never succeeded and the demo looped forever after "Waiting for backend health check." Now it waits on `/health/ready`.
- **New `scripts/smoke.py`** (stdlib only — no install) + **`make smoke`**: probes backend readiness (`/health/ready`), liveness (`/health/live`), the API schema (`/openapi.json`), and the frontend, then prints a clear PASS/FAIL report and exits non-zero if any core check is down (usable in CI / setup scripts). Dependency details are informational. Output is ASCII-only so it can't crash a Windows (cp1252) console. Base URLs override via `TL_API` / `TL_WEB`.
- **GETTING_STARTED.md**: Step 1 now uses `make quickstart` (dropping the stale "`cp .env.example` — defaults work for local dev" line, which was the broken path slice 1 fixed) and adds a `make smoke` verification step.
- **New `backend/tests/test_smoke_script.py`** (5 tests) — pins the pure `summarize()` decision core (core failure ⇒ non-pass; informational failure ⇒ still pass; empty ⇒ ok) and that `probe()` never raises on an unreachable host. ruff + quality-gate green.

### Added (2026-06-21 — Adoption: first-run getting-started guide on the dashboard)

Slice 3 of frictionless self-host adoption. A brand-new project (or a fresh instance) used to land on a zeroed-out dashboard with no obvious next step. Now, when there are no executions in the window and no recent runs, the Overview shows a dismissible **getting-started guide** that turns the empty state into a clear path to first value.

- **New `frontend/src/components/onboarding/FirstRunGuide.tsx`** — a presentational, dismissible card with three copy-to-clipboard steps (1: `make quickstart` to load the demo; 2: `testlookup upload results.xml` / ingest your own results; 3: explore) plus quick links to Failure analysis, Flaky coach, the Release gate, and the getting-started docs. Copy uses the shared `utils/clipboard` helper; dismissal persists per-browser via `localStorage` (`FIRST_RUN_DISMISS_KEY`).
- **`OverviewPage.tsx`** — renders the guide at the top when `!summaryLoading && total_executions === 0 && recentRunItems.length === 0` and it hasn't been dismissed; it disappears automatically once the first run lands (and the demo seed from slice 2 means demo users never see it).
- **New `frontend/src/components/onboarding/FirstRunGuide.test.tsx`** (7 tests) — steps/commands present, first-insight links wired, project name shown, clipboard copy, dismiss handler, no-dismiss-without-handler, stable key.

Validated locally: `vitest` (7 passed), `tsc --noEmit`, `eslint`, and `vite build` all green. (The live Overview render on a fresh instance is best confirmed with a `make quickstart` smoke — the component is fully unit-tested and the page integration type-checks/builds.)

### Added (2026-06-21 — Adoption: rich demo dataset so first-run views are populated)

Slice 2 of frictionless self-host adoption. The seed previously created only *authored* test cases/plans/releases, so a fresh install's dashboards, flaky-coach, trends, and failures pages were empty until real runs arrived (and a single upload can't show flakiness or trends). Now a `make quickstart` / `make seed-data` shows a populated, compelling app out of the box.

- **New `backend/app/services/demo_dataset.py`** — pure, deterministic generator (`generate_demo_runs(now=…)`) producing ~14 synthetic runs over ~30 days that embed the headline patterns: stable tests, a **flaky** test (alternating pass/fail with varied environmental error signatures), a **regression** (clean for most of the window then failing in the latest runs), a **product bug** (fails every run with one assertion), and a **perf** regression (duration spikes in recent runs). No DB / no wall-clock — the caller passes `now`.
- **`scripts/seed_dev_data.py`** — new best-effort `_seed_execution_history` runs AFTER the core seed commits, in isolated sessions, feeding the generated runs through the REAL ingestion pipeline (`create_run_from_payload` → `ingest_test_results` → `finalize_run`) so every derived table (history, fingerprints, canonical cases, suites, aggregates) is correct, then backdates the run + its rows so the history spans the trend window. Any failure here is swallowed per-run and can never break the user/project/case seed.
- **New `backend/tests/test_demo_dataset.py`** (14 tests) — pins valid `IngestPayload`/`IngestTestResult` shape, the ~30-day date spread, and that each pattern is detectable downstream (flaky alternates ≥3/≥3 with varied errors; regression is clean→failing; product bug always fails; stable always passes; perf duration spikes; realistic per-run pass rate; stable test identity; determinism). ruff + quality-gate green.

(End-to-end ingestion of the seed runs needs a running stack — validate with a `make quickstart` smoke; the generator contract is pinned by the tests.)

### Added (2026-06-21 — Adoption: one-command zero-config quickstart (`make quickstart`))

First slice of the "frictionless self-host adoption" track. Removes the #1 first-run barrier: previously a newcomer had to `cp .env.example .env` and hand-generate **8 secrets** via `openssl`, and `.env.example` shipped literal placeholders where `DATABASE_URL`/`MONGO_URI` embedded *different* placeholder passwords than `POSTGRES_PASSWORD`/`MONGO_PASSWORD` — so a bare copy + `make demo` brought up containers whose backend couldn't authenticate to Postgres (Compose hard-fails on empty `${POSTGRES_PASSWORD:?…}` etc.).

- **New `scripts/gen-dev-env.sh`** — copies `.env.example`, then fills every required secret (`POSTGRES_PASSWORD`, `MONGO_PASSWORD`, `MINIO_ACCESS_KEY/SECRET_KEY`, `APP_SECRET_KEY`, `JWT_SECRET_KEY`, `WEBHOOK_SECRET`, `FLOWER_PASSWORD`, `GF_SECURITY_ADMIN_PASSWORD`) with a freshly generated random value and rewrites `DATABASE_URL` / `MONGO_URI` so their passwords stay in sync. Secret source falls back openssl → python `secrets` → `/dev/urandom`. The file is git-ignored and stamped with a clear LOCAL/DEMO-only banner; the script no-ops if `.env` already exists (`--force` to regenerate).
- **Makefile**: the `.env` bootstrap target now runs the generator (so `make dev` / `make demo` start on the first try with zero manual editing), with a plain-copy fallback if `bash` is unavailable. New **`make quickstart`** target = generate `.env` + run the demo.
- **README**: Quick Start now leads with the one-command `make quickstart`; the secrets section is reframed as production-only (local/demo secrets are auto-generated); removed the stale "demo coming in v0.1.0" note (the `demo` target already exists).

### Changed (2026-06-20 — Frontend lint: PerformancePage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (33 sites remain after the SeedDataPage slice); the sites are cleared in reviewable slices before the rule is promoted to `error`. This slice clears one more site by a real refactor (not a disable):

- `src/pages/settings/PerformancePage.tsx` — replaced the load-on-mount `useEffect(() => { setLoading(true); Promise.all([getPerformanceBudgets(), getSearchConfig()]).then(([b, c]) => { setBudgets(b); setConfig(c) })… }, [])` with a new SWR hook `usePerformanceSettings`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns loading/data state declaratively, so the page no longer drives state from an effect.
- `src/hooks/usePerformanceSettings.ts` (new) — `useSWR` keyed on the constant `'performance-settings'`; both static, read-only endpoints are fetched in parallel under one key, exactly as the old `Promise.all`. `shouldRetryOnError: false` makes a failed load surface as empty immediately, matching the old `.catch(() => {})` that swallowed the error. `budgets`/`config` are `null` while loading or on error (preserving the old `useState<… | null>(null)` semantics); each tab body still renders only when its data is present. Behaviour unchanged.

Added `src/hooks/usePerformanceSettings.test.ts` (parallel fetch surfacing both budgets and config; the error path leaving both `null` and reporting `isError`); each render uses a fresh SWR cache so the constant key doesn't bleed across tests. Validated: `npm run lint` (0 errors, 74→73 warnings; the rule's count drops 33→32 and `PerformancePage` no longer flags it), `type-check`, `build`, and the full vitest suite (348 passed) all green. The rule stays at `warn` until the remaining 32 sites are cleared in later slices.

### Changed (2026-06-20 — Frontend lint: SeedDataPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (34 sites remain after the ValueMetricsPage slice); the sites are cleared in reviewable slices before the rule is promoted to `error`. This slice clears one more site by a real refactor (not a disable):

- `src/pages/settings/SeedDataPage.tsx` — replaced the load-on-mount `useEffect(() => fetchStatus(), [fetchStatus])` (whose `fetchStatus` called `setSeeded`/`setLoading`) with a new SWR hook `useSeedStatus`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns loading/data/error state declaratively, so the page no longer drives state from an effect. The post-mutation `await fetchStatus()` (after load/reset/delete) becomes `await refresh()` (SWR `mutate`).
- `src/hooks/useSeedStatus.ts` (new) — `useSWR` keyed on the constant `'dev-seed-status'` with `shouldRetryOnError: false` so a failed check surfaces immediately like the old `.catch`. `seeded` is `null` while loading or on error (preserving the old `useState<boolean | null>(null)` semantics); the page distinguishes the error case via `isError`. Behaviour unchanged.

Added `src/hooks/useSeedStatus.test.ts` (seeded=true, seeded=false, and the error path surfacing `isError` + `seeded=null`); each render uses a fresh SWR cache so the constant key doesn't bleed across tests. Validated: `npm run lint` (0 errors, 75→74 warnings; the rule's count drops 34→33 and `SeedDataPage` no longer flags it), `type-check`, `build`, and the full vitest suite (346 passed) all green. The rule stays at `warn` until the remaining 33 sites are cleared in later slices.

### Changed (2026-06-19 — Frontend lint: ValueMetricsPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (it flags 35 sites — far more than the `refs`/`immutability` flips, which each touched a single component — so it is cleared in reviewable slices before the rule is promoted to `error`). This slice clears one site by a real refactor (not a disable):

- `src/pages/ValueMetricsPage.tsx` — replaced the load-on-mount `useEffect(() => { setLoading(true); valueMetricsService.get(projectId, days).then(setMetrics)… })` with a new SWR hook `useValueMetrics`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns loading/data state declaratively, so the page no longer drives state from an effect.
- `src/hooks/useValueMetrics.ts` (new) — `useSWR` keyed on `['value-metrics', projectId ?? '__all__', days]`, mirroring the old effect's `[projectId, days]` dependency array. The key is never null, so the page still always fetches (including all-projects mode, where the service omits the `project_id` filter); `onError` preserves the prior toast. Behaviour unchanged.

Added `src/hooks/useValueMetrics.test.ts` (project-scoped fetch, all-projects fetch with no filter, error surfaced + toast). Validated: `npm run lint` (0 errors, 76→75 warnings; the rule's count drops 35→34 and `ValueMetricsPage` no longer flags it), `type-check`, `build`, and the full vitest suite (343 passed) all green. The rule stays at `warn` until the remaining 34 sites are cleared in later slices.

### Added (2026-06-19 — Flaky-Test Intelligence: cross-run step-flip DB read (FLK-P6, slice 3 — assemble))

Picks up the read that slice 2 deferred: it pulls the retained per-run step outcomes from `test_step_runs` (#199), groups them into the oldest→newest per-run window `compute_step_flips` (#200) expects, and returns the step-flip report — so the signal can finally be assembled from real history. New `runs_service.step_flip_report_by_fingerprint(db, project_id, fingerprints, *, since=None, max_runs=25) -> dict[fingerprint, StepFlipReport]`, a sibling of the existing `failing_step_detail_by_fingerprint` (which reads the latest-run snapshot):

- **Two batched queries, never N+1**: resolve the fingerprints to canonical ids (project-scoped via the canonical anchor), then one `test_step_runs JOIN test_runs` read ordered `(canonical, run created_at, run id, ordinal)` — oldest→newest, the order `compute_step_flips` wants — with deterministic run-id/ordinal tiebreaks when `created_at` collides for runs ingested together.
- Folds the flat, ordered rows into per-canonical per-run windows (first-seen run wins ordering; steps accumulate in ordinal order), capping to the most recent `max_runs` runs and honouring an optional `since` bound on `TestRun.created_at`. A resolved canonical with `<2` runs of history maps to an "insufficient history" report (caller distinguishes "no flip" from "no history"); a fingerprint with no anchor is absent. Pure read — the caller's transaction is never mutated.
- **Deferred to a later slice**: the surfacing (Flaky Coach response fields / agent verdict / UI). This slice is the read those will call — no migration, no ingestion/router change.
- New `backend/tests/test_step_flip_read.py` (10 cases, DB stubbed by SQL-dispatch fake) — oscillation flagged as a flip, stable-step no-flip, single-run/no-history insufficient-history reports, project scoping, the two-query batching, `max_runs` capping (drops early oscillation), `since` filtering, and per-ordinal step grouping within a run. Full backend suite (3421 passed) + 15 quality gates + ruff green.

### Added (2026-06-18 — Flaky-Test Intelligence: cross-run step-flip computation (FLK-P6, slice 2 — compute))

Builds on slice 1's `test_step_runs` retention (#199) to compute the signal FLK-P5 explicitly deferred ("left for future work" until per-run step history existed): **which step flipped between runs, how often, and in which direction**. A step that oscillates PASSED↔FAILED across runs reads as *step-level flakiness* — fix/quarantine that one step — rather than a whole-test verdict. New pure, no-DB, never-raise module `backend/app/services/flaky_step_flip.py`, a sibling of `flaky_signals`/`flaky_step_analysis`:

- `compute_step_flips(runs) -> StepFlipReport` over a per-run step-outcome window ordered oldest→newest (the shape `test_step_runs` yields). Steps are matched across runs by `ordinal` (the table's grain and the key FLK-P5 attribution already uses); a flip is a PASSED↔FAILED change between two consecutive runs where the step had a pass/fail outcome. `BROKEN` normalises to `FAILED`; `SKIPPED`/`UNKNOWN` carry no pass-vs-fail signal and are bridged (not counted as a third state that would manufacture spurious flips). Each transition is classified `regression` (PASSED→FAILED) or `recovery` (FAILED→PASSED); per-step roll-ups (`flip_count`, `runs_observed`, `last_status`) are sorted most-flipping-first.
- Defensive by construction: a `<2`-run window reports "insufficient history" (a flip is undefined with one run), malformed rows degrade to a neutral contribution, and the function never raises — safe under `AI_OFFLINE_MODE`.
- **Deferred to a later slice**: the DB read that assembles the per-run window from `test_step_runs` (ordered by run start) and the surfacing (Flaky Coach / agent verdict). This slice is the pure computation those will call — zero blast radius, no migration, no ingestion/router change.
- New `backend/tests/test_flaky_step_flip.py` (14 cases) — pass→fail/regression, fail→pass/recovery, multi-transition oscillation, stable-step no-flip, BROKEN/enum-prefix normalisation, SKIPPED bridging, independent-ordinal tracking, flip-count ordering, latest-name display, the `<2`-run guard, and the never-raise invariant. Full backend suite + 15 quality gates + ruff green.

### Added (2026-06-17 — Flaky-Test Intelligence: per-run step-outcome retention (FLK-P6, slice 1 — capture))

Foundation for cross-run step-flip analysis, fulfilling the schema change FLK-P5 flagged as "left for future work". `test_steps` is a LATEST-RUN-ONLY snapshot (one per `canonical_test_cases`, delete+reinsert on every ingest), so a step-flip — "PASSED in run N-1, FAILED in run N" — cannot be computed from it. This slice starts RETAINING per-run step outcomes without touching the snapshot or any of its readers (zero blast radius):

- **New table `test_step_runs`** (Alembic `0097`, down_revision `0096`, real downgrade) — one compact, flat row per `(canonical_test_case_id, source_test_run_id, ordinal)`. Deliberately minimal: only the step identity (`ordinal`/`depth`/`name`/`keyword`) and the per-run signal (`status`/`duration_ms`). The heavy, PII-bearing columns (assertion message/trace, expected/actual, parameters, attachments) stay ONLY on the latest-run `test_steps` snapshot and are **not** duplicated per run. `source_test_run_id` is **CASCADE** (the row *is* about that run — deleting the run deletes its step history), unlike the snapshot's SET NULL provenance pointer. A unique constraint on `(canonical_test_case_id, source_test_run_id, ordinal)` encodes the idempotency invariant.
- **Ingestion writes the history alongside the snapshot** (`_persist_step_snapshot` / `_insert_step` in `backend/app/services/ingestion.py`), idempotent per `(canonical, run)`: it deletes only **this run's** rows then reinserts them, so re-ingesting a run overwrites its own rows while prior runs' history is retained. Stays inside the ingestion-pipeline transaction — the router still owns the commit (no service-level commit).
- **Deferred to slice 2**: the cross-run step-flip computation + surfacing. Capture must ship first — step-flip is undefined until ≥2 runs of history have accumulated post-deploy.
- New `backend/tests/test_step_run_retention.py` — proves cross-run retention makes a PASSED→FAILED flip observable, per-`(canonical, run)` idempotency on re-ingest, the latest-run snapshot is unchanged (no regression), and the model/migration contract (compact columns, run-CASCADE, real downgrade). Full backend suite (3397 passed) + 15 quality gates + ruff green.

### Changed (2026-06-16 — Frontend lint: promote react-hooks/refs warn → error)

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set: `react-hooks/refs` moves from `warn` to `error` in `frontend/eslint.config.js`. All 18 flagged violations were in a single component and fixed by refactor (not disables):
- `src/pages/TestManagementPage.tsx` — the `CasesFilterBar` component took its props as an undestructured `p` object whose `searchInputRef: RefObject` member made the rule treat *every* `p.*` read as a ref-read-during-render (18 false positives across the search input, checkbox, filter chips, and saved-view buttons). Destructured the props at the parameter so the ref is a named binding forwarded straight to the DOM `ref=` (which the rule allows); the remaining props become plain locals. Behaviour unchanged.

Added `src/pages/TestManagementPage.refs.test.ts` (source-text invariants via `?raw`, matching the sibling suite-aggregates regression) asserting the rule stays at `error` and `CasesFilterBar` keeps its destructured signature and named-binding `ref=`. Validated: `npm run lint` (0 errors, 94→76 warnings), `type-check`, `build`, and the new test all green.

### Changed (2026-06-16 — Frontend lint: promote react-hooks/immutability warn → error)

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set: `react-hooks/immutability` moves from `warn` to `error` in `frontend/eslint.config.js`, with its four flagged violations fixed by refactor (not disables):
- `src/hooks/useLiveExecution.ts` — the WebSocket reconnect timer called `setTimeout(connect, …)` from inside `connect`'s own `onclose`, a forward self-reference the rule rejects (it closes over a stale `connect`). Routed the reconnect through a new `connectRef` kept in sync with the latest `connect` via an effect, so the timer always invokes the current closure.
- `src/hooks/useProjectChange.ts` — the wrapper-hook ref `previousProjectId` was mutated in effects but not named with the `Ref` suffix the rule keys on to recognise an intentional mutable ref. Renamed to `previousProjectIdRef` throughout; behaviour unchanged.

Added `src/hooks/useProjectChange.test.ts` asserting the previous-project tracker still: doesn't fire on first render, fires once per change while enabled, and silently absorbs changes while disabled without replaying them on re-enable. Validated: `npm run lint` (0 errors, immutability warnings gone), `type-check`, `build`, and the hook tests all green.

### Fixed (2026-06-13 — AI-Agent Quality: RegressionWatchman confidence coercion OverflowError (AIQ-P1 cleanup follow-up))

Follow-up to the AIQ-P1 cleanup. `_summarize_classification` in `backend/app/agents/regression_watchman.py` coerced each classification `confidence` inside `try/except (TypeError, ValueError)`, which did **not** catch `OverflowError` from `int(float("inf"))` — and `float("inf")` is reachable because Python's `json.loads` accepts the `Infinity` token, so a degraded LLM payload could still escape the guarded helper and fail the pipeline run. Added `OverflowError` to the except clause (the awkward value now coerces to `0`, like the other non-numeric cases). Extended `test_summarize_classification_defensive_coercion` in `backend/tests/test_agent_contract_outputs.py` with an `inf` case asserting it coerces rather than raises. No behavior change for well-formed input; contract suites green (13 passed).

### Added (2026-06-15 — Flaky-Test Intelligence: granular step-level surgical attribution (FLK-P5))

Final phase of the Flaky-Test Intelligence (FLK) initiative. Where FLK-P1..P4 reason at the *test* level, FLK-P5 drills into the granular `test_steps` snapshot to answer **which step / assertion is the failure** — so the recommendation is a *surgical fix of one step* instead of quarantining the whole test. New pure, no-DB, never-raise module `backend/app/services/flaky_step_analysis.py`:

- `build_step_attribution(first_failing_step, total_steps, failing_step_count) -> StepFailureAttribution` — pinpoints the lowest-ordinal failing step, summarises the assertion (`expected X, got Y`, or the message), classifies assertion-vs-error failures, SHA-**fingerprints the step location** (denoised name + assertion trace, so a consistent step reads as deterministic), and emits a surgical-fix recommendation. Assertion text is redacted + truncated at the boundary.
- **Data-model honesty**: `test_steps` is a LATEST-RUN-ONLY snapshot (one per `canonical_test_cases`, delete+reinsert on ingest), so cross-run step-flip history is not retained. FLK-P5 therefore attributes the failure in the most recent snapshot (the actionable "what to fix now") and fingerprints *where* it fails; true cross-run step-flip would require per-run step retention (a schema change left for future work). Test-level stack-trace fingerprinting already shipped in FLK-P1/P4.
- New batched read `runs_service.failing_step_detail_by_fingerprint(db, project_id, fingerprints)` — resolves fingerprints to their project-scoped canonical anchors and folds the snapshot into `{first_failing, total_steps, failing_step_count}` per fingerprint (two batched queries, no N+1).
- **Surfaced** on `/flaky-coach` (`FlakyCoachEntry.failing_step` + `failing_step_detail`, read-time, **no migration**, best-effort) and in the FLK-P4 agent verdict (`flaky_sentinel_agent` attaches a `step_attribution` block per finding). The Flaky Coach page renders the failing step + surgical recommendation.
- New `backend/tests/test_flaky_step_analysis.py` (assertion summary, surgical phrasing, stable noise-free fingerprint, redaction, never-raise); `test_flaky_signals.py` updated for the read-time step query. Full backend suite + 15 quality gates + architectural ratchets green.

### Added (2026-06-14 — Flaky-Test Intelligence: agentic flaky investigator + likely-cause (FLK-P4))

Fourth phase of the Flaky-Test Intelligence (FLK) initiative. The `flaky_sentinel_agent` is upgraded from a lifecycle reporter into a multi-step **investigator that confirms _and explains_ each flaky verdict**, emitting a structured `{is_flaky, confidence, likely_cause, evidence[]}` per AIQ-P1/P3. The reasoning lives in a new pure, no-DB, never-raise module `backend/app/services/flaky_investigator.py` so it is unit-testable and reusable by the read-time surfaces:

- `cluster_failures(records)` — groups the recent window's FAILED rows by error signature + stack fingerprint (FLK-P1 helpers, now exposed as public `error_signature` / `stack_fingerprint`) into an explainable cluster summary (distinct signatures/stacks, dominant error, examples).
- `determine_likely_cause(signals, ml_confidence=None)` — maps the FLK-P1 intermittency signals (+ optional FLK-P3 ML confidence) to a `(human_text, stable_code)` cause: `in_run_retry` · `environmental` · `race_condition` · `likely_regression` · `intermittent` · `low_volatility` · `insufficient_data`. A confident "not a flake" from the ML model overrides the heuristics.
- `build_flaky_verdict(...)` — assembles the structured verdict; `confidence` is the **AIQ-P3 evidence-weighted aggregate** (`aggregate_confidence` over `EvidenceRef`s for volatility, error/stack clustering, in-run retries, the ML score, the Wilson interval, and build changes) so it carries an auditable breakdown and the "high confidence needs strong evidence" cap. `is_flaky` is false for a confirmed regression / insufficient data.
- The agent now joins the per-run `TestCase` meta into its history window, computes signals + Wilson CI + ML confidence + clusters, and attaches the verdict (plus flat `is_flaky` / `likely_cause`) to each finding. The quarantine state machine and proposal path are unchanged.
- **Surfaced on `/flaky-coach`** (`FlakyCoachEntry.flaky_likely_cause` computed at read time from signals + the persisted ML confidence — **no migration**) and on **`/failures`** (`analytics_service.flaky_tests` attaches `likely_cause` via one bounded, tenant-scoped windowed query; best-effort so the list always renders). Both frontends render the likely cause.
- New `backend/tests/test_flaky_investigator.py` (clustering, every likely-cause branch incl. the ML-skeptic override, verdict evidence/confidence cap, never-raise); `test_flaky_failures_consistency.py` updated for the enrichment query. Full backend suite + 15 quality gates + architectural ratchets green.

### Added (2026-06-14 — Flaky-Test Intelligence: ML flakiness-confidence classifier (FLK-P3))

Third phase of the Flaky-Test Intelligence (FLK) initiative. Adds a focused ML model that scores **`is_flaky_confidence` ∈ [0, 1]** for each flaky verdict, learned from the team's own quarantine approve/reject decisions — it complements (does not replace) the 6-class triage classifier and the deterministic Wilson/intermittency signals. New module `backend/app/services/ml/flaky_confidence.py` deliberately mirrors the existing ML infra (`ml/classifier.py` + `ml/trainer.py`): versioned `flaky_confidence_v*.joblib` artifacts under `ML_MODEL_DIR`, a module-level cache with periodic hot-swap, a persisted feature contract validated before a model is trusted, and **graceful degradation** — absent `scikit-learn`/`joblib` or an absent model makes `predict()` return `None` (never raises), so the leaderboard falls back to the deterministic signals.

- **Features** (8, train/serve-parity via one pure `build_flaky_feature_vector`): `failure_rate`, `status_volatility`, `flip_count`, `error_signature_diversity`, `failure_rate_trend` (recent-half minus older-half failure rate), `run_interval_variance` (coefficient of variation of inter-run gaps), `in_run_retry_rate`, `stack_trace_diversity`. Most are reused from FLK-P1's `IntermittencySignals`; the trend + interval-variance are computed here.
- **Ground truth**: `FlakyQuarantineRequest` outcomes — APPROVED/QUARANTINED/RE_QUARANTINED = confirmed flake (1), REJECTED = confirmed non-flake (0); undecided states excluded. Binary `HistGradientBoostingClassifier`; **AUC** is the deploy gate (accuracy is misleading on the imbalanced flake set).
- **Training**: `train_flaky_confidence_model()` + a nightly Celery beat `nightly-flaky-confidence-training` (03:30 UTC). No-op-safe (`insufficient_data` / `insufficient_class_diversity` / `error` status strings) until enough labeled decisions exist. The model is a LOCAL scikit-learn artifact — no outbound calls — so it is unaffected by `AI_OFFLINE_MODE`.
- Migration **0096** (`down_revision = 0095`, real `downgrade()`) adds nullable `flaky_coach_results.is_flaky_confidence`. `refresh_flaky_coach` predicts + persists it and appends an advisory stabilization line (model-confirmed ≥70% / skeptical ≤30% / uncertain); `get_flaky_coach` surfaces it on a new optional `FlakyCoachEntry` field; the leaderboard adds it as a `NULLS LAST` ranking key between `impact_score` and the Wilson lower bound (surface high-confidence flakes first). The quarantine state machine is untouched.
- Frontend: `FlakyCoachEntry` type + Flaky Coach page render the ML confidence (colour-coded likely-flake / likely-real-failure / uncertain) in the expanded detail.
- New `backend/tests/test_flaky_confidence.py`: feature-vector correctness + never-raise, inference graceful degradation (no model / broken model / single-class), a trained-sklearn round-trip asserting separation, training degrade path, and feature parity with `compute_intermittency_signals`. `test_flaky_signals.py` extended to surface the persisted field.

### Added (2026-06-14 — Flaky-Test Intelligence: statistical confidence interval on flaky verdicts (FLK-P2))

Second phase of the Flaky-Test Intelligence (FLK) initiative. Every auto-detected flaky verdict now carries a **statistical-confidence band** on its failure ratio so the leaderboard can distinguish a well-evidenced flake (e.g. `30/100`) from a thin one (`3/10`) that share the same point estimate. A new pure, no-DB, never-raise module `backend/app/services/flaky_statistics.py` computes the **closed-form Wilson score 95% interval** (`wilson_failure_confidence(failures, total) -> FailureRatioConfidence`) — no `scipy` dependency. Wilson is preferred over the normal (Wald) approximation because it stays inside `[0, 1]` and is well-behaved for the small-`n` / extreme-`p̂` regime flaky tests live in. Degenerate inputs (`total<=0`, `failures>total`, non-numeric, non-finite `z`) degrade to a neutral band rather than raising.

- Migration **0095** (`down_revision = 0094`, real `downgrade()`) adds two nullable `Float` columns to `flaky_coach_results`: `flaky_confidence_low` / `flaky_confidence_high`. Nullable because historical cached rows and manual-triage leaderboard entries carry no statistical interval until the next `refresh_flaky_coach` recompute.
- `refresh_flaky_coach` computes and persists the interval; `get_flaky_coach` surfaces it on new optional `FlakyCoachEntry` fields (`flaky_confidence_low/high`).
- **Ranking by statistical strength**: `impact_score` already scales with `log2(total_runs)` (so `30/100` already outranks `3/10`); the Wilson lower bound is added as a deterministic `NULLS LAST` secondary sort key so equal-impact rows order by statistical strength.
- Frontend: `FlakyCoachEntry` type gains the FLK-P1/P2 optional fields and the Flaky Coach page renders the **"Failure rate 95% CI (Wilson)"** band in the expanded test detail.
- **No new service commit**, **no outbound calls** — `AI_OFFLINE_MODE` semantics untouched. New `backend/tests/test_flaky_statistics.py` (closed-form correctness vs an independent reference, the `30/100 > 3/10` lower-bound ranking property, interval-narrows-with-sample-size monotonicity, extremes at `p̂=0`/`p̂=1`, and never-raise on degenerate input); `test_flaky_signals.py` extended to assert the persisted band is surfaced.

### Added (2026-06-13 — Flaky-Test Intelligence: intermittency + error-signature analysis (FLK-P1))

First phase of the Flaky-Test Intelligence (FLK) initiative. Flaky verdicts now carry **intermittency signals** that discriminate a *high-volatility flake* (flips pass↔fail with many distinct errors / in-run framework retries) from a *low-volatility regression* (fails persistently with a single repeated error — a real bug that should **not** be quarantined on the flake track). A new pure, no-DB, never-raise scorer `backend/app/services/flaky_signals.py` defines `compute_intermittency_signals(records) -> IntermittencySignals` over a per-run window:

- **status_volatility** = `flips / (runs - 1)` (adjacent pass↔fail changes)
- **error_signature_diversity** = unique denoised error-prefix signatures / fail_count
- **stack_trace_diversity** = unique stack-trace fingerprints / fail_count (many unique ⇒ environmental/race; one ⇒ deterministic bug)
- **in_run_retry_rate** = fraction of runs the framework recorded an in-run retry / flaky flag (granular `retry_count` / `is_flaky_run` from PR #169 — a strong in-run flake signal)
- **intermittency_label** ∈ `intermittent_flaky` · `environmental_flaky` · `low_volatility_flaky` · `persistent_regression` · `insufficient_data`

`refresh_flaky_coach` joins the granular `TestCase` meta (`error_message`, `stack_trace`, `retry_count`, `is_flaky_run`) into its existing windowed query and uses the label to refine the advisory verdict: a `persistent_regression` that would otherwise be `QUARANTINE` is downgraded to `INVESTIGATE` (the quarantine **state machine is untouched** — only the advisory recommendation string changes), and signal-aware stabilization lines are appended. `get_flaky_coach` scores intermittency at read time for the bounded leaderboard set (one extra batched query) and surfaces the numeric signals on new optional `FlakyCoachEntry` fields, so `/flaky-coach` shows the new signal. **No migration** (read-time compute + verdict refinement only), **no new service commit**, **no outbound calls** — `AI_OFFLINE_MODE` semantics untouched. New `backend/tests/test_flaky_signals.py` (16 tests) covers discrimination, granular retry/stack signals, noise normalisation, never-raise on malformed input, and the service-level refinement + surfacing.

### Fixed (2026-06-13 — AIQ-P1 cleanup: RegressionWatchman never-raise + contract field-drop parity)

External review of the merged AIQ-P1 contract work surfaced two empirically-reproduced defects in `backend/app/agents/regression_watchman.py` and `backend/app/models/agent_contracts.py`, both now fixed:

- **MAJOR — success path could raise (never-raise regression).** `RegressionWatchman.run()`'s non-error branch computed `confidence`/`evidence_refs` inline over `classification.values()`. A classification merged back from a partially-validated LLM payload can hold a **non-dict value** (`v.get` → `AttributeError`) or a **non-numeric `confidence`** such as `'high'` (`int('high')` → `ValueError`), which raised into the graph node wrapper and **failed the whole pipeline run** — the exact failure the contract layer exists to prevent (live-LLM only; offline was already safe). Confidence/evidence derivation moved into a guarded `_summarize_classification(...)` helper that skips non-dict values and wraps each `int(...)` (default `0`; empty → `100` as before), so the success path can **never** raise.
- **MINOR — silent field drop.** `LogIntelligenceAgentOutput` and `RegressionWatchmanAgentOutput` lacked `model_config = ConfigDict(extra='allow')` (only `RunCompareAgentOutput` had it), so an undeclared payload key was silently dropped on `model_validate`. Both now set `extra='allow'` for parity, preserving undeclared nested keys.

Regression tests added in `backend/tests/test_agent_contract_outputs.py`: a `run()`-level test feeding a malformed classification with **both** a non-dict value and a non-numeric `confidence` (asserts no raise + valid contract), a direct helper-coercion test, and undeclared-key-survival tests for both contracts. AIQ-P1 ratchet + behavioral suites green; 15/15 quality-gate guards and architectural ratchets green.

### Added (2026-06-12 — AI-Agent Quality: report-quality eval harness for agent outputs (AIQ-P5))

Fifth phase of the AI-Agent Quality (AIQ) initiative. AIQ-P5 adds a **pure-local, offline-safe, never-raise** scorer for **RECORDED** agent outputs plus a golden-set **CI gate** that fails the build if an agent change regresses report quality. A new module `backend/app/services/agent_eval_harness.py` defines `evaluate_agent_outputs(samples) -> AgentEvalReport`, scoring 5 report-quality metrics over a list of `AgentEvalSample`: **coherence** (per-sample invariants), **completeness** (evidence present when `confidence >= 60`), **actionability** (a fix/action present when the verdict is non-flaky), **accuracy** (`verdict == ground truth`), and **calibration** — Brier score `mean((conf/100 - outcome)^2)` and ECE `sum over 10 bins of (|S_b|/N)*|acc_b - conf_b|`. The report carries `per_metric_pass` + an `overall passed`, gated by `PASS_THRESHOLDS` (`coherence >= 0.90`, `completeness >= 0.90`, `actionability >= 0.85`, `accuracy >= 0.70`, `brier <= 0.20`, `ece <= 0.15`) with `MIN_SAMPLES = 5` (insufficient data fails). The scorer **never raises**, makes **no DB**, and has **no outbound/LLM imports** (offline by construction). A golden set `backend/app/services/golden_agent_outputs.py` records agent outputs + ground truth (passes all thresholds, accuracy `0.889`) plus negative fixtures and an always-wrong under-confident fixture. Two additive helpers: `compute_agent_report_quality(samples) -> dict` (`backend/app/services/ai_eval_service.py`) and `evaluate_agent_report_quality_rules(report) -> list` of gate rule dicts (6 metric rules + an overall `agent_report_quality`, `backend/app/services/eval_gate_service.py`). The CI gate `backend/tests/test_architectural_agent_eval_harness.py` runs in the existing **backend-test** pytest job and fails CI if an agent change regresses calibration / evidence / actions / accuracy on the golden set, alongside the `backend/tests/services/test_agent_eval_harness.py` unit suite. Design note: an adversarial review found a calibration-only gate let an **under-confident, always-wrong** agent pass, so the **accuracy** metric was added to close that bypass. **No migration, no outbound calls, offline by construction** — `AI_OFFLINE_MODE` semantics untouched.

### Added (2026-06-12 — AI-Agent Quality: gap-detection + report-refinement agents (AIQ-P4))

Fourth phase of the AI-Agent Quality (AIQ) initiative. AIQ-P4 adds two **pure-local, offline-safe, never-raise** reconciliation agents that audit and reconcile the deep-workflow output, both wired **OPTIONALLY** behind a new default-off flag so there is **zero runtime impact until enabled**. A new module `backend/app/agents/gap_detection_agent.py` defines **GapDetectionAgent**: for every failed test it audits whether the test was analyzed / skipped / errored and enforces the referential-integrity invariant `analyzed_count + skipped_count + errored_count == failed_count`, emitting a validated `GapDetectionAgentOutput` whose `gap_report` carries `failed_count` / `analyzed_count` / `skipped_count` / `errored_count`, `coverage_ratio` (forced to `1.0` when there are no failures), `integrity_ok` (self-recomputed by a `@model_validator`), `inconclusive_count` / `no_evidence_count`, and a `gaps[]` list of `GapItem{test_id, reason ∈ unanalyzed|errored|inconclusive|no_evidence|low_confidence, detail (structural tokens only), bucket ∈ analyzed|skipped|errored}`. A second module `backend/app/agents/report_refinement_agent.py` defines **ReportRefinementAgent**: it reconciles the parallel anomaly / analysis / cluster signals — **deduping** a test analyzed by ≥2 routes (deterministic precedence `analysis > anomaly > cluster`), **detecting contradictions** (e.g. `flaky_vs_regression` when analysis says `is_flaky` but the test is a fresh regression), and **resolving** them (`prefer_analysis` | `prefer_anomaly` | `merge` | `flag_for_review`) — and emits a validated `ReportRefinementAgentOutput` whose `refined_report` carries `dedup_count`, `contradictions[]` (`Contradiction{test_id, type, routes, resolution, detail}`), `contradictions_resolved` / `unresolved_count` (self-recomputed from the contradiction list by a `@model_validator`), `multi_route_test_ids`, and `reconciled_tests`. Both stages are wired into the **DEEP** workflow graph (`backend/app/agents/workflow.py`) behind the new config flag **`AIQ_GAP_REFINEMENT_ENABLED`** (default `False`, `backend/app/core/config.py`): the **graph topology is identical whether the flag is on or off** — when off the nodes early-return a skip delta (`skipped_stages`), and when on the chain runs `summary → (triage) → gap_detection → report_refinement → flaky_sentinel → test_health → release_risk → END`. Both stages are added to `DEEP_OPTIONAL_STAGES` and to the agent_planner's `_DEEP_STAGES` (`backend/app/services/agent_planner.py`, flag-gated). New Pydantic v2 models in `backend/app/models/agent_contracts.py`: `GapReport`, `RefinedReport` (plain nested models with self-recomputing `@model_validator`s), `GapDetectionAgentOutput`, `ReportRefinementAgentOutput`, `GapItem`, `Contradiction`, and the enums `GapReason` / `ContradictionType` / `ResolutionStrategy`; the contracted-output-model ratchet floor is raised `12 → 14`. The offline gate is honored (`AI_OFFLINE_MODE=True` default): both agents are deterministic in-memory reconciliation with **no LLM, network, or DB calls**, and any malformed / non-dict input degrades to a fallback contract — they **never raise**. **No migration (pure in-memory, no new DB columns), no outbound calls, no new service commits** — `AI_OFFLINE_MODE` semantics untouched. New tests `backend/tests/test_gap_detection_agent.py`, `backend/tests/test_report_refinement_agent.py`, and `backend/tests/test_aiq_p4_workflow_wiring.py` (30 tests: integrity invariant, dedup precedence, contradiction taxonomy/resolution, never-raise, flag-on/flag-off topology, and skip-delta wiring).

### Added (2026-06-12 — AI-Agent Quality: structured evidence + confidence scoring (AIQ-P3))

Third phase of the AI-Agent Quality (AIQ) initiative. AIQ-P3 adds a **pure-local, additive evidence + confidence-scoring layer** so an agent's confidence is a weighted, capped function of named evidence rather than a hand-tuned constant — and the rationale is surfaced for audit. A new shared module `backend/app/agents/evidence.py` provides an `EvidenceRef` Pydantic v2 model (`source`, `ref_id`, `excerpt`, `strength` ∈ weak|medium|strong, `contribution` 0-100) and an `aggregate_confidence(refs) -> (final, breakdown)` helper. All `EvidenceRef` coercion lives in `field_validator(mode="before")` (None→"" strings, lowercased/fallback strength, contribution clamped 0-100, **excerpt redacted via `redact_text` then truncated to 240 chars + "…"** at the model boundary) so a malformed field degrades to its default and the model **never raises**. `aggregate_confidence` computes a strength-weighted mean (weak=1, medium=2, strong=3), rounds and clamps to [0, 100], then applies the **cap rule: any confidence >70 requires ≥1 strong OR ≥2 medium sources**, else it is capped to 70 with `cap_applied`/`cap_reason` recorded; empty/all-invalid input yields `(0, zero-breakdown)` and non-`EvidenceRef` items are filtered. `AgentContractMetadata` gains an optional `confidence_breakdown: Optional[dict]`, and `validate_agent_contract(...)` gains a keyword-only `structured_evidence=None`: when supplied it derives `confidence_score` from the aggregate (an **explicit `confidence` kwarg still wins**), auto-populates legacy `evidence_refs` from `as_legacy_dict()` when the caller passed none, and stamps the `confidence_breakdown` — all inside the existing try/except so it **never raises** (the `aggregate_confidence` import is lazy to avoid a `models → agents` cycle). Two adopters move onto structured evidence: **LogIntelligenceAgent** emits `distributed_trace` / `log_anomaly` medium refs (contribution 80, so both signals present preserve the prior confidence of 80 while a single source honestly caps to 70) and lets the contract own the score; **ReleaseRiskAgent** emits a `score_model` strong ref (contribution `100 − risk_score`) plus a weak `consistency_report` ref — existing `decision_reason`, consistency suffix, `log_decision`, and `BaseAgent` structure unchanged. **No behavior change to the analytic payloads, no migration, no outbound calls, no new service commits, no print/logging side effects** — `AI_OFFLINE_MODE` semantics untouched. New tests `backend/tests/test_evidence_confidence.py` (unit + property coverage of the cap, redaction/truncation, weighted mean, and never-raise invariants) and two additive ratchets in `backend/tests/test_architectural_agent_contracts.py` (`confidence_breakdown` field presence + the cap invariant), with `evidence.py` added to `INFRA_ALLOWLIST`.

### Added (2026-06-12 — AI-Agent Quality: self-critique / verification pass (AIQ-P2))

Second phase of the AI-Agent Quality (AIQ) initiative. AIQ-P2 adds a **pure-local, additive self-critique layer** so the analytic agents catch their own internal contradictions instead of emitting confident-but-incoherent output. A new shared module `backend/app/agents/consistency.py` (`ConsistencyCheck` / `ConsistencyReport` Pydantic v2 models, `log_consistency_failures`, and three checkers) is wired into **SummaryAgent**, **ReleaseRiskAgent**, and **AnalysisAgent** on the success path immediately before `validate_agent_contract(...)` — so every consistency result flows into the existing `agent_contracts` metadata (`decision_reason` gains a `; consistency_ok` / `; consistency_check_failed:<names>` suffix and one extra `consistency_report` evidence ref) **without changing any agent's output shape**. Failures are logged as a single structlog `consistency_check_failed` event per failed check (the event name lives in `consistency.py` only — a new ratchet enforces this) and are **never silently dropped**; the underlying analysis payload is read-only to the checker and is never mutated. The checks are **pure functions** of already-computed data: no outbound calls, no DB, no LLM, no migration, and — by hard invariant — **they never raise on any input shape** (all list-typed fields are isinstance-guarded; non-data is treated as "nothing to evaluate ⇒ passed"). **SummaryAgent** verifies referential integrity of every cited test id (cited ids must exist in `failed_test_ids ∪ analyses` keys, compared as strings so int/UUID keys don't false-positive; an empty analyzed universe with cited ids is itself flagged) plus cross-layer coherence (exec-summary ⇄ incident-view ⇄ release-impact ⇄ criticality ⇄ action-plan). **ReleaseRiskAgent** verifies the narrative never contradicts the deterministic score band (`<20 GO`, `20–55 CONDITIONAL_GO`, `≥55 NO_GO`, matching `score_to_recommendation`) using **negation-aware** phrase matching so a correct "not low risk" NO_GO is not flagged, and downgrades a *more-conservative* recommendation (e.g. a pass-rate-floored NO_GO at a low score) or a `policy_id` override from error to warning. **AnalysisAgent** gains expanded confidence-correction rules in `_validate_confidence` (UNKNOWN-category caps, error⇒zero-confidence floor, raw-confidence-vs-evidence cap, flaky-contradicted-by-history) that only ever *lower or correct* values and record an auditable adjustment, plus a stage-level coverage/flaky-floor consistency check. **No behavior change to the analytic payloads, no migration, no outbound calls, no new service commits** — `AI_OFFLINE_MODE` semantics untouched. New tests `backend/tests/test_agent_consistency.py` (DB-free behavioral coverage incl. the never-raise, int-key, negation, and conservative-override cases) and a new ratchet `test_target_agents_run_consistency_checks` in `backend/tests/test_architectural_agent_contracts.py`.

### Added (2026-06-12 — AI-Agent Quality: structured agent contracts (AIQ-P1))

First phase of the AI-Agent Quality (AIQ) initiative — making the analytic agents (test reporting, gap-finding, report analysis, engineering-intelligence) a **solid, auditable** signal. AIQ-P1 ratchets the **structured agent-contract** discipline: every analytic agent under `backend/app/agents/` must wrap its output through `validate_agent_contract(...)`, which stamps audit-friendly metadata (`confidence_score` clamped 0-100, `evidence_count`, `decision_reason`, `evidence_refs`, `fallback_used`, `generated_at`) under the output's `agent_contracts` key **without changing the agent's own output shape** (the metadata is additive; validation failure logs a structlog warning and returns the original payload stamped with `contract_validation_error` — it never raises, so the deterministic fallback is guaranteed). Three previously-unwrapped agents are brought under contract — **RunCompareAgent** (both the parsed-success and exception-fallback returns; `RunCompareAgentOutput` uses `extra="allow"` so arbitrary LLM keys pass through untouched), **LogIntelligenceAgent** (tiered confidence + per-source evidence refs derived from trace/anomaly success), and **RegressionWatchman** (`run()` success + error returns; the standalone `_classify`/`run_regression_watchman` path is deliberately left raw). `AgentContractMetadata` gains the explicit `confidence_score`/`evidence_count` fields (derived inside `validate_agent_contract` from the existing `confidence`/`evidence_refs` kwargs, so **none of the 9 prior callers change**) plus a Pydantic v2 `field_validator` clamping `confidence_score` to 0-100. **No behavior change, no migration, no outbound calls, no new service commits** — `AI_OFFLINE_MODE` semantics untouched. New ratchets: `backend/tests/test_architectural_agent_contracts.py` (AST scan asserting every non-infra agent calls `validate_agent_contract`, the required metadata fields exist, and the contracted-output-model count holds at its ≥12 floor — a downward-ratchet guard) and `backend/tests/test_agent_contract_outputs.py` (runtime coverage of the LogIntelligence success/fallback wrapping and the RegressionWatchman contracted output shape).

### Added (2026-06-10 — Granular steps in MCP + CLI (Phase 6))

Phases 1-5 captured and surfaced the LATEST-RUN-ONLY granular step/assertion snapshot across the UI and REST API, but the **MCP server** and **CLI** — the surfaces an AI assistant or a terminal user actually drives — still stopped at the top-line test outcome, so they couldn't answer *where* a test broke without a separate API round-trip. Phase 6 closes that gap and **completes the initiative**. It is purely **additive and best-effort** over the existing read endpoint `GET /runs/{run_id}/tests/{test_id}/steps` (no migration, no backend change, no new write): every new path fetches steps lazily and **degrades silently** when the snapshot is absent (older runs have none), so no existing tool/command output changes when steps aren't present. (1) MCP `get_test_case` gains an opt-in `include_steps` flag that renders the nested step tree as a readable markdown table (step #/indent, name, status, duration, truncated assertion) — default output unchanged. (2) MCP `trigger_ai_analysis` accepts an optional `run_id` and, when given, adds the first FAILED/BROKEN step to the Evidence References as `step N failed: <assertion>` so the agent's root-cause output points at the failing assertion. (3) MCP `search_tests` annotates a result's subtitle with `matched on step text` when the query isn't visible in the test name/suite/error (the Phase 3 keyword index also matches step text), explaining why the hit surfaced. (4) CLI `tests get` gains `--show-steps`, summarising step count + first/last step + the failing step name & assertion. MCP tool **names are unchanged** (a separate branch owns the tool-name collision work). Regression: `mcp/tests/test_mcp_server.py` adds static coverage for each surface (include_steps option + step table, `step N failed` evidence helper, step-match subtitle note).

### Fixed (2026-06-10 — Granular initiative: stale model stub in batch5 runs_service test)

`tests/services/test_batch5_all_projects.py::test_list_project_runs_no_filter_builds_correct_query` stubs `app.models.postgres` with a hand-built `SimpleNamespace` and force-reimports `app.services.runs_service`. Phase 5 added `CanonicalTestCase` to the module-top `from app.models.postgres import (...)` in `runs_service.py` (for the batched canonical step helpers), but the stub — already updated for the Phase 1 `TestStep`/`TestAttachment` imports — was not extended, so the forced reimport raised `ImportError: cannot import name 'CanonicalTestCase'`. The test failed in isolation and in the full suite (not ordering pollution). Fix: rather than enumerate yet another import name (the stub had already broken twice this way), make the fake module **auto-provide** any import-only model via `__getattr__`, so future `from app.models.postgres import X` additions can't re-break the test; only models whose attributes are read at import time (`TestRun.primary_suite_name`) stay explicit. Also dropped the dead `FakeDB`/`FakeResult`/`captured_queries` scaffolding the test never exercised and corrected its docstring to match what it actually asserts (the optional `project_id` signature). Test-only; no product-code change. Surfaced by a full-suite run during crash-recovery re-verification of the granular initiative; stub hardening added after multipass-review of the original fix.


### Added (2026-06-10 — Granular steps in reports/coverage/my-failures (Phase 5))

Phases 1-3 captured a granular step/assertion snapshot per logical test (LATEST-RUN-ONLY, anchored to `canonical_test_cases`), but that detail lived only on the test-case page — the report, analytics, and triage surfaces still showed only the top-line outcome, so an engineer still had to open the test to learn *where* a test broke. Phase 5 surfaces that signal across those high-traffic views. It is pure **enrichment**: every new field is an **additive, OPTIONAL** field on an existing Pydantic v2 response schema (defaults to `None`), so existing consumers are unaffected; there is **no migration** and **no new ingestion write** — these are reads, the service stages/returns and the router owns any commit. All step reads go through **batched** helpers keyed by a *set* of canonical ids / fingerprints (`runs_service.first_failed_step_by_canonical`, `first_failed_step_by_fingerprint`, `step_success_by_canonical`, plus the report service's own batched canonical+steps lookup) so no surface ever N+1s over individual tests. Suite grouping reuses `_effective_suite_sql()` so step labels line up with the existing breakdown, and fingerprint/canonical reads stay project-scoped via the snapshot's project-bound anchor (no cross-tenant leak — multi-tenant/unscoped analytics views leave the field `None` rather than resolve a snapshot).

- **my-failures `last_failure_step`.** Each `MyFailureItem` carries the name of the first FAILED/BROKEN step from the test's latest-run snapshot, batched once per page over the rows' canonical ids; rendered as a "failed at:" line on each inbox row when present.
- **analytics FAILURE LOCATION.** `top_failing_tests` attaches `failure_step` (first FAILED/BROKEN step name) to each top-failing test, resolved by `test_fingerprint` within the in-scope project and surfaced on the failure-analysis "what's failing" card.
- **summary-report step success-rate.** Each `SummarySuiteRow` gains optional `step_success_rate` / `passed_steps` / `total_steps` (share of captured steps that passed, per effective suite); the Summary Report suite table renders a "Step %" column only when at least one suite has step data. Each top-failing test also carries `failure_step` + an ordered `step_breakdown`.
- **PDF step breakdown.** `summary_report_pdf` renders an engineering "failure step breakdown" section for failing tests that have a snapshot — the ordered step list with status (failed/broken cells highlighted) and the failing step's assertion message — rendered over a bounded window centred on the failure location (capped per test, elided-count footer) so a 2000-step test can't blow up the flowable. `reportlab` is not installed locally, so the render-path test is import-guarded/skipped locally and runs in CI.

Regression suites: `backend/tests/test_granular_reports.py` (the batched step-read helpers + the no-N+1 / batched guarantee asserted by counting `execute` calls + project-scoping of the fingerprint helper), `backend/tests/test_my_failures_router.py` (`last_failure_step` enrichment + absence default), `backend/tests/test_summary_report_service.py` + `test_summary_report_router.py` (per-suite step success-rate + per-test failure-step/step-breakdown), `backend/tests/test_summary_report_pdf.py` (render-guarded engineering step section), `backend/tests/regression/test_summary_report_flaky_and_effective_suite.py` (effective-suite grouping still holds with the enrichment).

### Added (2026-06-10 — Duplicate test-case detection (Phase 4))

Phase 4 is the feature's end goal: a tiered, **offline-first, per-project** detector that finds near-duplicate **authored** test cases (`managed_test_cases` — never the execution `test_cases`) so a QA lead can review and merge them instead of re-discovering the same coverage. Detection runs in three tiers, all stdlib/local — no new dependency (`rapidfuzz` deliberately avoided): **Tier 0** is a normalised content fingerprint (sha256 over lowercased/whitespace-collapsed title+objective+steps+expected) that buckets exact matches in one equality scan; **Tier 1** is the structural tier — stdlib `difflib.SequenceMatcher` plus a pure-Python token-set/Jaccard ratio, made word-aware so opposite-sense titles (`valid`/`invalid`, `200`/`404`, `login`/`logout`) are demoted by a discriminating-token band cap instead of inflating on spurious character overlap, with an order-insensitive step-set so reordered-but-identical steps still match; **Tier 2** is an optional semantic pass over only the already-blocked cases, gated on a **local** ChromaDB ONNX embedder (never a cloud embedding function, satisfying the `AI_OFFLINE_MODE=True` default) and skipped silently on any import/client error. To stay off the O(n²) all-pairs path, cases are only compared when they share a **blocking key** (same suite, an overlapping tag, or a shared *rare* title token); blocks, the total pair budget, the per-project case count, and the semantic case count are each capped, and a capped run is flagged `sampled` with a human-readable `note`. Every candidate is **explainable**: it carries per-component scores, a human-readable `reason` (what the pair shares + how it differs), the `method` (fingerprint|structural|semantic) and a `band` (exact|strong|possible). Candidates are idempotent per `(project_id, case_a_id, case_b_id)` with canonical ordering (`case_a_id < case_b_id`, enforced by a DB CHECK + unique constraint), and a dismissed pair stays dismissed across re-runs via a `dismissed_duplicate_pairs` suppression table. Surfaced through `GET/POST /api/v1/projects/{project_id}/duplicate-candidates` (+ `/detect`, `/{id}/dismiss`, `/{id}/merge`), each guarded by `require_project_access()` verifying the **provided** `project_id` (IDOR ratchet); the detection **service stages only** (zero commits — the transaction-boundary ratchet stays green) while the router and the nightly Celery beat task own the commit, the beat task fanning out one sub-task per project so a single slow project can't starve the sweep under the task time limit. A new **Duplicates tab** on Test Management renders the banded review queue with the score chips, the reason, and detect/dismiss/merge actions. **MERGE IS NON-DESTRUCTIVE this phase** (pending product sign-off): resolving a pair only flips the candidate `status` to `merged` and may soft-deprecate the losing case (status flag + `is_stale`) — it never deletes a case, redirects `test_fingerprint`, or destroys data. Migration `0094` adds `managed_test_cases.dup_fingerprint` (indexed, lazily populated — no backfill) plus the `duplicate_test_case_candidates` and `dismissed_duplicate_pairs` tables. Regression suites: `backend/tests/test_duplicate_detection.py` (Tier 0-2 scoring, blocking, per-project scope, dismissal suppression, non-destructive merge), `backend/tests/test_duplicates_router.py` (IDOR, detect/dismiss/merge/list HTTP behaviour, commit ownership), `backend/tests/test_duplicate_detection_task_fanout.py` (beat fan-out + per-project commit).

### Added (2026-06-09 — Granular steps for Playwright/Cypress/TestNG + step search (Phase 3))

Phase 3 extends the granular step snapshot to the three remaining parsers (Playwright, Cypress, TestNG) so they emit the **same common step dict** Allure/pytest already produce and flow through the unchanged Phase 1 `_persist_step_snapshot`/`_insert_step` persistence — **no new migration, no new persistence code**, inheriting the shared depth/node caps + PII redaction for free. TestNG synthesizes coarse pseudo-steps preserving the `<failure>`→FAILED / `<error>`→BROKEN distinction (+ reporter/system-out log pseudo-steps) and now surfaces `stack_trace`; Cypress emits a single assertion step carrying its unique structured `expected`/`actual` chai diff; Playwright emits the native nested step tree (status from per-step `error` presence) + a synthetic step per remaining `errors[]` entry and carries `retry_count`/`is_flaky_run`/`stack_trace`. The `RunDetailPage` test-row list shows a `step_count` badge so a test's granularity is visible without opening the steps panel. It also surfaces step text in search: the keyword path matches a project-scoped correlated `EXISTS` over the test's own canonical steps, and the semantic embedding document now concatenates step names + assertion messages. Regression suites: `test_granular_steps_ingestion.py` (TestNG failure/error mapping), `tests/services/test_cypress_playwright_parsers.py` (Cypress expected/actual, Playwright steps/errors/flaky), `tests/regression/test_search_step_text_indexing.py` (keyword EXISTS + project scope + semantic doc step text).

- **Search surfacing — gate (c) accepted deviations (documented, not silent).** The keyword step-match is a project-scoped correlated `EXISTS` on the test's own canonical (NULL canonical → no match) under the always-`project_id`-bound outer query — offline-safe (pure SQL, no embeddings). For the **semantic** path, two pre-existing architecture choices in `semantic_search.py` are explicitly recorded rather than left implicit, since Phase 3 only widens the embedded document (it does not change the collection or embedder): (1) **single shared ChromaDB collection** isolated by a query-time `project_id` metadata filter + a Postgres-layer re-filter (defence-in-depth), rather than a physical per-project collection — no row is ever returned outside the caller's `project_id`/`allowed_project_ids`, so the rule is satisfied in effect; the physical per-project shard is deferred because it would also have to re-shape the cross-project full-reindex batching and the multi-project `$in` query fan-out. (2) **Offline-safe by construction** — the semantic path is opt-in (`search_type=semantic`/`hybrid`; router defaults to keyword) and falls back to keyword on any ChromaDB error; `_get_or_create_collection` passes **no** explicit `embedding_function`, so ChromaDB's bundled **local ONNX all-MiniLM** model is used (never a cloud API), which is why no `AI_OFFLINE_MODE` early-return is needed under the `AI_OFFLINE_MODE=True` default. Both deviations are now captured in the module + `_get_or_create_collection` docstrings; a future cloud `embedding_function` MUST be gated on `AI_OFFLINE_MODE` + a local-embedder check.

### Added (2026-06-09 — Granular test-case steps: Allure + pytest capture (Phase 1))

Test results showed only the top-line outcome per test — to see *where* a test failed (which step, which assertion, which screenshot) users had to leave TestLookup and open the raw Allure/pytest report. Phase 1 of the granular initiative captures a step/attachment **snapshot** at ingest and surfaces it on the test-case page.

- **Latest-run-only snapshot model (migration 0093).** Two new tables — `test_steps` (ordered, self-referencing nested step tree: name/keyword/status/duration/assertion message+trace/expected+actual/parameters) and `test_attachments` (index-only metadata — `source_ref`+`media_type`, no byte-proxying in Phase 1). Both anchor to the project-scoped `canonical_test_cases` identity (one snapshot per `(project_id, test_fingerprint)`, `ON DELETE CASCADE`), **not** the per-run `test_cases` rows, so there is exactly one snapshot per logical test. `source_test_run_id` (`SET NULL`) records which run produced it. On each new run for the same canonical test, ingestion does a **delete-then-insert overwrite** so the snapshot always reflects the latest run — no per-step time-series, smallest footprint. Also added four nullable per-run metadata columns to `test_cases` (`retry_count`, `is_flaky_run`, `stack_trace`, `step_count`) populated by the parsers; no backfill of history (going-forward only).
- **Allure + pytest step extraction.** The Allure parser recursively flattens the arbitrarily-nested `steps` tree (before/after fixtures + sub-steps) into the common step dict, mapping each node's status into the strict `PASSED/FAILED/SKIPPED/BROKEN/UNKNOWN` vocab (a value outside it would silently 422 the read endpoint) and indexing per-step + test-level attachments. Untrusted-input hardening: depth (`_MAX_STEP_DEPTH=20`) and node-budget (`_MAX_STEP_NODES=2000`) caps so a pathological customer `-result.json` can't `RecursionError` the worker or materialise unbounded rows mid-transaction. The pytest parser synthesizes one pseudo-step per execution phase (setup/call/teardown) from `--json-report`, each carrying its own outcome/duration/trace.
- **Staged inside the ingestion transaction (no new service commit).** `_persist_step_snapshot` / `_insert_step` do the delete-then-insert via `db.add`/`db.flush` and return; the ingestion router still owns the single `await db.commit()`, so the transaction-boundary ratchet is unaffected. Idempotent on re-ingest of the same run (the delete clears any prior rows first).
- **Read endpoint `GET /api/v1/runs/{run_id}/tests/{test_id}/steps`** — lazy/separate from the test-case detail payload (detail endpoint unchanged; clients fetch on demand). Guarded by `require_run_access()` which verifies the **provided** `run_id` (IDOR ratchet). `runs_service.get_test_steps_tree` resolves the canonical snapshot and returns the ordered, nested step tree with per-step + test-level attachments.
- **TestCase Steps UI panel.** New `components/runs/TestStepsPanel.tsx` (SWR via `useTestSteps`) renders the nested step timeline on `TestCasePage` — status badges, durations, expand/collapse, failed-step assertion traces, and attachment refs.
- Tests: `backend/tests/test_granular_steps_ingestion.py` (13 tests — Allure recursive flattening, pytest phase pseudo-steps, depth/node caps, latest-run delete-then-insert + same-run idempotency, the endpoint's `require_run_access` IDOR guard, migration 0093 columns/downgrade, and ORM snapshot relationships).

### Added (2026-06-09 — Test-case history, flakiness & metadata panel (Phase 2))

The test-case page showed only the current run's outcome plus the Phase 1 step snapshot — to judge whether a failure was a one-off, a flake, or a hard regression, and to find the owner/suite/age of the test, users had to cross-reference `/failures`, `/flaky-coach`, and the catalog by hand. Phase 2 folds that signal onto the test-case detail itself. **Read-only — no migration, no new ingestion writes, no new service commit;** it reuses the existing machinery rather than recomputing anything.

- **Read endpoint `GET /api/v1/runs/{run_id}/tests/{test_id}/history`** — lazy/separate from the test-case detail payload (detail endpoint unchanged; clients fetch on demand). Guarded by `require_run_access()` which verifies the **provided** `run_id` (IDOR ratchet). It resolves the test's `test_fingerprint` + `project_id` from that run, then returns a project-scoped `{history, flakiness, metadata}`. Because `test_fingerprint` is **not** project-salted, every history/flakiness query JOINs `test_runs` and filters on `project_id` — a same-fingerprint test in another tenant can never leak into the timeline.
- **Reuses existing machinery, invents no new formula.** Timeline = the windowed `ROW_NUMBER() PARTITION BY test_fingerprint ORDER BY created_at DESC` pattern over `test_case_history` already used by `test_health_coach_service.refresh_flaky_coach`, capped to a 50-run display depth inside the same 30-day window the flakiness value honours. Flakiness `failure_rate`/`failure_rate_pct` (FAILED+BROKEN over total in-window) match `analytics_service.flaky_tests`, and `is_flaky` gates on that detector's identical `≥3 runs` + `0.05–0.95` ratio band so the panel agrees with `/failures`; `classification` + `impact_score` reuse `test_health_coach_service` thresholds. Metadata surfaces owner (`TestCase.owner`/`assigned_to_user_id`), the **effective** suite via `_effective_suite_sql()` (both `tc.suite_name` and `tr.primary_suite_name`), per-test `severity`/`feature` (the existing per-test criticality fields), and first/last-seen run labels + catalog timestamps from `canonical_test_cases`.
- **No transaction-boundary or migration impact.** The new `services/test_case_history_service.py` only reads (`db.execute(select(...))`); the router owns the no-op transaction. No allowlist/cap bump, no schema change.
- **TestCase History & Flakiness UI panel.** New `components/runs/TestHistoryPanel.tsx` (SWR via `useTestCaseHistory`, single Axios base via `runsService.getTestHistory`) renders the pass/fail dot timeline, the flakiness badges (classification / fail-% / impact when flaky) with pass/fail/run counts, and the metadata block (suite, owner, severity, feature, first/last seen, created/updated) beside the Phase 1 Steps panel on `TestCasePage`.
- Tests: `backend/tests/test_granular_history.py` (8 tests — cross-project no-leak scoping, flakiness matching the analytics formula, 30-day-window exclusion of stale rows, the `0.05–0.95` flaky band, empty-history HEALTHY block, metadata first/last-seen labels, wrong-run resolver 404, and the endpoint's `require_run_access` IDOR guard).

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

### Changed (2026-06-08 — run identifiers show when the run was generated)

"Run #N" repeats per (project, suite) and across projects, so the same label can point at many different executions — on `/live` ("Run #1") and elsewhere it was impossible to tell same-numbered rows apart. Added a shared, null-safe `formatRunWhen(iso)` helper (`utils/formatters.ts`, compact `MMM dd, HH:mm`; returns `''` for missing/invalid timestamps so legacy rows never render "Invalid Date"). Wired into the `/live` sessions table, the `/agents` **live-run card**, and `/my-failures` — each shows the run's start time **inline** beside Run #N (consistent compact format). On `/deep-investigation` the run timestamp was upgraded from date-only to date+time (same compact helper) so same-day runs are distinguishable. The other run-label sites already render a run datetime in their own established layout and were left as-is to avoid regressing them: `/runs` and `/intelligence` use a dedicated full `toLocaleString` **date column** (kept verbose by design — a different UI element from the inline run-label treatment), run-compare embeds `created` in its option, and `/intelligence`'s activity feed uses a relative `fromNow`. `run_seq`/`build_number`/UUID routes are untouched everywhere (display-only).

A 4-lens adversarial review (correctness / security / UX / coverage) found **no functional defects** (the helper's null/invalid guards, field mappings, and frontend-only scope all verified). Its UX findings were applied: `/my-failures` moved from a hover-only tooltip to an inline datetime (matching `/live`); the `/agents` live-card timestamp got `whitespace-nowrap`/`shrink-0` to avoid awkward flex-wrap; double `formatRunWhen` calls were collapsed to a single computed value. Tests: `utils/formatters.test.ts` (format, ISO, null/empty/invalid → '') + a `MyFailuresPage` render test asserting the inline run datetime.

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

### Fixed (2026-06-06 — CI test-ordering pollution from the PR #164 regression tests)

- **CORRECTION to the entry below:** the 13 failures previously written off as "stale clobber" were **not stale** — they fail only in the **full-suite run** (`pytest tests/`), not in isolation, which is why the earlier isolated reproduction looked green. Root cause: two of the PR #164 regression tests leak global state into the rest of the suite. Both product code paths are correct; the fixes are test-isolation only. Reproduced + verified on a Python 3.11 venv (full suite minus integration): **3000 passed, the 13 dump failures gone.**
- **`tests/regression/test_chromadb_telemetry_disabled.py` left `app.core.config` reloaded.** It calls `importlib.reload(config)` to re-exercise the import-time telemetry guard, which rebinds the module's `settings`/`get_settings` to brand-new objects. Every module that already did `from app.core.config import settings` keeps the ORIGINAL instance, so downstream tests that `monkeypatch.setattr(config.settings, <flag>, ...)` patch a dead object while the app reads defaults — silently breaking ~12 "feature-disabled / offline" assertions (ingestion buffer-cap/routing/rate-limit/backpressure, ai_pipeline_debouncer, github_checks ×3, webhook, integration-probe). Fix: an autouse fixture snapshots and restores `config.settings`/`config.get_settings` so the reload can't escape the test.
- **`tests/test_db_postgres_lazy_engine.py` tripped over a `monkeypatch.setattr` leak.** `engine`/`AsyncSessionLocal` are served by PEP 562 `__getattr__` (not real attributes). Pytest's `monkeypatch.setattr` (used across ~20 files to inject a fake session factory) restores by *setattr*, not delattr, leaving a **real** stale attribute behind that shadows the lazy hook; combined with the BUG-003 `dispose_engine_for_loop` engine rebuild, `pg.AsyncSessionLocal` then no longer matched the live `get_session_factory()`. Fix: the two lazy-hook tests now drop any leaked `engine`/`AsyncSessionLocal` from `pg.__dict__` before asserting, so they exercise the hook itself.
- **`tests/services/test_audit_log_service.py` depended on a fragile fd-capture of structlog output.** Two tests asserted a WARNING via `capfd` (stdout file-descriptor). `configure_logging()` (triggered by app startup elsewhere in the suite) routes structlog through the stdlib `LoggerFactory` and binds a `StreamHandler` to whichever `sys.stdout` was current when it first ran (with `cache_logger_on_first_use=True`), so depending on ordering the line lands on a stale stream `capfd` never sees. Fix: spy the service logger directly (`patch("app.services.audit_log_service.logger")`) and assert the warning event name — independent of structlog config, streams, and ordering. (These two passed in CI but failed in the local full-suite run with integration excluded; now order-independent.)

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
