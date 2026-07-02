"""
Tests for MCP Server Alignment.

Covers:
  - Auth uses form-encoded data (not JSON)
  - Health calls correct endpoint
  - All tool modules exist and have register() functions
  - New tool modules (intelligence, deep, search, reports) are registered
  - Server documentation is updated
"""
from pathlib import Path

MCP_DIR = Path(__file__).parent.parent


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 0: Auth Format Fix
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestAuthFormat:
    def test_client_uses_form_encoded(self):
        """client.py must use data= (form-encoded) not json= for login."""
        content = (MCP_DIR / "client.py").read_text(encoding="utf-8")
        # Must have data= for the login call
        assert "data={" in content or 'data={"username"' in content
        # Must NOT use json= for the login call
        # (json= exists for other POST calls, so check specifically around auth/login)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "auth/login" in line:
                # The next few lines should contain data=, not json=
                context = "\n".join(lines[max(0, i-2):i+5])
                assert "data=" in context, f"Login call near line {i+1} should use data= not json="

    def test_tools_auth_uses_form_encoded(self):
        """tools/auth.py login must use data= (form-encoded)."""
        content = (MCP_DIR / "tools" / "auth.py").read_text(encoding="utf-8")
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "auth/login" in line:
                context = "\n".join(lines[max(0, i-2):i+5])
                assert "data=" in context, f"Login call near line {i+1} should use data="


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 0: Health Endpoint Fix
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestHealthEndpoint:
    def test_health_calls_correct_path(self):
        """Health check should NOT call /health (nonexistent)."""
        content = (MCP_DIR / "tools" / "auth.py").read_text(encoding="utf-8")
        # Should call /health/ready or /health/details, not bare /health
        assert "/health/ready" in content or "/health/details" in content
        # The old broken path should not be the primary call
        assert 'get("/health")' not in content

    def test_health_reads_checks_not_dependencies(self):
        """Regression: /health/details returns per-dependency status under the
        ``checks`` key. The renderer previously read a non-existent
        ``dependencies`` key, so dependency health was silently dropped."""
        content = (MCP_DIR / "tools" / "auth.py").read_text(encoding="utf-8")
        assert 'data.get("dependencies"' not in content
        assert 'data.get("checks")' in content


class TestHealthRenderer:
    """Functional coverage of the pure ``_render_health`` renderer.

    Mirrors the ``_render_step_flips`` renderer suite: the tool's formatting
    is pulled into a pure helper so it can be exercised against a realistic
    ``/health/details`` payload without a live backend.
    """

    @staticmethod
    def _renderer():
        import sys

        sys.path.insert(0, str(MCP_DIR))
        from tools import auth as auth_tool  # noqa: PLC0415

        return auth_tool._render_health

    def test_renders_status_version_env_and_uptime(self):
        out = self._renderer()(
            {
                "status": "healthy",
                "version": "0.1.0",
                "env": "production",
                "uptime_seconds": 42,
                "checks": {},
            }
        )
        assert "**Status:** healthy" in out
        assert "**Version:** 0.1.0" in out
        assert "**Environment:** production" in out
        assert "**Uptime:** 42s" in out

    def test_surfaces_per_dependency_status_from_checks(self):
        # The core regression: dependency health lives under ``checks`` and each
        # value is a dict whose ``status`` we surface (not the whole dict).
        out = self._renderer()(
            {
                "status": "degraded",
                "version": "0.1.0",
                "env": "production",
                "checks": {
                    "postgres": {"status": "ok", "latency_ms": 2},
                    "mongo": {"status": "ok"},
                    "redis": {"status": "degraded", "error": "timeout"},
                },
            }
        )
        assert "**Dependencies:**" in out
        assert "  - postgres: ok" in out
        assert "  - mongo: ok" in out
        assert "  - redis: degraded" in out
        # The raw nested dict must not leak into the output.
        assert "latency_ms" not in out

    def test_no_dependency_section_when_checks_absent(self):
        out = self._renderer()({"status": "healthy", "version": "0.1.0", "env": "dev"})
        assert "**Status:** healthy" in out
        assert "**Dependencies:**" not in out

    def test_degrades_on_malformed_checks(self):
        # A non-dict ``checks`` (or a plain-string dep value) must not raise.
        out = self._renderer()(
            {"status": "healthy", "checks": {"postgres": "ok", "mongo": None}}
        )
        assert "  - postgres: ok" in out
        assert "  - mongo: None" in out

    def test_empty_payload_degrades_to_unknowns(self):
        out = self._renderer()({})
        assert "**Status:** unknown" in out
        assert "**Version:** unknown" in out
        assert "**Dependencies:**" not in out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 1: Tool Modules Exist
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestToolModules:
    def test_original_tools_exist(self):
        """Original tool modules must still exist."""
        for name in ["auth", "projects", "runs", "metrics", "analytics", "analysis", "release"]:
            path = MCP_DIR / "tools" / f"{name}.py"
            assert path.is_file(), f"Missing original tool: {name}.py"

    def test_new_tools_exist(self):
        """New tool modules must exist."""
        for name in ["intelligence", "deep", "search", "reports"]:
            path = MCP_DIR / "tools" / f"{name}.py"
            assert path.is_file(), f"Missing new tool: {name}.py"

    def test_tool_modules_have_register(self):
        """Every tool module must export a register() function."""
        tool_dir = MCP_DIR / "tools"
        for py_file in tool_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            assert "def register(" in content, f"{py_file.name} missing register() function"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 1: New Tools Registered in Server
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestServerRegistration:
    def test_new_tools_imported(self):
        """server.py must import the new tool modules."""
        content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
        for module in ["intelligence", "deep", "search", "reports"]:
            assert module in content, f"server.py missing import for {module}"

    def test_new_tools_registered(self):
        """server.py must call register() on new tool modules."""
        content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
        for module in ["intelligence", "deep", "search", "reports"]:
            assert f"{module}.register(mcp)" in content, f"server.py missing {module}.register(mcp)"

    def test_server_instructions_updated(self):
        """Server instructions should mention Run Intelligence and Deep Analysis."""
        content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
        assert "intelligence" in content.lower()
        assert "deep" in content.lower()
        assert "global_search" in content or "search" in content.lower()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 1: Intelligence Tool Content
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestIntelligenceTools:
    def test_has_get_run_intelligence(self):
        content = (MCP_DIR / "tools" / "intelligence.py").read_text(encoding="utf-8")
        assert "get_run_intelligence" in content

    def test_has_refresh_intelligence(self):
        content = (MCP_DIR / "tools" / "intelligence.py").read_text(encoding="utf-8")
        assert "refresh_intelligence" in content

    def test_calls_intelligence_endpoint(self):
        content = (MCP_DIR / "tools" / "intelligence.py").read_text(encoding="utf-8")
        assert "/intelligence" in content


class TestDeepTools:
    def test_has_trigger(self):
        content = (MCP_DIR / "tools" / "deep.py").read_text(encoding="utf-8")
        assert "trigger_deep_analysis" in content

    def test_has_pipeline_status(self):
        content = (MCP_DIR / "tools" / "deep.py").read_text(encoding="utf-8")
        assert "get_pipeline_status" in content

    def test_has_clusters(self):
        content = (MCP_DIR / "tools" / "deep.py").read_text(encoding="utf-8")
        assert "get_failure_clusters" in content

    def test_calls_deep_endpoint(self):
        content = (MCP_DIR / "tools" / "deep.py").read_text(encoding="utf-8")
        assert "/deep-investigate/" in content


class TestSearchTools:
    def test_has_global_search(self):
        content = (MCP_DIR / "tools" / "search.py").read_text(encoding="utf-8")
        assert "global_search" in content

    def test_calls_global_search_endpoint(self):
        content = (MCP_DIR / "tools" / "search.py").read_text(encoding="utf-8")
        assert "/search/global" in content


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 6: Granular steps surfaced in MCP (runs / analysis / search)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGranularStepsRuns:
    """get_test_case gains an include_steps option that renders the step tree."""

    def test_get_test_case_has_include_steps_option(self):
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert "include_steps" in content

    def test_fetches_steps_endpoint(self):
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert "/tests/{test_id}/steps" in content

    def test_renders_step_table_header(self):
        """A markdown step table must be emitted when steps are included."""
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert "### Steps" in content
        assert "| # | Step | Status | Duration | Assertion |" in content

    def test_has_step_tree_renderers(self):
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert "_render_step_tree" in content
        assert "_render_step_rows" in content

    def test_step_renderers_are_recursive_over_nested_steps(self):
        """Nested children (node['steps']) must be walked for the tree."""
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert 'node.get("steps")' in content

    def test_default_output_unchanged_when_steps_omitted(self):
        """include_steps must default to False so existing output is preserved."""
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert "include_steps: bool = False" in content


class TestGranularStepsAnalysis:
    """trigger_ai_analysis surfaces 'step N failed: <assertion>' evidence."""

    def test_has_first_failed_step_helper(self):
        content = (MCP_DIR / "tools" / "analysis.py").read_text(encoding="utf-8")
        assert "_first_failed_step_evidence" in content

    def test_evidence_phrase_present(self):
        content = (MCP_DIR / "tools" / "analysis.py").read_text(encoding="utf-8")
        assert "step {i} failed" in content

    def test_picks_failed_or_broken_step(self):
        content = (MCP_DIR / "tools" / "analysis.py").read_text(encoding="utf-8")
        assert '"FAILED"' in content and '"BROKEN"' in content

    def test_fetches_steps_endpoint(self):
        content = (MCP_DIR / "tools" / "analysis.py").read_text(encoding="utf-8")
        assert "/tests/" in content and "/steps" in content

    def test_best_effort_run_id_optional(self):
        """run_id is optional — step evidence is skipped gracefully without it."""
        content = (MCP_DIR / "tools" / "analysis.py").read_text(encoding="utf-8")
        assert "run_id: Optional[str] = None" in content


class TestGranularStepsSearch:
    """search_tests notes when a result likely matched on step text."""

    def test_has_step_match_note_helper(self):
        content = (MCP_DIR / "tools" / "search.py").read_text(encoding="utf-8")
        assert "_step_match_note" in content

    def test_subtitle_mentions_step_match(self):
        content = (MCP_DIR / "tools" / "search.py").read_text(encoding="utf-8")
        # The note is honest that it can't distinguish error-message from step
        # matches (the search payload exposes neither field).
        assert "may match error/step text" in content

    def test_step_note_applied_to_results(self):
        content = (MCP_DIR / "tools" / "search.py").read_text(encoding="utf-8")
        assert "_step_match_note(" in content


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FLK-P6: cross-run step-flip report surfaced via MCP (get_test_step_flips)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestStepFlipsRuns:
    """runs.py gains a get_test_step_flips tool over the cross-run report.

    Static checks (matching the rest of this suite) plus a functional check of
    the pure ``_render_step_flips`` renderer so the three report states stay
    distinguishable.
    """

    def test_has_get_test_step_flips_tool(self):
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert "get_test_step_flips" in content

    def test_fetches_step_flips_endpoint(self):
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert "/tests/{test_id}/step-flips" in content

    def test_has_step_flip_renderer(self):
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert "_render_step_flips" in content

    def test_renders_step_flip_table_header(self):
        content = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
        assert "| Step | Flips | Runs Observed | Current |" in content


class TestStepFlipRenderer:
    """Functional coverage of the pure ``_render_step_flips`` renderer."""

    @staticmethod
    def _renderer():
        import sys
        sys.path.insert(0, str(MCP_DIR))
        from tools import runs as runs_tool  # noqa: PLC0415

        return runs_tool._render_step_flips

    def test_insufficient_history(self):
        out = self._renderer()({"test_id": "abc123ff", "report": {"runs_analyzed": 1}})
        assert "Not enough cross-run step history" in out
        assert "abc123ff"[:8] in out

    def test_stable_when_no_flip(self):
        out = self._renderer()(
            {"test_id": "t", "report": {"runs_analyzed": 5, "has_step_flip": False}}
        )
        assert "steps are stable" in out

    def test_flipping_steps_table(self):
        out = self._renderer()(
            {
                "test_id": "deadbeefcafe",
                "report": {
                    "runs_analyzed": 6,
                    "has_step_flip": True,
                    "total_flips": 3,
                    "summary": "Step 'login' flickered 3 times.",
                    "flipping_steps": [
                        {
                            "ordinal": 2,
                            "step_name": "login",
                            "flip_count": 3,
                            "runs_observed": 6,
                            "last_status": "FAILED",
                        }
                    ],
                },
            }
        )
        assert "3 total flips across 6 runs" in out
        assert "Step 'login' flickered 3 times." in out
        assert "| Step | Flips | Runs Observed | Current |" in out
        assert "| login | 3 | 6 |" in out
        assert "FAILED" in out

    def test_unnamed_step_falls_back_to_ordinal(self):
        out = self._renderer()(
            {
                "test_id": "x",
                "report": {
                    "runs_analyzed": 2,
                    "has_step_flip": True,
                    "total_flips": 1,
                    "flipping_steps": [
                        {"ordinal": 4, "flip_count": 1, "runs_observed": 2, "last_status": "PASSED"}
                    ],
                },
            }
        )
        assert "step #4" in out

    def test_malformed_payload_degrades(self):
        out = self._renderer()({})
        assert "Not enough cross-run step history" in out
