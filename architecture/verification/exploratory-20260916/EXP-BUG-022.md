# EXP-BUG-022 — queued worker could resurrect a review-rejected pipeline

**Mission / requirement / severity:** M07, terminal review rejection, P1.

The HTTP retry endpoints refused review-rejected pipelines, but a retry queued
immediately before a human rejection could reach the worker afterward. The
worker's locked resume claim checked generic resumability and could move the
rejected row back to running.

The worker claim now repeats the terminal `review_rejected` check while holding
the authoritative pipeline row lock.

**Red evidence:** `c3d66890` claimed and restarted a failed row whose error was
`review_rejected: missing_evidence`.

**Green evidence:** exact deployed `bbce4f5f` refused the claim in both the unit
and real PostgreSQL worker-resume suites.

**Mutation:** removing the locked worker refusal was killed by the queued-resume
regression; the harness asserts one replacement and restores the source bytes.
