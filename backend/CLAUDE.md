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
3. Add dispatch branch in `analysis_router.classify_test()`
4. Add mode to config validation regex in `schemas.py:AIConfigUpdate.analysis_mode`
5. Add radio option in `frontend/src/pages/settings/AIConfigPage.tsx:ANALYSIS_MODES`

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
