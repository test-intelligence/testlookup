"""The five hypothesis sub-agents of the Investigator (Agentic plan AI-1).

Each sub-agent follows the same discipline:

1. **Deterministic evidence weighing FIRST** — a pure ``evaluate(bundle)``
   classmethod turns the plan node's pre-gathered evidence bundle into a
   verdict (``validated`` / ``invalidated`` / ``inconclusive``), a 0-100
   confidence, a summary, and citation-style evidence items. Pure functions
   so the threshold matrix is unit-testable without a DB or a graph.
2. **AT MOST ONE bounded LLM call** — when an LLM is configured (not
   ``AI_OFFLINE_MODE``) and the per-run budget allows, the deterministic
   verdict + evidence lines are handed to the registry prompt
   ``investigator_hypothesis_weigh`` for re-weighing. The LLM may refine
   status/confidence/summary (``confidence_basis: "llm_weighted"``); any
   failure, timeout, or budget stop falls back to the deterministic verdict
   (``confidence_basis: "heuristic_estimate"``). **With no LLM configured
   the Investigator is fully functional offline.**
3. **Persist-as-you-go** — the finished hypothesis is written onto the
   investigation row immediately (the UI polls), the decision is logged via
   the BaseAgent decision-log hook, and the output is wrapped through
   ``validate_agent_contract``.

Evidence matrix (deterministic thresholds — see each ``evaluate``):

======================  =====================================================
hypothesis              validated when
======================  =====================================================
``infra``               infra signal ratio ≥ 0.5 — max(BROKEN-status ratio,
                        infra-classified/rule-pack-matched ratio) over the
                        run's failures, +0.1 for cross-suite blast radius
                        (≥3 failing suites covering ≥50% of suites).
``commit``              commit hash differs from the baseline's AND
                        newly-failed concentration ≥ 0.5 of the run's
                        failures. No git access — evidence states
                        "commit-range contents unavailable; onset alignment
                        only".
``environment``         strong env drift (ocp_namespace / ocp_node /
                        environment differ vs baseline) OR a broad slowdown
                        (run duration ≥ 2× baseline) with failures present.
                        Branch drift alone is a weak signal → inconclusive.
``known_flaky``         flaky coverage ≥ 0.6 — |newly-failed ∩ (active
                        quarantines ∪ flaky-coach cache)| / |newly-failed|;
                        evidence cites memory-recall lines (prior
                        corrections/analyses) for the top offenders.
``regression``          a clean non-flaky, non-infra newly-failed core
                        exists and covers ≥ 0.5 of the newly-failed set;
                        +8 confidence when one failure cluster concentrates
                        ≥ 50% of the core.
======================  =====================================================
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from app.agents.base import BaseAgent
from app.agents.investigator.persistence import (
    is_cancel_requested,
    persist_hypothesis,
    reserve_investigation_budget,
    settle_investigation_budget,
    utcnow_iso,
)
from app.core.config import settings
from app.models.agent_contracts import (
    InvestigatorHypothesisOutput,
    validate_agent_contract,
)
from app.services.llm_pricing import TokenUsage, estimate_cost, extract_token_usage
from app.services.resilience import estimate_token_count
from app.services.evidence_sanitizer import sanitize_persistence_payload

# Deterministic threshold constants (the evidence matrix above).
INFRA_VALIDATED_RATIO = 0.5
INFRA_INVALIDATED_RATIO = 0.15
COMMIT_VALIDATED_CONCENTRATION = 0.5
ENV_SLOWDOWN_RATIO = 2.0
FLAKY_VALIDATED_COVERAGE = 0.6
FLAKY_INVALIDATED_COVERAGE = 0.2
REGRESSION_VALIDATED_CORE_RATIO = 0.5
CLUSTER_CONCENTRATION_RATIO = 0.5

BASIS_HEURISTIC = "heuristic_estimate"
BASIS_LLM = "llm_weighted"

# Ceiling for a single hypothesis LLM call (seconds) — also clamped by the
# investigation's remaining wall-clock budget.
_LLM_CALL_TIMEOUT_S = 60

_MAX_EVIDENCE_ITEMS = 8
_MAX_SAMPLE_NAMES = 3


def _ev(kind: str, label: str, detail: str, url_path: Optional[str] = None) -> dict[str, Any]:
    """One evidence item in the pinned wire shape."""
    return {"kind": kind, "label": label, "url_path": url_path, "detail": detail}


def _clamp(value: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(value))))


def _sample_names(items: list[dict[str, Any]], limit: int = _MAX_SAMPLE_NAMES) -> str:
    names = [str(i.get("test_name") or i.get("fingerprint") or "?") for i in items[:limit]]
    suffix = "…" if len(items) > limit else ""
    return ", ".join(names) + suffix


def _parse_llm_json(raw: Any) -> Optional[dict[str, Any]]:
    """Parse the LLM's JSON reply, tolerating markdown fences. None on any
    shape problem — the deterministic verdict is the fallback."""
    text = str(getattr(raw, "content", raw) or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


class HypothesisAgent(BaseAgent):
    """Shared run() skeleton for the five hypothesis sub-agents."""

    hypothesis_id: str = "unknown"
    title: str = "Unknown hypothesis"

    @classmethod
    def evaluate(cls, bundle: dict[str, Any]) -> dict[str, Any]:
        """Pure deterministic weighing: bundle → {status, confidence,
        summary, evidence, signals}. Overridden per hypothesis."""
        raise NotImplementedError

    # ── Bounded LLM re-weigh (at most ONE call) ──────────────────────────

    async def _weigh_with_llm(
        self, det: dict[str, Any], state: dict[str, Any]
    ) -> tuple[Optional[dict[str, Any]], TokenUsage, Optional[str]]:
        """Try the single bounded LLM call. Returns (weighed|None, usage).
        Any failure returns (None, empty usage) — deterministic wins.

        Cost is no longer returned: it was always the literal 0.0, which is how
        this codebase came to meter $0.00 for every call. ``mark_stage_done``
        prices the reported input/output split centrally instead.
        """
        if settings.AI_OFFLINE_MODE:
            return None, TokenUsage(), None
        remaining = float(state.get("deadline_ts", 0)) - time.monotonic()
        if remaining <= 5:
            return None, TokenUsage(), "wall_clock_budget_exhausted"
        prompt = ""
        reservation = None
        reservation_attempted = False
        attempted = False
        usage = TokenUsage()
        async def settle_safely() -> Optional[str]:
            if reservation is None or not reservation.allowed:
                return None
            try:
                observed_cost = estimate_cost(
                    settings.LLM_PROVIDER,
                    settings.LLM_MODEL,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
                settled = await settle_investigation_budget(
                    str(state.get("investigation_id") or ""),
                    reservation,
                    actual_llm_calls=1 if attempted else 0,
                    actual_tokens=usage.total_tokens,
                    actual_cost_usd=observed_cost.cost_usd,
                    cost_source=observed_cost.source,
                )
                return None if settled else "budget_settlement_failed"
            except Exception as exc:  # noqa: BLE001 - preserve deterministic result
                self.logger.warning(
                    "hypothesis_budget_settlement_failed",
                    hypothesis_id=self.hypothesis_id,
                    error_type=type(exc).__name__,
                )
                return "budget_settlement_failed"
        try:
            from app.services.llm_factory import get_llm
            from app.services.prompt_registry import get_prompt_text

            safe_det, _ = sanitize_persistence_payload(det)
            evidence_lines = "\n".join(
                f"- [{e['kind']}] {e['label']}: {e['detail']}"
                for e in safe_det["evidence"]
            ) or "- (no evidence lines)"
            prompt = get_prompt_text("investigator_hypothesis_weigh").format(
                hypothesis_id=self.hypothesis_id,
                hypothesis_title=self.title,
                signals_json=json.dumps(
                    safe_det.get("signals", {}), sort_keys=True, default=str
                ),
                evidence_lines=evidence_lines,
                det_status=safe_det["status"],
                det_confidence=safe_det["confidence"],
            )
            # UTF-8 bytes are a conservative upper bound for normal tokenizer
            # input units; observed provider usage is still accounted in full.
            token_ceiling = len(prompt.encode("utf-8")) + int(settings.LLM_MAX_TOKENS)
            reserved_cost = estimate_cost(
                settings.LLM_PROVIDER,
                settings.LLM_MODEL,
                output_tokens=token_ceiling,
            )
            reservation_attempted = True
            reservation = await reserve_investigation_budget(
                str(state.get("investigation_id") or ""),
                reservation_id=(
                    f"investigation:{state.get('investigation_id')}:"
                    f"hypothesis:{self.hypothesis_id}"
                ),
                llm_calls=1,
                tokens=token_ceiling,
                reserved_cost_usd=reserved_cost.cost_usd,
                cost_source=f"reserved_{reserved_cost.source}",
                project_id=str(state.get("project_id") or ""),
                run_id=str(state.get("run_id") or ""),
                pipeline_run_id=str(state.get("pipeline_run_id") or ""),
                stage_name=self.stage_name,
            )
            if not reservation.allowed:
                return None, TokenUsage(), reservation.stop_reason
            # Cancellation can race the durable reservation. Re-check before
            # constructing/invoking the provider so a queued child does not
            # spend a call after its parent has cancelled it; settle the
            # reservation as an unattempted call.
            if await is_cancel_requested(str(state.get("investigation_id") or "")):
                return None, TokenUsage(), (await settle_safely()) or "cancelled"
            llm = await get_llm(temperature=0.0)
            if await is_cancel_requested(str(state.get("investigation_id") or "")):
                return None, TokenUsage(), (await settle_safely()) or "cancelled"
            attempted = True
            response = await asyncio.wait_for(
                llm.ainvoke(prompt),
                timeout=min(_LLM_CALL_TIMEOUT_S, remaining),
            )
            usage = extract_token_usage(
                response,
                fallback_prompt=prompt,
                fallback_completion=str(getattr(response, "content", "")),
            )
            parsed = _parse_llm_json(response)
            if parsed is None:
                return None, usage, await settle_safely()
            status = str(parsed.get("status", "")).lower()
            if status not in ("validated", "invalidated", "inconclusive"):
                return None, usage, await settle_safely()
            settlement_reason = await settle_safely()
            return (
                {
                    "status": status,
                    "confidence": _clamp(float(parsed.get("confidence", det["confidence"]))),
                    "summary": str(parsed.get("summary") or det["summary"])[:1000],
                },
                usage,
                settlement_reason,
            )
        except Exception as exc:  # noqa: BLE001 — LLM failure falls back
            self.logger.debug(
                "hypothesis_llm_weigh_failed", error_type=type(exc).__name__
            )
            if attempted and reservation is not None:
                usage = TokenUsage(
                    input_tokens=estimate_token_count(prompt),
                    output_tokens=max(
                        0,
                        reservation.reserved_tokens - estimate_token_count(prompt),
                    ),
                    source="estimated",
                    details={"reason": "provider_outcome_unknown"},
                )
            settlement_reason = await settle_safely()
            return (
                None,
                usage,
                settlement_reason
                or (
                    "budget_reservation_failed"
                    if reservation_attempted and reservation is None
                    else None
                ),
            )

    # ── Node entry ───────────────────────────────────────────────────────

    async def run(self, state: dict) -> dict:
        # A resumed child keeps completed hypothesis outputs and reservation
        # tombstones authoritative. Do not re-enter the provider path for a
        # completed hypothesis; the synthesis node will merge the persisted
        # output back into the reducer state.
        if self.hypothesis_id in (state.get("resume_completed_hypotheses") or set()):
            return {"hypotheses": [], "errors": []}

        pipeline_run_id = state.get("pipeline_run_id", "")
        investigation_id = state.get("investigation_id", "")
        started_at = utcnow_iso()
        await self.mark_stage_running(pipeline_run_id, input_keys=["bundle"])

        # Cooperative cancel — checked between nodes.
        if state.get("cancelled") or await is_cancel_requested(investigation_id):
            await self.log_decision(
                pipeline_run_id,
                "hypothesis_skip_cancelled",
                chosen="skip",
                rationale="cancel requested — hypothesis left pending",
                context={"hypothesis_id": self.hypothesis_id},
            )
            await self.mark_stage_done(
                pipeline_run_id,
                result_data={"skipped": "cancelled"},
                project_id=state.get("project_id"),
            )
            return {"hypotheses": [], "errors": []}

        # Wall-clock budget — exceeding leaves an honest inconclusive leftover.
        if time.monotonic() >= float(state.get("deadline_ts", float("inf"))):
            hypothesis = {
                "id": self.hypothesis_id,
                "title": self.title,
                "status": "inconclusive",
                "confidence": 0,
                "confidence_basis": BASIS_HEURISTIC,
                "summary": "Wall-clock budget exhausted before this hypothesis ran.",
                "evidence": [],
                "started_at": started_at,
                "completed_at": utcnow_iso(),
                "llm_enrichment_stop_reason": "wall_clock_budget_exhausted",
            }
            await self.log_decision(
                pipeline_run_id,
                "hypothesis_budget_exhausted",
                chosen="inconclusive",
                rationale="max_seconds budget exhausted before evaluation",
                context={"hypothesis_id": self.hypothesis_id},
            )
            await persist_hypothesis(investigation_id, hypothesis)
            await self.mark_stage_done(
                pipeline_run_id,
                result_data={
                    "hypothesis": self.hypothesis_id,
                    "status": "inconclusive",
                    "stop_reason": "wall_clock_budget_exhausted",
                },
                project_id=state.get("project_id"),
            )
            return {"hypotheses": [hypothesis], "errors": []}

        bundle = state.get("bundle") or {}
        det = self.evaluate(bundle)

        weighed, usage, enrichment_stop_reason = await self._weigh_with_llm(det, state)
        llm_calls = 1 if usage.total_tokens else 0
        if weighed is not None:
            status = weighed["status"]
            confidence = weighed["confidence"]
            summary = weighed["summary"]
            basis = BASIS_LLM
        else:
            status = det["status"]
            confidence = det["confidence"]
            summary = det["summary"]
            basis = BASIS_HEURISTIC
        if enrichment_stop_reason:
            summary = (
                f"{summary} LLM enrichment skipped: "
                f"{enrichment_stop_reason.replace('_', ' ')}."
            )[:1000]

        hypothesis = {
            "id": self.hypothesis_id,
            "title": self.title,
            "status": status,
            "confidence": confidence,
            "confidence_basis": basis,
            "summary": summary,
            "evidence": det["evidence"][:_MAX_EVIDENCE_ITEMS],
            "started_at": started_at,
            "completed_at": utcnow_iso(),
            "llm_enrichment_stop_reason": enrichment_stop_reason,
        }

        await self.log_decision(
            pipeline_run_id,
            "hypothesis_verdict",
            chosen=status,
            rationale=summary[:300],
            alternatives=[s for s in ("validated", "invalidated", "inconclusive") if s != status],
            context={
                "hypothesis_id": self.hypothesis_id,
                "confidence": confidence,
                "confidence_basis": basis,
                "deterministic_status": det["status"],
                "signals": det.get("signals", {}),
                "llm_enrichment_stop_reason": enrichment_stop_reason,
            },
        )

        # Contract wrap (audit metadata) — validated shape is what we persist.
        contracted = validate_agent_contract(
            InvestigatorHypothesisOutput,
            {"hypothesis": hypothesis},
            agent_name=f"investigator.{self.hypothesis_id}",
            confidence=confidence,
            evidence_refs=[
                {"type": e["kind"], "id": e["label"]} for e in hypothesis["evidence"]
            ],
            decision_reason=summary[:500],
        )
        persisted = contracted.get("hypothesis", hypothesis)

        await persist_hypothesis(investigation_id, persisted)
        # Price once and reuse: the stage record and the investigation-level
        # spend rollup must not disagree about what this call cost.
        estimate = await self._estimate_stage_cost(usage.input_tokens, usage.output_tokens)
        cost_usd = estimate.cost_usd if estimate else 0.0
        await self.mark_stage_done(
            pipeline_run_id,
            result_data={
                "hypothesis": self.hypothesis_id,
                "status": status,
                "stop_reason": enrichment_stop_reason,
            },
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            llm_calls_count=llm_calls,
            cost_usd=cost_usd,
            confidence_score=confidence,
            evidence_count=len(hypothesis["evidence"]),
            analysis_mode="investigator",
            project_id=state.get("project_id"),
        )
        await self.broadcast_progress(
            state.get("project_id", ""),
            {
                "investigation_id": investigation_id,
                "hypothesis": self.hypothesis_id,
                "status": status,
            },
        )
        return {
            "hypotheses": [persisted],
            "spend_llm_calls": llm_calls,
            "spend_tokens": usage.total_tokens,
            "spend_cost_usd": cost_usd,
            "errors": [],
        }


# ── 1. Infrastructure ─────────────────────────────────────────────────────────


class InfraHypothesisAgent(HypothesisAgent):
    stage_name = "hypothesis_infra"
    hypothesis_id = "infra"
    title = "Infrastructure failure (runners, services, platform)"

    @classmethod
    def evaluate(cls, bundle: dict[str, Any]) -> dict[str, Any]:
        counts = bundle.get("failure_counts") or {}
        total = int(counts.get("total") or 0)
        evidence: list[dict[str, Any]] = []
        if total == 0:
            return {
                "status": "invalidated",
                "confidence": 70,
                "summary": "No failures to explain — infrastructure hypothesis does not apply.",
                "evidence": evidence,
                "signals": {"total_failures": 0},
            }

        broken = int(counts.get("broken") or 0)
        broken_ratio = broken / total
        failures = bundle.get("failures") or []
        infra_classified = sum(
            1 for f in failures if str(f.get("category") or "").upper() == "INFRASTRUCTURE"
        )
        infra_kw = int(bundle.get("infra_keyword_matches") or 0)
        infra_ratio = max(infra_classified, infra_kw) / total

        distinct_suites = int(counts.get("distinct_failing_suites") or 0)
        total_suites = int(counts.get("total_suites") or 0)
        blast = distinct_suites >= 3 and (
            total_suites == 0 or distinct_suites >= 0.5 * total_suites
        )

        score = max(broken_ratio, infra_ratio) + (0.1 if blast else 0.0)

        evidence.append(_ev(
            "failure_kind",
            "BROKEN-status ratio",
            f"{broken}/{total} failures have BROKEN status (unexpected error, not assertion) — {broken_ratio:.0%}.",
        ))
        evidence.append(_ev(
            "analysis",
            "Infra-classified failures",
            f"{infra_classified}/{total} failures carry an INFRASTRUCTURE classification.",
        ))
        for hit in (bundle.get("infra_rule_hits") or [])[:3]:
            evidence.append(_ev(
                "rule_pack",
                str(hit.get("rule_id") or "infra-pattern"),
                f"Infra rule-pack match on '{hit.get('test_name')}'.",
            ))
        if blast:
            evidence.append(_ev(
                "blast_radius",
                "Cross-suite blast radius",
                f"Failures span {distinct_suites} suites"
                + (f" of {total_suites}" if total_suites else "")
                + " — broad blast radius suggests shared infrastructure.",
            ))

        signals = {
            "broken_ratio": round(broken_ratio, 3),
            "infra_ratio": round(infra_ratio, 3),
            "blast_radius": blast,
            "score": round(score, 3),
        }
        if score >= INFRA_VALIDATED_RATIO:
            return {
                "status": "validated",
                "confidence": _clamp(50 + 40 * min(score, 1.0)),
                "summary": (
                    f"Infrastructure signals dominate: {max(broken_ratio, infra_ratio):.0%} of "
                    f"{total} failures show infra shapes"
                    + (" with cross-suite blast radius" if blast else "")
                    + "."
                ),
                "evidence": evidence,
                "signals": signals,
            }
        if score < INFRA_INVALIDATED_RATIO:
            return {
                "status": "invalidated",
                "confidence": 65,
                "summary": (
                    f"Infrastructure signals are marginal ({max(broken_ratio, infra_ratio):.0%} "
                    f"of {total} failures) — infra is unlikely to be the primary cause."
                ),
                "evidence": evidence,
                "signals": signals,
            }
        return {
            "status": "inconclusive",
            "confidence": 40,
            "summary": (
                f"Mixed infrastructure signals ({max(broken_ratio, infra_ratio):.0%} of {total} "
                "failures) — neither dominant nor negligible."
            ),
            "evidence": evidence,
            "signals": signals,
        }


# ── 2. Commit onset ───────────────────────────────────────────────────────────


class CommitHypothesisAgent(HypothesisAgent):
    stage_name = "hypothesis_commit"
    hypothesis_id = "commit"
    title = "Code change onset (commit vs baseline)"

    # No git access in this deployment — stated on every evidence set.
    _NO_GIT_NOTE = "commit-range contents unavailable; onset alignment only"

    @classmethod
    def evaluate(cls, bundle: dict[str, Any]) -> dict[str, Any]:
        run = bundle.get("run") or {}
        baseline = bundle.get("baseline")
        compare = bundle.get("compare")
        evidence: list[dict[str, Any]] = []

        if baseline is None:
            return {
                "status": "inconclusive",
                "confidence": 25,
                "summary": "No baseline run available — commit onset cannot be aligned.",
                "evidence": [_ev("baseline", "No baseline", cls._NO_GIT_NOTE)],
                "signals": {"baseline": None},
            }

        run_commit = str(run.get("commit_hash") or "")
        base_commit = str(baseline.get("commit_hash") or "")
        evidence.append(_ev(
            "ci_context",
            "Commit hashes",
            f"run={run_commit[:12] or '?'} vs baseline={base_commit[:12] or '?'} "
            f"(baseline build #{baseline.get('build_number')}); {cls._NO_GIT_NOTE}.",
        ))
        if run.get("ci_run_url"):
            evidence.append(_ev(
                "ci_context", "CI run", f"CI job: {run['ci_run_url']}", None,
            ))

        if not run_commit or not base_commit:
            return {
                "status": "inconclusive",
                "confidence": 30,
                "summary": "Commit hashes are missing on the run and/or baseline — onset alignment impossible.",
                "evidence": evidence,
                "signals": {"run_commit": bool(run_commit), "baseline_commit": bool(base_commit)},
            }
        if run_commit == base_commit:
            return {
                "status": "invalidated",
                "confidence": 75,
                "summary": "The run executed the same commit as the baseline — a code change cannot be the onset.",
                "evidence": evidence,
                "signals": {"commit_differs": False},
            }

        total = int((bundle.get("failure_counts") or {}).get("total") or 0)
        newly_failed = int((compare or {}).get("new_failures") or 0)
        concentration = (newly_failed / total) if total else 0.0
        evidence.append(_ev(
            "run_compare",
            "Newly-failed vs baseline",
            f"{newly_failed} newly-failed tests vs baseline ({concentration:.0%} of {total} failures).",
        ))
        signals = {
            "commit_differs": True,
            "newly_failed": newly_failed,
            "concentration": round(concentration, 3),
        }
        if compare is None:
            return {
                "status": "inconclusive",
                "confidence": 35,
                "summary": "Commit differs from baseline but the run comparison is unavailable.",
                "evidence": evidence,
                "signals": signals,
            }
        if newly_failed == 0:
            return {
                "status": "invalidated",
                "confidence": 60,
                "summary": "Commit differs from baseline but no tests newly failed — the change shows no onset signature.",
                "evidence": evidence,
                "signals": signals,
            }
        if concentration >= COMMIT_VALIDATED_CONCENTRATION:
            return {
                "status": "validated",
                "confidence": _clamp(45 + 40 * min(concentration, 1.0)),
                "summary": (
                    f"Failure onset aligns with a commit change: {newly_failed} newly-failed tests "
                    f"({concentration:.0%} of failures) appeared with commit {run_commit[:12]}."
                ),
                "evidence": evidence,
                "signals": signals,
            }
        return {
            "status": "inconclusive",
            "confidence": 45,
            "summary": (
                f"Commit differs and {newly_failed} tests newly failed, but they are only "
                f"{concentration:.0%} of failures — onset alignment is partial."
            ),
            "evidence": evidence,
            "signals": signals,
        }


# ── 3. Environment drift ──────────────────────────────────────────────────────


class EnvironmentHypothesisAgent(HypothesisAgent):
    stage_name = "hypothesis_environment"
    hypothesis_id = "environment"
    title = "Environment drift vs baseline"

    # ocp_*/environment fields whose drift is a STRONG signal. Branch drift
    # is weak (baseline selection often crosses branches by design).
    _STRONG_FIELDS = ("environment", "ocp_namespace", "ocp_node")

    @classmethod
    def evaluate(cls, bundle: dict[str, Any]) -> dict[str, Any]:
        run = bundle.get("run") or {}
        baseline = bundle.get("baseline")
        evidence: list[dict[str, Any]] = []

        if baseline is None:
            return {
                "status": "inconclusive",
                "confidence": 25,
                "summary": "No baseline run available — environment drift cannot be measured.",
                "evidence": [_ev("baseline", "No baseline", "No completed baseline run found for this project.")],
                "signals": {"baseline": None},
            }

        strong_drift: list[dict[str, Any]] = []
        for field in cls._STRONG_FIELDS:
            left, right = baseline.get(field), run.get(field)
            if left and right and str(left) != str(right):
                strong_drift.append({"field": field, "baseline": left, "run": right})
                evidence.append(_ev(
                    "env_diff", f"{field} drift", f"{field}: baseline={left} → run={right}",
                ))
        branch_drift = bool(
            (run.get("branch") or baseline.get("branch"))
            and str(run.get("branch") or "") != str(baseline.get("branch") or "")
        )
        if branch_drift:
            evidence.append(_ev(
                "env_diff",
                "Branch drift (weak signal)",
                f"branch: baseline={baseline.get('branch')} → run={run.get('branch')} "
                "(expected when baselining against main — weak signal only).",
            ))

        run_dur = run.get("duration_ms")
        base_dur = baseline.get("duration_ms")
        duration_ratio: Optional[float] = None
        if isinstance(run_dur, (int, float)) and isinstance(base_dur, (int, float)) and base_dur > 0:
            duration_ratio = float(run_dur) / float(base_dur)
        slowdown = duration_ratio is not None and duration_ratio >= ENV_SLOWDOWN_RATIO
        if duration_ratio is not None:
            evidence.append(_ev(
                "duration",
                "Whole-run duration vs baseline",
                f"run duration is {duration_ratio:.1f}× the baseline"
                + (" — broad slowdown" if slowdown else " — within normal range")
                + ".",
            ))

        failures = int((bundle.get("failure_counts") or {}).get("total") or 0)
        signals = {
            "strong_drift_fields": [d["field"] for d in strong_drift],
            "branch_drift": branch_drift,
            "duration_ratio": round(duration_ratio, 2) if duration_ratio is not None else None,
            "slowdown": slowdown,
        }
        if strong_drift or (slowdown and failures > 0):
            return {
                "status": "validated",
                "confidence": _clamp(
                    45 + 15 * len(strong_drift) + (15 if slowdown else 0) + (5 if branch_drift else 0),
                    hi=85,
                ),
                "summary": (
                    "Environment drift detected vs baseline: "
                    + (", ".join(d["field"] for d in strong_drift) or "no field drift")
                    + (f"; whole-run slowdown {duration_ratio:.1f}×" if slowdown else "")
                    + "."
                ),
                "evidence": evidence,
                "signals": signals,
            }
        if branch_drift:
            return {
                "status": "inconclusive",
                "confidence": 40,
                "summary": "Only branch drift vs baseline (a weak signal) — no environment-field drift or broad slowdown.",
                "evidence": evidence,
                "signals": signals,
            }
        return {
            "status": "invalidated",
            "confidence": 60,
            "summary": "No environment-field drift and no broad slowdown vs baseline.",
            "evidence": evidence,
            "signals": signals,
        }


# ── 4. Known-flaky recurrence ─────────────────────────────────────────────────


class KnownFlakyHypothesisAgent(HypothesisAgent):
    stage_name = "hypothesis_known_flaky"
    hypothesis_id = "known_flaky"
    title = "Known-flaky recurrence"

    @classmethod
    def evaluate(cls, bundle: dict[str, Any]) -> dict[str, Any]:
        compare = bundle.get("compare")
        failures = bundle.get("failures") or []
        newly_failed = (compare or {}).get("newly_failed_tests")
        # Without a baseline compare, fall back to ALL failing tests.
        target = newly_failed if newly_failed is not None else failures
        target_fps = {str(t.get("fingerprint")) for t in target if t.get("fingerprint")}
        flaky_fps = set(bundle.get("flaky_fingerprints") or [])
        evidence: list[dict[str, Any]] = []

        if not target_fps:
            return {
                "status": "invalidated",
                "confidence": 65,
                "summary": "No newly-failing tests — known-flaky recurrence does not apply.",
                "evidence": evidence,
                "signals": {"target": 0},
            }

        overlap_fps = target_fps & flaky_fps
        coverage = len(overlap_fps) / len(target_fps)
        overlap_tests = [t for t in target if str(t.get("fingerprint")) in overlap_fps]

        evidence.append(_ev(
            "flaky_set",
            "Known-flaky overlap",
            f"{len(overlap_fps)}/{len(target_fps)} failing tests are in the known-flaky set "
            f"(active quarantines ∪ flaky-coach cache) — {coverage:.0%} coverage.",
        ))
        if overlap_tests:
            evidence.append(_ev(
                "flaky_set",
                "Top flaky offenders",
                f"e.g. {_sample_names(overlap_tests)}",
            ))
        recall_lines = bundle.get("recall_lines") or {}
        # Deterministic order: cite recall for fingerprints that HAVE history
        # first (sorted), so the memory evidence never depends on set order.
        cited = 0
        for fp in sorted(overlap_fps, key=lambda f: (f not in recall_lines, f)):
            if cited >= _MAX_SAMPLE_NAMES:
                break
            lines = recall_lines.get(fp) or []
            if lines:
                cited += 1
                for line in lines[:2]:
                    evidence.append(_ev("memory", "Prior history", str(line)[:300]))

        signals = {
            "target_count": len(target_fps),
            "flaky_overlap": len(overlap_fps),
            "coverage": round(coverage, 3),
        }
        if coverage >= FLAKY_VALIDATED_COVERAGE:
            return {
                "status": "validated",
                "confidence": _clamp(50 + 40 * coverage, hi=90),
                "summary": (
                    f"Known flaky tests explain {coverage:.0%} of the failing set "
                    f"({len(overlap_fps)}/{len(target_fps)}) — recurrence, not a new problem."
                ),
                "evidence": evidence,
                "signals": signals,
            }
        if coverage < FLAKY_INVALIDATED_COVERAGE:
            return {
                "status": "invalidated",
                "confidence": 60,
                "summary": (
                    f"Only {coverage:.0%} of the failing set is known-flaky — flakiness does not "
                    "explain this run."
                ),
                "evidence": evidence,
                "signals": signals,
            }
        return {
            "status": "inconclusive",
            "confidence": 45,
            "summary": (
                f"Partial flaky coverage ({coverage:.0%}) — flakiness explains some but not most "
                "of the failing set."
            ),
            "evidence": evidence,
            "signals": signals,
        }


# ── 5. Genuine regression ─────────────────────────────────────────────────────


class RegressionHypothesisAgent(HypothesisAgent):
    stage_name = "hypothesis_regression"
    hypothesis_id = "regression"
    title = "Genuine product regression"

    @classmethod
    def evaluate(cls, bundle: dict[str, Any]) -> dict[str, Any]:
        compare = bundle.get("compare")
        evidence: list[dict[str, Any]] = []
        if compare is None:
            return {
                "status": "inconclusive",
                "confidence": 30,
                "summary": "No baseline comparison available — a newly-failed core cannot be isolated.",
                "evidence": [_ev("baseline", "No baseline compare", "Regression onset requires a baseline run to diff against.")],
                "signals": {"compare": None},
            }
        newly = compare.get("newly_failed_tests") or []
        newly_fps = {str(t.get("fingerprint")) for t in newly if t.get("fingerprint")}
        if not newly_fps:
            return {
                "status": "invalidated",
                "confidence": 65,
                "summary": "No newly-failed tests vs baseline — nothing regressed.",
                "evidence": [_ev("run_compare", "Newly-failed", "0 newly-failed tests vs baseline.")],
                "signals": {"newly_failed": 0},
            }

        flaky_fps = set(bundle.get("flaky_fingerprints") or [])
        infra_fps = {
            str(f.get("fingerprint"))
            for f in (bundle.get("failures") or [])
            if str(f.get("category") or "").upper() == "INFRASTRUCTURE"
        }
        core_fps = newly_fps - flaky_fps - infra_fps
        core_ratio = len(core_fps) / len(newly_fps)
        core_tests = [t for t in newly if str(t.get("fingerprint")) in core_fps]

        evidence.append(_ev(
            "run_compare",
            "Non-flaky, non-infra newly-failed core",
            f"{len(core_fps)}/{len(newly_fps)} newly-failed tests are neither known-flaky nor "
            f"infra-classified ({core_ratio:.0%}).",
        ))
        if core_tests:
            evidence.append(_ev(
                "run_compare", "Core examples", f"e.g. {_sample_names(core_tests)}",
            ))

        clusters = bundle.get("clusters") or []
        max_cluster = max(clusters, key=lambda c: int(c.get("size") or 0), default=None)
        cluster_concentrated = bool(
            max_cluster
            and core_fps
            and int(max_cluster.get("size") or 0) >= CLUSTER_CONCENTRATION_RATIO * len(core_fps)
        )
        if max_cluster:
            evidence.append(_ev(
                "cluster",
                f"Cluster '{max_cluster.get('label')}'",
                f"Largest failure cluster holds {max_cluster.get('size')} failures"
                + (" — concentrates the regression core." if cluster_concentrated else "."),
            ))

        signals = {
            "newly_failed": len(newly_fps),
            "core": len(core_fps),
            "core_ratio": round(core_ratio, 3),
            "cluster_concentrated": cluster_concentrated,
        }
        if core_fps and core_ratio >= REGRESSION_VALIDATED_CORE_RATIO:
            # Small-core dampener: a single newly-failed test is honest but
            # thin evidence for "genuine regression" — keep it validated,
            # at visibly lower confidence than broad-signal hypotheses.
            small_core_penalty = 10 if len(core_fps) == 1 else 0
            return {
                "status": "validated",
                "confidence": _clamp(
                    45 + 35 * core_ratio + (8 if cluster_concentrated else 0) - small_core_penalty,
                    hi=88,
                ),
                "summary": (
                    f"A clean regression core exists: {len(core_fps)} newly-failed tests "
                    f"({core_ratio:.0%}) are neither known-flaky nor infra-classified"
                    + (f", concentrated in cluster '{max_cluster.get('label')}'" if cluster_concentrated and max_cluster else "")
                    + "."
                ),
                "evidence": evidence,
                "signals": signals,
            }
        if core_fps:
            return {
                "status": "inconclusive",
                "confidence": 45,
                "summary": (
                    f"Some non-flaky newly-failed tests exist ({len(core_fps)}), but flaky/infra "
                    f"signals explain most of the newly-failed set ({1 - core_ratio:.0%})."
                ),
                "evidence": evidence,
                "signals": signals,
            }
        return {
            "status": "invalidated",
            "confidence": 60,
            "summary": "Every newly-failed test is explained by known-flaky or infrastructure signals.",
            "evidence": evidence,
            "signals": signals,
        }


HYPOTHESIS_AGENT_CLASSES: tuple[type[HypothesisAgent], ...] = (
    InfraHypothesisAgent,
    CommitHypothesisAgent,
    EnvironmentHypothesisAgent,
    KnownFlakyHypothesisAgent,
    RegressionHypothesisAgent,
)
