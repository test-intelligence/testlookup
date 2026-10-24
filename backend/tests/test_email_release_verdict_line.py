"""W11 GATE-08b: a per-release digest email names its release's verdict.

A PER_RELEASE delivery is the run's AI-summary email, whose BODY the review
gate may rewrite; the release-level verdict therefore rides in the delivery's
``metadata["release_verdict"]`` and the email builders render ONE escaped line
from it (the sentence is ``digest_content_service.release_verdict_line``). With
no such key, both renderings are exactly what they were.
"""
from __future__ import annotations

import re
from html import escape

import pytest

from app.services.digest_content_service import release_verdict_line
from app.services.notification import email_service

RV = {
    "release_id": "6d5c0f0e-3b5a-4d43-9f43-6a3f3f1a2b01",
    "release_name": "2026.10",
    "source": "subscription",
    "verdict": "NO_GO",
    "decided_at": "2026-10-21T14:05:00+00:00",
    "blocker_count": 2,
}
LINE = "Release 2026.10 — No-Go (2 blockers) · decided 2026-10-21 14:05 UTC · this digest's release"
BODY = "Build 42 regressed in checkout."
META = {
    "project_name": "Checkout",
    "build_number": "42",
    "pass_rate": 80.0,
    "total_tests": 10,
    "failed_tests": 2,
    "dashboard_url": "https://testlookup.example/runs/1/intelligence",
    "executive_panel": None,
}

# The footer stamps the minute of rendering; two renders compared byte for byte
# must not straddle a minute boundary.
_STAMP = re.compile(r"TestLookup · \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC")


def _html(meta):
    return _STAMP.sub("TestLookup · <now>", email_service._build_html("AI Summary", BODY, "ai_analysis_complete", meta))


def test_the_sentence_is_the_digests():
    assert release_verdict_line(RV) == LINE


def test_html_carries_one_escaped_line_after_the_body():
    html = _html({**META, "release_verdict": RV})
    assert html.count("data-release-verdict") == 1
    # Escaped as HTML text (the apostrophe too: html.escape quotes).
    assert escape(LINE) in html
    assert LINE not in html
    assert html.index(BODY) < html.index(escape(LINE)) < html.index("View in Dashboard")


def test_plain_text_carries_the_line_after_the_body():
    text = email_service._build_plain("AI Summary", BODY, {**META, "release_verdict": RV}, "ai_analysis_complete")
    lines = text.split("\n")
    assert lines[lines.index(BODY) + 1] == LINE
    assert text.count(LINE) == 1


def test_a_hostile_release_name_is_escaped_in_html():
    hostile = {**RV, "release_name": "<img src=x onerror=alert(1)>"}
    html = _html({**META, "release_verdict": hostile})
    assert "<img src=x" not in html
    assert "Release &lt;img src=x onerror=alert(1)&gt; — No-Go" in html


@pytest.mark.parametrize("value", ["absent", None, "not-a-dict", ["NO_GO"]])
def test_without_a_verdict_both_renderings_are_unchanged(value):
    meta = dict(META) if value == "absent" else {**META, "release_verdict": value}
    assert _html(meta) == _html(dict(META))
    assert email_service._build_plain("AI Summary", BODY, meta, "ai_analysis_complete") == (
        email_service._build_plain("AI Summary", BODY, dict(META), "ai_analysis_complete")
    )
    assert "data-release-verdict" not in _html(meta)


def test_the_unchanged_rendering_is_todays_layout():
    """The no-key plain text is exactly the pre-W11 lines (no blank line added)."""
    text = email_service._build_plain("AI Summary", BODY, dict(META), "ai_analysis_complete")
    assert text.split("\n")[:6] == ["AI Summary", "=" * len("AI Summary"), "", BODY, "", "Project: Checkout"]
