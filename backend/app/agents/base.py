"""Base agent class shared by all pipeline agents."""
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.metrics import (
    active_pipeline_runs,
    pipeline_fallback_total,
    pipeline_stage_cost_usd,
    pipeline_stage_duration_seconds,
    pipeline_stage_errors_by_category,
    pipeline_stage_llm_calls_total,
    pipeline_stage_runs_total,
    pipeline_stage_tokens_total,
)
from app.core.config import settings
from app.core.tracing import get_tracer
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AgentPipelineRun, AgentStageResult
from app.services.pipeline_event_log import emit_event

import structlog


class BaseAgent(ABC):
    """
    Abstract base for all pipeline agents.
    Provides DB session management, stage result persistence,
    OpenTelemetry span tracking, and Prometheus metrics emission.
    """

    stage_name: str = "unknown"

    def __init__(self):
        self.logger = structlog.get_logger(f"agents.{self.stage_name}")
        self._tracer = get_tracer(f"agents.{self.stage_name}")
        # Track per-run start times and OTEL spans so mark_stage_done can calculate duration
        self._stage_start: dict[str, float] = {}
        self._stage_spans: dict[str, Any] = {}
        # Per-run decision trail accumulated by ``log_decision`` — flushed to
        # AgentStageResult.decision_log in mark_stage_done and to the
        # pipeline event log as individual ``decision_made`` events.
        self._stage_decisions: dict[str, list[dict[str, Any]]] = {}
        self._budget_reservations: dict[str, str] = {}
        self._budget_context_tokens: dict[str, Any] = {}

    @abstractmethod
    async def run(self, state: dict) -> dict:
        """Execute the agent logic. Returns partial state update."""

    # ── Stage tracking helpers ─────────────────────────────────────

    async def log_decision(
        self,
        pipeline_run_id: str,
        decision_point: str,
        chosen: str,
        rationale: str,
        *,
        alternatives: Optional[list[str]] = None,
        test_case_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        """Record a structured decision taken by this agent.

        Call this at every non-trivial branch: route selection, fallback
        trigger, stage skip, confidence clamp, retry, specialist-stage pick.
        Decisions end up in three places so operators, developers, and
        downstream consumers can all trace them:

        * Structlog info event — grep-friendly, correlates with other logs.
        * Pipeline event log (Mongo, immutable) — ``decision_made`` event
          typed by event_type, queryable via the existing timeline API.
        * ``AgentStageResult.decision_log`` (JSON array) — committed when
          the stage ends so the UI can render the trail alongside the
          result without querying Mongo.

        Args:
            pipeline_run_id: The active pipeline run ID.
            decision_point: Short identifier, e.g. ``"route_analysis_mode"``,
                ``"triage_skip"``, ``"confidence_clamp"``, ``"retry_low_confidence"``.
            chosen: The option taken (e.g., ``"ml"``, ``"skip_low_confidence"``).
            rationale: One-line explanation the reader can act on.
            alternatives: Other options considered but rejected (optional).
            test_case_id: Per-test context (optional).
            context: Any additional structured data worth persisting.
        """
        entry: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "decision_point": decision_point,
            "chosen": chosen,
            "rationale": rationale,
        }
        if alternatives:
            entry["alternatives"] = alternatives
        if test_case_id:
            entry["test_case_id"] = test_case_id
        if context:
            # Only store scalars/shallow dicts — deeply nested objects bloat the row.
            entry["context"] = {
                k: v for k, v in context.items()
                if v is None or isinstance(v, (str, int, float, bool, list, dict))
            }

        self._stage_decisions.setdefault(pipeline_run_id, []).append(entry)

        # Structlog — searchable + correlated with other log fields.
        self.logger.info(
            "agent_decision",
            pipeline_run_id=pipeline_run_id,
            decision_point=decision_point,
            chosen=chosen,
            rationale=rationale,
            test_case_id=test_case_id,
        )

        # Pipeline event log (immutable audit trail).
        await emit_event(
            pipeline_run_id,
            "decision_made",
            stage_name=self.stage_name,
            test_case_id=test_case_id,
            detail=entry,
        )

        # OTEL span event so Jaeger timelines show the decision point inline.
        span = self._stage_spans.get(pipeline_run_id)
        if span is not None:
            try:
                span.add_event(
                    f"decision.{decision_point}",
                    attributes={
                        "decision.chosen": chosen,
                        "decision.rationale": rationale[:500],
                    },
                )
            except Exception:  # pragma: no cover — tracing is best-effort
                pass

    def _has_stage_identity(self, pipeline_run_id: str, call: str) -> bool:
        """A stage can only be recorded against a pipeline that exists.

        Without an id there is no row to flip and no timeline to append to, so
        the lifecycle call is skipped -- but it is skipped *loudly*. A silent
        return here would look exactly like a healthy stage that recorded
        nothing, which is the failure mode this whole area keeps producing.
        """
        if pipeline_run_id:
            return True
        self.logger.warning(
            "stage_lifecycle_skipped_no_pipeline_run_id",
            stage_name=self.stage_name,
            call=call,
        )
        return False

    async def mark_stage_running(
        self,
        pipeline_run_id: str,
        *,
        input_keys: Optional[list[str]] = None,
    ) -> None:
        if not self._has_stage_identity(pipeline_run_id, "mark_stage_running"):
            return
        # Emit pipeline event — include a snapshot of which state keys the
        # stage received so a failing stage can be debugged by looking at the
        # event log alone, without reconstructing the upstream state.
        await emit_event(
            pipeline_run_id, "stage_started",
            stage_name=self.stage_name,
            detail={"input_state_keys": input_keys} if input_keys else None,
        )

        # Start OTEL span
        attrs: dict[str, Any] = {
            "pipeline.run_id": pipeline_run_id,
            "agent.stage": self.stage_name,
        }
        if input_keys:
            # Limit to 40 keys; otel attribute values have a size cap.
            attrs["stage.input_keys"] = ",".join(sorted(input_keys)[:40])
        span = self._tracer.start_span(
            f"agent.{self.stage_name}",
            attributes=attrs,
        )
        self._stage_spans[pipeline_run_id] = span
        self._stage_start[pipeline_run_id] = time.perf_counter()

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select  # noqa: PLC0415
            from app.services.pipeline_budget_service import (
                reserve_stage_in_metadata,
                set_pipeline_budget_context,
            )

            stage = (await db.execute(
                select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == pipeline_run_id,
                    AgentStageResult.stage_name == self.stage_name,
                )
            )).scalar_one_or_none()
            pipeline = (await db.execute(
                select(AgentPipelineRun).where(
                    AgentPipelineRun.id == pipeline_run_id
                ).with_for_update()
            )).scalar_one_or_none()
            context = {
                "pipeline_run_id": pipeline_run_id,
                "stage_name": self.stage_name,
                "blocked": False,
                "stop_reason": None,
            }
            if stage is not None and pipeline is not None and isinstance(stage.allocated_budget, dict):
                try:
                    from app.services.agent_capability_registry import get_capability  # noqa: PLC0415
                    is_llm_stage = get_capability(stage.stage_name).expected_cost_usd > 0
                except (KeyError, ValueError):
                    is_llm_stage = False
                if is_llm_stage:
                    pipeline_metadata = (
                        dict(pipeline.execution_metadata)
                        if isinstance(pipeline.execution_metadata, dict)
                        else {}
                    )
                    reservation_id = __import__("hashlib").sha256(
                        f"{pipeline_run_id}:{self.stage_name}:{int(stage.attempt or 1)}".encode()
                    ).hexdigest()
                    reservation = reserve_stage_in_metadata(
                        pipeline_metadata,
                        reservation_id=reservation_id,
                        stage_name=self.stage_name,
                        attempt=int(stage.attempt or 1),
                        llm_calls=int(stage.allocated_budget.get("max_llm_calls") or 0),
                        tokens=int(stage.allocated_budget.get("max_tokens") or 0),
                        cost_usd=float(stage.allocated_budget.get("max_cost_usd") or 0.0),
                    )
                    if reservation.allowed and reservation.reservation_id:
                        self._budget_reservations[pipeline_run_id] = reservation.reservation_id
                    else:
                        context["blocked"] = True
                        context["stop_reason"] = reservation.stop_reason or "budget_reservation_failed"
                        stage.stop_reason = context["stop_reason"]
                        stage.fallback_reason = context["stop_reason"]
                    pipeline.execution_metadata = pipeline_metadata
            token = set_pipeline_budget_context(**context)
            self._budget_context_tokens[pipeline_run_id] = token
            if stage is not None:
                stage.status = "running"
                stage.started_at = datetime.now(timezone.utc)
            await db.commit()

    async def _estimate_stage_cost(self, input_tokens: int, output_tokens: int):
        """Price a stage's tokens against the effective provider and model.

        Returns a ``CostEstimate`` or None. Best-effort by design: metering
        must never be able to fail a pipeline stage, so every error path
        degrades to "no estimate" and leaves the caller's value alone.
        """
        try:
            from app.services.ai_config_resolver import get_effective_ai_config  # noqa: PLC0415
            from app.services.llm_pricing import estimate_cost  # noqa: PLC0415

            try:
                config = await get_effective_ai_config()
            except Exception:  # noqa: BLE001 — fall back to env defaults
                config = {}

            provider = (config.get("provider") or settings.LLM_PROVIDER or "").lower()
            model = config.get("model") or settings.LLM_MODEL or ""
            return estimate_cost(
                provider,
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("stage_cost_estimate_failed", error=str(exc))
            return None

    async def mark_stage_done(
        self,
        pipeline_run_id: str,
        result_data: Optional[dict] = None,
        error: Optional[str] = None,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        llm_calls_count: int = 0,
        cost_usd: float = 0.0,
        error_category: Optional[str] = None,
        fallback_reason: Optional[str] = None,
        confidence_score: Optional[int] = None,
        evidence_count: Optional[int] = None,
        route_rationale: Optional[str] = None,
        analysis_mode: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        if not self._has_stage_identity(pipeline_run_id, "mark_stage_done"):
            return
        from app.services.pipeline_budget_service import get_pipeline_budget_context

        budget_context = get_pipeline_budget_context() or {}
        if not project_id:
            # Reviewer item 7 (b45 r2): most agents do not pass project_id, so
            # the calls this stage observed -- which BudgetedLLM left for the
            # stage to meter -- never reached the Postgres meter (only the
            # Redis counter). The graph runs inside cost_budget_scope, which
            # names the project the reservation was charged to.
            from app.services.llm_cost_reservation import current_cost_scope

            project_id = current_cost_scope()
        input_tokens = max(input_tokens, int(budget_context.get("observed_input_tokens") or 0))
        output_tokens = max(output_tokens, int(budget_context.get("observed_output_tokens") or 0))
        llm_calls_count = max(llm_calls_count, int(budget_context.get("observed_llm_calls") or 0))
        status = "failed" if error else "completed"
        total_tokens = input_tokens + output_tokens
        # Price the call when the caller reported tokens but no cost. Every
        # stage funnels through here, so deriving centrally is what makes the
        # meter cover the whole pipeline rather than the one or two call sites
        # that remembered. A caller that supplies its own cost_usd (a provider
        # that returns real billing) always wins.
        cost_source = "caller"
        if cost_usd <= 0 and total_tokens > 0:
            estimate = await self._estimate_stage_cost(input_tokens, output_tokens)
            if estimate is not None:
                cost_usd = estimate.cost_usd
                cost_source = estimate.source

        # Emit pipeline event with observability detail
        event_type = "stage_failed" if error else "stage_completed"
        duration_secs = round(
            time.perf_counter() - self._stage_start.get(pipeline_run_id, time.perf_counter()), 3
        )
        await emit_event(
            pipeline_run_id, event_type,
            stage_name=self.stage_name,
            detail={
                "duration_seconds": duration_secs,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "llm_calls_count": llm_calls_count,
                "cost_usd": round(cost_usd, 6),
                # How the number was arrived at: "priced" | "self_hosted" |
                # "unpriced" | "caller". A $0.00 from a self-hosted provider
                # and a $0.00 from an unpriced cloud model mean opposite
                # things, and the reader must be able to tell them apart.
                "cost_source": cost_source,
                **({"confidence_score": confidence_score} if confidence_score is not None else {}),
                **({"evidence_count": evidence_count} if evidence_count is not None else {}),
                **({"error": error[:500]} if error else {}),
                **({"error_category": error_category} if error_category else {}),
                **({"fallback_reason": fallback_reason} if fallback_reason else {}),
                **({"route_rationale": route_rationale} if route_rationale else {}),
                **({"result_summary": {k: v for k, v in (result_data or {}).items() if not isinstance(v, (list, dict))}} if result_data else {}),
            },
        )

        # Record Prometheus metrics
        duration = time.perf_counter() - self._stage_start.pop(pipeline_run_id, time.perf_counter())
        pipeline_stage_duration_seconds.labels(
            stage_name=self.stage_name, status=status
        ).observe(duration)
        pipeline_stage_runs_total.labels(
            stage_name=self.stage_name, status=status
        ).inc()

        # Phase 6: Token, cost, LLM call, fallback, and error metrics
        if input_tokens > 0:
            pipeline_stage_tokens_total.labels(
                stage_name=self.stage_name, direction="input"
            ).inc(input_tokens)
        if output_tokens > 0:
            pipeline_stage_tokens_total.labels(
                stage_name=self.stage_name, direction="output"
            ).inc(output_tokens)
        if cost_usd > 0:
            pipeline_stage_cost_usd.labels(stage_name=self.stage_name).inc(cost_usd)
        if llm_calls_count > 0:
            pipeline_stage_llm_calls_total.labels(stage_name=self.stage_name).inc(llm_calls_count)
        if fallback_reason:
            pipeline_fallback_total.labels(
                stage_name=self.stage_name, reason=fallback_reason[:50]
            ).inc()
        if error_category:
            pipeline_stage_errors_by_category.labels(
                stage_name=self.stage_name, error_category=error_category
            ).inc()

        # Tier 1 item 2 — feed the LLM cost meter. Runs only when the caller
        # passed project_id (every agent that owns state["project_id"]
        # propagates it). Best-effort; the service itself is a no-op unless
        # the ``llm_cost_budget`` feature flag is on.
        if project_id and (cost_usd > 0 or llm_calls_count > 0):
            try:
                from app.services.llm_cost_budget import record_usage
                await record_usage(
                    project_id,
                    cost_usd=float(cost_usd or 0.0),
                    input_tokens=int(input_tokens or 0),
                    output_tokens=int(output_tokens or 0),
                    llm_calls=int(llm_calls_count or 0),
                )
            except Exception as exc:  # pragma: no cover — metering is best-effort
                self.logger.debug("llm usage record failed", error=str(exc))

        # Close OTEL span — enrich with decision attributes so the Jaeger
        # timeline shows *why* the stage ran as it did, not just how long it
        # took. Consumers filter spans by analysis.mode, fallback.used,
        # error.category to audit agent behaviour in aggregate.
        span = self._stage_spans.pop(pipeline_run_id, None)
        if span is not None:
            try:
                from opentelemetry.trace import StatusCode  # noqa: PLC0415

                if error:
                    span.set_status(StatusCode.ERROR, error[:200])
                else:
                    span.set_status(StatusCode.OK)
                span.set_attribute("duration_seconds", round(duration, 3))
                span.set_attribute("tokens.input", input_tokens)
                span.set_attribute("tokens.output", output_tokens)
                span.set_attribute("tokens.total", total_tokens)
                span.set_attribute("cost_usd", round(cost_usd, 6))
                span.set_attribute("llm_calls", llm_calls_count)
                if analysis_mode:
                    span.set_attribute("analysis.mode", analysis_mode)
                if route_rationale:
                    span.set_attribute("route.rationale", route_rationale[:500])
                if fallback_reason:
                    span.set_attribute("fallback.used", True)
                    span.set_attribute("fallback.reason", fallback_reason[:200])
                if error_category:
                    span.set_attribute("error.category", error_category)
                if confidence_score is not None:
                    span.set_attribute("result.confidence_score", confidence_score)
                if evidence_count is not None:
                    span.set_attribute("result.evidence_count", evidence_count)
                # Summarise decision trail cardinality so dashboards can flag
                # unusually-chatty stages without pulling the full JSON.
                decisions = self._stage_decisions.get(pipeline_run_id, [])
                if decisions:
                    span.set_attribute("decisions.count", len(decisions))
            except ImportError:
                pass
            finally:
                span.end()

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select  # noqa: PLC0415
            from app.services.pipeline_budget_service import (
                append_provider_policy_audit,
                get_pipeline_budget_context,
                queue_stage_settlement,
            )

            pipeline = (await db.execute(
                select(AgentPipelineRun).where(
                    AgentPipelineRun.id == pipeline_run_id
                ).with_for_update()
            )).scalar_one_or_none()
            stage_attempt = 1
            stage_probe = await db.execute(
                select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == pipeline_run_id,
                    AgentStageResult.stage_name == self.stage_name,
                )
            )
            stage_probe_row = stage_probe.scalar_one_or_none()
            if stage_probe_row is not None:
                stage_attempt = max(int(stage_probe_row.attempt or 1), 1)
            reservation_id = self._budget_reservations.pop(pipeline_run_id, None)
            settlement_reason = None
            if pipeline is not None:
                pipeline_metadata = (
                    pipeline.execution_metadata
                    if isinstance(pipeline.execution_metadata, dict)
                    else {}
                )
                context = get_pipeline_budget_context() or {}
                append_provider_policy_audit(
                    pipeline_metadata, context.get("llm_privacy_events")
                )
                if reservation_id:
                    settlement_reason = queue_stage_settlement(
                        pipeline_metadata,
                        reservation_id=reservation_id,
                        stage_name=self.stage_name,
                        attempt=stage_attempt,
                        actual_llm_calls=int(llm_calls_count or 0),
                        actual_tokens=int(total_tokens or 0),
                        actual_cost_usd=float(cost_usd or 0.0),
                    )
                pipeline.execution_metadata = pipeline_metadata

            result = await db.execute(
                select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == pipeline_run_id,
                    AgentStageResult.stage_name == self.stage_name,
                )
            )
            stage = result.scalar_one_or_none()
            if stage:
                stage.status = status
                stage.completed_at = datetime.now(timezone.utc)
                if settlement_reason:
                    stage.stop_reason = settlement_reason
                    stage.fallback_reason = settlement_reason
                if result_data:
                    stage.result_data = result_data
                if error:
                    stage.error = error[:2000]
                # Phase 6: Persist observability columns
                stage.input_tokens = input_tokens or None
                stage.output_tokens = output_tokens or None
                stage.total_tokens = total_tokens or None
                stage.llm_calls_count = llm_calls_count or None
                stage.cost_usd = cost_usd or None
                stage.error_category = error_category
                stage.confidence_score = confidence_score
                stage.evidence_count = evidence_count
                stage.route_rationale = route_rationale
                stage.analysis_mode = analysis_mode
                if fallback_reason:
                    stage.fallback_used = True
                    stage.fallback_reason = fallback_reason[:200]
                # Flush the accumulated decision trail for this run.
                decisions = self._stage_decisions.pop(pipeline_run_id, None)
                if decisions:
                    stage.decision_log = decisions
                await db.commit()

        # Settlement is intentionally a second transaction. If its commit
        # fails, the pending receipt remains durable for the periodic reaper.
        if reservation_id:
            try:
                await self._retry_pipeline_settlement(pipeline_run_id, reservation_id)
            except Exception as exc:  # pragma: no cover - recovery is retried by beat
                self.logger.warning(
                    "pipeline_budget_settlement_deferred",
                    pipeline_run_id=pipeline_run_id,
                    stage_name=self.stage_name,
                    error_type=type(exc).__name__,
                )

        token = self._budget_context_tokens.pop(pipeline_run_id, None)
        if token is not None:
            from app.services.pipeline_budget_service import reset_pipeline_budget_context
            reset_pipeline_budget_context(token)

    async def _retry_pipeline_settlement(
        self, pipeline_run_id: str, reservation_id: str
    ) -> None:
        from sqlalchemy import select  # noqa: PLC0415
        from app.services.pipeline_budget_service import retry_pending_stage_settlements

        async with AsyncSessionLocal() as db:
            pipeline = (await db.execute(
                select(AgentPipelineRun).where(
                    AgentPipelineRun.id == pipeline_run_id
                ).with_for_update()
            )).scalar_one_or_none()
            if pipeline is None:
                return
            metadata = dict(pipeline.execution_metadata or {})
            retry_pending_stage_settlements(metadata, reservation_id=reservation_id)
            pipeline.execution_metadata = metadata
            await db.commit()
    def track_active(self, workflow_type: str, delta: float) -> None:
        """Increment or decrement the active-pipeline-runs Prometheus gauge."""
        active_pipeline_runs.labels(workflow_type=workflow_type).inc(delta)

    async def broadcast_progress(self, project_id: str, payload: dict) -> None:
        """Broadcast pipeline progress event via WebSocket."""
        try:
            from app.streams.live_fanout import publish_live_notification
            await publish_live_notification(
                project_id,
                {"type": "pipeline_progress", "stage": self.stage_name, **payload},
            )
        except Exception as exc:
            self.logger.debug("ws_broadcast_failed", error=str(exc))
