# EXP-BUG-118 — Repeated criteria deletion could execute twice

**Mission / severity:** M21, P0.

The criteria execute route only read a `previewed` deletion job. It did not
change the status before dispatch, and the worker repeated the same read before
writing `running` in another session. Concurrent submits or duplicate Celery
deliveries could therefore both cross the destructive boundary.

The route now locks the job row, changes `previewed` to `queued`, and commits
before dispatch. The queued row is also a durable outbox: a periodic relay
re-publishes queued criteria jobs after broker or process failure. The worker
separately locks and changes `queued` to `running` before deleting anything,
and terminal writes use compare-and-set status checks. A live synthetic race
against the deployed Postgres showed the second claimant waiting 0.407 seconds
for the first transaction and then receiving 409; its project and job rows
were removed afterwards.
