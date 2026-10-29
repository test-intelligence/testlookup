"""History-bisect narrowing (W23b FAIL-12): one failing test, narrowed to its own last pass.

``GET /api/v1/runs/{run_id}/bisect?fingerprint=`` (``routers/commit_attribution``)
is the only caller. Observe only: no bisect job, no table, no CI credential.

* ``fingerprint_in_run`` scopes the QUERY id to the PATH run (``WHERE test_run_id
  AND test_fingerprint``): another run's, another project's or an unknown
  fingerprint all raise ``FingerprintNotInRun`` -- the same 404, no oracle.
* ``narrow_range_for_test`` reads ONE bounded statement through
  ``ix_test_runs_project_branch_created`` (migration 0245) and hands the rows to
  the pure ``bisect_narrowing.walk``. (The backlog placed it in
  ``commit_attribution_service``; that 1 471-line service is imported, never
  edited -- contract D-16.)
* ``compare_narrowed`` lists the commits between the two builds through the
  EXISTING offline gate, git-host gates, target resolvers and SSRF-guarded
  fetchers of ``commit_attribution_service`` (GitHub first, else GitLab). Every
  DB read happens first; the request session's connection is handed back
  (``release_read_connection``) before Redis or HTTP. The compare is cached in
  Redis by the immutable SHA pair; Redis down or slow means NO compare
  (``cache_unavailable``); a refused compare is remembered 60 s; a timeout is
  not a failure and is never cached.

Reads only: no ``add``, ``flush`` or ``commit`` (``STAGE_ONLY_SERVICES``). A log
line carries ids, states, reasons, counts and durations -- never a test name,
a branch or a SHA.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.postgres import release_read_connection
from app.db.redis_client import get_redis
from app.models.postgres import LaunchStatus, TestCase, TestRun, TestStatus
from app.models.schemas import BisectResponse
from app.services import commit_attribution_service as cas
from app.services import github_checks_service, github_target
from app.services.bisect_narrowing import Narrowing, WalkRow, compare_gate, outcome_of, series_key, walk
from app.services.bisect_vocab import (
    BISECT_FALLBACK_BRANCHES,
    BISECT_MAX_RUNS,
    CACHE_IO_TIMEOUT_SECONDS,
    COMPARE_BUDGET_SECONDS,
    COMPARE_CACHE_KEY,
    COMPARE_CACHE_TTL_SECONDS,
    COMPARE_FAILED_TTL_SECONDS,
    LISTED_COMMITS_MAX,
)

_slog = structlog.get_logger(__name__)

#: Each cached commit keeps at most this many changed paths, and a cache value
#: larger than this is not stored (logged ``bisect_compare_not_cached``).
_CACHED_FILES_PER_COMMIT = 200
_CACHE_VALUE_MAX_BYTES = 512 * 1024
#: A stored commit hash reaches the compare URL only when it looks like a ref.
_REF_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")


class FingerprintNotInRun(Exception):
    """The QUERY fingerprint names no test case OF THE PATH RUN (-> 404 ``fingerprint_not_in_run``)."""


async def fingerprint_in_run(db: AsyncSession, run: TestRun, fingerprint: str) -> tuple[str, str]:
    """``(test_name, outcome)`` of the run's test case with this fingerprint.

    ``WHERE test_run_id = :run_id AND test_fingerprint = :fp`` (the unique index
    ``uq_test_cases_run_fingerprint``). Any other value raises
    ``FingerprintNotInRun``. Not named ``test_*``: pytest would collect it."""
    result = await db.execute(
        select(TestCase.test_name, TestCase.status)
        .where(TestCase.test_run_id == run.id, TestCase.test_fingerprint == fingerprint)
        .limit(1)
    )
    row = result.first()
    if row is None:
        raise FingerprintNotInRun()
    return str(row[0] or ""), outcome_of(row[1])


# ONE statement, two bounded parts (contract §2.1 B-1). Every value is bound.
# The series: the run itself plus the older finished runs of its branch series
# (``coalesce(lower(branch), '')`` == the 0245 index expression), newest first.
# The fallback: the newest finished runs on main / master in which this test
# PASSED, only for a run that is not itself on main / master.
_NARROW_SQL = text(
    """
WITH series AS (
  SELECT r.id, r.build_number, r.commit_hash, r.branch, r.created_at, r.ci_run_url
  FROM test_runs r
  WHERE r.project_id = :project_id
    AND coalesce(lower(r.branch), '') = :series_key
    AND r.created_at <= :pivot
    AND (r.id = :run_id OR (r.status <> :in_progress AND r.created_at < :pivot))
  ORDER BY r.created_at DESC, r.id DESC
  LIMIT :bound
), fallback AS (
  SELECT r.id, r.build_number, r.commit_hash, r.branch, r.created_at, r.ci_run_url
  FROM test_runs r
  WHERE r.project_id = :project_id
    AND coalesce(lower(r.branch), '') IN (:fallback_a, :fallback_b)
    AND :series_key NOT IN (:fallback_a, :fallback_b)
    AND r.status <> :in_progress AND r.created_at < :pivot
  ORDER BY r.created_at DESC, r.id DESC
  LIMIT :bound
)
SELECT 'series' AS part, s.*, tc.status AS test_status
  FROM series s
  LEFT JOIN test_cases tc ON tc.test_run_id = s.id AND tc.test_fingerprint = :fingerprint
UNION ALL
SELECT 'fallback' AS part, f.*, tc.status AS test_status
  FROM fallback f
  JOIN test_cases tc ON tc.test_run_id = f.id AND tc.test_fingerprint = :fingerprint AND tc.status = :passed
"""
)


def narrow_params(run: TestRun, fingerprint: str, *, bound: int = BISECT_MAX_RUNS) -> dict[str, Any]:
    """The bound values of ``_NARROW_SQL`` for one run and fingerprint (also used by the EXPLAIN test)."""
    fallback_a, fallback_b = BISECT_FALLBACK_BRANCHES
    return {
        "project_id": run.project_id,
        "series_key": series_key(run.branch),
        "pivot": run.created_at,
        "run_id": run.id,
        "in_progress": LaunchStatus.IN_PROGRESS.value,
        "bound": bound,
        "fallback_a": fallback_a,
        "fallback_b": fallback_b,
        "fingerprint": fingerprint,
        "passed": TestStatus.PASSED.value,
    }


def _walk_row(row: Any) -> WalkRow:
    return WalkRow(
        run_id=row["id"] if isinstance(row["id"], uuid.UUID) else uuid.UUID(str(row["id"])),
        build_number=str(row["build_number"] or ""),
        commit_hash=row["commit_hash"],
        branch=row["branch"],
        created_at=row["created_at"],
        ci_run_url=row["ci_run_url"],
        outcome=outcome_of(row["test_status"]),
    )


async def narrow_range_for_test(db: AsyncSession, run: TestRun, fingerprint: str) -> Narrowing:
    """Read the ONE bounded statement and judge it with ``bisect_narrowing.walk``."""
    result = await db.execute(_NARROW_SQL, narrow_params(run, fingerprint))
    series: list[WalkRow] = []
    fallback: list[WalkRow] = []
    for row in result.mappings().all():
        (series if row["part"] == "series" else fallback).append(_walk_row(row))
    # UNION ALL keeps no order across its parts: newest first, ties by id (as the statement).
    series.sort(key=lambda r: (r.created_at, r.run_id), reverse=True)
    fallback.sort(key=lambda r: (r.created_at, r.run_id), reverse=True)
    return walk(series, fallback, bound=BISECT_MAX_RUNS)


def _compare(state: str, reason: Optional[str], *, run_level_commit_count: Optional[int], **extra: Any) -> dict:
    """A ``BisectCompare`` body."""
    body: dict[str, Any] = {
        "state": state, "reason": reason, "host": None, "cached": False, "commit_count": 0,
        "truncated": False, "commits": [], "has_location_signal": False, "caveat": None,
        "run_level_commit_count": run_level_commit_count,
    }
    body.update(extra)
    return body


async def _cache_get(key: str) -> tuple[bool, Optional[str]]:
    """``(ok, raw)``: ``ok`` False when Redis is down or slower than ``CACHE_IO_TIMEOUT_SECONDS``."""
    try:
        raw = await asyncio.wait_for(get_redis().get(key), timeout=CACHE_IO_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - any cache fault means no compare (D-3)
        _slog.warning("bisect_compare_cache_unavailable", error_type=type(exc).__name__)
        return False, None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return True, raw if isinstance(raw, str) else None


async def _cache_set(key: str, value: dict, ttl: int, *, run_id: uuid.UUID) -> bool:
    """Best effort: a value over ``_CACHE_VALUE_MAX_BYTES`` or a cache fault is not stored."""
    encoded = json.dumps(value, separators=(",", ":"))
    size = len(encoded.encode())
    if size > _CACHE_VALUE_MAX_BYTES:
        _slog.info("bisect_compare_not_cached", run_id=str(run_id), size_bytes=size)
        return False
    try:
        await asyncio.wait_for(get_redis().set(key, encoded, ex=ttl), timeout=CACHE_IO_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - the answer stands; only the cache write is missed
        _slog.warning("bisect_compare_cache_write_failed", run_id=str(run_id), error_type=type(exc).__name__)
        return False
    return True


def _cacheable(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The fetched commits as plain values, each with at most 200 changed paths."""
    return [{**c, "files": list(c.get("files") or [])[:_CACHED_FILES_PER_COMMIT]} for c in commits]


def _parse_cached(raw: Optional[str]) -> Optional[dict]:
    """A readable cache value (``{"failed": true}`` or ``{"host", "commits"}``), else ``None`` (a miss)."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if isinstance(value, dict) and (value.get("failed") is True or isinstance(value.get("commits"), list)):
        return value
    return None


async def _resolve_target(db: AsyncSession, run: TestRun) -> tuple[Optional[str], Any]:
    """``(host, target)`` in ``resolve_commit_range``'s order: GitHub (its gate) first, else GitLab."""
    if await github_checks_service._post_allowed(db, project_id=run.project_id):
        target = await cas._connector_target(db, run)
        if target is not None:
            return "github", target
    gitlab = await cas._gitlab_connector_target(db, run)
    if gitlab is not None:
        return "gitlab", gitlab
    return None, None


async def compare_narrowed(
    db: AsyncSession,
    run: TestRun,
    narrowing: Narrowing,
    fingerprint: str,
    *,
    run_level_commit_count: Optional[int] = None,
) -> dict:
    """The ``BisectCompare`` body for a narrowing (contract §2.1 B-3).

    The gate and the offline mode are decided first and touch neither Redis nor
    HTTP. Then every DB read (target, the test's locator, the link base), then
    ``release_read_connection``, then ONE Redis read and, on a miss, the
    host's three-dot compare inside ``COMPARE_BUDGET_SECONDS``."""
    rlcc = run_level_commit_count
    gate = compare_gate(narrowing)
    if gate is not None:
        return _compare("not_attempted", gate, run_level_commit_count=rlcc)
    if settings.AI_OFFLINE_MODE:
        return _compare("unavailable", "offline_mode", run_level_commit_count=rlcc)
    if narrowing.good is None or narrowing.bad is None:  # compare_gate already said so; for the type checker
        return _compare("not_attempted", "not_narrowed", run_level_commit_count=rlcc)

    host, target = await _resolve_target(db, run)
    if target is None or host is None:
        reason, _api_base = await github_target.github_target_status(db, run.project_id, ci_repo=run.ci_repo)
        return _compare("unavailable", reason or "no_integration", run_level_commit_count=rlcc)
    test_cases = await cas._target_test_cases(db, run, cluster_id=None, fingerprint=fingerprint)
    loc = cas.build_locator(test_cases)
    api_base = await cas._commit_link_api_base(db, run)
    run_id, project_id, ci_repo = run.id, run.project_id, run.ci_repo
    good_sha = (narrowing.good.commit_hash or "").strip()
    bad_sha = (narrowing.bad.commit_hash or "").strip()
    # No connection is held across Redis or HTTP, and no second session is opened.
    await release_read_connection(db)

    if not (_REF_RE.match(good_sha) and _REF_RE.match(bad_sha)):
        # A stored value that cannot be a ref never reaches the host's URL.
        return _compare("unavailable", "fetch_failed", host=host, run_level_commit_count=rlcc)
    key = COMPARE_CACHE_KEY.format(project_id=project_id, host=host, good=good_sha.lower(), bad=bad_sha.lower())
    ok, raw = await _cache_get(key)
    if not ok:
        return _compare("unavailable", "cache_unavailable", host=host, run_level_commit_count=rlcc)
    cached = _parse_cached(raw)
    if cached is not None and cached.get("failed") is True:
        return _compare("unavailable", "fetch_failed", host=host, cached=True, run_level_commit_count=rlcc)

    if cached is not None:
        commits: list[dict[str, Any]] = [c for c in cached["commits"] if isinstance(c, dict)]
    else:
        fetch = (cas._fetch_connector_range if host == "github" else cas._fetch_gitlab_range)
        try:
            fetched = await asyncio.wait_for(fetch(target, good_sha, bad_sha, run_id=run_id),
                                             timeout=COMPARE_BUDGET_SECONDS)
        except TimeoutError:
            # A timeout is not a failure: nothing is cached, the block offers Retry.
            return _compare("timed_out", "timed_out", host=host, run_level_commit_count=rlcc)
        except (httpx.HTTPError, ValueError) as exc:
            _slog.warning("bisect_compare_failed", run_id=str(run_id), error_type=type(exc).__name__)
            fetched = None
        if fetched is None:
            await _cache_set(key, {"failed": True}, COMPARE_FAILED_TTL_SECONDS, run_id=run_id)
            return _compare("unavailable", "fetch_failed", host=host, run_level_commit_count=rlcc)
        commits = _cacheable(fetched)
        await _cache_set(key, {"host": host, "commits": commits}, COMPARE_CACHE_TTL_SECONDS, run_id=run_id)

    ranked = cas.score_commits(commits, loc)
    web_urls = {c.get("sha"): c.get("web_url") for c in commits if c.get("web_url")}
    for item in ranked:
        sha = item.get("sha") or ""
        item["commit_url"] = web_urls.get(sha) or cas._commit_html_url(ci_repo, api_base, sha)
    return _compare(
        "fetched", None, host=host, cached=cached is not None, commit_count=len(commits),
        truncated=len(commits) >= cas._MAX_COMMITS, commits=ranked[:LISTED_COMMITS_MAX],
        has_location_signal=bool(loc.has_signal), caveat=cas.MONOREPO_CAVEAT, run_level_commit_count=rlcc,
    )


def _run_ref(row: Optional[WalkRow]) -> Optional[dict]:
    if row is None:
        return None
    return {
        "run_id": row.run_id, "build_number": row.build_number, "commit_hash": row.commit_hash or None,
        "branch": row.branch, "created_at": row.created_at,
        "ci_run_url": cas._http_link(row.ci_run_url),  # only an absolute http(s) URL lands in an href
        "outcome": row.outcome,
    }


async def get_bisect(
    db: AsyncSession,
    run: TestRun,
    fingerprint: str,
    *,
    now: datetime,
    test_name: Optional[str] = None,
) -> dict:
    """The ``BisectResponse`` body. Raises ``FingerprintNotInRun`` (unless the
    caller already checked and passes ``test_name``). Writes nothing."""
    started = time.monotonic()
    if test_name is None:
        test_name, _outcome = await fingerprint_in_run(db, run, fingerprint)
    run_id, branch = run.id, run.branch
    narrowing = await narrow_range_for_test(db, run, fingerprint)
    row = await cas._get_row(db, run_id)
    narrowed_from = cas._base_source_of(row)
    # An ``unavailable`` row is no stored range (None), not a range of 0 commits.
    has_range = row is not None and row.source != cas.SOURCE_UNAVAILABLE
    run_level_commit_count = len(row.commits or []) if row is not None and has_range else None
    compare = await compare_narrowed(db, run, narrowing, fingerprint, run_level_commit_count=run_level_commit_count)

    body = BisectResponse.model_validate({
        "run_id": run_id, "fingerprint": fingerprint, "test_name": test_name,
        "state": narrowing.state, "series_branch": branch or None,
        "anchor": narrowing.anchor, "fallback_branch": narrowing.fallback_branch,
        "good": _run_ref(narrowing.good), "bad": _run_ref(narrowing.bad),
        "streak_length": narrowing.streak_length,
        "untested_runs": [_run_ref(r) for r in narrowing.untested],
        "untested_count": narrowing.untested_count,
        "runs_read": narrowing.runs_read, "bounded": narrowing.bounded,
        "narrowed_from": narrowed_from, "compare": compare, "generated_at": now,
    }).model_dump(mode="json")
    _slog.info(
        "bisect_answered",
        run_id=str(run_id),
        state=narrowing.state,
        anchor=narrowing.anchor,
        compare_state=compare["state"],
        compare_reason=compare["reason"],
        cached=bool(compare["cached"]),
        runs_read=narrowing.runs_read,
        untested_count=narrowing.untested_count,
        commit_count=int(compare["commit_count"]),
        duration_ms=round((time.monotonic() - started) * 1000, 1),
    )
    return body
