# M16 — Test Management and lifecycle races

## Result

**PARTIAL — all safe local, real-PostgreSQL and deployed read-only variants
pass; credentialed live mutations and destructive outage variants remain
blocked.** Exact executable candidate `81dca0bc` was deployed first as
`build-20260918-014434`. `/health/version` reported the full candidate revision,
all serving deployments were ready on the candidate images, Alembic reported
`0191 (head)`, and the authenticated Test Management case, plan and audit reads
all succeeded.

## What was proved

- Direct lifecycle actions and ordinary case edits carry the version rendered
  by the client. A stale tab receives a stable 409 conflict before content,
  status, history or audit state can change.
- Test-plan membership accepts only cases from the plan's project and returns an
  indistinguishable 404 for missing and foreign identifiers.
- Add, remove and execute operations serialize through a lock on the plan row,
  so membership and aggregate counts share one transaction authority. Duplicate
  membership returns a stable 409 and the database unique constraint remains the
  final concurrency backstop.
- Plan execution accepts only the published passed, failed, blocked and skipped
  states. Membership and execution changes write append-only audit rows in the
  same transaction.
- Existing lifecycle role/reason rules, suite ownership, quarantine, filtering,
  totals, empty/error distinction and frontend contracts stayed green in the
  broad mission suite.

## Verification

- Final focused backend suite: **109 passed**; broad Test Management, lifecycle,
  suite, quarantine and ownership selection: **665 passed, 17 skipped**.
- Final frontend Test Management suite: **81 passed in 15 files**.
- Real PostgreSQL lifecycle and two-session plan-race suite against the homelab
  database: **8 passed** and
  its synthetic rows self-cleaned.
- Backend Ruff passed; mypy held at **367/367**; TypeScript passed; ESLint
  reported **0 errors** and 19 existing warnings.
- Quality gate passed all **43 guards** and its self-tests passed **238 tests**.
- The authorization and transaction-boundary ratchets passed **56 tests**;
  generated agent API documentation matched the current OpenAPI schema.
- Mutation harness: **14 unsafe changes killed**, with every selector required to
  apply exactly once and source bytes restored after every mutation.
- Final homelab authority check: revision
  `81dca0bc3adf4d7d249a52364be8da4467290d9c`; backend and workers digest
  `sha256:db1c8bb320080f1b563d7891e122791a2721aedb1663b89f800b05814da6cdd9`;
  frontend digest
  `sha256:6bc09a0022d997654cdd788b3f8e997bb04f21dc8a12627ff68db8a8d1e1a1b5`;
  MCP digest
  `sha256:95d4563df97b940a33eacfc98523bcf307f1e6c9e89d4de386fdaa84db1c2366`;
  readiness was healthy and schema was `0191 (head)`.
- Authenticated deployed reads covered all five projects (three cases, no plans,
  four audit rows), and an omitted-`expected_version` lifecycle request returned
  422 without mutation.
- Independent final review returned **APPROVE** after checking every direct UI
  caller, plan/audit locking and attribution, all 14 mutation selectors and the
  protected real-PostgreSQL race test.

## Defects fixed

EXP-BUG-096 through EXP-BUG-101.

## Deviations and remaining gaps

The shared homelab has no dedicated disposable Test Management project or
scoped non-production credential. Automatic approval review previously refused
using the administrator credential to create, edit, transition or delete data
in an arbitrary existing project. The live browser mutation journey and a live
two-session plan race therefore are not claimed. Authenticated read-only API
checks, mounted-router and service tests, real-PostgreSQL lifecycle tests,
frontend tests and mutation coverage exercised the contracts against the exact
deployed candidate.

Database, Redis or table-health outage injection also remains deferred to an
isolated environment because the available namespace is shared. Existing UI
error/retry tests cover the presentation contract without disrupting homelab
users.
