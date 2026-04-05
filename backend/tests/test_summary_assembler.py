"""
Unit tests for summary_assembler.py — all pure functions, no DB required.
"""

# ── Tests for assemble_context ────────────────────────────────────────────────

def test_assemble_context_empty_inputs():
    from app.services.summary_assembler import assemble_context

    result = assemble_context(
        run_data={},
        anomalies=[],
        analyses={},
    )

    assert "facts" in result
    assert "top_analyses" in result
    assert "evidence_snippets" in result
    assert "cluster_rankings" in result
    assert "release_inputs" in result
    assert "owner_hints" in result
    assert "similar_failures" in result
    assert "data_sources_used" in result

    facts = result["facts"]
    assert facts["total_tests"] == 0
    assert facts["failed_tests"] == 0
    assert facts["pass_rate"] == 0.0
    assert facts["top_category"] == "UNKNOWN"


def test_assemble_context_populates_facts():
    from app.services.summary_assembler import assemble_context

    run_data = {
        "build_number": "build-42",
        "branch": "main",
        "total_tests": 200,
        "failed_tests": 10,
        "pass_rate": 95.0,
    }
    result = assemble_context(run_data=run_data, anomalies=[], analyses={})

    facts = result["facts"]
    assert facts["build"] == "build-42"
    assert facts["branch"] == "main"
    assert facts["total_tests"] == 200
    assert facts["failed_tests"] == 10
    assert facts["pass_rate"] == 95.0


def test_assemble_context_category_counts():
    from app.services.summary_assembler import assemble_context

    analyses = {
        "tc-1": {"failure_category": "PRODUCT_BUG", "is_flaky": False, "confidence_score": 80},
        "tc-2": {"failure_category": "PRODUCT_BUG", "is_flaky": False, "confidence_score": 70},
        "tc-3": {"failure_category": "INFRASTRUCTURE", "is_flaky": False, "confidence_score": 60},
        "tc-4": {"failure_category": "FLAKY", "is_flaky": True, "confidence_score": 40},
    }
    result = assemble_context(run_data={}, anomalies=[], analyses=analyses)

    facts = result["facts"]
    assert facts["category_counts"].get("PRODUCT_BUG") == 2
    assert facts["category_counts"].get("INFRASTRUCTURE") == 1
    assert facts["top_category"] == "PRODUCT_BUG"
    assert facts["flaky_count"] == 1


def test_assemble_context_top_analyses_threshold():
    from app.services.summary_assembler import assemble_context

    analyses = {
        "tc-high": {
            "failure_category": "PRODUCT_BUG",
            "is_flaky": False,
            "confidence_score": 85,
            "root_cause_summary": "DB connection pool exhausted",
            "recommended_actions": ["increase pool size"],
            "evidence_references": [],
        },
        "tc-low": {
            "failure_category": "UNKNOWN",
            "is_flaky": False,
            "confidence_score": 30,  # below 50 threshold
            "root_cause_summary": "Unknown error",
            "recommended_actions": [],
            "evidence_references": [],
        },
    }
    result = assemble_context(run_data={}, anomalies=[], analyses=analyses)

    top = result["top_analyses"]
    # Only tc-high should be in top_analyses (confidence >= 50)
    assert len(top) == 1
    assert top[0]["test_id"] == "tc-high"


def test_assemble_context_evidence_snippets():
    from app.services.summary_assembler import assemble_context

    analyses = {
        "tc-1": {
            "failure_category": "PRODUCT_BUG",
            "is_flaky": False,
            "confidence_score": 80,
            "root_cause_summary": "NPE",
            "recommended_actions": [],
            "evidence_references": [
                {"source": "stacktrace", "excerpt": "NullPointerException at line 10"},
                {"source": "splunk", "excerpt": "ERROR timeout after 30s"},
            ],
        }
    }
    result = assemble_context(run_data={}, anomalies=[], analyses=analyses)

    snippets = result["evidence_snippets"]
    assert len(snippets) == 2
    sources = {s["source"] for s in snippets}
    assert "stacktrace" in sources
    assert "splunk" in sources
    assert "stacktrace" in result["data_sources_used"]


def test_assemble_context_release_inputs():
    from app.services.summary_assembler import assemble_context

    release_decision = {
        "recommendation": "NO_GO",
        "risk_score": 75,
        "blocking_issues": ["Critical auth failures"],
        "conditions_for_go": [],
    }
    result = assemble_context(
        run_data={},
        anomalies=[],
        analyses={},
        release_decision=release_decision,
    )

    ri = result["release_inputs"]
    assert ri["recommendation"] == "NO_GO"
    assert ri["risk_score"] == 75
    assert "Critical auth failures" in ri["blocking_issues"]


def test_assemble_context_similar_failures():
    from app.services.summary_assembler import assemble_context

    similar = [{"test_name": "test_login", "error_message": "Timeout"}]
    result = assemble_context(
        run_data={}, anomalies=[], analyses={}, similar_failures=similar
    )
    assert result["similar_failures"] == similar


# ── Tests for extract_citations ────────────────────────────────────────────────

def test_extract_citations_matches_verbatim():
    from app.services.summary_assembler import extract_citations

    text = "The error was: NullPointerException at com.example.service.Foo which caused failures"
    evidence_snippets = [
        {
            "source": "stacktrace",
            "excerpt": "NullPointerException at com.example.service.Foo which caused",
            "test_id": "tc-1",
        }
    ]
    citations = extract_citations(text, evidence_snippets)
    assert len(citations) == 1
    assert citations[0]["source"] == "stacktrace"
    assert citations[0]["test_id"] == "tc-1"


def test_extract_citations_no_match():
    from app.services.summary_assembler import extract_citations

    text = "Build failed due to infrastructure issues"
    evidence_snippets = [
        {
            "source": "stacktrace",
            "excerpt": "NullPointerException at line 42 in completely different code",
            "test_id": "tc-1",
        }
    ]
    citations = extract_citations(text, evidence_snippets)
    assert citations == []


def test_extract_citations_short_excerpt_skipped():
    from app.services.summary_assembler import extract_citations

    text = "Some text that might match"
    evidence_snippets = [
        {"source": "src", "excerpt": "Short", "test_id": "tc-1"},  # < 10 chars
    ]
    citations = extract_citations(text, evidence_snippets)
    assert citations == []


def test_extract_citations_empty_inputs():
    from app.services.summary_assembler import extract_citations

    assert extract_citations("", []) == []
    assert extract_citations("some text", []) == []


def test_extract_citations_multiple_matches():
    from app.services.summary_assembler import extract_citations

    text = (
        "Connection pool exhausted after retry limit reached "
        "also Database timeout exceeded on query execution"
    )
    snippets = [
        {"source": "log1", "excerpt": "Connection pool exhausted after retry limit reached", "test_id": "tc-1"},
        {"source": "log2", "excerpt": "Database timeout exceeded on query execution", "test_id": "tc-2"},
        {"source": "log3", "excerpt": "Completely unrelated content that wont match here at all", "test_id": "tc-3"},
    ]
    citations = extract_citations(text, snippets)
    assert len(citations) == 2
    test_ids = {c["test_id"] for c in citations}
    assert "tc-1" in test_ids
    assert "tc-2" in test_ids


# ── Tests for build_context_string ────────────────────────────────────────────

def test_build_context_string_executive_shorter():
    from app.services.summary_assembler import assemble_context, build_context_string

    analyses = {
        "tc-1": {
            "failure_category": "PRODUCT_BUG",
            "is_flaky": False,
            "confidence_score": 80,
            "root_cause_summary": "DB pool exhausted",
            "recommended_actions": [],
            "evidence_references": [{"source": "stacktrace", "excerpt": "NPE at line 10 in FooService"}],
        }
    }
    assembled = assemble_context(
        run_data={"build_number": "b1", "pass_rate": 90.0, "total_tests": 100, "failed_tests": 10},
        anomalies=[],
        analyses=analyses,
    )

    exec_ctx = build_context_string(assembled, mode="executive")
    dev_ctx = build_context_string(assembled, mode="developer")

    # Developer context should be longer (includes evidence)
    assert len(dev_ctx) > len(exec_ctx)
    # Executive context should not include evidence excerpts header
    assert "Evidence excerpts" not in exec_ctx


def test_build_context_string_developer_includes_evidence():
    from app.services.summary_assembler import assemble_context, build_context_string

    analyses = {
        "tc-1": {
            "failure_category": "PRODUCT_BUG",
            "is_flaky": False,
            "confidence_score": 80,
            "root_cause_summary": "Cache miss on critical path",
            "recommended_actions": [],
            "evidence_references": [
                {"source": "stacktrace", "excerpt": "CacheMissException in Redis client at method get()"}
            ],
        }
    }
    assembled = assemble_context(
        run_data={"build_number": "b2", "pass_rate": 85.0, "total_tests": 50, "failed_tests": 7},
        anomalies=[],
        analyses=analyses,
    )

    dev_ctx = build_context_string(assembled, mode="developer")
    assert "Evidence excerpts" in dev_ctx


def test_build_context_string_manager_includes_root_causes():
    from app.services.summary_assembler import assemble_context, build_context_string

    analyses = {
        "tc-1": {
            "failure_category": "INFRASTRUCTURE",
            "is_flaky": False,
            "confidence_score": 75,
            "root_cause_summary": "Pod OOMKilled during load spike",
            "recommended_actions": [],
            "evidence_references": [],
        }
    }
    assembled = assemble_context(
        run_data={"build_number": "b3", "pass_rate": 70.0, "total_tests": 100, "failed_tests": 30},
        anomalies=[],
        analyses=analyses,
    )

    mgr_ctx = build_context_string(assembled, mode="manager")
    # Manager sees root causes but not evidence
    assert "High-confidence root causes" in mgr_ctx
    assert "Evidence excerpts" not in mgr_ctx


# ── Tests for _build_owner_hints ──────────────────────────────────────────────

def test_owner_hints_product_bug():
    from app.services.summary_assembler import _build_owner_hints

    hints = _build_owner_hints("PRODUCT_BUG", 80.0, {})
    assert "product bugs" in hints["developer"].lower()
    assert "regression" in hints["qa"].lower()
    assert "rollback" in hints["sre"].lower()


def test_owner_hints_infrastructure():
    from app.services.summary_assembler import _build_owner_hints

    hints = _build_owner_hints("INFRASTRUCTURE", 70.0, {})
    assert "dependencies" in hints["developer"].lower() or "service" in hints["developer"].lower()
    assert "infrastructure" in hints["sre"].lower() or "infra" in hints["sre"].lower()


def test_owner_hints_flaky():
    from app.services.summary_assembler import _build_owner_hints

    hints = _build_owner_hints("FLAKY", 90.0, {})
    assert "flaky" in hints["qa"].lower() or "quarantine" in hints["qa"].lower()


def test_owner_hints_with_release_recommendation():
    from app.services.summary_assembler import _build_owner_hints

    hints = _build_owner_hints("PRODUCT_BUG", 80.0, {"recommendation": "NO_GO"})
    assert "NO_GO" in hints["release_manager"]


def test_owner_hints_without_release_recommendation():
    from app.services.summary_assembler import _build_owner_hints

    hints = _build_owner_hints("UNKNOWN", 60.0, {})
    assert "60.0%" in hints["release_manager"]
