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

The consolidated minor-release branch now completes 234 files / 1,765 tests at
62.23% statements, 57.06% branches, 54.38% functions and 63.78% lines.
`testManagementService.ts` reaches 91.20% statements, 62.26% branches, 98.43%
functions and 92.77% lines.

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
- Investigator review-subject tests pin trigger attribution, transactional
  review creation, per-investigation subject isolation, pending-excerpt
  withholding, accepted distribution, draft watermarking, and the report
  collector/renderer seam used by digest attachments.
- `testManagementService.test.ts` pins project omission/scoping, every major
  CRUD and AI request family, encoded suite routes, duplicate-detection bodies,
  export filtering, download filenames, DOM cleanup, and object-URL release.
- Test Cases page tests pin stale-row preservation, initial-load failure copy,
  joint table/health retries, failed-retry visibility, and the single-flight
  retry control. The page no longer presents an outage as an empty catalog.
- `accessibility-keyboard.spec.ts` drives the real SPA using only keyboard
  input. It pins the skip-to-main landmark journey and the New Project dialog's
  focus entry, Tab wrap, Escape close, and opener-focus restoration.
- MCP coverage is measured over its client, config, prompts, resources,
  review, server, token-verifier, and tool modules: 41.96% measured with a 41%
  CI floor. CLI coverage is measured separately at 63.16% with a 62% floor;
  its SDK tests run in a distinct invocation to preserve the measurement.
- CI now runs frontend unit tests with coverage and the hermetic browser lane.

These tests target prior 0%-covered high-risk services and a previously live-only
auth journey. They do not inflate coverage by testing type-only modules or
trivial constants.

## Next targets, in order

| Priority | Target | Current signal | Required tests |
|---|---|---|---|
| P0 | TestManagement page | request failure/retry states covered | lifecycle mutation races and remaining tabs |
| P0 | notification distribution | AI summaries, digests, and Investigator excerpts are gated | webhook/comment repost after acceptance and provider-level delivery proofs |
| P0 | release decision/override browser path | live-only integration | hermetic decision rendering plus PostgreSQL audit integration |
| P1 | RAG drawers/components | roughly 0–14% | empty/citation/error/unsafe-link states |
| P1 | integrations settings | roughly 22–28% | saved/masked secrets, validation, provider failures |
| P1 | analytics widget picker/registry | roughly 0–11% | add/duplicate/limit/persistence and malformed saved view |
| P1 | digest/notification settings | roughly 0–43% | schedule/timezone, channel contract, error recovery |
| P2 | `main.tsx`/polyfills | 0% | boot smoke only if it catches configuration regressions |
| P2 | MCP and CLI | 41% MCP / 62% CLI floors enforced | expand low tool and command modules before raising independently |

Coverage percentage is a change detector, not a release claim. Critical
requirements still need negative, concurrency, authorization and live-system
evidence even when their lines are executed.
