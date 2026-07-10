"""
Tests for the MCP write-path tools (PMF US-14.1).

Follows the established pattern in this suite: static file-content checks
(no FastMCP / backend import needed) plus functional coverage of the pure
module-level helpers that own request-building and dry-run branching.
"""
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Modules exist and are registered
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestWriteToolRegistration:
    def test_new_modules_exist(self):
        for name in ["feedback", "assignments", "notifications"]:
            assert (MCP_DIR / "tools" / f"{name}.py").is_file(), f"missing tools/{name}.py"

    def test_new_modules_registered_in_server(self):
        content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
        for module in ["feedback", "assignments", "notifications"]:
            assert f"{module}.register(mcp)" in content, f"server.py missing {module}.register(mcp)"

    def test_write_tools_declared(self):
        expected = {
            "quarantine.py": [
                "propose_quarantine",
                "release_quarantine",
                "promote_ready_quarantines",
            ],
            "defects.py": ["create_defect"],
            "feedback.py": ["correct_classification"],
            "assignments.py": ["assign_failure", "get_assignment_options"],
            "notifications.py": ["get_transition_policy", "set_transition_policy"],
        }
        for filename, tools in expected.items():
            content = (MCP_DIR / "tools" / filename).read_text(encoding="utf-8")
            for tool in tools:
                assert f"async def {tool}(" in content, f"{filename} missing tool {tool}"

    def test_server_instructions_mention_write_path(self):
        content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
        assert "propose_quarantine" in content
        assert "correct_classification" in content

    def test_destructive_tools_default_to_dry_run_or_preview(self):
        """Bulk/destructive tools must be safe by default."""
        quarantine = (MCP_DIR / "tools" / "quarantine.py").read_text(encoding="utf-8")
        assert "dry_run: bool = True" in quarantine  # promote_ready_quarantines
        defects = (MCP_DIR / "tools" / "defects.py").read_text(encoding="utf-8")
        assert "dry_run: bool = False" in defects  # create_defect (explicit opt-in)
        # The docstring must instruct agents to preview first.
        assert "dry_run=True first" in defects

    def test_propose_is_a_proposal_not_direct_quarantine(self):
        """propose_quarantine must hit the proposal endpoint (POST /quarantine),
        never approve in the same call — human review stays in the loop."""
        content = (MCP_DIR / "tools" / "quarantine.py").read_text(encoding="utf-8")
        assert '"/api/v1/quarantine", json_body=body' in content
        # It must not chain into the approve endpoint.
        start = content.index("async def propose_quarantine")
        end = content.index("async def release_quarantine")
        assert "/approve" not in content[start:end]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pure helper: promote candidate selection (cap + flag filter)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSelectReadyToPromote:
    @staticmethod
    def _helper():
        from tools import quarantine as q  # noqa: PLC0415

        return q._select_ready_to_promote

    def test_filters_to_ready_rows_only(self):
        rows = [
            {"id": "a", "ready_to_promote": True},
            {"id": "b", "ready_to_promote": False},
            {"id": "c"},  # flag absent = not ready
            {"id": "d", "ready_to_promote": True},
        ]
        picked = self._helper()(rows)
        assert [r["id"] for r in picked] == ["a", "d"]

    def test_caps_batch_at_ten(self):
        rows = [{"id": str(i), "ready_to_promote": True} for i in range(25)]
        assert len(self._helper()(rows)) == 10

    def test_respects_explicit_cap(self):
        rows = [{"id": str(i), "ready_to_promote": True} for i in range(5)]
        assert len(self._helper()(rows, cap=2)) == 2

    def test_empty_input(self):
        assert self._helper()([]) == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pure helper: defect signature validation (fingerprint XOR cluster_id)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDefectSignatureParams:
    @staticmethod
    def _helper():
        from tools import defects as d  # noqa: PLC0415

        return d._signature_params

    def test_fingerprint_only(self):
        assert self._helper()("abc123", None) == {"fingerprint": "abc123"}

    def test_cluster_only(self):
        assert self._helper()(None, "cluster-9") == {"cluster_id": "cluster-9"}

    def test_both_rejected(self):
        with pytest.raises(ValueError):
            self._helper()("abc", "cluster")

    def test_neither_rejected(self):
        with pytest.raises(ValueError):
            self._helper()(None, None)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pure helper: classification-correction body building
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCorrectionBody:
    @staticmethod
    def _helper():
        from tools import feedback as f  # noqa: PLC0415

        return f._correction_body

    def test_rating_is_incorrect_so_backend_applies_correction(self):
        """The backend only rewrites the analysis row when rating ==
        'incorrect' AND corrected_category is set — the body must carry both."""
        body = self._helper()("PRODUCT_BUG")
        assert body == {"rating": "incorrect", "corrected_category": "PRODUCT_BUG"}

    def test_category_normalized_to_upper(self):
        assert self._helper()("flaky")["corrected_category"] == "FLAKY"

    def test_unknown_category_rejected_before_any_http_call(self):
        with pytest.raises(ValueError) as exc:
            self._helper()("NOT_A_CATEGORY")
        assert "PRODUCT_BUG" in str(exc.value)  # allowed list surfaced

    def test_optional_fields_included_when_given(self):
        body = self._helper()("TEST_DATA", comment="c", corrected_root_cause="r")
        assert body["comment"] == "c"
        assert body["corrected_root_cause"] == "r"

    def test_optional_fields_omitted_when_absent(self):
        body = self._helper()("TEST_DATA")
        assert "comment" not in body
        assert "corrected_root_cause" not in body


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pure helper: structured error payloads (never bare strings)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestErrorPayload:
    @staticmethod
    def _helper():
        import client  # noqa: PLC0415

        return client.error_payload

    def test_http_status_error_surfaces_code_and_detail(self):
        import httpx  # noqa: PLC0415

        request = httpx.Request("POST", "http://x/api/v1/quarantine")
        response = httpx.Response(
            403, request=request, json={"detail": "QA_LEAD role required"}
        )
        exc = httpx.HTTPStatusError("forbidden", request=request, response=response)
        payload = self._helper()(exc)
        assert payload["ok"] is False
        assert payload["status_code"] == 403
        assert payload["detail"] == "QA_LEAD role required"

    def test_non_json_body_degrades_to_text(self):
        import httpx  # noqa: PLC0415

        request = httpx.Request("GET", "http://x/y")
        response = httpx.Response(502, request=request, text="bad gateway")
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        payload = self._helper()(exc)
        assert payload["status_code"] == 502
        assert payload["detail"] == "bad gateway"

    def test_plain_exception(self):
        payload = self._helper()(RuntimeError("connection refused"))
        assert payload == {"ok": False, "error": "connection refused"}
