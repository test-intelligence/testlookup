"""MCP tool for the AI decision trail — Tier 0B surface.

Exposes the ``GET /api/v1/runs/{run_id}/decision-trail`` endpoint to
AI assistants so users can ask "explain why the AI recommended X for
run Y" in natural language. The response is rendered as markdown so
Claude Desktop / IDE clients can show the timeline inline.
"""
from __future__ import annotations

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_decision_trail(run_id: str) -> str:
        """
        Fetch the AI decision trail for a completed test run.

        The trail explains *why* the AI pipeline made every decision it
        made for this run — which engine classified each test (LLM / ML /
        rules), which stages were skipped and why, which workflow router
        decisions were taken, whether any tests fell back from their
        requested engine, and what confidence adjustments were applied.

        Use this when a user asks:
          - "Why did TestLookup mark this failure as a product bug?"
          - "Why did the release gate fire NO_GO?"
          - "What did the AI do on run X?"
          - "Was there a cost budget downgrade on this run?"

        Args:
            run_id: The test run UUID to explain.
        """
        data = await api.get(f"/api/v1/runs/{run_id}/decision-trail")
        if not data:
            return f"No decision trail available for run `{run_id}`."

        lines: list[str] = []
        lines.append(f"## AI Decision Trail — Run `{run_id}`\n")

        # Pipeline header.
        if data.get("pipeline_run_id"):
            lines.append(
                f"**Pipeline run:** `{data['pipeline_run_id']}` "
                f"({data.get('workflow_type') or 'unknown'})"
            )
        lines.append(f"**Status:** {data.get('pipeline_status') or 'unknown'}")
        lines.append(f"**Started:** {data.get('started_at') or '—'}")
        lines.append(f"**Completed:** {data.get('completed_at') or '—'}")
        lines.append(f"**Total LLM cost:** ${float(data.get('total_cost_usd') or 0):.4f}")
        lines.append(f"**Total tokens:** {int(data.get('total_tokens') or 0):,}")

        # Mode distribution (per-test rollup).
        modes = data.get("mode_distribution") or {}
        if modes:
            lines.append("")
            lines.append("**Engine distribution:** " + ", ".join(
                f"{m}×{c}" for m, c in modes.items()
            ))
        fallbacks = int(data.get("fallback_count") or 0)
        if fallbacks:
            lines.append(
                f"⚠️ **{fallbacks}** test(s) fell back from their requested engine."
            )

        # Workflow-level routing events (fast-path skips, specialist
        # stage selection).
        workflow_events = data.get("workflow_events") or []
        if workflow_events:
            lines.append("\n### Workflow routing\n")
            for ev in workflow_events:
                lines.append(
                    f"- **{ev.get('decision_point', '?')}** → "
                    f"`{ev.get('chosen', '?')}` — {ev.get('rationale') or ''}"
                )
                alternatives = ev.get("alternatives") or []
                if alternatives:
                    lines.append(f"  - Rejected: {', '.join(alternatives)}")

        # Per-stage decisions.
        stages = data.get("stages") or []
        if stages:
            lines.append("\n### Pipeline stages\n")
            for stage in stages:
                icon = {
                    "completed": "✅",
                    "failed": "❌",
                    "skipped": "⏭",
                }.get(stage.get("status"), "ℹ️")
                duration = stage.get("duration_seconds")
                dur_str = f" ({duration:.2f}s)" if duration is not None else ""
                lines.append(
                    f"#### {icon} {stage.get('stage_name', '?')}{dur_str}"
                )
                mode = stage.get("analysis_mode")
                if mode:
                    lines.append(f"- **Mode:** {mode}")
                if stage.get("fallback_used"):
                    lines.append(
                        f"- ⚠️ **Fallback:** {stage.get('fallback_reason') or 'unspecified'}"
                    )
                if stage.get("skipped_reason"):
                    lines.append(f"- **Skipped reason:** {stage['skipped_reason']}")
                if stage.get("route_rationale"):
                    lines.append(f"- **Rationale:** {stage['route_rationale']}")
                if stage.get("error_category"):
                    lines.append(f"- **Error category:** {stage['error_category']}")
                cost = stage.get("cost_usd")
                if cost:
                    lines.append(f"- **Cost:** ${cost:.4f}")
                confidence = stage.get("confidence_score")
                if confidence is not None:
                    lines.append(f"- **Confidence:** {confidence}")

                decisions = stage.get("decision_log") or []
                if decisions:
                    lines.append(f"- **Decisions ({len(decisions)}):**")
                    for d in decisions[:10]:  # cap for readability
                        alts = d.get("alternatives") or []
                        alt_note = f" (rejected: {', '.join(alts)})" if alts else ""
                        tc_note = ""
                        if d.get("test_case_id"):
                            tc_note = f" [test `{d['test_case_id'][:8]}`]"
                        lines.append(
                            f"  - `{d.get('decision_point', '?')}` → "
                            f"**{d.get('chosen', '?')}**{alt_note}{tc_note} "
                            f"— {d.get('rationale') or ''}"
                        )
                    if len(decisions) > 10:
                        lines.append(f"  - …and {len(decisions) - 10} more decisions")
                lines.append("")

        # Per-test routing — surface fallbacks first.
        per_test = data.get("per_test") or []
        if per_test:
            fallback_tests = [t for t in per_test if t.get("fallback_from")]
            if fallback_tests:
                lines.append("\n### Tests that fell back\n")
                for t in fallback_tests[:15]:
                    lines.append(
                        f"- `{t.get('test_name') or t.get('test_case_id', '?')[:16]}`: "
                        f"**{t.get('fallback_from')}** → "
                        f"**{t.get('analysis_mode')}** — "
                        f"{t.get('fallback_reason') or 'unspecified'}"
                    )
                if len(fallback_tests) > 15:
                    lines.append(f"- …and {len(fallback_tests) - 15} more")

        return "\n".join(lines)
