# CLAUDE.md — Backend

This file provides guidance to Claude Code when working in the `backend/` directory.

## Quick Reference

```bash
# Run all tests (inside container)
make test-backend                    # or: docker compose exec backend pytest tests/ -v

# Run single test
docker compose exec backend pytest tests/test_agent.py::TestAgentOutputSchema::test_parse_valid_json_output -v

# Run without Docker (from backend/)
cd backend && source venv/bin/activate   # Windows: venv\Scripts\activate
pytest tests/ -v --tb=short
pytest tests/test_agent.py -v            # single file
pytest tests/test_agent.py::TestClass::test_method -v  # single test

# Regression suite only — pins for every 2026-05-18/19 user-reported
# bug fix + enhancement. Master index: ``docs/REGRESSION_TEST_SUITE.md``.
pytest -m regression -v
pytest -m "not regression" -v        # everything except the regression pins

# Lint & format
ruff check app/ tests/
ruff format app/ tests/
mypy app/
```

## Adding a New API Endpoint

Follow this exact sequence:

### 1. Pydantic schemas in `app/models/schemas.py`

```python
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
import uuid
from datetime import datetime

class FeatureCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None

class FeatureResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class FeatureUpdate(BaseModel):
    """None = keep existing value."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
```

### 2. ORM model in `app/models/postgres.py`

```python
class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (
        Index("ix_features_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
```

### 3. Service in `app/services/feature_service.py`

```python
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

async def list_features(db: AsyncSession, project_id: str) -> list:
    result = await db.execute(
        select(Feature).where(Feature.project_id == project_id).order_by(Feature.name)
    )
    return result.scalars().all()
```

### 4. Router in `app/routers/feature.py`

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_current_active_user, require_role
from app.models.postgres import User, UserRole

router = APIRouter(prefix="/api/v1/features", tags=["Features"])

@router.get("", response_model=list[FeatureResponse])
async def list_features(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    ...

@router.post("", response_model=FeatureResponse, status_code=201,
             dependencies=[Depends(require_role(UserRole.QA_ENGINEER))])
async def create_feature(
    payload: FeatureCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    ...
```

### 5. Register in `app/bootstrap.py`

Add to `PROTECTED_ROUTERS` tuple (or `PUBLIC_ROUTERS` if no auth needed).

### 6. Migration

```bash
make migrate-create MSG="add_features_table"
# Then verify generated migration in migrations/versions/
```

### 7. Tests in `tests/test_feature.py`

## Code Patterns (Exact)

### Database Session

**In routers** (auto commit/rollback via dependency):
```python
async def endpoint(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Model).where(Model.field == value))
    obj = result.scalar_one_or_none()
    # session commits automatically on success, rolls back on exception
```

**In services/agents** (manual session):
```python
from app.db.postgres import AsyncSessionLocal

async with AsyncSessionLocal() as db:
    try:
        result = await db.execute(select(Model))
        await db.commit()
    except Exception:
        await db.rollback()
        raise
```

**Commit responsibility (single-owner rule):**

When a service or helper receives a session via `db: AsyncSession` parameter, the **caller** owns the transaction lifecycle. The service may:
- `db.add(...)`, `db.delete(...)`, mutate ORM objects
- `await db.flush()` to materialise server-generated values (PK, defaults) without ending the transaction

The service must NOT `await db.commit()` or `await db.rollback()` on an injected session. The caller does that — request handlers via the `get_db` dependency (commit-on-success, rollback-on-exception), or background tasks via their own `async with AsyncSessionLocal() as db:` block.

Likewise, `await db.refresh(obj)` is **almost never needed** because the session factory sets `expire_on_commit=False` (see `db/postgres.py`). Objects stay fully usable after commit — every column you read before the commit is still readable after, and lazy relationship traversal is the only thing that requires a fresh round trip (and you should be using `selectinload` for those anyway). Refresh just spends a SELECT on data you already hold. The only legitimate use is "I need to re-read a value that may have been changed by another transaction since I last read it" — which is almost never the case inside a single request handler.

The pattern is:

```python
# Service — receives injected session, never commits
async def create_thing(db: AsyncSession, payload: dict) -> Thing:
    thing = Thing(**payload)
    db.add(thing)
    await db.flush()            # populate thing.id + server defaults
    return thing                # caller commits

# Router — relies on get_db
async def endpoint(db: AsyncSession = Depends(get_db)):
    thing = await create_thing(db, payload)
    return ThingResponse.model_validate(thing)  # get_db commits on return
```

**Lazy engine bootstrap:**

The SQLAlchemy engine and session factory are constructed lazily on first use, not at module import time. Importing `app.db.postgres` is side-effect-free; the engine builds when something actually calls `get_engine()`, `get_session_factory()`, or accesses the module-level `engine` / `AsyncSessionLocal` names (PEP 562 `__getattr__`). Tests that stub the DB client don't need `DATABASE_URL` set to import the module — only to actually touch it.

To override the engine in a test (e.g. point at a fixture DB), monkeypatch `app.db.postgres.get_engine` and call `get_engine.cache_clear()` if it was already built.

### Audit-log writes — attempt vs outcome

`services/audit_log_service.py` exposes two helpers; pick one per call site:

- **`record_outcome(db, ...)`** — writes to the caller's injected session. The audit row participates in the caller's transaction: it commits when the caller commits, and rolls back when the caller rolls back. Use when the row only makes sense in the context of a SUCCESSFUL primary mutation. *Example:* "feature flag toggled to ON" should not be recorded if the toggle itself was rolled back.

- **`record_attempt(...)`** — opens a fresh `AsyncSessionLocal()`. The audit row commits independently and SURVIVES a caller rollback. Retries transient DB faults via `async_retry` against `DB_RETRYABLE_EXCEPTIONS`. Use when the *attempt* is audit-worthy on its own. *Example:* "admin issued a project reset (mode=full)" should be recorded even if the reset later fails midway. *Example:* webhook subscription audit — the subscription mutation already committed, the audit is durable evidence the operator did it.

Both helpers swallow transient failures, emit a structured WARNING on terminal failure, and never raise to the caller. Legacy services that hand-roll the same pattern (e.g. `flaky_quarantine_service._audit`, `webhook_service._audit`) should migrate to these helpers when their owning module is touched for feature work; greenfield audit writes use the helpers from day one.

### Foreign-key `ondelete` rubric

When adding a new FK column, pick the `ondelete` behaviour using this rule:

- **`CASCADE`** when the child row is meaningless without the parent. Examples: `TestCase.test_run_id` (a test result without its run is orphaned data), `Defect.project_id` (a defect outside any project can't be triaged), `QualityGate.project_id`, `WebhookSubscription.project_id`, `TestCaseHistory.test_case_id`.

- **`SET NULL`** when the child has standalone value and should survive the parent's deletion. Examples: `ChatSession.project_id` (conversation history is useful retrospectively even if the project is gone), `Defect.test_case_id` (the defect still represents a real bug even if the originating per-run test row is purged — the `failure_category` + Jira link are still actionable), audit-log `actor_id` (audit trails outlive user deactivation).

- **No `ondelete`** (Postgres default `NO ACTION`): use only when the deletion order is contractual — i.e. there's a service guarantee that children are explicitly cleaned up before the parent. This is rare; if you find yourself reaching for it, you probably want `CASCADE` instead.

The rule applies to new FK columns and to FKs being changed. Don't bulk-rewrite existing columns just to conform — `ondelete` behaviour is load-bearing once data is in production.

### Authentication & Authorization

```python
from app.core.deps import (
    get_db,                    # AsyncSession dependency
    get_current_active_user,   # JWT → User (401 if invalid)
    require_role,              # Minimum role check (403 if insufficient)
    require_project_access,    # Tenant isolation (project membership check)
    require_run_access,        # Run → Project → membership chain
    get_accessible_project_ids,# Returns set[UUID] or None (None = admin, sees all)
)
```

Role hierarchy: `VIEWER < TESTER < QA_ENGINEER < QA_LEAD < ADMIN`

### Tenant Isolation

```python
accessible = await get_accessible_project_ids(db, current_user)
stmt = select(Model).where(Model.is_active.is_(True))
if accessible is not None:  # None means ADMIN → no filter
    stmt = stmt.where(Model.project_id.in_(accessible))
```

### Partial Updates

```python
updates = payload.model_dump(exclude_none=True)
for field, value in updates.items():
    setattr(obj, field, value)
await db.commit()
```

### Logging

Always use structlog, never `print()` or `logging.getLogger()`:
```python
import structlog
logger = structlog.get_logger(__name__)
logger.info("event_name", key1=value1, key2=value2)
```

### Celery Tasks

Tasks in `worker/tasks.py` accept only simple serializable args (IDs, dicts):
```python
from app.worker.celery_app import celery_app

@celery_app.task(queue="ai_analysis")
def run_analysis_task(run_id: str, test_case_ids: list[str]):
    ...
```

Queue priority: `critical` > `ingestion` > `ai_analysis` > `default`

### Background Task Dispatch from Router

```python
from app.worker.tasks import run_analysis_task
task = run_analysis_task.delay(str(run.id), [str(tc.id) for tc in failed])
return {"status": "queued", "task_id": task.id}
```

## Adding a New LangGraph Agent

See `AGENTS.md` (repo root) for the full prompt/workflow specs of existing agents — useful when modeling a new one.

1. Subclass `BaseAgent` in `app/agents/new_agent.py`:
```python
from app.agents.base import BaseAgent

class NewAgent(BaseAgent):
    stage_name = "new_stage"

    async def run(self, state: dict) -> dict:
        pipeline_run_id = state.get("pipeline_run_id", "")
        await self.mark_stage_running(pipeline_run_id)
        try:
            # ... agent logic ...
            result_data = {"key": "value"}
            await self.mark_stage_done(pipeline_run_id, result_data=result_data)
            return {"completed_stages": [self.stage_name], "new_key": result_data}
        except Exception as exc:
            await self.mark_stage_done(pipeline_run_id, error=str(exc))
            return {"errors": [f"{self.stage_name}: {exc}"]}
```

2. Add stage to `WorkflowState` in `agents/state.py`
3. Create node function and wire into `_build_deep_graph()` in `agents/workflow.py`:
```python
_new_agent = NewAgent()

async def new_node(state: WorkflowState) -> dict:
    return await _new_agent.run(cast(dict[str, Any], state))
```

## Adding a New LangChain Tool

Add tool file under `app/tools/new_tool.py`, then register in `app/services/agent.py`.

## Adding a New Knowledge Source Connector

1. Create connector in `app/services/connectors/new_connector.py`:
```python
from app.services.connectors.base import BaseConnector

class NewConnector(BaseConnector):
    async def fetch_content(self, source) -> str:
        # Fetch and return raw text content
        ...

    async def test_connectivity(self, config: dict) -> dict:
        # Return {success: bool, latency_ms: int, error: str|None}
        ...
```

2. Register in `app/services/connectors/registry.py`:
```python
CONNECTOR_REGISTRY["new_type"] = NewConnector
```

3. Add source type to `KnowledgeSourceType` enum in `models/schemas.py`.

## Analysis Engine Architecture

The system supports three analysis modes: LLM, ML, and Rules. All classification
goes through `services/analysis_router.py` — never call engines directly.

### Dispatch flow
```
analysis_agent._analyse_one()
  → analysis_router.get_analysis_mode()   # reads ANALYSIS_MODE (env/DB/Redis cache)
  → if "rules":  rules_engine.RulesEngine.classify_test()
  → if "ml":     ml/classifier.MLClassifier.classify()  (falls back to rules if no model)
  → if "llm":    services/agent.run_triage_agent()       (falls back to rules on timeout)
```

### Key files
- `services/analysis_router.py` — central dispatcher, mode resolution, fallback chain
- `services/rules_engine.py` — 14 keyword patterns + historical flakiness + regression + suite-level + template summaries
- `services/ml/feature_extractor.py` — 31-feature numeric vector (FEATURE_NAMES list defines canonical order)
- `services/ml/classifier.py` — scikit-learn HistGradientBoosting wrapper, ~2ms/test, model cached per-process
- `services/ml/summary_generator.py` — enriches rules templates with ML metadata
- `services/ml/trainer.py` — gathers labeled data, trains model, saves .joblib, evaluates on holdout

### Adding a new analysis mode
1. Add mode constant to `AnalysisMode` in `analysis_router.py`
2. Create engine in `services/ml/` or `services/` with `classify(features) -> dict` method
3. Add dispatch branch in `analysis_router.classify_test()` — **must populate `routing` dict** with `mode_used` and, on fallback, `fallback_from` + `fallback_reason`
4. Add mode to config validation regex in `schemas.py:AIConfigUpdate.analysis_mode`
5. Add radio option in `frontend/src/pages/settings/AIConfigPage.tsx:ANALYSIS_MODES`

### Decision traceability contract

Every `classify_test()` return now carries a `_routing` dict:

```python
result["_routing"] = {
    "mode_requested": "auto",       # what caller asked for
    "mode_resolved": "llm",         # what get_analysis_mode() picked
    "mode_used": "rules",           # what actually ran (may differ on fallback)
    "fallback_from": "llm",         # set when mode_used != mode_resolved
    "fallback_reason": "llm_error: TimeoutError: ...",
    "auto_probe": {"ollama_model_available": True, "probe_age_seconds": 12.4},
}
```

The per-test `analysis_agent` block copies this into `_audit.analysis_mode` /
`_audit.fallback_from` / `_audit.fallback_reason` and pops
`_confidence_adjustments` into `_audit.confidence_adjustments`. The stage-level
`mark_stage_done(analysis_mode=..., fallback_reason=...)` call persists the
**dominant** mode and an aggregate fallback-count summary to
`agent_stage_results.analysis_mode` / `.fallback_reason`.

### Agent decision logging

All agents inherit `BaseAgent.log_decision(pipeline_run_id, decision_point, chosen, rationale, …)`:

```python
await self.log_decision(
    pipeline_run_id,
    decision_point="route_analysis_mode",
    chosen="ml",
    rationale="configured ANALYSIS_MODE=ml",
    test_case_id=tc_id,
    context={"severity": meta.get("severity")},
)
```

Decisions land in three places simultaneously so every consumer has what it needs:

1. **structlog** — searchable, correlated with other log fields (`event=agent_decision`).
2. **Pipeline event log** (Mongo, immutable) — `decision_made` event type; queryable via `get_pipeline_timeline()`.
3. **`AgentStageResult.decision_log`** (Postgres JSONB, migration 0061) — committed at `mark_stage_done`; the UI renders this alongside the stage result without querying Mongo.

Workflow router functions emit route decisions via `_emit_route_decision()` in `workflow.py` — fire-and-forget via `asyncio.create_task` since LangGraph routers are synchronous.

**When to call `log_decision`:** every non-trivial branch. Route selection, fallback trigger, stage skip, confidence clamp, retry, specialist-stage pick, threshold check. **Do not** log trivial branches (simple null checks, early returns on empty input) — the signal-to-noise ratio matters.

### OTEL span enrichment

`mark_stage_running` records `stage.input_keys` (comma-joined, sorted) so Jaeger shows what state each stage received. `mark_stage_done` adds `analysis.mode`, `route.rationale`, `fallback.used`, `fallback.reason`, `error.category`, `result.confidence_score`, `result.evidence_count`, `decisions.count`. Each call to `log_decision` also adds a `decision.<point>` span event inline with the stage timeline.

## Test Patterns

### Fixtures (conftest.py)

- `FakeExecuteResult` — Mock SQLAlchemy `.execute()` results with `.scalar_one_or_none()`, `.scalars().all()`, etc.
- `FakeRedis` — In-memory async Redis mock with TTL support (`get`, `set`, `setex`, `expire`, `delete`, `exists`, `xlen`, `scan`, `pipeline`)
- `ns` — `SimpleNamespace` factory for quick mock objects

### Mocking Pattern

```python
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=some_obj))
    db.commit = AsyncMock()
    return db
```

### Module Stubbing (for tests that don't need heavy deps)

Epic tests use autouse fixtures to stub `bcrypt`, `jose.jwt`, DB clients, etc. before import:
```python
@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch):
    # Stub heavy/optional dependencies
    ...
```

## Critical Rules

- **Pydantic v2 only.** Use `@field_validator(..., mode="before")` + `@classmethod`. Never `@validator` or `class Config`.
- **Async everywhere.** All DB/HTTP/service methods must be `async def`.
- **No SQL INTERVAL with params.** Compute `period_start` in Python: `datetime.now(timezone.utc) - timedelta(days=days)`.
- **AgentExecutor must include** `max_execution_time=settings.AI_TIMEOUT_SECONDS`.
- **API key `key_hint`** = `raw_key[:8] + "..."` (11 chars max, column is `String(12)`).
- **UserRole stored as `String(20)`**, not native PostgreSQL enum.
- **`ALL_PROJECTS_ID = "all"`** — never pass string `"all"` to backend as UUID. Backend receives `None` for all-projects mode.
- **Category normalization** — use `app.services.category_normalizer.normalize_category()`. Add new aliases to `CATEGORY_ALIASES` dict, never add fuzzy matching.
- **Analysis mode dispatch** — always use `services/analysis_router.classify_test()`, never call `run_triage_agent()` or `RulesEngine` directly. The router handles mode selection and fallback.
- **ML feature vectors must be deterministic** — same inputs produce same features. `FEATURE_NAMES` list in `ml/feature_extractor.py` defines the canonical order; never reorder.
- **ML inference budget** — <5ms per test. If exceeded, analysis_router falls back to rules engine automatically.
- **PII redaction at boundaries** — use `privacy_service.sanitize_for_persistence()` before DB writes, `sanitize_for_logging()` before log emission, `sanitize_for_llm()` before LLM calls, `sanitize_for_report()` before rendering. Placeholder is always `[REDACTED]`.
- **Tag normalization** — use `tag_utils.normalize_tag()` for all tag ops. System tags (13 reserved) cannot be custom tags — validate with `validate_custom_tags()`.
- **Knowledge connectors are pluggable** — new connectors implement `BaseConnector` in `services/connectors/base.py` and register in `services/connectors/registry.py`.
- **RAG feature gating** — check `KNOWLEDGE_RAG_ENABLED` via Redis → DB → env var chain. Never bypass.
- **Dual authentication** — `get_current_user_or_api_key()` in `core/deps.py` tries JWT first, then API key (SHA-256 hash). Use for endpoints accepting both methods.
- **Email notifications are async** — dispatch via Celery tasks in `worker/tasks.py`, never send synchronously in request handlers.
- **AI config resolution** — use `ai_config_resolver.py` for LLM config. Precedence: DB → secrets → env. Cached in Redis (60s TTL).
- **Feature flag gating** — every new capability must call `services/feature_flags.is_enabled(key, project_id=..., user=...)` before running. Resolution chain: in-proc cache (30s) → Redis (30s) → Postgres `feature_flags` → env fallback. CRUD via the flag router writes to `settings_audit_log`. The legacy single-column `FeatureFlag(flag_key, scope, enabled, config)` model was removed in Tier 0A — use the `key`/`enabled_global`/`enabled_projects`/`enabled_roles`/`rollout_percent` schema.
- **Offline-mode kill switch** — `AI_OFFLINE_MODE=true` blocks every outbound integration: GitHub Checks posting, webhook delivery, hosted LLM calls, hosted Ragas eval. Services must check `settings.AI_OFFLINE_MODE` before any network call; the feature flag alone is not sufficient.
- **JSONB on new tables** — import `JSONB` from `sqlalchemy.dialects.postgresql` for columns backed by JSONB in Postgres. Columns that must use `JSONB`, not `JSON`: `feature_flags.enabled_projects`, `feature_flags.enabled_roles`, `ai_analysis.routing_metadata`, `flaky_quarantine_requests.rationale`, `compliance_packs.metadata_snapshot`, `webhook_subscriptions.events`, `webhook_deliveries.event_payload`.
- **Decision logging budget** — `BaseAgent.log_decision()` fans out to structlog + Mongo pipeline event log + Postgres `AgentStageResult.decision_log`. Call it on every non-trivial branch (route selection, fallback, stage skip, confidence clamp, retry, specialist pick, threshold check). Do not call it on trivial null checks or empty-input early returns.
- **Outbound webhooks** — never emit events synchronously in request handlers. Use `services/webhook_service.emit_event(event_type, payload, project_id)` which enqueues the `deliver_webhook` Celery task. Deliveries are signed with HMAC-SHA256 and retry with exponential backoff.
- **Perf regression baselines** — `PerfBaseline` uses Welford's online algorithm. Update via `perf_regression_service.update_baseline(...)`. Never recompute mean/stddev from `TestCase` history at release-gate scoring time.
- **RAG faithfulness evaluator** — pluggable via `services/rag_faithfulness_service.py`. Default evaluator is Ollama; Ragas is optional. When `AI_OFFLINE_MODE=true` the service must fall back to Ollama regardless of config. Low-score cases get `ManagedTestCase.needs_review_reason="faithfulness_below_threshold"`.
- **Flaky quarantine state machine** — states: `PROPOSED → APPROVED → QUARANTINED → (RELEASED | RE_QUARANTINED)`. Both `QUARANTINED` and `RE_QUARANTINED` are treated as "active" by the ingestion enforcement path. Do not collapse `RE_QUARANTINED` to `QUARANTINED` at the end of `run_recheck_cycle` — that loses the re-quarantine signal for reports. Partial unique index `ux_fqr_live_per_fingerprint` enforces one live request per fingerprint.
- **Service ownership rule fields** — `ServiceOwnershipRule` columns are `match_pattern`, `service_name`, `team_name`, `priority`, `is_active`. Do NOT reference `glob_pattern` / `owner` / `team` — those names don't exist. Filter by `is_active.is_(True)` and order by `priority.desc()` for conflict resolution.
- **Degradation primitive** — when a router or service depends on an external system that can degrade (ChromaDB, MongoDB, MinIO, hosted LLM), wrap the call with `services/resilience.with_fallback(primary, fallback, name, is_empty=...)`. The primary runs first; on exception or `is_empty(result)` truth, the fallback runs and a `resilience.fallback` log line is emitted. The reference call site is `routers/search.py` (semantic → keyword fallback). Do not copy-paste inline try/except — every new copy drifts. The helper sits next to the existing `async_retry`; use both when retries + degradation are both needed.
- **Live-stream ingestion completes via `finalize_run`.** `worker/tasks.py::persist_live_session` MUST call `services/ingestion_pipeline.finalize_run` after committing the TestCase rows (wrapped in try/except so a finalize failure can't fail the task — the rows are already committed). Without this call, `test_suites`, `canonical_test_cases`, `suite_memberships`, auto-tagging, and the AI pipeline are all skipped for live-stream runs. The dedup check at the top of `_run` must use the **same session** as the writes below — opening a second `AsyncSessionLocal` under asyncpg races with the main session ("another operation in progress").
- **Agent pipeline status is effectively-derived for read endpoints.** `routers/agents.py::list_pipelines` and `get_pipeline` mutate `pipeline.status` to `"failed"` when the stored value is `"running"` AND either (a) any associated stage row is `failed`, or (b) `started_at` is past `RUNNING_STALE_THRESHOLD` (30 min) with no `completed_at`. The DB row is fixed asynchronously by the `reap_stuck_agent_pipelines` Celery beat task (`*/10 minutes`). The `?status=` query param applies to the STORED status (not derived) so paginated queries stay consistent.
- **Project reset endpoint requires typed-name match.** `POST /api/v1/projects/{id}/reset` (`routers/projects.py`) is ADMIN-only and the service (`project_reset_service.py`) rejects the call when `confirmation_name != project.name` (case-sensitive). Two modes: `runs` (cascade-delete from `test_runs`) and `full` (also wipes `test_suites`, `canonical_test_cases`, `releases`, `release_gate_policies`, `perf_baselines`, `flaky_quarantine_requests`, `managed_test_cases`, `test_plans`, `test_strategies`, `knowledge_sources`). The Project row + members + API keys + AI/SSO config survive both modes by design. Every successful reset writes a `settings_audit_log` row keyed `project_reset.<mode>`.
- **`/search/entity-counts` is the page-load fallback for the search UI.** `routers/search.py::get_entity_counts` returns project-scoped `{test_case, test_run, suite, defect, flaky_test, release}` totals via one round trip of six COUNT queries. The frontend's chip + Index Health rows render from this when no search response is active, and from `response.entity_counts` once a query runs. Empty `allowed_project_ids` (caller has zero memberships) returns all zeros via `where(False)` — never leaks cross-tenant counts.
- **Image tags in k8s manifests** — never `:latest`. CI guard `k8s-image-pin-check` in `.github/workflows/ci.yml` greps for `image: …:latest` and `newTag: latest` under `k8s/**`. The homelab overlay uses `newTag: BUILD_TAG_PLACEHOLDER`; `homelabsetup/deploy-homelab.sh` substitutes the placeholder with `build-YYYYMMDD-HHMMSS` immediately before `kubectl apply -k`, with a `trap` to restore the file on exit. The Celery worker + beat deployments carry a `wait-for-redis` `initContainer` (`busybox:1.36` running `until nc -z testlookup-redis 6379; do sleep 2; done`) so they cannot enter the zombie consumer state.
