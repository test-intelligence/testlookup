"""Prove M10 trust regressions kill the unsafe behavior they describe."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = "backend/tests/test_exploratory_m10_rag_trust.py"


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    safe: str
    unsafe: str
    test: str


MUTATIONS = (
    Mutation(
        "projectless-chat",
        "backend/app/routers/chat.py",
        "if project_id is None and user.role != UserRole.ADMIN:",
        "if False and project_id is None and user.role != UserRole.ADMIN:",
        "test_non_admin_chat_requires_a_project",
    ),
    Mutation(
        "revoked-session-membership",
        "backend/app/core/deps.py",
        "if accessible is not None and session.project_id not in accessible:",
        "if False and accessible is not None and session.project_id not in accessible:",
        "test_existing_chat_session_rechecks_current_project_membership",
    ),
    Mutation(
        "sql-summary-scope",
        "backend/app/services/chat_service.py",
        "elif allowed_project_ids is not None:\n        # Match the Mongo half above.",
        "elif False and allowed_project_ids is not None:\n        # Match the Mongo half above.",
        "test_run_summary_sql_fallback_uses_allowed_project_scope",
    ),
    Mutation(
        "prompt-injection",
        "backend/app/services/rag_generation_service.py",
        "chunk_text = sanitize_free_text(chunk.chunk_text, max_length=2000)",
        "chunk_text = chunk.chunk_text[:2000]",
        "test_retrieved_instructions_are_inert_and_neutralized",
    ),
    Mutation(
        "fabricated-citations",
        "backend/app/services/rag_generation_service.py",
        "for chunk in _cited_chunks_for_case(case, chunks):",
        "for chunk in chunks[:MAX_CITATIONS_PER_CASE]:",
        "test_citations_require_explicit_valid_evidence_ids",
    ),
    Mutation(
        "timeout-stub",
        "backend/app/services/rag_generation_service.py",
        'raise RagGenerationUnavailable("test case generation timed out") from exc',
        "return _stub_generated_cases(effective_prompt)",
        "test_timeout_never_returns_fabricated_cases",
    ),
    Mutation(
        "raw-prompt-persistence",
        "backend/app/services/rag_generation_service.py",
        "prompt_text=safe_prompt_text[:5000] if safe_prompt_text else None,",
        "prompt_text=prompt_text[:5000] if prompt_text else None,",
        "test_prompt_is_redacted_and_provenance_is_hashed_before_persistence",
    ),
    Mutation(
        "source-filter-cap",
        "backend/app/services/rag_retrieval_service.py",
        "if source_ids:\n        # Per-source queries merged for precise filtering",
        "if source_ids and len(source_ids) <= 10:\n        # Per-source queries merged for precise filtering",
        "test_more_than_ten_selected_sources_remain_source_scoped",
    ),
    Mutation(
        "stale-vector-authority",
        "backend/app/services/rag_retrieval_service.py",
        "chunks = [chunk for chunk in chunks if chunk.source_id in source_meta]",
        "chunks = list(chunks)",
        "test_relational_source_lifecycle_is_authoritative_over_vectors",
    ),
    Mutation(
        "source-lineage-delete",
        "backend/app/services/knowledge_source_service.py",
        "source.is_archived = True",
        "source.is_archived = False",
        "test_delete_archives_and_retires_source_without_erasing_lineage",
    ),
    Mutation(
        "unsafe-source-url",
        "backend/app/services/knowledge_source_service.py",
        "        validate_url_scheme(canonical_url)",
        "        pass",
        "test_source_creation_rejects_unsafe_scheme_before_insert",
    ),
    Mutation(
        "minio-false-success",
        "backend/app/services/knowledge_sync_service.py",
        "    await storage.put_object(\n",
        "    return key\n    await storage.put_object(\n",
        "test_minio_failure_cannot_return_a_success_path",
    ),
    Mutation(
        "unsafe-email-link",
        "backend/app/services/notification/email_service.py",
        'dashboard_url = _safe_dashboard_url(metadata.get("dashboard_url"))',
        'dashboard_url = str(metadata.get("dashboard_url", "#"))',
        "test_notification_html_escapes_content_and_refuses_unsafe_links",
    ),
)


def run_test(test: str, suffix: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:testlookup",
            "--basetemp", f".pytest-tmp-exploratory-m10-mutation-{suffix}",
            f"{TEST_FILE}::{test}",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    originals: dict[Path, bytes] = {}
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        originals.setdefault(path, path.read_bytes())
        text = originals[path].decode("utf-8")
        if text.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")
        baseline = run_test(mutation.test, f"baseline-{mutation.name}")
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed\n{baseline.stdout}{baseline.stderr}"
            )
        try:
            path.write_text(
                text.replace(mutation.safe, mutation.unsafe, 1),
                encoding="utf-8",
                newline="",
            )
            mutated = run_test(mutation.test, mutation.name)
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{mutation.name} was not killed with pytest exit 1\n"
                    f"{mutated.stdout}{mutated.stderr}"
                )
        finally:
            path.write_bytes(originals[path])
    for path, original in originals.items():
        if path.read_bytes() != original:
            raise AssertionError(f"mutation harness did not restore {path}")
    print(f"M10 RAG trust mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
