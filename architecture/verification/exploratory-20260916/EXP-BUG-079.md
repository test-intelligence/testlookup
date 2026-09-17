# EXP-BUG-079 — Frozen restore skipped a newly active eval-drift pin

Restoring accepted configuration reapplied environment ceilings but did not
query the current eval-drift pin. Frozen resolution now checks the live pin and
records every review-policy clamp it applies. Regression and mutation evidence
is in M11.
