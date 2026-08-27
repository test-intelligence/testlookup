"""A non-JSON error body must reach the operator.

Errors that never reach the application carry no ``detail`` field, because
nothing in the app produced them. A reverse proxy answers in plain text, and
that text is usually the whole diagnosis.

Found by fault injection against a live deployment: scaling Redis to zero made
every backend replica fail its ``/health/ready`` probe, Kubernetes pulled them
from the Service, and Traefik answered ``503 no available server``. The CLI
printed ``Server error (503).`` and discarded the only words that explained it —
so the operator was told the server erred, not that there was no server.
"""
from __future__ import annotations

import httpx
import pytest

from testlookup_cli.client import (
    _MAX_BODY_SNIPPET,
    _error_body_snippet,
    _raise_for_status,
)
from testlookup_cli.errors import CLIError


def _resp(status: int, text: str = "", json_body=None) -> httpx.Response:
    if json_body is not None:
        return httpx.Response(status, json=json_body)
    return httpx.Response(status, text=text)


def test_the_proxy_text_reaches_the_message():
    """The case this exists for."""
    with pytest.raises(CLIError) as exc:
        _raise_for_status(_resp(503, "no available server"))

    message = str(exc.value)
    assert "503" in message
    assert "no available server" in message, (
        "the proxy's explanation was dropped: " + message
    )


def test_a_json_detail_still_wins():
    """The application's own ``detail`` is better than a raw body, so it must
    keep precedence — this fallback only fills a gap, it does not replace."""
    with pytest.raises(CLIError) as exc:
        _raise_for_status(_resp(404, json_body={"detail": "Run not found."}))

    # Exact, not "contains": the raw JSON body ALSO contains this substring,
    # so a containment check passes even when the fallback wrongly overrides
    # the parsed detail and dumps `{"detail": "Run not found."}` verbatim.
    assert str(exc.value) == "Not found. Run not found."


def test_an_html_error_page_is_not_dumped_into_the_terminal():
    """A proxy's HTML page is noise, not information."""
    page = "<html><head><title>502</title></head><body>Bad Gateway</body></html>"
    assert _error_body_snippet(_resp(502, page)) == ""

    with pytest.raises(CLIError) as exc:
        _raise_for_status(_resp(502, page))
    assert "<html>" not in str(exc.value)


def test_a_long_body_is_capped_and_collapsed():
    """A long or multi-line body must not swamp the message."""
    # The messy whitespace has to sit INSIDE the cap. Put it past the cap and
    # truncation alone removes it, so the test passes with the collapse deleted.
    body = "upstream" + chr(10) * 2 + "connect   error" + chr(10) + ("padding " * 60)
    snippet = _error_body_snippet(_resp(503, body))

    assert len(snippet) <= _MAX_BODY_SNIPPET + 1, (
        "snippet was " + str(len(snippet)) + " chars"
    )
    assert chr(10) not in snippet, "newlines survived: " + repr(snippet[:60])
    assert "  " not in snippet, "not collapsed: " + repr(snippet[:60])
    assert snippet.startswith("upstream connect error padding")


def test_an_empty_body_adds_nothing():
    """No body means no snippet — the message stays as it was."""
    assert _error_body_snippet(_resp(500, "")) == ""
    assert _error_body_snippet(_resp(500, "   \n  ")) == ""

    with pytest.raises(CLIError) as exc:
        _raise_for_status(_resp(500, ""))
    assert str(exc.value).strip() == "Server error (500)."


def test_a_json_body_without_detail_falls_back_to_the_snippet():
    """Valid JSON that simply has no ``detail`` should not silently produce an
    empty message when the body itself says something."""
    with pytest.raises(CLIError) as exc:
        _raise_for_status(_resp(503, json_body={"error": "upstream unavailable"}))

    assert "upstream unavailable" in str(exc.value)


def test_a_non_object_json_body_does_not_crash():
    """``resp.json()`` can legitimately return a list or a string."""
    with pytest.raises(CLIError):
        _raise_for_status(_resp(500, json_body=["a", "b"]))
