"""
Cluster analysis service — business logic extracted from routers (P3-9).

Provides duplicate detection for failure clusters against open defects
using word-overlap (Jaccard) similarity.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Defect, FailureCluster

logger = logging.getLogger("services.cluster")


async def check_duplicate(
    run_id: str,
    cluster_id: str,
    db: AsyncSession,
) -> dict:
    """Check if a failure cluster likely duplicates an existing open defect.

    Returns a dict with cluster info and a list of potential duplicates
    ranked by word-overlap similarity.

    Raises ValueError if the cluster is not found.
    """
    result = await db.execute(
        select(FailureCluster).where(
            FailureCluster.test_run_id == run_id,
            FailureCluster.cluster_id == cluster_id,
        )
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found in run {run_id}")

    defects_result = await db.execute(
        select(Defect)
        .where(Defect.resolution_status == "OPEN")
        .order_by(Defect.created_at.desc())
        .limit(100)
    )
    open_defects = defects_result.scalars().all()

    cluster_label_lower = (cluster.label or "").lower()
    cluster_words = set(cluster_label_lower.split())

    duplicates: list[dict] = []
    for d in open_defects:
        defect_title_lower = (d.title or "").lower()
        defect_words = set(defect_title_lower.split())
        intersection = cluster_words & defect_words
        union = cluster_words | defect_words
        similarity = len(intersection) / max(len(union), 1)

        if (
            similarity > 0.3
            or cluster_label_lower in defect_title_lower
            or defect_title_lower in cluster_label_lower
        ):
            duplicates.append({
                "defect_id": str(d.id),
                "title": d.title,
                "severity": d.severity,
                "component": d.component,
                "similarity": round(similarity, 2),
                "jira_ticket_id": d.jira_ticket_id,
            })

    duplicates.sort(key=lambda x: x["similarity"], reverse=True)
    return {
        "cluster_id": cluster_id,
        "cluster_label": cluster.label,
        "potential_duplicates": duplicates[:5],
        "has_likely_duplicate": len(duplicates) > 0,
    }
