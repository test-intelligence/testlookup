"""
Defect Triage Agent — Stage 5 of the offline pipeline.

For each analysed failure with confidence >= threshold:
  1. Deduplicates against existing open defects using atomic DB operations
  2. Creates a new ticket (or skips if one already exists)
  3. Updates the Defect table

Concurrency-safe: uses SELECT ... FOR UPDATE SKIP LOCKED to prevent
duplicate defect creation from parallel pipeline executions.
"""
import structlog

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents.base import BaseAgent
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.agent_contracts import DefectTriageAgentOutput, validate_agent_contract
from app.models.postgres import Defect, TestCase
from app.services.action_policy import (
    ActionStatus,
    check_jira_ticket_creation_policy,
)
from app.services.jira_client import create_jira_issue
from app.services.pipeline_lease import LeaseLost
from app.services.tool_call_idempotency import JIRA_TICKET_TOOL, prior_tool_calls, run_once

logger = structlog.get_logger("agents.triage")

# Only auto-triage when confidence is high enough
_AUTO_TRIAGE_CONFIDENCE = settings.AI_CONFIDENCE_THRESHOLD
# Categories that always get a ticket (even at lower confidence)
_HIGH_PRIORITY_CATEGORIES = {"PRODUCT_BUG", "INFRASTRUCTURE"}


def _outcome_unknown_result(tc_id: object) -> dict:
    """A ticket call an earlier attempt started and never recorded (E7.6)."""
    return {
        "test_case_id": tc_id,
        "action": "jira_outcome_unknown",
        "reason": (
            "An earlier attempt started filing this ticket and never recorded the "
            "result, so it may already exist. Not filing again; verify in Jira."
        ),
    }


class DefectTriageAgent(BaseAgent):
    stage_name = "triage"

    async def run(self, state: dict) -> dict:
        pipeline_run_id = state["pipeline_run_id"]
        project_id = state["project_id"]
        analyses = state.get("analyses") or {}
        test_run_data = state.get("test_run_data") or {}

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(
            project_id,
            {"status": "running", "message": "Triaging defects and creating tickets..."},
        )

        triage_results: list[dict] = []
        errors: list[str] = []

        # E7.6 (section 7.5): read the ledger before planning any work. A subject
        # whose ticket call is still ``executing`` was started by an attempt that
        # never recorded how it ended -- it may already have a ticket -- so it is
        # taken off the work list rather than filed again. ``executed`` subjects
        # stay on it: their call replays from the ledger without reaching Jira,
        # which also repairs a defect row the crashed attempt never linked.
        # Gated by the same flag as the call itself; a failed read falls back to
        # the per-call guard, which enforces the same rule.
        prior_calls: dict = {}
        if settings.JIRA_ENABLED and analyses:
            try:
                prior_calls = await prior_tool_calls(
                    project_id=project_id,
                    tool=JIRA_TICKET_TOOL,
                    scope_id=state.get("test_run_id"),
                    subject_ids=list(analyses),
                )
            except Exception as lookup_exc:  # noqa: BLE001
                logger.warning(
                    "triage_prior_tool_calls_unavailable",
                    error_type=type(lookup_exc).__name__,
                )

        for tc_id, analysis in analyses.items():
            prior = prior_calls.get(str(tc_id))
            if prior is not None and prior.status == "executing":
                triage_results.append(_outcome_unknown_result(tc_id))
                continue
            confidence = analysis.get("confidence_score", 0)
            category = analysis.get("failure_category", "UNKNOWN")

            # Determine if this failure warrants a ticket
            should_triage = (
                (confidence >= _AUTO_TRIAGE_CONFIDENCE)
                or (category in _HIGH_PRIORITY_CATEGORIES and confidence >= 50)
            ) and not analysis.get("is_flaky", False)

            if not should_triage:
                triage_results.append({
                    "test_case_id": tc_id,
                    "action": "skipped",
                    "reason": f"confidence={confidence} / category={category} / flaky={analysis.get('is_flaky')}",
                })
                continue

            try:
                result = await self._triage_one(tc_id, analysis, project_id, test_run_data, state)
                triage_results.append(result)
            except LeaseLost:
                # The row was reclaimed: stop, never carry on writing under a
                # lease another attempt now holds.
                raise
            except Exception as exc:
                err = f"Triage failed for {tc_id}: {exc}"
                logger.error("Triage failed", test_case_id=tc_id, error=str(exc), exc_info=True)
                # A failure that passed the gate but got no ticket is invisible
                # in the counts -- it is neither "created" nor "skipped by rule".
                await self.log_decision(
                    pipeline_run_id,
                    decision_point="triage_ticket",
                    chosen="error",
                    rationale="failure met the triage gate but ticket creation failed",
                    test_case_id=str(tc_id),
                    context={"error_type": type(exc).__name__, "category": category},
                )
                errors.append(err)
                triage_results.append({"test_case_id": tc_id, "action": "error", "error": str(exc)})

        created = sum(1 for r in triage_results if r["action"] == "created")
        skipped = sum(1 for r in triage_results if r["action"] == "skipped")

        # Ticket creation is a mutating, externally-visible action, and the gate
        # that decides it is a threshold nobody can see from the result. Record
        # the rule alongside the counts so a "why was nothing filed?" question
        # is answerable from the trail rather than from the source.
        await self.log_decision(
            pipeline_run_id,
            decision_point="triage_gate",
            chosen=f"created_{created}",
            rationale=(
                f"{created} ticket(s) created, {skipped} skipped, {len(errors)} error(s) "
                f"across {len(analyses)} analysis/analyses; gate is confidence >= "
                f"{_AUTO_TRIAGE_CONFIDENCE}, or >= 50 for "
                f"{sorted(_HIGH_PRIORITY_CATEGORIES)}, and never for flaky"
            ),
            context={
                "created": created,
                "skipped": skipped,
                "errors": len(errors),
                "confidence_threshold": _AUTO_TRIAGE_CONFIDENCE,
            },
        )

        await self.mark_stage_done(
            pipeline_run_id,
            result_data={"created": created, "skipped": skipped, "errors": len(errors)},
        )
        await self.broadcast_progress(
            project_id,
            {
                "status": "completed",
                "message": f"Triage complete: {created} ticket(s) created, {skipped} skipped",
            },
        )

        return validate_agent_contract(
            DefectTriageAgentOutput,
            {
                "triage_results": triage_results,
                "completed_stages": ["triage"],
                "errors": errors,
                "current_stage": "done",
            },
            agent_name=self.stage_name,
            fallback_used=bool(errors),
            confidence=0 if errors else 100,
            evidence_refs=[
                {
                    "type": "triage_result",
                    "id": result.get("test_case_id", "unknown"),
                    "action": result.get("action", "unknown"),
                }
                for result in triage_results
            ],
            decision_reason=(
                "triage_completed_with_errors"
                if errors else "triage_completed"
            ),
        )

    async def _triage_one(
        self,
        tc_id: str,
        analysis: dict,
        project_id: str,
        test_run_data: dict,
        state: dict,
    ) -> dict:
        """
        Create or skip a defect for one failed test case.

        Uses an atomic INSERT ... ON CONFLICT approach:
        - If an OPEN defect already exists for this test_case_id, skip.
        - Otherwise, atomically insert a new one.
        This prevents duplicate defects from concurrent pipeline runs.
        """
        async with AsyncSessionLocal() as db:
            # Look up test case name
            tc_result = await db.execute(
                select(TestCase.test_name, TestCase.suite_name).where(TestCase.id == tc_id)
            )
            tc = tc_result.first()
            test_name = tc.test_name if tc else tc_id

            # Step 1: Atomically insert defect — DB enforces uniqueness via partial index
            stmt = pg_insert(Defect).values(
                test_case_id=tc_id,
                project_id=project_id,
                ai_confidence_score=analysis.get("confidence_score"),
                failure_category=analysis.get("failure_category", "UNKNOWN"),
                resolution_status="OPEN",
            ).on_conflict_do_nothing(
                index_elements=["test_case_id"],
                index_where=text("resolution_status = 'OPEN' AND test_case_id IS NOT NULL"),
            ).returning(Defect.id)
            result = await db.execute(stmt)
            new_row = result.first()
            await db.commit()

            action = "created"

            # If insert was a no-op, an open defect already exists. Retry Jira
            # creation only when the existing defect still has no ticket.
            if new_row is None:
                existing_result = await db.execute(
                    select(Defect.id, Defect.jira_ticket_id, Defect.jira_ticket_url).where(
                        Defect.test_case_id == tc_id,
                        Defect.resolution_status == "OPEN",
                    )
                )
                existing = existing_result.first()
                if not existing:
                    return {
                        "test_case_id": tc_id,
                        "action": "existing",
                        "reason": "Open defect already tracked",
                    }
                defect_id = existing.id
                if existing.jira_ticket_id:
                    return {
                        "test_case_id": tc_id,
                        "action": "existing",
                        "ticket_key": existing.jira_ticket_id,
                        "ticket_url": existing.jira_ticket_url,
                        "reason": "Open defect already tracked",
                    }
                action = "jira_retry"
            else:
                defect_id = new_row[0]

        # Step 2: Create Jira ticket AFTER successful DB insert, or retry it
        # for an existing open defect that was left ticketless by a prior
        # transient Jira failure.
        ticket_key = ticket_url = ticket_id = None
        jira_error = None
        if settings.JIRA_ENABLED:
            policy_result = await check_jira_ticket_creation_policy(
                project_id=project_id,
                confidence_score=analysis.get("confidence_score"),
                failure_category=analysis.get("failure_category", "UNKNOWN"),
                source="defect_triage_agent",
            )
            if policy_result["initial_status"] == ActionStatus.PENDING_REVIEW:
                await self._mark_defect_pending_review(defect_id, policy_result)
                return {
                    "test_case_id": tc_id,
                    "action": "pending_review",
                    "mutating_action": "jira_ticket_creation",
                    "approval_status": ActionStatus.PENDING_REVIEW.value,
                    "requires_approval": True,
                    "policy_reasons": policy_result["policy_reasons"],
                    "reason": "Jira ticket creation requires approval before execution",
                }
            try:
                jira_project_key = await self._get_jira_key(project_id)
                resolved_project_key = jira_project_key or settings.JIRA_DEFAULT_PROJECT_KEY

                async def _file_ticket() -> dict:
                    # No ``labels=``: create_jira_issue has never accepted one.
                    # The idempotency label this call used to pass raised
                    # TypeError, which the except below logged as "skipped", so
                    # the label never reached Jira. The ledger key is the guard.
                    return await create_jira_issue(
                        project_key=resolved_project_key,
                        test_name=test_name,
                        run_id=state["test_run_id"],
                        ai_summary=analysis.get("root_cause_summary", ""),
                        recommended_action=", ".join(analysis.get("recommended_actions", [])[:2]),
                        stack_trace="",
                        dashboard_link=f"{settings.public_base_url}/runs/{state['test_run_id']}",
                    )

                # E7.6: at most one ticket per (test case, test run), however
                # many times this stage re-runs after a crash.
                outcome = await run_once(
                    project_id=project_id,
                    tool=JIRA_TICKET_TOOL,
                    scope_id=state["test_run_id"],
                    subject_id=tc_id,
                    target_type="test_case",
                    request_payload={
                        "project_key": resolved_project_key,
                        "test_case_id": str(tc_id),
                        "test_run_id": str(state["test_run_id"]),
                    },
                    call=_file_ticket,
                    pipeline_run_id=state.get("pipeline_run_id"),
                    test_run_id=state.get("test_run_id"),
                    fencing_token=state.get("_fencing_token"),
                )
                if outcome.status == "outcome_unknown":
                    return _outcome_unknown_result(tc_id)
                ticket = outcome.result
                ticket_id = ticket.get("ticket_id")
                ticket_key = ticket.get("ticket_key")
                ticket_url = ticket.get("ticket_url")
            except LeaseLost:
                raise
            except Exception as jira_exc:
                jira_error = str(jira_exc)
                logger.warning(
                    "Jira ticket creation skipped",
                    test_case_id=tc_id,
                    error=jira_error,
                )

        # Step 3: Update defect row with Jira info (if ticket was created)
        if ticket_key:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import update
                await db.execute(
                    update(Defect)
                    .where(Defect.id == defect_id)
                    .values(
                        jira_ticket_id=ticket_id,
                        jira_ticket_url=ticket_url,
                        jira_status="Open",
                        approval_status=ActionStatus.EXECUTED.value,
                    )
                )
                await db.commit()

        if jira_error:
            return {
                "test_case_id": tc_id,
                "action": f"{action}_jira_failed",
                "error": jira_error,
                "reason": "Defect remains open without Jira ticket; future runs will retry",
            }

        return {
            "test_case_id": tc_id,
            "action": action,
            "approval_status": (
                ActionStatus.EXECUTED.value if ticket_key else ActionStatus.APPROVED.value
            ),
            "requires_approval": False,
            "ticket_key": ticket_key,
            "ticket_url": ticket_url,
        }

    async def _mark_defect_pending_review(self, defect_id, policy_result: dict) -> None:
        """Persist a policy hold before any external Jira side effect."""
        async with AsyncSessionLocal() as db:
            from sqlalchemy import update

            await db.execute(
                update(Defect)
                .where(Defect.id == defect_id)
                .values(
                    approval_status=ActionStatus.PENDING_REVIEW.value,
                    policy_evaluation=policy_result,
                )
            )
            await db.commit()

    async def _get_jira_key(self, project_id: str) -> str | None:
        from app.models.postgres import Project
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Project.jira_project_key).where(Project.id == project_id))
            row = result.first()
            return row.jira_project_key if row else None
