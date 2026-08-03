# Pass 2 — Cross-file integration checklist

These checks require looking at **multiple files together**. Each finding must cite every file/line involved. Work dimension by dimension.

## 1. Endpoint contract alignment (per touched endpoint)

Trace the full stack and confirm the shapes agree end to end:

`routers/<x>.py` (path, method, query/body params, response_model)
→ `services/<x>.py` (function signature, return type)
→ `models/postgres.py` (ORM columns it reads/writes)
→ `models/schemas.py` (Pydantic request/response models)

Flag: response_model fields the service never populates; request fields the router declares but the service ignores; Optional/required mismatches; a column written by the service but absent from the schema (silent data loss to the client); two routers sharing a prefix where the `GET /{id:UUID}` route is registered *before* a literal sub-path route (returns 422 on the literal path — register the specific route first).

## 2. Frontend ↔ backend parity

For each endpoint the frontend change consumes:
- `frontend/src/types/*.ts` matches the Pydantic response model field-for-field (name, optionality, enum members).
- The service-layer call (`frontend/src/services/*Service.ts`) sends the body the router expects and reads the fields the type declares.
- Enum string values match exactly between TS union types and the backend enum.

Flag any field present on one side and missing/renamed on the other — this is the classic "empty page, no error" bug.

## 3. Migration ↔ ORM coherence

- Every new/changed column in `models/postgres.py` has a corresponding migration op, and vice versa.
- Exactly one Alembic head after the new revision: the new `down_revision` chains onto the prior true head (re-check the latest revision; parallel agents create multiple-head conflicts).
- Server defaults / nullability in the migration match the ORM column definition.
- `downgrade()` actually reverses `upgrade()`.

## 4. Dependency direction & module coherence

- Routers import services, not the reverse. Services don't import routers.
- No new import cycle introduced (service A ↔ service B mutual import).
- Shared logic isn't duplicated across two new files — it should be one helper.
- A new service is registered/wired where it must be (router registered in `bootstrap.py`; Celery task registered; beat schedule entry added if it's periodic).

## 5. Shared invariants across the flow

The repo's highest-frequency silent regressions. Confirm the change set as a whole respects each:

- **One ingestion pipeline.** Any new ingestion/persist path ends in `ingestion_pipeline.finalize_run` (else `/runs` populated but `/suites` empty). Live-stream `persist_live_session` must call it.
- **One analysis router.** Classification goes through `analysis_router.classify_test`, never `run_triage_agent()` / `RulesEngine` directly.
- **One AI-config resolver.** `services/ai_config_resolver.py` is the only source (DB → secrets → env).
- **PII redaction at every boundary** before persistence / logging / LLM prompt / report render.
- **Offline mode is a hard gate above flags.** `AI_OFFLINE_MODE=true` short-circuits every outbound integration before the feature-flag check, not after.
- **Feature flag gates every new capability.**
- **`ALL_PROJECTS_ID = "all"`** is a frontend-only sentinel; backend `project_id` params are `Optional[uuid.UUID] = None`.
- **Tenant scoping:** every new list endpoint scopes by `get_accessible_project_ids` / project filter.
- **Effective-suite query pattern:** any query that filters/groups by suite considers BOTH `tc.suite_name` AND `tr.primary_suite_name`, or live-stream runs with old-SDK class-name events vanish from suite labels.
- **LiveSession.run_id is a slug,** not a UUID — converted via `canonical_test_run_uuid()` before queueing Celery tasks with UUID FKs.

## 6. End-to-end flow trace

For each user-facing flow the change touches, walk the data across all changed files at once: SDK/UI → router → service → DB → read path → UI. Confirm the new field/behavior actually threads through every hop. A change can be per-file-correct everywhere and still not work because a hop drops the field.

## 7. Test coverage across the seam

- Is the *interaction* tested, not just each unit? An integration test under `backend/tests/integration/` or an e2e spec under `frontend/tests/e2e/` for new cross-layer flows.
- Router tests that `from app.core.deps import X` at module load need the per-file autouse dependency patch (conftest monkeypatch on `app.core.deps.X` won't reach them).
