"""
Sticky GitHub PR summary comment — PMF backlog US-4.1.

When a test run linked to a GitHub PR finalizes (``TestRun.pr_number`` +
``TestRun.ci_repo``, populated by SDK/CLI CI auto-detection — US-4.3),
TestLookup upserts ONE comment on that PR summarizing newly-failed /
known-flaky / fixed tests. The comment is keyed by a hidden HTML marker
(first line) so re-runs PATCH the existing comment instead of spamming
the PR timeline.

Design notes
------------

* **Same guardrails as ``github_checks_service``** — per-project
  integration row + PAT in ``secret_service``, hard ``AI_OFFLINE_MODE``
  kill switch via ``_post_allowed`` (which also honors the
  ``github_checks`` feature flag: PR comments are part of the same
  GitHub outbound subsystem), SSRF egress guard on the QA_LEAD-editable
  ``api_base_url``, errors recorded on ``last_error`` for Integration
  Health, and a public entry point that NEVER raises — the ingestion
  pipeline calls this after finalize and a GitHub outage must not block
  persistence.

* **Repo match required** — we post to the integration's configured
  repo, so the run's ``ci_repo`` must equal ``owner/name`` (case-
  insensitive). A PR number is only meaningful within its own repo;
  posting run A's PR number onto repo B would hit an unrelated PR.

* **Baseline selection** — most recent completed run on ``main`` /
  ``master``; else the most recent completed run on a different branch
  than the PR run; else no baseline (all failures listed as "Failing"
  with an explanatory note, no newly/fixed split).

* **Known-flaky definition (reused, not invented)** — the union of
  fingerprints under active quarantine
  (``flaky_quarantine_service.active_quarantines_for_project``, the same
  set the ingestion pipeline tags ``quarantined``) and fingerprints with
  a ``FlakyCoachResult`` row (the flaky-coach cache: ≥3 runs in window
  with BOTH passes and failures — see
  ``test_health_coach_service._populate_flaky_cache``).

* **Diff semantics reused from run-compare** — pairing by
  ``test_fingerprint`` via ``run_compare_service._load_test_rows`` and
  classification via ``run_compare_service._classify`` (new_failure /
  fixed / still_failing), so the PR comment always agrees with
  ``/runs/compare``.

* **NO AI content in this slice** — analysis may not have finished when
  the comment posts; a follow-up story appends AI suggestions.

* **GitHub etiquette** — 10s timeout, a single retry on 5xx/network
  error, comment listing paginated to 3 pages of 100.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    FlakyCoachResult,
    GitHubIntegration,
    LaunchStatus,
    Project,
    TestRun,
)
from app.core.config import settings
from app.services.github_checks_service import (
    SECRET_SCOPE,
    _post_allowed,
    _secret_key,
    _ssrf_block_reason,
    get_integration,
)
from app.services.run_compare_service import (
    _classify,
    _load_test_rows,
    _status_bucket,
)

logger = structlog.get_logger("services.github_pr_comment")

# Marker prefix — the first line of every comment we own. The project id
# suffix lets two TestLookup projects report onto the same PR without
# clobbering each other's comment.
_MARKER_PREFIX = "<!-- testlookup-pr-summary:"

# Row caps per section. Overflow renders as a "+K more" line.
_NEWLY_FAILED_CAP = 10
_FLAKY_CAP = 5
_FIXED_CAP = 5

# Failure-message first-line truncation (chars).
_MESSAGE_CAP = 120

# Comment-listing pagination: up to 3 pages of 100.
_COMMENT_PAGE_SIZE = 100
_COMMENT_MAX_PAGES = 3

_HTTP_TIMEOUT = 10.0


def _marker(project_id: uuid.UUID | str) -> str:
    return f"{_MARKER_PREFIX}{project_id} -->"


# ── HTTP layer (patched in tests) ────────────────────────────────────────────


async def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        return await client.request(
            method, url, headers=headers, json=json_body, params=params,
        )


async def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> httpx.Response:
    """Single retry on network error or 5xx — GitHub etiquette, not the
    full ``resilience`` backoff (this is a best-effort cosmetic post)."""
    try:
        resp = await _request(
            method, url, headers=headers, json_body=json_body, params=params,
        )
    except httpx.HTTPError:
        return await _request(
            method, url, headers=headers, json_body=json_body, params=params,
        )
    if resp.status_code >= 500:
        return await _request(
            method, url, headers=headers, json_body=json_body, params=params,
        )
    return resp


# ── Pure helpers (unit-tested directly) ──────────────────────────────────────


def _first_message_line(message: Optional[str]) -> str:
    """First non-empty line of a failure message, backtick-safe and
    truncated to ``_MESSAGE_CAP`` chars for the code-quoted row."""
    if not message:
        return ""
    stripped = message.strip()
    if not stripped:
        return ""
    line = stripped.splitlines()[0].replace("`", "'").strip()
    if len(line) > _MESSAGE_CAP:
        return line[:_MESSAGE_CAP] + "…"
    return line


@dataclass
class _Partition:
    """PR-run failures split into newly-failed vs known-flaky, plus the
    fixed-vs-baseline rows and the still-failing-on-baseline count."""
    newly_failed: list[dict[str, str]] = field(default_factory=list)
    known_flaky: list[dict[str, str]] = field(default_factory=list)
    fixed: list[dict[str, str]] = field(default_factory=list)
    still_failing: int = 0
    has_baseline: bool = False


def _row(tc: Any) -> dict[str, str]:
    return {
        "name": str(getattr(tc, "test_name", None) or getattr(tc, "test_fingerprint", "?")),
        "message": _first_message_line(getattr(tc, "error_message", None)),
    }


def _partition_tests(
    right_tests: dict[str, Any],
    left_tests: dict[str, Any],
    flaky_fps: set[str],
    *,
    has_baseline: bool,
) -> _Partition:
    """Partition the PR run's tests against the baseline.

    A failing test whose fingerprint is known-flaky lands ONLY in the
    flaky section (never in newly-failed and never in the still-failing
    count) — the whole point is separating "your PR broke this" from
    "this was already unreliable".
    """
    part = _Partition(has_baseline=has_baseline)

    for fp, tc in sorted(right_tests.items()):
        bucket = _status_bucket(tc.status)
        if bucket not in ("failed", "broken"):
            continue
        if fp in flaky_fps:
            part.known_flaky.append(_row(tc))
            continue
        if not has_baseline:
            part.newly_failed.append(_row(tc))
            continue
        left_tc = left_tests.get(fp)
        cls = _classify(
            left_tc.status if left_tc else None, tc.status, None, None,
        )
        if cls == "still_failing":
            part.still_failing += 1
        else:
            # new_failure (incl. absent-from-baseline) and any other
            # status change into failed/broken (e.g. skipped→failed,
            # classified "regressed") reads as newly failed on this PR.
            part.newly_failed.append(_row(tc))

    if has_baseline:
        for fp, left_tc in sorted(left_tests.items()):
            if _status_bucket(left_tc.status) not in ("failed", "broken"):
                continue
            right_tc = right_tests.get(fp)
            if right_tc is not None and _status_bucket(right_tc.status) == "passed":
                part.fixed.append(_row(right_tc))

    return part


def _section(
    header: str,
    rows: list[dict[str, str]],
    cap: int,
    deep_link: Optional[str],
    *,
    note: Optional[str] = None,
    overflow_label: str = "more",
) -> list[str]:
    lines = [f"### {header} ({len(rows)})", ""]
    if note:
        lines.extend([f"_{note}_", ""])
    for row in rows[:cap]:
        name = f"`{row['name']}`"
        if deep_link:
            name = f"[{name}]({deep_link})"
        if row["message"]:
            lines.append(f"- {name} — `{row['message']}`")
        else:
            lines.append(f"- {name}")
    overflow = len(rows) - cap
    if overflow > 0:
        lines.append(f"- _+{overflow} {overflow_label}_")
    lines.append("")
    return lines


def _build_comment_body(
    run: Any,
    project_id: uuid.UUID | str,
    project_name: Optional[str],
    part: _Partition,
    baseline: Optional[Any],
) -> str:
    """Render the sticky comment markdown. First line is ALWAYS the
    hidden marker — it is the upsert key."""
    total = int(run.total_tests or 0)
    passed = int(run.passed_tests or 0)
    failed = int(run.failed_tests or 0)
    broken = int(run.broken_tests or 0)
    skipped = int(run.skipped_tests or 0)
    pass_rate = float(run.pass_rate or 0)
    green = failed == 0 and broken == 0
    icon = "✅" if green else "❌"

    base_url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    intel_path = f"/intelligence/{run.id}"
    deep_link = f"{base_url}{intel_path}" if base_url else None

    proj_label = project_name or "TestLookup"
    lines: list[str] = [
        _marker(project_id),
        f"## {icon} {proj_label} — {passed}/{total} passed ({pass_rate:.1f}%)",
        "",
    ]
    counts_line = (
        f"**Failed:** {failed} · **Broken:** {broken} · **Skipped:** {skipped}"
    )
    if deep_link:
        counts_line += f" · [Open run in TestLookup]({deep_link})"
    lines.extend([counts_line, ""])

    if not part.has_baseline and (part.newly_failed or part.known_flaky):
        lines.extend([
            "_No baseline run found — newly-failed vs fixed comparison "
            "unavailable; listing all failures._",
            "",
        ])

    if part.newly_failed:
        header = "❌ Newly failed" if part.has_baseline else "❌ Failing"
        lines.extend(_section(
            header, part.newly_failed, _NEWLY_FAILED_CAP, deep_link,
            overflow_label="more failed tests",
        ))

    if part.known_flaky:
        lines.extend(_section(
            "⚠️ Known flaky", part.known_flaky, _FLAKY_CAP, deep_link,
            note="Historically flaky — likely not caused by this PR.",
            overflow_label="more flaky tests",
        ))

    if part.fixed:
        lines.extend(_section(
            "✅ Fixed", part.fixed, _FIXED_CAP, deep_link,
            overflow_label="more fixed tests",
        ))

    if green and not part.newly_failed and not part.known_flaky and not part.fixed:
        lines.extend(["All tests passed.", ""])

    footer_bits: list[str] = []
    if part.has_baseline and baseline is not None:
        baseline_label = (
            getattr(baseline, "build_number", None) or str(getattr(baseline, "id", ""))[:8]
        )
        footer_bits.append(f"{part.still_failing} still failing on baseline")
        footer_bits.append(f"baseline: {baseline_label}")
    footer_bits.append("posted by TestLookup")
    lines.extend(["---", "_" + " · ".join(footer_bits) + "_"])
    return "\n".join(lines)


# ── DB helpers ───────────────────────────────────────────────────────────────


async def _load_run(db: AsyncSession, run_id: uuid.UUID) -> Optional[TestRun]:
    result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    return result.scalar_one_or_none()


async def _project_name(db: AsyncSession, project_id: uuid.UUID) -> Optional[str]:
    try:
        result = await db.execute(
            select(Project.name).where(Project.id == project_id)
        )
        return result.scalar_one_or_none()
    except Exception:
        return None


async def _select_baseline(db: AsyncSession, run: TestRun) -> Optional[TestRun]:
    """Most recent completed run on main/master; else the most recent
    completed run on a different branch than the PR run; else None."""
    recency = func.coalesce(TestRun.end_time, TestRun.created_at).desc()
    common = (
        TestRun.project_id == run.project_id,
        TestRun.id != run.id,
        TestRun.status != LaunchStatus.IN_PROGRESS,
    )

    result = await db.execute(
        select(TestRun)
        .where(*common, func.lower(TestRun.branch).in_(("main", "master")))
        .order_by(recency)
        .limit(1)
    )
    baseline = result.scalar_one_or_none()
    if baseline is not None:
        return baseline

    result = await db.execute(
        select(TestRun)
        .where(
            *common,
            func.coalesce(TestRun.branch, "") != (run.branch or ""),
        )
        .order_by(recency)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _flaky_fingerprints(db: AsyncSession, project_id: uuid.UUID) -> set[str]:
    """Union of the two existing known-flaky sources: active quarantine
    fingerprints (feature-flag-gated, same set the ingestion pipeline
    tags ``quarantined``) and the flaky-coach result cache."""
    fps: set[str] = set()
    try:
        from app.services.flaky_quarantine_service import (
            active_quarantines_for_project,
        )
        fps |= await active_quarantines_for_project(db, project_id)
    except Exception as exc:
        logger.debug("pr_comment quarantine lookup failed", error=str(exc))
    try:
        result = await db.execute(
            select(FlakyCoachResult.test_fingerprint).where(
                FlakyCoachResult.project_id == project_id
            )
        )
        fps |= {row[0] for row in result.all() if row[0]}
    except Exception as exc:
        logger.debug("pr_comment flaky-coach lookup failed", error=str(exc))
    return fps


async def _record_outcome(
    integration_id: uuid.UUID, *, error: Optional[str],
) -> None:
    """Persist last_error / last_posted_at bookkeeping on the integration
    row — same Integration Health surface the checks service writes.
    Own session (worker path, no caller session); never raises."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(GitHubIntegration).where(
                    GitHubIntegration.id == integration_id
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            now = datetime.now(timezone.utc)
            if error is None:
                row.last_posted_at = now
                row.last_error = None
                row.last_error_at = None
            else:
                row.last_error = error[:500]
                row.last_error_at = now
            await db.commit()
    except Exception as exc:
        logger.warning(
            "pr_comment outcome record failed",
            integration_id=str(integration_id),
            error=str(exc),
        )


# ── Context gathering ────────────────────────────────────────────────────────


@dataclass
class _PRCommentContext:
    integration_id: uuid.UUID
    api_base: str
    repo: str  # "owner/name"
    pr_number: int
    pat: str
    mode: str
    marker: str
    body: str
    has_failures: bool
    has_fixed: bool


async def _gather_context(run_id: uuid.UUID) -> Optional[_PRCommentContext]:
    """Phase 1 — all DB reads. Returns None (with a debug log) for every
    non-participating case; the session closes before any HTTP happens."""
    async with AsyncSessionLocal() as db:
        run = await _load_run(db, run_id)
        if run is None:
            logger.debug("pr_comment skipped: run not found", run_id=str(run_id))
            return None
        if not run.pr_number or not run.ci_repo:
            logger.debug(
                "pr_comment skipped: no PR context on run", run_id=str(run_id),
            )
            return None

        integration = await get_integration(db, run.project_id)
        if integration is None or not integration.enabled:
            logger.debug(
                "pr_comment skipped: integration disabled or missing",
                run_id=str(run_id),
            )
            return None

        mode = (getattr(integration, "pr_comment_mode", None) or "failures_only").lower()
        if mode == "off":
            logger.debug("pr_comment skipped: mode off", run_id=str(run_id))
            return None

        integration_repo = f"{integration.repo_owner}/{integration.repo_name}"
        if run.ci_repo.strip().lower() != integration_repo.lower():
            # PR numbers are repo-scoped: posting this run's pr_number
            # onto a different configured repo would hit an unrelated PR.
            logger.debug(
                "pr_comment skipped: run repo does not match integration",
                run_id=str(run_id),
                run_repo=run.ci_repo,
                integration_repo=integration_repo,
            )
            return None

        from app.services import secret_service
        pat = await secret_service.read_secret(
            db, SECRET_SCOPE, _secret_key(run.project_id),
        )
        if not pat:
            logger.debug("pr_comment skipped: no PAT", run_id=str(run_id))
            return None

        project_name = await _project_name(db, run.project_id)
        baseline = await _select_baseline(db, run)
        right_tests = await _load_test_rows(db, run.id)
        left_tests = (
            await _load_test_rows(db, baseline.id) if baseline is not None else {}
        )
        # A baseline run whose per-test rows are missing (live-stream
        # buffer eviction etc.) can't support a newly/fixed split —
        # treat it as no-baseline rather than classifying everything
        # as new_failure against an empty left side.
        has_baseline = baseline is not None and bool(left_tests)
        flaky_fps = await _flaky_fingerprints(db, run.project_id)

        part = _partition_tests(
            right_tests, left_tests, flaky_fps, has_baseline=has_baseline,
        )
        body = _build_comment_body(
            run, run.project_id, project_name, part,
            baseline if has_baseline else None,
        )

        has_failures = (int(run.failed_tests or 0) + int(run.broken_tests or 0)) > 0

        return _PRCommentContext(
            integration_id=integration.id,
            api_base=integration.api_base_url.rstrip("/"),
            repo=integration_repo,
            pr_number=int(run.pr_number),
            pat=pat,
            mode=mode,
            marker=_marker(run.project_id),
            body=body,
            has_failures=has_failures,
            has_fixed=bool(part.fixed),
        )


# ── Upsert against the GitHub Issues API ─────────────────────────────────────


async def _find_existing_comment(
    api_base: str,
    repo: str,
    pr_number: int,
    headers: dict[str, str],
    marker: str,
) -> Optional[int]:
    """Return the id of the comment whose body starts with ``marker``,
    scanning up to 3 pages of 100. Raises on HTTP failure (caller
    records the error)."""
    url = f"{api_base}/repos/{repo}/issues/{pr_number}/comments"
    for page in range(1, _COMMENT_MAX_PAGES + 1):
        resp = await _request_with_retry(
            "GET", url, headers=headers,
            params={"per_page": _COMMENT_PAGE_SIZE, "page": page},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"list comments HTTP {resp.status_code}: {resp.text[:200]}"
            )
        comments = resp.json() or []
        for comment in comments:
            if str(comment.get("body") or "").startswith(marker):
                comment_id = comment.get("id")
                return int(comment_id) if comment_id is not None else None
        if len(comments) < _COMMENT_PAGE_SIZE:
            break
    return None


# ── Public entry point ───────────────────────────────────────────────────────


async def post_pr_summary_for_run(run_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """Upsert the sticky PR summary comment for a finalized run.

    Best-effort and NEVER raises — the ingestion pipeline calls this
    after finalize and must not block on GitHub. Returns None for
    non-participating runs, a ``{"skipped": ...}`` dict for policy
    skips, ``{"posted"/"updated": True, ...}`` on success, and
    ``{"error": ...}`` on delivery failure (also recorded on the
    integration row for Integration Health).
    """
    try:
        if not await _post_allowed():
            logger.debug(
                "pr_comment skipped: offline mode or feature flag off",
                run_id=str(run_id),
            )
            return None

        ctx = await _gather_context(run_id)
        if ctx is None:
            return None

        comments_url = f"{ctx.api_base}/repos/{ctx.repo}/issues/{ctx.pr_number}/comments"
        block = await _ssrf_block_reason(comments_url)
        if block:
            await _record_outcome(
                ctx.integration_id, error=f"blocked unsafe target: {block}",
            )
            logger.warning(
                "pr_comment blocked unsafe target",
                run_id=str(run_id),
                reason=block,
            )
            return {"skipped": "blocked_unsafe_target", "reason": block}

        headers = {
            "Authorization": f"Bearer {ctx.pat}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "TestLookup/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            existing_id = await _find_existing_comment(
                ctx.api_base, ctx.repo, ctx.pr_number, headers, ctx.marker,
            )
        except Exception as exc:
            await _record_outcome(
                ctx.integration_id,
                error=f"{type(exc).__name__}: {str(exc)[:480]}",
            )
            logger.warning(
                "pr_comment list failed", run_id=str(run_id), error=str(exc),
            )
            return {"error": str(exc)}

        # failures_only: a green run with nothing fixed to report posts
        # nothing NEW — but an existing marker comment must be updated so
        # a PR that went red→green shows green.
        if (
            ctx.mode == "failures_only"
            and not ctx.has_failures
            and not ctx.has_fixed
            and existing_id is None
        ):
            logger.debug(
                "pr_comment skipped: green run, failures_only, no prior comment",
                run_id=str(run_id),
            )
            return {"skipped": "green_run_no_prior_comment"}

        try:
            if existing_id is not None:
                resp = await _request_with_retry(
                    "PATCH",
                    f"{ctx.api_base}/repos/{ctx.repo}/issues/comments/{existing_id}",
                    headers=headers,
                    json_body={"body": ctx.body},
                )
            else:
                resp = await _request_with_retry(
                    "POST", comments_url, headers=headers,
                    json_body={"body": ctx.body},
                )
        except Exception as exc:
            await _record_outcome(
                ctx.integration_id,
                error=f"{type(exc).__name__}: {str(exc)[:480]}",
            )
            logger.warning(
                "pr_comment post failed", run_id=str(run_id), error=str(exc),
            )
            return {"error": str(exc)}

        if resp.status_code in (200, 201):
            await _record_outcome(ctx.integration_id, error=None)
            logger.info(
                "pr_comment upserted",
                run_id=str(run_id),
                pr_number=ctx.pr_number,
                updated=existing_id is not None,
                status_code=resp.status_code,
            )
            comment_id = None
            try:
                comment_id = (resp.json() or {}).get("id") if resp.content else None
            except Exception:
                comment_id = None
            return {
                "posted": True,
                "updated": existing_id is not None,
                "status_code": resp.status_code,
                "comment_id": comment_id,
            }

        await _record_outcome(
            ctx.integration_id,
            error=f"HTTP {resp.status_code}: {resp.text[:480]}",
        )
        logger.warning(
            "pr_comment rejected",
            run_id=str(run_id),
            status_code=resp.status_code,
        )
        return {"error": f"HTTP {resp.status_code}", "status_code": resp.status_code}
    except Exception as exc:  # noqa: BLE001 — never raise into the pipeline
        logger.warning(
            "pr_comment unhandled failure", run_id=str(run_id), error=str(exc),
        )
        return {"error": str(exc)}
