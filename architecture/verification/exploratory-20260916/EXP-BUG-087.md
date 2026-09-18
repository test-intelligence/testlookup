# EXP-BUG-087 — Accepted work could not be cancelled before worker pickup

**Mission / severity:** M12, P0.

Cancel returned 409 until a pipeline row existed. An accepted request stuck in
the broker window therefore had no terminal cancellation path. Migration 0191
adds sticky `cancel_requested` to the invocation. The API commits it under a
row lock; worker entry and pipeline creation recheck it before executing.

Route, worker and pipeline-creation regressions cover every boundary, including
retry refusal and public projection. The broader real PostgreSQL cancellation
suite proves the shared run-lock ordering used once a pipeline exists.
