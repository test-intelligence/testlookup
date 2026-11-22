---
name: testlookup-data-storage-quality
description: Review and improve TestLookup data and storage tests. Use for PostgreSQL and Alembic, MongoDB evidence, Valkey or Redis state, object storage, derived indexes, retention, and backup or restore validation.
---

# TestLookup Data and Storage Quality

## Identify the ownership and failure boundary

- Read `docs/architecture/data-model.md`, `docs/reference/data-dictionary.md` and `docs/reference/migrations.md`. Trace `backend/app/models/postgres.py`, `backend/migrations` and the matching database, Mongo, Redis or object-storage module under `backend/app/db`.
- Identify which store is authoritative for each claim. Follow relational IDs through rich evidence, transient stream/cache keys, blobs and derived views. Check scope, retention, index/query shape, migration compatibility and reconciliation after partial failures. Check the rebuild source for optional indexes and the fate of historical records during restore or reindex.
- For modifications to existing rows, exercise pre-change data, constraints, a repeat execution or interruption where meaningful, and new reads after commit. For deletion or retention, use isolated synthetic data and verify authoritative state, projections and the documented cleanup window.

## Verify data invariants

- Use the relevant `backend/tests/integration` test with the actual disposable store for store-specific semantics. For migrations, backfills or constraints, verify the supported prior migration head, forward upgrade, representative read/write and fresh installation. Reuse the documented rehearsal for a supported rollback or restore.
- For claims spanning stores, prefer tests that verify IDs, terminal records and independently read projections after retry. Use `backend/tests/integration/test_evidence_artifact_authority_postgres.py` for evidence authority and `backend/tests/integration/test_decision_report_supersession_postgres_mongo.py` for a cross-store example.
- For operational changes, inspect `scripts/ops/verify_backup_scripts.sh` and the documented disposable upgrade runner before any actual backup or restore. Record migration heads, fixture shape, scope, constraints exercised and evidence of recovery.
- Refresh generated reference documentation when schemas, migration inventory or configuration contracts change.
