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

**Regression, fix, mutation, review, and deployed retest:** pending after the
M01 P1 authentication defects are closed.
