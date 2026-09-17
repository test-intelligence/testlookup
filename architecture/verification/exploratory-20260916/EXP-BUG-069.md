# EXP-BUG-069 — Storage/index failures looked synchronized

MinIO failure returned an `upload_failed` path and Chroma failure still saved
PostgreSQL chunk metadata. Both failures now propagate so sync records FAILED
instead of SYNCED. Regression and mutation evidence is in M10.
