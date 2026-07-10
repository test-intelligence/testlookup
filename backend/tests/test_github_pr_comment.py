"""
Regression tests for ``services.github_pr_comment_service`` (PMF US-4.1
— sticky PR summary comment).

The GitHub HTTP layer is mocked at ``svc._request`` (everything outbound
funnels through it, including the single-retry wrapper), mirroring how
``test_github_checks_service.py`` avoids real egress. DB reads are
covered by patching the service's own read helpers plus a fake
``AsyncSessionLocal`` — the partition/body logic is pure and tested
directly.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import github_pr_comment_service as svc


PROJECT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
INTEGRATION_ID = uuid.uuid4()


# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = b"x" if json_data is not None else b""

    def json(self):
        return self._json


class _FakeSessionCM:
    """Async-context-manager stand-in for ``AsyncSessionLocal()``."""

    def __init__(self):
        self.session = AsyncMock()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


def _fake_session_factory():
    return MagicMock(return_value=_FakeSessionCM())


def _fake_run(**overrides):
    base = dict(
        id=RUN_ID,
        project_id=PROJECT_ID,
        pr_number=42,
        ci_repo="acme/webapp",
        ci_provider="github_actions",
        branch="feature/checkout",
        build_number="b-77",
        status="FAILED",
        total_tests=10,
        passed_tests=7,
        failed_tests=2,
        broken_tests=1,
        skipped_tests=0,
        pass_rate=70.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_integration(**overrides):
    base = dict(
        id=INTEGRATION_ID,
        project_id=PROJECT_ID,
        enabled=True,
        repo_owner="acme",
        repo_name="webapp",
        api_base_url="https://api.github.com",
        has_pat=True,
        pr_comment_mode="failures_only",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _tc(fp, status, name=None, message=None):
    return SimpleNamespace(
        test_fingerprint=fp,
        status=status,
        test_name=name or f"test_{fp}",
        error_message=message,
    )


def _ctx(**overrides):
    base = dict(
        integration_id=INTEGRATION_ID,
        api_base="https://api.github.com",
        repo="acme/webapp",
        pr_number=42,
        pat="ghp_x",
        mode="failures_only",
        marker=svc._marker(PROJECT_ID),
        body=svc._marker(PROJECT_ID) + "\nbody",
        has_failures=True,
        has_fixed=False,
    )
    base.update(overrides)
    return svc._PRCommentContext(**base)


def _http_methods(request_mock):
    return [c.args[0] for c in request_mock.await_args_list]


# ── 1. Run without PR context → None, no HTTP ───────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"pr_number": None},
    {"ci_repo": None},
    {"pr_number": None, "ci_repo": None},
])
async def test_run_without_pr_context_returns_none_no_http(overrides):
    request_mock = AsyncMock()
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", _fake_session_factory()), \
         patch.object(svc, "_load_run", AsyncMock(return_value=_fake_run(**overrides))), \
         patch.object(svc, "get_integration", AsyncMock()) as integration_mock, \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result is None
    request_mock.assert_not_awaited()
    integration_mock.assert_not_awaited()  # bailed before touching config


# ── 2. AI_OFFLINE_MODE → no HTTP (real _post_allowed) ───────────────────────


@pytest.mark.asyncio
async def test_offline_mode_makes_no_http_calls():
    from app.core.config import settings

    request_mock = AsyncMock()
    gather_mock = AsyncMock()
    with patch.object(settings, "AI_OFFLINE_MODE", True), \
         patch("app.services.feature_flags.is_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_context", gather_mock), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result is None
    request_mock.assert_not_awaited()
    gather_mock.assert_not_awaited()  # kill switch fires before any DB read


# ── 3. Partition + comment body ─────────────────────────────────────────────


def test_flaky_failure_lands_only_in_flaky_section():
    right = {
        "fp_new": _tc("fp_new", "FAILED", message="AssertionError: boom"),
        "fp_flaky": _tc("fp_flaky", "FAILED", message="TimeoutError"),
        "fp_pass": _tc("fp_pass", "PASSED"),
    }
    left = {
        "fp_pass": _tc("fp_pass", "PASSED"),
        "fp_new": _tc("fp_new", "PASSED"),
        "fp_flaky": _tc("fp_flaky", "PASSED"),
    }
    part = svc._partition_tests(right, left, {"fp_flaky"}, has_baseline=True)

    newly_names = [r["name"] for r in part.newly_failed]
    flaky_names = [r["name"] for r in part.known_flaky]
    assert newly_names == ["test_fp_new"]
    assert flaky_names == ["test_fp_flaky"]
    assert part.still_failing == 0


def test_fixed_section_from_baseline_diff_and_still_failing_count():
    right = {
        "fp_fixed": _tc("fp_fixed", "PASSED"),
        "fp_still": _tc("fp_still", "FAILED", message="same old"),
        "fp_new": _tc("fp_new", "FAILED", message="fresh break"),
    }
    left = {
        "fp_fixed": _tc("fp_fixed", "FAILED"),
        "fp_still": _tc("fp_still", "FAILED"),
        "fp_new": _tc("fp_new", "PASSED"),
    }
    part = svc._partition_tests(right, left, set(), has_baseline=True)

    assert [r["name"] for r in part.fixed] == ["test_fp_fixed"]
    assert [r["name"] for r in part.newly_failed] == ["test_fp_new"]
    assert part.still_failing == 1


def test_no_baseline_treats_all_failures_as_failing_and_says_so():
    right = {
        "fp_a": _tc("fp_a", "FAILED", message="x"),
        "fp_b": _tc("fp_b", "BROKEN", message="y"),
    }
    part = svc._partition_tests(right, {}, set(), has_baseline=False)
    assert len(part.newly_failed) == 2
    assert part.fixed == []

    body = svc._build_comment_body(_fake_run(), PROJECT_ID, "acme", part, None)
    assert "No baseline run found" in body
    assert "### ❌ Failing (2)" in body
    assert "Newly failed" not in body


def test_body_marker_sections_and_overflow():
    newly = [
        {"name": f"test_n{i}", "message": f"err {i}"} for i in range(13)
    ]
    flaky = [{"name": f"test_f{i}", "message": ""} for i in range(7)]
    fixed = [{"name": f"test_x{i}", "message": ""} for i in range(6)]
    part = svc._Partition(
        newly_failed=newly, known_flaky=flaky, fixed=fixed,
        still_failing=3, has_baseline=True,
    )
    baseline = _fake_run(id=uuid.uuid4(), build_number="b-41")

    body = svc._build_comment_body(_fake_run(), PROJECT_ID, "acme", part, baseline)
    lines = body.splitlines()

    # Marker is the FIRST line — the upsert key.
    assert lines[0] == f"<!-- testlookup-pr-summary:{PROJECT_ID} -->"
    assert "### ❌ Newly failed (13)" in body
    assert "### ⚠️ Known flaky (7)" in body
    assert "### ✅ Fixed (6)" in body
    assert "Historically flaky — likely not caused by this PR." in body
    # Overflow lines at caps 10 / 5 / 5.
    assert "_+3 more failed tests_" in body
    assert "_+2 more flaky tests_" in body
    assert "_+1 more fixed tests_" in body
    # Capped rows: test_n9 shown, test_n10 not.
    assert "test_n9" in body
    assert "test_n10" not in body
    # Footer: still-failing count + baseline label + attribution.
    assert "3 still failing on baseline" in body
    assert "baseline: b-41" in body
    assert "posted by TestLookup" in body


def test_body_truncates_failure_message_first_line():
    long_msg = "A" * 300 + "\nsecond line with `backticks`"
    part = svc._Partition(
        newly_failed=[{"name": "test_long", "message": svc._first_message_line(long_msg)}],
        has_baseline=True,
    )
    row_msg = part.newly_failed[0]["message"]
    assert len(row_msg) <= svc._MESSAGE_CAP + 1  # cap + ellipsis
    assert row_msg.endswith("…")
    assert "second line" not in row_msg
    assert "`" not in svc._first_message_line("has `ticks` inside")


# ── 4. Upsert: PATCH existing marker comment / POST when absent ─────────────


@pytest.mark.asyncio
async def test_upsert_patches_existing_marker_comment():
    marker = svc._marker(PROJECT_ID)
    responses = [
        FakeResponse(200, json_data=[
            {"id": 1, "body": "unrelated human comment"},
            {"id": 55, "body": marker + "\nold body"},
        ]),
        FakeResponse(200, json_data={"id": 55}),
    ]
    request_mock = AsyncMock(side_effect=responses)
    record_mock = AsyncMock()
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_context", AsyncMock(return_value=_ctx())), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", record_mock), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result == {
        "posted": True, "updated": True, "status_code": 200, "comment_id": 55,
    }
    methods = _http_methods(request_mock)
    assert methods == ["GET", "PATCH"]  # no second marker comment ever POSTed
    patch_call = request_mock.await_args_list[1]
    assert patch_call.args[1].endswith("/repos/acme/webapp/issues/comments/55")
    assert patch_call.kwargs["json_body"]["body"].startswith(marker)
    record_mock.assert_awaited_once_with(INTEGRATION_ID, error=None)


@pytest.mark.asyncio
async def test_upsert_posts_new_comment_when_marker_absent():
    responses = [
        FakeResponse(200, json_data=[{"id": 1, "body": "human comment"}]),
        FakeResponse(201, json_data={"id": 99}),
    ]
    request_mock = AsyncMock(side_effect=responses)
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_context", AsyncMock(return_value=_ctx())), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result == {
        "posted": True, "updated": False, "status_code": 201, "comment_id": 99,
    }
    methods = _http_methods(request_mock)
    assert methods == ["GET", "POST"]
    post_call = request_mock.await_args_list[1]
    assert post_call.args[1].endswith("/repos/acme/webapp/issues/42/comments")


# ── 5. failures_only mode + green run ───────────────────────────────────────


@pytest.mark.asyncio
async def test_failures_only_green_run_without_prior_comment_skips():
    ctx = _ctx(has_failures=False, has_fixed=False)
    request_mock = AsyncMock(return_value=FakeResponse(200, json_data=[]))
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_context", AsyncMock(return_value=ctx)), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result == {"skipped": "green_run_no_prior_comment"}
    assert _http_methods(request_mock) == ["GET"]  # searched, never wrote


@pytest.mark.asyncio
async def test_failures_only_green_run_updates_existing_comment_to_green():
    """A PR that went red→green must show green — the stale red comment
    is PATCHed even though there is nothing new to report."""
    marker = svc._marker(PROJECT_ID)
    green_body = marker + "\n## ✅ acme — 10/10 passed (100.0%)"
    ctx = _ctx(has_failures=False, has_fixed=False, body=green_body)
    responses = [
        FakeResponse(200, json_data=[{"id": 7, "body": marker + "\nold red body"}]),
        FakeResponse(200, json_data={"id": 7}),
    ]
    request_mock = AsyncMock(side_effect=responses)
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_context", AsyncMock(return_value=ctx)), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result["posted"] is True and result["updated"] is True
    methods = _http_methods(request_mock)
    assert methods == ["GET", "PATCH"]
    assert request_mock.await_args_list[1].kwargs["json_body"]["body"] == green_body


@pytest.mark.asyncio
async def test_always_mode_posts_on_green_run():
    ctx = _ctx(mode="always", has_failures=False, has_fixed=False)
    responses = [
        FakeResponse(200, json_data=[]),
        FakeResponse(201, json_data={"id": 3}),
    ]
    request_mock = AsyncMock(side_effect=responses)
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_context", AsyncMock(return_value=ctx)), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result["posted"] is True
    assert _http_methods(request_mock) == ["GET", "POST"]


# ── 6. Mode off → nothing (through the real _gather_context) ────────────────


@pytest.mark.asyncio
async def test_mode_off_does_nothing():
    request_mock = AsyncMock()
    read_secret = AsyncMock()
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", _fake_session_factory()), \
         patch.object(svc, "_load_run", AsyncMock(return_value=_fake_run())), \
         patch.object(
             svc, "get_integration",
             AsyncMock(return_value=_fake_integration(pr_comment_mode="off")),
         ), \
         patch("app.services.secret_service.read_secret", read_secret), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result is None
    request_mock.assert_not_awaited()
    read_secret.assert_not_awaited()  # bailed before reading the PAT


# ── Repo mismatch guard ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_repo_not_matching_integration_repo_skips():
    """PR numbers are repo-scoped: never post run A's pr_number onto a
    differently-configured repo."""
    request_mock = AsyncMock()
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", _fake_session_factory()), \
         patch.object(
             svc, "_load_run",
             AsyncMock(return_value=_fake_run(ci_repo="other-org/other-repo")),
         ), \
         patch.object(
             svc, "get_integration",
             AsyncMock(return_value=_fake_integration()),
         ), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result is None
    request_mock.assert_not_awaited()


# ── SSRF guard parity with the checks service ────────────────────────────────


@pytest.mark.asyncio
async def test_blocked_target_never_sends_pat():
    request_mock = AsyncMock()
    record_mock = AsyncMock()
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_context", AsyncMock(return_value=_ctx())), \
         patch.object(
             svc, "_ssrf_block_reason",
             AsyncMock(return_value="blocked_target:127.0.0.1"),
         ), \
         patch.object(svc, "_record_outcome", record_mock), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result == {
        "skipped": "blocked_unsafe_target", "reason": "blocked_target:127.0.0.1",
    }
    request_mock.assert_not_awaited()
    record_mock.assert_awaited_once()
    assert "blocked unsafe target" in record_mock.await_args.kwargs["error"]


# ── Retry etiquette ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_retry_on_5xx():
    responses = [
        FakeResponse(200, json_data=[]),          # GET comments
        FakeResponse(502, text="bad gateway"),    # POST attempt 1 → 5xx
        FakeResponse(201, json_data={"id": 12}),  # POST retry succeeds
    ]
    request_mock = AsyncMock(side_effect=responses)
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_context", AsyncMock(return_value=_ctx())), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_pr_summary_for_run(RUN_ID)

    assert result["posted"] is True and result["status_code"] == 201
    assert _http_methods(request_mock) == ["GET", "POST", "POST"]
