"""Deep-pipeline persistence — FailureCluster + DeepFinding rows (AI-F4).

Before this module existed the ``deep_findings`` / ``failure_clusters``
tables were written **only by the demo seed scripts**, so
``GET /deep-investigate/{run_id}/findings`` (and ``/clusters``) served
seed fiction for every real run. The deep pipeline *does* compute
findings-shaped output — ``ClusterAgent`` produces ``failure_clusters``
and the root-cause stage produces per-test ``analyses`` — it just never
persisted them. This module closes that gap:

  * ``synthesize_deep_findings`` (pure) folds each cluster's member-test
    analyses into one per-cluster finding: majority failure category,
    mean confidence, the highest-confidence member's root cause, bounded
    evidence + recommended actions.
  * ``persist_deep_results`` upserts one FailureCluster + DeepFinding row
    per ``(test_run_id, cluster_id)`` — idempotent per the repo's
    per-(entity, run) convention; re-running the pipeline updates rows in
    place instead of duplicating them.

Honesty notes:
  * ``causal_chain`` / ``affected_services`` / ``contract_violations``
    stay ``None`` — nothing in the deep graph computes them today
    (``ContractAgent`` and ``LogIntelligenceAgent`` exist but are not
    wired into any workflow node). Persisting fabricated values here
    would recreate the seed-only fiction this module removes.
  * Every row carries ``log_evidence.origin = "pipeline"``; the seed
    scripts tag theirs ``"seed"``. The findings endpoint surfaces the
    origin so consumers (and the no-seed-only-data guard test) can tell
    real analysis from demo data.

Session ownership: this runs inside the Celery worker after the LangGraph
pipeline completes (no router transaction exists), so it owns its session
and commit — same pattern as ReleaseRiskAgent's ReleaseDecision write.
"""
from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

import structlog
from sqlalchemy import select

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import DeepFinding, FailureCluster

logger = structlog.get_logger("agents.deep_persistence")

ORIGIN_PIPELINE = "pipeline"
ORIGIN_SEED = "seed"

_MAX_EVIDENCE_PER_FINDING = 10
_MAX_ACTIONS_PER_FINDING = 5


def _member_analyses(
    member_ids: list[str], analyses: dict[str, Any]
) -> list[dict]:
    """Usable analyses for a cluster's members (skip error-only entries)."""
    out: list[dict] = []
    for mid in member_ids:
        a = analyses.get(mid)
        if isinstance(a, dict) and not (a.get("error") and not a.get("root_cause_summary")):
            out.append(a)
    return out


def synthesize_deep_findings(final_state: dict[str, Any]) -> dict[str, dict]:
    """Fold per-test analyses into per-cluster findings.

    Returns ``{cluster_id: finding_dict}`` shaped for the ``deep_findings``
    table / ``state["deep_findings"]``. Pure — no I/O.
    """
    clusters = final_state.get("failure_clusters") or []
    analyses = final_state.get("analyses") or {}
    findings: dict[str, dict] = {}

    for cluster in clusters:
        if not isinstance(cluster, dict) or not cluster.get("cluster_id"):
            continue
        cid = str(cluster["cluster_id"])
        member_ids = [str(m) for m in (cluster.get("member_test_ids") or [])]
        members = _member_analyses(member_ids, analyses)

        # Majority failure category across members (stable tie-break by name).
        categories = [
            str(a.get("failure_category"))
            for a in members
            if a.get("failure_category")
        ]
        category = (
            sorted(Counter(categories).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            if categories else None
        )

        confidences = [
            int(a["confidence_score"])
            for a in members
            if isinstance(a.get("confidence_score"), (int, float))
        ]
        confidence = int(sum(confidences) / len(confidences)) if confidences else None

        # Root cause: the highest-confidence member's summary; fall back to
        # the cluster's representative error when no member has one.
        best = max(
            members,
            key=lambda a: a.get("confidence_score") or 0,
            default=None,
        )
        root_cause = (best or {}).get("root_cause_summary") or (
            cluster.get("representative_error") or None
        )

        evidence: list[dict] = []
        actions: list[str] = []
        bases: list[str] = []
        for a in members:
            for ref in a.get("evidence_references") or []:
                if isinstance(ref, dict) and len(evidence) < _MAX_EVIDENCE_PER_FINDING:
                    evidence.append(ref)
            for act in a.get("recommended_actions") or []:
                if act not in actions and len(actions) < _MAX_ACTIONS_PER_FINDING:
                    actions.append(act)
            if a.get("confidence_basis"):
                bases.append(str(a["confidence_basis"]))

        # Basis of the folded confidence: conservative — if ANY member is a
        # heuristic estimate the aggregate is at best a heuristic estimate.
        basis = (
            "heuristic_estimate"
            if "heuristic_estimate" in bases
            else (bases[0] if bases else None)
        )

        findings[cid] = {
            "cluster_id": cid,
            "root_cause": root_cause,
            "failure_category": category,
            "confidence_score": confidence,
            # Honest None: no wired agent computes these (see module docstring).
            "causal_chain": None,
            "evidence": evidence or None,
            "affected_services": None,
            "contract_violations": None,
            "recommended_actions": actions or None,
            "log_evidence": {
                "origin": ORIGIN_PIPELINE,
                "synthesized_from": "member_test_analyses",
                "member_count": len(members),
                **({"confidence_basis": basis} if basis else {}),
            },
        }
    return findings


async def persist_deep_results(
    test_run_id: str,
    pipeline_run_id: str | None,
    final_state: dict[str, Any],
) -> dict[str, dict]:
    """Upsert FailureCluster + DeepFinding rows for a completed deep run.

    Idempotent per (test_run_id, cluster_id): re-running the deep pipeline
    for the same run updates existing rows (including replacing stale seed
    rows for that cluster_id) rather than inserting duplicates.
    Returns the synthesized findings map for ``state["deep_findings"]``.
    """
    findings = synthesize_deep_findings(final_state)
    clusters = [
        c for c in (final_state.get("failure_clusters") or [])
        if isinstance(c, dict) and c.get("cluster_id")
    ]
    if not clusters and not findings:
        return findings

    run_uuid = uuid.UUID(str(test_run_id))
    pr_uuid: uuid.UUID | None = None
    if pipeline_run_id:
        try:
            pr_uuid = uuid.UUID(str(pipeline_run_id))
        except ValueError:
            pr_uuid = None

    async with AsyncSessionLocal() as db:
        # ── Clusters ─────────────────────────────────────────────────
        existing_clusters = {
            row.cluster_id: row
            for row in (
                await db.execute(
                    select(FailureCluster).where(FailureCluster.test_run_id == run_uuid)
                )
            ).scalars()
        }
        for c in clusters:
            cid = str(c["cluster_id"])[:20]
            member_ids = [str(m) for m in (c.get("member_test_ids") or [])]
            values = {
                "pipeline_run_id": pr_uuid,
                "label": (str(c.get("label") or cid))[:500],
                "representative_error": c.get("representative_error"),
                "member_test_ids": member_ids,
                "size": int(c.get("size") or len(member_ids) or 1),
                "cohesion_score": c.get("cohesion_score"),
            }
            row = existing_clusters.get(cid)
            if row is not None:
                for key, val in values.items():
                    setattr(row, key, val)
            else:
                db.add(FailureCluster(test_run_id=run_uuid, cluster_id=cid, **values))

        # ── Findings ─────────────────────────────────────────────────
        existing_findings = {
            row.cluster_id: row
            for row in (
                await db.execute(
                    select(DeepFinding).where(DeepFinding.test_run_id == run_uuid)
                )
            ).scalars()
        }
        for cid, f in findings.items():
            cid_db = cid[:20]
            values = {
                "root_cause": f["root_cause"],
                "failure_category": f["failure_category"],
                "confidence_score": f["confidence_score"],
                "causal_chain": f["causal_chain"],
                "evidence": f["evidence"],
                "affected_services": f["affected_services"],
                "contract_violations": f["contract_violations"],
                "recommended_actions": f["recommended_actions"],
                "log_evidence": f["log_evidence"],
            }
            row = existing_findings.get(cid_db)
            if row is not None:
                for key, val in values.items():
                    setattr(row, key, val)
            else:
                db.add(DeepFinding(test_run_id=run_uuid, cluster_id=cid_db, **values))

        await db.commit()

    logger.info(
        "deep_results_persisted",
        test_run_id=str(test_run_id),
        cluster_count=len(clusters),
        finding_count=len(findings),
    )
    return findings
