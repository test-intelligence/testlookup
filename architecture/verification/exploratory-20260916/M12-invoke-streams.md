# M12 — Invoke, idempotency, polling and event streams

## Result

**PASS — the invocation contract now survives concurrent submission,
cancellation, retry and real EventSource use.** Exact executable candidate
`e96b9de3` was deployed first as `build-20260917-172252`. `/health/version`
reported the full candidate revision and every backend/worker Deployment became
available on that immutable tag before verification ran.

## What was proved

- The catalog publishes every registered capability and derives `invokable`
  and `sync_eligible` from executable registry/planner truth. The deployed
  deterministic sentinel accepted async and sync calls on a stored run; sync
  completed with HTTP 200 and public status `passed`.
- An identical idempotent replay returned HTTP 200 and the same invocation;
  changed content returned 422. Real PostgreSQL races converged on one active
  invocation, and the durable unique key is independently scoped by user,
  project and agent route.
- An explicit key cannot disappear behind an unrelated active invocation.
  Invocation creation locks the subject row, closing the unkeyed race as well.
- A live stream ticket opened SSE without a Bearer header, emitted an
  `invocation` frame, failed replay with 401, and failed after its real 60-second
  Redis expiry with 401. Disconnecting that stream did not affect the run;
  authenticated polling reached the same durable terminal result.
- Retry refuses review rejection and cancellation, commits `retry_wait` before
  dispatch and supplies the expected-attempt fence. Cancellation remains sticky
  in the broker window before a pipeline exists; worker entry and pipeline
  creation both recheck it.
- A zero-length sync wait reads once without sleeping; bounded 200/202/409/422/
  503 outcomes are present in OpenAPI. SSE change detection covers the whole
  public response, including error, output and review changes.

## Verification

- Focused invocation suite: **118 passed** before the final routing regression;
  final counts are recorded in the candidate manifest and branch validation.
- Real PostgreSQL concurrency/cancellation suite: **7 passed**.
- Mutation harness: **16 unsafe changes killed**, with exact-once mutation
  application and byte restoration.
- Live homelab journey: revision `e96b9de3`; catalog 30; async 202 then `passed`;
  same-key replay 200; changed request 422; SSE ticket issue 200, replay 401,
  expiry 401; sync 200/`passed`; ten requests; polling survived stream close.
- Alembic has one head, `0191`; both new migrations have real downgrades and
  the replacement index is built concurrently in an autocommit block.

## Defects fixed

EXP-BUG-082 through EXP-BUG-088. The branch also adds the existing real
PostgreSQL invocation race module to CI; it had been tracked but never selected.

## Deviations and remaining gaps

The stream ticket is intentionally a short-lived bearer capability bound to
one invocation. It has no actor identity at redemption because browser
`EventSource` cannot attach the actor's Authorization header; access is checked
when the ticket is minted. The live mission therefore proves anonymous ticket
redemption plus replay and expiry, rather than claiming actor binding.

Direct structured payload invocation remains outside E1.2. The live shared
homelab used a fast deterministic agent, so sync-capacity exhaustion and the
pre-worker cancellation edge are covered by deterministic unit, mounted-app,
worker and real-PostgreSQL tests rather than timing-dependent live races.
