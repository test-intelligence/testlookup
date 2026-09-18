# EXP-BUG-028 — notification retry froze stale review content

**Mission / severity:** M08, P0.

A queued delivery kept the projection made when it was staged. After a sink
failure, acceptance could still send a pending notice, or rejection could send
previously accepted prose. Every provider attempt now gates the original source
against current review authority.

**Green evidence:** exact deployed `77fc9bd2`; notification/outbox regressions
and three asserted retry mutations passed. Fix: `7cafbb22`.
