# EXP-BUG-102 — Saved views crossed tenant and ownership boundaries

**Mission / severity:** M17, P0.

Direct shared-view reads did not recheck project membership, removed members
could mutate rows they owned, project-less shared rows leaked beyond their
owner, and all-project listing did not apply one consistent access scope. The
router now enforces current membership and owner authority on every path.
