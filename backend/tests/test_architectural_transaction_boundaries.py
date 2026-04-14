"""
Architectural transaction-boundary ratchet.

Parallel to ``test_architectural_authorization.py`` — this one ratchets
out the **transaction-boundary** discipline we built up through item #2:

  * Services **should not own transactions**. They stage changes with
    ``db.add`` / mutation / ``db.flush()`` and return. The router handler
    owns ``await db.commit()`` so a single commit covers the whole unit
    of work (business mutation + audit row + counter update + anything
    else the handler wants to add).

  * Services that genuinely need to own a transaction fall into a small,
    explicitly-labeled allowlist: Celery workers, scheduled tasks,
    dedicated-write-session helpers for command/query separation, or
    outermost orchestration layers. Each allowlisted module has a reason.

The test fails if:

  1. A service module with **zero allowed commits** suddenly gains a new
     ``await db.commit()`` (regression — someone reintroduced the
     anti-pattern we just removed).
  2. An allowlisted module's commit count exceeds the approved cap.
  3. An allowlist entry becomes stale (module has fewer commits than the
     allowance — tighten the cap).

The ratchet mirrors the authorization one: corrections are always
welcome (shrink the list, lower the cap), additions require team
sign-off and a written reason.

Documented CQS exceptions — GET endpoints that intentionally write:

  * ``reports.py`` ``export_run_report_pdf`` / ``export_evidence_bundle``
    write an ``AccessAuditLog`` row when an authenticated user downloads
    a PDF or evidence bundle. These are audit-trail writes — the whole
    point of the row is to record the read access, so CQS doesn't apply.
  * ``shared_reports.py`` ``view_shared_report`` /
    ``download_shared_report_pdf`` write ``AccessAuditLog`` entries for
    anonymous share-link access. Same reasoning.

These are captured in-prose rather than in an allowlist because they
live in router handlers (not services) and they stage via ``db.add``
rather than a service-level ``db.commit()``. The handler's own
``db.commit()`` is the legitimate unit-of-work write.
"""
from __future__ import annotations

import re
from pathlib import Path

# Services that have been fully converted to stage-only. A future commit
# of ``await db.commit()`` in any of these would regress item #2.
STAGE_ONLY_SERVICES: frozenset[str] = frozenset({
    "chat_service.py",
    "release_service.py",
    "release_council_service.py",
    "defect_promotion_service.py",
    "knowledge_source_service.py",
    "test_management_service.py",
    "notification_service.py",
    "action_policy.py",
    "onboarding_service.py",
    "rag_review_service.py",
    "feedback_service.py",
    "test_management_ai_service.py",
    "evidence_service.py",
    "stream_service.py",
    "feature_flag_service.py",
    "share_link_service.py",
    "refresh_token_service.py",
})

# Services that still own commits, with a cap and a documented reason.
# Lower the cap when work lands that removes a commit; never raise it
# without team sign-off. Entry shape:
#   "module.py": (max_commits, "reason")
COMMIT_ALLOWLIST: dict[str, tuple[int, str]] = {
    # ── Worker-owned transactions (Celery tasks) ─────────────────────
    "agent_memory_service.py": (
        1,
        "Celery-task-owned: pipeline memory persist runs in an isolated "
        "AsyncSessionLocal from the worker, no HTTP request to hand off to.",
    ),
    "ingestion.py": (
        1,
        "Outermost orchestration: the ingestion pipeline is the owning "
        "unit of work; commit at the end of the full parse/persist cycle.",
    ),
    "ingestion_pipeline.py": (
        2,
        "Outermost orchestration used by both the ingest router and Celery "
        "tasks. After the 2026-04-14 data-corruption fix, finalize_run commits "
        "the run-aggregates update in its own session and then runs each "
        "post-step (suite_sync, auto_tagging, release_linking) in a "
        "``_run_isolated`` helper that commits per step — the helper contains "
        "the second commit. A single-commit model re-introduced the bug where "
        "a failing post-step left the SQLAlchemy session in a rollback-required "
        "state and poisoned subsequent steps.",
    ),
    "integration_probe_service.py": (
        1,
        "Scheduled Celery beat probe: owns its own AsyncSessionLocal, "
        "no request-scoped session to hand off to.",
    ),
    "intelligence_snapshot_service.py": (
        2,
        "save_snapshot + invalidate are called from Celery workers (where "
        "the worker owns the session) and from the run_intelligence router "
        "(which now uses a dedicated AsyncSessionLocal for GET-path cache "
        "populate — item #4). Worker-owned path makes service-level commits "
        "the pragmatic fit.",
    ),
    "knowledge_sync_service.py": (
        6,
        "Worker-heavy sync pipeline with its own AsyncSessionLocal. "
        "Converting would require splitting every sync step between a "
        "request-scoped path and a worker-scoped path. Left as-is until "
        "the worker session model is redesigned.",
    ),
    "notification/manager.py": (
        1,
        "Called exclusively from Celery tasks (dispatch_run_notifications "
        "and siblings) via AsyncSessionLocal — not from request handlers.",
    ),
    "rag_generation_service.py": (
        2,
        "grounded_generate has mutually-exclusive happy-path + "
        "failure-recovery commits. Moving them would require redesigning "
        "error-state rollback logic (SAVEPOINT or similar). At most one "
        "runs per invocation, so the caller still sees one-commit-per-call.",
    ),
    "run_diff_service.py": (
        1,
        "Outermost: the diff computation persists its own summary row as "
        "part of a single unit of work with no further caller-side work.",
    ),
    "run_intelligence_service.py": (
        1,
        "Dedicated write session for the defect-candidate cache populate "
        "(item #4 CQS split — see _build_and_persist_defect_candidates). "
        "The write_db is isolated from the caller's read session.",
    ),
    "suite_sync_service.py": (
        1,
        "Called from the ingestion pipeline orchestration; its persistence "
        "is the outermost unit of work for suite membership computation.",
    ),
    "test_health_coach_service.py": (
        1,
        "Dedicated write session for the flaky-coach cache populate "
        "(item #4 CQS split — see _populate_flaky_cache_in_new_session). "
        "The write_db is isolated from the caller's read session.",
    ),
    "training/exporter.py": (
        1,
        "Celery-task-owned: training data export runs in an isolated "
        "worker session, no request handoff.",
    ),
    "eval_gate_service.py": (
        1,
        "AI eval gate: persists evaluation result during a Celery-scheduled "
        "gate check. Worker-owned tx boundary.",
    ),
}

SERVICES_DIR = Path(__file__).resolve().parent.parent / "app" / "services"

# Count only real commits. Documentation (``""""``, ``#``) and variable
# assignments don't count.
_COMMIT_RE = re.compile(r"^\s*await\s+\w*db\w*\.commit\(\)\s*(#.*)?$", re.MULTILINE)


def _service_source_files() -> list[Path]:
    """All .py files under backend/app/services/, recursively."""
    return sorted(
        p for p in SERVICES_DIR.rglob("*.py")
        if p.name != "__init__.py"
    )


def _relative_name(path: Path) -> str:
    """Relative path from ``services/`` so ``notification/manager.py`` is
    distinct from a hypothetical top-level ``manager.py``."""
    return str(path.relative_to(SERVICES_DIR)).replace("\\", "/")


def _count_commits(path: Path) -> int:
    """Count ``await <session>.commit()`` occurrences in a source file."""
    text = path.read_text(encoding="utf-8")
    return len(_COMMIT_RE.findall(text))


# ── The tests ───────────────────────────────────────────────────────────────


def test_stage_only_services_have_zero_commits() -> None:
    """Every service in ``STAGE_ONLY_SERVICES`` must have **zero**
    ``await db.commit()`` occurrences. Regressing this list would
    reintroduce the anti-pattern item #2 removed.
    """
    offenders: list[tuple[str, int]] = []
    for source in _service_source_files():
        name = _relative_name(source)
        basename = source.name
        if basename not in STAGE_ONLY_SERVICES:
            continue
        count = _count_commits(source)
        if count > 0:
            offenders.append((name, count))

    assert not offenders, (
        "Stage-only services regressed — these modules should never own a "
        "transaction:\n  "
        + "\n  ".join(f"{name}: {count} commit(s)" for name, count in offenders)
        + "\n\nFix: remove the commit and let the router handler own it, "
        "following the pattern in the rest of the converted services."
    )


def test_commit_allowlist_caps_are_accurate() -> None:
    """Every allowlisted module must be within its approved cap, and the
    cap should not be set higher than the actual count (stale cap means
    we can safely tighten it).
    """
    exceeded: list[str] = []
    stale: list[str] = []

    by_relative_name = {
        _relative_name(p): _count_commits(p)
        for p in _service_source_files()
    }

    for relpath, (cap, _reason) in COMMIT_ALLOWLIST.items():
        count = by_relative_name.get(relpath, 0)
        if count > cap:
            exceeded.append(f"{relpath}: {count} commits (cap={cap})")
        elif count < cap:
            stale.append(f"{relpath}: {count} commits but cap={cap} — tighten it")

    messages: list[str] = []
    if exceeded:
        messages.append(
            "Allowlisted services exceeded their commit cap — add a stage-only "
            "refactor instead of raising the cap:\n  " + "\n  ".join(exceeded)
        )
    if stale:
        messages.append(
            "Allowlist caps are stale — lower them to match reality:\n  "
            + "\n  ".join(stale)
        )
    assert not messages, "\n\n".join(messages)


def test_new_services_are_either_stage_only_or_on_the_allowlist() -> None:
    """Any service module with a ``db.commit()`` call must be on the
    allowlist with a written reason. New files added without one fail CI.
    """
    allowlist_basenames = {
        Path(relpath).name for relpath in COMMIT_ALLOWLIST
    }

    unaccounted: list[str] = []
    for source in _service_source_files():
        count = _count_commits(source)
        if count == 0:
            continue
        relname = _relative_name(source)
        basename = source.name
        if relname in COMMIT_ALLOWLIST:
            continue
        if basename in allowlist_basenames:
            continue
        unaccounted.append(f"{relname}: {count} commit(s)")

    assert not unaccounted, (
        "Services own commits but are not on the allowlist. Either convert "
        "them to stage-only (preferred) or add an entry to "
        "COMMIT_ALLOWLIST with a written reason:\n  "
        + "\n  ".join(unaccounted)
    )


def test_allowlist_total_is_bounded() -> None:
    """Sanity cap — if the total allowed commits ever exceeds 25, someone
    is adding without cleaning up. Forces a downward-only ratchet over time.
    """
    total = sum(cap for cap, _ in COMMIT_ALLOWLIST.values())
    assert total <= 25, (
        f"COMMIT_ALLOWLIST sums to {total} allowed commits — lower the caps "
        "or remove entries instead of raising this limit."
    )
