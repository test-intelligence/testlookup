"""
Unit tests for ``services.github_checks_service`` — the pure helpers
(``_format_check_summary`` and ``_SHA_RE``) and a minimal test that
``_post_allowed`` respects ``AI_OFFLINE_MODE`` regardless of the
feature-flag state.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import github_checks_service as svc


# ── _SHA_RE ────────────────────────────────────────────────────────────────


def test_sha_regex_accepts_full_sha():
    sha = "abc123" + "0" * 34
    assert svc._SHA_RE.match(sha) is not None


def test_sha_regex_rejects_short_sha():
    assert svc._SHA_RE.match("abc123") is None


def test_sha_regex_rejects_uppercase_hex():
    # GitHub canonical SHAs are lowercase; reject uppercase to avoid
    # feeding a malformed payload into the Checks API.
    assert svc._SHA_RE.match("A" * 40) is None


# ── _format_check_summary ─────────────────────────────────────────────────


def _fake_run(
    *,
    total=10,
    passed=8,
    failed=2,
    broken=0,
    skipped=0,
    pass_rate=80.0,
    build="b-42",
    branch="feature/login",
    end_time=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        total_tests=total,
        passed_tests=passed,
        failed_tests=failed,
        broken_tests=broken,
        skipped_tests=skipped,
        pass_rate=pass_rate,
        build_number=build,
        branch=branch,
        end_time=end_time or datetime.now(timezone.utc),
    )


def test_format_check_summary_success_when_all_passed():
    run = _fake_run(total=10, passed=10, failed=0, broken=0, pass_rate=100.0)
    payload = svc._format_check_summary(run, project_name="acme")
    assert payload["conclusion"] == "success"
    assert payload["status"] == "completed"
    assert "10/10 passed" in payload["output"]["title"]
    assert "acme" in payload["name"]


def test_format_check_summary_failure_when_any_failed():
    run = _fake_run(failed=3)
    payload = svc._format_check_summary(run, project_name=None)
    assert payload["conclusion"] == "failure"
    # Without project_name we fall back to "TestLookup" in the label.
    assert "TestLookup" in payload["name"]


def test_format_check_summary_failure_when_broken_only():
    """Broken tests (infra errors) should still flag a check failure."""
    run = _fake_run(total=10, passed=9, failed=0, broken=1, pass_rate=90.0)
    payload = svc._format_check_summary(run, project_name="acme")
    assert payload["conclusion"] == "failure"


def test_format_check_summary_includes_deeplink_when_base_url_set():
    from app.core.config import settings
    with patch.object(settings, "PUBLIC_BASE_URL", "https://app.testlookup.io"):
        run = _fake_run()
        payload = svc._format_check_summary(run, project_name="acme")
    assert payload["details_url"] is not None
    assert payload["details_url"].startswith("https://app.testlookup.io/intelligence/")
    assert "app.testlookup.io" in payload["output"]["summary"]


def test_format_check_summary_handles_missing_base_url():
    from app.core.config import settings
    with patch.object(settings, "PUBLIC_BASE_URL", ""):
        run = _fake_run()
        payload = svc._format_check_summary(run, project_name="acme")
    # No base URL → details_url should be None (GitHub renders as plain text).
    assert payload["details_url"] is None


# ── _post_allowed ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_allowed_false_when_offline_mode_on():
    """Offline mode is the hard kill switch — flag value shouldn't matter."""
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", True), patch(
        "app.services.feature_flags.is_enabled",
        AsyncMock(return_value=True),
    ):
        assert await svc._post_allowed() is False


@pytest.mark.asyncio
async def test_post_allowed_false_when_feature_flag_off():
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", False), patch(
        "app.services.feature_flags.is_enabled",
        AsyncMock(return_value=False),
    ):
        assert await svc._post_allowed() is False


@pytest.mark.asyncio
async def test_post_allowed_true_when_online_and_flagged():
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", False), patch(
        "app.services.feature_flags.is_enabled",
        AsyncMock(return_value=True),
    ):
        assert await svc._post_allowed() is True


# ── _ssrf_block_reason (SSRF guard) ─────────────────────────────────────────
#
# Regression (review/github-checks-service, 2026-06-02): api_base_url is
# QA_LEAD-configurable and the PAT is sent to it (test_connection even returns
# the response body), so an unguarded base is an SSRF read primitive (cloud
# metadata 169.254.169.254 → IAM creds; localhost pivot; path-traversal via
# repo_owner/name). Block loopback/link-local/unspecified at egress time while
# ALLOWING RFC1918 so self-hosted GitHub Enterprise on a private net still works.


def _addrinfo(ip: str):
    return [(2, 1, 6, "", (ip, 0))]


@pytest.mark.asyncio
@pytest.mark.parametrize("url,ip", [
    ("http://169.254.169.254/x", "169.254.169.254"),  # cloud metadata
    ("http://127.0.0.1/x", "127.0.0.1"),               # loopback
    ("http://0.0.0.0/x", "0.0.0.0"),                   # unspecified
    ("http://[::1]/x", "::1"),                         # IPv6 loopback (bracketed)
])
async def test_ssrf_block_reason_blocks_dangerous_targets(url, ip):
    with patch.object(svc.socket, "getaddrinfo", lambda *a, **k: _addrinfo(ip)):
        reason = await svc._ssrf_block_reason(url)
    assert reason is not None and reason.startswith("blocked_target")


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", ["140.82.112.3", "10.0.0.5", "192.168.1.10"])
async def test_ssrf_block_reason_allows_public_and_private_ghe(ip):
    # Public GitHub AND RFC1918 (self-hosted GHE) must be allowed.
    with patch.object(svc.socket, "getaddrinfo", lambda *a, **k: _addrinfo(ip)):
        assert await svc._ssrf_block_reason(f"http://{ip}/x") is None


@pytest.mark.asyncio
async def test_ssrf_block_reason_allows_non_resolving_host():
    def _boom(*a, **k):
        raise OSError("name resolution failed")

    with patch.object(svc.socket, "getaddrinfo", _boom):
        assert await svc._ssrf_block_reason("https://ghe.invalid.example/x") is None


@pytest.mark.asyncio
async def test_ssrf_block_reason_rejects_empty_url():
    assert await svc._ssrf_block_reason("") == "invalid_url"


# ═══════════════════════════════════════════════════════════════════════════
# PMF US-4.2 — per-test annotations + flaky-aware conclusion
# ═══════════════════════════════════════════════════════════════════════════


def _tc(fp, status="FAILED", name=None, message=None, stack=None):
    return SimpleNamespace(
        test_fingerprint=fp,
        status=status,
        test_name=name or f"test_{fp}",
        error_message=message,
        stack_trace=stack,
    )


_PY_TRACE = (
    "Traceback (most recent call last):\n"
    '  File "tests/test_checkout.py", line 42, in test_pay\n'
    "    charge()\n"
    '  File "app/services/pay.py", line 88, in charge\n'
    "    raise ValueError\n"
    '  File "/usr/lib/python3.11/site-packages/requests/api.py", line 59, in get\n'
    "ValueError: boom\n"
)

_JS_TRACE = (
    "AssertionError: expected 200 to equal 500\n"
    "    at node_modules/chai/lib/assert.js:100:5\n"
    "    at Context.<anonymous> (cypress/e2e/checkout.cy.ts:23:9)\n"
)

_JAVA_TRACE = (
    "java.lang.AssertionError: expected:<200> but was:<500>\n"
    "    at org.junit.Assert.fail(Assert.java:89)\n"
    "    at com.acme.checkout.CheckoutTest.paysOk(CheckoutTest.java:42)\n"
)


# ── locate_in_trace — the best-effort locator ───────────────────────────────


def test_locator_python_picks_deepest_repo_frame():
    # "most recent call last": the LAST repo-relative frame is the failure
    # site; the trailing site-packages frame must be skipped.
    assert svc.locate_in_trace(_PY_TRACE) == ("app/services/pay.py", 88)


def test_locator_python_rejects_absolute_paths():
    trace = (
        '  File "/home/ci/build/tests/test_a.py", line 7, in test_a\n'
        '  File "C:\\ci\\build\\tests\\test_b.py", line 9, in test_b\n'
    )
    # Absolute POSIX and drive-letter paths are not repo-relative — honest
    # answer is "not locatable", never a guessed path.
    assert svc.locate_in_trace(trace) is None


def test_locator_python_normalizes_windows_relative_path():
    trace = '  File "tests\\unit\\test_win.py", line 12, in test_x\n'
    assert svc.locate_in_trace(trace) == ("tests/unit/test_win.py", 12)


def test_locator_js_picks_first_repo_frame_skipping_node_modules():
    # JS stacks are innermost-first: node_modules frame skipped, repo
    # frame (in parentheses, with column) wins.
    assert svc.locate_in_trace(_JS_TRACE) == ("cypress/e2e/checkout.cy.ts", 23)


def test_locator_js_handles_webpack_bundler_prefix():
    trace = "    at eval (webpack:///./cypress/e2e/spec.cy.js:12:8)\n"
    assert svc.locate_in_trace(trace) == ("cypress/e2e/spec.cy.js", 12)


def test_locator_java_trace_is_honestly_unlocatable():
    # Surefire frames carry a bare file name (CheckoutTest.java), not a
    # repo-relative path — the src/test/java prefix is unknowable, so we
    # refuse to guess.
    assert svc.locate_in_trace(_JAVA_TRACE) is None


@pytest.mark.parametrize("text", [None, "", "no trace here at all"])
def test_locator_no_location(text):
    assert svc.locate_in_trace(text) is None


def test_locator_rejects_traversal_and_urls():
    assert svc._normalize_repo_path("../../outside/repo.py") is None
    assert svc._normalize_repo_path("https://example.invalid/x.js") is None
    assert svc._normalize_repo_path("./src/app.ts") == "src/app.ts"


def test_locate_failure_falls_back_to_error_message():
    tc = _tc("fp", stack=None, message="boom\n" + _PY_TRACE)
    assert svc._locate_failure(tc) == ("app/services/pay.py", 88)


# ── _partition_for_check — PR-comment parity ────────────────────────────────


def test_partition_flaky_lands_only_in_flaky_bucket():
    right = {
        "fp_new": _tc("fp_new"),
        "fp_still": _tc("fp_still"),
        "fp_flaky": _tc("fp_flaky"),
        "fp_fixed": _tc("fp_fixed", status="PASSED"),
    }
    left = {
        "fp_new": _tc("fp_new", status="PASSED"),
        "fp_still": _tc("fp_still", status="FAILED"),
        "fp_flaky": _tc("fp_flaky", status="PASSED"),
        "fp_fixed": _tc("fp_fixed", status="FAILED"),
    }
    enrich = svc._partition_for_check(right, left, {"fp_flaky"}, has_baseline=True)

    assert [fp for fp, _ in enrich.newly_failed] == ["fp_new"]
    assert [fp for fp, _ in enrich.still_failing] == ["fp_still"]
    assert [fp for fp, _ in enrich.known_flaky] == ["fp_flaky"]
    assert enrich.fixed_count == 1
    assert enrich.all_failures_flaky is False


def test_partition_no_baseline_all_failures_read_as_newly_failed():
    right = {"fp_a": _tc("fp_a"), "fp_b": _tc("fp_b", status="BROKEN")}
    enrich = svc._partition_for_check(right, {}, set(), has_baseline=False)
    assert len(enrich.newly_failed) == 2
    assert enrich.still_failing == []
    assert enrich.fixed_count == 0


def test_all_failures_flaky_property():
    only_flaky = svc._CheckEnrichment(known_flaky=[("f", _tc("f"))])
    assert only_flaky.all_failures_flaky is True
    mixed = svc._CheckEnrichment(
        known_flaky=[("f", _tc("f"))], newly_failed=[("n", _tc("n"))],
    )
    assert mixed.all_failures_flaky is False
    green = svc._CheckEnrichment()
    assert green.all_failures_flaky is False


# ── _build_annotations — shape, priority, cap, split ────────────────────────


def test_annotation_payload_shape_and_levels():
    enrich = svc._CheckEnrichment(
        newly_failed=[("fp_n", _tc("fp_n", message="AssertionError: boom", stack=_PY_TRACE))],
        known_flaky=[("fp_f", _tc("fp_f", message="TimeoutError", stack=_JS_TRACE))],
        has_baseline=True,
    )
    annotations, non_locatable, omitted = svc._build_annotations(enrich)

    assert omitted == 0 and non_locatable == []
    assert annotations[0] == {
        "path": "app/services/pay.py",
        "start_line": 88,
        "end_line": 88,
        "annotation_level": "failure",
        "title": "test_fp_n",
        "message": "AssertionError: boom",
    }
    # Known-flaky annotates at warning level, after the real failures.
    assert annotations[1]["annotation_level"] == "warning"
    assert annotations[1]["path"] == "cypress/e2e/checkout.cy.ts"


def test_annotations_newly_failed_first_then_cluster_size():
    # Two still-failing clusters: "big" (3 members) and "small" (1) — plus
    # one newly-failed with a unique message. Newly-failed must come FIRST
    # despite its cluster of 1; remaining order is big cluster before small.
    def trace(line):
        return f'  File "tests/test_x.py", line {line}, in t\n'

    still = (
        [(f"fp_big{i}", _tc(f"fp_big{i}", message="ConnectionError: db down", stack=trace(i + 1))) for i in range(3)]
        + [("fp_small", _tc("fp_small", message="unique error", stack=trace(50)))]
    )
    enrich = svc._CheckEnrichment(
        newly_failed=[("fp_new", _tc("fp_new", message="fresh break", stack=trace(99)))],
        still_failing=still,
        has_baseline=True,
    )
    annotations, _, _ = svc._build_annotations(enrich)
    titles = [a["title"] for a in annotations]
    assert titles[0] == "test_fp_new"
    assert titles[1:4] == ["test_fp_big0", "test_fp_big1", "test_fp_big2"]
    assert titles[4] == "test_fp_small"


def test_annotations_capped_at_50_with_omitted_count():
    rows = [
        (f"fp{i:03d}", _tc(f"fp{i:03d}", message=f"err {i}", stack=f'  File "tests/test_a.py", line {i + 1}, in t\n'))
        for i in range(60)
    ]
    enrich = svc._CheckEnrichment(newly_failed=rows, has_baseline=True)
    annotations, non_locatable, omitted = svc._build_annotations(enrich)
    assert len(annotations) == svc._MAX_ANNOTATIONS == 50
    assert omitted == 10
    assert non_locatable == []


def test_non_locatable_failures_split_to_text_list_not_annotations():
    enrich = svc._CheckEnrichment(
        newly_failed=[
            ("fp_loc", _tc("fp_loc", message="boom", stack=_PY_TRACE)),
            ("fp_java", _tc("fp_java", message="expected:<200>", stack=_JAVA_TRACE)),
        ],
        known_flaky=[("fp_nofr", _tc("fp_nofr", message=None, stack=None))],
        has_baseline=True,
    )
    annotations, non_locatable, omitted = svc._build_annotations(enrich)
    assert [a["title"] for a in annotations] == ["test_fp_loc"]
    assert [(r["name"], r["flaky"]) for r in non_locatable] == [
        ("test_fp_java", False), ("test_fp_nofr", True),
    ]
    assert omitted == 0


def test_annotation_message_bounded_and_never_empty():
    long_msg = "\n".join(f"line {i}" for i in range(20))
    assert svc._annotation_message(_tc("f", message=long_msg)).count("\n") == 4
    assert svc._annotation_message(_tc("f", message="x" * 2000)).endswith("…")
    assert svc._annotation_message(_tc("f", message=None)) != ""


# ── Conclusion matrix + output enrichment ───────────────────────────────────


def _flaky_enrichment(n=2):
    return svc._CheckEnrichment(
        known_flaky=[(f"fp{i}", _tc(f"fp{i}", message="flaky")) for i in range(n)],
        has_baseline=True,
    )


def test_conclusion_neutral_when_all_failures_flaky():
    run = _fake_run(total=10, passed=8, failed=2, broken=0, pass_rate=80.0)
    payload = svc._format_check_summary(run, "acme", _flaky_enrichment(2))
    assert payload["conclusion"] == "neutral"
    assert "2 failures — all known-flaky/quarantined" in payload["output"]["title"]
    assert "likely not caused by this change" in payload["output"]["summary"]


def test_conclusion_failure_when_any_real_failure_among_flaky():
    run = _fake_run(failed=3)
    enrich = _flaky_enrichment(2)
    enrich.newly_failed.append(("fp_real", _tc("fp_real", message="real break")))
    payload = svc._format_check_summary(run, "acme", enrich)
    assert payload["conclusion"] == "failure"


def test_conclusion_failure_when_per_test_rows_unavailable():
    # Aggregates say failures but enrichment is None (rows missing) — we
    # can't prove all-flaky, so the conclusion stays failure (fail-honest).
    run = _fake_run(failed=2)
    payload = svc._format_check_summary(run, "acme", None)
    assert payload["conclusion"] == "failure"
    assert "annotations" not in payload["output"]


def test_conclusion_success_on_green_run_with_enrichment():
    run = _fake_run(total=10, passed=10, failed=0, broken=0, pass_rate=100.0)
    enrich = svc._CheckEnrichment(fixed_count=2, has_baseline=True)
    payload = svc._format_check_summary(run, "acme", enrich)
    assert payload["conclusion"] == "success"
    assert "**Fixed:** 2" in payload["output"]["summary"]


def test_summary_counts_and_text_sections():
    run = _fake_run(failed=2)
    enrich = svc._CheckEnrichment(
        newly_failed=[("fp_java", _tc("fp_java", message="expected:<200>", stack=_JAVA_TRACE))],
        known_flaky=[("fp_f", _tc("fp_f", message="flaky", stack=_PY_TRACE))],
        fixed_count=1,
        has_baseline=True,
    )
    payload = svc._format_check_summary(run, "acme", enrich)
    summary = payload["output"]["summary"]
    assert "**Newly failed:** 1" in summary
    assert "**Known flaky:** 1" in summary
    assert "**Fixed:** 1" in summary
    text = payload["output"]["text"]
    assert "Failures without a source location (1)" in text
    assert "`test_fp_java`" in text
    # The locatable flaky failure became a warning annotation.
    assert payload["output"]["annotations"][0]["annotation_level"] == "warning"


def test_no_baseline_summary_says_so():
    run = _fake_run(failed=1)
    enrich = svc._CheckEnrichment(
        newly_failed=[("fp_a", _tc("fp_a", message="x", stack=_PY_TRACE))],
        has_baseline=False,
    )
    payload = svc._format_check_summary(run, "acme", enrich)
    summary = payload["output"]["summary"]
    assert "**Failing:** 1" in summary
    assert "no baseline run" in summary
    assert "Newly failed:" not in summary


def test_overflow_note_in_text_when_over_50():
    rows = [
        (f"fp{i:03d}", _tc(f"fp{i:03d}", message=f"e{i}", stack=f'  File "tests/test_a.py", line {i + 1}, in t\n'))
        for i in range(55)
    ]
    run = _fake_run(failed=55)
    payload = svc._format_check_summary(
        run, "acme", svc._CheckEnrichment(newly_failed=rows, has_baseline=True),
    )
    assert len(payload["output"]["annotations"]) == 50
    assert "+5 more locatable failure annotations omitted" in payload["output"]["text"]


def test_enrichment_none_keeps_pre_us42_payload_shape():
    run = _fake_run(total=10, passed=10, failed=0, broken=0, pass_rate=100.0)
    payload = svc._format_check_summary(run, project_name="acme")
    assert payload["conclusion"] == "success"
    assert set(payload["output"].keys()) == {"title", "summary"}


# ── _gather_enrichment ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gather_enrichment_none_when_no_per_test_rows():
    db = AsyncMock()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    with patch.object(svc, "_load_test_rows", AsyncMock(return_value={})):
        assert await svc._gather_enrichment(db, run) is None


@pytest.mark.asyncio
async def test_gather_enrichment_baseline_without_rows_treated_as_no_baseline():
    db = AsyncMock()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    baseline = SimpleNamespace(id=uuid.uuid4())
    right = {"fp_a": _tc("fp_a")}

    async def _rows(_db, run_id, *a, **k):
        return right if run_id == run.id else {}

    with patch.object(svc, "_load_test_rows", side_effect=_rows), \
         patch(
             "app.services.github_pr_comment_service._select_baseline",
             AsyncMock(return_value=baseline),
         ), \
         patch(
             "app.services.github_pr_comment_service._flaky_fingerprints",
             AsyncMock(return_value=set()),
         ):
        enrich = await svc._gather_enrichment(db, run)

    assert enrich is not None
    assert enrich.has_baseline is False
    assert [fp for fp, _ in enrich.newly_failed] == ["fp_a"]


@pytest.mark.asyncio
async def test_test_connection_refuses_blocked_target_without_calling_httpx():
    """test_connection must not send the PAT to a blocked target."""
    from app.core.config import settings

    db = AsyncMock()
    row = SimpleNamespace(
        api_base_url="http://169.254.169.254", repo_owner="o", repo_name="n",
    )
    httpx_client = patch.object(svc.httpx, "AsyncClient")
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch.object(svc, "get_integration", AsyncMock(return_value=row)), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value="ghp_x")), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value="blocked_target:169.254.169.254")), \
         httpx_client as mock_client:
        result = await svc.test_connection(db, uuid.uuid4())

    assert result["success"] is False
    assert "not allowed" in result["message"]
    mock_client.assert_not_called()  # the PAT was never sent anywhere


# ── AI-4: kind label on annotations (display-floor gated) ────────────────────


def test_annotation_kind_label_appended_when_floor_met():
    tc = _tc("fp_n", message="connection refused", stack=_PY_TRACE)
    tc.id = "11111111-1111-1111-1111-111111111111"
    tc_low = _tc("fp_low", message="mystery", stack=_PY_TRACE)
    tc_low.id = "22222222-2222-2222-2222-222222222222"
    enrich = svc._CheckEnrichment(
        newly_failed=[("fp_n", tc), ("fp_low", tc_low)],
        has_baseline=True,
        # kind_labels_for_test_cases already applied the display floor —
        # the below-floor test case has no entry.
        kind_labels={tc.id: "Infrastructure (82% conf, AI-classified)"},
    )
    annotations, _, _ = svc._build_annotations(enrich)
    by_title = {a["title"]: a for a in annotations}
    assert "Kind: Infrastructure (82% conf, AI-classified)" in by_title["test_fp_n"]["message"]
    assert "Kind:" not in by_title["test_fp_low"]["message"]


def test_non_locatable_row_carries_kind_label():
    tc = _tc("fp_n", message="connection refused", stack=None)
    tc.id = "11111111-1111-1111-1111-111111111111"
    enrich = svc._CheckEnrichment(
        newly_failed=[("fp_n", tc)],
        has_baseline=True,
        kind_labels={tc.id: "Infrastructure (82% conf, AI-classified)"},
    )
    _, non_locatable, _ = svc._build_annotations(enrich)
    assert non_locatable[0]["kind"] == "Infrastructure (82% conf, AI-classified)"
