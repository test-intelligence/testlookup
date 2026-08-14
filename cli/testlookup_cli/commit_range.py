"""Commit-range collection from local git for the TestLookup CLI (US-8.1 follow-up).

NOTE: this is a deliberate copy of ``client/commit_range.py`` — the CLI and
the Python SDK are separate installable packages with no dependency between
them, exactly as with ``ci_context.py``. Keep both copies in sync when
changing the base-resolution matrix.

Companion to ``ci_context.py``. Where that module answers *which CI job ran
this*, this one answers *which commits are in play* — the ``commit_range``
payload field that feeds commit attribution today (``run_commit_ranges``,
Epic 8) and test-impact analysis tomorrow (Epic 10).

Why local git rather than a VCS connector: the connector path needs a PAT, a
matching repo, ``AI_OFFLINE_MODE`` off, and a fully-green baseline run — so it
yields nothing on air-gapped installs. ``git`` is present in CI by definition
and needs no network, no token, and no green baseline.

Base-ref resolution (first tier that produces a commit wins):

  1. Explicit caller        — SDK ``commit_range_base=`` / CLI ``--commit-range-base``
  2. ``TESTLOOKUP_COMMIT_RANGE_BASE`` env
  3. Config-file override   — ``ci.commit_range_base``
  4. CI-provided diff base  — per-provider, see ``detect_ci_base_ref``
  5. Local git fallback     — ``merge-base`` vs the default branch, else ``HEAD^``

Tiers 1–3 are user-supplied and authoritative: if a user-supplied base does
not resolve we emit NOTHING rather than quietly substituting a guess. Tiers
4–5 are inferred, so an unresolvable value falls through to the next tier.
When nothing resolves we send nothing — an absent range is honest, a wrong
one poisons the model.

Shallow clones (``--depth 1``, the CI default) usually lack the base commit.
We detect that with ``git rev-parse --is-shallow-repository`` and degrade to
a head-only range (the one commit we can prove is in history) rather than
fabricating one. Never a synthesised base.

Collection is best-effort by construction: every git invocation is wrapped,
any failure omits the range rather than raising. Nothing in this module can
fail a test run.

Best-effort is not the same as silent, though. Every git invocation lands in
one of three states that must NOT be collapsed together:

  * ``GIT_OK``          — git ran and answered
  * ``GIT_REFUSED``     — git ran and answered "no" (the ref does not exist)
  * ``GIT_UNAVAILABLE`` — git never answered (missing binary, timeout, signal)

Collapsing the last two into a bare ``None`` is what once made a transient
git failure in CI indistinguishable from "the base you named does not exist":
both arrived as ``None``, the collector took the do-not-guess branch, and the
absent range had no recorded cause. :func:`diagnose_commit_range` returns the
range *and* why it is what it is, and a git invocation that stops answering
*after* git has demonstrably worked is logged at WARNING rather than DEBUG —
that combination is an anomaly, not a normal empty result.

Security: git is invoked with argv LISTS only — never a shell, never string
interpolation of untrusted values into a command line. Ref values sourced
from the environment are additionally shape-validated (``_safe_ref``) so they
cannot smuggle a leading ``-`` and be reparsed by git as an option.

Zero new runtime dependencies — stdlib ``subprocess`` against the git CLI.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Any, Mapping, NamedTuple, Optional, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "collect_commit_range",
    "diagnose_commit_range",
    "resolve_commit_range",
    "detect_ci_base_ref",
    "is_collection_enabled",
    "CommitRangeOutcome",
]

# ── Caps ──────────────────────────────────────────────────────────────────────
# Mirrored from backend/app/services/commit_attribution_service.py and
# backend/app/models/schemas.py (SuppliedCommit / _COMMIT_RANGE_MAX). Enforced
# client-side so the payload is never silently truncated server-side.
MAX_COMMITS = 100
FILES_PER_COMMIT_CAP = 500
FILE_PATH_CAP = 512
MESSAGE_CAP = 2000
SHA_CAP = 64
AUTHOR_CAP = 255
COMMITTED_AT_CAP = 40

# ── Env / config keys ─────────────────────────────────────────────────────────
# Opt-out + explicit base, following the TESTLOOKUP_* convention ci_context.py
# and ConfigLoader already use.
ENV_ENABLED = "TESTLOOKUP_COMMIT_RANGE"
ENV_BASE = "TESTLOOKUP_COMMIT_RANGE_BASE"
CONFIG_ENABLED_KEY = "collect_commit_range"
CONFIG_BASE_KEY = "commit_range_base"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSEY = {"0", "false", "no", "off"}

# git subprocess timeouts (seconds). Generous enough for a cold NTFS repo,
# short enough that a wedged git can't stall a CI job.
_TIMEOUT_QUICK = 10.0
_TIMEOUT_LOG = 20.0

# Cap on the CI event-payload file we parse (GitHub writes the full webhook
# body there; a pathological one shouldn't be slurped into memory).
_EVENT_PAYLOAD_MAX_BYTES = 8 * 1024 * 1024

# Record/field separators for the single `git log` call. \x1e (RS) prefixes
# every commit header, \x1f (US) separates its fields — neither can appear in
# a sha, an author name, an ISO date, or a one-line subject.
_RS = "\x1e"
_US = "\x1f"
_LOG_FORMAT = f"format:{_RS}%H{_US}%an{_US}%aI{_US}%s"

# Conservative ref shape. Deliberately excludes a leading "-" (git option
# injection) and anything outside the git-refname character set.
_REF_RE = re.compile(r"^[A-Za-z0-9._/@^~{}+:=-]{1,255}$")


# ── Small pure helpers ────────────────────────────────────────────────────────

def _flag(value: Any) -> Optional[bool]:
    """Parse a tri-state boolean: True / False / None (unset or unparseable)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in _TRUTHY:
        return True
    if s in _FALSEY:
        return False
    return None


def _safe_ref(value: Any) -> Optional[str]:
    """Validate a ref/sha sourced from the environment.

    Returns the cleaned ref, or ``None`` when it is empty, option-shaped
    (leading ``-``), outside the ref character set, or the all-zero sentinel
    several CI systems use for "no previous commit"
    (``CI_COMMIT_BEFORE_SHA``, GitHub push-event ``before``).
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.startswith("-"):
        return None
    if not _REF_RE.match(s):
        return None
    if set(s) == {"0"}:  # 0000…0 sentinel
        return None
    return s


def _clip(value: Any, cap: int) -> Optional[str]:
    """Strip + truncate a field, or ``None`` when it carries nothing."""
    if value is None:
        return None
    s = str(value).strip()
    return s[:cap] if s else None


def _candidate_refs(ref: str) -> list[str]:
    """Expand a ref into the forms a CI checkout might actually have.

    CI checkouts are frequently detached with only remote-tracking refs
    present, and Azure hands out fully-qualified ``refs/heads/x``. Try the
    literal ref first (a raw sha resolves immediately), then the remote forms.
    """
    out = [ref]
    short = ref
    for prefix in ("refs/heads/", "refs/remotes/origin/", "refs/"):
        if short.startswith(prefix):
            short = short[len(prefix):]
            break
    for cand in (f"origin/{short}", f"refs/remotes/origin/{short}", short):
        if cand not in out:
            out.append(cand)
    return out


# ── git invocation (argv lists only — never a shell) ──────────────────────────

# The three states a git invocation can end in. Keeping REFUSED and
# UNAVAILABLE apart is the whole point: "git says that ref does not exist" is
# a fact we can act on, "git never answered" is an outage we must not dress up
# as a fact about the user's repository.
GIT_OK = "ok"
GIT_REFUSED = "refused"
GIT_UNAVAILABLE = "unavailable"


class _GitResult(NamedTuple):
    """One git invocation's outcome, with enough detail to explain itself."""

    status: str
    out: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == GIT_OK


def _run_git(
    args: Sequence[str],
    *,
    repo_path: Optional[str] = None,
    timeout: float = _TIMEOUT_QUICK,
) -> _GitResult:
    """Run one git command as an argv LIST and classify the outcome.

    Never raises. ``args`` is appended to a fixed prefix; no element is ever
    built by interpolating a value into a larger command string, and
    ``shell=True`` is never used. This mirrors the argv discipline in the
    Fixer's ``backend/app/agents/fixer/runners.py``.

    Exit-code classification: 1 is git's conventional "no" and is REFUSED;
    anything higher is a ``fatal:`` git could not complete, and a negative
    code means the process was killed by a signal — neither is an answer
    about the repository, so both are UNAVAILABLE.
    """
    label = " ".join(str(a) for a in list(args)[:2])

    argv: list[str] = ["git"]
    if repo_path:
        # A caller-controlled path, but still refuse an option-shaped one.
        if str(repo_path).startswith("-"):
            return _GitResult(GIT_REFUSED, "", f"option-shaped repo path {repo_path!r}")
        argv += ["-C", str(repo_path)]
    # quotePath=false keeps UTF-8 paths literal; git still C-quotes any path
    # containing a control character, so a path can never span output lines.
    argv += ["-c", "core.quotePath=false", "--no-pager"]
    argv += [str(a) for a in args]

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"   # never block on credentials
    env["GIT_OPTIONAL_LOCKS"] = "0"    # read-only: don't touch the index

    try:
        proc = subprocess.run(  # noqa: S603 — argv list, shell=False, fixed argv[0]
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _GitResult(
            GIT_UNAVAILABLE, "", f"git {label} timed out after {timeout}s"
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # OSError covers the interesting transients too: a missing binary, and
        # EAGAIN/ENOMEM from fork() on a machine that has run out of headroom.
        return _GitResult(
            GIT_UNAVAILABLE, "", f"git {label} could not be run ({exc!r})"
        )

    stderr_lines = (proc.stderr or "").strip().splitlines()
    tail = stderr_lines[-1][:200] if stderr_lines else ""

    if proc.returncode == 0:
        return _GitResult(
            GIT_OK, (proc.stdout or "").strip("\n").strip(), f"git {label} ok"
        )
    if proc.returncode < 0:
        return _GitResult(
            GIT_UNAVAILABLE, "",
            f"git {label} killed by signal {-proc.returncode}: {tail}",
        )
    if proc.returncode == 1:
        return _GitResult(GIT_REFUSED, "", f"git {label} exited 1: {tail}")
    return _GitResult(
        GIT_UNAVAILABLE, "", f"git {label} exited {proc.returncode}: {tail}"
    )


def _git(
    args: Sequence[str],
    *,
    repo_path: Optional[str] = None,
    timeout: float = _TIMEOUT_QUICK,
) -> Optional[str]:
    """:func:`_run_git` for callers that only need "did I get a value".

    Used by the *inferred* base tiers (default branch, merge-base), where a
    refusal and an outage both correctly mean "fall through to the next tier".
    Anywhere the difference changes what we report, call ``_run_git``.
    """
    result = _run_git(args, repo_path=repo_path, timeout=timeout)
    return result.out if result.ok else None


def _rev_parse_result(ref: str, *, repo_path: Optional[str]) -> _GitResult:
    """Resolve a ref to a full commit sha, keeping the outcome's three states.

    ``--verify --quiet`` exits 1 for a ref that does not exist, so a REFUSED
    result here really does mean "no such commit". An empty stdout on a zero
    exit is not something git does, but it would be indistinguishable from a
    resolved sha downstream, so it is normalised to REFUSED.
    """
    result = _run_git(
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        repo_path=repo_path,
    )
    if result.ok and not result.out:
        return _GitResult(GIT_REFUSED, "", f"rev-parse {ref}: empty output")
    return result


def _rev_parse(ref: str, *, repo_path: Optional[str]) -> Optional[str]:
    """Resolve a ref to a full commit sha, or ``None`` when it doesn't exist."""
    result = _rev_parse_result(ref, repo_path=repo_path)
    return result.out if result.ok else None


def _is_shallow(repo_path: Optional[str]) -> bool:
    """True when the working copy is a shallow clone (``--depth N``)."""
    return _git(["rev-parse", "--is-shallow-repository"], repo_path=repo_path) == "true"


def _resolve_ref(ref: Optional[str], *, repo_path: Optional[str]) -> _GitResult:
    """Resolve a raw CI/user ref through its candidate forms.

    REFUSED only when git answered "no" for every candidate. If any candidate
    probe went UNAVAILABLE we do not know whether the ref exists — reporting
    that as "unresolvable" would blame the user's ref for our own outage, and
    is exactly the conflation this module used to make.
    """
    if not ref:
        return _GitResult(GIT_REFUSED, "", "no ref to resolve")
    unavailable: Optional[_GitResult] = None
    for cand in _candidate_refs(ref):
        result = _rev_parse_result(cand, repo_path=repo_path)
        if result.ok:
            return result
        if result.status == GIT_UNAVAILABLE and unavailable is None:
            unavailable = result
    if unavailable is not None:
        return unavailable
    return _GitResult(GIT_REFUSED, "", f"{ref!r} matched no known ref form")


def _default_branch_ref(repo_path: Optional[str]) -> Optional[str]:
    """Best-effort default branch, preferring what the remote actually says."""
    head = _git(
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        repo_path=repo_path,
    )
    if head:
        return head
    for cand in ("origin/main", "origin/master", "main", "master"):
        if _rev_parse(cand, repo_path=repo_path):
            return cand
    return None


def _fallback_base(head_sha: str, *, repo_path: Optional[str]) -> Optional[str]:
    """Local-git base: merge-base against the default branch, else ``HEAD^``."""
    default_ref = _default_branch_ref(repo_path)
    if default_ref:
        merge_base = _git(
            ["merge-base", default_ref, head_sha], repo_path=repo_path
        )
        if merge_base and merge_base != head_sha:
            return merge_base
    # Already on (or merged into) the default branch → the previous commit on
    # this branch is the honest range.
    parent = _rev_parse(f"{head_sha}^", repo_path=repo_path)
    if parent and parent != head_sha:
        return parent
    return None


# ── CI-provided diff base ─────────────────────────────────────────────────────

def _read_event_payload(path: Optional[str]) -> dict[str, Any]:
    """Parse a CI event-payload JSON file; ``{}`` on anything unexpected."""
    if not path:
        return {}
    try:
        if os.path.getsize(path) > _EVENT_PAYLOAD_MAX_BYTES:
            return {}
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            payload = json.load(fh)
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("commit_range: event payload unreadable (%s)", exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def detect_ci_base_ref(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Return the CI provider's diff base as a raw ref/sha, or ``None``.

    Provider matrix mirrors ``ci_context.detect_ci_context`` (first match
    wins). Every variable below is taken from the provider's own published
    reference:

      * GitHub Actions — ``GITHUB_EVENT_PATH`` payload
        (``pull_request.base.sha`` for PR events, ``before`` for push events),
        falling back to ``GITHUB_BASE_REF`` (a *branch name*, resolved via
        git; only set for ``pull_request`` / ``pull_request_target``).
      * GitLab CI — ``CI_MERGE_REQUEST_DIFF_BASE_SHA`` ("The base SHA of the
        merge request diff"), then ``CI_MERGE_REQUEST_TARGET_BRANCH_SHA``
        (empty outside merged-results pipelines), then
        ``CI_COMMIT_BEFORE_SHA`` (all-zero for MR/scheduled/first-commit
        pipelines — rejected by ``_safe_ref``), then the target branch name.
      * Jenkins — ``CHANGE_TARGET`` (Branch API: the base branch a change
        request would merge to), then git-plugin
        ``GIT_PREVIOUS_SUCCESSFUL_COMMIT`` / ``GIT_PREVIOUS_COMMIT``.
      * Azure DevOps — ``SYSTEM_PULLREQUEST_TARGETBRANCH`` (e.g.
        ``refs/heads/main``) / ``SYSTEM_PULLREQUEST_TARGETBRANCHNAME``.
        ``SYSTEM_PULLREQUEST_SOURCECOMMITID`` is the commit *being reviewed*
        (the head), so it is deliberately NOT used as a base.
      * CircleCI — **omitted on purpose.** CircleCI exposes no built-in
        *environment variable* carrying a diff base; ``pipeline.git.base_revision``
        is a pipeline value that only exists if the user maps it into the job
        environment. Rather than guess, CircleCI falls through to the local
        git fallback — users who want the pipeline value can map it to
        ``TESTLOOKUP_COMMIT_RANGE_BASE`` themselves.

    Never raises; unknown providers return ``None``.
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

    def first(*keys: str) -> Optional[str]:
        for key in keys:
            ref = _safe_ref(get(key))
            if ref:
                return ref
        return None

    if truthy("GITHUB_ACTIONS"):
        payload = _read_event_payload(get("GITHUB_EVENT_PATH"))
        event_name = get("GITHUB_EVENT_NAME")
        if event_name in ("pull_request", "pull_request_target"):
            pr = payload.get("pull_request")
            base = (pr or {}).get("base") if isinstance(pr, dict) else None
            if isinstance(base, dict):
                ref = _safe_ref(base.get("sha"))
                if ref:
                    return ref
            return first("GITHUB_BASE_REF")
        if event_name == "push":
            return _safe_ref(payload.get("before"))
        return None

    if truthy("GITLAB_CI"):
        return first(
            "CI_MERGE_REQUEST_DIFF_BASE_SHA",
            "CI_MERGE_REQUEST_TARGET_BRANCH_SHA",
            "CI_COMMIT_BEFORE_SHA",
            "CI_MERGE_REQUEST_TARGET_BRANCH_NAME",
        )

    if get("JENKINS_URL"):
        return first(
            "CHANGE_TARGET",
            "GIT_PREVIOUS_SUCCESSFUL_COMMIT",
            "GIT_PREVIOUS_COMMIT",
        )

    if truthy("TF_BUILD"):
        return first(
            "SYSTEM_PULLREQUEST_TARGETBRANCH",
            "SYSTEM_PULLREQUEST_TARGETBRANCHNAME",
        )

    # CircleCI (CIRCLECI=true) and everything else: no verified base variable.
    return None


# ── Enablement ────────────────────────────────────────────────────────────────

def is_collection_enabled(
    env: Optional[Mapping[str, str]] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
    explicit: Optional[bool] = None,
) -> bool:
    """Whether commit-range collection should run. Defaults to enabled.

    Precedence mirrors ``resolve_ci_context``: default < config-file override
    (``ci.collect_commit_range``) < ``TESTLOOKUP_COMMIT_RANGE`` env < explicit
    caller (``--no-commit-range`` / ``collect_commit_range=False``).
    """
    e: Mapping[str, str] = os.environ if env is None else env
    enabled = True
    if overrides:
        flag = _flag(overrides.get(CONFIG_ENABLED_KEY))
        if flag is not None:
            enabled = flag
    flag = _flag(e.get(ENV_ENABLED))
    if flag is not None:
        enabled = flag
    if explicit is not None:
        enabled = bool(explicit)
    return enabled


def _user_base(
    explicit_base: Optional[str],
    overrides: Optional[Mapping[str, Any]],
    e: Mapping[str, str],
) -> Optional[str]:
    """The strongest user-supplied base: explicit > env > config file."""
    for candidate in (
        explicit_base,
        e.get(ENV_BASE),
        (overrides or {}).get(CONFIG_BASE_KEY),
    ):
        ref = _safe_ref(candidate)
        if ref:
            return ref
    return None


# ── Commit collection ─────────────────────────────────────────────────────────

def _commit_dict(sha: str, author: str, date: str, subject: str, files: list[str]) -> dict[str, Any]:
    """Build one commit in the server's ``SuppliedCommit`` shape (capped)."""
    return {
        "sha": sha[:SHA_CAP],
        "author": _clip(author, AUTHOR_CAP),
        "message": _clip(subject, MESSAGE_CAP) or "",
        "files": [str(f)[:FILE_PATH_CAP] for f in files[:FILES_PER_COMMIT_CAP]],
        "committed_at": _clip(date, COMMITTED_AT_CAP),
    }


def _parse_log(out: str) -> list[dict[str, Any]]:
    """Parse the ``git log`` output into commits, newest-first as git emits."""
    commits: list[dict[str, Any]] = []
    for record in out.split(_RS):
        if not record.strip():
            continue
        lines = record.split("\n")
        parts = lines[0].split(_US, 3)
        if len(parts) < 4:
            continue
        sha = parts[0].strip()
        if not sha:
            continue
        files = [ln.strip() for ln in lines[1:] if ln.strip()]
        commits.append(_commit_dict(sha, parts[1], parts[2], parts[3], files))
    return commits


class _LogResult(NamedTuple):
    """``git log`` outcome: the commits plus why there might not be any."""

    status: str
    detail: str
    commits: list[dict[str, Any]]
    truncated: bool


def _log_commits(
    revs: Sequence[str], *, repo_path: Optional[str], limit: int
) -> _LogResult:
    """Collect commits for a rev spec, oldest→newest, with per-commit files.

    Asks for ``limit + 1`` so truncation is detectable, keeps the NEWEST
    ``limit`` (the likeliest culprits) and returns them oldest→newest to match
    the server's normalizer.

    A non-OK status means the list is empty because git did not deliver, which
    the caller must not confuse with a genuinely empty range.
    """
    result = _run_git(
        [
            "log",
            f"--max-count={limit + 1}",
            f"--format={_LOG_FORMAT}",
            "--name-only",
            "--no-renames",
            *revs,
        ],
        repo_path=repo_path,
        timeout=_TIMEOUT_LOG,
    )
    if not result.ok:
        return _LogResult(result.status, result.detail, [], False)
    commits = _parse_log(result.out)   # newest-first
    truncated = len(commits) > limit
    commits = commits[:limit]
    commits.reverse()                  # oldest→newest
    return _LogResult(GIT_OK, result.detail, commits, truncated)


# ── Public API ────────────────────────────────────────────────────────────────

# Why a collection ended the way it did. Stable slugs: they are what a CI
# failure message quotes, so they are part of the contract.
REASON_OK = "ok"
REASON_DEGRADED = "degraded_head_only"
REASON_DISABLED = "disabled"
REASON_NO_CHECKOUT = "not_a_git_checkout"
REASON_GIT_FAILED = "git_failed"
REASON_USER_BASE_UNRESOLVABLE = "user_base_unresolvable"
REASON_NO_BASE = "no_base_commit"
REASON_EMPTY_RANGE = "empty_range"
REASON_LOG_FAILED = "log_failed"
REASON_ERROR = "unexpected_error"

# Reasons that mean something went wrong rather than "there was nothing to
# send". Only these are worth interrupting a user's log with — everything
# else is a normal, expected empty result on somebody's machine.
_LOUD_REASONS = frozenset({REASON_GIT_FAILED, REASON_ERROR})


class CommitRangeOutcome(NamedTuple):
    """A collected range (or ``None``) together with why it is what it is.

    ``reason`` is one of the ``REASON_*`` slugs; ``detail`` is human text
    carrying git's own exit code and stderr tail when git is involved.
    """

    commit_range: Optional[dict[str, Any]]
    reason: str
    detail: str


def _outcome(
    commit_range: Optional[dict[str, Any]], reason: str, detail: str
) -> CommitRangeOutcome:
    """Log the outcome at a level that matches how surprising it is."""
    if reason in _LOUD_REASONS:
        logger.warning("commit_range: %s — %s", reason, detail)
    else:
        logger.debug("commit_range: %s — %s", reason, detail)
    return CommitRangeOutcome(commit_range, reason, detail)


def diagnose_commit_range(
    *,
    explicit_base: Optional[str] = None,
    overrides: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    repo_path: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> CommitRangeOutcome:
    """:func:`collect_commit_range`, but it tells you why it produced nothing.

    Identical behaviour and the same never-raises contract; the difference is
    that the cause survives the call instead of vanishing into a DEBUG log
    nobody has enabled. Prefer this in tests and anywhere an empty range would
    otherwise be a mystery.
    """
    e: Mapping[str, str] = os.environ if env is None else env

    if not is_collection_enabled(e, overrides=overrides, explicit=enabled):
        return _outcome(None, REASON_DISABLED, "collection disabled")

    try:
        head = _rev_parse_result("HEAD", repo_path=repo_path)
        if not head.ok:
            # Nothing has proven git usable here yet: a missing binary, a
            # plain directory and a broken checkout all land here, and none of
            # them deserves more than DEBUG on a client that must not intrude.
            # The detail carries git's own words for whoever does look.
            return _outcome(None, REASON_NO_CHECKOUT, head.detail)
        head_sha = head.out

        shallow = _is_shallow(repo_path)

        def head_only(reason: str, detail: str) -> CommitRangeOutcome:
            """Degraded mode: emit only the commit we can prove is present."""
            if not shallow:
                return _outcome(None, reason, detail)
            log = _log_commits([head_sha], repo_path=repo_path, limit=1)
            if not log.commits:
                return _outcome(
                    None,
                    REASON_GIT_FAILED if log.status == GIT_UNAVAILABLE
                    else REASON_LOG_FAILED,
                    f"head-only log produced nothing: {log.detail}",
                )
            return _outcome(
                {
                    "base_commit": None,
                    "head_commit": head_sha,
                    "commits": log.commits,
                    "base_source": "head_only",
                    "degraded": True,
                    "truncated": False,
                },
                REASON_DEGRADED,
                f"shallow clone: {detail}",
            )

        base_source = "user"
        base_ref = _user_base(explicit_base, overrides, e)
        base_sha: Optional[str] = None

        if base_ref:
            resolved = _resolve_ref(base_ref, repo_path=repo_path)
            if resolved.status == GIT_UNAVAILABLE:
                # git worked a moment ago and has now stopped answering. We do
                # NOT know that the user's base is bad, so we must not say so.
                return _outcome(
                    None, REASON_GIT_FAILED,
                    f"resolving user base {base_ref!r}: {resolved.detail}",
                )
            base_sha = resolved.out or None
            if not base_sha:
                # A user told us the base and git says it does not exist.
                # Substituting a guess would silently report a different range
                # than they asked for, so degrade (shallow) or stay silent.
                return head_only(
                    REASON_USER_BASE_UNRESOLVABLE,
                    f"user base {base_ref!r} does not resolve: {resolved.detail}",
                )
        else:
            ci_ref = detect_ci_base_ref(e)
            if ci_ref:
                # Inferred tier: a refusal and an outage both mean "fall
                # through", so the distinction genuinely does not matter here.
                base_sha = _resolve_ref(ci_ref, repo_path=repo_path).out or None
            base_source = "ci"
            if base_sha == head_sha:
                base_sha = None       # e.g. default-branch pipeline
            if not base_sha:
                base_sha = _fallback_base(head_sha, repo_path=repo_path)
                base_source = "git_fallback"

        if not base_sha or base_sha == head_sha:
            return head_only(REASON_NO_BASE, "no base commit could be determined")

        log = _log_commits(
            [f"{base_sha}..{head_sha}"], repo_path=repo_path, limit=MAX_COMMITS
        )
        if log.status != GIT_OK:
            # A refusal is routine in a shallow clone, where base isn't in the
            # grafted history. An outage is not, and is reported as one.
            return head_only(
                REASON_GIT_FAILED if log.status == GIT_UNAVAILABLE
                else REASON_LOG_FAILED,
                f"git log {base_sha[:8]}..{head_sha[:8]}: {log.detail}",
            )
        if not log.commits:
            return _outcome(
                None, REASON_EMPTY_RANGE,
                f"empty range {base_sha[:8]}..{head_sha[:8]}",
            )

        return _outcome(
            {
                "base_commit": base_sha,
                "head_commit": head_sha,
                "commits": log.commits,
                "base_source": base_source,
                "degraded": False,
                "truncated": log.truncated,
            },
            REASON_OK,
            f"{len(log.commits)} commits via {base_source}"
            f"{' (truncated)' if log.truncated else ''}",
        )
    except Exception as exc:  # noqa: BLE001 — must never fail the test run
        return _outcome(None, REASON_ERROR, f"collection failed ({exc!r})")


def collect_commit_range(
    *,
    explicit_base: Optional[str] = None,
    overrides: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    repo_path: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    """Collect the commit range for this run from local git.

    Returns ``{"base_commit", "head_commit", "commits", "base_source",
    "degraded", "truncated"}`` — ``commits`` oldest→newest in the server's
    ``SuppliedCommit`` shape — or ``None`` when collection is disabled, this
    isn't a git repo, or no base could be honestly determined.

    ``base_source`` is one of ``user`` / ``ci`` / ``git_fallback`` /
    ``head_only``; ``degraded`` is True for the shallow-clone head-only case.

    Never raises. Call :func:`diagnose_commit_range` when you need to know
    which of those ``None``\\ s you got.
    """
    return diagnose_commit_range(
        explicit_base=explicit_base,
        overrides=overrides,
        env=env,
        repo_path=repo_path,
        enabled=enabled,
    ).commit_range


def resolve_commit_range(
    *,
    explicit_base: Optional[str] = None,
    overrides: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    repo_path: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    """The wire form of :func:`collect_commit_range`.

    Returns the boundary-carrying ``{base_commit, head_commit, commits}``
    object for ``commit_range`` on ``IngestPayload`` / ``LiveSessionCreate``,
    or ``None`` to send nothing.

    It must NOT return a bare ``commits`` list. The server accepts both
    shapes, but the bare list throws the boundary away: the range row lands
    with ``base_commit = NULL`` and ``base_source = "unavailable"``, so the
    base we just resolved is discarded on the wire and the row is useless as
    Epic-10 training data. Pinned by a test.
    """
    rng = collect_commit_range(
        explicit_base=explicit_base,
        overrides=overrides,
        env=env,
        repo_path=repo_path,
        enabled=enabled,
    )
    if not rng:
        return None
    return {
        "base_commit": rng.get("base_commit"),
        "head_commit": rng.get("head_commit"),
        "commits": rng["commits"],
    }
