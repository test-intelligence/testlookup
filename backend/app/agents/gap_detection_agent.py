"""
Gap Detection Agent (AIQ-P4).

Pure-local, offline-safe, deterministic, never-raise analytic agent. It audits
the analysis coverage of a run: for every failed test it decides whether the
test was analyzed, skipped, or errored, and — for analyzed tests — whether the
analysis is of usable quality (conclusive, evidence-backed, confident enough).

Like the other AIQ helpers it NEVER performs outbound calls, NEVER writes to a
database from its own logic, NEVER mutates its inputs, and NEVER raises: the
whole body is wrapped in a try/except that degrades to a deterministic fallback
contract. Gap detail carries ONLY structural tokens — never raw error/log text.
"""
import structlog

from app.agents.base import BaseAgent
from app.agents.evidence import EvidenceRef
from app.core.config import settings
from app.models.agent_contracts import (
    GapDetectionAgentOutput,
    GapItem,
    GapReason,
    validate_agent_contract,
)

logger = structlog.get_logger("agents.gap_detection")


def _as_list(value) -> list:
    """Coerce a value to a list, returning [] for any non-collection input."""
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


class GapDetectionAgent(BaseAgent):
    stage_name = "gap_detection"

    async def run(self, state: dict) -> dict:
        pipeline_run_id: str = ""
        project_id: str = ""

        try:
            pipeline_run_id = state.get("pipeline_run_id", "")
            project_id = state.get("project_id", "")

            await self.mark_stage_running(pipeline_run_id)
            await self.broadcast_progress(
                project_id,
                {"status": "running", "message": "Auditing analysis coverage..."},
            )

            universe = {str(tid) for tid in _as_list(state.get("failed_test_ids"))}
            failed_count = len(universe)

            raw_analyses = state.get("analyses")
            analyses = raw_analyses if isinstance(raw_analyses, dict) else {}
            analysis_keys = {str(k): v for k, v in analyses.items()}

            threshold = settings.AI_CONFIDENCE_THRESHOLD
            gaps: list[GapItem] = []
            analyzed_count = 0
            skipped_count = 0
            errored_count = 0
            inconclusive_count = 0
            no_evidence_count = 0

            for tid in sorted(universe):
                if tid not in analysis_keys:
                    skipped_count += 1
                    gaps.append(GapItem(
                        test_id=tid,
                        reason=GapReason.UNANALYZED,
                        detail="no_analysis_entry",
                        bucket="skipped",
                    ))
                    continue

                entry = analysis_keys.get(tid)
                entry = entry if isinstance(entry, dict) else {}
                if entry.get("error") or entry.get("timed_out"):
                    errored_count += 1
                    gaps.append(GapItem(
                        test_id=tid,
                        reason=GapReason.ERRORED,
                        detail="analysis_errored" if entry.get("error") else "analysis_timed_out",
                        bucket="errored",
                    ))
                    continue

                # Analyzed: sub-classify quality (priority inconclusive >
                # no_evidence > low_confidence). Clean analyzed tests emit no gap.
                analyzed_count += 1
                category = entry.get("failure_category")
                evidence = _as_list(entry.get("evidence_references"))
                try:
                    confidence = int(entry.get("confidence_score") or 0)
                except (TypeError, ValueError):
                    confidence = 0

                if category == "UNKNOWN":
                    inconclusive_count += 1
                    gaps.append(GapItem(
                        test_id=tid,
                        reason=GapReason.INCONCLUSIVE,
                        detail="failure_category_unknown",
                        bucket="analyzed",
                    ))
                elif not evidence:
                    no_evidence_count += 1
                    gaps.append(GapItem(
                        test_id=tid,
                        reason=GapReason.NO_EVIDENCE,
                        detail="no_evidence_references",
                        bucket="analyzed",
                    ))
                elif 0 < confidence < threshold:
                    gaps.append(GapItem(
                        test_id=tid,
                        reason=GapReason.LOW_CONFIDENCE,
                        detail=f"confidence_below_threshold:{threshold}",
                        bucket="analyzed",
                    ))

            coverage_ratio = analyzed_count / failed_count if failed_count else 1.0
            integrity_ok = (
                analyzed_count + skipped_count + errored_count == failed_count
            )

            evidence_refs = [EvidenceRef(
                source="gap_detection",
                ref_id="coverage",
                excerpt=f"analyzed {analyzed_count}/{failed_count}",
                strength="strong",
                contribution=round(coverage_ratio * 100),
            )]

            payload = {
                "gap_report": {
                    "failed_count": failed_count,
                    "analyzed_count": analyzed_count,
                    "skipped_count": skipped_count,
                    "errored_count": errored_count,
                    "coverage_ratio": coverage_ratio,
                    "integrity_ok": integrity_ok,
                    "gaps": [g.model_dump(mode="json") for g in gaps],
                    "inconclusive_count": inconclusive_count,
                    "no_evidence_count": no_evidence_count,
                },
                "completed_stages": ["gap_detection"],
                "current_stage": "report_refinement",
            }

            await self.mark_stage_done(
                pipeline_run_id,
                result_data={
                    "failed_count": failed_count,
                    "analyzed_count": analyzed_count,
                    "gap_count": len(gaps),
                },
            )
            await self.broadcast_progress(project_id, {
                "status": "completed",
                "message": f"Coverage {analyzed_count}/{failed_count}; {len(gaps)} gap(s)",
            })

            await self.log_decision(
                pipeline_run_id,
                decision_point="coverage_audit",
                chosen=f"coverage:{analyzed_count}/{failed_count}",
                rationale=f"{len(gaps)} gap(s); integrity_ok={integrity_ok}",
                context={
                    "failed_count": failed_count,
                    "analyzed_count": analyzed_count,
                    "skipped_count": skipped_count,
                    "errored_count": errored_count,
                    "integrity_ok": integrity_ok,
                },
            )

            return validate_agent_contract(
                GapDetectionAgentOutput,
                payload,
                agent_name=self.stage_name,
                structured_evidence=evidence_refs,
                decision_reason=(
                    f"coverage {analyzed_count}/{failed_count}; integrity_ok={integrity_ok}"
                ),
            )
        except Exception as exc:
            logger.warning("gap_detection_failed", error=str(exc))
            try:
                await self.log_decision(
                    pipeline_run_id,
                    decision_point="gap_detection_fallback",
                    chosen="fallback_contract",
                    rationale="coverage audit failed; emitting deterministic fallback",
                )
            except Exception:  # never-raise: a failing decision log must not escape
                pass
            return validate_agent_contract(
                GapDetectionAgentOutput,
                {
                    "gap_report": {},
                    "completed_stages": ["gap_detection"],
                    "current_stage": "report_refinement",
                },
                agent_name=self.stage_name,
                fallback_used=True,
                confidence=0,
                decision_reason="gap_detection_fallback_used",
            )
