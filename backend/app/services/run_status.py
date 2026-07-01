"""Single source of truth for a *finalized* run's terminal ``LaunchStatus``.

Shared by the file/finalize path (``ingestion._update_run_aggregates``) and the
live/close path (``stream_service.upsert_test_run``) so both grade a run the
same way.

A run that executed nothing — ``executed = passed + failed + broken == 0``,
i.e. an empty or parse-failed upload, or a fully-skipped suite — is **STOPPED**:
neither PASSED (which let empty/broken ingests masquerade as green runs in
trends and the release gate) nor FAILED (which would inflate failure counts and
falsely block a release). STOPPED is the neutral terminal state — it is excluded
from the "last green" baseline (``run_diff``) and the FAILED buckets
(``audit_dashboard``) alike. Only ``executed > 0`` runs grade as PASSED/FAILED.

This function must only be applied to a *finalized/closed* run — an in-progress
run legitimately has ``executed == 0`` and stays IN_PROGRESS (the live drainer
creates it that way; this helper is never called mid-run).
"""
from app.models.postgres import LaunchStatus


def terminal_run_status(executed: int, failed: int, broken: int) -> LaunchStatus:
    """Return the terminal status for a finalized run from its counts."""
    if executed <= 0:
        return LaunchStatus.STOPPED
    if (failed + broken) > 0:
        return LaunchStatus.FAILED
    return LaunchStatus.PASSED
