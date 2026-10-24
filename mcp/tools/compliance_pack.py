"""MCP tools for release compliance packs — Tier 1 item 4 surface.

Exposes list + generate + verify (W11 REL-11). Download is deliberately NOT
exposed as a tool because ZIP bytes do not fit into an MCP tool response —
AI assistants should direct users to the Release Gate page to download via
the browser instead. ``verify_compliance_pack`` is a POST that writes
nothing: the server checks the stored ZIP and answers.
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

        Produces a ZIP containing the release snapshot, AI decision
        trail, active ReleaseGatePolicy version, failure clusters,
        defects with Jira links, and all audit events relevant to the
        decision. The ZIP lives in MinIO with a 7-year retention window
        (configurable) and every file is SHA-256 hashed into the manifest
        for tamper detection. The pack is tamper-EVIDENT, not tamper-proof.
        Signed since W11: a new pack carries signature.json, an Ed25519
        signature by the deployment's key over the manifest's SHA-256, the
        pack id and the key id. Signing proves the deployment's key signed
        this exact manifest; the private key is stored encrypted beside the
        data, so it defends against storage-only tampering, not against an
        actor holding both the database and APP_SECRET_KEY. Check a pack with
        `verify_compliance_pack`.

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

    @mcp.tool()
    async def verify_compliance_pack(pack_id: str) -> str:
        """
        Verify a stored compliance pack: every file against its manifest, the
        manifest against the digest recorded when the pack was generated, and
        the Ed25519 signature (packs generated since W11).

        Read-only: the server checks the stored ZIP and writes nothing.
        Results: `verified` (all files match; the signature is valid, or the
        pack predates signing and is tamper-evident only), `tampered` (the
        files that do not match are listed), or `unreadable` (the pack could
        not be read from storage — not a verdict on the pack).

        Use this when a user asks:
          - "Has this audit pack been tampered with?"
          - "Is the compliance pack for release X signed?"
          - "Verify pack <id>"

        Args:
            pack_id: The compliance pack UUID (from list_compliance_packs).
        """
        try:
            result = await api.post(f"/api/v1/compliance-packs/{pack_id}/verify")
        except Exception as exc:
            return f"❌ Failed to verify compliance pack: {exc}"
        if not result:
            return "❌ No verification returned from the backend."
        return render_verification(result)


#: The non-ok file statuses as the panel and the CLI word them.
_STATUS_PHRASES = {
    "mismatch": "does not match the manifest",
    "missing": "is missing from the pack",
    "unexpected": "is not listed in the manifest",
}


def render_verification(result: dict) -> str:
    """Markdown for one verification answer: the summary, then each non-ok file."""
    lines = [
        f"## Compliance pack `{result.get('pack_id', '')}`",
        "",
        f"**{result.get('summary', '')}**",
        "",
        f"- **Result:** {result.get('result', '')}",
        f"- **Checksum chain:** {result.get('chain', '')}",
        (
            f"- **Manifest matches the recorded digest:** "
            f"{'yes' if result.get('manifest_matches_record') else 'no'}"
        ),
        f"- **Signature:** {result.get('signature', '')}"
        + (f" (key `{result['signing_key_id']}`)" if result.get("signing_key_id") else ""),
        f"- **Files:** {result.get('files_ok', 0)} of {result.get('files_checked', 0)} match",
    ]
    bad = [f for f in (result.get("files") or []) if f.get("status") != "ok"]
    if bad:
        lines.append("")
        lines.append("### Files that do not match")
        for f in bad:
            phrase = _STATUS_PHRASES.get(str(f.get("status")), str(f.get("status")))
            lines.append(f"- `{f.get('name')}` {phrase}")
    return "\n".join(lines)
