# EXP-BUG-014 — release compliance history mixed phase decisions

**Mission / requirement / severity:** M06, immutable and correctly scoped release
decision history, P1.

`release_gate_decision_service.decision_history()` treated a missing `phase_id`
as an omitted filter. Release-level compliance exports therefore included both
release decisions and phase decisions for the same release. A phase verdict such
as `NOT_EVALUATED` could appear in the release history beside `GO` or `NO_GO`,
making the exported audit record internally inconsistent.

The service now applies `phase_id IS NULL` for release-level history and retains
exact phase equality when a phase is requested. The change does not alter the
decision state machine, public status vocabulary, transaction owner, or tenant
boundary.

**Red evidence:** parent revision
`b807de3bc865be93c1b52bb759679005cfd2adb7` failed the unit query contract and
the real PostgreSQL compliance-pack regression. The persisted export contained
the phase-level `NOT_EVALUATED` verdict in release-level history.

**Green evidence:** exact revision
`d65eabdfa7c893375f2def18a6530bf08f3b47b0` was deployed as
`build-20260917-002215`. All nine application deployments were fully updated and
ready, including both critical-worker replicas. `/health/version` reported the
exact revision, `/health/ready` reported PostgreSQL, MongoDB and Redis healthy,
and Alembic reported `0189 (head)`. The backend manifest digest was
`sha256:4877c5e014d24d4668ff4900c24185d1665f2ee3f2e2826fa08784807006cb56`.

After that exact deployment, 26 focused release decision/outcome unit tests and
both real PostgreSQL ingestion/compliance journeys passed. Ruff was clean, all
43 quality guards passed, all 238 quality-guard self-tests passed, and the mypy
ratchet remained at 369 errors in 114 files against the 369 baseline.

**Mutation:** the harness asserts each replacement applies exactly once, bounds
each child test at 60 seconds, leaves 180 seconds for the outer process, and
restores the service source byte-for-byte in `finally`. Removing the release
`IS NULL` predicate and changing the phase predicate to `IS NULL` were both
killed by the regression suite.

**Independent review:** APPROVE on commit
`d65eabdfa7c893375f2def18a6530bf08f3b47b0`; reviewed tree
`98dd75b9a5480917d532aa71433d1ab59a2a8727` and tracked diff
`a192d06d566ee1b3fe52bc4be2eed2c8be2a485c`. The reviewer confirmed the SQL
scope, transaction and tenant safety, cleanup, mutation applicability, timeout
margin, and byte restoration.
