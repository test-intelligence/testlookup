"""Tests for client-side commit-range collection (US-8.1 follow-up).

The collector lives in two deliberate copies — ``client/commit_range.py``
(shipped with the testlookup-reporter SDK) and
``cli/testlookup_cli/commit_range.py`` (the CLI is a separate installable
package) — exactly as ``ci_context.py`` does. Every collector test here is
parametrized over BOTH copies so they cannot drift silently.

Git-facing behaviour is exercised against REAL temporary git repositories
(including a real ``--depth 1`` shallow clone) rather than mocked stdout, so
the tests pin what git actually emits rather than a guess at its shape. The
only exceptions are the path/field cap tests, which drive ``_parse_log``
directly — a >512-character path cannot be created on Windows without long
paths enabled, and the cap is pure-function territory anyway.

Run with:  python -m pytest client/tests cli/tests
"""
from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SDK_CR = _load("_sdk_commit_range", _REPO_ROOT / "client" / "commit_range.py")
CLI_CR = _load("_cli_commit_range", _REPO_ROOT / "cli" / "testlookup_cli" / "commit_range.py")

both = pytest.mark.parametrize("cr", [SDK_CR, CLI_CR], ids=["sdk", "cli"])


# ── git repo fixtures ─────────────────────────────────────────────────────────

def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (proc.stdout or "").strip()


def _init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], path)
    _git(["config", "user.email", "dev@example.com"], path)
    _git(["config", "user.name", "Dev Example"], path)
    _git(["config", "commit.gpgsign", "false"], path)
    return path


def _commit(path: Path, message: str, files: dict[str, str] | None = None) -> str:
    if files:
        for name, content in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        _git(["add", "-A"], path)
        _git(["commit", "-m", message], path)
    else:
        _git(["commit", "--allow-empty", "-m", message], path)
    return _git(["rev-parse", "HEAD"], path)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo on ``main`` with two commits."""
    path = _init(tmp_path / "repo")
    _commit(path, "root commit", {"README.md": "hello\n"})
    _commit(path, "second commit", {"src/app.py": "print(1)\n"})
    return path


@pytest.fixture
def branch_repo(repo: Path) -> Path:
    """``repo`` plus a ``feature`` branch carrying two commits off main."""
    _git(["checkout", "-b", "feature"], repo)
    _commit(repo, "feature one", {"src/one.py": "one\n"})
    _commit(repo, "feature two", {"src/two.py": "two\n"})
    return repo


# ── _safe_ref: option-injection + sentinel rejection ──────────────────────────

@both
@pytest.mark.parametrize("bad", [
    None, "", "   ",
    "--upload-pack=evil",           # option-shaped: git would reparse it
    "-x",
    "0000000000000000000000000000000000000000",   # CI "no previous commit"
    "0" * 64,
    "main;rm -rf /",                # outside the ref charset
    "main branch",
    "ref\nwith\nnewline",
    "a" * 300,
])
def test_safe_ref_rejects(cr, bad):
    assert cr._safe_ref(bad) is None


@both
@pytest.mark.parametrize("good", [
    "main", "origin/main", "refs/heads/main", "HEAD~1", "HEAD^{commit}",
    "release/2.5.0", "deadbeef" * 5,
])
def test_safe_ref_accepts(cr, good):
    assert cr._safe_ref(good) == good


# ── CI-provided diff base (verified provider variables) ───────────────────────

@both
def test_github_pr_event_payload_base_sha(cr, tmp_path):
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps({"pull_request": {"base": {"sha": "a" * 40}}}))
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(payload),
        "GITHUB_BASE_REF": "main",
    }
    assert cr.detect_ci_base_ref(env) == "a" * 40


@both
def test_github_pr_falls_back_to_base_ref_branch(cr, tmp_path):
    """No usable payload → GITHUB_BASE_REF (a branch name) is the base."""
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps({"pull_request": {"base": {}}}))
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(payload),
        "GITHUB_BASE_REF": "main",
    }
    assert cr.detect_ci_base_ref(env) == "main"


@both
def test_github_push_event_uses_before(cr, tmp_path):
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps({"before": "b" * 40}))
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_EVENT_PATH": str(payload),
    }
    assert cr.detect_ci_base_ref(env) == "b" * 40


@both
def test_github_push_all_zero_before_is_rejected(cr, tmp_path):
    """A branch's first push reports the all-zero sentinel — not a base."""
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps({"before": "0" * 40}))
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_EVENT_PATH": str(payload),
    }
    assert cr.detect_ci_base_ref(env) is None


@both
def test_github_unreadable_event_payload_is_not_fatal(cr, tmp_path):
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_EVENT_PATH": str(tmp_path / "does-not-exist.json"),
    }
    assert cr.detect_ci_base_ref(env) is None


@both
def test_gitlab_merge_request_diff_base_sha(cr):
    env = {"GITLAB_CI": "true", "CI_MERGE_REQUEST_DIFF_BASE_SHA": "c" * 40}
    assert cr.detect_ci_base_ref(env) == "c" * 40


@both
def test_gitlab_zero_before_sha_falls_through_to_target_branch(cr):
    """CI_COMMIT_BEFORE_SHA is all-zero in MR pipelines — skip to the branch."""
    env = {
        "GITLAB_CI": "true",
        "CI_COMMIT_BEFORE_SHA": "0" * 40,
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
    }
    assert cr.detect_ci_base_ref(env) == "main"


@both
def test_gitlab_commit_before_sha_used_on_branch_pipelines(cr):
    env = {"GITLAB_CI": "true", "CI_COMMIT_BEFORE_SHA": "d" * 40}
    assert cr.detect_ci_base_ref(env) == "d" * 40


@both
def test_jenkins_change_target_wins(cr):
    env = {
        "JENKINS_URL": "https://ci.example.com/",
        "CHANGE_TARGET": "main",
        "GIT_PREVIOUS_SUCCESSFUL_COMMIT": "e" * 40,
    }
    assert cr.detect_ci_base_ref(env) == "main"


@both
def test_jenkins_previous_successful_commit_fallback(cr):
    env = {
        "JENKINS_URL": "https://ci.example.com/",
        "GIT_PREVIOUS_SUCCESSFUL_COMMIT": "e" * 40,
    }
    assert cr.detect_ci_base_ref(env) == "e" * 40


@both
def test_azure_target_branch(cr):
    env = {"TF_BUILD": "True", "SYSTEM_PULLREQUEST_TARGETBRANCH": "refs/heads/main"}
    assert cr.detect_ci_base_ref(env) == "refs/heads/main"


@both
def test_azure_source_commit_id_is_not_used_as_base(cr):
    """SYSTEM_PULLREQUEST_SOURCECOMMITID is the commit under review (head)."""
    env = {"TF_BUILD": "True", "SYSTEM_PULLREQUEST_SOURCECOMMITID": "f" * 40}
    assert cr.detect_ci_base_ref(env) is None


@both
def test_circleci_has_no_verified_base_variable(cr):
    """CircleCI exposes no built-in env var carrying a diff base — omitted
    on purpose so the local-git fallback takes over."""
    env = {"CIRCLECI": "true", "CIRCLE_SHA1": "a" * 40, "CIRCLE_BRANCH": "feature"}
    assert cr.detect_ci_base_ref(env) is None


@both
def test_no_ci_no_base(cr):
    assert cr.detect_ci_base_ref({}) is None


# ── Base-resolution precedence (real repos) ───────────────────────────────────

@both
def test_precedence_explicit_beats_everything(cr, branch_repo):
    root = _git(["rev-list", "--max-parents=0", "HEAD"], branch_repo)
    env = {
        "GITLAB_CI": "true",
        "CI_MERGE_REQUEST_DIFF_BASE_SHA": _git(["rev-parse", "main"], branch_repo),
        "TESTLOOKUP_COMMIT_RANGE_BASE": "main",
    }
    rng = cr.collect_commit_range(
        explicit_base=root, overrides={"commit_range_base": "main"},
        env=env, repo_path=str(branch_repo),
    )
    assert rng is not None
    assert rng["base_commit"] == root
    assert rng["base_source"] == "user"


@both
def test_precedence_env_beats_config_and_ci(cr, branch_repo):
    root = _git(["rev-list", "--max-parents=0", "HEAD"], branch_repo)
    env = {
        "GITLAB_CI": "true",
        "CI_MERGE_REQUEST_DIFF_BASE_SHA": _git(["rev-parse", "main"], branch_repo),
        "TESTLOOKUP_COMMIT_RANGE_BASE": root,
    }
    rng = cr.collect_commit_range(
        overrides={"commit_range_base": "main"}, env=env, repo_path=str(branch_repo),
    )
    assert rng["base_commit"] == root
    assert rng["base_source"] == "user"


@both
def test_precedence_config_beats_ci(cr, branch_repo):
    root = _git(["rev-list", "--max-parents=0", "HEAD"], branch_repo)
    env = {
        "GITLAB_CI": "true",
        "CI_MERGE_REQUEST_DIFF_BASE_SHA": _git(["rev-parse", "main"], branch_repo),
    }
    rng = cr.collect_commit_range(
        overrides={"commit_range_base": root}, env=env, repo_path=str(branch_repo),
    )
    assert rng["base_commit"] == root
    assert rng["base_source"] == "user"


@both
def test_precedence_ci_beats_merge_base_fallback(cr, branch_repo):
    """CI hands us the root commit; merge-base would have said main's tip."""
    root = _git(["rev-list", "--max-parents=0", "HEAD"], branch_repo)
    env = {"GITLAB_CI": "true", "CI_MERGE_REQUEST_DIFF_BASE_SHA": root}
    rng = cr.collect_commit_range(env=env, repo_path=str(branch_repo))
    assert rng["base_commit"] == root
    assert rng["base_source"] == "ci"
    assert len(rng["commits"]) == 3   # second + feature one + feature two


@both
def test_fallback_merge_base_against_default_branch(cr, branch_repo):
    """No CI, no user base → merge-base(main, HEAD) = main's tip."""
    main_tip = _git(["rev-parse", "main"], branch_repo)
    rng = cr.collect_commit_range(env={}, repo_path=str(branch_repo))
    assert rng["base_commit"] == main_tip
    assert rng["base_source"] == "git_fallback"
    assert [c["message"] for c in rng["commits"]] == ["feature one", "feature two"]


@both
def test_fallback_previous_commit_on_default_branch(cr, repo):
    """On main itself, merge-base == HEAD, so HEAD^ is the honest base."""
    parent = _git(["rev-parse", "HEAD^"], repo)
    rng = cr.collect_commit_range(env={}, repo_path=str(repo))
    assert rng["base_commit"] == parent
    assert rng["base_source"] == "git_fallback"
    assert [c["message"] for c in rng["commits"]] == ["second commit"]


@both
def test_ci_base_equal_to_head_falls_through_to_git(cr, repo):
    """A default-branch pipeline whose 'base' is HEAD gives an empty range —
    fall through rather than emit nothing."""
    head = _git(["rev-parse", "HEAD"], repo)
    env = {"GITLAB_CI": "true", "CI_MERGE_REQUEST_DIFF_BASE_SHA": head}
    rng = cr.collect_commit_range(env=env, repo_path=str(repo))
    assert rng["base_source"] == "git_fallback"
    assert rng["base_commit"] == _git(["rev-parse", "HEAD^"], repo)


@both
def test_unresolvable_ci_base_falls_through_to_git(cr, branch_repo):
    env = {"GITLAB_CI": "true", "CI_MERGE_REQUEST_DIFF_BASE_SHA": "9" * 40}
    rng = cr.collect_commit_range(env=env, repo_path=str(branch_repo))
    assert rng["base_source"] == "git_fallback"


@both
def test_unresolvable_user_base_emits_nothing(cr, branch_repo):
    """A user told us the base. If it doesn't resolve we do NOT silently
    substitute a guess — an absent range is honest."""
    rng = cr.collect_commit_range(
        explicit_base="9" * 40, env={}, repo_path=str(branch_repo),
    )
    assert rng is None


@both
def test_single_commit_repo_emits_nothing(cr, tmp_path):
    """No base exists at all in a full clone → send nothing."""
    path = _init(tmp_path / "solo")
    _commit(path, "only commit", {"a.txt": "a\n"})
    assert cr.collect_commit_range(env={}, repo_path=str(path)) is None


@both
def test_ci_branch_name_resolves_via_remote_tracking_ref(cr, tmp_path):
    """CI hands out a branch NAME on a detached checkout — resolution has to
    try origin/<name>, not just <name>."""
    origin = _init(tmp_path / "origin")
    root = _commit(origin, "root", {"a.txt": "a\n"})
    _git(["branch", "release"], origin)          # release stays at root
    main_tip = _commit(origin, "main tip", {"b.txt": "b\n"})

    clone = tmp_path / "clone"
    _git(["clone", origin.as_uri(), str(clone)], tmp_path)
    _git(["config", "user.email", "dev@example.com"], clone)
    _git(["config", "user.name", "Dev Example"], clone)
    _git(["checkout", "-b", "feature"], clone)
    _commit(clone, "work", {"c.txt": "c\n"})
    _git(["branch", "-D", "main"], clone)        # only origin/* refs remain

    # The PR targets `release`, so the base is root — NOT main's tip, which
    # is what the merge-base fallback would have produced.
    env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request",
           "GITHUB_BASE_REF": "release"}
    rng = cr.collect_commit_range(env=env, repo_path=str(clone))
    assert rng["base_source"] == "ci"
    assert rng["base_commit"] == root
    assert rng["base_commit"] != main_tip


# ── Shallow clones ────────────────────────────────────────────────────────────

@pytest.fixture
def shallow_clone(tmp_path: Path) -> Path:
    origin = _init(tmp_path / "origin")
    _commit(origin, "one", {"a.txt": "a\n"})
    _commit(origin, "two", {"b.txt": "b\n"})
    _commit(origin, "three", {"c.txt": "c\n"})
    clone = tmp_path / "shallow"
    _git(["clone", "--depth", "1", origin.as_uri(), str(clone)], tmp_path)
    return clone


@both
def test_shallow_repository_is_detected(cr, shallow_clone, repo):
    assert cr._is_shallow(str(shallow_clone)) is True
    assert cr._is_shallow(str(repo)) is False


@both
def test_shallow_clone_degrades_to_head_only(cr, shallow_clone):
    """The base isn't in a --depth 1 history. Emit the one commit we can
    prove is present — never a fabricated range."""
    rng = cr.collect_commit_range(env={}, repo_path=str(shallow_clone))
    assert rng is not None
    assert rng["degraded"] is True
    assert rng["base_source"] == "head_only"
    assert rng["base_commit"] is None
    assert len(rng["commits"]) == 1
    assert rng["commits"][0]["message"] == "three"
    assert rng["head_commit"] == rng["commits"][0]["sha"]


@both
def test_shallow_clone_with_unresolvable_user_base_degrades(cr, shallow_clone):
    rng = cr.collect_commit_range(
        explicit_base="9" * 40, env={}, repo_path=str(shallow_clone),
    )
    assert rng is not None and rng["degraded"] is True
    assert len(rng["commits"]) == 1


# ── Failure is never fatal ────────────────────────────────────────────────────

@both
def test_non_git_directory_returns_none(cr, tmp_path):
    # Covers a directory OUTSIDE any checkout, where git fails on its own. It
    # is NOT the regression test for TL-2026-08-29-02-001 -- it passes with
    # that fix removed. See the nested test below.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert cr.collect_commit_range(env={}, repo_path=str(plain)) is None
    assert cr.resolve_commit_range(env={}, repo_path=str(plain)) is None


@both
def test_a_plain_directory_inside_a_checkout_does_not_report_the_parents_history(
    cr, repo
):
    """TL-2026-08-29-02-001.

    ``git -C <path>`` searches upward for a repository, so a plain directory
    NESTED inside a checkout -- a build folder, a temp dir under the workspace
    -- silently resolved to the parent repository and reported its commit
    range as if it were the caller's.

    The fix (d2bbad24) shipped with the test above as its only coverage. That
    test's directory sits outside any checkout, where git fails with or without
    the fix, so it passed with the fix removed: the defect had no real
    regression test. This one puts the directory inside a real checkout.
    """
    # Positive control: the parent must HAVE history to leak. Without it, a
    # None below could just mean "nothing there", and this test would be as
    # vacuous as the one it replaces.
    parent_range = cr.collect_commit_range(env={}, repo_path=str(repo))
    assert parent_range is not None and parent_range["commits"], (
        "fixture checkout has no commit range, so a leak could not be observed"
    )

    nested = repo / "build" / "plain"
    nested.mkdir(parents=True)

    assert cr.collect_commit_range(env={}, repo_path=str(nested)) is None, (
        "a plain directory inside a checkout reported the parent's history"
    )
    assert cr.resolve_commit_range(env={}, repo_path=str(nested)) is None

    # And it says why, rather than looking like a git failure.
    outcome = cr.diagnose_commit_range(env={}, repo_path=str(nested))
    assert outcome.reason == cr.REASON_NO_CHECKOUT, outcome.detail


@both
def test_missing_git_binary_is_not_fatal(cr, repo, monkeypatch):
    def _boom(*_a, **_kw):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(cr.subprocess, "run", _boom)
    assert cr.collect_commit_range(env={}, repo_path=str(repo)) is None


@both
def test_git_timeout_is_not_fatal(cr, repo, monkeypatch):
    def _timeout(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=1.0)

    monkeypatch.setattr(cr.subprocess, "run", _timeout)
    assert cr.collect_commit_range(env={}, repo_path=str(repo)) is None


@both
def test_unexpected_exception_is_swallowed(cr, repo, monkeypatch):
    monkeypatch.setattr(
        cr, "_rev_parse_result",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert cr.collect_commit_range(env={}, repo_path=str(repo)) is None


@both
def test_option_shaped_repo_path_is_refused(cr):
    assert cr._git(["rev-parse", "HEAD"], repo_path="--exec-path=/evil") is None


# ── "git said no" is not "git never answered" ─────────────────────────────────
#
# This whole section exists because of a real CI flake: the collector returned
# a bare None for a repo it had built itself, and there was no way to tell
# afterwards whether git had answered "no such ref" or had failed to answer at
# all. Both arrived as None, both took the do-not-guess branch, and the cause
# was gone. Every assertion below pins the distinction that was missing.


@both
def test_a_transient_git_failure_is_not_blamed_on_the_users_base(cr, repo, monkeypatch):
    """The bug this section exists for.

    git resolves HEAD, then stops answering while we look up the base the user
    named. That tells us NOTHING about whether their base exists, so it must
    not be reported as ``user_base_unresolvable`` — the ref is fine, we are
    not.
    """
    base = _git(["rev-parse", "HEAD^"], repo)   # a real, resolvable base
    real_run = cr.subprocess.run
    calls = {"n": 0}

    def _fails_after_head(argv, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:            # let the HEAD probe through
            return real_run(argv, **kwargs)
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1.0)

    monkeypatch.setattr(cr.subprocess, "run", _fails_after_head)
    outcome = cr.diagnose_commit_range(
        explicit_base=base, env={}, repo_path=str(repo),
    )
    assert outcome.commit_range is None
    assert outcome.reason == cr.REASON_GIT_FAILED, outcome.detail
    assert "timed out" in outcome.detail


@both
def test_a_genuinely_absent_user_base_is_still_reported_as_such(cr, branch_repo):
    """The other half of the distinction: when git DOES answer "no such ref",
    we must say so rather than crying outage."""
    outcome = cr.diagnose_commit_range(
        explicit_base="9" * 40, env={}, repo_path=str(branch_repo),
    )
    assert outcome.commit_range is None
    assert outcome.reason == cr.REASON_USER_BASE_UNRESOLVABLE, outcome.detail


@both
def test_a_git_process_killed_by_a_signal_counts_as_unavailable(cr, repo, monkeypatch):
    """A negative returncode means git was killed before it could answer —
    the OOM-killer case. Treating that as "the ref does not exist" would turn
    machine pressure into a false statement about the repository."""
    class _Killed:
        returncode = -9
        stdout = ""
        stderr = ""

    monkeypatch.setattr(cr.subprocess, "run", lambda *_a, **_kw: _Killed())
    result = cr._run_git(["rev-parse", "HEAD"], repo_path=str(repo))
    assert result.status == cr.GIT_UNAVAILABLE
    assert "signal 9" in result.detail


@both
def test_a_fatal_exit_counts_as_unavailable_not_as_a_no(cr, repo, monkeypatch):
    """Exit 1 is git's conventional "no". Anything above it is a ``fatal:``
    git could not complete — an unreadable object, a torn index — which is a
    failure to answer, not an answer."""
    class _Fatal:
        returncode = 128
        stdout = ""
        stderr = "fatal: unable to read tree deadbeef\n"

    monkeypatch.setattr(cr.subprocess, "run", lambda *_a, **_kw: _Fatal())
    result = cr._run_git(["rev-parse", "HEAD"], repo_path=str(repo))
    assert result.status == cr.GIT_UNAVAILABLE
    assert "unable to read tree" in result.detail


@both
def test_every_none_carries_a_reason(cr, repo, tmp_path):
    """No caller should ever have to guess again."""
    plain = tmp_path / "plain"
    plain.mkdir()
    cases = {
        cr.REASON_DISABLED: dict(enabled=False, repo_path=str(repo)),
        cr.REASON_NO_CHECKOUT: dict(repo_path=str(plain)),
        cr.REASON_USER_BASE_UNRESOLVABLE: dict(
            explicit_base="9" * 40, repo_path=str(repo)
        ),
    }
    for expected, kwargs in cases.items():
        outcome = cr.diagnose_commit_range(env={}, **kwargs)
        assert outcome.commit_range is None
        assert outcome.reason == expected, outcome.detail
        assert outcome.detail, f"{expected} produced no detail"


@both
def test_a_successful_collection_says_so(cr, branch_repo):
    outcome = cr.diagnose_commit_range(env={}, repo_path=str(branch_repo))
    assert outcome.reason == cr.REASON_OK
    assert outcome.commit_range == cr.collect_commit_range(
        env={}, repo_path=str(branch_repo)
    )


@both
def test_a_shallow_degrade_is_reported_as_degraded(cr, shallow_clone):
    outcome = cr.diagnose_commit_range(env={}, repo_path=str(shallow_clone))
    assert outcome.reason == cr.REASON_DEGRADED
    assert outcome.commit_range["degraded"] is True


@both
def test_git_outages_are_logged_loudly_normal_empties_are_not(cr, repo, monkeypatch, caplog):
    """A client SDK must not shout about a run with nothing to report — but a
    git that worked and then stopped answering is an anomaly, and burying it
    at DEBUG is what made the original flake unexplainable."""
    caplog.set_level(logging.DEBUG, logger=cr.logger.name)

    cr.collect_commit_range(enabled=False, env={}, repo_path=str(repo))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    caplog.clear()
    base = _git(["rev-parse", "HEAD^"], repo)   # before the patch bites
    real_run = cr.subprocess.run
    calls = {"n": 0}

    def _fails_after_head(argv, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_run(argv, **kwargs)
        raise OSError("Cannot allocate memory")

    monkeypatch.setattr(cr.subprocess, "run", _fails_after_head)
    cr.collect_commit_range(explicit_base=base, env={}, repo_path=str(repo))
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a git outage mid-collection must not be silent"
    assert "Cannot allocate memory" in warnings[0].getMessage()


# ── argv discipline ───────────────────────────────────────────────────────────

@both
def test_git_is_invoked_with_an_argv_list_never_a_shell(cr, repo, monkeypatch):
    seen: list = []
    real_run = cr.subprocess.run

    def _spy(argv, **kwargs):
        seen.append((argv, kwargs))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(cr.subprocess, "run", _spy)
    cr.collect_commit_range(env={}, repo_path=str(repo))

    assert seen, "expected at least one git invocation"
    for argv, kwargs in seen:
        assert isinstance(argv, list), f"argv must be a list, got {type(argv)}"
        assert all(isinstance(a, str) for a in argv)
        assert argv[0] == "git"
        assert kwargs.get("shell") in (None, False)


@both
def test_hostile_base_ref_never_reaches_git_as_an_option(cr, repo, monkeypatch):
    """An option-shaped base from the environment must be rejected before any
    subprocess runs — it can never become a git flag."""
    seen: list[list[str]] = []
    real_run = cr.subprocess.run

    def _spy(argv, **kwargs):
        seen.append(list(argv))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(cr.subprocess, "run", _spy)
    cr.collect_commit_range(
        env={"TESTLOOKUP_COMMIT_RANGE_BASE": "--upload-pack=touch /tmp/pwned"},
        repo_path=str(repo),
    )
    for argv in seen:
        assert not any("upload-pack" in a for a in argv)
        assert not any("pwned" in a for a in argv)


# ── Caps ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def many_commit_repo(tmp_path_factory) -> tuple[Path, str]:
    """105 commits past a known base, built by a single ``git fast-import``.

    The obvious version of this fixture — a Python loop calling ``git commit
    --allow-empty`` 105 times — spawned 211 git processes and accounted for
    most of this module's runtime. That is a lot of environment to be exposed
    to for a fixture that only needs a history longer than ``MAX_COMMITS``,
    and every one of those spawns is a chance for a loaded CI runner to fail
    one. One ``fast-import`` builds the same real history in one process.

    Timestamps are fixed and strictly increasing rather than "whatever the
    clock said", which also removes a latent hazard: ``git log`` walks a
    date-ordered queue, so a test asserting the exact identity of the newest
    100 commits should not depend on 106 commits landing in a readable order
    within the same second.
    """
    path = _init(tmp_path_factory.mktemp("many"))
    stream = ["blob", "mark :1", "data 2", "a"]
    for i, message in enumerate(["base"] + [f"change {i:03d}" for i in range(105)]):
        stream += [
            "commit refs/heads/main",
            f"committer Dev Example <dev@example.com> {1_700_000_000 + i} +0000",
            f"data {len(message.encode('utf-8'))}",
            message,
        ]
        if i == 0:
            stream.append("M 100644 :1 a.txt")   # the rest carry the same tree
    # bytes, not text=True: fast-import counts the bytes a ``data`` header
    # promises, and Python's text mode would translate every \n to \r\n on
    # Windows and desynchronise the stream.
    subprocess.run(
        ["git", "fast-import", "--quiet"], cwd=str(path), check=True,
        input=("\n".join(stream) + "\n").encode("utf-8"),
        capture_output=True,
    )
    _git(["reset", "--hard", "main"], path)
    base = _git(["rev-list", "--max-parents=0", "HEAD"], path)
    return path, base


@both
def test_commit_cap_keeps_newest_hundred_oldest_first(cr, many_commit_repo):
    path, base = many_commit_repo

    # diagnose_* rather than collect_*: when this assertion failed in CI it
    # said only "assert None is not None", which cost a manual re-run to
    # learn nothing. The reason now travels with the failure.
    outcome = cr.diagnose_commit_range(
        explicit_base=base, env={}, repo_path=str(path),
    )
    rng = outcome.commit_range
    assert rng is not None, f"{outcome.reason}: {outcome.detail}"
    commits = rng["commits"]
    assert len(commits) == cr.MAX_COMMITS == 100
    assert rng["truncated"] is True
    # Newest 100 of the 105, still oldest→newest to match the server's
    # normalizer (which reads raw_commits[:100] off an oldest-first list).
    assert commits[0]["message"] == "change 005"
    assert commits[-1]["message"] == "change 104"


@both
def test_files_per_commit_cap(cr, tmp_path):
    path = _init(tmp_path / "wide")
    base = _commit(path, "base", {"a.txt": "a\n"})
    _commit(path, "wide commit", {f"f/{i:04d}.txt": "x\n" for i in range(cr.FILES_PER_COMMIT_CAP + 1)})

    rng = cr.collect_commit_range(explicit_base=base, env={}, repo_path=str(path))
    assert len(rng["commits"]) == 1
    assert len(rng["commits"][0]["files"]) == cr.FILES_PER_COMMIT_CAP == 500


@both
def test_parse_log_caps_path_and_field_lengths(cr):
    """Driven through ``_parse_log`` — a >512-char path can't be created on
    Windows without long paths enabled, and the caps are pure functions."""
    long_path = "d/" * 400 + "file.py"
    record = cr._RS + cr._US.join([
        "a" * 80,                       # sha longer than the 64 cap
        "N" * 400,                      # author longer than the 255 cap
        "2026-08-04T10:00:00+00:00",
        "S" * 3000,                     # subject longer than the 2000 cap
    ]) + "\n" + long_path
    commits = cr._parse_log(record)

    assert len(commits) == 1
    commit = commits[0]
    assert len(commit["sha"]) == cr.SHA_CAP == 64
    assert len(commit["author"]) == cr.AUTHOR_CAP == 255
    assert len(commit["message"]) == cr.MESSAGE_CAP == 2000
    assert len(commit["files"][0]) == cr.FILE_PATH_CAP == 512


@both
def test_parse_log_skips_malformed_records(cr):
    malformed = cr._RS + "only-a-sha" + "\n" + cr._RS + cr._US.join(
        ["", "Dev", "2026-08-04T10:00:00+00:00", "empty sha"]
    )
    assert cr._parse_log(malformed) == []


# ── Payload shape ─────────────────────────────────────────────────────────────

@both
def test_commit_shape_matches_supplied_commit(cr, branch_repo):
    rng = cr.collect_commit_range(env={}, repo_path=str(branch_repo))
    for commit in rng["commits"]:
        assert set(commit) == {"sha", "author", "message", "files", "committed_at"}
        assert len(commit["sha"]) == 40
        assert commit["author"] == "Dev Example"
        assert commit["committed_at"].startswith("20")
        assert isinstance(commit["files"], list)
    assert rng["commits"][0]["files"] == ["src/one.py"]
    assert rng["commits"][1]["files"] == ["src/two.py"]


@both
def test_resolve_commit_range_returns_the_wire_object(cr, branch_repo):
    """The wire form carries the boundary, not just the commits.

    This test previously asserted a bare ``commits`` list. That shape is
    accepted by the server but discards the base: the row lands with
    ``base_commit = NULL`` / ``base_source = "unavailable"``, which is exactly
    what makes it useless as Epic-10 training data. The assertion was pinning
    the defect, so it now pins the boundary-carrying object instead.
    """
    wire = cr.resolve_commit_range(env={}, repo_path=str(branch_repo))
    full = cr.collect_commit_range(env={}, repo_path=str(branch_repo))
    assert wire["commits"] == full["commits"]
    assert wire["base_commit"] == full["base_commit"]
    assert wire["head_commit"] == full["head_commit"]
    assert json.dumps(wire)  # JSON-serialisable for the multipart form


# ── Opt-out ───────────────────────────────────────────────────────────────────

@both
@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE"])
def test_opt_out_via_env(cr, branch_repo, value):
    env = {cr.ENV_ENABLED: value}
    assert cr.collect_commit_range(env=env, repo_path=str(branch_repo)) is None


@both
def test_opt_out_via_config(cr, branch_repo):
    rng = cr.collect_commit_range(
        overrides={"collect_commit_range": False}, env={}, repo_path=str(branch_repo),
    )
    assert rng is None


@both
def test_opt_out_via_explicit_argument(cr, branch_repo):
    assert cr.collect_commit_range(
        enabled=False, env={}, repo_path=str(branch_repo)
    ) is None


@both
def test_explicit_enable_beats_config_opt_out(cr, branch_repo):
    rng = cr.collect_commit_range(
        enabled=True, overrides={"collect_commit_range": False},
        env={}, repo_path=str(branch_repo),
    )
    assert rng is not None


@both
def test_env_opt_out_beats_config_opt_in(cr, branch_repo):
    rng = cr.collect_commit_range(
        overrides={"collect_commit_range": True},
        env={cr.ENV_ENABLED: "0"}, repo_path=str(branch_repo),
    )
    assert rng is None


@both
def test_enabled_by_default(cr):
    assert cr.is_collection_enabled({}) is True


@both
def test_unparseable_opt_out_value_is_ignored(cr):
    assert cr.is_collection_enabled({cr.ENV_ENABLED: "maybe"}) is True


# ── Copies must not drift ─────────────────────────────────────────────────────

def test_sdk_and_cli_copies_are_identical_below_the_header():
    """The two copies are deliberate duplicates; only the module docstring
    may differ. Anything else drifting is a bug."""
    sdk = (_REPO_ROOT / "client" / "commit_range.py").read_text(encoding="utf-8")
    cli = (_REPO_ROOT / "cli" / "testlookup_cli" / "commit_range.py").read_text(encoding="utf-8")
    marker = "from __future__ import annotations"
    assert sdk[sdk.index(marker):] == cli[cli.index(marker):]


# ── wire-shape contract: the boundary must survive the trip ────────────────


@both
def test_resolve_returns_boundary_object_not_a_bare_list(cr, repo):
    """``resolve_commit_range`` must emit {base_commit, head_commit, commits}.

    The server accepts a bare ``commits`` list too, but that shape throws the
    boundary away: the row lands with ``base_commit = NULL`` and
    ``base_source = "unavailable"``, discarding the base we just resolved and
    making the row useless as Epic-10 training data. Caught exactly this at
    merge — the collector resolved a base and the wire form dropped it.
    """
    out = cr.resolve_commit_range(repo_path=str(repo), env={})
    assert out is not None, "expected a range from the fixture repo"
    assert isinstance(out, dict), f"wire form regressed to {type(out).__name__}"
    assert set(out) == {"base_commit", "head_commit", "commits"}
    assert isinstance(out["commits"], list) and out["commits"]
    assert out["head_commit"], "head_commit must survive onto the wire"


@both
def test_wire_keys_are_ones_the_server_accepts(cr):
    """Emitted keys must match SuppliedCommitRange's aliases.

    Server accepts base|base_commit|from_commit and head|head_commit|
    to_commit (backend/app/models/schemas.py). Anything else is silently
    dropped by Pydantic's extra="ignore" — a failure with no error.
    """
    import inspect as _inspect

    src = _inspect.getsource(cr.resolve_commit_range)
    assert any(f'"{k}"' in src for k in ("base", "base_commit", "from_commit"))
    assert any(f'"{k}"' in src for k in ("head", "head_commit", "to_commit"))
