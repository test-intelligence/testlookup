# EXP-BUG-084 — Explicit idempotency keys could be silently discarded

**Mission / severity:** M12, P0.

When unrelated work for the same run and agent was already active, a request
with a new explicit key returned that invocation and never stored the caller's
key. The route now returns 409, preserving the promise that an accepted key
identifies the request it created.

The route-level regression reproduces the exact branch. The mounted live
journey proves accepted keys replay the same durable invocation and reject a
changed request.
