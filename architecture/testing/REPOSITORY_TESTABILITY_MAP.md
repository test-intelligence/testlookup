# Repository Testability Map

Evidence date: 2026-09-15. Source of truth: `origin/main` at `7c372679`, plus
the coverage-expansion change that introduced this report.

## Test surfaces

| Surface | Production scope | Automated evidence | Ordinary CI | Main limitation |
|---|---|---|---|---|
| Backend API and services | `backend/app` | 998 test modules, about 10,786 test/class declarations; unit, integration, security, property, migration, contract, and mutation suites | Yes; PostgreSQL and Redis services, Ruff, mypy ratchet, 74% line floor | Some marked release proofs are path-gated because they are intentionally expensive |
| Frontend components and services | `frontend/src` | 230 Vitest files and 1,739 tests before this change | Yes; now with 60/55/51/61 statement/branch/function/line floors | Service layer is 24.97% statements; several orchestration-heavy pages remain below 20% |
| Hermetic browser journeys | Real SPA, mocked HTTP boundary | `frontend/tests/ci-e2e` | Yes; Chromium, Vite, no credentials/backend | Small smoke lane; does not prove database, worker, or third-party integration behavior |
| Live browser journeys | Full deployed UI/API/worker stack | `frontend/tests/e2e` and `frontend/tests/probe-*.spec.ts` | No; run manually or against a deployment | Requires a configured live stack and credentials; results can drift with data |
| Documentation rendering | Docs page and Mermaid diagrams | `frontend/tests/docs` | Yes; real Chromium | Deliberately limited to documentation rendering |
| MCP | `mcp` | Python tests plus syntax and manifest checks | Yes | Most transport tests use fakes; deployed authentication still needs live verification |
| CLI/client | `cli`, generated clients | Pytest contract and command tests | Yes | Shell/profile behavior varies by host and needs release smoke checks |
| Database | SQLAlchemy models and Alembic | Migration-chain, PostgreSQL integration, downgrade and schema guards | Yes | SQLite unit tests cannot substitute for PostgreSQL concurrency and index behavior |
| Architecture policy | Entire tracked tree | 43 quality-gate guards plus fixture self-tests | Yes | Static guards prove structure and ownership, not runtime correctness |
| AI and agents | Registry, router, workflows, reviews, evals | Golden corpora, mutation suites, replay evaluation, live DoD probe | Partly; deterministic suites in CI | Provider inference and operational drift require scheduled/live evidence |

## Verification layers

1. **Static:** Ruff, ESLint, TypeScript, mypy ratchet, OpenAPI/docs generation,
   quality guards, migration-head and source-ownership checks.
2. **Isolated behavior:** pytest and Vitest unit/component tests, property tests,
   contract tests, and asserted mutation harnesses.
3. **Service integration:** PostgreSQL/Redis-backed pytest, API authorization,
   outbox, worker fencing, migration, and concurrency tests.
4. **Hermetic browser:** real built application and browser with controlled HTTP
   responses. It proves routing, persistence, form submission, and rendering.
5. **Live system:** deployed browser probes and the agentic live DoD. It proves
   the API, database, broker, worker, provider, and browser are wired together.
6. **Acceptance and exploration:** the UAT and charter suites in this directory.

## High-value seams

| Seam | Controllable input | Observable output | Preferred test |
|---|---|---|---|
| HTTP client | Axios adapter or Playwright route | request URL/body/headers, rendered state | Vitest contract + hermetic browser |
| Authentication | token/user store and auth responses | redirect, persisted session, role guard | Unit + hermetic browser + live login |
| Project scope | active-project store and JWT claims | query scope, 403/404, cross-tenant absence | Component + PostgreSQL security integration |
| Pipeline | frozen execution context, task/broker fakes | durable stages, public status, audit events | Service integration + mutation |
| Review gate | review request and distinct reviewer | refusal/accept/reject, notification/export gating | PostgreSQL integration + UAT |
| Model/provider | capability registry and provider fake | selected tier, escalation, usage and fallback | Deterministic unit + offline integration + eval |
| Workflow | versioned definition and replay corpus | validation, compiled topology, publication refusal | Compiler/property + database + UI |
| External integrations | adapter fake or sandbox account | durable outbox/result/audit record | Contract + sandbox live smoke |

## Canonical commands

```text
backend:  ruff check app tests; pytest -p no:testlookup ...; mypy ratchet
frontend: npm run lint; npm run type-check; npm run test:coverage
browser:  npm run test:e2e:ci; npm run test:e2e (live stack)
policy:   python scripts/quality_gate.py; pytest scripts/test_quality_gate.py
```

The older `Product_Plan/ImprovementPlan_Phase2_Tests.md` contains historical
counts and failures. It is useful as provenance, but it is not current evidence.
