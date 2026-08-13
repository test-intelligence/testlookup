"""Tenant-scoped change-correlation and ownership specialist.

This stage deliberately stays deterministic: it composes the existing baseline
diff and ownership resolver services, then emits a bounded structural payload
for the terminal decision report.  No model call or caller-supplied evidence is
accepted here; the run, project, failed tests, and clusters are re-resolved
from PostgreSQL before any result is produced.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.postgres import AsyncSessionLocal
from app.models.agent_contracts import ChangeOwnershipAgentOutput, validate_agent_contract
from app.models.postgres import FailureCluster, TestCase, TestRun
from app.services.evidence_sanitizer import sanitize_reference_text
from app.services.ownership_resolver_service import resolve_cluster_ownership
from app.services.regression_diff_service import compute_regression_diff

_MAX_CLUSTERS = 20
_MAX_MEMBERS = 50


def _safe(value: Any, limit: int = 240) -> str:
    return sanitize_reference_text(str(value or ""), limit=limit)[0]


def _public_diff(diff: dict[str, Any]) -> dict[str, Any]:
    """Keep only bounded, non-secret change facts in the report contract."""
    return {
        "baseline_available": bool(diff.get("baseline_available")),
        "baseline_run_id": _safe(diff.get("baseline_run_id"), 64) or None,
        "baseline_build_number": _safe(diff.get("baseline_build_number"), 120) or None,
        "build_number": _safe(diff.get("build_number"), 120) or None,
        "pass_rate": diff.get("pass_rate"),
        "baseline_pass_rate": diff.get("baseline_pass_rate"),
        "pass_rate_delta": diff.get("pass_rate_delta"),
        "new_failing_count": int(diff.get("new_failing_count") or 0),
        "resolved_count": int(diff.get("resolved_count") or 0),
        "commit_count": int(diff.get("commit_count") or 0),
        "classification": _safe(diff.get("regression_classification"), 64) or None,
        "selection_reason": "latest_passing" if diff.get("baseline_available") else "no_baseline",
    }


class ChangeOwnershipAgent(BaseAgent):
    """Compose authoritative baseline and cluster ownership evidence."""

    stage_name = "change_ownership"

    async def _record_scope_decision(self, pipeline_run_id: str, chosen: str, rationale: str) -> None:
        if not pipeline_run_id:
            return
        try:
            await self.log_decision(
                pipeline_run_id,
                decision_point="change_ownership_scope",
                chosen=chosen,
                rationale=rationale,
            )
        except Exception:  # pragma: no cover - observability must not change authority
            self.logger.debug("change_ownership_decision_log_failed")

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        project_id = str(state.get("project_id") or "")
        run_id = str(state.get("test_run_id") or "")
        pipeline_run_id = str(state.get("pipeline_run_id") or "")
        await self._record_scope_decision(
            pipeline_run_id,
            "evaluate" if project_id and run_id else "not_enough_evidence",
            "authoritative project and run scope are required before change/ownership evaluation",
        )
        if not project_id or not run_id:
            return self._result("not_enough_evidence", "Project and run scope are required.")
        try:
            project_uuid = uuid.UUID(project_id)
            run_uuid = uuid.UUID(run_id)
        except (ValueError, TypeError):
            return self._result("failed", "invalid_scope")

        try:
            async with AsyncSessionLocal() as db:
                run = (
                    await db.execute(
                        select(TestRun).where(
                            TestRun.id == run_uuid,
                            TestRun.project_id == project_uuid,
                        )
                    )
                ).scalar_one_or_none()
                if run is None:
                    return self._result("failed", "run_scope_not_found")

                diff = await compute_regression_diff(run, db)
                ownership: list[dict[str, Any]] = []
                clusters = (
                    await db.execute(
                        select(FailureCluster)
                        .where(FailureCluster.test_run_id == run_uuid)
                        .order_by(FailureCluster.size.desc(), FailureCluster.cluster_id)
                        .limit(_MAX_CLUSTERS)
                    )
                ).scalars().all()
                for cluster in clusters:
                    member_ids = list(dict.fromkeys(str(item) for item in (cluster.member_test_ids or [])))[:_MAX_MEMBERS]
                    if not member_ids:
                        continue
                    try:
                        member_uuids = [uuid.UUID(item) for item in member_ids]
                    except ValueError:
                        return self._result("failed", "cluster_member_authority_invalid")
                    members = (
                        await db.execute(
                            select(TestCase.id).where(
                                TestCase.test_run_id == run_uuid,
                                TestCase.id.in_(member_uuids),
                                TestCase.status.in_(("FAILED", "BROKEN")),
                            )
                        )
                    ).scalars().all()
                    if {str(item) for item in members} != set(member_ids):
                        return self._result("failed", "cluster_member_authority_invalid")
                    resolved = await resolve_cluster_ownership(db, project_uuid, member_ids)
                    payload = resolved.to_dict()
                    ownership.append({
                        "cluster_id": _safe(cluster.cluster_id, 20),
                        "member_count": len(member_ids),
                        "service_name": _safe(payload.get("service_name"), 120) or None,
                        "team_name": _safe(payload.get("team_name"), 120) or None,
                        "confidence": _safe(payload.get("confidence"), 16) or "none",
                        "match_source": _safe(payload.get("match_source"), 40) or None,
                        "fallback_reason": _safe(payload.get("fallback_reason"), 240) or None,
                    })

            status = "complete" if diff.get("baseline_available") or ownership else "not_enough_evidence"
            return self._result(
                status,
                "change_and_ownership_evaluated" if status == "complete" else "no_baseline_or_ownership_evidence",
                baseline_diff=_public_diff(diff),
                ownership_resolutions=ownership,
            )
        except Exception as exc:  # fail closed; never persist exception text
            return self._result("failed", type(exc).__name__)

    def _result(
        self,
        status: str,
        reason: str,
        *,
        baseline_diff: dict[str, Any] | None = None,
        ownership_resolutions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        # Every return path funnels through here, so this is the single place
        # the analytic contract is stamped (agents.contract-metadata ratchet).
        return validate_agent_contract(
            ChangeOwnershipAgentOutput,
            {
                "status": status,
                "baseline_diff": baseline_diff or {},
                "ownership_resolutions": ownership_resolutions or [],
                "summary": reason[:240],
            },
            agent_name=self.stage_name,
            confidence=90 if status == "complete" else 0,
            fallback_used=status != "complete",
            decision_reason=reason[:240],
        )
