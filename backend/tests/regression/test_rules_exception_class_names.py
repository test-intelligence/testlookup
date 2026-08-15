"""Infrastructure failures must classify from their exception CLASS NAME too.

Found by ingesting five failures with distinguishable causes and reading what
the analysis pipeline made of them. Four were right. One was not:

```
test_infra_dns  |  java.net.UnknownHostException: payments.internal
  -> failure_category = UNKNOWN, confidence 30
  -> "Could not determine failure cause from available data"
```

For a failure whose cause is fully determined by its first line — and while the
sibling ``java.net.ConnectException: Connection refused`` classified correctly,
because that message happens to *also* contain the prose "connection refused".

The rules matched **human prose** ("unknown host", "connection refused") but not
the **exception class names** that carry the same meaning. Class names are how
these failures actually appear in a Java, Python or .NET stack trace — which is
this product's entire corpus: JUnit, TestNG, pytest, NUnit, TRX.

Probing eight common signatures found **six** falling through to UNKNOWN. That
is not a tuning question. An unclassified infrastructure failure loses its
cause, its confidence drops to the "could not determine" floor, and it stops
feeding the co-failure clustering that would otherwise group an outage into one
finding.

That the codebase already knew better elsewhere — ``systemic_cluster_service``
lists ``"unknownhost"`` in its cause-family matcher — is what makes this an
oversight rather than a deliberate scope choice.

The guard is the **class**: a signature that a competent engineer would classify
from its first line must not land in UNKNOWN, whichever language threw it.
"""
from __future__ import annotations

import pytest

from app.services.rules_engine import RulesEngine

# (signature, expected_category, what a human would call it)
#
# Deliberately spans the three runtimes this product ingests from, because the
# bug was exactly a runtime-specific phrasing gap: the JVM says
# UnknownHostException, glibc says "Temporary failure in name resolution",
# .NET says "No such host is known", and Python raises socket.gaierror.
INFRA_SIGNATURES = [
    # ── DNS / name resolution ────────────────────────────────────────────
    ("java.net.UnknownHostException: payments.internal", "INFRASTRUCTURE", "JVM DNS"),
    ("socket.gaierror: [Errno -2] Name or service not known", "INFRASTRUCTURE", "Python DNS"),
    ("System.Net.Sockets.SocketException: No such host is known", "INFRASTRUCTURE", ".NET DNS"),
    ("curl: (6) Could not resolve host: api.internal — Temporary failure in name resolution",
     "INFRASTRUCTURE", "glibc DNS"),
    # ── Connection ───────────────────────────────────────────────────────
    ("java.net.ConnectException: connect failed", "INFRASTRUCTURE", "JVM connect"),
    ("requests.exceptions.ConnectionError: Max retries exceeded with url: /health",
     "INFRASTRUCTURE", "Python connect"),
    ("ConnectionResetError: [Errno 104] Connection reset by peer", "INFRASTRUCTURE", "Python reset"),
    # ── Routing ──────────────────────────────────────────────────────────
    # Tail deliberately carries NO prose keyword — an earlier version of this
    # signature ended in "no route to host", so the prose rule matched and the
    # class-name path it claims to test was never exercised. It passed with the
    # class name deleted.
    ("java.net.NoRouteToHostException: connect to 10.0.0.5 failed",
     "INFRASTRUCTURE", "JVM routing"),
    # ── Timeout (already worked — pinned so a keyword edit cannot lose it) ─
    ("java.net.SocketTimeoutException: Read timed out", "INFRASTRUCTURE", "JVM timeout"),
    ("org.openqa.selenium.TimeoutException: waited 30 seconds for element",
     "INFRASTRUCTURE", "Selenium timeout"),
]


@pytest.mark.parametrize("signature,expected,label", INFRA_SIGNATURES)
def test_infrastructure_signatures_are_classified_not_shrugged_at(
    signature, expected, label
):
    """The regression. Each of these names its own cause in its first line."""
    result = RulesEngine.classify_test(signature, test_name="t")
    assert result["failure_category"] == expected, (
        f"{label}: {signature!r} classified as "
        f"{result['failure_category']} (confidence {result.get('confidence_score')})"
    )


@pytest.mark.parametrize("signature,expected,label", INFRA_SIGNATURES)
def test_a_classified_failure_beats_the_could_not_determine_floor(
    signature, expected, label
):
    """UNKNOWN comes with a low confidence and a "could not determine" summary.
    A signature that IS determinable must not be reported at that floor."""
    result = RulesEngine.classify_test(signature, test_name="t")
    assert (result.get("confidence_score") or 0) > 30, label
    summary = (result.get("root_cause_summary") or "").lower()
    assert "could not determine" not in summary, label


# ── The other half: no false positives ──────────────────────────────────────
#
# Widening keywords is exactly how a classifier starts calling everything
# infrastructure. These are the signatures that must NOT move.

NON_INFRA = [
    ("AssertionError: expected 200 but was 500", "PRODUCT_BUG", "assertion"),
    ("AssertionError: expected total 42 but was 41", "PRODUCT_BUG", "assertion"),
    ("java.lang.NullPointerException: Cannot invoke \"Order.getId()\"",
     None, "NPE — must not be INFRASTRUCTURE"),
    ("NoSuchElementException: Unable to locate element: #checkout-button",
     None, "locator — must not be INFRASTRUCTURE"),
]


@pytest.mark.parametrize("signature,expected,label", NON_INFRA)
def test_widening_the_keywords_did_not_make_everything_infrastructure(
    signature, expected, label
):
    result = RulesEngine.classify_test(signature, test_name="t")
    category = result["failure_category"]
    if expected is not None:
        assert category == expected, f"{label}: got {category}"
    else:
        assert category != "INFRASTRUCTURE", f"{label}: got {category}"


def test_the_word_connection_alone_is_not_an_infrastructure_verdict():
    """A test *named* for connections, failing an assertion, is a product bug.
    Matching on the bare word would have been the lazy fix."""
    result = RulesEngine.classify_test(
        "AssertionError: expected connection pool size 5 but was 3",
        test_name="test_connection_pool_sizing",
    )
    assert result["failure_category"] == "PRODUCT_BUG"


def test_an_empty_error_message_is_not_classified_as_anything_confident():
    """No evidence is not evidence of infrastructure."""
    result = RulesEngine.classify_test(None, test_name="t")
    assert result["failure_category"] != "INFRASTRUCTURE"


def test_the_dns_rule_covers_both_prose_and_class_name():
    """Named explicitly so a future keyword trim cannot quietly drop one half
    and leave the other passing."""
    prose = RulesEngine.classify_test("DNS resolution failed for host", test_name="t")
    class_name = RulesEngine.classify_test(
        "java.net.UnknownHostException: payments.internal", test_name="t"
    )
    assert prose["failure_category"] == "INFRASTRUCTURE"
    assert class_name["failure_category"] == "INFRASTRUCTURE"
