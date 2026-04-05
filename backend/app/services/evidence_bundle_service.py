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

from app.models.postgres import EvidenceArtifact, RunIntelligenceSnapshot
from app.services.report_composition_service import compose_report
from app.services.report_pdf_renderer import render_report_pdf

logger = logging.getLogger("services.evidence_bundle")


async def build_evidence_bundle(
    db: AsyncSession,
    run_id: uuid.UUID,
    include_pdf: bool = True,
) -> bytes:
    """
    Build a ZIP archive containing:
      - intelligence-report.pdf (engineering layout)
      - intelligence-snapshot.json (full cached payload)
      - evidence/ directory with individual evidence artifacts
      - release-decision.json (extracted from snapshot)
      - provenance.json (extracted from snapshot)

    Returns ZIP bytes.
    """
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── Intelligence snapshot JSON ──────────────────────────────────
        snapshot_result = await db.execute(
            select(RunIntelligenceSnapshot).where(RunIntelligenceSnapshot.run_id == run_id)
        )
        snapshot = snapshot_result.scalar_one_or_none()
        payload = snapshot.payload if snapshot else {}

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

        # ── Evidence artifacts from DB ──────────────────────────────────
        evidence_result = await db.execute(
            select(EvidenceArtifact)
            .where(EvidenceArtifact.run_id == run_id)
            .order_by(EvidenceArtifact.created_at)
        )
        artifacts = evidence_result.scalars().all()
        for artifact in artifacts:
            artifact_data = {
                "id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "source_system": artifact.source_system,
                "uri_or_ref": artifact.uri_or_ref,
                "summary_excerpt": artifact.summary_excerpt,
                "relevance_score": artifact.relevance_score,
                "cluster_id": artifact.cluster_id,
                "test_case_id": str(artifact.test_case_id) if artifact.test_case_id else None,
            }
            safe_type = (artifact.artifact_type or "unknown").replace("/", "_")
            zf.writestr(
                f"evidence/{safe_type}_{str(artifact.id)[:8]}.json",
                json.dumps(artifact_data, indent=2, default=str),
            )

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
