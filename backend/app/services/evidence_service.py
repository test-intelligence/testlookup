"""
Evidence Service — manages evidence artifacts and provenance records.

Provides:
  - collect_evidence_for_run: gathers all evidence from pipeline outputs
  - build_enriched_provenance: constructs full provenance with confidence and sources
  - persist_provenance: saves provenance record for an entity
  - get_evidence_for_run: retrieves persisted evidence artifacts

Evidence sources are collected from:
  - AI analyses (stack traces, error messages)
  - Anomaly detection (log anomalies, metric deviations)
  - Cluster analysis (semantic groupings)
  - Baseline diff (regression classification)
  - Release risk scoring (dimension scores)
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AIProvenanceRecord,
    EvidenceArtifact,
)

logger = logging.getLogger("services.evidence")


def build_enriched_provenance(
    pipeline_meta: Optional[dict],
    analyses: list[dict],
    clusters: list[dict],
    has_baseline: bool,
    has_release_decision: bool,
    fallback_used: bool,
    generated_at: Optional[str],
) -> dict:
    """
    Build a rich provenance dict from pipeline outputs.
    Purely deterministic — no DB or LLM calls.
    """
    # Collect sources used
    sources: set[str] = set()
    deterministic_checks: set[str] = set()
    evidence_count = 0

    # From analyses
    for a in analyses:
        evidence_refs = a.get("evidence_references", [])
        evidence_count += len(evidence_refs)
        for ref in evidence_refs:
            src = ref.get("source", "")
            if src:
                sources.add(src)
        if a.get("is_flaky"):
            deterministic_checks.add("flaky_detection")

    # From pipeline metadata
    tools_used = []
    if pipeline_meta:
        tools_used = pipeline_meta.get("tools_used", [])
        for tool in tools_used:
            if "splunk" in tool.lower():
                sources.add("splunk")
            elif "stacktrace" in tool.lower():
                sources.add("stacktrace")
            elif "ocp" in tool.lower() or "openshift" in tool.lower():
                sources.add("ocp")
            elif "chroma" in tool.lower() or "embed" in tool.lower():
                sources.add("chromadb")
            elif "prometheus" in tool.lower() or "metric" in tool.lower():
                sources.add("prometheus")
            elif "github" in tool.lower() or "build" in tool.lower():
                sources.add("github")
            elif "contract" in tool.lower() or "api" in tool.lower():
                sources.add("api_contract")

    # From clusters
    if clusters:
        deterministic_checks.add("semantic_clustering")
        evidence_count += len(clusters)

    # From baseline
    if has_baseline:
        deterministic_checks.add("regression_classification")
        deterministic_checks.add("baseline_comparison")

    # From release decision
    if has_release_decision:
        deterministic_checks.add("criticality_scoring")
        deterministic_checks.add("release_risk_assessment")

    # Confidence computation
    confidence = _compute_confidence(
        evidence_count=evidence_count,
        sources_count=len(sources),
        analyses_count=len(analyses),
        has_baseline=has_baseline,
        fallback_used=fallback_used,
    )

    confidence_reason = _build_confidence_reason(
        confidence=confidence,
        evidence_count=evidence_count,
        sources_count=len(sources),
        fallback_used=fallback_used,
        has_baseline=has_baseline,
    )

    return {
        "schema_version": 2,
        "fallback_used": fallback_used,
        "generated_by": "ai_pipeline" if pipeline_meta else "deterministic",
        "tools_used_count": len(tools_used),
        "generated_at": generated_at,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "evidence_count": evidence_count,
        "sources_used": sorted(sources),
        "deterministic_checks_used": sorted(deterministic_checks),
    }


def _compute_confidence(
    evidence_count: int,
    sources_count: int,
    analyses_count: int,
    has_baseline: bool,
    fallback_used: bool,
) -> int:
    """
    Compute overall confidence 0-100 from evidence signals.

    Formula:
      base = 20 (always some value from deterministic checks)
      + min(30, evidence_count * 3)   (more evidence → higher confidence)
      + min(20, sources_count * 5)    (diverse sources → higher confidence)
      + min(15, analyses_count * 2)   (more analyses → higher confidence)
      + 10 if has_baseline            (baseline comparison adds certainty)
      - 15 if fallback_used           (LLM unavailable reduces narrative quality)
    """
    score = 20
    score += min(30, evidence_count * 3)
    score += min(20, sources_count * 5)
    score += min(15, analyses_count * 2)
    if has_baseline:
        score += 10
    if fallback_used:
        score -= 15
    return max(0, min(100, score))


def _build_confidence_reason(
    confidence: int,
    evidence_count: int,
    sources_count: int,
    fallback_used: bool,
    has_baseline: bool,
) -> str:
    """Build a human-readable explanation for the confidence score."""
    parts: list[str] = []

    if evidence_count == 0:
        parts.append("No evidence artifacts collected")
    elif evidence_count < 3:
        parts.append(f"Limited evidence ({evidence_count} items)")
    else:
        parts.append(f"{evidence_count} evidence items from {sources_count} source(s)")

    if has_baseline:
        parts.append("baseline comparison available")
    else:
        parts.append("no baseline for comparison")

    if fallback_used:
        parts.append("LLM was unavailable — deterministic analysis only")

    if confidence >= 80:
        level = "High confidence"
    elif confidence >= 50:
        level = "Moderate confidence"
    else:
        level = "Low confidence"

    return f"{level}: {'; '.join(parts)}."


async def persist_evidence_artifacts(
    db: AsyncSession,
    run_id: uuid.UUID,
    analyses: list,
    clusters: list,
) -> int:
    """
    Stage evidence artifacts from pipeline outputs.

    Idempotent: skips if evidence already exists for this run. Returns the
    count of artifacts staged. The caller owns ``db.commit()`` — typically
    this runs inside the deep-investigation pipeline handler alongside the
    analyses and clusters that produced the evidence.
    """
    # Check if already persisted
    existing = await db.execute(
        select(EvidenceArtifact.id).where(EvidenceArtifact.run_id == run_id).limit(1)
    )
    if existing.scalar_one_or_none():
        return 0

    count = 0

    # Evidence from AI analyses
    for a in analyses:
        for ref in (a.get("evidence_references") or []):
            db.add(EvidenceArtifact(
                run_id=run_id,
                test_case_id=uuid.UUID(a["test_case_id"]) if a.get("test_case_id") else None,
                artifact_type=ref.get("source", "unknown"),
                source_system=ref.get("source", "unknown"),
                summary_excerpt=(ref.get("excerpt") or "")[:500],
                uri_or_ref=ref.get("reference_id"),
            ))
            count += 1

    # Evidence from clusters
    for c in clusters:
        if c.get("representative_error"):
            db.add(EvidenceArtifact(
                run_id=run_id,
                cluster_id=c.get("cluster_id"),
                artifact_type="cluster_error",
                source_system="cluster_analysis",
                summary_excerpt=c["representative_error"][:500],
            ))
            count += 1

    return count


async def persist_provenance(
    db: AsyncSession,
    entity_type: str,
    entity_id: uuid.UUID,
    run_id: uuid.UUID,
    provenance: dict,
) -> None:
    """Stage a provenance record for an entity. Caller owns ``db.commit()``."""
    db.add(AIProvenanceRecord(
        entity_type=entity_type,
        entity_id=entity_id,
        run_id=run_id,
        model_name=provenance.get("generated_by"),
        fallback_used=provenance.get("fallback_used", False),
        confidence=provenance.get("confidence"),
        confidence_reason=provenance.get("confidence_reason"),
        evidence_count=provenance.get("evidence_count", 0),
        sources_used=provenance.get("sources_used"),
        deterministic_checks_used=provenance.get("deterministic_checks_used"),
    ))


async def get_evidence_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    cluster_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Retrieve evidence artifacts for a run, optionally filtered by cluster."""
    stmt = (
        select(EvidenceArtifact)
        .where(EvidenceArtifact.run_id == run_id)
        .order_by(EvidenceArtifact.relevance_score.desc().nulls_last())
        .limit(limit)
    )
    if cluster_id:
        stmt = stmt.where(EvidenceArtifact.cluster_id == cluster_id)

    result = await db.execute(stmt)
    return [
        {
            "id": str(e.id),
            "artifact_type": e.artifact_type,
            "source_system": e.source_system,
            "summary_excerpt": e.summary_excerpt,
            "relevance_score": e.relevance_score,
            "uri_or_ref": e.uri_or_ref,
            "cluster_id": e.cluster_id,
        }
        for e in result.scalars().all()
    ]
