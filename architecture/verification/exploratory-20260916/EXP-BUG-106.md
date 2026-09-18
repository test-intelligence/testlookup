# EXP-BUG-106 — Ingestion left aggregate analytics caches stale

**Mission / severity:** M17, P1.

Ingestion could commit new project facts without clearing all-project analytics
keys. Both ingestion paths now invalidate project and aggregate keys only after
the database commit succeeds.
