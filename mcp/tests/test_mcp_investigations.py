"""
Tests for the AI-5 MCP surface: Investigator tools, the run-investigation
resource, the record_fix_outcome learning-loop tool, and the packaged
fix_this_flaky_test prompt.

Follows the suite's conventions: static file-content checks (no FastMCP /
backend import needed) plus functional coverage of the pure module-level
helpers that own request-building, validation, and compact rendering.
"""
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Registration: module, tools, resource, prompt
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestInvestigationRegistration:
    def test_module_exists(self):
        assert (MCP_DIR / "tools" / "investigations.py").is_file()

    def test_registered_in_server(self):
        content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
        assert "investigations.register(mcp)" in content

    def test_tools_declared(self):
        content = (MCP_DIR / "tools" / "investigations.py").read_text(encoding="utf-8")
        for tool in ("start_investigation", "get_investigation", "list_investigations"):
            assert f"async def {tool}(" in content, f"missing tool {tool}"

    def test_wraps_pinned_rest_contract(self):
        """The tools must hit the #383 wire contract paths verbatim."""
        content = (MCP_DIR / "tools" / "investigations.py").read_text(encoding="utf-8")
        assert "/investigations" in content
        assert 'f"/api/v1/runs/{run_id}/investigations"' in content
        assert 'f"/api/v1/investigations/{investigation_id}"' in content
        assert 'f"/api/v1/projects/{project_id}/investigations"' in content

    def test_start_docstring_declares_shadow_mode(self):
        """start_investigation is a safe write — its docstring must say the
        Investigator is shadow-mode and never acts."""
        content = (MCP_DIR / "tools" / "investigations.py").read_text(encoding="utf-8")
        start = content.index("async def start_investigation")
        end = content.index("async def get_investigation")
        docstring = content[start:end]
        assert "SHADOW-MODE" in docstring or "shadow" in docstring.lower()
        assert "never executes actions" in docstring or "never acts" in docstring.lower()

    def test_start_surfaces_policy_conflict_budget_codes(self):
        """403 / 409 / 429 are first-class structured answers, documented."""
        content = (MCP_DIR / "tools" / "investigations.py").read_text(encoding="utf-8")
        for code in ("403", "409", "429"):
            assert code in content, f"missing {code} in structured-result docs"
        # Errors must go through the structured error_payload shape.
        assert "api.error_payload(exc)" in content

    def test_resource_registered(self):
        content = (MCP_DIR / "resources" / "registry.py").read_text(encoding="utf-8")
        assert 'testlookup://runs/{run_id}/investigation' in content

    def test_server_instructions_mention_investigator(self):
        content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
        assert "start_investigation" in content
        assert "record_fix_outcome" in content


class TestFixOutcomeRegistration:
    def test_tool_declared(self):
        content = (MCP_DIR / "tools" / "feedback.py").read_text(encoding="utf-8")
        assert "async def record_fix_outcome(" in content

    def test_posts_to_fix_outcomes_endpoint(self):
        content = (MCP_DIR / "tools" / "feedback.py").read_text(encoding="utf-8")
        assert "/fix-outcomes" in content

    def test_docstring_pins_human_indirect_provenance(self):
        content = (MCP_DIR / "tools" / "feedback.py").read_text(encoding="utf-8")
        assert "human_indirect" in content


class TestFixThisFlakyPrompt:
    def test_prompt_registered(self):
        content = (MCP_DIR / "prompts" / "templates.py").read_text(encoding="utf-8")
        assert "def fix_this_flaky_test(" in content
        assert '"mcp.fix_this_flaky_test"' in content

    def test_prompt_version_pinned(self):
        from prompts import templates  # noqa: PLC0415

        assert "mcp.fix_this_flaky_test" in templates.PROMPT_TEMPLATE_VERSIONS
        assert "mcp.fix_this_flaky_test" in templates.PROMPT_TEMPLATES

    def test_template_renders_with_format(self):
        """The template must survive str.format (no stray unescaped braces)
        and interpolate both parameters."""
        from prompts import templates  # noqa: PLC0415

        out = templates.PROMPT_TEMPLATES["mcp.fix_this_flaky_test"].format(
            project_id="proj-uuid-123", fingerprint="deadbeefcafe0123",
        )
        assert "proj-uuid-123" in out
        assert "deadbeefcafe0123" in out

    def test_template_walks_the_full_loop(self):
        """The packaged workflow must chain evidence → investigator →
        quarantine proposal → local fix → record_fix_outcome."""
        from prompts import templates  # noqa: PLC0415

        text = templates.PROMPT_TEMPLATES["mcp.fix_this_flaky_test"]
        for step in (
            "get_test_step_flips",
            "get_investigation",
            "propose_quarantine",
            "record_fix_outcome",
        ):
            assert step in text, f"workflow missing step {step}"
        # Order matters: containment before fix, outcome recording last.
        assert text.index("propose_quarantine") < text.index("record_fix_outcome")

    def test_negative_outcomes_are_first_class(self):
        from prompts import templates  # noqa: PLC0415

        text = templates.PROMPT_TEMPLATES["mcp.fix_this_flaky_test"]
        assert "not_fixed" in text
        assert "reverted" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pure helper: _fix_outcome_body (enum + fingerprint validation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFixOutcomeBody:
    @staticmethod
    def _helper():
        from tools import feedback as fb  # noqa: PLC0415

        return fb._fix_outcome_body

    def test_minimal_body(self):
        body = self._helper()("deadbeef", "fixed")
        assert body == {"fingerprint": "deadbeef", "outcome": "fixed"}

    def test_all_outcomes_accepted(self):
        for outcome in ("fixed", "not_fixed", "reverted"):
            assert self._helper()("fp", outcome)["outcome"] == outcome

    def test_outcome_is_normalized(self):
        assert self._helper()("fp", " Fixed ")["outcome"] == "fixed"

    def test_unknown_outcome_rejected(self):
        with pytest.raises(ValueError, match="Unknown outcome"):
            self._helper()("fp", "merged")

    def test_fingerprint_required(self):
        with pytest.raises(ValueError, match="fingerprint is required"):
            self._helper()("", "fixed")
        with pytest.raises(ValueError, match="fingerprint is required"):
            self._helper()("   ", "fixed")

    def test_optional_fields_included_when_given(self):
        body = self._helper()("fp", "reverted", reference="org/repo#42", comment="flaked again")
        assert body["reference"] == "org/repo#42"
        assert body["comment"] == "flaked again"

    def test_optional_fields_omitted_when_absent(self):
        body = self._helper()("fp", "fixed")
        assert "reference" not in body
        assert "comment" not in body


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pure helpers: compact rendering + limit clamping
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCompactDetail:
    @staticmethod
    def _mod():
        from tools import investigations as inv  # noqa: PLC0415

        return inv

    def test_short_fields_pass_through_unchanged(self):
        detail = {
            "id": "i1",
            "status": "completed",
            "hypotheses": [
                {"id": "infra", "summary": "short", "evidence": ["e1"], "confidence": 40}
            ],
            "verdict": {"primary_cause": "infra", "confidence": 40, "narrative": "brief"},
        }
        out = self._mod()._compact_detail(detail)
        assert out["hypotheses"][0]["summary"] == "short"
        assert out["hypotheses"][0]["evidence"] == ["e1"]
        assert out["verdict"]["narrative"] == "brief"
        # Non-prose keys of the pinned wire shape survive.
        assert out["id"] == "i1" and out["status"] == "completed"

    def test_long_summary_and_narrative_truncated_with_marker(self):
        mod = self._mod()
        detail = {
            "hypotheses": [{"id": "h", "summary": "x" * 1000, "evidence": []}],
            "verdict": {"narrative": "y" * 5000},
        }
        out = mod._compact_detail(detail)
        assert len(out["hypotheses"][0]["summary"]) <= mod._MAX_TEXT
        assert out["hypotheses"][0]["summary"].endswith("…")
        assert len(out["verdict"]["narrative"]) <= 1200
        assert out["verdict"]["narrative"].endswith("…")

    def test_evidence_capped_with_honest_elision_marker(self):
        mod = self._mod()
        detail = {
            "hypotheses": [{"id": "h", "summary": "", "evidence": [f"e{i}" for i in range(9)]}],
            "verdict": None,
        }
        out = mod._compact_detail(detail)
        evidence = out["hypotheses"][0]["evidence"]
        assert len(evidence) == mod._MAX_EVIDENCE_ITEMS + 1
        assert "4 more evidence item(s) elided" in evidence[-1]

    def test_dict_evidence_values_truncated(self):
        mod = self._mod()
        detail = {
            "hypotheses": [
                {"id": "h", "evidence": [{"type": "log", "text": "z" * 900}]}
            ],
        }
        out = mod._compact_detail(detail)
        item = out["hypotheses"][0]["evidence"][0]
        assert item["type"] == "log"
        assert len(item["text"]) <= mod._MAX_TEXT

    def test_input_detail_not_mutated(self):
        mod = self._mod()
        hyp = {"id": "h", "summary": "s" * 1000, "evidence": []}
        detail = {"hypotheses": [hyp], "verdict": {"narrative": "n" * 5000}}
        mod._compact_detail(detail)
        assert len(hyp["summary"]) == 1000
        assert len(detail["verdict"]["narrative"]) == 5000

    def test_degrades_on_malformed_payload(self):
        out = self._mod()._compact_detail({})
        assert out["hypotheses"] == []
        assert "verdict" not in out or out.get("verdict") is None


class TestClampLimit:
    @staticmethod
    def _helper():
        from tools import investigations as inv  # noqa: PLC0415

        return inv._clamp_limit

    def test_in_range_passes(self):
        assert self._helper()(20) == 20

    def test_clamps_to_api_bounds(self):
        assert self._helper()(0) == 1
        assert self._helper()(9999) == 100

    def test_garbage_falls_back_to_default(self):
        assert self._helper()("lots") == 20
        assert self._helper()(None) == 20
