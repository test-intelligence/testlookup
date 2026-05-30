"""MCP tools for release compliance packs — Tier 1 item 4 surface.

Exposes list + generate. Download is deliberately NOT exposed as a tool
because ZIP bytes do not fit into an MCP tool response — AI assistants
should direct users to the Release Gate page to download via the
browser instead.
"""
from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_compliance_packs(release_id: str) -> str:
        """
        List every compliance pack ever generated for a release.

        Returns each pack's id, generated_at, size, file count, SHA-256
        manifest hash, and optional notes. The ZIP bytes are not
        returned — use the UI link to download.

        Use this when a user asks:
          - "Show me the audit packs for release X"
          - "When was the last compliance pack generated?"
          - "What's the manifest hash of that audit zip?"

        Args:
            release_id: The release UUID to list packs for.
        """
        data = await api.get(f"/api/v1/releases/{release_id}/compliance-packs")
        if not data:
            return f"No compliance packs found for release `{release_id}`."

        lines = [f"## Compliance packs for release `{release_id}`\n"]
        for p in data:
            generated_at = (p.get("generated_at") or "")[:19].replace("T", " ")
            size_kb = float(p.get("bytes") or 0) / 1024
            lines.append(f"### Pack `{p['id']}`")
            lines.append(f"- **Generated:** {generated_at}")
            lines.append(f"- **Size:** {size_kb:.1f} KB · {p.get('file_count', 0)} files")
            lines.append(f"- **Manifest SHA-256:** `{p.get('manifest_sha256', '')}`")
            lines.append(f"- **Retention expires:** {(p.get('retention_expires_at') or '')[:10]}")
            if p.get("notes"):
                lines.append(f"- **Notes:** {p['notes']}")
            meta = p.get("metadata_snapshot") or {}
            if meta.get("recommendation"):
                lines.append(
                    f"- **Captured decision:** {meta['recommendation']} "
                    f"(risk {meta.get('risk_score', '?')})"
                )
            lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    async def generate_compliance_pack(
        release_id: str,
        notes: Optional[str] = None,
        retention_days: Optional[int] = None,
    ) -> str:
        """
        Generate a new release compliance export pack for a release.

        Produces a signed ZIP containing the release snapshot, AI decision
        trail, active ReleaseGatePolicy version, failure clusters,
        defects with Jira links, and all audit events relevant to the
        decision. The ZIP lives in MinIO with a 7-year retention window
        (configurable) and every file is SHA-256 hashed into the manifest
        for tamper detection.

        Requires QA_LEAD+ role on the release's project. Returns the
        pack metadata — download via the Release Gate page UI.

        Args:
            release_id: The release UUID to generate a pack for.
            notes: Optional human notes recorded on the pack row.
            retention_days: Override the default 7-year retention window.
        """
        body: dict = {}
        if notes is not None:
            body["notes"] = notes
        if retention_days is not None:
            body["retention_days"] = retention_days

        try:
            pack = await api.post(
                f"/api/v1/releases/{release_id}/compliance-pack",
                json_body=body,
            )
        except Exception as exc:
            return f"❌ Failed to generate compliance pack: {exc}"

        if not pack:
            return "❌ No pack returned from the backend."

        size_kb = float(pack.get("bytes") or 0) / 1024
        return (
            f"✅ **Compliance pack generated**\n\n"
            f"- **Pack ID:** `{pack.get('id')}`\n"
            f"- **Size:** {size_kb:.1f} KB · {pack.get('file_count', 0)} files\n"
            f"- **Manifest SHA-256:** `{pack.get('manifest_sha256', '')}`\n"
            f"- **Retention expires:** "
            f"{(pack.get('retention_expires_at') or '')[:10]}\n"
            f"\nDownload from the Release Gate page — ZIP bytes are not "
            f"returned in MCP tool responses."
        )
