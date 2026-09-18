# M13 — Custom workflow and reviewer governance

## Result

**PARTIAL — all safe local, database and deployed read-only variants pass; the
credentialed live mutation journey remains blocked.** Exact executable candidate
`78ccd6b3` was deployed first as `build-20260917-234755`. `/health/version`
reported the full candidate revision, all serving deployments were ready on the
candidate images, Alembic reported `0191 (head)`, and the review gate remained at
its owner-approved default of false before final verification.

## What was proved

- The compiler rejects unknown or registered-but-unexecutable capabilities,
  missing dependencies, cycles, invalid and excessive conditions, unsupported
  per-step model metadata, policy violations, extra reviewer loops and parallel
  reviewers. Missing relational facts take the false branch.
- Published custom steps use only their declared tool subset. Runs freeze the
  definition, behavior plan, project/version, sanitized configuration, model,
  endpoint fingerprint, prompt, feature and policy authority. Resume and
  pipeline-bound checkpoint restore fail closed when any executable authority or
  runtime version changes; named repeated steps retain their instance identity.
- A reviewer has one bounded producer retry. Final rejection stops execution,
  deterministic blocking failures cannot become `pass_with_flags`, and low
  independent-model agreement forces human review. Reviewer evidence, verdict,
  supervisor route and shared budget remain in durable metadata even when no
  report stage ran.
- Producer escalation and reviewer retry share one per-step budget, and a
  supervisor LLM override is applied to the retried producer. Independent
  reviewer checks use the run-level atomic model budget without consuming the
  producer loop counter.
- Publication is bound to the selected draft version and definition digest,
  serializes with competing publication/evaluation, keeps published evidence
  immutable, and makes an exact repeated publish read-only. It always reruns the
  authoritative 100-run window and requires a fresh manifest checksum when a
  measured regression is accepted.
- G4 replay requires a tamper-evident stage receipt and binds the recorded input,
  output, behavior plan, configuration, prompt, runtime, tools, feature/policy
  snapshot and evaluation corpus. Duplicate attempts select the latest terminal
  result. Cached step output never claims to measure control-flow topology.
  Behavior-identical workflow forks may reuse exact behavior evidence while
  workflow identity remains recorded as provenance.

## Verification

- Final focused backend suite: **262 passed**; final frontend workflow suite:
  **7 passed**.
- Backend Ruff passed; mypy stayed below the ratchet at **367/367**; TypeScript
  passed; ESLint reported **0 errors** and 19 existing warnings.
- Quality gate passed all **43 guards** and its self-tests passed **238 tests**;
  generated agent API documentation matched source.
- Mutation harness: **46 unsafe changes killed**, with every selector required
  to apply exactly once and source bytes restored after each mutation.
- Final homelab authority check: revision
  `78ccd6b3c30bd6d60ac8fe41da0275fba33d5f9e`; backend and workers digest
  `sha256:9fdddef93b3ece9e2516556850c399bcf2786dedd4c0e0ecfa58199fcb0ad13c`;
  frontend digest
  `sha256:6e8c75c1765c9caf64d606e1e4518eee90222540581b525f74ed4bc98c557af5`;
  MCP digest
  `sha256:95d4563df97b940a33eacfc98523bcf307f1e6c9e89d4de386fdaa84db1c2366`;
  readiness was healthy and schema was `0191 (head)`.
- The final evaluation attestation is manifest
  `231cf64f9a0d2e2dc56495d5757e897b099a69e75b2f4f05032beedcaa678c6d`.
  Independent read-only reviews drove the final authority, finalization and
  mutation-selector repairs; no known code-level M13 release blocker remains.

## Defects fixed

EXP-BUG-089 through EXP-BUG-095.

## Deviations and remaining gaps

The shared homelab has no dedicated disposable workflow project or scoped
non-production credential. Automatic approval review refused using an admin
credential to create and publish definitions in an arbitrary existing project.
Consequently the live fork/edit/preview/evaluate/publish/execute journey and a
live two-session publication race are not claimed. Mounted-router, service,
real-PostgreSQL race, frontend and mutation coverage exercise those contracts
against the exact deployed code.

Candidate G4 cost, latency and reviewer comparisons intentionally replay exact
historical outputs; candidate model calls are not fresh inference. Replay also
marks topology unmeasured because cached step outputs cannot prove branch and
loop execution. These are explicit product semantics rather than waived tests.
