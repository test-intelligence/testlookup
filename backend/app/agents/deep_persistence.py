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
  * ``persist_failure_cluster_snapshot`` freezes one immutable cluster set per
    producing pipeline. A retry with identical bytes is idempotent; changed
    membership under the same pipeline identity is rejected.
  * ``persist_deep_results`` persists the immutable cluster snapshot and keeps
    the latest run-level DeepFinding projection for existing consumers.

Honesty notes:
  * ``causal_chain`` / ``affected_services`` / ``contract_violations``
    stay ``None``. ``ContractAgent`` and ``LogIntelligenceAgent`` *are*
    wired into the deep graph and do produce findings, but nothing folds
    that output into these columns, so there is still no computed value
    to persist. Persisting fabricated ones would recreate the seed-only
    fiction this module removes.
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
from app.models.postgres import DeepFinding, FailureCluster, TestCase, TestStatus
from app.services.evidence_sanitizer import sanitize_reference_text

logger = structlog.get_logger("agents.deep_persistence")

ORIGIN_PIPELINE = "pipeline"
ORIGIN_SEED = "seed"

_MAX_EVIDENCE_PER_FINDING = 10
_MAX_ACTIONS_PER_FINDING = 5
_MAX_CLUSTERS_PER_SNAPSHOT = 200
_MAX_MEMBERS_PER_CLUSTER = 500


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
    """Persist immutable pipeline clusters plus the latest finding projection."""
    findings = synthesize_deep_findings(final_state)
    clusters = [
        c for c in (final_state.get("failure_clusters") or [])
        if isinstance(c, dict) and c.get("cluster_id")
    ]
    if not clusters and not findings:
        return findings

    run_uuid = uuid.UUID(str(test_run_id))
    await persist_failure_cluster_snapshot(test_run_id, pipeline_run_id, clusters)

    async with AsyncSessionLocal() as db:
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


async def persist_failure_cluster_snapshot(
    test_run_id: str,
    pipeline_run_id: str | None,
    clusters: list[dict[str, Any]],
) -> list[FailureCluster]:
    """Freeze validated clusters for one producing pipeline.

    Cluster identity is the database row plus the producing pipeline, never the
    model-generated display ``cluster_id`` alone. Existing rows may only be
    reused when every authoritative field is identical.
    """
    run_uuid = uuid.UUID(str(test_run_id))
    if pipeline_run_id is None:
        raise ValueError("pipeline_run_id is required for cluster authority")
    pipeline_uuid = uuid.UUID(str(pipeline_run_id))
    if len(clusters) > _MAX_CLUSTERS_PER_SNAPSHOT:
        raise ValueError("cluster snapshot exceeds bounded candidate count")

    normalized: dict[str, dict[str, Any]] = {}
    claimed_members: set[str] = set()
    for cluster in clusters:
        if not isinstance(cluster, dict):
            raise ValueError("cluster snapshot entries must be objects")
        cluster_id = str(cluster.get("cluster_id") or "")[:20]
        if not cluster_id or cluster_id in normalized:
            raise ValueError("cluster snapshot contains a missing or duplicate cluster_id")
        raw_members = cluster.get("member_test_ids")
        if not isinstance(raw_members, list) or not raw_members:
            raise ValueError(f"cluster {cluster_id} has no member authority")
        if len(raw_members) > _MAX_MEMBERS_PER_CLUSTER:
            raise ValueError(f"cluster {cluster_id} exceeds member bound")
        members = sorted({str(uuid.UUID(str(member))) for member in raw_members})
        if len(members) != len(raw_members):
            raise ValueError(f"cluster {cluster_id} contains duplicate members")
        overlap = claimed_members.intersection(members)
        if overlap:
            raise ValueError("cluster snapshot contains overlapping membership")
        claimed_members.update(members)
        normalized[cluster_id] = {
            "pipeline_run_id": pipeline_uuid,
            "label": sanitize_reference_text(
                str(cluster.get("label") or cluster_id), limit=500
            )[0],
            "representative_error": sanitize_reference_text(
                str(cluster.get("representative_error") or ""), limit=2_000
            )[0] or None,
            "member_test_ids": members,
            "size": len(members),
            "cohesion_score": cluster.get("cohesion_score"),
        }

    async with AsyncSessionLocal() as db:
        authorized_members = {
            str(item)
            for item in (
                await db.execute(
                    select(TestCase.id).where(
                        TestCase.test_run_id == run_uuid,
                        TestCase.id.in_([uuid.UUID(item) for item in claimed_members]),
                        TestCase.status.in_((TestStatus.FAILED, TestStatus.BROKEN)),
                    )
                )
            ).scalars().all()
        }
        if authorized_members != claimed_members:
            raise ValueError(
                "cluster snapshot contains non-failed or foreign-run members"
            )
        existing = {
            row.cluster_id: row
            for row in (
                await db.execute(
                    select(FailureCluster).where(
                        FailureCluster.test_run_id == run_uuid,
                        FailureCluster.pipeline_run_id == pipeline_uuid,
                    )
                )
            ).scalars()
        }
        if set(existing).difference(normalized):
            raise RuntimeError("persisted cluster snapshot has unexpected extra clusters")
        for cluster_id, values in normalized.items():
            row = existing.get(cluster_id)
            if row is None:
                row = FailureCluster(
                    test_run_id=run_uuid,
                    cluster_id=cluster_id,
                    **values,
                )
                db.add(row)
                continue
            observed = {
                "pipeline_run_id": row.pipeline_run_id,
                "label": row.label,
                "representative_error": row.representative_error,
                "member_test_ids": sorted(str(item) for item in (row.member_test_ids or [])),
                "size": int(row.size or 0),
                "cohesion_score": row.cohesion_score,
            }
            if observed != values:
                raise RuntimeError(f"cluster snapshot mutation rejected for {cluster_id}")
        await db.commit()
        rows = (
            await db.execute(
                select(FailureCluster).where(
                    FailureCluster.test_run_id == run_uuid,
                    FailureCluster.pipeline_run_id == pipeline_uuid,
                )
            )
        ).scalars().all()
        return list(rows)
