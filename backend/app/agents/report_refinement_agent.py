"""
Report Refinement Agent (AIQ-P4).

Pure-local, offline-safe, deterministic, never-raise analytic agent. It
reconciles the three diagnostic routes that can independently flag a failed
test — root-cause analysis, anomaly detection, and failure clustering —
deduplicating multi-route tests and resolving cross-route contradictions into a
single, stable per-test view.

Like the other AIQ helpers it NEVER performs outbound calls, NEVER writes to a
database from its own logic, NEVER mutates its inputs, and NEVER raises: the
whole body is wrapped in a try/except that degrades to a deterministic fallback
contract. Contradiction detail carries ONLY structural tokens — never raw
error/log text.
"""
import structlog

from app.agents.base import BaseAgent
from app.agents.evidence import EvidenceRef
from app.models.agent_contracts import (
    Contradiction,
    ContradictionType,
    ReportRefinementAgentOutput,
    ResolutionStrategy,
    validate_agent_contract,
)

logger = structlog.get_logger("agents.report_refinement")


def _as_list(value) -> list:
    """Coerce a value to a list, returning [] for any non-collection input."""
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


class ReportRefinementAgent(BaseAgent):
    stage_name = "report_refinement"

    async def run(self, state: dict) -> dict:
        pipeline_run_id: str = ""
        project_id: str = ""

        try:
            pipeline_run_id = state.get("pipeline_run_id", "")
            project_id = state.get("project_id", "")

            await self.mark_stage_running(pipeline_run_id)
            await self.broadcast_progress(
                project_id,
                {"status": "running", "message": "Reconciling diagnostic routes..."},
            )

            raw_analyses = state.get("analyses")
            analyses = raw_analyses if isinstance(raw_analyses, dict) else {}
            analysis_ids = {str(k) for k in analyses}

            anomaly_ids = {
                str(tid)
                for a in _as_list(state.get("anomalies"))
                if isinstance(a, dict)
                for tid in _as_list(a.get("test_ids"))
            }

            raw_cluster_map = state.get("cluster_map")
            cluster_map = raw_cluster_map if isinstance(raw_cluster_map, dict) else {}
            cluster_ids = {str(k) for k in cluster_map}

            regression_ids = {str(t) for t in _as_list(state.get("regression_tests"))}

            # Multi-route tests appear in >= 2 of the three route id-sets.
            all_ids = analysis_ids | anomaly_ids | cluster_ids
            multi_route_test_ids: list[str] = []
            for tid in sorted(all_ids):
                routes = self._routes_for(tid, analysis_ids, anomaly_ids, cluster_ids)
                if len(routes) >= 2:
                    multi_route_test_ids.append(tid)

            dedup_count = len(multi_route_test_ids)

            contradictions: list[Contradiction] = []
            reconciled_tests: dict[str, dict] = {}

            for tid in multi_route_test_ids:
                routes = self._routes_for(tid, analysis_ids, anomaly_ids, cluster_ids)
                entry = analyses.get(tid)
                entry = entry if isinstance(entry, dict) else {}

                # FLAKY_VS_REGRESSION: analysis calls it flaky but a route also
                # treats it as a regression/new anomaly.
                if entry.get("is_flaky") is True and (
                    tid in regression_ids or tid in anomaly_ids
                ):
                    contradictions.append(Contradiction(
                        test_id=tid,
                        type=ContradictionType.FLAKY_VS_REGRESSION,
                        routes=routes,
                        resolution=ResolutionStrategy.PREFER_ANOMALY,
                        detail="flaky_analysis_vs_regression_signal",
                    ))
                # CATEGORY_DISAGREEMENT: analysis category present and the test
                # also routes through anomaly under a different signal.
                elif (
                    entry.get("failure_category")
                    and tid in anomaly_ids
                    and tid in analysis_ids
                ):
                    contradictions.append(Contradiction(
                        test_id=tid,
                        type=ContradictionType.CATEGORY_DISAGREEMENT,
                        routes=routes,
                        resolution=ResolutionStrategy.MERGE,
                        detail="analysis_category_vs_anomaly_route",
                    ))

                reconciled_tests[tid] = {
                    "primary_route": self._primary_route(routes),
                    "is_flaky": bool(entry.get("is_flaky")),
                    "category": entry.get("failure_category"),
                    "severity": entry.get("severity"),
                    "routes": routes,
                }

            resolved = sum(
                1
                for c in contradictions
                if c.resolution != ResolutionStrategy.FLAG_FOR_REVIEW
            )
            total = len(contradictions)
            resolved_fraction = (resolved / total) if total else 1.0

            evidence_refs = [EvidenceRef(
                source="report_refinement",
                ref_id="reconciliation",
                excerpt=f"deduped {dedup_count}; resolved {resolved}/{total}",
                strength="strong",
                contribution=round(resolved_fraction * 100),
            )]

            payload = {
                "refined_report": {
                    "dedup_count": dedup_count,
                    "contradictions_resolved": resolved,
                    "contradictions": [c.model_dump(mode="json") for c in contradictions],
                    "reconciled_tests": reconciled_tests,
                    "multi_route_test_ids": multi_route_test_ids,
                    "unresolved_count": total - resolved,
                },
                "completed_stages": ["report_refinement"],
                "current_stage": "flaky_sentinel",
            }

            await self.mark_stage_done(
                pipeline_run_id,
                result_data={
                    "dedup_count": dedup_count,
                    "contradictions": total,
                    "resolved": resolved,
                },
            )
            await self.broadcast_progress(project_id, {
                "status": "completed",
                "message": f"Deduped {dedup_count}; resolved {resolved}/{total} contradiction(s)",
            })

            return validate_agent_contract(
                ReportRefinementAgentOutput,
                payload,
                agent_name=self.stage_name,
                structured_evidence=evidence_refs,
                decision_reason=f"deduped {dedup_count}, resolved {resolved}/{total}",
            )
        except Exception as exc:
            logger.warning("report_refinement_failed", error=str(exc))
            return validate_agent_contract(
                ReportRefinementAgentOutput,
                {
                    "refined_report": {},
                    "completed_stages": ["report_refinement"],
                    "current_stage": "flaky_sentinel",
                },
                agent_name=self.stage_name,
                fallback_used=True,
                confidence=0,
                decision_reason="report_refinement_fallback_used",
            )

    @staticmethod
    def _routes_for(
        tid: str,
        analysis_ids: set[str],
        anomaly_ids: set[str],
        cluster_ids: set[str],
    ) -> list[str]:
        routes: list[str] = []
        if tid in analysis_ids:
            routes.append("analysis")
        if tid in anomaly_ids:
            routes.append("anomaly")
        if tid in cluster_ids:
            routes.append("cluster")
        return routes

    @staticmethod
    def _primary_route(routes: list[str]) -> str:
        # Stable precedence: analysis > anomaly > cluster.
        for route in ("analysis", "anomaly", "cluster"):
            if route in routes:
                return route
        return ""
