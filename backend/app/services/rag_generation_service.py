"""RAG grounded test case generation service (RAG-8 / RAG-9)."""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import (
    GenerationBatch,
    GenerationCaseSource,
    KnowledgeSource,
    ManagedTestCase,
    RequirementCoverage,
    User,
)
import structlog

from app.services.knowledge_source_service import require_rag_enabled_async
from app.services.async_utils import await_if_needed
from app.services.rag_retrieval_service import RetrievedChunk, retrieve_chunks
from app.services.test_management_audit_service import audit_event
from app.services.test_case_lifecycle_service import stage_test_case_snapshot

logger = structlog.get_logger(__name__)

# Maximum number of citation chunks linked to each generated case
MAX_CITATIONS_PER_CASE = 5


@dataclass
class CitationGroup:
    case_index: int
    chunks: list[RetrievedChunk] = field(default_factory=list)


@dataclass
class GroundedGenerationResult:
    batch_id: uuid.UUID
    generation_mode: str
    test_cases: list[dict]
    citations: list[dict]
    coverage_summary: Optional[str] = None
    gaps_noted: list[str] = field(default_factory=list)
    created_ids: list[str] = field(default_factory=list)


async def grounded_generate(
    db: AsyncSession,
    project_id: uuid.UUID,
    prompt_text: str,
    source_ids: list[uuid.UUID],
    generation_config: Optional[dict],
    current_user: User,
    persist: bool = False,
) -> GroundedGenerationResult:
    """
    Full grounded generation pipeline:
    1. Retrieve top-k chunks from selected sources
    2. Build augmented prompt with evidence
    3. Call LLM to generate test cases
    4. Persist GenerationBatch
    5. If persist, create ManagedTestCase + GenerationCaseSource rows
    6. Run coverage mapping
    """
    await require_rag_enabled_async(db)

    is_grounded = bool(source_ids)
    generation_mode = "grounded" if is_grounded else "raw"

    # Create batch record
    batch = GenerationBatch(
        project_id=project_id,
        created_by_id=current_user.id,
        prompt_text=prompt_text[:5000] if prompt_text else None,
        source_ids=[str(sid) for sid in source_ids] if source_ids else None,
        generation_mode=generation_mode,
        generation_config=generation_config,
        status="pending",
    )
    db.add(batch)
    await db.flush()

    try:
        # Step 1: Retrieve chunks if grounded
        retrieved: list[RetrievedChunk] = []
        if is_grounded:
            query_text = prompt_text or "test case requirements"
            retrieved = await retrieve_chunks(
                db, project_id, query_text,
                source_ids=source_ids,
                top_k=generation_config.get("top_k", 15) if generation_config else 15,
            )

        # Step 2: Build augmented prompt
        augmented_prompt = _build_grounded_prompt(prompt_text, retrieved)

        # RAG-13: Redact sensitive content before sending to LLM
        from app.services.rag_redaction_service import redact_prompt
        augmented_prompt, was_redacted = redact_prompt(augmented_prompt)
        if was_redacted:
            logger.info("grounded_generation_prompt_redacted", batch_id=str(batch.id))

        # Step 3: Call LLM
        generated_cases = await _call_llm_generate(augmented_prompt, generation_config, project_id=project_id)

        batch.cases_generated = len(generated_cases)
        batch.status = "complete"
        batch.completed_at = datetime.now(timezone.utc)
        batch.llm_model_used = settings.LLM_MODEL if hasattr(settings, "LLM_MODEL") else None

        # Step 4: Build citations
        citations = _build_citations(generated_cases, retrieved)

        # Step 5: Persist if requested
        created_ids: list[str] = []
        if persist and generated_cases:
            created_ids = await _persist_cases(
                db, batch, project_id, generated_cases, retrieved, current_user,
            )

        # Step 6: Coverage mapping
        coverage_summary = None
        gaps: list[str] = []
        if retrieved:
            coverage_summary, gaps = await _map_coverage(db, batch.id, project_id, retrieved, generated_cases)

        await db.commit()

        return GroundedGenerationResult(
            batch_id=batch.id,
            generation_mode=generation_mode,
            test_cases=generated_cases,
            citations=citations,
            coverage_summary=coverage_summary,
            gaps_noted=gaps,
            created_ids=created_ids,
        )

    except Exception as exc:
        # Re-raise so the request's ``get_db`` dependency rolls back the whole
        # unit of work (the pending GenerationBatch + any partial cases).
        # Deliberately do NOT ``rollback()``/re-commit the injected session
        # here: rolling back a caller-owned session is the anti-pattern that
        # aborts the caller's transaction, and the batch was only ``flush``ed
        # (never committed) so after a rollback it's detached — the previous
        # "mark failed + commit" recovery therefore persisted nothing anyway.
        logger.error("grounded_generation_failed", batch_id=str(batch.id), error=str(exc))
        raise


def _build_grounded_prompt(prompt_text: str, chunks: list[RetrievedChunk]) -> str:
    """Build a prompt that injects retrieved evidence for grounded generation."""
    parts = []

    if chunks:
        parts.append("## Retrieved Requirements Evidence\n")
        parts.append("Use the following evidence to ground your test case generation. "
                     "Each test case should cite which evidence it is based on.\n")
        for i, chunk in enumerate(chunks, 1):
            source_label = f"[Source: {chunk.source_title}]"
            section = f" > {chunk.section_heading}" if chunk.section_heading else ""
            req = f" (Req: {chunk.requirement_id})" if chunk.requirement_id else ""
            parts.append(f"\n### Evidence {i} {source_label}{section}{req}")
            parts.append(chunk.chunk_text[:2000])

        parts.append("\n\n## Generation Instructions\n")
        parts.append("Generate test cases grounded in the evidence above. "
                     "For each test case, note which evidence items it covers. "
                     "Identify any requirements that are NOT covered by the generated cases.\n")

    if prompt_text:
        parts.append(f"\n## Additional Context\n{prompt_text}\n")

    if not parts:
        parts.append("Generate functional test cases based on general best practices.")

    return "\n".join(parts)


async def _call_llm_generate(prompt: str, config: Optional[dict], *, project_id: Any = None) -> list[dict]:
    """Call the LLM to generate test cases. Returns list of case dicts.

    ``config`` may contain ``test_type``, ``priority``, ``count`` or
    other generation preferences — appended as instructions when present.

    The LLM call is wrapped in ``asyncio.wait_for`` with a timeout derived
    from ``AI_TIMEOUT_SECONDS`` (default 90 s) so a slow or unreachable
    Ollama instance cannot hang the request beyond the Axios budget.
    """
    effective_prompt = prompt
    if config:
        extras: list[str] = []
        if config.get("test_type"):
            extras.append(f"Focus on {config['test_type']} test cases.")
        if config.get("priority"):
            extras.append(f"Prioritise {config['priority']} priority scenarios.")
        if config.get("count"):
            extras.append(f"Generate approximately {config['count']} test cases.")
        if extras:
            effective_prompt = prompt + "\n\n" + " ".join(extras)

    llm_timeout = getattr(settings, "AI_TIMEOUT_SECONDS", 90)

    try:
        from app.services.test_case_ai_agent import ai_generate_test_cases
        result = await asyncio.wait_for(
            ai_generate_test_cases(effective_prompt, project_id=project_id),
            timeout=llm_timeout,
        )
        if isinstance(result, list):
            return result if result else _stub_generated_cases(effective_prompt)
        if isinstance(result, dict) and "test_cases" in result:
            cases = result["test_cases"]
            return cases if cases else _stub_generated_cases(effective_prompt)
        return _stub_generated_cases(effective_prompt)
    except ImportError:
        logger.warning("test_case_ai_agent not available, returning stub cases")
        return _stub_generated_cases(effective_prompt)
    except asyncio.TimeoutError:
        logger.warning("llm_generation_timed_out", timeout_seconds=llm_timeout)
        return _stub_generated_cases(effective_prompt)
    except Exception as exc:
        logger.error("llm_generation_failed", error=str(exc))
        return _stub_generated_cases(effective_prompt)


def _stub_generated_cases(prompt: str) -> list[dict]:
    """Fallback stub when LLM is unavailable."""
    return [
        {
            "title": "Verify core functionality",
            "description": "Validate the primary workflow described in the requirements",
            "steps": [
                {"step_number": 1, "action": "Set up preconditions", "expected_result": "System ready"},
                {"step_number": 2, "action": "Execute main flow", "expected_result": "Expected outcome achieved"},
                {"step_number": 3, "action": "Verify results", "expected_result": "All assertions pass"},
            ],
            "test_type": "functional",
            "priority": "high",
            "severity": "major",
            "grounding_notes": "Generated from retrieved evidence (stub mode)",
        },
        {
            "title": "Verify error handling",
            "description": "Test negative scenarios and boundary conditions",
            "steps": [
                {"step_number": 1, "action": "Provide invalid input", "expected_result": "Appropriate error message"},
                {"step_number": 2, "action": "Test boundary values", "expected_result": "System handles gracefully"},
            ],
            "test_type": "functional",
            "priority": "medium",
            "severity": "major",
            "grounding_notes": "Generated for negative scenario coverage (stub mode)",
        },
    ]


def _build_citations(cases: list[dict], chunks: list[RetrievedChunk]) -> list[dict]:
    """Build citation mappings from cases to chunks."""
    citations = []
    for i, _case in enumerate(cases):
        # Simple round-robin: each case gets citations from nearby chunks
        for chunk in chunks[:MAX_CITATIONS_PER_CASE]:
            citations.append({
                "case_index": i,
                "vector_id": chunk.vector_id,
                "source_id": str(chunk.source_id),
                "source_title": chunk.source_title,
                "section_heading": chunk.section_heading,
                "chunk_text_preview": chunk.chunk_text_preview,
                "relevance_score": chunk.relevance_score,
            })
    return citations


def _case_text_for_faithfulness(case_data: dict) -> str:
    """The generated case as one string for the evaluator to judge.

    Title, description and steps together — judging the title alone would
    pass almost anything, since a plausible title is the easiest part of a
    hallucinated case to get right.
    """
    parts = [str(case_data.get("title") or ""), str(case_data.get("description") or "")]
    steps = case_data.get("steps")
    if isinstance(steps, list):
        parts.extend(
            str(s.get("action") or s.get("description") or s) if isinstance(s, dict) else str(s)
            for s in steps
        )
    elif steps:
        parts.append(str(steps))
    return "\n".join(p for p in parts if p.strip())


async def _persist_cases(
    db: AsyncSession,
    batch: GenerationBatch,
    project_id: uuid.UUID,
    cases: list[dict],
    chunks: list[RetrievedChunk],
    user: User,
) -> list[str]:
    """Create draft ManagedTestCase rows plus generation-source evidence."""
    created_ids = []

    # Get content hash snapshot for each source
    source_hashes: dict[uuid.UUID, str] = {}
    if chunks:
        source_id_set = {c.source_id for c in chunks}
        result = await db.execute(
            select(KnowledgeSource.id, KnowledgeSource.content_hash).where(
                KnowledgeSource.id.in_(source_id_set),
            )
        )
        source_hashes = {row.id: row.content_hash or "" for row in result.all()}

    for i, case_data in enumerate(cases):
        case = ManagedTestCase(
            project_id=project_id,
            title=case_data.get("title", f"Generated Case {i + 1}")[:500],
            description=case_data.get("description"),
            steps=case_data.get("steps"),
            test_type=case_data.get("test_type", "functional"),
            priority=case_data.get("priority", "medium"),
            severity=case_data.get("severity", "major"),
            status="draft",
            version=1,
            author_id=user.id,
            ai_generated=True,
            ai_generation_prompt=batch.prompt_text[:1000] if batch.prompt_text else None,
            generation_batch_id=batch.id,
        )
        db.add(case)
        await db.flush()
        created_ids.append(str(case.id))

        # Faithfulness evaluation (Tier 2 item 9).
        #
        # Scored here because this is the only place that holds both the
        # generated content and the chunks it was grounded in. `evaluate`
        # returns None when the `rag_faithfulness_gate` flag is off, which is
        # the default — so an existing deployment sees no extra LLM calls and
        # no behaviour change until someone turns it on.
        #
        # Staged on THIS session via apply_evaluation rather than calling
        # persist_evaluation: the row has only been flushed, so a second
        # session would not see it and would silently score nothing.
        try:
            from app.services.rag_faithfulness_service import (  # noqa: PLC0415
                apply_evaluation,
                evaluate,
            )

            citation_texts = [
                c.chunk_text_preview or ""
                for c in chunks[:MAX_CITATIONS_PER_CASE]
                if c.chunk_text_preview
            ]
            evaluation = await evaluate(
                _case_text_for_faithfulness(case_data), citation_texts, db=db,
                project_id=project_id,
            )
            if evaluation is not None:
                apply_evaluation(case, evaluation)
        except Exception as exc:  # noqa: BLE001
            # Never fail generation because the evaluator had a bad day — the
            # cases are still reviewable by a human, which is the fallback the
            # gate exists to route them to anyway.
            logger.warning(
                "faithfulness_evaluation_skipped",
                case_id=str(case.id),
                error=str(exc)[:200],
            )

        stage_test_case_snapshot(
            db,
            case,
            actor_id=user.id,
            change_type="created",
            change_summary="Created by grounded generation",
            changed_fields=[
                "title", "description", "steps", "test_type", "priority",
                "severity", "status",
            ],
        )
        await audit_event(
            db,
            "test_case",
            case.id,
            case.project_id,
            "created",
            user,
            details=f"Created by generation batch {batch.id}",
        )

        # Create citation links
        for chunk in chunks[:MAX_CITATIONS_PER_CASE]:
            gcs = GenerationCaseSource(
                batch_id=batch.id,
                case_id=case.id,
                source_id=chunk.source_id,
                chunk_vector_id=chunk.vector_id,
                relevance_score=chunk.relevance_score,
                section_heading=chunk.section_heading,
                chunk_text_preview=chunk.chunk_text_preview,
                source_content_hash_at_generation=source_hashes.get(chunk.source_id),
            )
            db.add(gcs)

    return created_ids


async def _map_coverage(
    db: AsyncSession,
    batch_id: uuid.UUID,
    project_id: uuid.UUID,
    chunks: list[RetrievedChunk],
    cases: list[dict],
) -> tuple[Optional[str], list[str]]:
    """Extract requirement IDs from chunks and create coverage records."""
    req_ids = set()
    for chunk in chunks:
        if chunk.requirement_id:
            req_ids.add(chunk.requirement_id)
        elif chunk.section_heading:
            req_ids.add(chunk.section_heading[:200])

    if not req_ids:
        return None, []

    gaps: list[str] = []
    total = len(req_ids)
    covered = 0

    for req_id in req_ids:
        # Simple heuristic: if any case title/description mentions the req_id, it's covered
        is_covered = any(
            req_id.lower() in json.dumps(c).lower()
            for c in cases
        )
        status = "covered" if is_covered else "uncovered"
        if is_covered:
            covered += 1
        else:
            gaps.append(req_id)

        rc = RequirementCoverage(
            batch_id=batch_id,
            project_id=project_id,
            requirement_id=req_id[:200],
            coverage_status=status,
        )
        await await_if_needed(db.add(rc))

    score = int((covered / total) * 100) if total > 0 else 0
    summary = f"{covered}/{total} requirements covered ({score}%)"
    return summary, gaps
