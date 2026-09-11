"""Fixer run orchestrator (Agentic plan AI-2).

``run_fixer_run`` executes one budgeted fixer run end-to-end:

    select candidates → for each (within budgets, honoring the kill switch):
        diagnose → generate (test-code-only) → glob-reject → validate → PR

Invariants enforced here:

* **Validated-only PRs.** A PR is opened ONLY from a ``validated`` result in
  suggest mode. ``error`` (infra) and ``failed`` (fix didn't validate) — and
  the ``NoRunner`` default — NEVER open a PR. Shadow mode never opens a PR.
* **Test-code-only.** A generated diff touching any non-test file is
  ``rejected_globs`` BEFORE any execution.
* **Budgets + kill switch.** ``max_tests_per_run`` / ``max_attempts_per_test``
  cap work; ``enabled=false`` mid-run stops cooperatively between candidates.

Every terminal run writes one ``agent_runs`` ledger entry (AI-3). This module
is a support module (no ``BaseAgent`` implementation) — it emits its audit via
the ledger + ``fix_attempts`` rows.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import structlog
from sqlalchemy import func, select, update

from app.agents.fixer import pipeline
from app.agents.fixer.runners import ValidationRunner, build_runner, redact_secrets
from app.agents.fixer.state import (
    RESULT_ERROR,
    RESULT_FAILED,
    RESULT_VALIDATED,
    STATUS_DIAGNOSING,
    STATUS_ERROR,
    STATUS_FAILED_VALIDATION,
    STATUS_GENERATING,
    STATUS_PR_OPENED,
    STATUS_REJECTED_GLOBS,
    STATUS_SELECTED,
    STATUS_SKIPPED_BUDGET,
    STATUS_VALIDATED,
    STATUS_VALIDATING,
    FixCandidate,
    TestIdentity,
    ValidationSpec,
)
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import FixAttempt
from app.services import fixer_service
from app.services.llm_cost_reservation import cost_budget_scope

logger = structlog.get_logger("agents.fixer.workflow")

GenerateFn = Callable[..., Awaitable[dict[str, Any]]]


class TerminalDecision:
    """Pure result of mapping a validation outcome to a terminal attempt state.

    ``open_pr`` is True ONLY for a validated fix in suggest mode with an open-PR
    slot AND a usable GitHub integration. This encodes the load-bearing
    invariant: ``error`` (infra), ``failed`` (fix didn't validate), shadow
    mode, and the NoRunner default NEVER open a PR.
    """

    __slots__ = ("status", "open_pr", "reason")

    def __init__(self, status: str, open_pr: bool, reason: str) -> None:
        self.status = status
        self.open_pr = open_pr
        self.reason = reason


def classify_validation_terminal(
    mode: str,
    result_status: str,
    *,
    passed: int,
    reruns: int,
    open_pr_slots: int,
    github_available: bool,
) -> TerminalDecision:
    """Pure decision: (mode, validation result) → terminal status + whether to
    open a PR. Unit-tested directly (the validated-only-PR invariant)."""
    if result_status == RESULT_ERROR:
        return TerminalDecision(STATUS_ERROR, False, "validation infra error — fix merit unknown")
    if result_status == RESULT_FAILED:
        return TerminalDecision(
            STATUS_FAILED_VALIDATION, False,
            f"fix did not validate ({passed}/{reruns} reruns passed)",
        )
    if result_status != RESULT_VALIDATED:
        # Defensive: unknown status is treated as infra error — never a PR.
        return TerminalDecision(STATUS_ERROR, False, f"unknown validation status {result_status!r}")
    if mode != "suggest":
        return TerminalDecision(STATUS_VALIDATED, False, "validated (shadow mode — no PR opened)")
    if open_pr_slots <= 0:
        return TerminalDecision(
            STATUS_VALIDATED, False,
            "validated; PR not opened — max_concurrent_open_prs reached",
        )
    if not github_available:
        return TerminalDecision(
            STATUS_VALIDATED, False,
            "validated; PR not opened — GitHub integration unavailable/offline",
        )
    return TerminalDecision(STATUS_VALIDATED, True, "validated — opening draft PR")


async def _kill_switch_tripped(project_id: uuid.UUID, db=None) -> bool:
    """Re-read the fixer policy's enabled flag (cooperative cancel). Reuses
    the caller's session when given (the candidate loop passes its own —
    no dedicated session per check). A read fault FAILS CLOSED (stops the
    run): a kill switch we cannot read must be assumed thrown."""
    try:
        if db is not None:
            row = await fixer_service.get_fixer_policy_row(db, project_id)
        else:
            async with AsyncSessionLocal() as own_db:
                row = await fixer_service.get_fixer_policy_row(own_db, project_id)
        return not fixer_service.serialize_fixer_config(row)["enabled"]
    except Exception as exc:  # noqa: BLE001 — fail closed
        logger.warning("fixer_kill_switch_read_failed", error=str(exc))
        return True


def _clone_host(api_base_url: Optional[str]) -> str:
    """HTML/clone host for the integration's API base (mirrors
    commit_attribution_service._commit_html_url): github.com only when the
    API base IS api.github.com; GitHub Enterprise strips the /api/v3 suffix."""
    base = (api_base_url or "").rstrip("/")
    if not base or "api.github.com" in base:
        return "https://github.com"
    host = base
    for suffix in ("/api/v3", "/api"):
        if host.endswith(suffix):
            host = host[: -len(suffix)]
            break
    return host.rstrip("/")


async def _github_ctx(project_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """Load the project's GitHub integration + PAT for PR opening / dispatch.
    None when offline / not configured. Also resolves the repo's real default
    branch (AFTER the DB session closes — it's an HTTP call) so validation
    never hardcodes "main"."""
    from app.core.config import settings

    if settings.AI_OFFLINE_MODE:
        return None
    try:
        async with AsyncSessionLocal() as db:
            from app.services import secret_service
            from app.services.github_checks_service import (
                SECRET_SCOPE,
                _secret_key,
                get_integration,
            )

            integration = await get_integration(db, project_id)
            if integration is None or not integration.enabled:
                return None
            pat = await secret_service.read_secret(db, SECRET_SCOPE, _secret_key(project_id))
            if not pat:
                return None
            api_base_url = integration.api_base_url
            repo_owner = integration.repo_owner
            repo_name = integration.repo_name
        # Session is closed — the default-branch lookup is an outbound HTTP
        # call and must not ride inside a DB transaction.
        default_branch = await pipeline.fetch_default_branch(
            api_base_url=api_base_url, repo_owner=repo_owner, repo_name=repo_name, pat=pat,
        )
        return {
            "api_base_url": api_base_url,
            "repo_owner": repo_owner,
            "repo_name": repo_name,
            "pat": pat,
            "repo_url": f"{_clone_host(api_base_url)}/{repo_owner}/{repo_name}.git",
            "default_branch": default_branch,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("fixer_github_ctx_failed", error=str(exc))
        return None


async def _count_open_prs(db, project_id: uuid.UUID) -> int:
    return int((
        await db.execute(
            select(func.count(FixAttempt.id)).where(
                FixAttempt.project_id == project_id,
                FixAttempt.pr_state == "open",
            )
        )
    ).scalar() or 0)


async def _new_attempt(
    db, *, project_id, fixer_run_id, candidate: FixCandidate, runner_type: str,
) -> FixAttempt:
    row = FixAttempt(
        project_id=project_id,
        fixer_run_id=fixer_run_id,
        test_fingerprint=candidate.test_fingerprint,
        test_name=candidate.test_name,
        status=STATUS_SELECTED,
        attempt_no=candidate.prior_attempts + 1,
        runner_type=runner_type,
    )
    db.add(row)
    await db.flush()
    return row


async def _advance(db, row: FixAttempt, status: str, *, terminal: bool = False, **fields: Any) -> None:
    row.status = status
    for k, v in fields.items():
        setattr(row, k, v)
    if terminal:
        row.completed_at = datetime.now(timezone.utc)
    await db.commit()


async def run_fixer_run(
    project_id: str,
    fixer_run_id: str,
    triggered_by: str = "scheduled",
    *,
    runner_override: Optional[ValidationRunner] = None,
    generate_fn: Optional[GenerateFn] = None,
) -> dict[str, Any]:
    """Execute one fixer run. Celery task entry point (``run_fixer_run_task``).
    ``runner_override`` / ``generate_fn`` are test seams."""
    pid = uuid.UUID(project_id)
    frid = uuid.UUID(fixer_run_id)
    generate = generate_fn or pipeline.generate_candidate_patch
    started = time.monotonic()

    async with AsyncSessionLocal() as db:
        config = await fixer_service.get_effective_config(db, pid)
    mode = config["mode"]
    runner_cfg = config["runner"]
    test_globs = config["test_globs"]
    budgets = config["budgets"]
    reruns = int(budgets["validation_reruns"])
    max_tests = int(budgets["max_tests_per_run"])
    max_attempts = int(budgets["max_attempts_per_test"])
    max_open_prs = int(budgets["max_concurrent_open_prs"])

    counters = {
        "selected": 0, "validated": 0, "failed_validation": 0,
        "rejected_globs": 0, "pr_opened": 0, "error": 0, "skipped_budget": 0,
    }
    actions_proposed: list[str] = []
    actions_taken: list[str] = []
    total_tokens = 0

    if not config["enabled"]:
        return await _finalize(
            db_pid=pid, fixer_run_id=frid, mode=mode, trigger=triggered_by,
            status="skipped", summary="fixer disabled — nothing run",
            counters=counters, actions_proposed=[], actions_taken=[],
            tokens=0, started=started,
        )

    ghctx = await _github_ctx(pid)
    runner = runner_override or build_runner(runner_cfg, github_ctx=ghctx)

    async with AsyncSessionLocal() as db:
        candidates = await pipeline.select_candidates(db, pid, max_tests=max_tests)
    candidates = candidates[:max_tests]

    logger.info(
        "fixer_run_started", project_id=project_id, fixer_run_id=fixer_run_id,
        mode=mode, runner=runner.type, candidates=len(candidates),
    )

    # The whole candidate loop is guarded so _finalize ALWAYS runs — a crash
    # mid-run must still write the ledger entry (status="error") and release
    # the dispatch lock.
    run_error: Optional[str] = None
    try:
        for candidate in candidates:
            async with AsyncSessionLocal() as db:
                # Kill switch — cooperative stop between candidates (reuses
                # this candidate's session; no dedicated session per check).
                if await _kill_switch_tripped(pid, db):
                    row = await _new_attempt(
                        db, project_id=pid, fixer_run_id=frid, candidate=candidate, runner_type=runner.type,
                    )
                    await _advance(
                        db, row, STATUS_SKIPPED_BUDGET, terminal=True,
                        reason="run stopped mid-flight — fixer disabled (kill switch)",
                    )
                    counters["skipped_budget"] += 1
                    logger.info("fixer_kill_switch_stop", fixer_run_id=fixer_run_id)
                    break

                # Per-test attempt budget.
                if candidate.prior_attempts >= max_attempts:
                    row = await _new_attempt(
                        db, project_id=pid, fixer_run_id=frid, candidate=candidate, runner_type=runner.type,
                    )
                    await _advance(
                        db, row, STATUS_SKIPPED_BUDGET, terminal=True,
                        reason=f"max_attempts_per_test ({max_attempts}) reached for this test",
                    )
                    counters["skipped_budget"] += 1
                    continue

                row = await _new_attempt(
                    db, project_id=pid, fixer_run_id=frid, candidate=candidate, runner_type=runner.type,
                )
                counters["selected"] += 1
                attempt_id = row.id

                # Diagnose (reuse existing signals — never rerun the investigator).
                await _advance(db, row, STATUS_DIAGNOSING)
                diagnosis = await pipeline.gather_diagnosis(db, pid, candidate)
                await _advance(db, row, STATUS_GENERATING)

            # Generate with NO session open — the LLM call can take minutes
            # and must never ride inside a DB transaction (mirrors the
            # validation pattern below).
            # Re-audit R-B45-1: the patch generation reserves against the
            # project's monthly LLM cap.
            with cost_budget_scope(project_id):
                gen = await generate(
                    candidate=candidate, diagnosis=diagnosis, test_source="", budget=budgets,
                )
            total_tokens += int(gen.get("tokens") or 0)
            patch = gen.get("patch")

            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(select(FixAttempt).where(FixAttempt.id == attempt_id))
                ).scalar_one_or_none()
                if row is None:
                    logger.warning("fixer_attempt_row_missing", attempt_id=str(attempt_id))
                    continue
                if not gen.get("can_fix") or not patch:
                    await _advance(
                        db, row, STATUS_ERROR, terminal=True,
                        reason=str(gen.get("reasoning") or "no fix generated")[:2000],
                    )
                    counters["error"] += 1
                    continue

                # Glob rejection — structural, BEFORE any execution.
                ok, offending = pipeline.patch_touches_only_test_globs(patch, test_globs)
                if not ok:
                    await _advance(
                        db, row, STATUS_REJECTED_GLOBS, terminal=True,
                        patch=patch,
                        patch_summary=pipeline.summarize_patch(patch),
                        reason=(
                            "patch touches non-test files: " + ", ".join(offending[:5])
                            if offending else "patch touched no files"
                        ),
                    )
                    counters["rejected_globs"] += 1
                    continue

                patch_summary = pipeline.summarize_patch(patch)
                reasoning = str(gen.get("reasoning") or "")
                actions_proposed.append(f"fix {candidate.test_name or candidate.test_fingerprint}")
                await _advance(db, row, STATUS_VALIDATING, patch=patch, patch_summary=patch_summary)

            # Validate (runner call OUTSIDE the DB session — it can be slow).
            spec = _build_spec(candidate, patch, runner_cfg, reruns, ghctx)
            result = await runner.run_validation(spec)
            egress = bool(spec.allow_network_egress)
            log_digest = result.runs[-1].log_digest if result.runs else None

            open_pr_payload: Optional[dict[str, Any]] = None
            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(select(FixAttempt).where(FixAttempt.id == attempt_id))
                ).scalar_one_or_none()
                if row is None:
                    logger.warning("fixer_attempt_row_missing", attempt_id=str(attempt_id))
                    continue

                # Record the validation tally (even for failed — it's honest data).
                if result.runs:
                    row.validation_reruns = result.reruns
                    row.validation_passed = result.passed_count
                row.runner_log_digest = log_digest
                row.egress_opened = egress

                open_prs = await _count_open_prs(db, pid)
                decision = classify_validation_terminal(
                    mode, result.status,
                    passed=result.passed_count, reruns=result.reruns,
                    open_pr_slots=max_open_prs - open_prs,
                    github_available=ghctx is not None,
                )

                if decision.status == STATUS_ERROR:
                    # Defensive redaction: the runner already scrubs its own
                    # output, but nothing token-shaped may reach the stored
                    # (API-readable) reason regardless of which runner ran.
                    err = redact_secrets(str(result.error or ""), (ghctx or {}).get("pat"))
                    await _advance(
                        db, row, STATUS_ERROR, terminal=True,
                        reason=(decision.reason + f": {err}" if err else decision.reason)[:2000],
                    )
                    counters["error"] += 1
                    continue
                if decision.status == STATUS_FAILED_VALIDATION:
                    await _advance(db, row, STATUS_FAILED_VALIDATION, terminal=True, reason=decision.reason)
                    counters["failed_validation"] += 1
                    continue

                # decision.status == STATUS_VALIDATED — a real, validated fix.
                counters["validated"] += 1
                if not decision.open_pr:
                    await _advance(db, row, STATUS_VALIDATED, terminal=True, reason=decision.reason)
                    continue

                # Commit the validated state BEFORE the PR HTTP calls — the
                # GitHub round-trips must not ride inside a DB transaction.
                await _advance(db, row, STATUS_VALIDATED, reason=decision.reason)
                open_pr_payload = {
                    "validation": {"reruns": result.reruns, "passed": result.passed_count},
                    "reasoning": reasoning,
                }

            if open_pr_payload is None:
                continue

            # Suggest mode + validated + slot + integration → open a DRAFT PR
            # (HTTP, with NO session open).
            pr = await pipeline.open_draft_pr(
                api_base_url=ghctx["api_base_url"],
                repo_owner=ghctx["repo_owner"],
                repo_name=ghctx["repo_name"],
                pat=ghctx["pat"],
                candidate=candidate,
                patch=patch,
                validation=open_pr_payload["validation"],
                reasoning=open_pr_payload["reasoning"],
                ledger_deep_link=f"/fixer/runs/{fixer_run_id}",
            )

            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(select(FixAttempt).where(FixAttempt.id == attempt_id))
                ).scalar_one_or_none()
                if row is None:
                    logger.warning("fixer_attempt_row_missing", attempt_id=str(attempt_id))
                    continue
                if pr.get("pr_url"):
                    await _advance(
                        db, row, STATUS_PR_OPENED, terminal=True,
                        pr_url=pr["pr_url"], pr_number=pr.get("pr_number"), pr_state="open",
                        reason="validated — draft PR opened",
                    )
                    counters["pr_opened"] += 1
                    actions_taken.append(str(pr["pr_url"]))
                else:
                    pr_err = redact_secrets(
                        str(pr.get("error") or pr.get("skipped") or "unknown"),
                        (ghctx or {}).get("pat"),
                    )
                    await _advance(
                        db, row, STATUS_VALIDATED, terminal=True,
                        reason=f"validated; PR open failed: {pr_err}",
                    )
    except Exception as exc:  # noqa: BLE001 — _finalize must still run
        run_error = f"{type(exc).__name__}: {exc}"
        logger.error("fixer_run_crashed", fixer_run_id=fixer_run_id, error=run_error)

    status = "error" if run_error else "completed"
    summary = _run_summary(mode, runner.type, counters)
    if run_error:
        summary += f" Run aborted by an unexpected error: {run_error[:300]}"
    return await _finalize(
        db_pid=pid, fixer_run_id=frid, mode=mode, trigger=triggered_by,
        status=status, summary=summary, counters=counters,
        actions_proposed=actions_proposed, actions_taken=actions_taken,
        tokens=total_tokens, started=started,
    )


def _build_spec(
    candidate: FixCandidate, patch: str, runner_cfg: dict[str, Any], reruns: int,
    ghctx: Optional[dict[str, Any]],
) -> ValidationSpec:
    identity = TestIdentity(
        framework="pytest",
        command_template=str(runner_cfg.get("command_template") or "pytest -x {test_selector}"),
        test_selector=candidate.test_name or candidate.test_fingerprint,
    )
    repo_url = (ghctx or {}).get("repo_url") or ""
    return ValidationSpec(
        repo_url=repo_url,
        # The repo's REAL default branch (resolved in _github_ctx); "main"
        # only when it could not be determined.
        ref=(ghctx or {}).get("default_branch") or "main",
        patch=patch,
        test_identity=identity,
        reruns=reruns,
        runner_image=runner_cfg.get("runner_image"),
        workflow_ref=runner_cfg.get("workflow_ref"),
        clone_token=(ghctx or {}).get("pat"),
    )


def _run_summary(mode: str, runner_type: str, counters: dict[str, int]) -> str:
    return (
        f"Fixer run ({mode}, runner={runner_type}): "
        f"selected {counters['selected']}, validated {counters['validated']}, "
        f"PRs {counters['pr_opened']}, failed {counters['failed_validation']}, "
        f"rejected_globs {counters['rejected_globs']}, error {counters['error']}, "
        f"skipped {counters['skipped_budget']}."
    )


async def _finalize(
    *, db_pid: uuid.UUID, fixer_run_id: uuid.UUID, mode: str, trigger: str,
    status: str, summary: str, counters: dict[str, int],
    actions_proposed: list[str], actions_taken: list[str], tokens: int, started: float,
) -> dict[str, Any]:
    duration_ms = int((time.monotonic() - started) * 1000)
    digest = _registry_digest()
    async with AsyncSessionLocal() as db:
        ledger = await fixer_service.write_fixer_ledger(
            db,
            project_id=db_pid,
            fixer_run_id=fixer_run_id,
            mode=mode,
            trigger=trigger,
            status=status,
            summary=summary,
            actions_proposed=actions_proposed,
            actions_taken=actions_taken,
            tokens=tokens,
            duration_ms=duration_ms,
            prompt_registry_digest=digest,
        )
        ledger_id = ledger.id
        # Stamp the ledger deep-link id onto this run's attempts — one UPDATE,
        # not a per-row load/mutate loop.
        await db.execute(
            update(FixAttempt)
            .where(
                FixAttempt.fixer_run_id == fixer_run_id,
                FixAttempt.ledger_run_id.is_(None),
            )
            .values(ledger_run_id=ledger_id)
        )
        await db.commit()
    # Release the cross-process dispatch lock (taken at gate time) so the
    # next scheduled/manual run may start.
    await fixer_service.release_fixer_run_lock(db_pid)
    logger.info(
        "fixer_run_finished", fixer_run_id=str(fixer_run_id), status=status, **counters,
    )
    return {"fixer_run_id": str(fixer_run_id), "status": status, "counters": counters,
            "actions_taken": actions_taken}


def _registry_digest() -> Optional[str]:
    try:
        from app.services.prompt_registry import registry_digest

        return registry_digest()[:12]
    except Exception:  # pragma: no cover
        return None
