"""CI-context auto-detection for the TestLookup CLI (US-4.3b).

NOTE: this is a deliberate copy of ``client/ci_context.py`` — the CLI and
the Python SDK are separate installable packages with no dependency between
them, and this module is small enough that duplication beats coupling.
Keep both copies (and the JS/Java/Go SDK ports) in sync when changing the
detection matrix.

Detects the CI provider, repository, PR number, actor, and run URL from the
standard environment variables each CI system exports, so test runs land in
TestLookup already linked to their pull request and CI job.

Detection matrix (first match wins):

  1. GitHub Actions  — ``GITHUB_ACTIONS=true``
  2. GitLab CI       — ``GITLAB_CI=true``
  3. Jenkins         — ``JENKINS_URL`` set
  4. Azure DevOps    — ``TF_BUILD=True``
  5. CircleCI        — ``CIRCLECI=true``

Nothing matches → empty dict (never guess). Malformed integers → the field
is omitted (never raise). String values are defensively truncated to the
backend's length caps (``IngestPayload`` / ``LiveSessionCreate``).

Precedence when merging (``resolve_ci_context``): detection < config-file
overrides < ``TESTLOOKUP_CI_*`` env overrides < explicit caller values.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping, Optional

__all__ = ["detect_ci_context", "resolve_ci_context"]

# Backend length caps — see backend/app/models/schemas.py (IngestPayload,
# LiveSessionCreate) and backend/app/routers/ingest.py (/ingest/file form).
_CAPS = {
    "ci_provider": 30,
    "ci_repo": 300,
    "ci_actor": 120,
    "ci_run_url": 1000,
}

# Explicit user overrides — follow the TESTLOOKUP_* env convention used by
# ConfigLoader. These always beat auto-detection.
_ENV_OVERRIDES = {
    "TESTLOOKUP_CI_PROVIDER": "ci_provider",
    "TESTLOOKUP_CI_REPO": "ci_repo",
    "TESTLOOKUP_PR_NUMBER": "pr_number",
    "TESTLOOKUP_CI_ACTOR": "ci_actor",
    "TESTLOOKUP_CI_RUN_URL": "ci_run_url",
}

_CI_FIELDS = ("ci_provider", "ci_repo", "pr_number", "ci_actor", "ci_run_url")


def _to_pr_number(value: Any) -> Optional[int]:
    """Coerce a PR number to a positive int; malformed / <1 → None."""
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _clip(field: str, value: Any) -> Optional[str]:
    """Normalise a string field: strip, drop empties, truncate to caps."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    cap = _CAPS.get(field)
    return s[:cap] if cap else s


def _repo_from_git_url(git_url: Optional[str]) -> Optional[str]:
    """Derive ``org/name`` from a Jenkins GIT_URL, or None when unparseable.

    Handles both HTTPS (``https://host/org/name.git``) and SCP-style SSH
    (``git@host:org/name.git``) remotes: strip a trailing ``.git``, treat
    ``:`` like a path separator, and take the last two path segments.
    """
    if not git_url:
        return None
    s = git_url.strip()
    if s.endswith(".git"):
        s = s[: -len(".git")]
    segments = [p for p in s.replace(":", "/").split("/") if p]
    if len(segments) < 2:
        return None
    return f"{segments[-2]}/{segments[-1]}"


def detect_ci_context(env: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """Detect CI context from standard CI env vars.

    Returns a dict containing only the non-None subset of ``ci_provider``,
    ``ci_repo``, ``pr_number`` (int), ``ci_actor``, ``ci_run_url``.
    Outside CI (no provider matched) returns ``{}``. Never raises.
    """
    e: Mapping[str, str] = os.environ if env is None else env

    def get(key: str) -> Optional[str]:
        v = e.get(key)
        if v is None:
            return None
        v = str(v).strip()
        return v or None

    def truthy(key: str) -> bool:
        v = get(key)
        return v is not None and v.lower() == "true"

    provider: Optional[str] = None
    repo: Optional[str] = None
    pr: Optional[int] = None
    actor: Optional[str] = None
    run_url: Optional[str] = None

    if truthy("GITHUB_ACTIONS"):
        provider = "github_actions"
        repo = get("GITHUB_REPOSITORY")
        if get("GITHUB_EVENT_NAME") in ("pull_request", "pull_request_target"):
            m = re.match(r"^refs/pull/(\d+)/", get("GITHUB_REF") or "")
            if m:
                pr = _to_pr_number(m.group(1))
        actor = get("GITHUB_ACTOR")
        server, run_id = get("GITHUB_SERVER_URL"), get("GITHUB_RUN_ID")
        if server and repo and run_id:
            run_url = f"{server.rstrip('/')}/{repo}/actions/runs/{run_id}"
    elif truthy("GITLAB_CI"):
        provider = "gitlab_ci"
        repo = get("CI_PROJECT_PATH")
        pr = _to_pr_number(get("CI_MERGE_REQUEST_IID"))
        actor = get("GITLAB_USER_LOGIN") or get("GITLAB_USER_NAME")
        run_url = get("CI_JOB_URL") or get("CI_PIPELINE_URL")
    elif get("JENKINS_URL"):
        provider = "jenkins"
        repo = _repo_from_git_url(get("GIT_URL"))
        pr = _to_pr_number(get("CHANGE_ID"))  # multibranch PR builds
        actor = get("CHANGE_AUTHOR") or get("BUILD_USER_ID")
        run_url = get("BUILD_URL")
    elif truthy("TF_BUILD"):
        provider = "azure_devops"
        repo = get("BUILD_REPOSITORY_NAME")
        pr = _to_pr_number(get("SYSTEM_PULLREQUEST_PULLREQUESTNUMBER"))
        if pr is None:
            pr = _to_pr_number(get("SYSTEM_PULLREQUEST_PULLREQUESTID"))
        actor = get("BUILD_REQUESTEDFOR")
        coll = get("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI")
        proj = get("SYSTEM_TEAMPROJECT")
        build_id = get("BUILD_BUILDID")
        if coll and proj and build_id:
            base = coll if coll.endswith("/") else coll + "/"
            run_url = f"{base}{proj}/_build/results?buildId={build_id}"
    elif truthy("CIRCLECI"):
        provider = "circleci"
        user, name = get("CIRCLE_PROJECT_USERNAME"), get("CIRCLE_PROJECT_REPONAME")
        if user and name:
            repo = f"{user}/{name}"
        pr_url = get("CIRCLE_PULL_REQUEST")
        if pr_url:
            m = re.search(r"/(\d+)/?$", pr_url)
            if m:
                pr = _to_pr_number(m.group(1))
        actor = get("CIRCLE_USERNAME")
        run_url = get("CIRCLE_BUILD_URL")
    else:
        return {}

    out: dict[str, Any] = {}
    for field, value in (
        ("ci_provider", provider),
        ("ci_repo", repo),
        ("ci_actor", actor),
        ("ci_run_url", run_url),
    ):
        clipped = _clip(field, value)
        if clipped is not None:
            out[field] = clipped
    if pr is not None:
        out["pr_number"] = pr
    return out


def resolve_ci_context(
    explicit: Optional[Mapping[str, Any]] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Merge CI context with the standard precedence chain.

    ``detection < overrides (config file) < TESTLOOKUP_CI_* env < explicit``.
    Only non-None values participate; malformed pr_number overrides are
    ignored (detection's value, if any, survives). Returns only non-None
    keys, ready to splat into an ingest / session-create payload.
    """
    e: Mapping[str, str] = os.environ if env is None else env
    ctx = detect_ci_context(e)

    def apply(source: Mapping[str, Any]) -> None:
        for field, value in source.items():
            if field not in _CI_FIELDS or value is None:
                continue
            if field == "pr_number":
                n = _to_pr_number(value)
                if n is not None:
                    ctx[field] = n
            else:
                clipped = _clip(field, value)
                if clipped is not None:
                    ctx[field] = clipped

    if overrides:
        apply(overrides)
    apply({field: e.get(var) for var, field in _ENV_OVERRIDES.items()})
    if explicit:
        apply(explicit)
    return ctx
