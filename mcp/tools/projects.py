"""Tools: list_projects, get_project, create_project."""

from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_projects() -> str:
        """
        List all active QA projects registered in TestLookup.
        Returns project names, slugs, integration keys (Jira, OCP, Splunk),
        and unique IDs needed for other tool calls.
        """
        data = await api.get("/api/v1/projects")
        if not data:
            return "No active projects found."

        lines = ["## Active Projects\n"]
        for p in data:
            lines.append(f"### {p['name']} (`{p['slug']}`)")
            lines.append(f"- **ID:** `{p['id']}`")
            if p.get("description"):
                lines.append(f"- **Description:** {p['description']}")
            if p.get("jira_project_key"):
                lines.append(f"- **Jira Key:** {p['jira_project_key']}")
            if p.get("ocp_namespace"):
                lines.append(f"- **OCP Namespace:** {p['ocp_namespace']}")
            lines.append(f"- **Created:** {p.get('created_at', 'N/A')[:10]}")
            lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    async def get_project(project_id: str) -> str:
        """
        Get full details for a specific project including all integration configuration.

        Args:
            project_id: The project UUID (from list_projects).
        """
        data = await api.get(f"/api/v1/projects/{project_id}")

        lines = [
            f"## Project: {data['name']}",
            f"- **ID:** `{data['id']}`",
            f"- **Slug:** `{data['slug']}`",
            f"- **Active:** {data.get('is_active', True)}",
            f"- **Description:** {data.get('description') or 'None'}",
            "",
            "### Integrations",
            f"- **Jira Project Key:** {data.get('jira_project_key') or 'Not configured'}",
            f"- **Splunk Index:** {data.get('splunk_index') or 'Not configured'}",
            f"- **OCP Namespace:** {data.get('ocp_namespace') or 'Not configured'}",
            f"- **Jenkins Job Pattern:** {data.get('jenkins_job_pattern') or 'Not configured'}",
            "",
            f"- **Created:** {data.get('created_at', 'N/A')}",
            f"- **Updated:** {data.get('updated_at', 'N/A')}",
        ]
        return "\n".join(lines)

    @mcp.tool()
    async def create_project(
        name: str,
        slug: str,
        description: Optional[str] = None,
        jira_project_key: Optional[str] = None,
        splunk_index: Optional[str] = None,
        ocp_namespace: Optional[str] = None,
        jenkins_job_pattern: Optional[str] = None,
    ) -> str:
        """
        Register a new project in TestLookup for test monitoring.

        Args:
            name: Human-readable project name (e.g., "Payments Service").
            slug: URL-safe identifier, lowercase, hyphens (e.g., "payments-service").
            description: Optional description of the project.
            jira_project_key: Jira project key for auto-ticket creation (e.g., "PAY").
            splunk_index: Splunk index to query for service logs.
            ocp_namespace: OpenShift namespace for pod event correlation.
            jenkins_job_pattern: Jenkins job name pattern for build detection.
        """
        body = {
            "name": name,
            "slug": slug,
            "description": description,
            "jira_project_key": jira_project_key,
            "splunk_index": splunk_index,
            "ocp_namespace": ocp_namespace,
            "jenkins_job_pattern": jenkins_job_pattern,
        }
        # Remove None values
        body = {k: v for k, v in body.items() if v is not None}

        data = await api.post("/api/v1/projects", json_body=body)

        return (
            f"Project **{data['name']}** created successfully.\n"
            f"- **ID:** `{data['id']}`\n"
            f"- **Slug:** `{data['slug']}`\n"
            f"- **Created:** {data.get('created_at', 'N/A')}\n\n"
            f"Use this project_id in other tools: `{data['id']}`"
        )
