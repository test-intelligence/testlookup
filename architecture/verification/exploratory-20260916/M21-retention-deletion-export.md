# M21 — Retention, protected evidence, deletion and export

## Result

**PARTIAL — two P0 deletion-boundary defects are fixed and the exact executable
was deployed before testing.** Candidate `47356a97` was deployed as
`build-20260918-094024`. Every application deployment was ready on that tag,
the backend and all workers used the candidate digest, `/health/version`
returned the full revision, readiness and detailed health were green, and
Alembic reported `0191 (head)`.

## What was proved

- Criteria execution has durable, one-way ownership transitions:
  `previewed -> queued` in the API transaction, followed by `queued -> running`
  in the worker transaction. Both transitions lock the deletion-job row.
- The API commits `queued` before dispatch. That queued row is a durable outbox;
  the beat relay republishes queued jobs every minute after broker or process
  failure, and duplicate deliveries are safe.
- Duplicate API claims and at-least-once worker deliveries cannot both cross
  the destructive boundary.
- Single-run and criteria workers lock the current run and recheck executing
  status, release links, compliance packs, decision reports, and citation-store
  availability before purging search or touching any deletion store.
- Decision-report publication takes a Postgres share lock on its run through
  the Mongo insert. Deletion takes the conflicting update lock, closing the
  cross-store interval between citation recheck and destructive work.
- Critic failure attempts and summary projections hold that subject lock as a
  unit. If deletion wins and removes the run first, failure persistence is
  suppressed rather than recreating orphan Mongo evidence.
- Existing retention coverage continued to prove project/run scoping, safe
  artifact prefixes, preview blocker reporting, tombstones, append-only audit
  discipline, report-artifact protection, compliance packs, export scope, and
  review envelopes.
- A live synthetic Postgres race on the exact deployed backend held the first
  claim transaction open. The second claim waited **0.407 seconds**, observed
  `queued`, and received **409**. The synthetic job and project were deleted in
  the script's cleanup path.
- A second live race held decision-report publication inside its Mongo insert.
  An update-lock contender waited **0.154 seconds** until publication
  completed. A deletion-first inverse race held the update lock, removed the
  run, and proved the waiting critic produced **zero Mongo writes** after
  **0.167 seconds**. All synthetic rows were removed afterwards.

## Verification

- Homelab executable authority: revision
  `47356a97f50ad18b4320854e405b379d4fecf3e2`; tag
  `build-20260918-094024`; backend and worker digest
  `sha256:030201c98b2721d6cf32b5e636d9562a99f451d9534950bca9792b6d451e3432`;
  frontend digest
  `sha256:b2f120016b413fbf88455f749241b8efbb468a072a03b3ea4a0da7cf7ba05052`;
  MCP digest
  `sha256:0dbe7c85653c85043e4ae28d35d60cb52f8bf31176acc6370c5626f88d6c7263`.
- Focused criteria/single-run deletion tests: **88 passed**. Broader retention,
  deletion, compliance, report protection, and export suite: **312 passed, 8
  skipped**. The skips require optional integration services.
- Ruff passed. Mypy held at **367/367**.
- Quality gate: all **43 guards** green. Guard self-tests: **238 passed**.
- Mutation harness: **12 unsafe changes killed** and its pytest wrapper passed.
  Each mutation selector must apply exactly once, every mutated run must fail,
  and the original bytes are restored in `finally`.
- Reviewer follow-up `d39700b8` added cross-store lock serialization, the
  durable queue relay, compare-and-set terminal writes, and behavioral
  concurrency coverage. Follow-ups through `e7132a00` corrected test doubles
  and the mypy-visible row-count type. Reviewer follow-up `47356a97` protected
  failure evidence from deletion-first races; the final executable was
  redeployed.
- Independent final review: pending.

The final deployment completed the application build, push, migration, and
rollout with the unrelated Ollama refresh explicitly skipped. The separate
exact-deployment verifier passed every application tag, digest, revision,
readiness, health, and schema check before tests ran.

## Defects fixed

EXP-BUG-118 and EXP-BUG-119.

## Deviations and remaining gaps

The mission requires dedicated synthetic stores and a successful disposable
backup/restore rehearsal before any destructive cross-store delete. The only
available homelab namespace is shared, and its restore operation replaces
PostgreSQL, MongoDB, and MinIO data. No whole-run deletion, store-boundary
interruption, destructive retry, restore, or live export reconciliation was
performed. The live exercise was limited to synthetic projects, a deletion-job
claim, a test run, and an in-memory report collection, and removed all
persisted rows it created. Legal holds remain future product work as the
mission specification states. These gaps keep M21 partial.
