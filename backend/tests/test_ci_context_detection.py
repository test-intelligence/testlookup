"""Tests for CI-context auto-detection in the Python SDK and CLI (US-4.3b).

The detection matrix lives in two deliberate copies — ``client/ci_context.py``
(shipped with the testlookup-reporter SDK) and
``cli/testlookup_cli/ci_context.py`` (the CLI is a separate installable
package) — plus JS/Java/Go ports. This suite:

  1. Runs the full provider matrix against BOTH Python copies (parametrized)
     so they cannot drift silently.
  2. Pins the merge precedence chain of ``resolve_ci_context``:
     detection < config-file overrides < TESTLOOKUP_CI_* env < explicit.
  3. Verifies the SDK folds CI context into the session-create payload and
     the LiveStream meta, and that the CLI stamps the multipart form fields.

Both modules are loaded via importlib under distinct names because they share
the module name ``ci_context``.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLIENT_DIR = _REPO_ROOT / "client"
_CLI_DIR = _REPO_ROOT / "cli"
for p in (_CLIENT_DIR, _CLI_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load_copy(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SDK_CI = _load_copy(_CLIENT_DIR / "ci_context.py", "_sdk_ci_context")
_CLI_CI = _load_copy(_CLI_DIR / "testlookup_cli" / "ci_context.py", "_cli_ci_context")


@pytest.fixture(params=[_SDK_CI, _CLI_CI], ids=["client-sdk", "cli"])
def ci(request):
    return request.param


# All env vars the matrix reads — passing explicit dicts to detect() keeps
# ambient CI env (this suite itself may run inside GitHub Actions) out of
# the assertions entirely.

# ── Provider matrix ─────────────────────────────────────────────────────────


def test_github_actions_pull_request(ci):
    result = ci.detect_ci_context({
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "acme/webapp",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/421/merge",
        "GITHUB_ACTOR": "octocat",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "99",
    })
    assert result == {
        "ci_provider": "github_actions",
        "ci_repo": "acme/webapp",
        "pr_number": 421,
        "ci_actor": "octocat",
        "ci_run_url": "https://github.com/acme/webapp/actions/runs/99",
    }


def test_github_actions_pull_request_target(ci):
    result = ci.detect_ci_context({
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_REF": "refs/pull/7/merge",
    })
    assert result["pr_number"] == 7


def test_github_actions_push_has_no_pr_number(ci):
    result = ci.detect_ci_context({
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "acme/webapp",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTOR": "octocat",
    })
    assert result["ci_provider"] == "github_actions"
    assert "pr_number" not in result


def test_github_actions_run_url_requires_all_three_parts(ci):
    result = ci.detect_ci_context({
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "acme/webapp",
        "GITHUB_RUN_ID": "99",
        # GITHUB_SERVER_URL missing → no run URL
    })
    assert "ci_run_url" not in result


def test_gitlab_merge_request(ci):
    result = ci.detect_ci_context({
        "GITLAB_CI": "true",
        "CI_PROJECT_PATH": "group/project",
        "CI_MERGE_REQUEST_IID": "17",
        "GITLAB_USER_LOGIN": "jdoe",
        "CI_JOB_URL": "https://gitlab.com/group/project/-/jobs/123",
    })
    assert result == {
        "ci_provider": "gitlab_ci",
        "ci_repo": "group/project",
        "pr_number": 17,
        "ci_actor": "jdoe",
        "ci_run_url": "https://gitlab.com/group/project/-/jobs/123",
    }


def test_gitlab_fallbacks(ci):
    result = ci.detect_ci_context({
        "GITLAB_CI": "true",
        "GITLAB_USER_NAME": "Jane Doe",
        "CI_PIPELINE_URL": "https://gitlab.com/g/p/-/pipelines/9",
    })
    assert result["ci_actor"] == "Jane Doe"
    assert result["ci_run_url"] == "https://gitlab.com/g/p/-/pipelines/9"


def test_jenkins_multibranch_pr_https_git_url(ci):
    result = ci.detect_ci_context({
        "JENKINS_URL": "https://ci.example.com/",
        "GIT_URL": "https://github.com/acme/webapp.git",
        "CHANGE_ID": "55",
        "CHANGE_AUTHOR": "jdoe",
        "BUILD_URL": "https://ci.example.com/job/webapp/55/",
    })
    assert result == {
        "ci_provider": "jenkins",
        "ci_repo": "acme/webapp",
        "pr_number": 55,
        "ci_actor": "jdoe",
        "ci_run_url": "https://ci.example.com/job/webapp/55/",
    }


def test_jenkins_ssh_git_url_and_build_user_fallback(ci):
    result = ci.detect_ci_context({
        "JENKINS_URL": "https://ci.example.com/",
        "GIT_URL": "git@github.com:acme/webapp.git",
        "BUILD_USER_ID": "release-bot",
    })
    assert result["ci_repo"] == "acme/webapp"
    assert result["ci_actor"] == "release-bot"


def test_jenkins_unparseable_git_url_leaves_repo_unset(ci):
    result = ci.detect_ci_context({
        "JENKINS_URL": "https://ci.example.com/",
        "GIT_URL": "webapp",
    })
    assert result["ci_provider"] == "jenkins"
    assert "ci_repo" not in result


def test_azure_devops_pull_request(ci):
    result = ci.detect_ci_context({
        "TF_BUILD": "True",
        "BUILD_REPOSITORY_NAME": "acme/webapp",
        "SYSTEM_PULLREQUEST_PULLREQUESTNUMBER": "88",
        "BUILD_REQUESTEDFOR": "Jane Doe",
        "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI": "https://dev.azure.com/acme/",
        "SYSTEM_TEAMPROJECT": "WebApp",
        "BUILD_BUILDID": "1234",
    })
    assert result == {
        "ci_provider": "azure_devops",
        "ci_repo": "acme/webapp",
        "pr_number": 88,
        "ci_actor": "Jane Doe",
        "ci_run_url": "https://dev.azure.com/acme/WebApp/_build/results?buildId=1234",
    }


def test_azure_devops_pr_id_fallback(ci):
    result = ci.detect_ci_context({
        "TF_BUILD": "True",
        "SYSTEM_PULLREQUEST_PULLREQUESTID": "89",
    })
    assert result["pr_number"] == 89


def test_circleci_pull_request(ci):
    result = ci.detect_ci_context({
        "CIRCLECI": "true",
        "CIRCLE_PROJECT_USERNAME": "acme",
        "CIRCLE_PROJECT_REPONAME": "webapp",
        "CIRCLE_PULL_REQUEST": "https://github.com/acme/webapp/pull/33",
        "CIRCLE_USERNAME": "jdoe",
        "CIRCLE_BUILD_URL": "https://circleci.com/gh/acme/webapp/77",
    })
    assert result == {
        "ci_provider": "circleci",
        "ci_repo": "acme/webapp",
        "pr_number": 33,
        "ci_actor": "jdoe",
        "ci_run_url": "https://circleci.com/gh/acme/webapp/77",
    }


# ── Cross-provider rules ────────────────────────────────────────────────────


def test_no_ci_detected_returns_empty_dict(ci):
    assert ci.detect_ci_context({}) == {}
    assert ci.detect_ci_context({"PATH": "/usr/bin", "HOME": "/home/x"}) == {}


def test_provider_precedence_github_wins(ci):
    result = ci.detect_ci_context({
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "acme/webapp",
        "GITLAB_CI": "true",
        "CI_PROJECT_PATH": "group/project",
        "JENKINS_URL": "https://ci.example.com/",
        "CIRCLECI": "true",
    })
    assert result["ci_provider"] == "github_actions"
    assert result["ci_repo"] == "acme/webapp"


@pytest.mark.parametrize("bad", ["not-a-number", "", "  ", "0", "-3", "1.5"])
def test_malformed_pr_numbers_are_dropped_never_raise(ci, bad):
    result = ci.detect_ci_context({
        "GITLAB_CI": "true",
        "CI_MERGE_REQUEST_IID": bad,
    })
    assert "pr_number" not in result


def test_github_ref_without_pr_shape_yields_no_pr_number(ci):
    result = ci.detect_ci_context({
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/heads/feature/pull-refresh",
    })
    assert "pr_number" not in result


def test_string_fields_truncated_to_backend_caps(ci):
    result = ci.detect_ci_context({
        "GITLAB_CI": "true",
        "CI_PROJECT_PATH": "r" * 400,
        "GITLAB_USER_LOGIN": "a" * 200,
        "CI_JOB_URL": "https://x/" + "u" * 2000,
    })
    assert len(result["ci_repo"]) == 300
    assert len(result["ci_actor"]) == 120
    assert len(result["ci_run_url"]) == 1000


def test_gate_values_other_than_true_do_not_match(ci):
    assert ci.detect_ci_context({"GITHUB_ACTIONS": "false"}) == {}
    assert ci.detect_ci_context({"GITLAB_CI": "0"}) == {}
    assert ci.detect_ci_context({"CIRCLECI": "yes"}) == {}


# ── resolve_ci_context precedence chain ─────────────────────────────────────


def test_resolve_env_overrides_beat_detection(ci):
    result = ci.resolve_ci_context(env={
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "acme/webapp",
        "GITHUB_ACTOR": "octocat",
        "TESTLOOKUP_CI_REPO": "acme/override-repo",
        "TESTLOOKUP_PR_NUMBER": "7",
    })
    assert result["ci_provider"] == "github_actions"
    assert result["ci_repo"] == "acme/override-repo"
    assert result["pr_number"] == 7
    assert result["ci_actor"] == "octocat"


def test_resolve_explicit_beats_env_and_detection(ci):
    result = ci.resolve_ci_context(
        {"ci_actor": "explicit-actor", "pr_number": 99},
        env={
            "GITLAB_CI": "true",
            "GITLAB_USER_LOGIN": "detected-actor",
            "CI_MERGE_REQUEST_IID": "17",
            "TESTLOOKUP_CI_ACTOR": "env-actor",
        },
    )
    assert result["ci_actor"] == "explicit-actor"
    assert result["pr_number"] == 99


def test_resolve_config_overrides_sit_between_detection_and_env(ci):
    result = ci.resolve_ci_context(
        overrides={"ci_repo": "file/repo", "ci_actor": "file-actor"},
        env={
            "GITLAB_CI": "true",
            "CI_PROJECT_PATH": "detected/repo",
            "TESTLOOKUP_CI_ACTOR": "env-actor",
        },
    )
    assert result["ci_repo"] == "file/repo"      # file beats detection
    assert result["ci_actor"] == "env-actor"     # env beats file


def test_resolve_malformed_explicit_pr_number_keeps_detection(ci):
    result = ci.resolve_ci_context(
        {"pr_number": "abc"},
        env={"GITLAB_CI": "true", "CI_MERGE_REQUEST_IID": "17"},
    )
    assert result["pr_number"] == 17


def test_resolve_outside_ci_with_no_overrides_is_empty(ci):
    assert ci.resolve_ci_context(env={}) == {}


def test_resolve_ignores_unknown_fields(ci):
    result = ci.resolve_ci_context({"bogus_field": "x"}, env={})
    assert result == {}


# ── SDK wiring: session-create payload ──────────────────────────────────────


def _make_reporter(monkeypatch):
    import testlookup_reporter as tr
    # Neutralise any config file discovery / ambient env.
    monkeypatch.setattr(tr.ConfigLoader, "load", classmethod(lambda cls, overrides=None: {}))
    return tr


def test_sdk_session_payload_carries_detected_ci_context(monkeypatch):
    tr = _make_reporter(monkeypatch)
    for var in ("GITLAB_CI", "JENKINS_URL", "TF_BUILD", "CIRCLECI"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/webapp")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/421/merge")
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")
    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    for var in ("TESTLOOKUP_CI_PROVIDER", "TESTLOOKUP_CI_REPO", "TESTLOOKUP_PR_NUMBER",
                "TESTLOOKUP_CI_ACTOR", "TESTLOOKUP_CI_RUN_URL"):
        monkeypatch.delenv(var, raising=False)

    reporter = tr.TestLookupReporter(
        base_url="http://localhost:8000", token="t", project_id="p",
    )
    assert reporter._ci_context["ci_provider"] == "github_actions"
    assert reporter._ci_context["pr_number"] == 421

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "session_id": "s" * 32, "session_token": "tok", "run_id": "r" * 32,
    }
    reporter._http = MagicMock()
    reporter._http.post = AsyncMock(return_value=response)

    async def run():
        # Explicit pr_number must beat the detected 421.
        live = await reporter._create_session(
            build_number="b-1", branch=None, commit_hash=None,
            total_tests=None, machine_id=None, launch_name=None,
            suite_name=None, release_name=None,
            ci_provider=None, ci_repo=None, pr_number=999,
            ci_actor=None, ci_run_url=None,
        )
        await live._shutdown()

    asyncio.run(run())
    payload = reporter._http.post.call_args.kwargs["json"]
    assert payload["ci_provider"] == "github_actions"
    assert payload["ci_repo"] == "acme/webapp"
    assert payload["pr_number"] == 999  # explicit beats detection
    assert payload["ci_actor"] == "octocat"


def test_sdk_session_payload_omits_ci_fields_outside_ci(monkeypatch):
    tr = _make_reporter(monkeypatch)
    for var in ("GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "TF_BUILD", "CIRCLECI",
                "TESTLOOKUP_CI_PROVIDER", "TESTLOOKUP_CI_REPO", "TESTLOOKUP_PR_NUMBER",
                "TESTLOOKUP_CI_ACTOR", "TESTLOOKUP_CI_RUN_URL"):
        monkeypatch.delenv(var, raising=False)

    reporter = tr.TestLookupReporter(
        base_url="http://localhost:8000", token="t", project_id="p",
    )
    assert reporter._ci_context == {}

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "session_id": "s" * 32, "session_token": "tok", "run_id": "r" * 32,
    }
    reporter._http = MagicMock()
    reporter._http.post = AsyncMock(return_value=response)

    async def run():
        live = await reporter._create_session(
            build_number="b-1", branch=None, commit_hash=None,
            total_tests=None, machine_id=None, launch_name=None,
            suite_name=None, release_name=None,
            ci_provider=None, ci_repo=None, pr_number=None,
            ci_actor=None, ci_run_url=None,
        )
        await live._shutdown()

    asyncio.run(run())
    payload = reporter._http.post.call_args.kwargs["json"]
    for field in ("ci_provider", "ci_repo", "pr_number", "ci_actor", "ci_run_url"):
        assert field not in payload


# ── SDK wiring: LiveStream meta fold ────────────────────────────────────────


def test_livestream_folds_ci_context_into_meta_metadata(monkeypatch):
    tr = _make_reporter(monkeypatch)
    for var in ("GITHUB_ACTIONS", "JENKINS_URL", "TF_BUILD", "CIRCLECI",
                "TESTLOOKUP_CI_PROVIDER", "TESTLOOKUP_CI_REPO", "TESTLOOKUP_PR_NUMBER",
                "TESTLOOKUP_CI_ACTOR", "TESTLOOKUP_CI_RUN_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GITLAB_CI", "true")
    monkeypatch.setenv("CI_PROJECT_PATH", "group/project")
    monkeypatch.setenv("CI_MERGE_REQUEST_IID", "17")

    stream = tr.LiveStream(
        api_key="tlk_x", run_id="run-1", base_url="http://localhost:8000",
        metadata={"ci_context": {"ci_actor": "user-wins"}},
        ci_repo="explicit/repo",
    )
    ci_meta = stream._meta["metadata"]["ci_context"]
    assert ci_meta["ci_provider"] == "gitlab_ci"
    assert ci_meta["ci_repo"] == "explicit/repo"     # ctor kwarg beats detection
    assert ci_meta["pr_number"] == 17
    assert ci_meta["ci_actor"] == "user-wins"        # metadata["ci_context"] beats all


def test_livestream_outside_ci_leaves_meta_untouched(monkeypatch):
    tr = _make_reporter(monkeypatch)
    for var in ("GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "TF_BUILD", "CIRCLECI",
                "TESTLOOKUP_CI_PROVIDER", "TESTLOOKUP_CI_REPO", "TESTLOOKUP_PR_NUMBER",
                "TESTLOOKUP_CI_ACTOR", "TESTLOOKUP_CI_RUN_URL"):
        monkeypatch.delenv(var, raising=False)

    stream = tr.LiveStream(
        api_key="tlk_x", run_id="run-1", base_url="http://localhost:8000",
        # US-8.1 follow-up: LiveStream also collects a commit range from local
        # git, which would populate meta.metadata regardless of CI. Disabled
        # here so this test keeps asserting the CI-context invariant only.
        collect_commit_range=False,
    )
    assert "metadata" not in stream._meta


# ── CLI wiring: multipart form fields ───────────────────────────────────────


def test_cli_form_data_includes_detected_ci_context(monkeypatch):
    # The CLI package pulls in deps (platformdirs via testlookup_cli.config)
    # that the backend CI env doesn't install — skip there; the CLI detection
    # logic itself is still covered by the byte-identity drift guard below
    # plus the full matrix run against the CLI module copy.
    upload = pytest.importorskip(
        "testlookup_cli.commands.upload", reason="CLI package deps not installed",
    )

    for var in ("GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "TF_BUILD", "CIRCLECI",
                "TESTLOOKUP_CI_PROVIDER", "TESTLOOKUP_CI_REPO", "TESTLOOKUP_PR_NUMBER",
                "TESTLOOKUP_CI_ACTOR", "TESTLOOKUP_CI_RUN_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/webapp")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/421/merge")
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")

    form = upload._build_form_data(project="p", build="b-1", pr_number=555)
    assert form["ci_provider"] == "github_actions"
    assert form["ci_repo"] == "acme/webapp"
    assert form["pr_number"] == "555"  # explicit flag beats detection
    assert form["ci_actor"] == "octocat"


def test_cli_form_data_outside_ci_has_no_ci_fields(monkeypatch):
    upload = pytest.importorskip(
        "testlookup_cli.commands.upload", reason="CLI package deps not installed",
    )

    for var in ("GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "TF_BUILD", "CIRCLECI",
                "TESTLOOKUP_CI_PROVIDER", "TESTLOOKUP_CI_REPO", "TESTLOOKUP_PR_NUMBER",
                "TESTLOOKUP_CI_ACTOR", "TESTLOOKUP_CI_RUN_URL"):
        monkeypatch.delenv(var, raising=False)

    form = upload._build_form_data(project="p", build="b-1")
    for field in ("ci_provider", "ci_repo", "pr_number", "ci_actor", "ci_run_url"):
        assert field not in form
    assert form["project_id"] == "p"


def test_python_copies_share_identical_detection_logic():
    """Guard against silent drift between the SDK and CLI module copies:
    everything below the module docstring must be byte-identical."""
    def body(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        # Strip the module docstring (the only intentional difference).
        end = text.index('"""', text.index('"""') + 3) + 3
        return text[end:]

    sdk = body(_CLIENT_DIR / "ci_context.py")
    cli = body(_CLI_DIR / "testlookup_cli" / "ci_context.py")
    assert sdk == cli
