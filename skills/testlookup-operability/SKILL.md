---
name: testlookup-operability
description: Assess TestLookup operability and supportability. Use for incident triage, runbooks, health checks, ownership, routine maintenance, and operator actions across the deployed system.
---

# TestLookup Operability and Supportability

## Follow the operator workflow

- Read `docs/reference/api/health.md`, `docs/reference/api/integration-health.md`, and `docs/reference/worker-operations.md` for the affected area. Identify the profile, responsible role, trigger, affected dependency, required access, and supported maintenance or recovery operation.
- Follow the signal from detection to triage, action, and confirmation. Verify that alert context leads to the right component and revision, that the runbook describes a safe operation in the selected profile, and that the operator can distinguish active work from stuck or already completed work.

## Verify maintenance and evidence

- Use disposable fixtures for actions that change deployment state. Check initial conditions, intended action, user-visible status, and readback after the system settles. Exercise a repeated or unavailable action if the documented workflow supports it.
- Use `backend/tests/regression/test_admin_maintenance_router.py` and `backend/tests/regression/test_backup_alerts_and_scrape_targets.py` for relevant operator-path checks. Use [Observability](../testlookup-observability-quality/SKILL.md) for signal correctness and [Reliability](../testlookup-reliability/SKILL.md) for recovery behaviour.
- Record the scenario, first actionable signal, responsible role, runbook, operation evidence, time to detect or complete when measured, and any handoff or documentation gap.
