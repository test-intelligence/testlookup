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
    # ── Tier 0-2 stage-only conversions (Phase E-1, 2026-04-15) ──────
    "feature_flags.py",
    "compliance_pack_service.py",
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
        "the worker session model is redesigned. Re-added 2026-06-04: the "
        "module was wrongly clobbered to a 0-commit stub in 51dd4bc and has "
        "been restored to its full form (fix/restore-rag-knowledge-services).",
    ),
    "notification/manager.py": (
        1,
        "Called exclusively from Celery tasks (dispatch_run_notifications "
        "and siblings) via AsyncSessionLocal — not from request handlers.",
    ),
    "prompt_registry.py": (
        1,
        "CLI-owned transaction (AI-F2): `python -m app.services.prompt_registry "
        "--attest <change-id>` opens its own AsyncSessionLocal to run the "
        "eval gate and persist the AIEvalGateRun row for the attestation — "
        "a developer-tooling entry point with no request handler to hand "
        "the commit to. The runtime API surface of the module (get_prompt / "
        "registry_versions / manifests) never touches the DB.",
    ),
    "notification_transitions.py": (
        1,
        "Celery-task-owned (PMF US-7.1): evaluate_run_transitions runs from "
        "the dispatch_transition_notifications worker task on its own "
        "AsyncSessionLocal and commits the notification_test_states advance "
        "before dispatching. The router-facing policy upsert is stage-only "
        "(the notifications router handler owns that commit).",
    ),
    "agent_investigation_service.py": (
        1,
        "Hook-owned (Agentic AI-1): maybe_auto_trigger fires from inside "
        "Celery-owned host paths (transition-notification evaluation, the "
        "release-gate NO_GO persist) with no request session to hand off "
        "to; it opens its own AsyncSessionLocal, commits the investigation "
        "row, and only then enqueues the Celery task — the row must be "
        "durable before the worker can pick it up. Every router-facing "
        "function in the module (start_investigation, upsert_policy, "
        "request_cancel, record_agent_run) is stage-only.",
    ),
    "evidence_artifact_service.py": (
        1,
        "Terminal-agent-owned authority capture: the decision-report agent "
        "opens an isolated AsyncSessionLocal and must commit verified, "
        "immutable artifact rows before the signed evidence snapshot and "
        "critic can reference them. There is no request-scoped transaction "
        "to hand off to, and the single commit covers the bounded capture.",
    ),
    "cluster_investigation_orchestrator.py": (
        1,
        "Celery/deep-pipeline-owned Phase 3 orchestration: child rows, parent "
        "task correlation and the ID-only outbox commit atomically before "
        "broker delivery; relay outcome and bounded join reconciliation run "
        "in isolated AsyncSessionLocal transactions with no request session.",
    ),
    "agent_action_ledger_service.py": (
        4,
        "Durable action-ledger/outbox owner: approval execution and broker "
        "relay use isolated worker sessions so action state and dispatch "
        "retries remain atomic without a request-scoped transaction.",
    ),
    "decision_report_supersession_service.py": (
        6,
        "Durable report-supersession worker: request claiming, retry state, "
        "terminal publication and rejection each commit in isolated sessions "
        "because processing is asynchronous and has no request owner.",
    ),
    "notification_routing.py": (
        1,
        "Celery-task-owned (PMF US-7.3): record_team_delivery_logs persists "
        "the NotificationLog audit rows for team-channel deliveries. It runs "
        "inside evaluate_run_transitions (the dispatch_transition_"
        "notifications worker task) on its own AsyncSessionLocal — no "
        "request-scoped session exists to hand the commit to.",
    ),
    "rag_generation_service.py": (
        1,
        "grounded_generate commits once at the end of the happy path. The "
        "2026-06-02 review removed the failure-path rollback-then-recommit "
        "recovery: it called rollback() on the injected request session (the "
        "anti-pattern) and, because the batch was only flushed, persisted "
        "nothing — the except now just re-raises and lets get_db roll back. "
        "Ratcheted 2 -> 1. Re-added 2026-06-04 after the module was wrongly "
        "clobbered to a 0-commit stub in 51dd4bc and restored.",
    ),
    "run_diff_service.py": (
        1,
        "Dedicated write session for the diff-cache populate (CQS split, "
        "2026-06-02 review): get_baseline_diff is consumed by GET aggregators "
        "(run_intelligence_service.get_run_intelligence + the /baseline-diff "
        "endpoint) that keep using the injected session for further reads, so "
        "the RunBaseline/RunDiff cache is written via its own AsyncSessionLocal "
        "and the injected session stays read-only.",
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
    # eval_gate_service.py — removed from allowlist 2026-05-16 (P1-2 fix).
    # ``persist_agent_stack_gate_run`` and ``set_baseline_from_eval`` are
    # called from router handlers (Depends(get_db)) only — no Celery
    # worker calls them. The single-owner commit rule applies: services
    # flush, the dependency commits. See docs/DATABASE_AUDIT_2026-05-16.md
    # (P1-2) and backend/CLAUDE.md "Commit responsibility (single-owner
    # rule)".
    # ── Tier 0-2 services (2026-04-14 batch) ──────────────────────────
    # Each of these services integrates with audit-log writes that must
    # land in the same transaction as the primary mutation. Stage-only
    # conversion requires staging both the primary mutation and the audit
    # row in the caller's session so a single router-owned commit covers
    # both — see feature_flags.py (Phase E-1 reference conversion,
    # 2026-04-15). Services remaining below still need the same treatment.
    "llm_cost_budget.py": (
        2,
        "Tier 1-2 worker paths only: record_usage (from BaseAgent "
        "mark_stage_done) and _increment_cap_hit (from check_and_apply_cap) "
        "each open their own AsyncSessionLocal. The router-facing "
        "upsert_quota was converted to stage-only in Phase E-1 (2026-04-15).",
    ),
    "github_checks_service.py": (
        4,
        "Tier 1-5 worker paths only: post_check_run_for_run (called from "
        "the ingestion worker's finalize_run) writes last_error / "
        "last_posted_at / last_error_at on FOUR mutually-exclusive branches "
        "(retry-failure, success, non-success, and the SSRF-blocked-target "
        "branch added in the 2026-06-02 github-checks review — refuses to "
        "send the PAT to a loopback/link-local target and records it). Each "
        "runs on its own AsyncSessionLocal. The router-facing "
        "upsert_integration was converted to stage-only in Phase E-1 "
        "(2026-04-15). Ratcheted 3 → 4.",
    ),
    "github_pr_comment_service.py": (
        1,
        "Worker-path only (PMF US-4.1): post_pr_summary_for_run is called "
        "from the ingestion pipeline's post-ingestion orchestration — no "
        "request session to hand off to. The single commit lives in "
        "_record_outcome, which writes last_posted_at / last_error / "
        "last_error_at bookkeeping on the integration row via its own "
        "AsyncSessionLocal (same Integration Health surface the checks "
        "service writes).",
    ),
    "gitlab_integration_service.py": (
        1,
        "Worker-path only (PMF Epic 3 US-3.2): post_mr_note_for_run and "
        "post_commit_status_for_run are called from the ingestion pipeline's "
        "post-ingestion orchestration — no request session to hand off to. "
        "The single commit lives in _record_outcome (last_posted_at / "
        "last_error bookkeeping on its own short AsyncSessionLocal); BOTH "
        "delivery paths persist outcomes through it since the 2026-08-01 "
        "review fix restructured the commit-status path to stop holding a "
        "session across the HTTP round-trip (ratcheted 5 → 1). "
        "upsert_integration is stage-only (router owns the commit).",
    ),
    "flaky_quarantine_service.py": (
        11,
        "Tier 1-3: state-machine transitions (propose/approve/reject/"
        "release/expire/recheck) are called from both routers and the "
        "nightly Celery beat maintenance task; each transition owns its "
        "own transaction so a partial batch failure doesn't poison the "
        "rest of the sweep. +2 for the PMF US-5.4/US-5.5 lifecycle: the "
        "staleness sweep (beat) and the run-finalization stability "
        "tracker (Celery task hook) each own one commit on their own "
        "AsyncSessionLocal — no request session exists at either call "
        "site.",
    ),
    "webhook_service.py": (
        10,
        "Tier 2-6 (post 2026-05-16 P1 follow-up cleanup): subscription "
        "CRUD is now stage-only — create/update/delete_subscription let "
        "get_db commit the primary mutation; the audit row uses a fresh "
        "session inside ``_audit`` (P2-4 pattern) so audit failure no "
        "longer rolls back the subscription. The 10 remaining commits are "
        "all on isolated sessions: replay_delivery (1, enqueue-after-commit), "
        "emit_event (1, enqueue-after-commit), deliver_webhook (7, each "
        "branch of the Celery task that records delivery outcome on its "
        "own session — incl. the SSRF-blocked-target branch added in the "
        "webhook-service review), and ``_audit``'s own fresh-session commit "
        "(1). Ratcheted 14 → 9 → 10.",
    ),
    "perf_regression_service.py": (
        1,
        "Tier 2-10 worker-only: nightly Celery beat refresh_baselines "
        "owns its own AsyncSessionLocal — no caller session to hand off "
        "to. Confirmed in Phase E-1 audit (2026-04-15) that there is no "
        "router-facing mutation path: record_observation is stage-only, "
        "detect_spikes_for_run and list_top_baselines are read-only.",
    ),
    "rag_faithfulness_service.py": (
        2,
        "Tier 2-9 worker-only: both commit paths (gate_accept, "
        "persist_evaluation) open their own AsyncSessionLocal. "
        "Confirmed in Phase E-1 audit (2026-04-15) that neither entry "
        "point accepts a caller session — gate_accept runs inside the "
        "RAG review workflow and persist_evaluation runs inside the RAG "
        "generation Celery pipeline. No router-facing mutation path to "
        "convert.",
    ),
    # ── Fresh-session / worker / atomic-operation owners (2026-05) ─────
    "audit_log_service.py": (
        1,
        "Fresh-session by design: ``record_attempt`` opens its own "
        "AsyncSessionLocal so the audit row commits independently and "
        "SURVIVES a caller rollback (see backend/CLAUDE.md 'Audit-log "
        "writes — attempt vs outcome'). ``record_outcome`` writes to the "
        "caller's session and does NOT commit. The single commit here is "
        "the intentional independent-durability path.",
    ),
    "live_session_drainer.py": (
        1,
        "Celery-beat-owned: ``drain_run_buffer`` opens its own "
        "AsyncSessionLocal (the ``drain-active-live-sessions`` beat task "
        "has no request session to hand off to) and commits the "
        "incrementally-drained per-test rows. Phase 4.5 incremental drain.",
    ),
    "project_reset_service.py": (
        1,
        "Outermost atomic operation: ``reset_project`` is a destructive "
        "danger-zone reset that must delete project-scoped data + write "
        "the audit row in ONE transaction (FOR UPDATE serialises "
        "concurrent resets) so a mid-way failure leaves no orphans. The "
        "service owns the unit of work; the router just dispatches.",
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
    # Raised from 25 → 65 on 2026-04-14 to absorb the Tier 0-2 batch
    # (feature flags, LLM cost budget, compliance packs, GitHub Checks,
    # flaky quarantine, outbound webhooks, perf regression, RAG
    # faithfulness). Ratcheted 65 → 61 → 59 → 57 → 55 on 2026-04-15
    # after Phase E-1 converted feature_flags.py,
    # llm_cost_budget.upsert_quota, compliance_pack_service.generate_pack,
    # and github_checks_service.upsert_integration to stage-only.
    # Re-raised 55 → 56 later the same day to accommodate
    # webhook_service.replay_delivery, which must own its own session
    # for the same enqueue-after-commit reason as emit_event. Ratchet
    # back down further as the remaining services migrate.
    # Ratcheted 56 → 55 on 2026-05-16 after eval_gate_service was
    # removed from the allowlist (P1-2 audit fix: persist_agent_stack_gate_run
    # and set_baseline_from_eval are router-only, no Celery worker calls
    # them, so they flush and let get_db commit).
    # Ratcheted 55 → 50 on 2026-05-16 (later same day) after the webhook
    # P1 follow-up cleanup: subscription CRUD converted to stage-only
    # (14 → 9 commits remaining; the 9th is _audit's fresh-session
    # commit that ``_count_commits`` matches by regex shape).
    # Raised 50 → 51 on 2026-06-01: the webhook-service SSRF review added a
    # delivery-time "blocked unsafe target → mark FAILED" branch to
    # ``deliver`` that commits on its own worker session (9 → 10 for
    # webhook_service.py), mirroring the other deliver outcome branches.
    # Raised 51 → 52 on 2026-06-02: the github-checks SSRF review added the
    # same delivery-time blocked-target branch to ``post_check_run_for_run``
    # (3 → 4 for github_checks_service.py), on its own worker session.
    # Raised 52 → 53 on 2026-07-09: PMF US-7.1 added the transition
    # notification engine (notification_transitions.py), whose single commit
    # is Celery-task-owned — the state-store advance must be durable before
    # dispatch so re-finalization stays idempotent.
    # Raised 53 → 55 on 2026-07-09 (PMF US-5.4/US-5.5): the quarantine
    # lifecycle added two beat/task-owned commits to
    # flaky_quarantine_service.py (9 → 11) — the staleness sweep and the
    # run-finalization stability tracker, each on its own AsyncSessionLocal
    # because no request session exists at either call site, and each must
    # be durable BEFORE its notification dispatch (once-only anchors).
    # Raised 55 → 56 on 2026-07-10 (PMF US-7.3): ownership-routed
    # notifications added notification_routing.py, whose single commit is
    # Celery-task-owned — record_team_delivery_logs persists the
    # NotificationLog audit rows for team-channel deliveries from inside
    # the dispatch_transition_notifications worker task.
    # Raised 56 → 57 on 2026-07-15 (Agentic AI-F2): prompt_registry.py's
    # single commit is CLI-owned — the `--attest` entry point persists the
    # eval-gate run on its own AsyncSessionLocal (no request handler exists).
    # Raised 57 → 58 on 2026-07-15 (Agentic AI-1): agent_investigation_
    # service.py's single commit is hook-owned — maybe_auto_trigger runs
    # inside Celery-owned host paths (transition engine / gate NO_GO) on its
    # own AsyncSessionLocal and must commit the investigation row before the
    # worker task is enqueued.
    # Raised 58 → 63 on 2026-07-16: PMF Epic 3 (US-3.2) added
    # gitlab_integration_service.py, whose 5 commits are all Celery-worker-
    # owned (MR-note _record_outcome bookkeeping + the four commit-status
    # delivery-outcome branches on their own AsyncSessionLocal) — the exact
    # mirror of github_checks_service / github_pr_comment_service.
    # Lowered 63 → 59 on 2026-08-01 (post-merge review of PR #394): the
    # commit-status path was restructured to the gather-context/close-
    # session/HTTP/_record_outcome shape, collapsing its four delivery-
    # outcome commits into the single _record_outcome commit (cap 5 → 1).
    # Raised 59 -> 60 for the Phase 2 immutable evidence capture authority,
    # 60 -> 61 for the Phase 3 cluster-child transactional outbox, and
    # 61 -> 71 for the durable action-ledger and report-supersession
    # workers added in the Phase 3 decision/action slices.
    assert total <= 71, (
        f"COMMIT_ALLOWLIST sums to {total} allowed commits — lower the caps "
        "or remove entries instead of raising this limit."
    )
