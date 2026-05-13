"""
Stage 2b: Failure Clustering Agent.
Embeds all error messages for the run and groups them into semantic failure clusters.
Reduces O(n) LLM calls -> O(k) deep investigations where k << n.

Includes schema validation of clustering tool output:
  - Validates cluster_id, member_test_ids presence and types
  - Deduplicates cluster members
  - Enforces referential integrity (members must be in failed_test_ids)
  - Deterministic fallback on invalid output
"""
import json
import structlog

from app.agents.base import BaseAgent
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import TestCase

logger = structlog.get_logger("agents.cluster")


class ClusterAgent(BaseAgent):
    stage_name = "failure_clustering"

    async def run(self, state: dict) -> dict:
        pipeline_run_id: str = state["pipeline_run_id"]
        project_id: str = state["project_id"]
        failed_test_ids: list[str] = state.get("failed_test_ids", [])

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(project_id, {"status": "running", "message": "Clustering failure patterns..."})

        if not failed_test_ids:
            await self.mark_stage_done(pipeline_run_id, result_data={"clusters": 0})
            return {"failure_clusters": [], "cluster_map": {}}

        # Fetch error messages for all failed tests
        test_id_to_error: dict[str, str] = {}
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(TestCase.id, TestCase.test_name, TestCase.error_message)
                .where(TestCase.id.in_(failed_test_ids))
            )
            for row in result:
                tc_id = str(row.id)
                error = row.error_message or f"Test '{row.test_name}' failed with no error message"
                test_id_to_error[tc_id] = error

        test_ids = sorted(test_id_to_error.keys())
        errors = [test_id_to_error[tid] for tid in test_ids]
        valid_ids = set(test_ids)

        # Call the clustering tool
        fallback_reason = None
        try:
            from app.tools.embed_and_cluster import embed_and_cluster

            result_json = await embed_and_cluster.ainvoke({
                "error_messages_json": json.dumps({"test_ids": test_ids, "error_messages": errors})
            })
            result_data = json.loads(result_json)
            raw_clusters = result_data.get("clusters", [])

            # Validate and sanitize cluster output
            clusters = self._validate_clusters(raw_clusters, valid_ids)
        except Exception as exc:
            logger.warning("Clustering failed, using per-test fallback", error=str(exc))
            fallback_reason = str(exc)
            clusters = self._build_fallback_clusters(test_ids, errors)

        # Build reverse map: test_id -> cluster_id
        cluster_map: dict[str, str] = {}
        for cluster in clusters:
            for tid in cluster.get("member_test_ids", []):
                cluster_map[tid] = cluster["cluster_id"]

        # Check for orphaned tests (not in any cluster) and add them
        orphaned = valid_ids - set(cluster_map.keys())
        if orphaned:
            logger.info("Adding orphaned tests to individual clusters", count=len(orphaned))
            for i, tid in enumerate(sorted(orphaned)):
                error = test_id_to_error.get(tid, "")
                orphan_cluster = {
                    "cluster_id": f"cl_orphan_{i+1:03d}",
                    "label": error[:80] if error else "orphaned test",
                    "member_test_ids": [tid],
                    "representative_error": error[:300],
                    "size": 1,
                }
                clusters.append(orphan_cluster)
                cluster_map[tid] = orphan_cluster["cluster_id"]

        logger.info(
            "Clustering complete",
            pipeline_run_id=pipeline_run_id,
            failure_count=len(failed_test_ids),
            cluster_count=len(clusters),
            fallback=bool(fallback_reason),
        )
        await self.mark_stage_done(
            pipeline_run_id,
            result_data={
                "cluster_count": len(clusters),
                "failure_count": len(failed_test_ids),
            },
            fallback_reason=fallback_reason,
        )
        await self.broadcast_progress(project_id, {
            "status": "completed",
            "message": f"Grouped {len(failed_test_ids)} failures into {len(clusters)} clusters",
        })

        return {"failure_clusters": clusters, "cluster_map": cluster_map}

    def _validate_clusters(
        self, raw_clusters: list, valid_ids: set[str]
    ) -> list[dict]:
        """
        Validate and sanitize clustering tool output:
        - Each cluster must have a cluster_id (string) and member_test_ids (list)
        - Members must reference valid failed test IDs
        - Duplicate members are removed
        """
        validated: list[dict] = []
        seen_members: set[str] = set()

        for i, cluster in enumerate(raw_clusters):
            if not isinstance(cluster, dict):
                logger.warning("Skipping non-dict cluster entry", index=i)
                continue

            cid = cluster.get("cluster_id")
            if not cid or not isinstance(cid, str):
                cid = f"cl_auto_{i+1:03d}"
                logger.info("Auto-assigned cluster_id", original=cluster.get("cluster_id"), assigned=cid)

            raw_members = cluster.get("member_test_ids", [])
            if not isinstance(raw_members, list):
                logger.warning("Invalid member_test_ids type", cluster_id=cid, type=type(raw_members).__name__)
                raw_members = []

            # Filter to valid IDs and deduplicate
            clean_members = []
            for mid in raw_members:
                mid_str = str(mid)
                if mid_str in valid_ids and mid_str not in seen_members:
                    clean_members.append(mid_str)
                    seen_members.add(mid_str)

            if not clean_members:
                logger.debug("Skipping empty cluster after validation", cluster_id=cid)
                continue

            validated.append({
                "cluster_id": cid,
                "label": str(cluster.get("label", ""))[:200] or cid,
                "member_test_ids": clean_members,
                "representative_error": str(cluster.get("representative_error", ""))[:500],
                "size": len(clean_members),
            })

        return validated

    def _build_fallback_clusters(
        self, test_ids: list[str], errors: list[str]
    ) -> list[dict]:
        """Each test becomes its own cluster (deterministic fallback)."""
        return [
            {
                "cluster_id": f"cl_{i+1:03d}",
                "label": errors[i][:80] if i < len(errors) else "unknown",
                "member_test_ids": [test_ids[i]],
                "representative_error": errors[i][:300] if i < len(errors) else "",
                "size": 1,
            }
            for i in range(len(test_ids))
        ]
