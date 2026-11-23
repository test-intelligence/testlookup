"""MCP tools for the enterprise governance surface.

Bundles read-oriented tools across the governance features enterprise QA
Leads care about: release gate policies, saved views, digest
subscriptions, service ownership rules, and feature flag status. Keeping
these in one module avoids registering five near-empty tool files.

Write operations are deliberately NOT exposed here — governance
changes are high-stakes and should run through the Settings UI with
its audit + confirmation UX. Read-only means an AI assistant can answer
"what policy is active" and "who owns this service" without risk.
"""
from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    # ── Release gate policies ──────────────────────────────────────────────

    @mcp.tool()
    async def list_release_gate_policies(
        project_id: Optional[str] = None,
    ) -> str:
        """
        List release gate policies — the active rules that drive the
        GO/NO_GO/CONDITIONAL_GO recommendation on every ReleaseDecision.

        Args:
            project_id: Optional — the project's own policies, and the policy
                in force for it when it has none of its own (the workspace
                default). Without a project only an administrator sees the
                workspace policies.
        """
        params = {"project_id": project_id} if project_id else None
        data = await api.get(api.path("/api/v1/release-gate-policies"), params=params)
        effective = None
        if project_id and not any(isinstance(p, dict) and p.get("is_active") for p in data or []):
            # O-09: a project on the workspace default has no row of its own,
            # and the list hides workspace rows from non-admins. The policy in
            # force is still an answer.
            try:
                effective = await api.get(
                    api.path("/api/v1/release-gate-policies/effective/{project_id}", project_id=api.uuid_id(project_id))
                )
            except Exception:  # noqa: BLE001 - the list below still answers
                effective = None
        if not data and not effective:
            return (
                "No release gate policy is in force for this project: the server's built-in readiness rule decides."
                if project_id else "No release gate policies configured (or none visible without a project_id)."
            )

        lines = ["## Release gate policies\n"]
        if isinstance(effective, dict):
            origin = "workspace default" if not effective.get("project_id") else "project policy"
            lines.append(
                f"- **In force for this project:** {effective.get('name', 'Unnamed')} "
                f"v{effective.get('version', '?')} ({origin})"
            )
        for p in data or []:
            scope = "workspace default" if not p.get("project_id") else f"project `{p['project_id']}`"
            # Three states: a retired version is neither active nor a draft.
            active = "✅ active" if p.get("is_active") else ("draft" if p.get("is_draft") else "retired")
            lines.append(
                f"- **{p.get('name', 'Unnamed')}** v{p.get('version', '?')} "
                f"· {scope} · {active}"
            )
            if p.get("description"):
                lines.append(f"  - {p['description']}")
        return "\n".join(lines)

    # ── Saved views ────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_saved_views(
        project_id: Optional[str] = None,
        page: Optional[str] = None,
    ) -> str:
        """
        List saved filter views. Saved views capture a page-specific
        combination of filters (e.g. "failed tests in last 7 days on
        production branch") that users can recall with one click.

        Args:
            project_id: Optional — scope to a single project.
            page: Optional — restrict to one page. One of: dashboard, trends,
                coverage, defects, failures, summary_report, explore, search,
                test_management (any other value is refused).
        """
        params: dict = {}
        if project_id:
            params["project_id"] = project_id
        if page:
            params["page"] = page
        data = await api.get(api.path("/api/v1/saved-views"), params=params or None)
        if not data:
            return "No saved views configured."

        lines = ["## Saved views\n"]
        for v in data:
            shared = "shared" if v.get("is_shared") else "personal"
            default = " · default" if v.get("is_default") else ""
            lines.append(
                f"- **{v.get('name', 'Unnamed')}** "
                f"· page `{v.get('page') or '—'}` · {shared}{default}"
            )
        return "\n".join(lines)

    # ── Digest subscriptions ───────────────────────────────────────────────

    @mcp.tool()
    async def list_digest_subscriptions(
        project_id: Optional[str] = None,
    ) -> str:
        """
        List **your** digest subscriptions — scheduled quality reports emailed
        or posted to Slack/Teams at a cadence. The backend scopes these to the
        calling user; it does not expose other users' subscriptions.

        Args:
            project_id: Optional — scope to a single project. Filtered
                client-side, since the endpoint takes no such parameter.
        """
        # The route is /digests/subscriptions — the bare prefix 404s, and
        # client.get raises on it, so this tool always errored. Correcting the
        # path alone would have been worse: list_subscriptions declares no
        # project_id, FastAPI ignores undeclared query params, and the tool
        # would have returned unfiltered rows while promising to scope. Filter
        # on the returned rows, which carry project_id.
        data = await api.get(api.path("/api/v1/digests/subscriptions"))
        if project_id and data:
            data = [s for s in data if str(s.get("project_id") or "") == str(project_id)]
        if not data:
            return "No digest subscriptions configured."

        lines = ["## Digest subscriptions\n"]
        for s in data:
            status = "paused" if s.get("is_paused") else ("active" if s.get("is_active") else "inactive")
            lines.append(
                f"- **{s.get('name', 'Unnamed')}** · schedule `{s.get('schedule', '?')}` "
                f"· channel `{s.get('channel', '?')}` · {status}"
            )
            if s.get("next_delivery_at"):
                lines.append(f"  - Next: {(s['next_delivery_at'] or '')[:16].replace('T', ' ')}")
        return "\n".join(lines)

    # ── Ownership rules ────────────────────────────────────────────────────

    @mcp.tool()
    async def list_ownership_rules(project_id: str) -> str:
        """
        List service ownership rules for a project. Ownership rules map
        glob patterns (e.g. ``payment/*``) to a team/owner so failure
        clusters and defect promotions route to the right team
        automatically.

        Args:
            project_id: The project UUID to list rules for.
        """
        data = await api.get(api.path("/api/v1/projects/{project_id}/ownership/rules", project_id=api.uuid_id(project_id)))
        if not data:
            return f"No ownership rules for project `{project_id}`."

        # The endpoint returns a list of rules sorted by priority.
        lines = [f"## Ownership rules — project `{project_id}`\n"]
        for r in data:
            # TL-QA-15: the fields of ``OwnershipRuleResponse``. The names read
            # here before (glob_pattern, owner, component, team) are not sent,
            # so every rule showed "?" for its pattern and its owner.
            lines.append(
                f"- priority {r.get('priority', '?')} · "
                f"{r.get('match_type') or 'match'} `{r.get('match_pattern') or '?'}` → "
                f"team **{r.get('team_name') or '?'}**"
                + ("" if r.get("is_active", True) else " (inactive)")
            )
            if r.get("service_name"):
                lines.append(f"  - Service: {r['service_name']}")
            if r.get("team_contact"):
                lines.append(f"  - Contact: {r['team_contact']}")
        return "\n".join(lines)

    # ── Feature flags ──────────────────────────────────────────────────────

    @mcp.tool()
    async def list_feature_flags() -> str:
        """
        List every feature flag configured in the workspace with its
        global state, rollout percent, and scope. Admin-only on the
        backend — the MCP tool inherits that restriction via the
        caller's credentials.

        Use this to answer "is RAG enabled?" / "which tier-1 features
        are live in production?" without opening the Settings UI.
        """
        try:
            data = await api.get(api.path("/api/v1/feature-flags"))
        except Exception as exc:
            if "403" in str(exc):
                return (
                    "❌ Admin role required to list feature flags. "
                    "Ask an administrator to run this tool."
                )
            return f"❌ Failed to fetch flags: {api.error_text(exc)}"

        if not data:
            return "No feature flags configured."

        lines = ["## Feature flags\n"]
        for f in data:
            state = "🟢 ON" if f.get("enabled_global") else "⚪ OFF"
            rollout = f" · {f.get('rollout_percent', 100)}%" if f.get("enabled_global") else ""
            scope_note = ""
            if f.get("enabled_projects"):
                scope_note += f" · {len(f['enabled_projects'])} project(s)"
            if f.get("enabled_roles"):
                scope_note += f" · roles: {', '.join(f['enabled_roles'])}"
            lines.append(f"- `{f['key']}` {state}{rollout}{scope_note}")
            if f.get("description"):
                lines.append(f"  - {f['description']}")
        return "\n".join(lines)

    @mcp.tool()
    async def check_feature_flag(
        key: str,
        project_id: Optional[str] = None,
    ) -> str:
        """
        Check whether a specific feature flag is enabled for the calling
        user in the given project context. Uses the same evaluation
        logic as the backend services (global kill switch → project
        allow-list → role allow-list → rollout percent).

        Args:
            key: The flag key (e.g. ``flaky_auto_quarantine``).
            project_id: Optional project scope.
        """
        params = {"project_id": project_id} if project_id else None
        try:
            data = await api.get(api.path("/api/v1/feature-flags/{key}/status", key=key), params=params)
        except Exception as exc:
            return f"❌ Failed to check flag `{key}`: {api.error_text(exc)}"
        if data.get("enabled"):
            return f"Feature flag `{key}` is **enabled** for this caller."
        # O-10: the status route answers "off" for a key that does not exist too.
        return (
            f"Feature flag `{key}` is **not enabled** for this caller: it is off, "
            "or no flag has that key (check the spelling with `list_feature_flags`)."
        )
