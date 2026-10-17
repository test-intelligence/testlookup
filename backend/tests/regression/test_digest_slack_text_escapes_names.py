"""The Slack / Teams digest text escapes every name it carries, not only the scope lines.

W09 escaped a saved view's scope lines with ``slack_text``; the rest of the text (the new-failure
test names, the action items, the teams' flaky-debt drafts) went out raw, so a test named
``<!channel>`` pinged the channel and ``<https://evil.example|click>`` became a link. Every line is
now escaped once at the join; the scope lines are not escaped twice.
"""
from app.services.digest_content_service import render_digest_text
from app.services.digest_scope_service import DigestScope, scope_info


def _digest(**over):
    digest = {
        "total_runs": 3,
        "avg_pass_rate": 91.5,
        "pass_rate_trend": -1.0,
        "flaky_test_count": 2,
        "delta": {
            "new_failures": 2,
            "new_failures_top": ["test_<!channel>_ping", "test_a&b"],
            "newly_flaky": 1,
            "recovered": 0,
            "quarantine_debt": {"active": 1, "stale": 0, "ready_to_promote": 0},
        },
        "action_items": ["Look at <https://evil.example|click here>"],
        "flaky_debt_review": {"teams": [{"text": "Team <!here>: 3 flaky · see https://testlookup.example.com/quarantine"}]},
        "scope": None,
    }
    digest.update(over)
    return digest


def test_names_in_every_line_are_escaped():
    text = render_digest_text(_digest())
    lines = text.split("\n")
    assert not any(raw in text for raw in ("<!channel>", "<!here>", "<https"))
    assert lines[1].startswith("New failures: 2 (top: test_&lt;!channel&gt;_ping, test_a&amp;b) · ")
    # Whole lines, compared exactly: the action item's link is text, and the
    # draft's plain URL stays a plain URL (Slack links it; nothing is markup).
    assert "• Look at &lt;https://evil.example|click here&gt;" in lines
    assert "Team &lt;!here&gt;: 3 flaky · see https://testlookup.example.com/quarantine" in lines


def test_the_scope_lines_are_escaped_exactly_once():
    scope = scope_info(DigestScope(source="saved_view_deleted", view_name="Checkout <!channel> & co"))
    for digest in (_digest(scope=scope), _digest(scope=scope, is_zero_change=True, latest_run_total_tests=12)):
        text = render_digest_text(digest)
        assert "<!channel>" not in text
        assert "&amp;amp;" not in text and "&amp;lt;" not in text  # never escaped twice
        assert "View deleted: Checkout &lt;!channel&gt; &amp; co" in text
