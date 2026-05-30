"""
Defect Promotion Service.

Assembles a defect candidate from a failure cluster and its deep-investigation
findings, detects duplicates via ChromaDB semantic search, and persists the
promoted defect record.

Separates the stateless candidate assembly (used by GET) from the side-effectful
persist+Jira step (used by POST).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid as _uuid
from typing import Any, Optional, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AIAnalysis,
    Defect,
    DeepFinding,
    FailureCategory,
    FailureCluster,
    TestCase,
    TestRun,
)
from app.core.metrics import defect_promotions_total
from app.services.action_policy import (
    ActionStatus,
    check_defect_promotion_policy,
)
from app.services.criticality_service import get_scoring_model_info, score_cluster

logger = logging.getLogger("services.defect_promotion")

_DEFECT_PROMPT = """\
You are a senior QA lead promoting a failure cluster to a defect ticket.

Cluster Information:
{cluster_json}

Root Cause Analyses for member tests:
{analyses_json}

Evidence:
{evidence_json}

Produce a Jira-ready defect in JSON format:
{{
  "title": "concise defect title (max 80 chars)",
  "description": "structured defect description with: What/Steps to reproduce/Expected/Actual/Environment",
  "severity": "CRITICAL | HIGH | MEDIUM | LOW",
  "component": "affected component or service name",
  "owner_team": "probable team responsible (e.g. payments-backend, auth-service, frontend)",
  "labels": ["regression", "automated-test", "cluster-promoted"],
  "duplicate_hint": "brief description to help detect similar open tickets (for dedup query)"
}}"""


async def get_llm(*args, **kwargs):
    """Lazy LLM factory import so pure defect helpers work without LangChain."""
    from app.services.llm_factory import get_llm as _factory_get_llm

    return await _factory_get_llm(*args, **kwargs)


def _failure_category_value(category: Any) -> str:
    """Normalize FailureCategory values from either enum-backed or raw-string rows."""
    if not category:
        return "UNKNOWN"
    return str(getattr(category, "value", category))


async def get_defect_candidate(
    run_id: str,
    cluster_id: str,
    db: AsyncSession,
) -> dict:
    """
    Assemble a pre-filled defect candidate for a cluster.
    No side effects — used by GET endpoint to pre-populate the promotion modal.
    """
    # Load cluster
    cluster_result = await db.execute(
        select(FailureCluster).where(
            FailureCluster.test_run_id == _uuid.UUID(run_id),
            FailureCluster.cluster_id == cluster_id,
        )
    )
    cluster = cluster_result.scalar_one_or_none()
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found for run {run_id}")

    # Load run (for project_id)
    run_result = await db.execute(select(TestRun).where(TestRun.id == _uuid.UUID(run_id)))
    run = run_result.scalar_one_or_none()
    if not run:
        raise ValueError(f"TestRun {run_id} not found")

    member_ids = cluster.member_test_ids or []

    # Load analyses
    analyses: list[AIAnalysis] = []
    if member_ids:
        analyses_result = await db.execute(
            select(AIAnalysis)
            .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
            .where(TestCase.id.in_(member_ids))
            .limit(10)
        )
        analyses = list(analyses_result.scalars().all())

    # Load deep finding for this cluster
    finding_result = await db.execute(
        select(DeepFinding).where(
            DeepFinding.test_run_id == _uuid.UUID(run_id),
            DeepFinding.cluster_id == cluster_id,
        )
    )
    finding = finding_result.scalar_one_or_none()

    # Compute dimension scores using criticality_service
    model_info = get_scoring_model_info()
    total_run_analyses = max(len(member_ids), 1)
    all_dim_scores = _dim_scores_from_analyses(analyses)
    dim_scores = score_cluster(
        cluster={"member_test_ids": member_ids},
        all_dim_scores=all_dim_scores,
        total_analyses=total_run_analyses,
        member_count=len(member_ids),
    )
    composite = sum(
        dim_scores.get(d["name"], 0) * d["weight"]
        for d in model_info["dimensions"]
    )
    composite = round(composite, 1)
    severity = _composite_to_severity(composite)

    # Build evidence bundle
    evidence_bundle = _build_evidence_bundle(analyses, finding)

    # LLM-generated Jira content (with fallback)
    jira_content = await _generate_jira_content(cluster, analyses)
    memory_ownership = await _resolve_defect_owner_from_memory(
        db,
        project_id=str(run.project_id),
        cluster_id=cluster_id,
        component=jira_content.get("component"),
        member_test_ids=[str(member_id) for member_id in member_ids],
    )
    if memory_ownership and jira_content.get("owner_team") in {None, "", "Unknown"}:
        jira_content["owner_team"] = memory_ownership.get("team_name") or "Unknown"
    if memory_ownership and jira_content.get("component") in {None, "", "Unknown"}:
        jira_content["component"] = memory_ownership.get("service_name") or "Unknown"

    # Semantic duplicate detection
    duplicate_id, duplicate_detected = await _find_duplicate_semantic(
        project_id=str(run.project_id),
        duplicate_hint=jira_content.get("duplicate_hint", cluster.label),
        db=db,
    )

    return {
        "cluster_id": cluster_id,
        "run_id": run_id,
        "title": jira_content.get("title", cluster.label[:80]),
        "description": jira_content.get(
            "description",
            f"Cluster '{cluster.label}' with {cluster.size} failures.",
        ),
        "severity": jira_content.get("severity", severity),
        "component": jira_content.get("component", "Unknown"),
        "owner_team": jira_content.get("owner_team", "Unknown"),
        "labels": jira_content.get("labels", ["automated-test", "cluster-promoted"]),
        "duplicate_hint": jira_content.get("duplicate_hint", cluster.label[:100]),
        "duplicate_detected": duplicate_detected,
        "duplicate_defect_id": duplicate_id,
        "criticality_scores": dim_scores,
        "composite_score": composite,
        "evidence_bundle": evidence_bundle,
        "failure_category": (
            _failure_category_value(analyses[0].failure_category)
            if analyses
            else "UNKNOWN"
        ),
        "member_count": cluster.size or len(member_ids),
    }


async def promote_cluster(
    run_id: str,
    cluster_id: str,
    project_id: str,
    request: dict,
    db: AsyncSession,
) -> dict:
    """
    Persist a promoted defect from a cluster.
    Called by POST /runs/{run_id}/clusters/{cluster_id}/promote.
    """
    # Load cluster for evidence bundle
    cluster_result = await db.execute(
        select(FailureCluster).where(
            FailureCluster.test_run_id == _uuid.UUID(run_id),
            FailureCluster.cluster_id == cluster_id,
        )
    )
    cluster = cluster_result.scalar_one_or_none()
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found for run {run_id}")

    member_ids = cluster.member_test_ids or []

    # Load analyses for evidence bundle
    analyses: list[AIAnalysis] = []
    if member_ids:
        analyses_result = await db.execute(
            select(AIAnalysis)
            .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
            .where(TestCase.id.in_(member_ids))
            .limit(10)
        )
        analyses = list(analyses_result.scalars().all())

    # Load deep finding
    finding_result = await db.execute(
        select(DeepFinding).where(
            DeepFinding.test_run_id == _uuid.UUID(run_id),
            DeepFinding.cluster_id == cluster_id,
        )
    )
    finding = finding_result.scalar_one_or_none()

    # Build evidence bundle
    evidence_bundle = _build_evidence_bundle(analyses, finding)

    # Compute criticality scores
    all_dim_scores = _dim_scores_from_analyses(analyses)
    model_info = get_scoring_model_info()
    dim_scores = score_cluster(
        cluster={"member_test_ids": member_ids},
        all_dim_scores=all_dim_scores,
        total_analyses=max(len(member_ids), 1),
        member_count=len(member_ids),
    )
    composite = sum(
        dim_scores.get(d["name"], 0) * d["weight"]
        for d in model_info["dimensions"]
    )
    composite = round(composite, 1)

    memory_ownership = await _resolve_defect_owner_from_memory(
        db,
        project_id=project_id,
        cluster_id=cluster_id,
        component=request.get("component"),
        member_test_ids=[str(member_id) for member_id in member_ids],
    )
    if memory_ownership and request.get("owner_team") in {None, "", "Unknown"}:
        request["owner_team"] = memory_ownership.get("team_name") or "Unknown"
    if memory_ownership and request.get("component") in {None, "", "Unknown"}:
        request["component"] = memory_ownership.get("service_name") or "Unknown"

    # Check for duplicate
    duplicate_id, duplicate_detected = await _find_duplicate_semantic(
        project_id=project_id,
        duplicate_hint=request.get("title", cluster.label),
        db=db,
    )

    # Resolve test_case_id (optional — use first member if it exists)
    tc_id: Optional[_uuid.UUID] = None
    if member_ids:
        try:
            tc_result = await db.execute(
                select(TestCase.id).where(TestCase.id == _uuid.UUID(str(member_ids[0])))
            )
            tc_id = tc_result.scalar_one_or_none()
        except Exception:
            pass

    severity = request.get("severity", "HIGH")
    failure_cat = None
    if analyses and analyses[0].failure_category:
        failure_cat = analyses[0].failure_category

    project_key = request.get("project_key")

    # Phase 4: Policy check — determine if human approval is required
    policy_result = await check_defect_promotion_policy(
        db,
        project_id=project_id,
        severity=severity,
        has_jira_key=bool(project_key),
        confidence_score=int(composite),
        is_duplicate=duplicate_detected,
    )

    initial_status = str(policy_result["initial_status"])

    defect = Defect(
        test_case_id=tc_id,
        project_id=_uuid.UUID(project_id),
        cluster_id=cluster_id,
        title=request.get("title", cluster.label[:80]),
        description=request.get("description", ""),
        severity=severity,
        component=request.get("component", "Unknown"),
        owner_team=request.get("owner_team", "Unknown"),
        labels=request.get("labels", []),
        criticality_scores=dim_scores,
        evidence_bundle=evidence_bundle,
        ai_confidence_score=int(composite),
        failure_category=failure_cat,
        resolution_status="OPEN",
        is_duplicate=duplicate_detected,
        duplicate_of=_uuid.UUID(duplicate_id) if duplicate_id else None,
        approval_status=initial_status,
        policy_evaluation=policy_result,
    )
    db.add(defect)
    await db.flush()  # materialize defect.id so jira ticket writes can reference it

    # Only create Jira ticket if approved (not pending review).
    # Jira is an *external* side effect, so we commit the defect record
    # first (outside this service) before calling out. The handler is
    # responsible for the atomic write; Jira creation then happens on a
    # best-effort basis and gets its own follow-up flush if it updates
    # the defect row.
    jira_ticket: Optional[dict] = None
    jira_url: Optional[str] = None
    if (
        project_key
        and not duplicate_detected
        and initial_status == ActionStatus.APPROVED
    ):
        jira_ticket, jira_url = await _create_jira_ticket(
            project_key=project_key,
            title=request.get("title", cluster.label),
            description=request.get("description", ""),
            severity=severity,
            labels=request.get("labels", []),
        )
        if jira_ticket:
            defect.jira_ticket_id = jira_ticket.get("key")
            defect.jira_ticket_url = jira_url
            defect.approval_status = ActionStatus.EXECUTED

    # Track promotion metrics
    if duplicate_detected:
        defect_promotions_total.labels(result="duplicate_detected").inc()
    elif initial_status == ActionStatus.PENDING_REVIEW:
        defect_promotions_total.labels(result="pending_review").inc()
    elif jira_ticket:
        defect_promotions_total.labels(result="jira_created").inc()
    elif project_key and not jira_ticket:
        defect_promotions_total.labels(result="jira_failed").inc()
    else:
        defect_promotions_total.labels(result="local_only").inc()

    return {
        "defect_id": str(defect.id),
        "cluster_id": cluster_id,
        "severity": severity,
        "title": defect.title,
        "owner_team": defect.owner_team,
        "component": defect.component,
        "duplicate_detected": duplicate_detected,
        "duplicate_defect_id": duplicate_id,
        "jira_ticket": jira_ticket,
        "jira_url": jira_url,
        "approval_status": initial_status,
        "requires_approval": policy_result["requires_approval"],
        "policy_reasons": policy_result["policy_reasons"],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dim_scores_from_analyses(analyses: list[AIAnalysis]) -> dict[str, float]:
    """Compute approximate dimension scores from a cluster's analyses."""
    total = max(len(analyses), 1)
    product_bugs = sum(
        1 for a in analyses
        if a.failure_category == FailureCategory.PRODUCT_BUG and not a.is_flaky
    )
    infra = sum(
        1 for a in analyses if a.failure_category == FailureCategory.INFRASTRUCTURE
    )
    flaky = sum(1 for a in analyses if a.is_flaky)
    non_flaky_frac = (total - flaky) / total
    avg_conf = sum(a.confidence_score or 0 for a in analyses) / total
    cluster_size = len(analyses)  # 0 when empty — avoids false positive scores

    return {
        "user_impact":       min(100.0, (product_bugs / total) * 80 + cluster_size * 3),
        "env_sensitivity":   min(100.0, (infra / total) * 80),
        "reproducibility":   min(100.0, non_flaky_frac * 80),
        "regression_likely": min(100.0, 50.0 if product_bugs > 0 else 20.0),
        "hist_recurrence":   min(100.0, (1 - (avg_conf / 100)) * 60),
        "blast_radius":      min(100.0, cluster_size * 8),
        "diagnosis_conf":    max(0.0, 100.0 - avg_conf),
    }


def _composite_to_severity(composite: float) -> str:
    if composite >= 70:
        return "CRITICAL"
    if composite >= 50:
        return "HIGH"
    if composite >= 30:
        return "MEDIUM"
    return "LOW"


def _build_evidence_bundle(analyses: list[AIAnalysis], finding: Any) -> dict:
    stack_traces: list[str] = []
    log_anomalies: list[str] = []
    data_sources: set[str] = set()

    for a in analyses[:5]:
        for ev in (a.evidence_references or [])[:2]:
            src = str(ev.get("source", ""))
            excerpt = str(ev.get("excerpt", ""))[:300]
            if src:
                data_sources.add(src)
            if excerpt:
                stack_traces.append(f"[{src}] {excerpt}")

    if finding and finding.evidence:
        for ev in finding.evidence[:3]:
            src = str(ev.get("source", ""))
            excerpt = str(ev.get("excerpt", ""))[:300]
            if src:
                data_sources.add(src)
            log_anomalies.append(f"[{src}] {excerpt}" if excerpt else src)

    return {
        "stack_traces": stack_traces[:5],
        "log_anomalies": log_anomalies[:5],
        "data_sources": sorted(data_sources),
    }


async def _resolve_defect_owner_from_memory(
    db: AsyncSession,
    *,
    project_id: str,
    cluster_id: str,
    component: str | None,
    member_test_ids: list[str],
) -> dict[str, Any] | None:
    """Resolve candidate owner through canonical memory, if available."""
    try:
        from app.services.agent_memory_service import resolve_ownership_from_memory

        context = await resolve_ownership_from_memory(
            db,
            _uuid.UUID(str(project_id)),
            cluster_id=cluster_id,
            component=component,
            member_test_ids=member_test_ids,
        )
    except Exception as exc:
        logger.debug("Defect owner memory lookup skipped: %s", exc)
        return None
    if not context:
        return None
    ownership = context.get("ownership") or {}
    if not isinstance(ownership, dict):
        return None
    return ownership


async def _generate_jira_content(cluster: FailureCluster, analyses: list[AIAnalysis]) -> dict:
    analyses_json = json.dumps(
        [
            {
                "failure_category": _failure_category_value(a.failure_category),
                "root_cause_summary": a.root_cause_summary,
                "confidence_score": a.confidence_score,
            }
            for a in analyses[:5]
        ],
        indent=2,
    )

    evidence_json = json.dumps(
        [
            ev
            for a in analyses[:3]
            for ev in (a.evidence_references or [])[:2]
        ],
        indent=2,
    )

    prompt = _DEFECT_PROMPT.format(
        cluster_json=json.dumps(
            {
                "label": cluster.label,
                "size": cluster.size,
                "representative_error": (cluster.representative_error or "")[:300],
            }
        ),
        analyses_json=analyses_json,
        evidence_json=evidence_json or "No evidence collected",
    )

    try:
        llm = await get_llm(temperature=0.1)
        response = await llm.ainvoke(prompt)
        raw = response.content if hasattr(response, "content") else str(response)
        content = str(raw).strip()
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
        content = re.sub(r"\s*```$", "", content, flags=re.MULTILINE)
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return cast(dict[str, Any], json.loads(match.group()))
    except Exception as exc:
        logger.warning("LLM defect content generation failed: %s", exc)

    return {
        "title": cluster.label[:80],
        "description": f"Failure cluster '{cluster.label}' with {cluster.size} failures.",
        "severity": "HIGH",
        "component": "Unknown",
        "owner_team": "Unknown",
        "labels": ["automated-test", "cluster-promoted"],
        "duplicate_hint": cluster.label[:100],
    }


async def _find_duplicate_semantic(
    project_id: str,
    duplicate_hint: str,
    db: AsyncSession,
) -> tuple[Optional[str], bool]:
    """
    Find semantically similar open defects using ChromaDB.
    Falls back to difflib title similarity when ChromaDB is unavailable.
    Returns (duplicate_defect_id, found_bool).
    """
    try:
        from app.services.agent_memory_service import find_duplicate_defect_memory

        memory_match = await find_duplicate_defect_memory(
            db,
            _uuid.UUID(str(project_id)),
            duplicate_hint,
        )
        if memory_match.get("found"):
            return str(memory_match.get("duplicate_defect_id")), True
        audit = memory_match.get("retrieval_audit") or {}
        if audit.get("memory_entry_count"):
            return None, False
    except Exception as exc:
        logger.debug("Canonical memory duplicate check skipped: %s", exc)

    # Get open defects for this project
    try:
        result = await db.execute(
            select(Defect)
            .where(
                Defect.project_id == _uuid.UUID(project_id),
                Defect.resolution_status == "OPEN",
                Defect.is_duplicate.is_(False),
            )
            .order_by(Defect.created_at.desc())
            .limit(100)
        )
        open_defects = list(result.scalars().all())
    except Exception as exc:
        logger.debug("Could not load open defects for duplicate check: %s", exc)
        return None, False

    if not open_defects:
        return None, False

    # Try ChromaDB semantic similarity
    try:
        from app.core.config import settings

        _DEFECT_COLLECTION = "open_defects"

        def _chroma_dedup() -> Optional[str]:
            import chromadb
            client = chromadb.HttpClient(
                host=settings.CHROMA_HOST, port=settings.CHROMA_PORT
            )
            coll = client.get_or_create_collection(_DEFECT_COLLECTION)
            # Upsert current open defects
            ids = [str(d.id) for d in open_defects if d.title]
            docs = [d.title or d.cluster_id or "" for d in open_defects if d.title]
            if ids and docs:
                coll.upsert(ids=ids, documents=docs)
            # Query for similar
            results = coll.query(query_texts=[duplicate_hint[:200]], n_results=1)
            ids_list = results.get("ids", [[]])[0]
            distance_rows = cast(list[list[float]], results.get("distances") or [[]])
            distances = distance_rows[0] if distance_rows else []
            if ids_list and distances and distances[0] < 0.4:
                return ids_list[0]
            return None

        match_id = await asyncio.to_thread(_chroma_dedup)
        if match_id:
            return match_id, True
    except Exception as exc:
        logger.debug(
            "ChromaDB duplicate check failed, falling back to difflib: %s", exc
        )

    # Fallback: difflib title similarity
    import difflib

    hint_lower = duplicate_hint.lower()
    for d in open_defects:
        candidate_title = (d.title or d.cluster_id or "").lower()
        ratio = difflib.SequenceMatcher(
            None, hint_lower[:100], candidate_title[:100]
        ).ratio()
        if ratio > 0.7:
            return str(d.id), True

    return None, False


async def _create_jira_ticket(
    project_key: str,
    title: str,
    description: str,
    severity: str,
    labels: list[str],
) -> tuple[Optional[dict], Optional[str]]:
    try:
        from app.services.jira_client import create_jira_issue

        ticket = await create_jira_issue(
            project_key=project_key,
            test_name=title[:255],
            run_id="defect-promotion",
            ai_summary=description[:1000],
            recommended_action=f"Severity: {severity}. Labels: {', '.join(labels) if labels else 'none'}",
            stack_trace="",
            dashboard_link="",
        )
        url = ticket.get("ticket_url") if ticket else None
        return ticket, url
    except Exception as exc:
        logger.warning("Jira ticket creation failed: %s", exc)
        return None, None
