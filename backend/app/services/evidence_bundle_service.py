"""
Evidence Bundle Service — packages intelligence data, evidence, and PDF into a ZIP archive.
"""
from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import RunIntelligenceSnapshot, TestRun
from app.services.report_composition_service import compose_report
from app.services.report_export_sanitizer import sanitize_report_export_payload
from app.services.report_pdf_renderer import render_report_pdf

logger = logging.getLogger("services.evidence_bundle")


async def build_evidence_bundle(
    db: AsyncSession,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    include_pdf: bool = True,
) -> bytes:
    """
    Build a ZIP archive containing:
      - intelligence-report.pdf (engineering layout)
      - intelligence-snapshot.json (safe, evidence-free projection)
      - release-decision.json (extracted from snapshot)
      - provenance.json (extracted from snapshot)

    Returns ZIP bytes.
    """
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── Intelligence snapshot JSON ──────────────────────────────────
        snapshot_result = await db.execute(
            select(RunIntelligenceSnapshot)
            .join(TestRun, TestRun.id == RunIntelligenceSnapshot.run_id)
            .where(
                RunIntelligenceSnapshot.run_id == run_id,
                TestRun.project_id == project_id,
            )
        )
        snapshot = snapshot_result.scalar_one_or_none()
        payload = sanitize_report_export_payload(snapshot.payload) if snapshot else {}

        zf.writestr(
            "intelligence-snapshot.json",
            json.dumps(payload, indent=2, default=str),
        )

        # ── Release decision JSON ───────────────────────────────────────
        decision = payload.get("release_decision") or {}
        zf.writestr(
            "release-decision.json",
            json.dumps(decision, indent=2, default=str),
        )

        # ── Provenance JSON ─────────────────────────────────────────────
        provenance = payload.get("provenance") or {}
        zf.writestr(
            "provenance.json",
            json.dumps(provenance, indent=2, default=str),
        )

        # ── Cluster details ─────────────────────────────────────────────
        clusters = payload.get("failure_clusters") or []
        for i, cluster in enumerate(clusters[:20]):
            cluster_id = cluster.get("cluster_id", f"cl_{i:03d}")
            zf.writestr(
                f"clusters/{cluster_id}.json",
                json.dumps(cluster, indent=2, default=str),
            )

        # Restricted artifact excerpts are deliberately not exported until a
        # published-snapshot allowlist and dedicated evidence permission are
        # available. The signed report retains opaque IDs/checksums only.

        # ── PDF report (engineering layout) ─────────────────────────────
        if include_pdf:
            try:
                report = await compose_report(db, run_id, layout="engineering")
                pdf_bytes = render_report_pdf(report)
                zf.writestr("intelligence-report.pdf", pdf_bytes)
            except Exception as exc:
                logger.warning("PDF generation failed for bundle: %s", exc)
                zf.writestr("intelligence-report-error.txt", f"PDF generation failed: {exc}")

    buf.seek(0)
    return buf.getvalue()
