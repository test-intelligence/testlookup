"""MCP tools for LLM cost budget + billing — Tier 1 item 2 surface.

Exposes the workspace overview, per-project usage, per-project quota
config. Write operations (PUT quota) are NOT exposed via MCP — billing
config is too high-stakes to adjust from a chat prompt; admins use the
Settings > LLM Cost Budget page instead.
"""
from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_billing_overview() -> str:
        """
        Return workspace-wide LLM cost overview for the current billing
        period (calendar month UTC).

        Shows total spend, total LLM calls, per-project utilization
        with caps, and which projects are in SOFT_WARN or CAPPED state.
        Use this to answer "what's our LLM bill looking like this
        month?" and "which projects are close to their cap?"
        """
        data = await api.get("/api/v1/billing/overview")
        if not data:
            return "No billing data available."

        lines = [
            "## LLM Cost Budget — workspace overview",
            f"**Period:** {(data.get('period_start') or '')[:10]} — "
            f"{(data.get('period_end') or '')[:10]}",
            f"**Total spend:** ${float(data.get('total_cost_usd') or 0):.4f}",
            f"**Total LLM calls:** {int(data.get('total_llm_calls') or 0):,}",
        ]

        projects = data.get("projects") or []
        if not projects:
            lines.append("\n_No projects have LLM usage this period._")
            return "\n".join(lines)

        capped = [p for p in projects if p.get("status") == "CAPPED"]
        warn = [p for p in projects if p.get("status") == "SOFT_WARN"]
        if capped or warn:
            lines.append("\n### ⚠️ Projects needing attention\n")
            for p in capped + warn:
                util = p.get("utilization_pct")
                util_str = f"{util:.0f}%" if util is not None else "—"
                cap_str = (
                    f"${p['hard_cap_usd']:.2f}"
                    if p.get("hard_cap_usd") is not None
                    else "no cap"
                )
                lines.append(
                    f"- **{p['project_name']}** · `{p['status']}` · "
                    f"${p['current_cost_usd']:.4f} of {cap_str} ({util_str}) · "
                    f"cap hits: {p.get('cap_hits', 0)}"
                )

        ok = [
            p for p in projects
            if p.get("status") not in ("CAPPED", "SOFT_WARN")
        ]
        if ok:
            lines.append("\n### All projects\n")
            for p in ok[:20]:
                cap_str = (
                    f" / ${p['hard_cap_usd']:.2f}"
                    if p.get("hard_cap_usd") is not None
                    else " (unlimited)"
                )
                lines.append(
                    f"- **{p['project_name']}** · "
                    f"${p['current_cost_usd']:.4f}{cap_str} · `{p['status']}`"
                )
            if len(ok) > 20:
                lines.append(f"- …and {len(ok) - 20} more")
        return "\n".join(lines)

    @mcp.tool()
    async def get_project_llm_usage(project_id: str) -> str:
        """
        Return current-period LLM usage for a single project — cost,
        token counts, cap utilization, cap-hit count.

        Args:
            project_id: The project UUID to query.
        """
        data = await api.get(f"/api/v1/projects/{project_id}/llm-usage")
        if not data:
            return "No usage data returned."

        lines = [
            f"## LLM usage — project `{project_id}`",
            f"**Period:** {(data.get('period_start') or '')[:10]} — "
            f"{(data.get('period_end') or '')[:10]}",
            f"**Status:** {data.get('status', 'unknown')}",
            f"**Total spend:** ${float(data.get('total_cost_usd') or 0):.4f}",
        ]
        if data.get("included_usd") is not None:
            lines.append(f"**Included:** ${data['included_usd']:.2f}")
        if data.get("hard_cap_usd") is not None:
            lines.append(f"**Hard cap:** ${data['hard_cap_usd']:.2f}")
        if data.get("utilization_pct") is not None:
            lines.append(f"**Utilization:** {data['utilization_pct']:.1f}%")
        lines.append(f"**Input tokens:** {int(data.get('total_input_tokens') or 0):,}")
        lines.append(f"**Output tokens:** {int(data.get('total_output_tokens') or 0):,}")
        lines.append(f"**LLM calls:** {int(data.get('total_llm_calls') or 0):,}")
        lines.append(f"**Cap hits this period:** {data.get('cap_hits', 0)}")
        return "\n".join(lines)

    @mcp.tool()
    async def get_project_llm_quota(project_id: str) -> str:
        """
        Return the LLM cost budget config for a project — included
        dollars, hard cap, at-cap action, enabled flag.

        Returns a helpful "no quota configured" message when the project
        has not had a budget set.

        Args:
            project_id: The project UUID to query.
        """
        try:
            data = await api.get(f"/api/v1/projects/{project_id}/llm-quota")
        except Exception as exc:
            # 404 is expected when no quota exists — render a clean message.
            if "404" in str(exc):
                return (
                    f"No LLM cost budget configured for project `{project_id}`. "
                    f"Use Settings > LLM Cost Budget to set one."
                )
            return f"❌ Failed to fetch quota: {exc}"

        return "\n".join([
            f"## LLM quota — project `{project_id}`",
            f"- **Enabled:** {data.get('enabled', False)}",
            f"- **Included:** ${data.get('included_usd', 0):.2f} / "
            f"{data.get('period_type', 'MONTHLY').lower()}",
            f"- **Hard cap:** ${data.get('hard_cap_usd', 0):.2f}",
            f"- **Overage rate:** ${data.get('overage_rate_usd', 0):.2f}/$ over included",
            f"- **Soft-warn threshold:** {data.get('soft_warn_threshold_pct', 100)}%",
            f"- **At-cap action:** `{data.get('at_cap_action', '?')}`",
        ])
