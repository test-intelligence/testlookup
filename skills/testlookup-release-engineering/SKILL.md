---
name: testlookup-release-engineering
description: Review and improve TestLookup CI and release engineering. Use for workflow triggers, protected gates, artifacts, manifests, packaging, nightly verification, and release promotion evidence.
---

# TestLookup CI and Release Engineering Quality

## Trace the candidate

- Read `RELEASING.md`, the workflows at the tested ref and `e2e-runner/README.md`. Inspect triggers and branch/path filters in `.github/workflows/ci.yml` and `.github/workflows/release-integration.yml`. Confirm the actual jobs, dependencies and conditions that execute for the proposed event.
- Follow `.github/workflows/require-verified-sha.yml`, `.github/workflows/release-e2e.yml` and `.github/workflows/release.yml` from exact candidate SHA through verified images, the functional decision and promotion. Inspect `.github/workflows/release-e2e-nightly.yml` for recurring regression behavior. Use the E2E quality skill to assess product journey obligations.
- Match durable outputs to workflow run ID, source SHA, manifest hash, image digests and selected test tier. Check the upgrade baseline and the runner's actual verdict. Trace an unexecuted job, expired artifact or conditional result through the actual promotion conditions, including the manifest and revision checks.

## Verify a workflow change

- Run the targeted checks under `scripts/release`, `scripts/test_quality_gate.py` and `scripts/test_ci_security.py` as applicable. Use `make push-check-list` and `scripts/push_check.py` to find local equivalents. Check a passing candidate plus a missing or mismatched evidence fixture. For workflow changes, validate both intended trigger paths and event paths that should be skipped.
- Inspect artifacts and attestations for source binding, least-required job permissions, stable producer/consumer paths, and sanitized output. Use a dry run or safe fixture for packaging. Tags, uploads and promotion use their authorized release procedures.
- Record the trigger and candidate, gate outcomes, package or artifact identifiers, blockers and remaining evidence. Update runbook references when a workflow or artifact contract changes.
