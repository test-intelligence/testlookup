---
name: testlookup-deployment-quality
description: Review and improve TestLookup deployment and operations tests. Use for Compose, Kubernetes, K3s, OpenShift, VM or air-gap profiles, readiness, capacity configuration, backup, and upgrade or rollback rehearsals.
---

# TestLookup Deployment and Operations Quality

## Select the target profile

- Read `docs/operations/deployment.md` and the relevant deployment guide. Trace only the applicable Compose file, `k8s/base` and overlay, `k3ssetup`, `openshiftsetup`, `infra` or air-gap manifest. Identify service sets, network/TLS assumptions, queues, datastore ownership, probes and dependency profiles.
- Compare rendered configuration against the verified candidate manifest, required migrations and `deploy/images.manifest.txt`. Use `scripts/release/check_image_drift.py`. Validate the PostgreSQL budget against the rendered profile using `scripts/validate_db_connection_budget.py` and `docs/operations/database-connection-budget.md` when replicas, processes or pools change.
- Review rolling startup, migration ordering, worker subscriptions, graceful shutdown, probes and storage privileges in the target profile. Render with fixture values and verify service selectors, queue coverage and scrape targets after render or overlay substitutions. Sanitize rendered configuration before retaining it.

## Prove install and recovery

- Verify a fresh start, matching running image digests and revision, readiness, and a representative ingestion and processing path on an authorized disposable target. Check the effect of a relevant unavailable dependency on readiness, durable work and recovery.
- Select the documented baseline and supported upgrade/rollback drill in `e2e-runner/upgrade_README.md`. Check data and migration revision after restore alongside service recovery. Use `scripts/ops/verify_backup_scripts.sh` for the backup/restore tooling checks before any environment procedure.
- Record target profile, manifest identity, migration heads, health and functional readback, recovery evidence, and profile-specific gaps. Treat an environment-specific change, such as a rollout or restore, as an operation that requires its own authorization.
