"""
Defect Commander Agent.

Takes a high-confidence failure cluster and produces a promoted defect with:
  - 7-dimension criticality score (same dimensions as ReleaseRiskAgent)
  - Severity recommendation (CRITICAL / HIGH / MEDIUM / LOW)
  - Component / affected service
  - Probable owner team
  - Jira-ready title, description, and evidence bundle
  - Duplicate detection (checks for open defects with similar root cause)

Exposes:
  POST /api/v1/agents/defect-command
    Body: { cluster_id, test_run_id, project_id, project_key (jira) }
    Returns: promoted defect with Jira ticket reference (if Jira enabled)
"""
import structlog
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    AIAnalysis, Defect, FailureCluster, TestCase,
)
from app.services.criticality_service import get_scoring_model_info, score_cluster
from app.services.defect_promotion_service import (
    _build_evidence_bundle,
    _composite_to_severity,
    _create_jira_ticket,
    _dim_scores_from_analyses,
    _find_duplicate_semantic,
    _generate_jira_content,
)

logger = structlog.get_logger("agents.defect_commander")


class DefectCommander(BaseAgent):
    stage_name = "defect_commander"

    async def run(self, state: dict) -> dict:
        pipeline_run_id = state["pipeline_run_id"]
        project_id = state["project_id"]

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(
            project_id,
            {"status": "running", "message": "Promoting failure cluster to defect…"},
        )

        try:
            result = await self._promote(state)
        except Exception as exc:
            logger.error("DefectCommander failed: %s", exc, exc_info=True)
            await self.mark_stage_done(pipeline_run_id, error=str(exc))
            return {
                "defect_promotion": None,
                "completed_stages": ["defect_commander"],
                "errors": [str(exc)],
            }

        await self.mark_stage_done(
            pipeline_run_id,
            result_data={"defect_id": result.get("defect_id"), "severity": result.get("severity")},
        )
        await self.broadcast_progress(project_id, {
            "status": "completed",
            "defect_severity": result.get("severity"),
        })

        return {
            "defect_promotion": result,
            "completed_stages": ["defect_commander"],
            "errors": [],
        }

    # ── Promotion logic ───────────────────────────────────────────────────────

    async def _promote(self, state: dict) -> dict:
        cluster_id: str = state["cluster_id"]
        test_run_id: str = state["test_run_id"]
        project_id: str = state["project_id"]
        project_key: Optional[str] = state.get("project_key")

        async with AsyncSessionLocal() as db:
            # Load cluster
            cluster_result = await db.execute(
                select(FailureCluster).where(
                    FailureCluster.cluster_id == cluster_id,
                    FailureCluster.test_run_id == test_run_id,
                )
            )
            cluster = cluster_result.scalar_one_or_none()
            if not cluster:
                raise ValueError(f"Cluster {cluster_id} not found for run {test_run_id}")

            member_ids = cluster.member_test_ids or []

            # Load analyses for cluster members
            analyses_result = await db.execute(
                select(AIAnalysis)
                .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
                .where(TestCase.id.in_(member_ids))
                .limit(10)
            )
            analyses = list(analyses_result.scalars().all())

            # Check for duplicate open defects via semantic similarity
            duplicate_id, duplicate_detected = await _find_duplicate_semantic(
                project_id=project_id,
                duplicate_hint=cluster.label,
                db=db,
            )

        # Compute 7-dimension criticality score via criticality_service
        model_info = get_scoring_model_info()
        all_dim_scores = _dim_scores_from_analyses(analyses)
        dim_scores = score_cluster(
            cluster={"member_test_ids": member_ids},
            all_dim_scores=all_dim_scores,
            total_analyses=max(len(member_ids), 1),
            member_count=len(member_ids),
        )
        composite = sum(
            dim_scores.get(d["name"], 0) * d["weight"]
            for d in model_info["dimensions"]
        )
        composite = round(composite, 1)
        severity = _composite_to_severity(composite)

        # LLM: generate Jira-ready defect content
        jira_content = await _generate_jira_content(cluster, analyses)

        # Persist as Defect record
        defect_id = await self._persist_defect(
            test_run_id=test_run_id,
            project_id=project_id,
            cluster=cluster,
            analyses=analyses,
            dim_scores=dim_scores,
            composite=composite,
            severity=severity,
            jira_content=jira_content,
            duplicate_id=duplicate_id,
            duplicate_detected=duplicate_detected,
        )

        # Optionally create Jira ticket
        jira_ticket: Optional[dict] = None
        if project_key and not duplicate_detected:
            jira_ticket_result, _ = await _create_jira_ticket(
                project_key=project_key,
                title=jira_content.get("title", cluster.label),
                description=jira_content.get("description", ""),
                severity=severity,
                labels=jira_content.get("labels", []),
            )
            jira_ticket = jira_ticket_result

        return {
            "defect_id": str(defect_id),
            "cluster_id": cluster_id,
            "severity": severity,
            "criticality_scores": dim_scores,
            "composite_score": composite,
            "title": jira_content.get("title", cluster.label),
            "owner_team": jira_content.get("owner_team"),
            "component": jira_content.get("component"),
            "duplicate_detected": duplicate_detected,
            "duplicate_defect_id": duplicate_id,
            "jira_ticket": jira_ticket,
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    async def _persist_defect(
        self,
        test_run_id: str,
        project_id: str,
        cluster: FailureCluster,
        analyses: list,
        dim_scores: dict,
        composite: float,
        severity: str,
        jira_content: dict,
        duplicate_id: Optional[str] = None,
        duplicate_detected: bool = False,
    ):
        import uuid as _uuid

        first_test_id = (cluster.member_test_ids or [None])[0]
        evidence_bundle = _build_evidence_bundle(analyses, None)

        async with AsyncSessionLocal() as db:
            # Find a valid test_case_id from the cluster
            tc_id = None
            if first_test_id:
                try:
                    tc_result = await db.execute(
                        select(TestCase.id).where(
                            TestCase.id == _uuid.UUID(str(first_test_id))
                        )
                    )
                    tc_id = tc_result.scalar_one_or_none()
                except Exception:
                    pass

            avg_conf = int(composite)
            defect = Defect(
                test_case_id=tc_id,
                project_id=project_id,
                cluster_id=cluster.cluster_id,
                title=jira_content.get("title", cluster.label[:80]),
                description=jira_content.get("description", ""),
                severity=severity,
                component=jira_content.get("component", "Unknown"),
                owner_team=jira_content.get("owner_team", "Unknown"),
                labels=jira_content.get("labels", []),
                criticality_scores=dim_scores,
                evidence_bundle=evidence_bundle,
                ai_confidence_score=avg_conf,
                failure_category=analyses[0].failure_category if analyses else None,
                resolution_status="OPEN",
                is_duplicate=duplicate_detected,
                duplicate_of=_uuid.UUID(duplicate_id) if duplicate_id else None,
            )
            db.add(defect)
            await db.commit()
            await db.refresh(defect)
            return defect.id


# ── Standalone runner ─────────────────────────────────────────────────────────

async def run_defect_commander(
    cluster_id: str,
    test_run_id: str,
    project_id: str,
    project_key: Optional[str] = None,
) -> dict:
    """Standalone entry point for POST /api/v1/agents/defect-command."""
    import uuid

    fake_pipeline_id = str(uuid.uuid4())
    state = {
        "pipeline_run_id": fake_pipeline_id,
        "cluster_id": cluster_id,
        "test_run_id": test_run_id,
        "project_id": project_id,
        "project_key": project_key,
    }

    agent = _StandaloneCommander()
    return await agent._promote(state)


class _StandaloneCommander(DefectCommander):
    """Variant that skips DB stage tracking."""

    async def mark_stage_running(self, pipeline_run_id: str) -> None:
        pass

    async def mark_stage_done(
        self,
        pipeline_run_id: str,
        result_data: dict[Any, Any] | None = None,
        error: str | None = None,
    ) -> None:
        pass

    async def broadcast_progress(self, project_id: str, payload: dict) -> None:
        pass
