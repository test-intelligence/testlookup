"""A Fixer patch may only name plain, repo-relative test paths.

The "test code only" rule used to be a regular expression over the diff header
text, so ``tests/../.github/workflows/ci.yml`` matched ``tests/**`` and the
pull-request step then put that raw path into a GitHub contents URL.

The rule now:

* a diff path that is absolute, has a ``.`` or ``..`` segment, contains a
  backslash, or is not already in its normal form is refused whatever the
  globs say;
* nothing under ``.github/`` is accepted, whatever the globs say;
* ``open_draft_pr`` refuses such a path itself (second layer) and URL-quotes
  every path segment it sends.
"""
from __future__ import annotations

import base64

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.fixer import pipeline  # noqa: E402
from app.agents.fixer.pipeline import (  # noqa: E402
    open_draft_pr,
    patch_touches_only_test_globs,
)
from app.agents.fixer.state import FixCandidate  # noqa: E402

pytestmark = pytest.mark.regression

DEFAULT_GLOBS = ["tests/**", "**/*.spec.*", "**/*.test.*"]


def _diff(*paths: str) -> str:
    return "".join(
        f"--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n" for p in paths
    )


# ── The glob check ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "tests/../.github/workflows/ci.yml",   # the audit's proof of concept
    "tests/../conftest.py",
    "tests/sub/../../setup.py",
    "./tests/test_a.py",
    "tests/./test_a.py",
    "/tests/test_a.py",
    "tests\\test_a.py",
    "tests\\..\\setup.py",
    "tests//test_a.py",
    "tests/test_a.py/",
    "..",
])
def test_a_path_that_is_not_plain_is_refused_even_when_a_glob_matches(path):
    ok, offending = patch_touches_only_test_globs(_diff(path), ["tests/**", "**"])
    assert ok is False
    assert path in offending


@pytest.mark.parametrize("path", [
    ".github/workflows/a.test.yml",
    ".github/workflows/ci.yml",
    ".github/x.spec.js",
    ".GitHub/workflows/a.test.yml",
])
def test_nothing_under_dot_github_is_accepted_whatever_the_globs_say(path):
    ok, offending = patch_touches_only_test_globs(_diff(path), DEFAULT_GLOBS + ["**", ".github/**"])
    assert ok is False
    assert offending == [path]


def test_one_bad_path_refuses_the_whole_patch():
    ok, offending = patch_touches_only_test_globs(
        _diff("tests/test_a.py", "tests/../src/app.py"), DEFAULT_GLOBS,
    )
    assert ok is False
    assert offending == ["tests/../src/app.py"]


@pytest.mark.parametrize("paths", [
    ("tests/unit/test_a.py",),
    ("src/a.spec.ts",),
    ("tests/unit/test_a.py", "src/a.spec.ts", "api/user.test.js"),
    ("tests/.hidden/test_a.py",),
    ("tests/data/a..b.json",),            # dots inside a name are not a ".." segment
    ("docs/github/x.test.md",),           # "github" without the leading dot is ordinary
])
def test_plain_test_paths_are_still_accepted(paths):
    ok, offending = patch_touches_only_test_globs(_diff(*paths), DEFAULT_GLOBS)
    assert ok is True and offending == []


# ── The pull-request step ────────────────────────────────────────────────────


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class _RecordingClient:
    """Stands in for httpx.AsyncClient: records every request, sends nothing."""

    requests: list[tuple[str, str]] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **k):
        _RecordingClient.requests.append(("GET", url))
        if "/contents/" in url:
            return _Response(200, {
                "content": base64.b64encode(b"old\n").decode("ascii"), "sha": "s1",
            })
        if "/git/ref/" in url:
            return _Response(200, {"object": {"sha": "base"}})
        return _Response(200, {"default_branch": "main"})

    async def post(self, url, **k):
        _RecordingClient.requests.append(("POST", url))
        if url.endswith("/pulls"):
            return _Response(201, {"html_url": "https://github.com/o/r/pull/9", "number": 9})
        return _Response(201)

    async def put(self, url, **k):
        _RecordingClient.requests.append(("PUT", url))
        return _Response(200)


async def _open(monkeypatch, patch: str, **over):
    import httpx

    from app.services import github_checks_service

    async def _no_block(_url):
        return None

    monkeypatch.setattr(github_checks_service, "_ssrf_block_reason", _no_block)
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    _RecordingClient.requests = []
    kwargs = dict(
        api_base_url="https://api.github.com", repo_owner="o", repo_name="r",
        pat="not-a-real-token",
        candidate=FixCandidate(
            test_fingerprint="f" * 64, test_name="tests/test_a.py::test_k",
            suite_name="s", flip_rate=0.5, flip_window_size=10,
        ),
        patch=patch,
        validation={"reruns": 5, "passed": 5},
        reasoning="seed the rng",
        ledger_deep_link="/fixer/runs/x",
        runner_type="docker",
    )
    kwargs.update(over)
    return await open_draft_pr(**kwargs)


def _content_urls() -> list[str]:
    return [url for _method, url in _RecordingClient.requests if "/contents/" in url]


@pytest.mark.parametrize("path", [
    "tests/../.github/workflows/ci.yml",
    "tests/../conftest.py",
    "./tests/test_a.py",
    "/etc/passwd",
    "tests\\test_a.py",
    ".github/workflows/a.test.yml",
])
async def test_the_pull_request_step_never_requests_a_path_that_is_not_plain(monkeypatch, path):
    result = await _open(monkeypatch, _diff(path))
    assert "pr_url" not in result
    assert "refused" in result["error"] and path in result["error"]
    assert _content_urls() == []
    assert not any(".." in url for _m, url in _RecordingClient.requests)
    # Nothing was written at all: no branch, no file, no pull request.
    assert [m for m, _u in _RecordingClient.requests if m in ("POST", "PUT")] == []


async def test_a_header_the_glob_check_read_differently_is_refused(monkeypatch):
    # Two spaces after "+++": the glob check reads "b/x", the splitter reads "x".
    # A path the glob check did not see must never be written.
    patch = "--- a/tests/test_a.py\n+++  b/setup.py\n@@ -1 +1 @@\n-old\n+new\n"
    result = await _open(monkeypatch, patch)
    assert "pr_url" not in result and "refused" in result["error"]
    assert _content_urls() == []


async def test_each_path_segment_is_url_quoted(monkeypatch):
    path = "tests/a b/test_x#1?.py"
    result = await _open(monkeypatch, _diff(path))
    assert result["pr_url"] == "https://github.com/o/r/pull/9"
    urls = _content_urls()
    assert urls == [
        "https://api.github.com/repos/o/r/contents/tests/a%20b/test_x%231%3F.py",
    ] * 2  # one GET, one PUT


async def test_a_plain_test_path_still_opens_a_draft_pull_request(monkeypatch):
    result = await _open(monkeypatch, _diff("tests/unit/test_a.py"))
    assert result == {"pr_url": "https://github.com/o/r/pull/9", "pr_number": 9}
    assert _content_urls() == [
        "https://api.github.com/repos/o/r/contents/tests/unit/test_a.py",
    ] * 2


def test_the_path_rule_is_one_function_shared_by_both_layers():
    assert pipeline.unsafe_diff_path("tests/unit/test_a.py") is None
    assert pipeline.unsafe_diff_path("tests/../x") is not None
