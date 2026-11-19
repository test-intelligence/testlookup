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

# One log event for every place the rule below refuses a namespace.
NAMESPACE_REFUSED_EVENT = "ocp_namespace_refused"


def outside_saved_namespace(saved: Optional[str], named: Optional[str]) -> bool:
    """True when a project's own saved namespace exists and ``named`` is another one.

    The rule for every OpenShift lookup made for a project: a project that
    saved its namespace is limited to it. The pod and namespace of a run come
    from the ingested report, so without this limit whoever holds one
    project's ingest key could name a pod in another team's namespace and
    have the server read it with the deployment's own cluster token.

    False when the project saved none (the earlier behaviour: the report's
    value is used) and when nothing is named (the saved one is the default).
    """
    own = (saved or "").strip()
    asked = (named or "").strip()
    return bool(own) and bool(asked) and asked != own


def namespace_refused_for_project(cfg: Any, namespace: Optional[str]) -> bool:
    """:func:`outside_saved_namespace` against a resolved runtime config.

    Only a namespace saved on the PROJECT limits it (``ocp_namespace_source``
    is ``project``); the deployment-wide default stays a default.
    """
    if cfg is None or getattr(cfg, "ocp_namespace_source", None) != "project":
        return False
    return outside_saved_namespace(getattr(cfg, "ocp_default_namespace", None), namespace)


async def pod_metadata_for_run(run_id: uuid.UUID) -> Optional[tuple[uuid.UUID, dict[str, Any]]]:
    """``(run_id, metadata)`` for a run that names a pod and a namespace, else ``None``.

    No session is open during the OpenShift calls. ``None`` too when the
    integration is off, offline mode is on, a name is refused, the host is
    not allowed or the API call fails (``ocp_client.get_pod_metadata``).

    ``None`` also when the project has its own saved namespace and the run
    names another one (a run stored before the ingest started dropping such
    values), and when the project's config cannot be read at all: a lookup
    that cannot be checked against the project is not made.
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
    if cfg is None:
        logger.info("ocp_enrichment_skipped", run_id=str(run_id), reason="project_config_unavailable")
        return None
    if namespace_refused_for_project(cfg, namespace):
        logger.warning(
            NAMESPACE_REFUSED_EVENT,
            run_id=str(run_id),
            project_id=str(project_id),
            reported_namespace=str(namespace).strip()[:255],
            project_namespace=cfg.ocp_default_namespace,
            action="no OpenShift lookup for this run",
        )
        return None
    meta = await ocp_client.get_pod_metadata(str(pod_name).strip(), str(namespace).strip(), config=cfg)
    if not meta:
        logger.info("ocp_enrichment_no_metadata", run_id=str(run_id))
        return None
    return run_id, meta


async def stage_ocp_metadata(db: AsyncSession, run_id: uuid.UUID, meta: dict[str, Any]) -> None:
    """Stage ``ocp_metadata`` on the run (no flush, no commit: the caller owns it)."""
    await db.execute(update(TestRun).where(TestRun.id == run_id).values(ocp_metadata=meta))
