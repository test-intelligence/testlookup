# EXP-BUG-082 — Cancelled invocation retries could be admitted

**Mission / severity:** M12, P0.

Retry did not treat pipeline cancellation as terminal and dispatched before
persisting its own admission, so cancellation or a second request could race
the worker. Retry now locks the run, refuses either cancellation signal,
commits `retry_wait` first and passes an expected-attempt fence.

Unit regressions cover cancelled and concurrent retry behavior; the real
PostgreSQL cancellation/retry serialization test covers the row-lock boundary.
The M12 mutation harness removes each control independently.
