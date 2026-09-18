# M06 — release decisions, overrides and outcome history

**Result:** PASSED

**Window:** 2026-09-17T00:22Z–2026-09-17T01:48Z

**Final candidate:** `5d8dd8a579d5dbfd047f71c683d25007f78d5a52`
(tree `563e7c11e7f1ff22668bda0c71b577968204c668`), deployed before
final testing as `build-20260917-013906`, built at
`2026-09-17T01:39:07Z`. All nine application deployments were fully updated,
ready, and available; the critical worker had both replicas ready. Backend and
workers served `sha256:9cd8e30e67091b06281597a9b4c24a560d04c26af64939c18951e337db1299d0`,
frontend served `sha256:d855a636fe6de606885a72a910ae11ae1549cb57b053c8eef60f9a3bc45b0dd8`,
and MCP served `sha256:b01b5c380ab0039a077223ddfcd5c8469adbf0fa5a77aa8245024f13bbd89070`.
The version endpoint reported the exact revision, readiness reported
PostgreSQL, MongoDB, and Redis healthy, and Alembic reported `0189 (head)`.

## Deployed decision and outcome journey

Marker `exp-m06-1789609594` exercised four release evidence boundaries. Five
passes produced `GO` with 5/5 evidence; four passes and one failure produced
`NO_GO` with 5/5 evidence and named the failed test; five skipped tests produced
`NOT_EVALUATED` with 0/5 evidence; no runs produced `NOT_EVALUATED` with 0/0
evidence. This confirms the system does not infer optimistic readiness from
missing evidence.

A viewer received 403 for both gate evaluation and outcome creation. An
authorized actor recorded incident and rollback outcomes, both remained on the
release detail, and the activity ledger exposed exactly two
`release.outcome_marked` events. Cleanup reset and deleted the synthetic project
and disabled its viewer account.

## Persistence, concurrency, and history

Five real PostgreSQL journeys passed against the deployed homelab schema. They
cover ingest-to-release lineage, exclusion of phase decisions from release
compliance history, two concurrent override audit appends, recompute after an
existing override, and a controlled override-first race in which recompute
waits for the row lock. The race preserved human `GO`, current automated risk,
the immutable original `NO_GO`/80 snapshot, and the audit entry.

Three product/test defects were found and fixed: EXP-BUG-014 separated phase
and release histories; EXP-BUG-015 serialized JSON audit appends; EXP-BUG-016
serialized recomputation and protected human authority. A broadened suite also
found EXP-BUG-017, a false failure in its source-inspection helper, which now
removes docstrings by AST location.

## Verification

- 811 release, council, policy, and workflow tests passed.
- Five real PostgreSQL integration journeys passed.
- Six wrong-behavior mutations were killed: history scope (2), override audit
  serialization (1), recompute/override handling (2), and source helper (1).
- Ruff passed for `backend/app` and `backend/tests`.
- All 43 quality guards and all 238 guard self-tests passed.
- The mypy ratchet held at 369 errors in 114 files against baseline 369.
- Independent reviews approved EXP-BUG-014, EXP-BUG-015, and EXP-BUG-016;
  EXP-BUG-017's final review is recorded in its defect note.

No route, public state, migration, wire shape, review restriction, offline
ceiling, provider policy, or capability schema changed.
