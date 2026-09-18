# EXP-BUG-024 — JSON export audit was never committed

**Mission / severity:** M08, P1.

The JSON route staged its draft/would-refuse audit and returned before the row
was committed. It now commits the distribution record before returning.

**Green evidence:** exact deployed `77fc9bd2`; focused export and asserted audit
mutations passed. Fix: `395d05e0`.
