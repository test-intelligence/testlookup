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
