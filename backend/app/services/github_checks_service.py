"""
GitHub Checks API integration — Tier 1 item 5.

Outbound integration that posts a check run to a commit SHA every time
TestLookup ingests a test run that has a GitHub-recognizable commit. The
check appears next to the PR's CI results and deep-links back to Run
Intelligence, giving developers a single place to see "did my PR pass
TestLookup's gate".

Design notes
------------

* **Per-project config** — the ``github_integrations`` table holds repo
  owner/name/API-base. The PAT lives in ``secret_service`` so a DB
  dump cannot recover tokens.

* **Offline-mode gate** — when ``AI_OFFLINE_MODE=true`` the service is a
  silent no-op. Air-gapped customers won't leak egress traffic if the
  feature flag gets flipped on accidentally.

* **Feature flag** — ``github_checks`` gates the whole subsystem. When
  off, every entry point returns early before touching the DB.

* **Retry + circuit breaker** — delegates to ``services/resilience``
  for exponential backoff on 5xx / network errors.

* **Never raises** from the public entry point. Ingestion pipeline
  triggers this after every run and a GitHub outage must not block
  test-case persistence. Errors land in ``last_error`` so Integration
  Health surfaces them instead.

* **Per-test annotations + flaky-aware conclusion (PMF US-4.2)** — the
  check run carries up to 50 ``output.annotations`` (newly-failed first,
  then by cluster size; locations best-effort parsed from stack traces),
  the newly-failed/known-flaky/fixed triad in the summary, and concludes
  ``neutral`` instead of ``failure`` when EVERY failure is known-flaky /
  quarantined. See the "Per-test annotations" section below.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import GitHubIntegration, Project, TestRun, User
from app.services.run_compare_service import (
    _classify,
    _load_test_rows,
    _status_bucket,
)

logger = structlog.get_logger("services.github_checks")

# ``secret_service`` scope under which we store the PAT. Consumers must
# use the exact string — it becomes part of the encryption key namespace.
SECRET_SCOPE = "github_integration"


def _secret_key(project_id: uuid.UUID | str) -> str:
    return f"project:{project_id}:pat"


# Commit-SHA recognizer. Rejects short SHAs because the Checks API
# requires a full 40-char SHA on the target commit.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


async def _ssrf_block_reason(url: str) -> Optional[str]:
    """Return a reason string if ``url``'s host resolves to a loopback,
    link-local, or unspecified address — else ``None``.

    ``api_base_url`` is QA_LEAD-configurable and we send the project's PAT to
    it (and surface the response body from ``test_connection``), so an
    unguarded base is an SSRF read primitive: a QA_LEAD could point it at the
    cloud metadata endpoint (169.254.169.254 → IAM creds) or localhost and use
    ``repo_owner``/``repo_name`` path-traversal to hit arbitrary internal
    paths. We block exactly the loopback + link-local + unspecified ranges —
    those are never a valid GitHub / GitHub-Enterprise base — while
    INTENTIONALLY allowing RFC1918 private ranges so a self-hosted GHE on a
    private network still works. A non-resolving host is allowed (don't block
    initial setup / air-gapped DNS); the check runs at egress time so it also
    defends against DNS rebinding and pre-existing rows.
    """
    host = (urlparse(url).hostname or "").strip()
    if not host:
        return "invalid_url"
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except Exception:
        return None  # non-resolving → allow (setup-friendly, no SSRF reach)
    for info in infos:
        raw = info[4][0]
        try:
            addr = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            continue
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            return f"blocked_target:{raw}"
    return None


class GitHubChecksDisabledError(RuntimeError):
    """Raised by sync helpers when the feature flag is off."""


# ── Feature-flag + offline gate ────────────────────────────────────────────


async def _post_allowed(db: Optional[AsyncSession] = None) -> bool:
    """Compound gate: feature flag ON and offline-mode OFF.

    ``AI_OFFLINE_MODE`` is the hard kill switch for every outbound
    integration — we respect it regardless of feature flag state so
    air-gapped customers don't accidentally egress traffic when an
    admin toggles ``github_checks`` on.
    """
    if settings.AI_OFFLINE_MODE:
        return False
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("github_checks", db=db)
    except Exception as exc:
        logger.debug("github_checks flag check failed", error=str(exc))
        return False


# ── Config CRUD ────────────────────────────────────────────────────────────


async def get_integration(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[GitHubIntegration]:
    result = await db.execute(
        select(GitHubIntegration).where(GitHubIntegration.project_id == project_id)
    )
    return result.scalar_one_or_none()


async def upsert_integration(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: User,
    enabled: bool,
    repo_owner: str,
    repo_name: str,
    api_base_url: str,
    pat: Optional[str],
    pr_comment_mode: Optional[str] = None,
) -> GitHubIntegration:
    """Create or update a project's GitHub integration.

    ``pat`` semantics:

    * ``None``   — leave the existing secret alone.
    * ``""``     — clear the stored secret + flip ``has_pat=False``.
    * any value  — upsert the secret + flip ``has_pat=True``.

    ``pr_comment_mode`` (PMF US-4.1): ``off`` | ``failures_only`` |
    ``always``. ``None`` leaves the stored value alone.
    """
    row = await get_integration(db, project_id)
    if row is None:
        row = GitHubIntegration(
            project_id=project_id,
            repo_owner=repo_owner,
            repo_name=repo_name,
            api_base_url=api_base_url.rstrip("/"),
            enabled=enabled,
            updated_by_user_id=actor.id,
        )
        db.add(row)
    else:
        row.repo_owner = repo_owner
        row.repo_name = repo_name
        row.api_base_url = api_base_url.rstrip("/")
        row.enabled = enabled
        row.updated_by_user_id = actor.id
        row.updated_at = datetime.now(timezone.utc)

    if pr_comment_mode is not None:
        row.pr_comment_mode = pr_comment_mode

    # Token handling — delegated to secret_service so we don't store the
    # plaintext on the DB row.
    if pat is not None:
        from app.services import secret_service
        if pat == "":
            # Explicit clear — mark has_pat False and (best-effort) blank
            # the secret value. secret_service doesn't have a delete_secret
            # helper today so we overwrite with empty and flip the flag.
            await secret_service.store_secret(
                db, SECRET_SCOPE, _secret_key(project_id), "", actor.id,
            )
            row.has_pat = False
        else:
            await secret_service.store_secret(
                db, SECRET_SCOPE, _secret_key(project_id), pat, actor.id,
            )
            row.has_pat = True

    await db.flush()

    # Audit — staged under the router session (Phase E-1). Router's
    # get_db owns the single commit so integration row, secret row, and
    # audit entry land in one transaction.
    try:
        from app.models.postgres import SettingsAuditLog
        entry = SettingsAuditLog(
            setting_key=f"github_integration:{project_id}",
            action="update",
            actor_id=actor.id,
            actor_name=getattr(actor, "username", None) or getattr(actor, "email", None),
            changed_fields=["repo_owner", "repo_name", "api_base_url", "enabled"]
            + (["pat"] if pat is not None else [])
            + (["pr_comment_mode"] if pr_comment_mode is not None else []),
        )
        db.add(entry)
    except Exception as exc:
        logger.warning("github integration audit stage failed", error=str(exc))

    return row


# ── Connection test ────────────────────────────────────────────────────────


async def test_connection(
    db: AsyncSession, project_id: uuid.UUID,
) -> dict[str, Any]:
    """Probe the configured GitHub repo to confirm the PAT works.

    Runs even when the ``github_checks`` feature flag is off so admins
    can set things up before enabling. Still respects ``AI_OFFLINE_MODE``
    as a hard gate.
    """
    if settings.AI_OFFLINE_MODE:
        return {
            "success": False,
            "status_code": None,
            "message": "AI_OFFLINE_MODE is enabled — outbound calls are disabled",
            "repo_html_url": None,
        }

    row = await get_integration(db, project_id)
    if row is None:
        return {
            "success": False,
            "status_code": None,
            "message": "No GitHub integration configured for this project",
            "repo_html_url": None,
        }

    from app.services import secret_service
    pat = await secret_service.read_secret(db, SECRET_SCOPE, _secret_key(project_id))
    if not pat:
        return {
            "success": False,
            "status_code": None,
            "message": "No PAT stored for this integration",
            "repo_html_url": None,
        }

    url = f"{row.api_base_url.rstrip('/')}/repos/{row.repo_owner}/{row.repo_name}"

    block = await _ssrf_block_reason(url)
    if block:
        return {
            "success": False,
            "status_code": None,
            "message": f"Target host is not allowed ({block})",
            "repo_html_url": None,
        }

    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "TestLookup/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:
        return {
            "success": False,
            "status_code": None,
            "message": f"Network error: {exc}",
            "repo_html_url": None,
        }

    if resp.status_code == 200:
        body = resp.json() if resp.content else {}
        return {
            "success": True,
            "status_code": 200,
            "message": f"OK — repo '{body.get('full_name', '?')}' reachable",
            "repo_html_url": body.get("html_url"),
        }
    return {
        "success": False,
        "status_code": resp.status_code,
        "message": f"GitHub returned {resp.status_code}: {resp.text[:200]}",
        "repo_html_url": None,
    }


# ── Per-test annotations + flaky-aware enrichment (PMF US-4.2) ─────────────
#
# The Checks API accepts up to 50 ``output.annotations`` per create/update
# request. We attach annotations for the run's failures, prioritized:
# newly-failed (vs the SAME baseline the PR summary comment uses) first,
# then the remaining failures ordered by cluster size. "Cluster size" here
# is the count of failures sharing the same first error-message line — the
# check posts at finalize time, BEFORE the AI analysis Celery task runs, so
# ``failure_clusters`` rows don't exist yet; the message-line grouping is a
# deterministic proxy available at post time.
#
# GitHub annotations require a repo-relative ``path`` + ``start_line``. We
# derive them best-effort from the stack trace (``locate_in_trace``); tests
# whose location can't be derived honestly are listed in the check's
# ``output.text`` markdown instead of being pinned to a fake path.
#
# Enablement: this enrichment is unconditional within the gates the checks
# service already has (``github_checks`` feature flag + per-project
# integration ``enabled`` + ``AI_OFFLINE_MODE``). The feature-flag service
# resolves unknown keys to False (no code-default mechanism), so a separate
# flag would have required a seed migration — deliberately not added.

# GitHub caps output.annotations at 50 per request. We send the first 50
# and note the overflow in output.text — multi-request pagination
# (PATCH-ing the check run with subsequent batches) is a follow-up.
_MAX_ANNOTATIONS = 50

# Bounds for annotation messages / output-text lists.
_ANNOTATION_MESSAGE_MAX_LINES = 5
_ANNOTATION_MESSAGE_CAP = 800
_ANNOTATION_TITLE_CAP = 255  # GitHub rejects longer annotation titles
_NON_LOCATABLE_CAP = 15
_TEXT_MESSAGE_CAP = 120

# Python traceback frame: File "tests/test_x.py", line 42
_PY_FRAME_RE = re.compile(r'File "([^"\n]+)", line (\d+)')

# JS/TS stack frame: "at fn (src/x.test.ts:12:34)" / "at src/x.js:12:34".
# Extension-anchored so it never fires inside Python or Java traces.
_JS_FRAME_RE = re.compile(
    r"(?:\(|\bat\s+)"
    r"((?:webpack://)?[A-Za-z0-9_@$./\\-]+"
    r"\.(?:jsx?|tsx?|mjs|cjs)):(\d+)(?::\d+)?"
)

# Frames that live outside the repo — never a valid annotation target.
_VENDOR_MARKERS = (
    "site-packages",
    "dist-packages",
    "node_modules",
    "/usr/lib",
    "<frozen",
    "importlib._bootstrap",
)


def _normalize_repo_path(raw: str) -> Optional[str]:
    """Best-effort repo-relative path from a stack-trace frame.

    Returns ``None`` for anything we can't honestly claim is relative to
    the repo root: absolute paths (POSIX or drive-letter), URLs, ``..``
    traversal, and vendor/runtime frames. Backslashes normalize to ``/``
    (Windows CI traces), ``webpack://`` bundler prefixes are stripped.
    """
    path = (raw or "").strip().replace("\\", "/")
    if path.startswith("webpack://"):
        path = path[len("webpack://"):].lstrip("/")
    if path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path) or "://" in path:
        return None
    lowered = path.lower()
    if any(marker in lowered for marker in _VENDOR_MARKERS):
        return None
    if any(part == ".." for part in path.split("/")):
        return None
    return path


def locate_in_trace(text: Optional[str]) -> Optional[tuple[str, int]]:
    """Extract a ``(repo_relative_path, line)`` failure location from a
    stack trace, or ``None`` when no honest location is derivable.

    Supported shapes:

    * **Python** tracebacks — "most recent call last", so the LAST
      repo-relative ``File "...", line N`` frame is the failure site.
      Vendor frames (site-packages etc.) after it are skipped.
    * **JS/TS** stacks — innermost frame comes FIRST, so the first
      repo-relative ``at ... (path.ts:12:34)`` frame wins;
      ``node_modules`` and ``node:internal`` frames are skipped.
    * **Java** surefire frames (``at pkg.Cls.m(Cls.java:42)``) carry a
      bare file name, NOT a repo-relative path (the ``src/test/java``
      prefix is unknowable) — deliberately unlocated rather than guessed.
    """
    if not text:
        return None
    py_frames = [(m.group(1), int(m.group(2))) for m in _PY_FRAME_RE.finditer(text)]
    for raw, line in reversed(py_frames):
        path = _normalize_repo_path(raw)
        if path and line > 0:
            return path, line
    for m in _JS_FRAME_RE.finditer(text):
        path = _normalize_repo_path(m.group(1))
        line = int(m.group(2))
        if path and line > 0:
            return path, line
    return None


def _locate_failure(tc: Any) -> Optional[tuple[str, int]]:
    """Locate a failing test: full stack trace first, then the (often
    trace-bearing) error message."""
    return (
        locate_in_trace(getattr(tc, "stack_trace", None))
        or locate_in_trace(getattr(tc, "error_message", None))
    )


def _first_line(message: Optional[str]) -> str:
    """First non-empty line, backtick-safe, capped — for output-text rows.

    Local twin of ``github_pr_comment_service._first_message_line``: that
    module imports THIS one at load time, so importing it back here would
    be a circular import.
    """
    stripped = (message or "").strip()
    if not stripped:
        return ""
    line = stripped.splitlines()[0].replace("`", "'").strip()
    if len(line) > _TEXT_MESSAGE_CAP:
        return line[:_TEXT_MESSAGE_CAP] + "…"
    return line


def _annotation_message(tc: Any) -> str:
    """First lines of the failure message, bounded — GitHub requires a
    non-empty ``message`` on every annotation."""
    raw = (getattr(tc, "error_message", None) or "").strip()
    if not raw:
        return "Test failed (no failure message captured)."
    lines = [ln.rstrip() for ln in raw.splitlines() if ln.strip()]
    msg = "\n".join(lines[:_ANNOTATION_MESSAGE_MAX_LINES])
    if len(msg) > _ANNOTATION_MESSAGE_CAP:
        msg = msg[:_ANNOTATION_MESSAGE_CAP] + "…"
    return msg


@dataclass
class _CheckEnrichment:
    """The run's failures partitioned with the same semantics as the PR
    summary comment (``github_pr_comment_service._partition_tests``) but
    keeping the ``(fingerprint, TestCase)`` pairs — annotations need the
    stack trace and still-failing rows, which the comment partition
    discards."""
    newly_failed: list[tuple[str, Any]] = field(default_factory=list)
    still_failing: list[tuple[str, Any]] = field(default_factory=list)
    known_flaky: list[tuple[str, Any]] = field(default_factory=list)
    fixed_count: int = 0
    has_baseline: bool = False
    # AI-4: {str(test_case_id): display label} for failures whose kind
    # confidence met the display floor — empty entries mean no label.
    kind_labels: dict[str, str] = field(default_factory=dict)

    @property
    def all_failures_flaky(self) -> bool:
        """True when every failing row is known-flaky/quarantined (and at
        least one failing row exists) — the neutral-conclusion condition."""
        return bool(self.known_flaky) and not self.newly_failed and not self.still_failing


def _partition_for_check(
    right_tests: dict[str, Any],
    left_tests: dict[str, Any],
    flaky_fps: set[str],
    *,
    has_baseline: bool,
) -> _CheckEnrichment:
    """Mirror of the PR-comment partition semantics: a known-flaky failure
    lands ONLY in the flaky bucket; without a baseline every non-flaky
    failure reads as newly failed."""
    enrich = _CheckEnrichment(has_baseline=has_baseline)
    for fp, tc in sorted(right_tests.items()):
        if _status_bucket(tc.status) not in ("failed", "broken"):
            continue
        if fp in flaky_fps:
            enrich.known_flaky.append((fp, tc))
            continue
        if not has_baseline:
            enrich.newly_failed.append((fp, tc))
            continue
        left_tc = left_tests.get(fp)
        cls = _classify(left_tc.status if left_tc else None, tc.status, None, None)
        if cls == "still_failing":
            enrich.still_failing.append((fp, tc))
        else:
            enrich.newly_failed.append((fp, tc))

    if has_baseline:
        for fp, left_tc in left_tests.items():
            if _status_bucket(left_tc.status) not in ("failed", "broken"):
                continue
            right_tc = right_tests.get(fp)
            if right_tc is not None and _status_bucket(right_tc.status) == "passed":
                enrich.fixed_count += 1
    return enrich


def _cluster_key(tc: Any) -> str:
    """Failures sharing the same first error-message line cluster together;
    message-less failures stay singletons instead of clumping."""
    msg = (getattr(tc, "error_message", None) or "").strip()
    first = msg.splitlines()[0].strip().lower() if msg else ""
    return first or f"__solo__:{getattr(tc, 'test_fingerprint', id(tc))}"


def _build_annotations(
    enrich: _CheckEnrichment,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Build the annotation payloads.

    Returns ``(annotations, non_locatable_rows, omitted_locatable_count)``.
    Priority: newly-failed → still-failing → known-flaky, each group
    ordered by cluster size (desc) then test name. Known-flaky annotate at
    ``warning`` level, everything else at ``failure``. Rows with no
    derivable location go to ``non_locatable_rows`` (rendered in
    ``output.text``); locatable rows beyond the 50-cap count as omitted.
    """
    all_failing = enrich.newly_failed + enrich.still_failing + enrich.known_flaky
    counts = Counter(_cluster_key(tc) for _, tc in all_failing)

    def _ordered(rows: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
        return sorted(
            rows,
            key=lambda pair: (
                -counts[_cluster_key(pair[1])],
                str(getattr(pair[1], "test_name", "") or pair[0]),
            ),
        )

    prioritized: list[tuple[str, Any, str]] = (
        [(fp, tc, "failure") for fp, tc in _ordered(enrich.newly_failed)]
        + [(fp, tc, "failure") for fp, tc in _ordered(enrich.still_failing)]
        + [(fp, tc, "warning") for fp, tc in _ordered(enrich.known_flaky)]
    )

    annotations: list[dict[str, Any]] = []
    non_locatable: list[dict[str, Any]] = []
    omitted = 0
    for fp, tc, level in prioritized:
        # AI-4: kind label only when the per-failure kind confidence met the
        # display floor (kind_labels_for_test_cases applied it) — otherwise
        # the annotation renders exactly as before.
        kind_label = enrich.kind_labels.get(str(getattr(tc, "id", "") or ""))
        loc = _locate_failure(tc)
        if loc is None:
            non_locatable.append({
                "name": str(getattr(tc, "test_name", None) or fp),
                "message": _first_line(getattr(tc, "error_message", None)),
                "flaky": level == "warning",
                "kind": kind_label,
            })
            continue
        if len(annotations) >= _MAX_ANNOTATIONS:
            omitted += 1
            continue
        path, line = loc
        message = _annotation_message(tc)
        if kind_label:
            message = f"{message}\nKind: {kind_label}"
        annotations.append({
            "path": path,
            "start_line": line,
            "end_line": line,
            "annotation_level": level,
            "title": str(getattr(tc, "test_name", None) or fp)[:_ANNOTATION_TITLE_CAP],
            "message": message,
        })
    return annotations, non_locatable, omitted


async def _gather_enrichment(
    db: AsyncSession, run: TestRun,
) -> Optional[_CheckEnrichment]:
    """Load per-test rows + baseline + flaky set and partition.

    Reuses the PR-comment service's baseline selection and known-flaky
    fingerprint set (active quarantines ∪ flaky-coach cache) so the check
    run and the PR comment NEVER disagree about what's newly failed or
    flaky. Returns ``None`` when the run has no per-test rows (live-stream
    buffer eviction etc.) — the caller then behaves exactly as before
    US-4.2 (aggregate-only summary, no neutral override).
    """
    # Lazy import: github_pr_comment_service imports this module at load
    # time, so a module-level import back would be circular.
    from app.services.github_pr_comment_service import (
        _flaky_fingerprints,
        _select_baseline,
    )

    right_tests = await _load_test_rows(db, run.id)
    if not right_tests:
        return None
    baseline = await _select_baseline(db, run)
    left_tests = (
        await _load_test_rows(db, baseline.id) if baseline is not None else {}
    )
    # Baseline with missing per-test rows can't support a newly/fixed
    # split — same no-baseline fallback the PR comment applies.
    has_baseline = baseline is not None and bool(left_tests)
    flaky_fps = await _flaky_fingerprints(db, run.project_id)
    enrich = _partition_for_check(
        right_tests, left_tests, flaky_fps, has_baseline=has_baseline,
    )
    # AI-4: display-floor-gated kind labels for the annotation payloads —
    # same source + floor as the PR comment so the two surfaces agree.
    from app.services.kind_evidence import kind_labels_for_test_cases
    failing_ids = [
        getattr(tc, "id", None)
        for _fp, tc in (enrich.newly_failed + enrich.still_failing + enrich.known_flaky)
        if getattr(tc, "id", None) is not None
    ]
    enrich.kind_labels = await kind_labels_for_test_cases(db, failing_ids)
    return enrich


# ── Check run posting ──────────────────────────────────────────────────────


def _format_check_summary(
    run: TestRun,
    project_name: Optional[str],
    enrichment: Optional[_CheckEnrichment] = None,
) -> dict[str, Any]:
    """Build the ``check_run`` payload from a TestRun row.

    The Checks API accepts a name, status, conclusion, and a
    ``output`` block with title/summary/text rendered as markdown.
    Keeps the summary short because GitHub truncates at 4 KB per field.

    ``enrichment`` (US-4.2, optional — ``None`` keeps the pre-US-4.2
    aggregate-only payload) adds per-test ``output.annotations``, the
    newly-failed/known-flaky/fixed counts, the non-locatable failure list
    in ``output.text``, and the flaky-aware conclusion: a run whose
    failures are ALL known-flaky/quarantined concludes ``neutral``
    instead of ``failure`` — mirroring the ``ci-verdict`` semantics on
    the Checks surface. Any real failure still concludes ``failure``;
    when per-test rows are unavailable we can't prove all-flaky, so the
    conclusion stays ``failure`` (fail-honest, not fail-open).
    """
    total = int(run.total_tests or 0)
    passed = int(run.passed_tests or 0)
    failed = int(run.failed_tests or 0)
    skipped = int(run.skipped_tests or 0)
    broken = int(run.broken_tests or 0)
    pass_rate = float(run.pass_rate or 0)
    failures = failed + broken

    all_flaky = enrichment is not None and enrichment.all_failures_flaky

    if failures == 0:
        conclusion = "success"
        title_icon = "✅"
    elif all_flaky:
        conclusion = "neutral"
        title_icon = "⚠️"
    else:
        conclusion = "failure"
        title_icon = "❌"

    # Deep link back to Run Intelligence. Falls back to relative path if
    # the public base URL isn't configured — the customer's GitHub will
    # render it as plain text rather than a link.
    base_url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    intel_path = f"/intelligence/{run.id}"
    deep_link = f"{base_url}{intel_path}" if base_url else intel_path

    proj_label = f"{project_name}" if project_name else "TestLookup"
    if all_flaky:
        noun = "failure" if failures == 1 else "failures"
        title = f"{title_icon} {proj_label} — {failures} {noun} — all known-flaky/quarantined"
    else:
        # Over EXECUTED tests, matching the rate's own denominator. `passed/total`
        # beside the rate read as a contradiction whenever anything was skipped:
        # "4/10 passed (44.4%)" invites 4/10 = 40%. The summary body below still
        # breaks out every bucket including the skipped ones.
        executed = max(total - skipped, 0)
        title = (
            f"{title_icon} {proj_label} — {passed}/{executed} passed "
            f"({pass_rate:.1f}%)"
        )

    summary_lines = [
        f"**Build:** {run.build_number or run.id}",
        f"**Branch:** {run.branch or '—'}",
        f"**Passed:** {passed}    **Failed:** {failed}    **Broken:** {broken}    **Skipped:** {skipped}",
        f"**Pass rate:** {pass_rate:.1f}%",
    ]

    annotations: list[dict[str, Any]] = []
    non_locatable: list[dict[str, Any]] = []
    omitted = 0
    if enrichment is not None:
        annotations, non_locatable, omitted = _build_annotations(enrichment)
        # Same triad the sticky PR comment reports — one vocabulary.
        if enrichment.has_baseline:
            summary_lines.append(
                f"**Newly failed:** {len(enrichment.newly_failed)}    "
                f"**Known flaky:** {len(enrichment.known_flaky)}    "
                f"**Fixed:** {enrichment.fixed_count}"
            )
        else:
            failing = len(enrichment.newly_failed) + len(enrichment.still_failing)
            summary_lines.append(
                f"**Failing:** {failing}    "
                f"**Known flaky:** {len(enrichment.known_flaky)}    "
                "_(no baseline run — newly-failed vs fixed unavailable)_"
            )
        if all_flaky:
            summary_lines.append(
                "_All failures are known-flaky/quarantined — "
                "likely not caused by this change._"
            )

    summary_lines.extend(["", f"[→ Open Run Intelligence]({deep_link})"])

    text_lines: list[str] = []
    if omitted > 0:
        text_lines.extend([
            f"_+{omitted} more locatable failure annotations omitted — "
            "GitHub caps annotations at 50 per request._",
            "",
        ])
    if non_locatable:
        text_lines.extend([
            f"### Failures without a source location ({len(non_locatable)})",
            "",
            "_No repo-relative file/line was derivable from the stack "
            "trace — listed here instead of annotated._",
            "",
        ])
        for row in non_locatable[:_NON_LOCATABLE_CAP]:
            entry = f"- `{row['name']}`"
            if row["message"]:
                entry += f" — `{row['message']}`"
            if row["flaky"]:
                entry += " _(known-flaky)_"
            if row.get("kind"):
                entry += f" · _{row['kind']}_"
            text_lines.append(entry)
        overflow = len(non_locatable) - _NON_LOCATABLE_CAP
        if overflow > 0:
            text_lines.append(f"- _+{overflow} more_")

    output: dict[str, Any] = {
        "title": title,
        "summary": "\n".join(summary_lines),
    }
    if text_lines:
        output["text"] = "\n".join(text_lines)
    if annotations:
        output["annotations"] = annotations

    return {
        "name": f"TestLookup · {proj_label}",
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": (run.end_time or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        "output": output,
        "details_url": deep_link if base_url else None,
    }


async def post_check_run_for_run(run_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """Post a check run to GitHub for a completed test run.

    Best-effort — always returns a dict describing what happened but
    never raises. When the integration is disabled / offline / missing
    config, returns ``{"skipped": "<reason>"}``.

    The caller (ingestion_pipeline.finalize_run) treats every non-None
    return value as informational and never fails the pipeline on it.
    """
    if not await _post_allowed():
        return {"skipped": "feature_flag_off_or_offline_mode"}

    async with AsyncSessionLocal() as db:
        run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
        run = run_result.scalar_one_or_none()
        if run is None:
            return {"skipped": "run_not_found"}
        if not run.commit_hash or not _SHA_RE.match(run.commit_hash):
            return {"skipped": "no_full_commit_sha"}

        integration = await get_integration(db, run.project_id)
        if integration is None or not integration.enabled:
            return {"skipped": "integration_disabled_or_missing"}

        from app.services import secret_service
        pat = await secret_service.read_secret(
            db, SECRET_SCOPE, _secret_key(run.project_id),
        )
        if not pat:
            return {"skipped": "no_pat_configured"}

        # Pull the project name for a more readable check title.
        project_name: Optional[str] = None
        try:
            proj_row = await db.execute(
                select(Project.name).where(Project.id == run.project_id)
            )
            project_name = proj_row.scalar_one_or_none()
        except Exception:
            project_name = None

        # US-4.2 enrichment — best-effort: a failure here degrades to the
        # aggregate-only check (pre-US-4.2 shape) rather than skipping.
        enrichment: Optional[_CheckEnrichment] = None
        try:
            enrichment = await _gather_enrichment(db, run)
        except Exception as exc:
            logger.debug(
                "github_checks enrichment unavailable",
                run_id=str(run_id),
                error=str(exc),
            )

        payload = _format_check_summary(run, project_name, enrichment)
        payload["head_sha"] = run.commit_hash

        url = (
            f"{integration.api_base_url.rstrip('/')}"
            f"/repos/{integration.repo_owner}/{integration.repo_name}/check-runs"
        )

        block = await _ssrf_block_reason(url)
        if block:
            # Refuse to send the PAT to a loopback/link-local target (SSRF) —
            # record it so Integration Health surfaces the misconfiguration.
            integration.last_error = f"blocked unsafe target: {block}"
            integration.last_error_at = datetime.now(timezone.utc)
            await db.commit()
            logger.warning(
                "github_checks blocked unsafe target",
                project_id=str(run.project_id),
                run_id=str(run_id),
                reason=block,
            )
            return {"skipped": "blocked_unsafe_target", "reason": block}

        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "TestLookup/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async def _do_post() -> httpx.Response:
            async with httpx.AsyncClient(timeout=15.0) as client:
                return await client.post(url, headers=headers, json=payload)

        from app.services.resilience import async_retry
        try:
            resp: httpx.Response = await async_retry(
                _do_post,
                max_retries=3,
                base_delay=1.0,
                operation_name="github_checks_post",
            )
        except Exception as exc:
            integration.last_error = f"{type(exc).__name__}: {str(exc)[:480]}"
            integration.last_error_at = datetime.now(timezone.utc)
            await db.commit()
            logger.warning(
                "github_checks post failed",
                project_id=str(run.project_id),
                run_id=str(run_id),
                error=str(exc),
            )
            return {"error": str(exc)}

        if resp.status_code in (200, 201):
            integration.last_posted_at = datetime.now(timezone.utc)
            integration.last_error = None
            integration.last_error_at = None
            await db.commit()
            logger.info(
                "github_checks posted",
                project_id=str(run.project_id),
                run_id=str(run_id),
                status_code=resp.status_code,
                commit_sha=run.commit_hash[:8],
            )
            return {
                "posted": True,
                "status_code": resp.status_code,
                "check_run_id": (resp.json() or {}).get("id") if resp.content else None,
            }

        # Non-success from GitHub — record the error so Integration Health
        # shows it and the user knows what to fix.
        integration.last_error = f"HTTP {resp.status_code}: {resp.text[:480]}"
        integration.last_error_at = datetime.now(timezone.utc)
        await db.commit()
        logger.warning(
            "github_checks rejected",
            project_id=str(run.project_id),
            run_id=str(run_id),
            status_code=resp.status_code,
        )
        return {"error": f"HTTP {resp.status_code}", "status_code": resp.status_code}
