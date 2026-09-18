# EXP-BUG-100 — Concurrent plan changes could publish inconsistent aggregates

**Mission / severity:** M16, P0.

Membership and execution mutations recomputed counters without one shared
serialization point, and duplicate membership depended on a late database
error. These operations now lock the plan aggregate, recompute inside the same
transaction and return a stable 409 for an existing member while retaining the
unique constraint as the concurrent backstop.
