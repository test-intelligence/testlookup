"""
Unit tests for ``services.gitlab_integration_service`` (PMF Epic 3
US-3.1/3.2) — mirrors ``test_github_checks_service.py`` /
``test_github_pr_comment.py``.

The GitLab HTTP layer is mocked at ``svc._request`` (everything outbound
funnels through it, including the single-retry wrapper and, via
``_request``, the commit-status ``async_retry`` path). DB reads are covered
by patching the service's own helpers plus a fake ``AsyncSessionLocal``. The
MR-note body/partition is REUSED from ``github_pr_comment_service`` and
tested there — here we assert the reuse + marker swap.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import gitlab_integration_service as svc


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
        ci_provider="gitlab_ci",
        branch="feature/checkout",
        build_number="b-77",
        commit_hash="deadbeef",
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
        base_url="https://gitlab.com",
        project_path="acme/webapp",
        has_pat=True,
        mr_comment_mode="failures_only",
        commit_status_enabled=True,
        last_error=None,
        last_error_at=None,
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
        api_root="https://gitlab.com/api/v4",
        project_ref="acme%2Fwebapp",
        mr_iid=42,
        pat="glpat_x",
        mode="failures_only",
        marker=svc._marker(PROJECT_ID),
        body=svc._marker(PROJECT_ID) + "\nbody",
        has_failures=True,
        has_fixed=False,
    )
    base.update(overrides)
    return svc._MRNoteContext(**base)


def _http_methods(request_mock):
    return [c.args[0] for c in request_mock.await_args_list]


# ── URL / project-ref helpers (self-managed base URL handling) ──────────────


def test_api_root_appends_v4_and_strips_slash():
    assert svc._api_root("https://gitlab.com/") == "https://gitlab.com/api/v4"
    assert svc._api_root("https://gitlab.mycorp.com") == "https://gitlab.mycorp.com/api/v4"


def test_project_ref_url_encodes_group_path():
    assert svc._project_ref("acme/web/app") == "acme%2Fweb%2Fapp"
    # numeric id passes through
    assert svc._project_ref("12345") == "12345"


def test_marker_is_gitlab_specific_and_per_project():
    m = svc._marker(PROJECT_ID)
    assert m.startswith("<!-- testlookup-mr-summary:")
    assert str(PROJECT_ID) in m


# ── _post_allowed ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_allowed_false_when_offline_mode_on():
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", True), patch(
        "app.services.feature_flags.is_enabled", AsyncMock(return_value=True),
    ):
        assert await svc._post_allowed() is False


@pytest.mark.asyncio
async def test_post_allowed_false_when_flag_off():
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", False), patch(
        "app.services.feature_flags.is_enabled", AsyncMock(return_value=False),
    ):
        assert await svc._post_allowed() is False


@pytest.mark.asyncio
async def test_post_allowed_true_when_online_and_flagged():
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", False), patch(
        "app.services.feature_flags.is_enabled", AsyncMock(return_value=True),
    ):
        assert await svc._post_allowed() is True


# ── SSRF guard is REUSED from github_checks_service ─────────────────────────


def test_ssrf_guard_is_the_shared_github_helper():
    from app.services import github_checks_service as gh
    assert svc._ssrf_block_reason is gh._ssrf_block_reason


# ── test_connection ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_offline_returns_not_ok():
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", True):
        result = await svc.test_connection(AsyncMock(), PROJECT_ID)
    assert result == {
        "ok": False,
        "detail": "AI_OFFLINE_MODE is enabled — outbound calls are disabled",
        "project_id_resolved": None,
    }


@pytest.mark.asyncio
async def test_connection_refuses_blocked_target_without_calling_httpx():
    from app.core.config import settings
    row = _fake_integration(base_url="http://169.254.169.254")
    request_mock = AsyncMock()
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch.object(svc, "get_integration", AsyncMock(return_value=row)), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value="glpat_x")), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value="blocked_target:169.254.169.254")), \
         patch.object(svc, "_request", request_mock):
        result = await svc.test_connection(AsyncMock(), PROJECT_ID)
    assert result["ok"] is False
    assert "not allowed" in result["detail"]
    request_mock.assert_not_awaited()  # the PAT was never sent


@pytest.mark.asyncio
async def test_connection_success_resolves_project_id():
    from app.core.config import settings
    row = _fake_integration()
    resp = FakeResponse(
        200, json_data={"id": 987, "path_with_namespace": "acme/webapp"},
    )
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch.object(svc, "get_integration", AsyncMock(return_value=row)), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value="glpat_x")), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_request", AsyncMock(return_value=resp)):
        result = await svc.test_connection(AsyncMock(), PROJECT_ID)
    assert result["ok"] is True
    assert result["project_id_resolved"] == "987"
    assert "acme/webapp" in result["detail"]


@pytest.mark.asyncio
async def test_connection_no_token_configured():
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch.object(svc, "get_integration", AsyncMock(return_value=_fake_integration())), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value=None)):
        result = await svc.test_connection(AsyncMock(), PROJECT_ID)
    assert result["ok"] is False
    assert "No token stored" in result["detail"]


# ── MR-note body reuse ───────────────────────────────────────────────────────


def test_mr_note_body_reuses_github_renderer_with_gitlab_marker():
    from app.services import github_pr_comment_service as gh
    part = gh._Partition(
        newly_failed=[{"name": "test_a", "message": "boom"}],
        has_baseline=False,
    )
    body = svc._build_mr_note_body(_fake_run(), PROJECT_ID, "acme", part, None)
    lines = body.splitlines()
    # First line is the GitLab marker (upsert key), NOT the GitHub one.
    assert lines[0] == f"<!-- testlookup-mr-summary:{PROJECT_ID} -->"
    assert "testlookup-pr-summary" not in body
    # The shared renderer's sections still come through.
    assert "### ❌ Failing (1)" in body
    assert "posted by TestLookup" in body


# ── MR-note: no participation → no HTTP ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"pr_number": None},
    {"ci_repo": None},
])
async def test_mr_note_without_context_returns_none_no_http(overrides):
    request_mock = AsyncMock()
    # The run loads but lacks MR context (no pr_number / no ci_repo) → bail
    # before any integration lookup or HTTP.
    factory = _session_cm_returning(_fake_run(**overrides), _fake_integration())
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", factory), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result is None
    request_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mr_note_offline_makes_no_http():
    from app.core.config import settings
    request_mock = AsyncMock()
    gather_mock = AsyncMock()
    with patch.object(settings, "AI_OFFLINE_MODE", True), \
         patch("app.services.feature_flags.is_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_mr_context", gather_mock), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)
    assert result is None
    request_mock.assert_not_awaited()
    gather_mock.assert_not_awaited()  # kill switch before any DB read


# ── MR-note upsert: PUT existing vs POST new ────────────────────────────────


@pytest.mark.asyncio
async def test_mr_note_updates_existing_marker_note():
    marker = svc._marker(PROJECT_ID)
    responses = [
        FakeResponse(200, json_data=[
            {"id": 1, "body": "unrelated human note"},
            {"id": 55, "body": marker + "\nold body"},
        ]),
        FakeResponse(200, json_data={"id": 55}),
    ]
    request_mock = AsyncMock(side_effect=responses)
    record_mock = AsyncMock()
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_mr_context", AsyncMock(return_value=_ctx())), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", record_mock), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result == {
        "posted": True, "updated": True, "status_code": 200, "note_id": 55,
    }
    methods = _http_methods(request_mock)
    assert methods == ["GET", "PUT"]  # never POST a second note
    put_call = request_mock.await_args_list[1]
    assert put_call.args[1].endswith(
        "/projects/acme%2Fwebapp/merge_requests/42/notes/55"
    )
    record_mock.assert_awaited_once_with(INTEGRATION_ID, error=None)


@pytest.mark.asyncio
async def test_mr_note_posts_new_note_when_marker_absent():
    responses = [
        FakeResponse(200, json_data=[{"id": 1, "body": "human note"}]),
        FakeResponse(201, json_data={"id": 99}),
    ]
    request_mock = AsyncMock(side_effect=responses)
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_mr_context", AsyncMock(return_value=_ctx())), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result == {
        "posted": True, "updated": False, "status_code": 201, "note_id": 99,
    }
    assert _http_methods(request_mock) == ["GET", "POST"]
    post_call = request_mock.await_args_list[1]
    assert post_call.args[1].endswith(
        "/projects/acme%2Fwebapp/merge_requests/42/notes"
    )


# ── failures_only mode + green run ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_failures_only_green_no_prior_note_skips():
    ctx = _ctx(has_failures=False, has_fixed=False)
    request_mock = AsyncMock(return_value=FakeResponse(200, json_data=[]))
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_mr_context", AsyncMock(return_value=ctx)), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result == {"skipped": "green_run_no_prior_comment"}
    assert _http_methods(request_mock) == ["GET"]  # searched, never wrote


@pytest.mark.asyncio
async def test_failures_only_green_updates_existing_note_to_green():
    marker = svc._marker(PROJECT_ID)
    green_body = marker + "\n## ✅ acme — 10/10 passed (100.0%)"
    ctx = _ctx(has_failures=False, has_fixed=False, body=green_body)
    responses = [
        FakeResponse(200, json_data=[{"id": 7, "body": marker + "\nold red body"}]),
        FakeResponse(200, json_data={"id": 7}),
    ]
    request_mock = AsyncMock(side_effect=responses)
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_mr_context", AsyncMock(return_value=ctx)), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result["posted"] is True and result["updated"] is True
    assert _http_methods(request_mock) == ["GET", "PUT"]
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
         patch.object(svc, "_gather_mr_context", AsyncMock(return_value=ctx)), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result["posted"] is True
    assert _http_methods(request_mock) == ["GET", "POST"]


# ── mode off / repo mismatch → nothing (through real _gather_mr_context) ────


def _session_cm_returning(run, integration):
    """A fake AsyncSessionLocal whose ``execute().scalar_one_or_none`` returns
    the run first, then the integration, then None for anything else."""
    seq = [run, integration]

    async def _execute(*a, **k):
        res = MagicMock()
        res.scalar_one_or_none.return_value = seq.pop(0) if seq else None
        return res

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)

    class _CM:
        async def __aenter__(self_):
            return session

        async def __aexit__(self_, *a):
            return False

    return MagicMock(return_value=_CM())


@pytest.mark.asyncio
async def test_mode_off_does_nothing():
    request_mock = AsyncMock()
    read_secret = AsyncMock()
    factory = _session_cm_returning(
        _fake_run(), _fake_integration(mr_comment_mode="off"),
    )
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", factory), \
         patch("app.services.secret_service.read_secret", read_secret), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result is None
    request_mock.assert_not_awaited()
    read_secret.assert_not_awaited()  # bailed before reading the token


@pytest.mark.asyncio
async def test_repo_mismatch_skips():
    request_mock = AsyncMock()
    factory = _session_cm_returning(
        _fake_run(ci_repo="other-group/other-project"), _fake_integration(),
    )
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", factory), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result is None
    request_mock.assert_not_awaited()


# ── SSRF guard blocks the note post ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_blocked_target_never_sends_token():
    request_mock = AsyncMock()
    record_mock = AsyncMock()
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_mr_context", AsyncMock(return_value=_ctx())), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value="blocked_target:127.0.0.1")), \
         patch.object(svc, "_record_outcome", record_mock), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result == {
        "skipped": "blocked_unsafe_target", "reason": "blocked_target:127.0.0.1",
    }
    request_mock.assert_not_awaited()
    record_mock.assert_awaited_once()


# ── Retry etiquette ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_retry_on_5xx():
    responses = [
        FakeResponse(200, json_data=[]),          # GET notes
        FakeResponse(502, text="bad gateway"),    # POST attempt 1 → 5xx
        FakeResponse(201, json_data={"id": 12}),  # POST retry succeeds
    ]
    request_mock = AsyncMock(side_effect=responses)
    with patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_gather_mr_context", AsyncMock(return_value=_ctx())), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_record_outcome", AsyncMock()), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_mr_note_for_run(RUN_ID)

    assert result["posted"] is True and result["status_code"] == 201
    assert _http_methods(request_mock) == ["GET", "POST", "POST"]


# ── Commit status ────────────────────────────────────────────────────────────


def test_commit_status_state_mapping():
    assert svc._commit_status_state(_fake_run(failed_tests=0, broken_tests=0)) == "success"
    assert svc._commit_status_state(_fake_run(failed_tests=2, broken_tests=0)) == "failed"
    assert svc._commit_status_state(_fake_run(failed_tests=0, broken_tests=1)) == "failed"


def test_commit_status_payload_shape_and_deeplink():
    from app.core.config import settings
    with patch.object(settings, "PUBLIC_BASE_URL", "https://app.testlookup.io"):
        payload = svc._commit_status_payload(_fake_run(), "acme")
    assert payload["state"] == "failed"
    assert payload["name"] == "TestLookup · acme"
    assert "7/10 passed (70.0%)" in payload["description"]
    assert payload["target_url"].startswith("https://app.testlookup.io/intelligence/")


def test_commit_status_payload_no_target_url_without_base():
    from app.core.config import settings
    with patch.object(settings, "PUBLIC_BASE_URL", ""):
        payload = svc._commit_status_payload(_fake_run(), None)
    assert "target_url" not in payload
    assert payload["name"] == "TestLookup · TestLookup"


@pytest.mark.asyncio
async def test_commit_status_success_posts_state():
    from app.core.config import settings
    integration = _fake_integration()
    factory = _session_cm_returning(_fake_run(), integration)
    resp = FakeResponse(201, json_data={"id": 5})
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch("app.services.feature_flags.is_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", factory), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value="glpat_x")), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_request", AsyncMock(return_value=resp)) as req_mock:
        # Project name lookup is the 3rd execute; the fake returns None → fine.
        result = await svc.post_commit_status_for_run(RUN_ID)

    assert result == {"posted": True, "status_code": 201, "state": "failed"}
    posted_url = req_mock.await_args.args[1]
    assert posted_url.endswith("/projects/acme%2Fwebapp/statuses/deadbeef")
    assert req_mock.await_args.kwargs["json_body"]["state"] == "failed"


@pytest.mark.asyncio
async def test_commit_status_skips_when_toggle_off():
    from app.core.config import settings
    factory = _session_cm_returning(
        _fake_run(), _fake_integration(commit_status_enabled=False),
    )
    request_mock = AsyncMock()
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch("app.services.feature_flags.is_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", factory), \
         patch.object(svc, "_request", request_mock):
        result = await svc.post_commit_status_for_run(RUN_ID)

    assert result == {"skipped": "commit_status_disabled"}
    request_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_status_skips_without_commit_hash():
    from app.core.config import settings
    factory = _session_cm_returning(_fake_run(commit_hash=None), _fake_integration())
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch("app.services.feature_flags.is_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", factory):
        result = await svc.post_commit_status_for_run(RUN_ID)
    assert result == {"skipped": "no_commit_sha"}


@pytest.mark.asyncio
async def test_commit_status_offline_skips():
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", True):
        result = await svc.post_commit_status_for_run(RUN_ID)
    assert result == {"skipped": "feature_flag_off_or_offline_mode"}


# ── Config contract serialization (router helper) ───────────────────────────


def test_config_contract_never_leaks_token_and_reflects_has_pat():
    from app.routers.gitlab_integration import _to_config
    row = _fake_integration(has_pat=True, last_error="boom")
    cfg = _to_config(row)
    dumped = cfg.model_dump()
    # PAT never surfaces; has_token reflects has_pat.
    assert dumped["has_token"] is True
    assert dumped.get("token") is None
    assert dumped["enabled"] is True
    assert dumped["base_url"] == "https://gitlab.com"
    assert dumped["project_path"] == "acme/webapp"
    assert dumped["mr_comment_mode"] == "failures_only"
    assert dumped["commit_status_enabled"] is True
    assert dumped["last_error"] == "boom"


def test_config_contract_defaults_when_unconfigured():
    from app.routers.gitlab_integration import _to_config
    cfg = _to_config(None).model_dump()
    assert cfg["enabled"] is False
    assert cfg["base_url"] == "https://gitlab.com"
    assert cfg["project_path"] == ""
    assert cfg["mr_comment_mode"] == "failures_only"
    assert cfg["commit_status_enabled"] is True
    assert cfg["has_token"] is False


# ── Migration 0111 contract ─────────────────────────────────────────────────


def test_migration_0111_revision_chain():
    import importlib.util
    from pathlib import Path

    path = (
        Path(svc.__file__).resolve().parents[2]
        / "migrations" / "versions" / "0111_gitlab_integrations.py"
    )
    spec = importlib.util.spec_from_file_location("mig0111", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0111"
    assert mod.down_revision == "0110"
    assert callable(mod.upgrade) and callable(mod.downgrade)
