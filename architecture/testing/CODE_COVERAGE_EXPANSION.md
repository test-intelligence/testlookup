# Code-Coverage Expansion Suite

## Measured baseline

Before this slice on 2026-09-15, `npm run test -- --coverage` completed 230
files / 1,739 tests:

| Metric | Measured | Enforced floor |
|---|---:|---:|
| Statements | 60.80% (9,033 / 14,855) | 60% |
| Branches | 56.12% (9,993 / 17,805) | 55% |
| Functions | 52.34% (2,635 / 5,034) | 51% |
| Lines | 62.26% (8,027 / 12,891) | 61% |

The floor is deliberately below the measurement to allow small V8/source-map
movement while rejecting material regression. Backend retains its existing 74%
CI floor (75.52% last documented measurement).

After this slice the same command completed 232 files / 1,745 tests at 60.87%
statements, 56.17% branches, 52.46% functions and 62.34% lines. Frontend
service statements rose from 24.97% to 26.14%; `reviewService.ts` moved from 0%
to 100% statements and lines.

## Coverage added in this change

- `authService.test.ts` pins OAuth form encoding, special-character handling,
  explicit bearer use while completing login, and normal authenticated reads.
- `reviewService.test.ts` pins project/review ID encoding, pending/all filters,
  and the distinct accept/reject payload contracts.
- `critical-journeys.spec.ts` exercises deep-link protection, resume-after-login,
  and explicit rejection of a cached token through a real browser.
- `review-gate.spec.ts` exercises review acceptance, required rejection reasons,
  separation of duties, and a read-only QA Engineer queue through the real SPA.
- `test_ingest_intelligence_defect_release_postgres.py` proves that persisted
  ingestion, run intelligence, defect promotion, and the append-only release
  gate share one release identity. It also caught and pins the missing release
  attribution on promoted defects.
- CI now runs frontend unit tests with coverage and the hermetic browser lane.

These tests target prior 0%-covered high-risk services and a previously live-only
auth journey. They do not inflate coverage by testing type-only modules or
trivial constants.

## Next targets, in order

| Priority | Target | Current signal | Required tests |
|---|---|---|---|
| P0 | `testManagementService` and TestManagement page | about 5–10% | lifecycle mutations, stale/race/error states, request contracts |
| P0 | notification distribution | review UI journey is covered; narrative delivery remains safety-critical | reviewed/pending narrative subject and redaction rules |
| P0 | release decision/override browser path | live-only integration | hermetic decision rendering plus PostgreSQL audit integration |
| P1 | RAG drawers/components | roughly 0–14% | empty/citation/error/unsafe-link states |
| P1 | integrations settings | roughly 22–28% | saved/masked secrets, validation, provider failures |
| P1 | analytics widget picker/registry | roughly 0–11% | add/duplicate/limit/persistence and malformed saved view |
| P1 | digest/notification settings | roughly 0–43% | schedule/timezone, channel contract, error recovery |
| P2 | `main.tsx`/polyfills | 0% | boot smoke only if it catches configuration regressions |
| P2 | MCP and CLI | no numeric floors | measure first; then ratchet by package |

Coverage percentage is a change detector, not a release claim. Critical
requirements still need negative, concurrency, authorization and live-system
evidence even when their lines are executed.
