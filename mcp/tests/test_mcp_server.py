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
