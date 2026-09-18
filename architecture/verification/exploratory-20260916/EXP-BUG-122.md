# EXP-BUG-122 — Foreign run IDs were distinguishable from missing IDs

**Mission / severity:** M22, P1.

`require_run_access` returned 403 after resolving an existing foreign run but
404 for a missing run. A project-bound key could therefore test whether a UUID
belonged to another project. Run-subject access now returns the same 404 and
message for foreign membership and foreign key binding. Focused guard tests,
two asserted mutations, and the live CLI probe verify the concealment.
