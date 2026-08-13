"""AI triage analysis endpoint."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    require_role,
    resolve_authorized_test_case,
)
from app.db.postgres import get_db
from app.models.postgres import AIAnalysis, FailureCategory, TestCase, User, UserRole
from app.models.schemas import (
    AnalysisProvenance,
    AnalysisResponse,
    AnalyzeRequest,
    ConfidenceWhy,
    RoleActions,
    ThresholdCheck,
)
from app.services import analysis_router

router = APIRouter(prefix="/api/v1", tags=["AI Analysis"])

# Keys that make a routing_metadata blob genuinely informative about which
# engine ran. A blob carrying only unrelated keys (e.g. an old row that only
# ever stored ``kind_evidence``) yields provenance=None rather than a block of
# nulls that reads like "we don't know" dressed up as an answer.
_PROVENANCE_KEYS = (
    "analysis_mode", "mode_used", "mode_requested", "mode_resolved",
    "fallback_from", "fallback_reason",
)


def _build_provenance(
    routing_metadata: dict | None,
    *,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> AnalysisProvenance | None:
    """US-15.1: read engine provenance straight off routing_metadata.

    Returns ``None`` when there is no routing metadata to read — absent, not
    fabricated. Nothing here is recomputed or inferred from other fields.

    ``analysis_mode`` is the key the analysis pipeline persists for the engine
    that actually ran (``_audit["analysis_mode"] = routing["mode_used"]``);
    ``mode_used`` is the router's own name for it. Both are accepted so a
    directly-stamped ``_routing`` dict and a persisted ``_audit`` blob read
    identically.
    """
    if not isinstance(routing_metadata, dict) or not routing_metadata:
        return None
    if not any(routing_metadata.get(k) for k in _PROVENANCE_KEYS):
        return None

    raw_check = routing_metadata.get("threshold_check")
    check = None
    if isinstance(raw_check, dict) and raw_check.get("threshold") is not None:
        try:
            check = ThresholdCheck(**raw_check)
        except Exception:  # pragma: no cover — a malformed legacy blob
            check = None

    versions = routing_metadata.get("prompt_versions")
    if not isinstance(versions, dict):
        versions = {}

    fallback_from = routing_metadata.get("fallback_from")
    return AnalysisProvenance(
        mode_used=routing_metadata.get("mode_used") or routing_metadata.get("analysis_mode"),
        mode_requested=routing_metadata.get("mode_requested"),
        mode_resolved=routing_metadata.get("mode_resolved"),
        fallback_from=fallback_from,
        fallback_reason=routing_metadata.get("fallback_reason"),
        fallback_occurred=bool(fallback_from),
        llm_provider=llm_provider,
        llm_model=llm_model,
        prompt_versions={str(k): str(v) for k, v in versions.items()},
        confidence_basis=routing_metadata.get("confidence_basis"),
        threshold_check=check,
    )


async def _build_confidence_gate(
    confidence_score: Any,
) -> tuple[ThresholdCheck | None, bool, str]:
    """Evaluate the CURRENT confidence gate for this response.

    Deliberately evaluated live rather than replayed from the stored check:
    the banner answers "does this clear the policy in force right now?", which
    changes the moment an operator moves the knob. The historical check stays
    available under ``provenance.threshold_check``.
    """
    from app.services.confidence_gate import check_confidence, gate_status, is_low_confidence

    try:
        raw = await check_confidence(confidence_score)
    except Exception:  # pragma: no cover — the gate must never 500 the card
        return None, False, "not_evaluated"
    return ThresholdCheck(**raw), is_low_confidence(raw), gate_status(raw)


def _build_confidence_why(analysis: dict) -> ConfidenceWhy:
    """Derive a confidence explanation from the analysis dict deterministically."""
    evidence_refs: list = analysis.get("evidence_references", []) or []
    tools_used: list = analysis.get("tools_used", []) or []

    data_sources = list({
        ref.get("source", "") if isinstance(ref, dict) else ""
        for ref in evidence_refs
        if (ref.get("source") if isinstance(ref, dict) else None)
    })

    n_tools = len(tools_used)
    if n_tools == 0:
        depth = "fast_path"
    elif n_tools <= 3:
        depth = "standard"
    else:
        depth = "deep"

    return ConfidenceWhy(
        evidence_count=len(evidence_refs),
        data_sources=data_sources,
        is_llm_inference=n_tools > 0,
        investigation_depth=depth,
        # AI-F4: present when the rules engine classified (carried on the
        # analysis dict, or recovered from routing_metadata on stored rows).
        confidence_basis=analysis.get("confidence_basis"),
    )


def _build_role_actions(analysis: dict) -> RoleActions:
    """Extract role_actions from the analysis dict, defaulting gracefully."""
    raw: dict = analysis.get("role_actions") or {}
    return RoleActions(
        qa=raw.get("qa", ""),
        developer=raw.get("developer", ""),
        sre=raw.get("sre", ""),
        release_manager=raw.get("release_manager", ""),
    )


@router.get(
    "/analyze/{test_case_id}",
    response_model=AnalysisResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
    summary="Fetch existing AI analysis for a test case (no re-run)",
)
async def get_existing_analysis(
    test_case_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Return the previously stored AI analysis for a test case without triggering
    a new LLM run.  Returns 404 if no analysis has been run yet.
    """
    import uuid as _uuid
    try:
        tc_uuid = _uuid.UUID(test_case_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid test_case_id format")

    await resolve_authorized_test_case(db, current_user, tc_uuid)
    row = (await db.execute(
        select(AIAnalysis).where(AIAnalysis.test_case_id == tc_uuid)
    )).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="No analysis found for this test case")

    # ``failure_category`` is declared as ``Mapped[Optional[FailureCategory]]``
    # but the underlying column is ``String(30)`` — SQLAlchemy hands back a
    # plain ``str``, so ``.value`` raises ``AttributeError: 'str' object has
    # no attribute 'value'`` and FastAPI 500s. Treat the field as already a
    # string (or None), then coerce legacy/lowercase values to UNKNOWN so
    # the strict ``AnalysisResponse.failure_category: FailureCategory``
    # Pydantic check doesn't 500 a second time on validation.
    raw_category = row.failure_category
    cat_value = getattr(raw_category, "value", None) or (raw_category or "UNKNOWN")
    if isinstance(cat_value, str) and cat_value not in {c.value for c in FailureCategory}:
        cat_value = FailureCategory.UNKNOWN.value
    analysis = {
        "root_cause_summary":  row.root_cause_summary or "",
        "failure_category":    cat_value,
        "backend_error_found": row.backend_error_found or False,
        "pod_issue_found":     row.pod_issue_found or False,
        "is_flaky":            row.is_flaky or False,
        "confidence_score":    row.confidence_score or 0,
        "recommended_actions": row.recommended_actions or [],
        "evidence_references": row.evidence_references or [],
        "tools_used":          row.tools_used or [],
        "role_actions":        row.role_actions or {},
        "llm_provider":        row.llm_provider or "unknown",
        "llm_model":           row.llm_model or "unknown",
        "requires_human_review": row.requires_human_review if row.requires_human_review is not None else True,
        # AI-F4: the pipeline persists confidence_basis inside the
        # routing_metadata JSONB audit blob (no dedicated column). getattr:
        # legacy-shaped rows/mocks may not carry the attribute at all.
        "confidence_basis": (getattr(row, "routing_metadata", None) or {}).get("confidence_basis"),
    }

    # AI-4: evidence checklist for the derived failure kind — the stored
    # blob when the pipeline persisted one, computed on demand for rows
    # written before the feature (no backfill). Best-effort: None on failure.
    kind_evidence = (getattr(row, "routing_metadata", None) or {}).get("kind_evidence")
    if not isinstance(kind_evidence, dict) or not kind_evidence.get("kind"):
        from app.services.kind_evidence import compute_kind_evidence_for_test_case
        kind_evidence = await compute_kind_evidence_for_test_case(db, tc_uuid)

    # US-15.1 / US-15.2 — engine provenance (absent on pre-feature rows) and
    # the live confidence gate.
    provenance = _build_provenance(
        getattr(row, "routing_metadata", None),
        llm_provider=row.llm_provider,
        llm_model=row.llm_model,
    )
    gate, low_confidence, gate_status_value = await _build_confidence_gate(
        analysis["confidence_score"]
    )

    return AnalysisResponse(
        test_case_id=tc_uuid,
        # Identity of the persisted row — the UI needs it to reach
        # POST /api/v1/feedback/{analysis_id} (confirm / correct).
        analysis_id=getattr(row, "id", None),
        **{k: v for k, v in analysis.items() if k not in {"role_actions", "llm_provider", "llm_model", "requires_human_review"}},
        role_actions=_build_role_actions(analysis),
        confidence_why=_build_confidence_why(analysis),
        kind_evidence=kind_evidence,
        llm_provider=analysis["llm_provider"],
        llm_model=analysis["llm_model"],
        requires_human_review=analysis["requires_human_review"],
        provenance=provenance,
        low_confidence=low_confidence,
        confidence_gate_status=gate_status_value,
        confidence_gate=gate,
    )


@router.post(
    "/analyze",
    response_model=AnalysisResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def analyze_test_case(
    request: AnalyzeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Trigger the LangChain ReAct agent to investigate a failed test case.
    Returns a structured root-cause analysis with confidence scoring, role-aware
    recommended actions, and a 'confidence_why' breakdown showing the evidence basis.
    """
    # Fetch test case details
    authorized = await resolve_authorized_test_case(
        db, current_user, request.test_case_id
    )
    tc = authorized.test_case
    test_run = authorized.test_run

    # Run the AI agent
    analysis = await analysis_router.classify_test(
        test_case={
            "test_case_id": str(tc.id),
            "test_name": tc.test_name,
            "suite_name": tc.suite_name,
            "timestamp": (test_run.end_time or test_run.start_time).isoformat()
            if (test_run.end_time or test_run.start_time) else None,
            "ocp_pod_name": test_run.ocp_pod_name,
            "ocp_namespace": test_run.ocp_namespace,
            "run_id": str(authorized.run_id),
            "project_id": str(authorized.project_id),
            "test_fingerprint": tc.test_fingerprint,
        },
        run_context={"run_id": str(authorized.run_id), "project_id": str(authorized.project_id)},
    )

    # Persist structured result to PostgreSQL
    category = analysis.get("failure_category", "UNKNOWN")
    try:
        fc = FailureCategory(category)
    except ValueError:
        fc = FailureCategory.UNKNOWN

    existing = await db.execute(
        select(AIAnalysis).where(AIAnalysis.test_case_id == tc.id)
    )
    ai_row = existing.scalar_one_or_none()

    # US-15.1: persist the routing decision so a later GET renders the same
    # provenance this response carries. Merged over any existing blob (an
    # earlier pipeline run may have stored kind_evidence / confidence_basis
    # there) — never a wholesale replace.
    routing = analysis.get("_routing") or {}
    routing_metadata: dict | None = None
    if routing:
        routing_metadata = dict((ai_row.routing_metadata if ai_row else None) or {})
        routing_metadata.update({
            k: v for k, v in routing.items()
            if k in {
                "mode_used", "mode_requested", "mode_resolved", "fallback_from",
                "fallback_reason", "execution_path", "threshold_check",
            }
        })
        # ``analysis_mode`` is the key the pipeline writes for the engine that
        # ran; keep both spellings consistent for readers of either.
        routing_metadata["analysis_mode"] = routing.get("mode_used")

    if ai_row:
        ai_row.root_cause_summary = analysis.get("root_cause_summary")
        ai_row.failure_category = fc
        ai_row.backend_error_found = analysis.get("backend_error_found", False)
        ai_row.pod_issue_found = analysis.get("pod_issue_found", False)
        ai_row.is_flaky = analysis.get("is_flaky", False)
        ai_row.confidence_score = analysis.get("confidence_score", 0)
        ai_row.recommended_actions = analysis.get("recommended_actions", [])
        ai_row.evidence_references = analysis.get("evidence_references", [])
        ai_row.tools_used = analysis.get("tools_used", [])
        ai_row.role_actions = analysis.get("role_actions") or {}
        ai_row.llm_provider = analysis.get("llm_provider")
        ai_row.llm_model = analysis.get("llm_model")
        ai_row.requires_human_review = analysis.get("requires_human_review", True)
        if routing_metadata is not None:
            ai_row.routing_metadata = routing_metadata
    else:
        ai_row = AIAnalysis(
            test_case_id=tc.id,
            root_cause_summary=analysis.get("root_cause_summary"),
            failure_category=fc,
            backend_error_found=analysis.get("backend_error_found", False),
            pod_issue_found=analysis.get("pod_issue_found", False),
            is_flaky=analysis.get("is_flaky", False),
            confidence_score=analysis.get("confidence_score", 0),
            recommended_actions=analysis.get("recommended_actions", []),
            evidence_references=analysis.get("evidence_references", []),
            tools_used=analysis.get("tools_used", []),
            role_actions=analysis.get("role_actions") or {},
            llm_provider=analysis.get("llm_provider"),
            llm_model=analysis.get("llm_model"),
            requires_human_review=analysis.get("requires_human_review", True),
            routing_metadata=routing_metadata,
        )
        db.add(ai_row)

    # Update test case failure category
    await db.execute(
        update(TestCase)
        .where(TestCase.id == tc.id)
        .values(failure_category=fc)
    )
    await db.commit()

    provenance = _build_provenance(
        routing_metadata,
        llm_provider=analysis.get("llm_provider"),
        llm_model=analysis.get("llm_model"),
    )
    gate, low_confidence, gate_status_value = await _build_confidence_gate(
        analysis.get("confidence_score")
    )

    _excluded = {
        "llm_provider", "llm_model", "requires_human_review", "role_actions",
        # Set explicitly below from the gate — never passed through from the
        # analysis dict, so the response cannot disagree with the gate.
        "low_confidence", "confidence_gate_status", "provenance", "confidence_gate",
    }
    return AnalysisResponse(
        test_case_id=tc.id,
        # ``ai_row`` is bound on both branches above (updated or created) and
        # committed, so its id is populated — this is what lets the UI reach
        # POST /api/v1/feedback/{analysis_id}.
        analysis_id=getattr(ai_row, "id", None),
        # Private keys (``_routing``, ``_audit``) are internal plumbing, never
        # response fields — Pydantic would choke on the leading underscore.
        **{k: v for k, v in analysis.items() if k not in _excluded and not k.startswith("_")},
        role_actions=_build_role_actions(analysis),
        confidence_why=_build_confidence_why(analysis),
        llm_provider=analysis.get("llm_provider", "unknown"),
        llm_model=analysis.get("llm_model", "unknown"),
        requires_human_review=analysis.get("requires_human_review", True),
        provenance=provenance,
        low_confidence=low_confidence,
        confidence_gate_status=gate_status_value,
        confidence_gate=gate,
    )
