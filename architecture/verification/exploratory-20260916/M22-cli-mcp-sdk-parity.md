# M22 — CLI, MCP and SDK contract parity

## Result

**PARTIAL — five client-contract defects are fixed and the exact executable
was deployed before final testing.** Candidate `96f56b6b` was deployed as
`build-20260918-105610`. Every application deployment was ready on that tag,
the backend and workers used digest
`sha256:0c8cc5d3941d1f90f9e47fd256930e9610c663d091923f268b5a9bf0d985b94f`,
`/health/version` returned the full revision, all health endpoints were green,
and Alembic reported `0191 (head)`.

## Live proof

- The installed CLI read a project and run with a project-bound API key. A
  foreign run returned the same not-found code **5** as an absent subject,
  malformed input returned validation code **2**, a revoked key returned auth
  code **3**, and a network refusal returned nonzero code **1**. Review accept
  was refused locally for the API-key profile.
- The MCP inventory exposed **67** tools, including catalog, invoke, invocation
  status, and pending-review reads, with no accept/reject tool. A synchronous
  ingestion invocation replayed the same idempotency key without a second
  invocation. A foreign invocation was refused, malformed input was rejected,
  and an asynchronous summary reached `completed` with `pending_review` in
  both MCP and REST. Its review queue subject matched the pipeline run.
- The Python SDK download contained six files and imported from an isolated
  extraction directory. Go, Java, and JavaScript archives contained 7, 14,
  and 9 entries respectively, with no build-output directories. The installed
  Python streaming SDK created and completed a two-case run. After key
  revocation it reported zero sent events and bounded failures without leaking
  the key.
- Every synthetic project, run owner, and credential created by the probe was
  removed or deactivated in `finally` cleanup.

## Defects fixed

- **EXP-BUG-120:** the public Python SDK download contained only
  `testlookup_reporter.py`, although it imports two sibling modules. It is now
  a manifest-controlled ZIP containing the reporter, both siblings, install
  metadata, README, and example configuration.
- **EXP-BUG-121:** CLI command wrappers replaced mapped auth, permission,
  not-found, validation, and timeout exit codes with generic code 1. Commands
  now preserve `CLIError.exit_code`; HTTP 400 joins 422 as validation.
- **EXP-BUG-122:** direct run lookup distinguished a foreign run from a missing
  run through 403 versus 404. Run-subject guards now conceal both foreign
  membership and project-bound-key mismatches as not found.
- **EXP-BUG-123:** upload POST and status-poll HTTP failures bypassed the
  shared CLI error mapper, so stable automation codes were replaced with code
  1. Both paths now raise mapped `CLIError` instances.
- **EXP-BUG-124:** the live execution guide still described the Python SDK as
  one `.py` file after the endpoint became a required-module ZIP. It now tells
  users to extract and install the bundled project.

## Verification

- Exact-deploy verification covered nine application deployments, the two
  autoscaled critical and ingestion replicas, serving image digests, revision,
  readiness, detailed health, and schema head before tests ran.
- Backend Ruff passed. The mypy ratchet held at **367/367**. All **43 guards**
  passed and **238** guard self-tests passed. Agent API artifacts matched the
  current OpenAPI schema and TypeScript compiled cleanly.
- **448** agent/review/authorization/transaction key tests, **36** focused M22
  tests, **149** CLI tests, and **177** MCP tests passed; optional CLI suites
  skipped two environment-dependent cases.
- The M22 harness killed **10** asserted mutations covering the missing Python
  sibling and real package metadata, stable CLI code propagation, HTTP 400
  classification, upload POST and poll mappings, UI installation guidance,
  and both foreign-run concealment paths. Each selector passed before its
  mutation, each mutated run had to fail with pytest/vitest status 1, every
  replacement applied exactly once, and original bytes were restored.

## Deviations and remaining gaps

The architecture already records that the CLI has no agent catalog or invoke
surface. The four streaming SDKs likewise do not expose agent invocation, so
those unsupported paths could only be identified explicitly rather than
compared with MCP/REST. Java, Go, and JavaScript were inspected as candidate
archives but were not each compiled and run against the live backend. No live
browser session was used for the REST/UI comparison; the REST review envelope
and queue were compared directly with MCP. These gaps keep M22 partial.

M23 remains blocked because the shared homelab cannot safely restart its Redis
consumer or inject a second stale worker. It needs an isolated disposable
environment before the live ordering and replay mission can run.
