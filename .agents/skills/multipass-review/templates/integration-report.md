# Pass 2 — Cross-File Integration Review

- **Branch:** `<branch>` vs `main`
- **Reviewer:** multipass-review (Pass 2, cross-file integration)

> Findings here span multiple files. Each cites every file/line involved.

## 1. Endpoint contract alignment
- [SEV] `routers/x.py:NN` + `services/x.py:NN` + `models/schemas.py:NN` — <mismatch>. **Fix:** <action>.
- _or:_ Checked endpoints `POST /api/v1/...`, `GET /api/v1/...` — contracts aligned.

## 2. Frontend ↔ backend parity
- [SEV] `frontend/src/types/x.ts:NN` ↔ `models/schemas.py:NN` — field `<name>` <renamed/missing/optional-mismatch>. **Fix:** <action>.

## 3. Migration ↔ ORM coherence
- Alembic heads after this change: <1 / N — list revisions if >1>.
- [SEV] `migrations/versions/xxxx.py` ↔ `models/postgres.py:NN` — <drift>. **Fix:** <action>.

## 4. Dependency direction & module coherence
- [SEV] <file A> ↔ <file B> — <cycle / wrong direction / unwired service / duplicated logic>. **Fix:** <action>.

## 5. Shared invariants
| Invariant | Status | Note (file:line if violated) |
|---|---|---|
| One ingestion pipeline (`finalize_run`) | OK / VIOLATED / N/A | |
| One analysis router (`classify_test`) | | |
| One AI-config resolver | | |
| PII redaction at boundaries | | |
| Offline mode gates above flags | | |
| Feature flag on new capability | | |
| `ALL_PROJECTS_ID` never a backend UUID | | |
| Tenant scoping on list endpoints | | |
| Effective-suite query pattern | | |
| LiveSession slug→UUID conversion | | |

## 6. End-to-end flow trace
For each touched flow: <flow name> — SDK/UI → router → service → DB → read → UI.
- [SEV] <flow> — field `<x>` is dropped at <hop / file:line>. **Fix:** <action>.
- _or:_ <flow> threads through cleanly.

## 7. Cross-seam test coverage
- [SEV] No integration/e2e test for new flow <x>. **Fix:** add `backend/tests/integration/...` or `frontend/tests/e2e/...`.

## Integration tally

| Severity | Count |
|---|---|
| Blocker | 0 |
| Major | 0 |
| Minor | 0 |
| Nit | 0 |
