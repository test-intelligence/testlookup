"""OpenShift pod metadata for an ingested run, fetched after the ingest commits (W10 INT-03).

The MinIO sentinel ingest used to call the OpenShift API inside its own
transaction, holding a pooled connection (and an open transaction) across an
HTTP call of up to 3 x 10 s. Now the ingest stores the run's namespace, commits,
and enqueues ``worker.tasks.enrich_run_ocp_metadata``, which uses this module:

1. ``pod_metadata_for_run`` -- a short session READS the run's pod, namespace
   and project and is closed; the runtime integration config is resolved (the
   bound config, the 30 s cache, or its own short session); then the pod and
   events GETs run with NO session open;
2. ``stage_ocp_metadata`` -- stages the one UPDATE on the worker task's own
   session. Stage-only: the worker task owns the commit (the services' commit
   allow-list is at its bound).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import TestRun
from app.services import ocp_client
from app.services.integration_config_service import runtime_integrations_for

logger = structlog.get_logger("services.ocp_enrichment")


async def pod_metadata_for_run(run_id: uuid.UUID) -> Optional[tuple[uuid.UUID, dict[str, Any]]]:
    """``(run_id, metadata)`` for a run that names a pod and a namespace, else ``None``.

    No session is open during the OpenShift calls. ``None`` too when the
    integration is off, offline mode is on, a name is refused, the host is
    not allowed or the API call fails (``ocp_client.get_pod_metadata``).
    """
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(TestRun.ocp_pod_name, TestRun.ocp_namespace, TestRun.project_id).where(TestRun.id == run_id)
        )).first()
    if row is None:
        return None
    pod_name, namespace, project_id = row[0], row[1], row[2]
    if not (pod_name or "").strip() or not (namespace or "").strip():
        return None
    cfg = await runtime_integrations_for(project_id)
    meta = await ocp_client.get_pod_metadata(str(pod_name).strip(), str(namespace).strip(), config=cfg)
    if not meta:
        logger.info("ocp_enrichment_no_metadata", run_id=str(run_id))
        return None
    return run_id, meta


async def stage_ocp_metadata(db: AsyncSession, run_id: uuid.UUID, meta: dict[str, Any]) -> None:
    """Stage ``ocp_metadata`` on the run (no flush, no commit: the caller owns it)."""
    await db.execute(update(TestRun).where(TestRun.id == run_id).values(ocp_metadata=meta))
