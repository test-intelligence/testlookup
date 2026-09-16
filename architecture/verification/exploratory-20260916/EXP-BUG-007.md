# EXP-BUG-007 — deploy readiness selector includes completed migration pods

**Mission / requirement / severity:** M26, reliable homelab deployment, P2.

The application rollout and strict workload authority verification both pass,
but the later `wait_for_pods app=testlookup-backend` helper also selects every
retained migration Job pod. Those completed pods never satisfy Ready, so the
script waits 180 seconds, reports the healthy backend as unavailable, waits
another 120 seconds, and skips automatic admin creation before its final live
health check succeeds.

This was reproduced on both exact candidate deployments. The serving backend
was `1/1 Running`, all application workload digests matched, `/health/version`
reported the exact commit, and the final health check passed.

The fix defines one serving-backend selector qualified by
`app.kubernetes.io/component=api` and uses it for both readiness waits, admin
creation, and legacy-account cleanup. The regression pins every use so a broad
selector cannot silently return. The mutation harness replaces the shared
selector with the old broad value and requires the regression to fail; it also
verifies that every mutated file is restored byte-for-byte.

Exact commit `1cff9ae4b942f805a700339a035b14f69216d95e` was built as
`build-20260916-174750` and deployed before green validation. Both waits logged
the API-qualified selector and completed immediately while 28 retained
migration pods remained visible. The idempotent admin command then ran and
reported that the admin already existed. `/health/version` reported the exact
commit, ready and detailed health were green, all nine application Deployments
were ready on the immutable tag, and Alembic reported `0189 (head)`.

The focused regression suite passed 25 tests, Ruff was clean, the mypy ratchet
held at 369, all 19 M26 mutations were killed, the 43-guard quality gate passed,
and its 238 fixture self-tests passed. Independent review approved the patch
with tracked diff hash `3cc4e9b5d8ff43c69d594330f1ec3363a35ada33`.

**Result:** REVIEW_APPROVED. The deployment no longer waits on completed
migration pods or skips admin creation. M26 still requires its rollback
rehearsal and restored-candidate proof.
