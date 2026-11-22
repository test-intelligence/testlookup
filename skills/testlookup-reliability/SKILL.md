---
name: testlookup-reliability
description: Review TestLookup reliability, availability, fault tolerance, and recovery. Use for dependency outages, worker restarts, durable retries, service degradation, failover, backups, restore, and disaster recovery drills.
---

# TestLookup Reliability and Recovery

## Establish the service and recovery contract

- Identify the affected API or user journey, critical dependencies, durable acceptance boundary, and eligible operations. Record the expected normal and degraded behaviour, availability target, and recovery time or recovery point objective where specified. Read `docs/reference/worker-operations.md` and `docs/operations/deployment.md` for the chosen profile.
- Trace a request or run through the accepted, queued, persisted, and terminal states. Identify retry ownership, replay semantics, terminal failure handling, and user-visible status. Derive which records must survive the event and how the workflow accounts for retried work.

## Rehearse failure and recovery

- Use disposable fixtures and a comparable healthy control. Select one relevant dependency failure or process restart. Check the documented response during the interruption, completion after recovery, visible status, and duplicate or missing records from a fresh read. For live ingestion, inspect `backend/tests/integration/test_live_recovery_protocol_postgres_redis.py`.
- Use `e2e-runner/upgrade_README.md` and `scripts/ops/verify_backup_scripts.sh` for the applicable rollback or recovery checks. Restore disposable stores as the profile requires and verify known data, schema, required external artifacts, and an end-to-end operation before declaring recovery.
- Record candidate revision, profile, failure interval, successful eligible operations, observed recovery time, and the timestamp or checkpoint of the latest restored data. Link the restart, replay, or restore evidence and record any unverified failure mode.
