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
