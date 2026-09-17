# EXP-BUG-083 — Concurrent invokes could create duplicate active work

**Mission / severity:** M12, P0.

The active-invocation read and insert were not serialized. Two requests for the
same stored subject could both observe no active row and dispatch duplicate
work. Invocation now locks the authoritative `TestRun` through the lookup and
insert transaction.

The route regression proves the lock is present. A two-session real PostgreSQL
race proves both callers converge on one durable invocation and one dispatch.
