# backend/tests — layout guide

There are 71 top-level test files plus 4 subdirectories. The naming is dense; this file maps every prefix to what it covers and where new tests should land.

## Running tests

```bash
# Inside the container (preferred — matches CI):
make test-backend                                     # all
docker compose exec backend pytest tests/test_agent.py -v
docker compose exec backend pytest tests/test_agent.py::TestClass::test_method -v

# Without Docker (from backend/):
pip install -r requirements-dev.txt
pytest tests/ -v --tb=short
pytest tests/services/ -v                             # one subdir
pytest -k "ingestion or analysis_router"              # by keyword
```

See `tests/conftest.py` for shared fixtures (`FakeExecuteResult`, `FakeRedis`, `ns`).

## Subdirectories

| Path | Style | What goes here |
|------|-------|----------------|
| `tests/` (root) | unit-ish — services called directly, deps stubbed via `monkeypatch` / `unittest.mock` | Single-service / single-feature units, epic & phase suites, architectural ratchets. |
| `tests/services/` | unit, focused on one service module | Per-service tests (e.g. `test_webhook_service.py`) and the `test_batch[1-5]_*.py` grab-bags below. |
| `tests/integration/` | full HTTP stack — httpx + ASGITransport against the FastAPI app, `dependency_overrides` fake the DB | End-to-end router contract tests: status codes, auth wiring, request/response shapes. See `integration/conftest.py` for the fixture rationale. |
| `tests/routers/` | router-internal helpers | Router-private logic that doesn't need the full app (e.g. `test_detect_format.py`, `test_ingest_security.py`). |
| `tests/core/` | core/deps unit tests | Auth/permission helpers (`test_resolve_project_scope.py`, `test_token_revocation.py`). |

## Top-level prefix taxonomy

Roughly chronological: each milestone of work landed its tests under a prefix. Knowing the prefix tells you the *intent* of the test, which helps when picking where to add a new one.

| Prefix | Origin | Scope |
|--------|--------|-------|
| `test_<service>.py` / `test_<feature>.py` | "regular" tests | Direct, focused tests for one service or feature. Default landing zone for new tests unless they fit a milestone suite. |
| `test_architectural_*.py` | architectural ratchets | Static-analysis-style assertions that codify project rules (transaction boundaries, authorization guards). New rules of this shape go here. |
| `test_phase[1-6]_*.py` | "Phase 1–6" hardening waves | P1: foundations & shared abstractions. P2: AI accuracy & agent pipeline. P3: performance. P4: safety & HITL. P5: eval gate / hardening. P6: observability & cost control. |
| `test_epic[1-11]_*.py` | "Epic 1–11" QAI program | Cross-cutting feature epics referenced by `QAI-1xx` IDs in the docstrings. Epic 1 = secure controls, 2 = run intelligence front door, ... 11 = quality, observability, rollout. |
| `test_bl0[1-3]_*.py` | "Baseline" hardening | BL-01 tenant/project isolation, BL-02 secret encryption & config hardening, BL-03 snapshot invalidation/freshness. |
| `test_ops0[1-4]_*.py` | "Ops" workstream | OPS-01 integration health probes, OPS-02 AI eval dashboards, OPS-03 load/perf for large runs, OPS-04 tenant-aware audit/observability. |
| `test_roi0[1-4]_*.py` | "ROI" workstream | ROI-01 real defect candidates, ROI-02 value metrics, ROI-03 summary context, ROI-04 background semantic indexing. |
| `test_ent0[3-5]_*.py` | "Enterprise" features | ENT-03 PDF/evidence-bundle/share-link reports, ENT-04 service ownership routing, ENT-05 saved views & scheduled digests. |
| `test_p2_features.py` | second product wave | Semantic similarity cache, OTEL tracing, MinIO artifact persistence, Mongo pipeline event log. |
| `test_new_features.py` | "AI enhancements" milestone | Specific units shipped in that milestone (tools-used extraction, ReleaseRiskAgent scoring, fast-path workflow, etc.). |

### `services/test_batch[1-5]_*.py`

Grouped service-level tests, kept together to amortize stubbing setup across related modules:

| Batch | Focus |
|-------|-------|
| `batch1_pure_services` | Pure-Python parsers and helpers (allure parser, llm_factory). |
| `batch2_core_services` | Agent output parsing and other core-service paths. |
| `batch3_clients_notifications` | External clients (Jira, OCP) and notification surfaces. |
| `batch4_training_ai` | Classifier training/parsing and AI-side helpers. |
| `batch5_all_projects` | All-projects (no `project_id` filter) mode across services. |

## Where should a new test go?

- **One service, one feature, no milestone tie-in** → root `tests/test_<service>.py` (or `tests/services/test_<service>.py` if it's a dedicated service test alongside the existing ones there).
- **Router contract / status codes / auth wiring through the real app** → `tests/integration/test_<router>_api.py`.
- **Project-wide invariant** ("no service may commit outside the allowlist") → extend `test_architectural_*.py`.
- **Continuation of an active milestone** → matching `test_<prefix>_*.py` file (check `docs/PROGRESS.md` for the current phase).
- **Multi-service grab-bag with shared stubbing** → `tests/services/test_batch*.py`, but prefer a dedicated file unless setup cost truly justifies bundling.

See `backend/CLAUDE.md` "Test Patterns" for the fixture/mocking style and Pydantic v2 / async conventions every test must follow.
