"""Tool output reaches an LLM prompt without an Authorization credential, whatever the scheme.

``input_sanitizer`` cleans tool output before it is placed in a prompt. Its
Authorization pattern replaced the first token after the colon -- the scheme's
name -- so ``Authorization: Basic dXNl...`` became ``Authorization: [REDACTED]
dXNl...`` with the credential intact (found while fixing the same bug in log
redaction, re-audit H3).
"""
from __future__ import annotations

import pytest

from app.services.input_sanitizer import sanitize_tool_output

CREDENTIAL = "dXNlcjpwYXNzd29yZA=="


@pytest.mark.parametrize("scheme", ["Basic", "Bearer", "Digest", "Negotiate", "NTLM", "Token", "basic"])
def test_the_credential_is_redacted_whatever_the_scheme(scheme):
    out = sanitize_tool_output(f"curl -H 'Authorization: {scheme} {CREDENTIAL}' -> 401")
    assert CREDENTIAL not in out, out
    assert "[REDACTED]" in out


def test_a_credential_with_no_scheme_is_redacted_too():
    out = sanitize_tool_output(f"Authorization: {CREDENTIAL}")
    assert CREDENTIAL not in out, out


def test_the_rest_of_the_output_survives():
    out = sanitize_tool_output(f"GET /api/v1/runs 401\nAuthorization: Basic {CREDENTIAL}\nretrying")
    assert "GET /api/v1/runs 401" in out
    assert "retrying" in out
