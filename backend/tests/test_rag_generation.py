"""
Knowledge-Grounded Test Case Generation — Unit Tests (RAG-7 through RAG-14).

Tests retrieval, generation, coverage, review, staleness, redaction, and eval.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.postgres import (
    GenerationBatch,
    GenerationCaseSource,
    ManagedTestCase,
    RequirementCoverage,
)


# ── RAG-7: Retrieval Tests ───────────────────────────────────────────────────


class TestRetrievalParsing:
    def test_parse_results_empty(self):
        from app.services.rag_retrieval_service import _parse_results
        assert _parse_results({}) == []
        assert _parse_results({"ids": [[]]}) == []

    def test_parse_results_with_data(self):
        from app.services.rag_retrieval_service import _parse_results
        results = {
            "ids": [["vec-1", "vec-2"]],
            "documents": [["text 1", "text 2"]],
            "distances": [[0.3, 0.7]],
            "metadatas": [[
                {"source_id": str(uuid.uuid4()), "section_heading": "Requirements", "requirement_id": "REQ-1"},
                {"source_id": str(uuid.uuid4()), "section_heading": "", "requirement_id": ""},
            ]],
        }
        chunks = _parse_results(results)
        assert len(chunks) == 2
        assert chunks[0].vector_id == "vec-1"
        assert chunks[0].relevance_score == pytest.approx(0.7, abs=0.01)
        assert chunks[0].section_heading == "Requirements"
        assert chunks[0].requirement_id == "REQ-1"

    def test_parse_results_invalid_source_id_skipped(self):
        from app.services.rag_retrieval_service import _parse_results
        results = {
            "ids": [["vec-1"]],
            "documents": [["text"]],
            "distances": [[0.5]],
            "metadatas": [[{"source_id": "not-a-uuid"}]],
        }
        assert len(_parse_results(results)) == 0


# ── RAG-8: Generation Tests ──────────────────────────────────────────────────


class TestGroundedPromptBuilding:
    def test_build_grounded_prompt_with_chunks(self):
        from app.services.rag_generation_service import _build_grounded_prompt
        from app.services.rag_retrieval_service import RetrievedChunk
        chunks = [RetrievedChunk(
            vector_id="v1", source_id=uuid.uuid4(), source_title="Jira PROJ-1",
            section_heading="Acceptance Criteria", chunk_text="User can login with valid credentials",
            relevance_score=0.95, requirement_id="AC-1",
        )]
        prompt = _build_grounded_prompt("Generate login tests", chunks)
        assert "Evidence 1" in prompt
        assert "Jira PROJ-1" in prompt
        assert "AC-1" in prompt
        assert "Acceptance Criteria" in prompt
        assert "Generate login tests" in prompt

    def test_build_grounded_prompt_empty_chunks(self):
        from app.services.rag_generation_service import _build_grounded_prompt
        prompt = _build_grounded_prompt("Just some text", [])
        assert "Just some text" in prompt

    def test_build_grounded_prompt_no_input(self):
        from app.services.rag_generation_service import _build_grounded_prompt
        prompt = _build_grounded_prompt("", [])
        assert "general best practices" in prompt


class TestStubGeneration:
    def test_stub_cases_returned(self):
        from app.services.rag_generation_service import _stub_generated_cases
        cases = _stub_generated_cases("any prompt")
        assert len(cases) >= 2
        assert all("title" in c for c in cases)
        assert all("steps" in c for c in cases)


class TestCitationBuilding:
    def test_build_citations(self):
        from app.services.rag_generation_service import _build_citations
        from app.services.rag_retrieval_service import RetrievedChunk
        cases = [{"title": "Test 1"}, {"title": "Test 2"}]
        chunks = [RetrievedChunk(
            vector_id="v1", source_id=uuid.uuid4(), source_title="Source A",
            section_heading="S1", chunk_text="text", relevance_score=0.9,
            requirement_id=None,
        )]
        citations = _build_citations(cases, chunks)
        assert len(citations) == 2  # one per case
        assert citations[0]["case_index"] == 0
        assert citations[1]["case_index"] == 1


# ── RAG-9: Coverage Tests ────────────────────────────────────────────────────


class TestCoverageMapping:
    def test_requirement_coverage_model(self):
        rc = RequirementCoverage(
            batch_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            requirement_id="REQ-42",
            coverage_status="covered",
        )
        assert rc.coverage_status == "covered"
        assert rc.requirement_id == "REQ-42"


# ── RAG-10: Review Tests ─────────────────────────────────────────────────────


class TestGenerationBatchModel:
    def test_explicit_values(self):
        """Server defaults apply at DB level; test with explicit values."""
        batch = GenerationBatch(
            project_id=uuid.uuid4(),
            generation_mode="grounded",
            cases_generated=0,
            cases_accepted=0,
            cases_rejected=0,
            status="pending",
            prompt_redacted=False,
        )
        assert batch.cases_generated == 0
        assert batch.cases_accepted == 0
        assert batch.cases_rejected == 0
        assert batch.status == "pending"
        assert batch.prompt_redacted is False

    def test_generation_case_source_explicit_values(self):
        gcs = GenerationCaseSource(
            batch_id=uuid.uuid4(),
            case_id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            chunk_vector_id="vec-123",
            is_stale=False,
        )
        assert gcs.is_stale is False
        assert gcs.stale_detected_at is None


# ── RAG-12: Staleness Tests ──────────────────────────────────────────────────


class TestManagedTestCaseStaleFields:
    def test_is_stale_explicit_false(self):
        """Server defaults apply at DB level; test with explicit values."""
        tc = ManagedTestCase(
            project_id=uuid.uuid4(),
            title="Test stale",
            is_stale=False,
        )
        assert tc.is_stale is False
        assert tc.stale_reason is None


# ── RAG-13: Redaction Tests ──────────────────────────────────────────────────


class TestRedaction:
    def test_redact_bearer_token(self):
        from app.services.rag_redaction_service import redact_prompt
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123"
        result, was_redacted = redact_prompt(text)
        assert "[REDACTED]" in result
        assert was_redacted is True

    def test_redact_email(self):
        from app.services.rag_redaction_service import redact_prompt
        text = "Contact admin@secret.com for access"
        result, was_redacted = redact_prompt(text)
        assert "[REDACTED]" in result
        assert was_redacted is True

    def test_normal_text_unchanged(self):
        from app.services.rag_redaction_service import redact_prompt
        text = "The user should be able to login"
        result, was_redacted = redact_prompt(text)
        assert result == text
        assert was_redacted is False

    def test_redact_confidential_classification(self):
        from app.services.rag_redaction_service import redact_prompt
        text = "password: super_secret_value_123"
        result, was_redacted = redact_prompt(text, classification="confidential")
        assert "[REDACTED]" in result
        assert was_redacted is True

    def test_validate_document_upload_valid(self):
        from app.services.rag_redaction_service import validate_document_upload
        validate_document_upload("requirements.pdf", 1024)  # should not raise

    def test_validate_document_upload_invalid_type(self):
        from app.services.rag_redaction_service import validate_document_upload
        with pytest.raises(ValueError, match="not allowed"):
            validate_document_upload("virus.exe", 1024)

    def test_validate_document_upload_too_large(self):
        from app.services.rag_redaction_service import validate_document_upload
        with pytest.raises(ValueError, match="exceeds maximum"):
            validate_document_upload("big.pdf", 20 * 1024 * 1024)

    def test_validate_url_scheme_blocked(self):
        from app.services.rag_redaction_service import validate_url_scheme
        with pytest.raises(ValueError, match="not allowed"):
            validate_url_scheme("file:///etc/passwd")

    def test_validate_url_scheme_allowed(self):
        from app.services.rag_redaction_service import validate_url_scheme
        validate_url_scheme("https://confluence.corp.com/page/123")  # should not raise


# ── RAG-14: Eval Tests ───────────────────────────────────────────────────────


class TestRagStatus:
    def test_feature_flag_field(self):
        from app.models.schemas import RagStatusResponse
        status = RagStatusResponse(enabled=True)
        assert status.feature_flag == "KNOWLEDGE_RAG_ENABLED"
        assert status.total_sources == 0


# ── RAG-5: Chunking Tests ────────────────────────────────────────────────────


class TestChunkingStrategy:
    def test_split_by_headings(self):
        from app.services.knowledge_chunking_service import _split_by_headings
        text = "Intro text\n\n## Section A\nContent A\n\n### Sub B\nContent B"
        parts = _split_by_headings(text)
        assert len(parts) == 3
        assert parts[0][0] is None  # intro has no heading
        assert parts[1][0] == "Section A"
        assert parts[2][0] == "Sub B"

    def test_split_by_requirements(self):
        from app.services.knowledge_chunking_service import _split_by_requirements
        text = "AC-1: User can login\nAC-2: User can logout\nAC-3: User sees dashboard"
        parts = _split_by_requirements(text)
        assert len(parts) >= 2

    def test_extract_requirement_id(self):
        from app.services.knowledge_chunking_service import _extract_requirement_id
        assert _extract_requirement_id("REQ-42: The system shall") is not None
        assert _extract_requirement_id("AC-3 - User can login") is not None
        assert _extract_requirement_id("Regular text without ID") is None
        assert _extract_requirement_id("FR-1: Functional requirement") is not None

    def test_estimate_tokens(self):
        from app.services.knowledge_chunking_service import _estimate_tokens
        assert _estimate_tokens("one two three") > 0
        assert _estimate_tokens("") >= 1

    def test_split_into_chunks_empty(self):
        from app.services.knowledge_chunking_service import _split_into_chunks
        from app.services.connectors.base import FetchedContent
        content = FetchedContent(raw_text="", content_hash="abc", title="Empty", source_url="http://x")
        chunks = _split_into_chunks(content)
        assert chunks == []

    def test_split_into_chunks_simple_text(self):
        from app.services.knowledge_chunking_service import _split_into_chunks
        from app.services.connectors.base import FetchedContent
        content = FetchedContent(
            raw_text="## Requirements\nREQ-1: User can login\nREQ-2: User can logout",
            content_hash="abc",
            title="Test",
            source_url="http://x",
        )
        chunks = _split_into_chunks(content)
        assert len(chunks) >= 1
        assert all(c.token_count > 0 for c in chunks)

    def test_hard_split_large_text(self):
        from app.services.knowledge_chunking_service import _hard_split
        large_text = " ".join(["word"] * 2000)
        parts = _hard_split(large_text, 800)
        assert len(parts) > 1
        # Each part should be under the max
        from app.services.knowledge_chunking_service import _estimate_tokens
        for part in parts:
            assert _estimate_tokens(part) <= 850  # allow small overshoot


# ── Schema Tests ──────────────────────────────────────────────────────────────


class TestRagSchemas:
    def test_rag_retrieve_request(self):
        from app.models.schemas import RagRetrieveRequest
        req = RagRetrieveRequest(
            project_id=uuid.uuid4(),
            query_text="login flow",
            top_k=5,
        )
        assert req.top_k == 5
        assert req.source_ids is None

    def test_rag_generate_request(self):
        from app.models.schemas import RagGenerateRequest
        req = RagGenerateRequest(
            project_id=uuid.uuid4(),
            prompt_text="Generate tests for auth",
            source_ids=[uuid.uuid4()],
            persist=True,
        )
        assert req.persist is True
        assert len(req.source_ids) == 1

    def test_generation_batch_response(self):
        from app.models.schemas import GenerationBatchResponse
        resp = GenerationBatchResponse(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            generation_mode="grounded",
            cases_generated=5,
            cases_accepted=3,
            cases_rejected=1,
            status="complete",
            created_at=datetime.now(timezone.utc),
        )
        assert resp.cases_generated == 5

    def test_requirement_coverage_schema(self):
        from app.models.schemas import RequirementCoverageSchema
        rc = RequirementCoverageSchema(
            id=uuid.uuid4(),
            batch_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            requirement_id="REQ-1",
            coverage_status="covered",
            created_at=datetime.now(timezone.utc),
        )
        assert rc.coverage_status == "covered"

    def test_citation_schema(self):
        from app.models.schemas import CitationSchema
        cit = CitationSchema(
            case_index=0,
            vector_id="vec-1",
            source_id=uuid.uuid4(),
            source_title="Jira PROJ-1",
        )
        assert cit.case_index == 0
