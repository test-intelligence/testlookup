"""``create_share_link`` sends nothing until the user confirmed.

A share link is a public, unauthenticated URL for a run report. The tool
posted on every call and its description carried no side-effect wording, so an
assistant could publish a report without the user being asked, for example
because text inside a test log told it to.

The rule: without ``confirm=True`` the tool sends NO request and returns text
that says nothing was created and what a link would expose; with
``confirm=True`` it posts exactly once. The description says the URL is public
and that the user must be asked first.
"""

import asyncio
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

RUN_ID = "33333333-3333-4333-8333-333333333333"


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _register


@pytest.fixture
def api(monkeypatch):
    import client
    from tools import reports

    state = {"calls": []}

    async def _post(path, json_body=None, params=None, extra_headers=None):
        state["calls"].append(("POST", path, json_body))
        return {"token": "tok-fake-0001", "expires_at": "2026-12-19T00:00:00+00:00"}

    async def _get(path, params=None):
        raise AssertionError(f"unexpected GET {path}")

    monkeypatch.setattr(client, "post", _post)
    monkeypatch.setattr(client, "get", _get)
    mcp = _FakeMCP()
    reports.register(mcp)
    state["tool"] = mcp.tools["create_share_link"]
    return state


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"confirm": False}, {"confirm": None}, {"confirm": "yes"}, {"confirm": 1}],
    ids=["default", "false", "none", "the-string-yes", "the-number-1"],
)
def test_without_confirm_true_nothing_is_sent_and_the_warning_is_returned(api, kwargs):
    result = asyncio.run(api["tool"](run_id=RUN_ID, expires_days=30, layout="engineering", **kwargs))

    assert api["calls"] == [], "an unconfirmed call must not create a public link"
    assert result.startswith("No share link was created.")
    assert "public URL" in result and "without signing in" in result
    assert RUN_ID in result and "30 day(s)" in result
    assert "confirm=True" in result
    assert "tok-" not in result


def test_with_confirm_true_it_posts_exactly_once(api):
    result = asyncio.run(
        api["tool"](run_id=RUN_ID, expires_days=30, layout="engineering", confirm=True)
    )

    assert api["calls"] == [
        (
            "POST",
            f"/api/v1/reports/runs/{RUN_ID}/share",
            {"expiry_days": 30, "layout": "engineering"},
        )
    ]
    assert "tok-fake-0001" in result


def test_the_description_says_the_link_is_public_and_the_user_is_asked_first(api):
    doc = " ".join((api["tool"].__doc__ or "").split())

    assert doc.startswith("SIDE EFFECT")
    assert "PUBLIC, unauthenticated URL" in doc
    assert "ask the user first" in doc.lower()
    assert "confirm=True" in doc
