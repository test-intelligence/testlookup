# M07 — review subject, races and separation of duties

**Result:** PARTIAL — all executable authority and concurrency variants passed;
the dedicated enforcement-on deployment precondition is unavailable.

**Window:** 2026-09-17T02:22Z–2026-09-17T03:57Z

**Final executable candidate:** `bbce4f5f63105dfbba5d9eeb1b17b2d3a5b3ba26`
(tree `138aa53198da19a23763f6a931a3fc64ffcb3e50`), deployed before final
testing as `build-20260917-034955`, built at `2026-09-17T03:49:56Z`.
All nine application deployments rolled to the candidate and were ready. The
backend and workers served
`sha256:533184e3b700d9d5e90723b8e41aa377c82104ef9443e38e19b59ed69e1c3888`;
frontend served
`sha256:d855a636fe6de606885a72a910ae11ae1549cb57b053c8eef60f9a3bc45b0dd8`;
MCP served
`sha256:b01b5c380ab0039a077223ddfcd5c8469adbf0fa5a77aa8245024f13bbd89070`.
`/health/version` reported the exact revision and `/health/ready` reported
PostgreSQL, MongoDB, and Redis healthy.

## Findings and fixes

The first red probe on `c3d668907a21dc91b69634c934a70660127ffc24`
failed six authority contracts. Pending evidence reused its old review id;
distinct Investigator narratives superseded one another; review creation did
not lock its live subject; a generic parent-run envelope could select an
Investigator review; separation of duties consulted mutable live mode instead
of proposal-time mode; and an already queued worker could resume a
review-rejected pipeline. The focused run recorded 6 failures and 73 passes.

The implementation now serializes review creation and settlement in one
pipeline-then-review lock order, locks the stable parent scope before a first
review insert, and has ordinary and Investigator finalizers acquire that parent
before their child row to match retention/reset cascade order. It binds each
review id to one evidence hash,
refreshes the final locked ORM row after a waiter wakes, preserves distinct
Investigator subjects, excludes Investigator reviews from generic parent-run
authorization, resolves act mode only from frozen execution metadata, and
repeats the review-rejection refusal in the worker's locked resume claim.

Five defects are recorded as EXP-BUG-018 through EXP-BUG-022. The code also
corrects the architecture's stale premise that ordinary workflow separation of
duties consulted mutable project config; the code is the source of truth and
both ordinary and invocation proposals now use frozen execution metadata.

## Verification

- 323 focused review, retry, distribution, export, Investigator, and
  action-ledger tests passed on the final candidate; the mutation wrapper also
  passed.
- 15 real PostgreSQL tests passed on the final exact deployment. They include
  accept-versus-reject, accept-versus-supersession, distinct Investigator
  subjects, evidence replacement, and locked worker-resume behavior.
- Sixteen asserted wrong-behavior mutations were killed: ordinary and
  Investigator parent-first finalization, stable parent scope locking, subject
  locking,
  older-scope locking, evidence identity, Investigator isolation, parent and
  exact-pipeline envelope scope, canonical and frozen act authority, invocation
  precedence and preservation, ORM refresh, pipeline-first lock order, and
  rejected worker resume.
  Every replacement must occur exactly once and every file is restored
  byte-for-byte.
- The deployed `/reviews` accept journey passed in Chromium, Firefox, and
  WebKit. Its backend responses are mocked, so it is UI-transition evidence;
  the real settlement evidence is the PostgreSQL suite above.
- Four focused frontend Vitest files passed 22 tests; TypeScript and focused
  ESLint were clean.
- Ruff passed for `backend/app` and `backend/tests`; all 43 quality guards and
  238 guard self-tests passed; the mypy ratchet held at 369 errors in 114 files.
- Independent final rereview found no Blocker or Major findings and approved
  M07 executable scope at `bbce4f5f`.

## Deviations and remaining gaps

The shared homelab remains at `REVIEW_GATE_ENFORCED=false`. The mission called
for a dedicated enforcement-on stack, but no isolated namespace or disposable
database is available; changing the shared deployment would alter other users'
release behavior and contradict the owner's date-driven activation decision.
The enforcement-on deployed identity matrix therefore remains blocked.

The review queue still links an Investigator review to the parent run and does
not expose the exact narrative in the row. Exact backend authorization is now
safe, but reviewer navigation for that subject remains a P1 usability gap for a
future UI story. The owner's configured release-date activation mechanism is
also still a product gap; only the global Boolean setting exists today.

No public state, state-machine transition, stored config, review credential
restriction, capability schema, migration, or pinned wire shape changed.
