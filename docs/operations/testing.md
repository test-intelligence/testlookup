# Testing and release verification

[Documentation home](../README.md)

The repository combines behavior tests, architecture/contract guards, mutation harnesses, browser tests, deployment checks and live evidence. A passing unit suite cannot establish deployed queue connectivity or model accuracy. The checks actually run for this documentation change are in [verification](../handoff/verification.md).

## Test map

| Layer | Location and purpose | Typical command |
|---|---|---|
| Backend unit/regression | `backend/tests`, mocked services and targeted local databases | From backend: `python -m pytest tests -m 'not integration and not live'` |
| Backend integration/live | `backend/tests/integration`, live-tagged tests | Run selected tests only with their explicitly configured disposable services |
| Frontend unit | Colocated Vitest tests under `frontend/src` | `npm test` from frontend |
| Frontend type/build | TypeScript and Vite | `npm run type-check`, `npm run build` |
| Browser E2E | `frontend/tests`, Playwright configs | `npm run test:e2e:ci` or explicitly configured live target |
| CLI | `cli/tests` | `python -m pytest cli/tests` with CLI installed |
| Client SDK | `client/tests` and language-specific packages | Python pytest plus each language's build/test instructions |
| MCP | `mcp/tests` | `python -m pytest mcp/tests` with MCP dependencies |
| Architecture ratchets | `scripts/quality_gate.py`, baseline files and self-tests | `python scripts/quality_gate.py` |
| Image/deployment | `scripts/release`, Compose/Kustomize contracts | `python scripts/release/check_image_drift.py` |
| Documentation | Generated references, links, schemas and Mermaid | [maintenance commands](../handoff/maintenance.md) |
| AI quality | Agent/decision eval suites, manifests, golden fixtures and benchmark scripts | Select the exact offline/mock/live evaluator; preserve provenance |

Backend pytest sets `TESTING=true`, disables OTel and supplies a non-routable placeholder DB URL when unset. This allows imports; it does not create a real database or prove every test is service-free. Integration/live markers are assigned by config/hooks. Inspect fixtures before running large suites against any external infrastructure. Separate SDK/MCP/CLI test runs can avoid Python import-path collisions.

## High-value regression scenarios

Ingestion should test duplicate/concurrent delivery, malformed and empty reports, expanded archive limits, cross-project denial, source identity collision, partially rejected rows and parameter-boundary batch sizes. Live flows should test missing completion, multiple API processes, auth revocation, ordered fan-out, materialization recovery and producer retry.

Agent tests should cover frozen plan/authority changes, lease loss, checkpoint eligibility, budget/deadline handling, child queue starvation, cancellation, bounded retries and deterministic terminal report behavior. Report tests should cover rejected/superseded authority, pending draft exceptions, export/search/notification/MCP consumers and review changes before delayed delivery.

Release/test management tests should cover evidence floors, aggregation denominators, attribution windows, current-decision uniqueness, stale overrides, lifecycle concurrency and tenant boundaries. Test happy-path responses alongside negative credential/scope/stale-version cases; source-text guards alone are not a substitute for behavior.

## CI and deployment evidence

[CI workflow](../../.github/workflows/ci.yml) runs multiple jobs including quality ratchets, image pin/drift checks, package tests/builds and docs validation. Release and deployment workflows have separate credentials, image/provenance and environment requirements. Some expensive queue/large-ingestion proofs are manually selectable workflow inputs; their existence does not imply they run on every commit.

For a release, identify the exact source SHA, images/digests, migration head, selected deployment topology, worker queue proof and full representative ingest → persist → analysis → review → report journey. Archive evidence with timestamps and environment, and state mock/offline vs live provider clearly. Dated material under `architecture/verification` and `qa` is historical evidence, not proof for a new SHA.
