# Test Suite Run Comparison Design

## Goal

Add suite-scoped comparison of two test runs with this default behavior:

- User selects a `suite_name`.
- System compares the latest completed test run containing that suite against the previous latest completed test run containing the same suite.
- User may explicitly compare any two historical runs, but only when both run IDs are provided by the user.
- An AI agent reviews the deterministic diff and produces a grounded comparison report.

The feature should reuse the existing run comparison surface instead of creating a parallel diff engine.

## Current Code Structure

Existing pieces to extend:

- `backend/app/routers/run_compare.py`
  - Current endpoint: `GET /api/v1/runs/compare?left={uuid}&right={uuid}`.
  - Enforces tenant access for both run IDs before calling the service.
- `backend/app/services/run_compare_service.py`
  - Compares two runs by `TestCase.test_fingerprint`.
  - Classifies deltas as `new_failure`, `fixed`, `still_failing`, `regressed`, `new_test`, `removed_test`, `duration_spike`, and `renamed`.
  - Already has fuzzy pairing for renamed tests.
- `backend/app/models/schemas.py`
  - Contains `RunCompareSummary`, `RunCompareTestDelta`, and `RunCompareResponse`.
- `frontend/src/pages/RunComparePage.tsx`
  - Current UI is explicit left/right UUID comparison.
- `frontend/src/services/runCompareService.ts` and `frontend/src/hooks/useRunCompare.ts`
  - Current client supports only explicit left/right query params.
- `backend/app/models/postgres.py`
  - `TestRun` already has `primary_suite_name` and `suite_names`.
  - `TestCase` has `suite_name`, `test_fingerprint`, status, duration, and failure fields.
- `backend/app/services/llm_factory.py` and `backend/app/services/llm_json_parser.py`
  - Existing provider-agnostic LLM and strict JSON parsing utilities.
- `backend/app/agents/summary_agent.py` and `backend/app/agents/regression_watchman.py`
  - Good prompt/fallback patterns for grounded AI reports.

Notable current gap:

- `RunComparePage` and frontend types expect `primary_suite_name` / `suite_names`, but `RunCompareSummary` and `_summary_dict()` do not currently include those fields. The suite comparison implementation should align the backend schema and response with the frontend expectation.

## Product Behavior

### Default Comparison

Default comparison is suite-scoped and automatic:

```text
Compare suite "checkout-regression"
=> latest completed run containing checkout-regression
vs previous completed run containing checkout-regression
```

Selection rules:

1. Filter by project access.
2. Filter to completed/final runs, not `IN_PROGRESS`.
3. Filter to runs where at least one `TestCase.suite_name == suite_name`.
4. Sort by effective execution time:
   - Prefer `TestRun.end_time`.
   - Fall back to `TestRun.created_at`.
5. Pick newest as `right` and second newest as `left`.

If fewer than two runs exist for the suite, return a `404` or `422` with a clear message:

```json
{
  "detail": "At least two completed runs are required to compare suite checkout-regression"
}
```

### Explicit Historical Comparison

Historical comparison is allowed only when the user explicitly provides both run IDs:

```text
GET /api/v1/runs/compare?left={old_run_id}&right={new_run_id}&suite_name=checkout-regression
```

Rules:

- Both run IDs must be different.
- Both runs must be accessible to the current user.
- Both runs must contain the specified suite.
- The service compares only test cases from the specified suite.
- The UI should label this as `Manual comparison` or `Explicit historical comparison`.

### Run-Level Comparison

Keep the existing explicit run-level endpoint behavior:

```text
GET /api/v1/runs/compare?left={uuid}&right={uuid}
```

This remains useful for full-run comparisons.

## Backend API Design

Extend the existing `run_compare` router instead of adding unrelated endpoints.

### Endpoint 1: Explicit Compare

```http
GET /api/v1/runs/compare?left={uuid}&right={uuid}&suite_name={optional}
```

Behavior:

- Without `suite_name`: current whole-run behavior.
- With `suite_name`: compare only that suite in the two specified runs.

### Endpoint 2: Default Latest Suite Compare

```http
GET /api/v1/runs/compare/latest?suite_name={suite_name}&project_id={optional}
```

Behavior:

- Resolves the latest and previous latest completed runs for the suite.
- Returns the same `RunCompareResponse` shape as explicit comparison.
- Adds selection metadata so the UI can explain what happened.

Recommended response additions:

```python
class RunCompareSelection(BaseModel):
    mode: Literal["latest_vs_previous", "explicit"]
    suite_name: Optional[str] = None
    selection_reason: str
    project_id: uuid.UUID

class RunCompareAIReport(BaseModel):
    executive_summary: str
    markdown_report: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    key_differences: list[str]
    recommended_actions: list[str]
    confidence: int
    fallback_used: bool = False

class RunCompareResponse(BaseModel):
    ...
    scope: Literal["run", "suite"] = "run"
    suite_name: Optional[str] = None
    selection: Optional[RunCompareSelection] = None
    ai_report: Optional[RunCompareAIReport] = None
```

Keep `ai_report` optional initially so the deterministic compare remains fast and reliable when AI is unavailable.

## Service Design

Extend `backend/app/services/run_compare_service.py`.

### New Public Functions

```python
async def resolve_latest_suite_pair(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    suite_name: str,
) -> tuple[TestRun, TestRun]:
    """Return (previous, latest) completed runs containing suite_name."""

async def compare_runs(
    db: AsyncSession,
    left_id: uuid.UUID,
    right_id: uuid.UUID,
    *,
    suite_name: str | None = None,
    include_ai_report: bool = False,
) -> dict[str, Any]:
    ...
```

### Query for Latest Suite Pair

Use `TestCase` as the source of truth for suite membership:

```sql
SELECT tr.*
FROM test_runs tr
WHERE tr.project_id = :project_id
  AND tr.status != 'IN_PROGRESS'
  AND EXISTS (
    SELECT 1
    FROM test_cases tc
    WHERE tc.test_run_id = tr.id
      AND tc.suite_name = :suite_name
  )
ORDER BY COALESCE(tr.end_time, tr.created_at) DESC
LIMIT 2
```

Avoid relying only on `TestRun.suite_names` JSON because `TestCase.suite_name` is queryable, canonical, and already indexed-adjacent.

### Suite-Scoped Test Loading

Modify `_load_test_rows()`:

```python
async def _load_test_rows(
    db: AsyncSession,
    run_id: uuid.UUID,
    suite_name: str | None = None,
) -> dict[str, TestCase]:
    stmt = select(TestCase).where(TestCase.test_run_id == run_id)
    if suite_name:
        stmt = stmt.where(TestCase.suite_name == suite_name)
```

When `suite_name` is passed, all aggregate counts in the compare response should be suite-local, not whole-run counts. That means `_summary_dict()` needs a suite-aware variant:

```python
async def _summary_dict_for_scope(db, run, suite_name=None) -> dict:
    if not suite_name:
        return _summary_dict(run)
    # compute counts from TestCase rows for that suite
```

This is important because a run may contain 20 suites, but the user asked for one.

## AI Agent Design

Add a dedicated comparison agent:

```text
backend/app/agents/run_compare_agent.py
```

Name: `RunCompareAgent`

Purpose:

- Consume deterministic `RunCompareResponse` data.
- Produce a grounded narrative report.
- Never recompute the diff.
- Never invent failures or causes outside the diff payload.

Inputs:

- `left` and `right` summaries.
- `suite_name`.
- Delta counts.
- Top N urgent deltas:
  - new failures
  - regressions
  - still failing
  - duration spikes
  - fixed tests
  - removed/new tests
- Optional existing AIAnalysis records for changed failing tests.

Output JSON:

```json
{
  "executive_summary": "2-4 sentence summary",
  "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
  "key_differences": ["..."],
  "new_risks": ["..."],
  "resolved_risks": ["..."],
  "duration_concerns": ["..."],
  "recommended_actions": ["..."],
  "confidence": 0,
  "confidence_reason": "..."
}
```

Prompt rules:

- State exact run IDs/build numbers and suite name.
- Use exact counts from deterministic diff.
- If no root cause data exists, say "Insufficient AI analysis evidence".
- Do not call something a product bug unless existing `AIAnalysis.failure_category` supports it.
- Use deterministic classification as the source of truth.

Fallback behavior:

- If the LLM fails, create a deterministic report from counts and top deltas.
- Set `fallback_used = true`.
- This mirrors `SummaryAgent` fallback behavior.

Implementation location:

- `backend/app/agents/run_compare_agent.py` for prompt + fallback.
- `backend/app/services/run_compare_ai_service.py` if we want a service-level wrapper that can be called from router or Celery.

## Persistence

For initial implementation, compute on demand and return the AI report inline.

For production caching, add a table:

```python
class RunComparisonReport(Base):
    __tablename__ = "run_comparison_reports"
    id: UUID primary key
    project_id: UUID
    left_run_id: UUID
    right_run_id: UUID
    suite_name: Optional[str]
    compare_payload: JSON
    ai_report: JSON
    fallback_used: bool
    prompt_version: str
    model_name: Optional[str]
    created_by_user_id: Optional[UUID]
    created_at: datetime
```

Unique index:

```text
(project_id, left_run_id, right_run_id, suite_name, prompt_version)
```

This avoids rerunning AI for the same comparison and gives report history.

## Frontend Design

Extend `RunComparePage` rather than creating a new page.

### Default Flow

Add a suite-first panel:

- Project selector or current project context.
- Suite selector/search.
- Primary action: `Compare latest two runs`.

The page calls:

```ts
runCompareService.compareLatestSuite(projectId, suiteName)
```

### Explicit Historical Flow

Keep the existing left/right UUID inputs but visually label them as manual:

- `Manual historical comparison`
- Optional `suite_name` input/select.
- Require user to press `Compare`; never auto-select old runs except via latest suite flow.

### AI Report UI

Add an `AI Comparison Report` section above the delta table:

- Risk badge.
- Executive summary.
- Key differences.
- Recommended actions.
- Fallback label when generated deterministically.

Do not block the deterministic diff table if AI fails.

## Tests

### Backend Unit Tests

Add to `backend/tests/services/test_run_compare_service.py`:

- `_load_test_rows()` filters by suite.
- Suite-scoped summaries use only that suite's cases.
- Default latest pair selects newest and second newest runs containing suite.
- Runs from other suites are ignored.
- Runs from other projects are ignored.
- In-progress runs are ignored.
- Fewer than two suite runs raises a clear error.

### Backend Router Tests

Add under `backend/tests/routers/`:

- `GET /runs/compare/latest?suite_name=...` returns latest-vs-previous.
- Explicit `left/right/suite_name` compares only that suite.
- Explicit comparison fails if either run lacks the suite.
- Tenant isolation still applies to both explicit and default modes.
- AI unavailable returns deterministic compare with fallback report or no `ai_report`, depending on chosen default.

### AI Tests

Add tests for:

- Prompt context contains only deterministic facts.
- JSON parse fallback works.
- Generated fallback report includes new failures, fixed tests, and duration spikes.
- AI report never changes deterministic counts.

### Frontend Tests

Add page/service tests:

- Latest suite compare calls `/api/v1/runs/compare/latest`.
- Manual compare still calls `/api/v1/runs/compare`.
- AI report renders when present.
- Deterministic diff renders when `ai_report` is absent.
- Empty state for fewer than two suite runs.

## Implementation Order

1. Backend schema updates:
   - Add `primary_suite_name` and `suite_names` to `RunCompareSummary`.
   - Add `scope`, `suite_name`, `selection`, and optional `ai_report` to `RunCompareResponse`.
2. Service updates:
   - Add suite filter to `compare_runs`.
   - Add suite-scoped summary counts.
   - Add `resolve_latest_suite_pair`.
3. Router updates:
   - Add optional `suite_name` to existing compare endpoint.
   - Add `/api/v1/runs/compare/latest`.
4. AI comparison agent:
   - Add prompt, JSON parser, deterministic fallback.
   - Wire as optional report generation.
5. Frontend:
   - Add latest suite compare flow.
   - Add optional suite filter to manual comparison.
   - Render AI report.
6. Tests:
   - Add service, router, AI fallback, and frontend tests.
7. Optional persistence:
   - Add migration and `RunComparisonReport` model if cached AI reports are desired in the first release.

## Open Questions

1. Should latest-vs-previous be scoped by branch by default, or should branch be only a visible warning when the two latest suite runs are on different branches?
2. Should `suite_name` matching be exact, case-insensitive, or normalized with aliases?
3. Should AI report generation be synchronous in the compare response, or async with a `report_status` field for large suites?

Recommended defaults:

- Exact `suite_name` match for v1.
- Same project only; show branch/commit drift but do not filter by branch unless product decides otherwise.
- Deterministic diff synchronous; AI report optional and cached/async later if latency becomes noticeable.
