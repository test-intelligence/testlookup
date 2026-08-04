"""Commit attribution — Epic 8 US-8.1 (commit ranges) + US-8.2 (suspects).

Two capabilities, one service:

**US-8.1 — commit-range association.** For a run, resolve the commits
landed since the run's last-green baseline (``base`` = last-green run's
``commit_hash``, ``head`` = this run's ``commit_hash``). Two acquisition
paths, in priority order:

1. **Supplied** (air-gapped) — a caller (SDK/CI) pushes the commit list on
   ingest (``IngestPayload.commit_range`` / ``LiveSessionCreate`` /
   ``/ingest/file``). Stored verbatim, NO outbound VCS call. Supplied
   always wins: if a supplied row already exists we never overwrite it
   with a connector fetch.
2. **Connector** (online) — fetch commits between ``base..head`` and each
   commit's changed files from the configured GitHub integration, reusing
   the checks service's auth / SSRF / offline / error-bookkeeping patterns
   (``AI_OFFLINE_MODE`` + ``github_checks`` flag gate the whole path).

When neither path yields data we persist an honest ``unavailable`` row so
the UI can say so plainly. Persistence is idempotent per run
(``run_commit_ranges.run_id`` UNIQUE).

**Base anchoring (2026-08).** The connector originally required a fully
all-green prior run to anchor the range. Projects with a persistent
flaky/failing tail — exactly the ones that need attribution — never have
one, so every run resolved to ``unavailable`` forever. There is now a
fallback anchor (most recent completed prior run with a commit hash,
pass/fail irrelevant), and every row records WHICH anchor it used in
``base_source``: ``supplied`` | ``green_baseline`` |
``last_completed_run`` | ``unavailable``. A weaker anchor is acceptable;
presenting it as if it were a green baseline is not.

**TIA readiness.** ``get_tia_readiness`` measures, per project, whether
these rows have accumulated into a corpus a test-impact model could
actually be trained on — see the section at the bottom of this module.

**US-8.2 — suspect ranking.** ``rank_suspects`` scores each commit in the
range for a newly-failing cluster/test with a DETERMINISTIC heuristic (no
LLM this slice): path/package overlap between the commit's changed files
and the failing test's derived location (the dominant signal), commit
recency within the range, and whether the author touched the same module
elsewhere in the range. Every ranking is inspectable — the rationale lists
exactly which files overlapped. These are **suspects, not culprits**: in a
monorepo the file owner is often not the commit author, so the copy frames
them as leads to inspect, never blame.

Expected precision (honest): this is a path-overlap heuristic. When the
failing test's stack trace yields a repo-relative location and a commit
touched a file in the same package/directory, the top suspect is usually
right; when the trace is unlocatable (Java surefire frames, vendored
frames) or the change is indirect (config, shared util) it degrades to a
recency ordering and should be read as "here's the range to look at", not
a verdict.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    FailureCluster,
    RunCommitRange,
    TestCase,
    TestRun,
)
from app.services.github_checks_service import (
    SECRET_SCOPE,
    _locate_failure,
    _post_allowed,
    _secret_key,
    _ssrf_block_reason,
    get_integration,
)
from app.services.run_compare_service import _status_bucket

logger = structlog.get_logger("services.commit_attribution")

# Acquisition sources persisted on ``run_commit_ranges.source``.
SOURCE_CONNECTOR = "connector"
SOURCE_SUPPLIED = "supplied"
SOURCE_UNAVAILABLE = "unavailable"

# Base-anchor provenance persisted on ``run_commit_ranges.base_source``
# (migration 0116). ``source`` says HOW the commit list was acquired;
# ``base_source`` says WHAT the range is measured FROM — a distinction that
# matters because the two anchors below are not equally trustworthy.
BASE_SOURCE_SUPPLIED = "supplied"              # caller pushed the base ref
BASE_SOURCE_GREEN = "green_baseline"           # last all-green prior run
BASE_SOURCE_LAST_COMPLETED = "last_completed_run"  # weaker: any completed prior run
BASE_SOURCE_UNAVAILABLE = "unavailable"        # no base could be determined
BASE_SOURCES = (
    BASE_SOURCE_SUPPLIED,
    BASE_SOURCE_GREEN,
    BASE_SOURCE_LAST_COMPLETED,
    BASE_SOURCE_UNAVAILABLE,
)
# Anchors strong enough that "commits landed since a known state" is literally
# true. ``last_completed_run`` is deliberately excluded — see its constant.
STRONG_BASE_SOURCES = (BASE_SOURCE_SUPPLIED, BASE_SOURCE_GREEN)

# Bound the connector fetch — a compare across a stale baseline could span
# thousands of commits; ranking beyond ~100 adds noise, not signal.
_MAX_COMMITS = 100
# Per-commit changed-file detail is one API call each, so this is a direct
# rate-limit knob rather than a modelling one.
#
# Arithmetic (authenticated PAT = 5 000 GitHub REST calls/hour/token, shared
# with the checks service): one resolve costs ``1 + N`` calls, so the ceiling
# is ``5000 / (1 + N)`` resolves per hour per token — N=25 → ~192 runs/h,
# N=50 → ~98, N=100 → ~49.
#
# Why it is now configurable rather than fixed at 25: the two consumers want
# different values and neither is wrong.
#   * Suspect ranking (US-8.2) is a path-overlap heuristic whose signal decays
#     fast across a long range; detail past the first ~25 commits buys almost
#     no ranking accuracy, so 25 stays the DEFAULT.
#   * A test-impact (Epic 10) corpus is the opposite: a commit persisted with
#     ``files: []`` contributes exactly zero path→test evidence, so capping at
#     25 silently discards up to 75 % of every long range's training value.
# Leaving one hard-coded number would have quietly picked ranking over TIA for
# every deployment. ``settings.COMMIT_RANGE_FILE_FETCH_LIMIT`` lets an operator
# building a corpus pay the rate-limit cost knowingly; the readiness metric
# below reports ``file_detail_coverage`` so they can see whether it is needed.
_DEFAULT_COMMIT_FILE_FETCHES = 25
# Concurrent per-commit detail fetches per gather batch.
_DETAIL_FETCH_CHUNK = 8
_HTTP_TIMEOUT = 10.0

# An ``unavailable`` row younger than this suppresses re-resolution — without
# it every GET /commit-range on a range-less run re-ran the connector (up to
# 1 + the file-fetch limit GitHub calls per request; rate-limit burn).
_RERESOLVE_COOLDOWN = timedelta(hours=6)

# First-line message cap for storage / display.
_MESSAGE_CAP = 200
# Files list cap per commit (defensive — supplied lists are already bounded).
_FILES_PER_COMMIT_CAP = 500
# Per-file path string cap (defensive — a supplied entry could be multi-MB).
_FILE_PATH_CAP = 512

# Deterministic scoring weights. Path overlap dominates so a commit that
# touched the failing test's package outranks a merely-recent one; recency
# is the tiebreak between equal-overlap commits; author-prior is a nudge.
_W_OVERLAP = 0.70
_W_RECENCY = 0.20
_W_AUTHOR = 0.10

# Common path segments that carry no attribution signal — dropped from the
# directory/token overlap so ``src`` / ``tests`` don't falsely match.
_NOISE_SEGMENTS = {
    "src", "tests", "test", "app", "lib", "main", "java", "python",
    "com", "org", "net", "io", "__tests__", "spec", "specs", "e2e",
}


# ── Pure helpers (unit-tested directly) ──────────────────────────────────────


def _file_fetch_limit() -> int:
    """Per-commit changed-file fan-out cap, clamped to ``1.._MAX_COMMITS``.

    Read per call (not snapshotted at import) so a settings override applies
    without a restart, and so tests can patch ``settings`` directly.
    """
    from app.core.config import settings

    raw = getattr(settings, "COMMIT_RANGE_FILE_FETCH_LIMIT", _DEFAULT_COMMIT_FILE_FETCHES)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_COMMIT_FILE_FETCHES
    return max(1, min(value, _MAX_COMMITS))


def _first_line(message: Optional[str]) -> str:
    stripped = (message or "").strip()
    if not stripped:
        return ""
    line = stripped.splitlines()[0].strip()
    return line[:_MESSAGE_CAP]


def _strip_test_affixes(stem: str) -> str:
    """Reduce a file/class stem to its subject: ``test_charge`` → ``charge``,
    ``ChargeTest`` → ``charge``, ``charge.spec`` → ``charge``."""
    s = stem.lower()
    for suf in (".spec", ".test"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    if s.startswith("test_"):
        s = s[len("test_"):]
    for suf in ("_test", "test", "spec"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
    return s


def _path_segments(path: str) -> list[str]:
    """Lowercased path segments with the final extension stripped."""
    norm = (path or "").strip().replace("\\", "/").lstrip("./")
    parts = [p for p in norm.split("/") if p and p != "."]
    if not parts:
        return []
    last = parts[-1]
    if "." in last:
        last = last.rsplit(".", 1)[0]
    parts[-1] = last
    return [p.lower() for p in parts]


def _split_dotted(value: Optional[str]) -> list[str]:
    """Split a package/class like ``com.acme.PaymentsTest`` into lowercased
    tokens, dropping empties."""
    if not value:
        return []
    raw = value.replace("$", ".").replace("/", ".")
    return [tok.lower() for tok in raw.split(".") if tok]


class _FailingLocator:
    """The derived location signals for the failing test(s) under scrutiny.

    * ``located_paths`` — repo-relative paths pulled from stack traces.
    * ``dir_sets``      — per-located-path directory segment lists.
    * ``stems``         — subject stems (test-affix-stripped) from located
                          paths, class names, and test names.
    * ``module_tokens`` — package/class/dir tokens, minus noise segments.
    """

    def __init__(self) -> None:
        self.located_paths: set[str] = set()
        self.dir_sets: list[set[str]] = []
        self.stems: set[str] = set()
        self.module_tokens: set[str] = set()

    @property
    def has_signal(self) -> bool:
        return bool(self.stems or self.module_tokens or self.located_paths)


def build_locator(test_cases: list[Any]) -> _FailingLocator:
    """Derive path/package/stem signals from the failing test cases."""
    loc = _FailingLocator()
    for tc in test_cases:
        located = _locate_failure(tc)  # (repo_relative_path, line) | None
        if located:
            path = located[0]
            loc.located_paths.add(path)
            segs = _path_segments(path)
            if segs:
                dirs = {s for s in segs[:-1] if s not in _NOISE_SEGMENTS}
                loc.dir_sets.append(dirs)
                loc.module_tokens |= dirs
                loc.stems.add(_strip_test_affixes(segs[-1]))
        for attr in ("class_name", "package_name"):
            for tok in _split_dotted(getattr(tc, attr, None)):
                if tok in _NOISE_SEGMENTS:
                    continue
                loc.module_tokens.add(tok)
                loc.stems.add(_strip_test_affixes(tok))
        name = getattr(tc, "test_name", None)
        if name:
            loc.stems.add(_strip_test_affixes(str(name).split("[")[0]))
    loc.stems.discard("")
    loc.module_tokens.discard("")
    return loc


def path_overlap(loc: _FailingLocator, changed_file: str) -> float:
    """Overlap score in ``0..1`` between a changed file and the failing test.

    Strongest → weakest: same-subject filename stem (1.0), package/module
    token appearing as a path segment (0.7), directory overlap with the
    located test file (up to 0.8), fuzzy stem containment (0.6).
    """
    segs = _path_segments(changed_file)
    if not segs:
        return 0.0
    cf_dirs = {s for s in segs[:-1] if s not in _NOISE_SEGMENTS}
    cf_stem = _strip_test_affixes(segs[-1])
    seg_set = set(segs)
    best = 0.0
    if cf_stem and cf_stem in loc.stems:
        return 1.0
    if loc.module_tokens & seg_set:
        best = max(best, 0.7)
    for ld in loc.dir_sets:
        if not ld:
            continue
        common = len(cf_dirs & ld)
        if common:
            best = max(best, min(1.0, common / len(ld)) * 0.8)
    if cf_stem:
        for t in loc.module_tokens:
            if len(cf_stem) >= 3 and (cf_stem in t or t in cf_stem):
                best = max(best, 0.6)
                break
    return round(best, 4)


def _normalize_supplied_commit(raw: Any) -> Optional[dict[str, Any]]:
    """Coerce a caller-supplied commit dict into the stored shape, or
    ``None`` when it carries no usable ``sha``."""
    if not isinstance(raw, dict):
        # Pydantic SuppliedCommit model → dump.
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        else:
            return None
    sha = str(raw.get("sha") or "").strip()
    if not sha:
        return None
    files_raw = raw.get("files") or []
    files = (
        [str(f)[:_FILE_PATH_CAP] for f in files_raw[:_FILES_PER_COMMIT_CAP]]
        if isinstance(files_raw, list)
        else []
    )
    return {
        "sha": sha[:64],
        "author": (str(raw["author"])[:255] if raw.get("author") else None),
        "message": _first_line(raw.get("message")),
        "files": files,
        "committed_at": (str(raw["committed_at"])[:40] if raw.get("committed_at") else None),
    }


def normalize_supplied_range(raw_commits: Any) -> list[dict[str, Any]]:
    """Normalize a caller-supplied commit list (bounded)."""
    if not isinstance(raw_commits, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_commits[:_MAX_COMMITS]:
        norm = _normalize_supplied_commit(raw)
        if norm:
            out.append(norm)
    return out


def _clean_ref(value: Any) -> Optional[str]:
    """A commit ref stripped + bounded to the ``String(64)`` column, or None."""
    ref = str(value or "").strip()
    return ref[:64] or None


def normalize_supplied_payload(raw: Any) -> tuple[Optional[str], Optional[str], list[dict[str, Any]]]:
    """Normalize either supplied wire shape into ``(base, head, commits)``.

    Two shapes are accepted (``SuppliedCommitRangeInput``):

    * the legacy bare list ``[{sha, ...}, ...]`` — no boundary refs, so
      ``base``/``head`` come back ``None``;
    * the boundary-carrying object ``{base, head, commits: [...]}`` (also
      spelled ``base_commit``/``head_commit`` or ``from_commit``/``to_commit``).

    Pydantic models are accepted alongside plain dicts because this runs on
    both the HTTP path (validated models) and the Celery path (JSON dicts).
    """
    if raw is None:
        return None, None, []
    if hasattr(raw, "model_dump") and not isinstance(raw, (list, dict)):
        raw = raw.model_dump()
    if isinstance(raw, list):
        return None, None, normalize_supplied_range(raw)
    if isinstance(raw, dict):
        base = _clean_ref(
            raw.get("base") or raw.get("base_commit") or raw.get("from_commit")
        )
        head = _clean_ref(
            raw.get("head") or raw.get("head_commit") or raw.get("to_commit")
        )
        return base, head, normalize_supplied_range(raw.get("commits"))
    return None, None, []


def _commit_html_url(ci_repo: Optional[str], api_base: Optional[str], sha: str) -> Optional[str]:
    """Best-effort deep link to a commit. ``None`` when we can't honestly
    build one (no repo known) — the UI then shows the SHA as plain text."""
    if not ci_repo or not sha:
        return None
    repo = ci_repo.strip().strip("/")
    base = (api_base or "").rstrip("/")
    if not base or "api.github.com" in base:
        return f"https://github.com/{repo}/commit/{sha}"
    # GitHub Enterprise: api base is ``https://host/api/v3`` → html root is host.
    host = base
    for suffix in ("/api/v3", "/api"):
        if host.endswith(suffix):
            host = host[: -len(suffix)]
            break
    return f"{host.rstrip('/')}/{repo}/commit/{sha}"


# ── Baseline / connector fetch ───────────────────────────────────────────────


def _anchor_predicates(run: TestRun) -> tuple:
    """Shared predicates for any run that can anchor a range: same project,
    not this run, finished, carries a commit hash, and PREDATES this run.

    The "predates" clause matters — without it a *later* run could be picked
    as the base and the compare would be inverted (or empty), which is a
    fabricated range dressed up as a real one.
    """
    preds = [
        TestRun.project_id == run.project_id,
        TestRun.id != run.id,
        TestRun.status != "IN_PROGRESS",
        TestRun.commit_hash.isnot(None),
        TestRun.commit_hash != "",
    ]
    pivot = getattr(run, "end_time", None) or getattr(run, "created_at", None)
    if pivot is not None:
        preds.append(func.coalesce(TestRun.end_time, TestRun.created_at) < pivot)
    return tuple(preds)


async def _pick_anchor(db: AsyncSession, run: TestRun, extra: tuple) -> Optional[TestRun]:
    """Most recent qualifying run on this run's branch, falling back to
    main/master. ``extra`` adds the anchor-class-specific predicates."""
    recency = func.coalesce(TestRun.end_time, TestRun.created_at).desc()
    base_preds = _anchor_predicates(run) + tuple(extra)
    if run.branch:
        result = await db.execute(
            select(TestRun)
            .where(*base_preds, func.lower(TestRun.branch) == run.branch.lower())
            .order_by(recency)
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row
    result = await db.execute(
        select(TestRun)
        .where(*base_preds, func.lower(TestRun.branch).in_(("main", "master")))
        .order_by(recency)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _last_green_run(db: AsyncSession, run: TestRun) -> Optional[TestRun]:
    """Most recent completed, all-green run for the same project on the same
    branch (falling back to main/master) that predates this run and carries a
    commit hash — the STRONGEST ``base`` for the range.

    Green = zero failed and zero broken. We require a ``commit_hash`` because
    it's the base of the compare; a green run without one can't anchor a range.
    """
    return await _pick_anchor(db, run, (
        func.coalesce(TestRun.failed_tests, 0) == 0,
        func.coalesce(TestRun.broken_tests, 0) == 0,
    ))


async def _last_completed_run(db: AsyncSession, run: TestRun) -> Optional[TestRun]:
    """Most recent completed prior run with a commit hash, REGARDLESS of
    pass/fail — the fallback anchor.

    Why this exists: requiring a fully-green baseline made the connector a
    dead end for exactly the projects that need attribution most. A project
    with a persistent flaky/failing tail never has an all-green run, so
    ``resolve_commit_range`` short-circuited to ``unavailable`` forever and
    the range corpus stayed empty.

    This anchor is genuinely weaker — "landed since" is only true relative to
    a run that was itself failing, so the range can contain changes that were
    already in the baseline's tree when it failed. That is why it is recorded
    as ``base_source=last_completed_run`` rather than being blended into the
    green case: consumers get a real range AND the fact that its boundary is
    a weak one.
    """
    return await _pick_anchor(db, run, ())


async def resolve_base_anchor(
    db: AsyncSession, run: TestRun,
) -> tuple[Optional[TestRun], str]:
    """Pick the range's base run + label WHICH anchor class it is.

    Order: green baseline (strong) → last completed run (weak) → none. The
    returned label is persisted verbatim as ``base_source``; it must never
    over-state the anchor actually used.
    """
    green = await _last_green_run(db, run)
    if green is not None:
        return green, BASE_SOURCE_GREEN
    fallback = await _last_completed_run(db, run)
    if fallback is not None:
        logger.info(
            "commit_range using weak base anchor",
            run_id=str(run.id),
            base_run_id=str(fallback.id),
            base_source=BASE_SOURCE_LAST_COMPLETED,
            reason="no all-green prior run on this branch",
        )
        return fallback, BASE_SOURCE_LAST_COMPLETED
    return None, BASE_SOURCE_UNAVAILABLE


async def _gh_get(
    url: str,
    headers: dict[str, str],
    params: Optional[dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> httpx.Response:
    """Single GET against the GitHub API. Patched in tests.

    Pass ``client`` to reuse one pooled connection for a whole fetch; without
    it a throwaway client is created (kept for one-off callers + the patch
    seam's default)."""
    if client is not None:
        return await client.get(url, headers=headers, params=params)
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as one_shot:
        return await one_shot.get(url, headers=headers, params=params)


@dataclass
class _ConnectorTarget:
    """Plain-value connector coordinates read out of the DB up front so the
    HTTP phase runs with no session open."""

    api_base: str
    repo: str
    pat: str


async def _connector_target(db: AsyncSession, run: TestRun) -> Optional[_ConnectorTarget]:
    """Resolve integration + PAT into plain values (DB reads only, no HTTP).
    ``None`` when the connector isn't usable (no integration / repo mismatch /
    no PAT)."""
    integration = await get_integration(db, run.project_id)
    if integration is None or not integration.enabled:
        return None
    api_base = (integration.api_base_url or "").rstrip("/")
    repo = f"{integration.repo_owner}/{integration.repo_name}"
    # Honor the repo match: a run's commit only makes sense against its repo.
    if run.ci_repo and run.ci_repo.strip().lower() != repo.lower():
        logger.debug("commit_range connector repo mismatch", run_id=str(run.id))
        return None

    from app.services import secret_service
    pat = await secret_service.read_secret(db, SECRET_SCOPE, _secret_key(run.project_id))
    if not pat:
        return None
    return _ConnectorTarget(api_base=api_base, repo=repo, pat=pat)


async def _fetch_commit_files(
    target: _ConnectorTarget,
    sha: str,
    headers: dict[str, str],
    client: Optional[httpx.AsyncClient],
) -> list[str]:
    """Changed files for one commit; empty list on any network failure."""
    try:
        detail = await _gh_get(
            f"{target.api_base}/repos/{target.repo}/commits/{sha}",
            headers,
            client=client,
        )
        if detail.status_code == 200 and detail.content:
            return [
                str(f.get("filename"))[:_FILE_PATH_CAP]
                for f in (detail.json().get("files") or [])
                if f.get("filename")
            ][:_FILES_PER_COMMIT_CAP]
    except httpx.HTTPError as exc:
        logger.debug("commit_range commit-detail fetch failed", sha=sha[:8], error=str(exc))
    return []


async def _fetch_connector_range(
    target: _ConnectorTarget, base: str, head: str, *, run_id: uuid.UUID,
) -> Optional[list[dict[str, Any]]]:
    """Fetch commits ``base..head`` + per-commit changed files from GitHub.

    Pure HTTP — takes plain connector coordinates, holds NO DB session. One
    pooled ``httpx.AsyncClient`` serves the compare call and the per-commit
    detail fan-out (gathered in chunks of ``_DETAIL_FETCH_CHUNK``). Returns
    the normalized commit list, or ``None`` when the target is SSRF-blocked
    or the compare call fails. Network failures never raise.
    """
    compare_url = f"{target.api_base}/repos/{target.repo}/compare/{base}...{head}"
    block = await _ssrf_block_reason(compare_url)
    if block:
        logger.warning("commit_range connector blocked unsafe target", run_id=str(run_id), reason=block)
        return None

    headers = {
        "Authorization": f"Bearer {target.pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "TestLookup/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        try:
            resp = await _gh_get(compare_url, headers, client=client)
        except httpx.HTTPError as exc:
            logger.warning("commit_range compare fetch failed", run_id=str(run_id), error=str(exc))
            return None
        if resp.status_code != 200:
            logger.warning("commit_range compare rejected", run_id=str(run_id), status_code=resp.status_code)
            return None

        body = resp.json() if resp.content else {}
        raw_commits = (body.get("commits") or [])[:_MAX_COMMITS]  # oldest→newest
        metas: list[dict[str, Any]] = []
        for rc in raw_commits:
            sha = str(rc.get("sha") or "").strip()
            if not sha:
                continue
            commit_meta = rc.get("commit") or {}
            author_meta = commit_meta.get("author") or {}
            metas.append({
                "sha": sha[:64],
                "author": (str(author_meta.get("name"))[:255] if author_meta.get("name") else None),
                "message": _first_line(commit_meta.get("message")),
                "files": [],
                "committed_at": (str(author_meta.get("date"))[:40] if author_meta.get("date") else None),
            })

        # The compare payload doesn't carry per-commit files — fetch commit
        # detail for the first N commits (bounded fan-out). Re-check the
        # resolved api_base host ONCE before the fan-out (DNS-rebinding
        # window since the compare check) rather than per commit.
        to_fetch = [m["sha"] for m in metas[:_file_fetch_limit()]]
        detail_block = await _ssrf_block_reason(target.api_base)
        if detail_block:
            logger.warning(
                "commit_range detail fetch blocked unsafe target",
                run_id=str(run_id),
                reason=detail_block,
            )
        elif to_fetch:
            files_by_sha: dict[str, list[str]] = {}
            for start in range(0, len(to_fetch), _DETAIL_FETCH_CHUNK):
                chunk = to_fetch[start:start + _DETAIL_FETCH_CHUNK]
                results = await asyncio.gather(
                    *(_fetch_commit_files(target, sha, headers, client) for sha in chunk)
                )
                files_by_sha.update(zip(chunk, results))
            for m in metas:
                m["files"] = files_by_sha.get(m["sha"], [])
    return metas


# ── Persistence (idempotent per run) ─────────────────────────────────────────


async def _get_row(db: AsyncSession, run_id: uuid.UUID) -> Optional[RunCommitRange]:
    result = await db.execute(
        select(RunCommitRange).where(RunCommitRange.run_id == run_id)
    )
    return result.scalar_one_or_none()


async def _upsert_range(
    db: AsyncSession,
    run: TestRun,
    *,
    base_commit: Optional[str],
    head_commit: Optional[str],
    base_run_id: Optional[uuid.UUID],
    source: str,
    commits: list[dict[str, Any]],
    base_source: str = BASE_SOURCE_UNAVAILABLE,
) -> Optional[RunCommitRange]:
    """Stage (insert or update) the range row for a run. Does NOT commit —
    the caller owns the transaction (transaction-boundary ratchet).

    Uses ``INSERT .. ON CONFLICT (run_id) DO UPDATE`` so a concurrent resolve
    (finalize + lazy GET racing) can't raise ``IntegrityError`` and poison the
    caller's session. Non-supplied writes carry a ``WHERE source != supplied``
    guard so a racing supplied insert is never clobbered (supplied wins).
    """
    # The label must never claim an anchor we didn't get. A base_source of
    # anything but ``unavailable`` requires an actual base ref, and an
    # unknown label degrades to ``unavailable`` rather than being persisted.
    if base_source not in BASE_SOURCES:
        base_source = BASE_SOURCE_UNAVAILABLE
    if not base_commit:
        base_source = BASE_SOURCE_UNAVAILABLE
    values: dict[str, Any] = {
        "base_commit": base_commit,
        "head_commit": head_commit,
        "base_run_id": base_run_id,
        "source": source,
        "base_source": base_source,
        "commits": commits,
        "resolved_at": datetime.now(timezone.utc),
    }
    stmt = pg_insert(RunCommitRange).values(
        id=uuid.uuid4(), run_id=run.id, project_id=run.project_id, **values,
    )
    if source == SOURCE_SUPPLIED:
        stmt = stmt.on_conflict_do_update(index_elements=["run_id"], set_=values)
    else:
        stmt = stmt.on_conflict_do_update(
            index_elements=["run_id"],
            set_=values,
            where=RunCommitRange.source != SOURCE_SUPPLIED,
        )
    await db.execute(stmt)
    return await _get_row(db, run.id)


async def store_supplied_range(
    db: AsyncSession, run: TestRun, raw_commits: Any,
) -> Optional[RunCommitRange]:
    """Persist a caller-supplied commit range (US-8.1 air-gapped path).

    Accepts either supplied wire shape (bare list, or ``{base, head,
    commits}``). When the caller sends a ``base`` we now PERSIST it: the
    original implementation hard-coded ``base_commit=None``, which threw away
    the one thing that makes a supplied row reconstructible — and therefore
    usable as test-impact training data. A caller who only sends the bare
    list still gets ``base_commit=None`` / ``base_source=unavailable``,
    honestly recorded rather than guessed at.

    Staged under the caller's session (ingest owns the commit). Idempotent
    per run. Returns ``None`` (no row) when the supplied list is empty.
    """
    base, head, commits = normalize_supplied_payload(raw_commits)
    if not commits:
        return None
    head = head or run.commit_hash or commits[-1]["sha"]
    # A base equal to head bounds an empty range — it is not a usable anchor,
    # so don't dress it up as one.
    if base and base == head:
        base = None
    return await _upsert_range(
        db, run,
        base_commit=base,
        head_commit=head,
        base_run_id=None,  # a supplied base is a raw ref, not one of our runs
        source=SOURCE_SUPPLIED,
        commits=commits,
        base_source=BASE_SOURCE_SUPPLIED if base else BASE_SOURCE_UNAVAILABLE,
    )


def within_resolve_cooldown(resolved_at: Optional[datetime]) -> bool:
    """True when a prior resolution attempt is recent enough that re-running
    the connector would be rate-limit burn, not new information."""
    if resolved_at is None:
        return False
    ts = resolved_at if resolved_at.tzinfo else resolved_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts) < _RERESOLVE_COOLDOWN


def needs_resolution(serialized: dict[str, Any]) -> bool:
    """Whether a serialized range (from ``get_commit_range``) warrants a lazy
    resolution attempt: no row yet, or an ``unavailable`` row whose last
    attempt is older than the cooldown. The GET handlers gate on this so a
    range-less run can't be turned into a GitHub-call amplifier."""
    if serialized.get("available"):
        return False
    if serialized.get("source") not in (None, SOURCE_UNAVAILABLE):
        return False
    raw = serialized.get("resolved_at")
    if not raw:
        return True
    try:
        resolved = datetime.fromisoformat(str(raw))
    except ValueError:
        return True
    return not within_resolve_cooldown(resolved)


async def resolve_commit_range(db: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    """Resolve + STAGE the commit range for a run (stage-only, no commit).

    Called from finalize (via ``_run_isolated``, which owns the commit) and the
    endpoint's lazy path (the router owns the commit on a dedicated write
    session). Priority: an existing ``supplied`` (or non-empty connector) row
    is kept as-is; an ``unavailable`` row younger than the 6h cooldown skips;
    otherwise try the connector; otherwise stage an honest ``unavailable`` row.

    Session discipline (audit 2026-07 #11): all DB reads happen on a
    short-lived internal session that is CLOSED before any HTTP; the GitHub
    fan-out then runs with no session/transaction open; only the final upsert
    touches the caller's session (which owns the commit). Network/DB failures
    return ``{"error": ...}``; programming errors surface to the caller's
    isolated-step handler.
    """
    try:
        # ── Phase 1: DB reads → plain values, on a short-lived session ──────
        target: Optional[_ConnectorTarget] = None
        base_commit: Optional[str] = None
        base_run_id: Optional[uuid.UUID] = None
        base_source: str = BASE_SOURCE_UNAVAILABLE
        async with AsyncSessionLocal() as read_db:
            result = await read_db.execute(select(TestRun).where(TestRun.id == run_id))
            run = result.scalar_one_or_none()
            if run is None:
                return {"skipped": "run_not_found"}

            existing = await _get_row(read_db, run_id)
            if existing is not None:
                if existing.source == SOURCE_SUPPLIED or existing.commits:
                    # Supplied wins; a non-empty connector row is already resolved.
                    return {"skipped": "already_resolved", "source": existing.source}
                if within_resolve_cooldown(existing.resolved_at):
                    return {"skipped": "cooldown", "source": existing.source}

            head = run.commit_hash
            if head and await _post_allowed(read_db):
                base_run, base_source = await resolve_base_anchor(read_db, run)
                if base_run is not None:
                    base_commit = base_run.commit_hash
                    base_run_id = base_run.id
                if base_commit and base_commit != head:
                    target = await _connector_target(read_db, run)
                elif base_commit:
                    # Base == head bounds an empty range; the anchor is real
                    # but useless, so don't persist a strong-looking label.
                    base_source = BASE_SOURCE_UNAVAILABLE

        # ── Phase 2: HTTP fan-out — no session open ─────────────────────────
        commits: Optional[list[dict[str, Any]]] = None
        if target is not None and base_commit and head:
            commits = await _fetch_connector_range(
                target, base_commit, head, run_id=run_id,
            )

        # ── Phase 3: stage on the caller's session (caller owns the commit) ─
        if commits:
            await _upsert_range(
                db, run,
                base_commit=base_commit,
                head_commit=head,
                base_run_id=base_run_id,
                source=SOURCE_CONNECTOR,
                commits=commits,
                base_source=base_source,
            )
            return {
                "source": SOURCE_CONNECTOR,
                "base_source": base_source,
                "commit_count": len(commits),
            }

        await _upsert_range(
            db, run,
            base_commit=base_commit,
            head_commit=head,
            base_run_id=base_run_id,
            source=SOURCE_UNAVAILABLE,
            commits=[],
            base_source=base_source,
        )
        return {"source": SOURCE_UNAVAILABLE, "base_source": base_source}
    except (httpx.HTTPError, SQLAlchemyError) as exc:
        # Best-effort for infra faults only — programming errors must surface.
        logger.warning("commit_range resolve failed", run_id=str(run_id), error=str(exc))
        return {"error": str(exc)}


# ── Read model ───────────────────────────────────────────────────────────────


def _base_source_of(row: Optional[RunCommitRange]) -> str:
    """The row's anchor label, defaulting to ``unavailable`` for rows written
    before migration 0116 (or when no row exists)."""
    if row is None:
        return BASE_SOURCE_UNAVAILABLE
    value = getattr(row, "base_source", None)
    return value if value in BASE_SOURCES else BASE_SOURCE_UNAVAILABLE


def _serialize_range(row: Optional[RunCommitRange], run: TestRun) -> dict[str, Any]:
    """Shape the range for the API. Honest ``available`` flag."""
    base_source = _base_source_of(row)
    if row is None or row.source == SOURCE_UNAVAILABLE or not row.commits:
        return {
            "run_id": str(run.id),
            "available": False,
            "source": row.source if row is not None else SOURCE_UNAVAILABLE,
            "base_source": base_source,
            "base_anchor_is_strong": base_source in STRONG_BASE_SOURCES,
            "base_commit": row.base_commit if row is not None else None,
            "head_commit": (row.head_commit if row is not None else None) or run.commit_hash,
            "base_run_id": str(row.base_run_id) if row is not None and row.base_run_id else None,
            "commits": [],
            "resolved_at": row.resolved_at.isoformat() if row is not None and row.resolved_at else None,
        }
    api_base = None  # html-url derivation only needs ci_repo for github.com
    commits = [
        {**c, "commit_url": _commit_html_url(run.ci_repo, api_base, c.get("sha", ""))}
        for c in row.commits
    ]
    return {
        "run_id": str(run.id),
        "available": True,
        "source": row.source,
        "base_source": base_source,
        # Surfaced so the UI can caveat a weak anchor instead of presenting
        # "commits since last green" when that is not what happened.
        "base_anchor_is_strong": base_source in STRONG_BASE_SOURCES,
        "base_commit": row.base_commit,
        "head_commit": row.head_commit or run.commit_hash,
        "base_run_id": str(row.base_run_id) if row.base_run_id else None,
        "commits": commits,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


async def get_commit_range(db: AsyncSession, run: TestRun) -> dict[str, Any]:
    """Return the persisted commit range for a run (read-only)."""
    row = await _get_row(db, run.id)
    return _serialize_range(row, run)


# ── Suspect ranking (US-8.2) ─────────────────────────────────────────────────


async def _target_test_cases(
    db: AsyncSession,
    run: TestRun,
    *,
    cluster_id: Optional[str],
    fingerprint: Optional[str],
) -> list[TestCase]:
    """Resolve the failing test case(s) to attribute.

    ``cluster_id`` → the cluster's member test cases; ``fingerprint`` → the
    single matching test case; neither → every failed/broken case in the run.
    """
    if fingerprint:
        result = await db.execute(
            select(TestCase).where(
                TestCase.test_run_id == run.id,
                TestCase.test_fingerprint == fingerprint,
            )
        )
        return list(result.scalars().all())

    if cluster_id:
        cluster_result = await db.execute(
            select(FailureCluster).where(
                FailureCluster.test_run_id == run.id,
                FailureCluster.cluster_id == cluster_id,
            )
        )
        cluster = cluster_result.scalar_one_or_none()
        if cluster is None:
            return []
        member_ids: list[uuid.UUID] = []
        for raw in (cluster.member_test_ids or []):
            try:
                member_ids.append(uuid.UUID(str(raw)))
            except (ValueError, TypeError):
                continue
        if not member_ids:
            return []
        result = await db.execute(
            select(TestCase).where(TestCase.id.in_(member_ids))
        )
        return list(result.scalars().all())

    # No selector — attribute against every failure in the run.
    result = await db.execute(
        select(TestCase).where(TestCase.test_run_id == run.id)
    )
    return [
        tc for tc in result.scalars().all()
        if _status_bucket(tc.status) in ("failed", "broken")
    ]


def score_commits(
    commits: list[dict[str, Any]], loc: _FailingLocator,
) -> list[dict[str, Any]]:
    """Deterministically score + rank commits as suspects (pure).

    ``commits`` is oldest→newest. Score = ``0.70*overlap + 0.20*recency +
    0.10*author_prior`` (path overlap dominant, recency the tiebreak). Every
    result carries an inspectable rationale (which files overlapped, the
    recency rank, whether the author touched the module elsewhere).
    """
    n = len(commits)
    if n == 0:
        return []

    # Per-commit overlap + overlapping-file list.
    overlaps: list[float] = []
    overlapping_files: list[list[str]] = []
    for c in commits:
        files = c.get("files") or []
        scored = [(f, path_overlap(loc, f)) for f in files]  # score once per file
        matched = [f for f, s in scored if s > 0.0]
        best = max((s for _f, s in scored), default=0.0)
        overlaps.append(best)
        overlapping_files.append(matched)

    # Which authors touched the module (overlap>0) more than once in the range.
    author_overlap_counts: dict[str, int] = {}
    for c, ov in zip(commits, overlaps):
        if ov > 0 and c.get("author"):
            author_overlap_counts[c["author"]] = author_overlap_counts.get(c["author"], 0) + 1

    ranked: list[dict[str, Any]] = []
    for idx, c in enumerate(commits):
        overlap = overlaps[idx]
        recency = (idx + 1) / n  # newest (last) → 1.0
        author = c.get("author")
        author_prior = 1.0 if author and author_overlap_counts.get(author, 0) > 1 else 0.0
        score = round(
            100.0 * (_W_OVERLAP * overlap + _W_RECENCY * recency + _W_AUTHOR * author_prior),
            1,
        )
        ranked.append({
            "sha": c.get("sha"),
            "author": author,
            "message": _first_line(c.get("message")),
            "committed_at": c.get("committed_at"),
            "score": score,
            "rationale": {
                "overlapping_files": overlapping_files[idx],
                "overlap_score": round(overlap, 3),
                "recency_rank": n - idx,  # 1 = most recent
                "author_touched_module_before": bool(author_prior),
                "changed_file_count": len(c.get("files") or []),
            },
        })
    # Highest score first; recency then sha for a stable, deterministic order.
    ranked.sort(key=lambda r: (-r["score"], -r["rationale"]["recency_rank"], r["sha"] or ""))
    return ranked


# Monorepo caveat surfaced verbatim in the API + UI (owner ≠ author).
MONOREPO_CAVEAT = (
    "Suspects, not culprits: these commits changed files near the failing "
    "test. In a monorepo the file owner is often not the commit author — "
    "treat each as a lead to inspect, not blame."
)


async def rank_suspects(
    db: AsyncSession,
    run: TestRun,
    *,
    cluster_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> dict[str, Any]:
    """Rank the range's commits as suspects for a failing cluster/test.

    Returns an honest ``available: False`` when there's no commit range, and
    a ranked ``suspects`` list (with per-commit rationale) otherwise.
    """
    row = await _get_row(db, run.id)
    if row is None or not row.commits:
        return {
            "run_id": str(run.id),
            "cluster_id": cluster_id,
            "fingerprint": fingerprint,
            "available": False,
            "reason": "no_commit_range",
            "caveat": MONOREPO_CAVEAT,
            "suspects": [],
        }

    test_cases = await _target_test_cases(
        db, run, cluster_id=cluster_id, fingerprint=fingerprint,
    )
    loc = build_locator(test_cases)
    suspects = score_commits(list(row.commits), loc)
    # Attach deep links now that we know the repo.
    for s in suspects:
        s["commit_url"] = _commit_html_url(run.ci_repo, None, s.get("sha", ""))

    return {
        "run_id": str(run.id),
        "cluster_id": cluster_id,
        "fingerprint": fingerprint,
        "available": True,
        "source": row.source,
        "base_source": _base_source_of(row),
        "base_anchor_is_strong": _base_source_of(row) in STRONG_BASE_SOURCES,
        "has_location_signal": loc.has_signal,
        "target_test_count": len(test_cases),
        "base_commit": row.base_commit,
        "head_commit": row.head_commit or run.commit_hash,
        "caveat": MONOREPO_CAVEAT,
        "suspects": suspects,
    }


# ── TIA readiness (Epic 10 go/no-go signal) ──────────────────────────────────
#
# ``run_commit_ranges`` is the would-be training corpus for test-impact
# analysis: a path→test correlation model learns from "these files changed,
# these tests then failed". This block answers ONE question, per project, from
# that project's OWN rows — never a fleet average:
#
#     is there enough usable commit-range data here to train a model yet?
#
# A row is only *usable* corpus when it (a) actually resolved (``source`` is
# ``connector`` or ``supplied``, not ``unavailable``) and (b) carries at least
# one commit with a non-empty ``files`` list — a commit with ``files: []``
# names a SHA and contributes zero path evidence.
#
# The thresholds below are a judgement call, published in the response so the
# caller can disagree with them: they are a FLOOR beneath which training is
# obviously premature, not a guarantee that training above them will work.

# Minimum runs carrying a usable range before a correlation model is worth
# fitting at all — under a few dozen, per-path evidence is single-digit.
TIA_MIN_USABLE_RUNS = 30
# Minimum calendar span those runs must cover. 30 usable runs from one
# afternoon describe one day's code, not a project's change patterns.
TIA_MIN_HISTORY_DAYS = 14
# Minimum distinct changed paths — the model's feature space. Fewer than this
# and it can only ever say "the whole repo is one blob".
TIA_MIN_DISTINCT_PATHS = 25
# Default lookback for the readiness scan.
TIA_READINESS_WINDOW_DAYS = 90
# Rows read per scan. Bounded so a huge project can't turn a metrics GET into
# an unbounded JSONB read; when it bites, ``scan_capped`` says so and every
# count is a floor (real data >= reported).
TIA_READINESS_SCAN_CAP = 1000
# Distinct paths tracked before we stop growing the set (memory bound).
TIA_MAX_TRACKED_PATHS = 20_000


def _as_dt(value: Any) -> Optional[datetime]:
    """Coerce a stored timestamp to tz-aware UTC, or ``None``."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _resolve_tia_availability(
    *,
    runs_scanned: int,
    runs_with_range: int,
    usable_runs: int,
    history_days: float,
    distinct_paths: int,
) -> tuple[bool, Optional[str]]:
    """``(available, insufficient_data_reason)`` for the readiness metric.

    Reasons are ordered most-fundamental-first so the caller is told the one
    thing to fix next rather than a list. Same contract as
    ``value_metrics_service.resolve_availability``.
    """
    if runs_scanned == 0:
        return False, "no commit ranges have been resolved for this project yet"
    if runs_with_range == 0:
        return False, (
            f"none of the {runs_scanned} resolved ranges contain any commits "
            "(no VCS connector configured, and no commit range supplied on ingest)"
        )
    if usable_runs == 0:
        return False, (
            f"{runs_with_range} runs have a commit range but none carry per-commit "
            "changed files — a range without file paths cannot train a path model"
        )
    if usable_runs < TIA_MIN_USABLE_RUNS:
        return False, (
            f"only {usable_runs} of {runs_scanned} runs carry a usable commit range "
            f"(need {TIA_MIN_USABLE_RUNS})"
        )
    if history_days < TIA_MIN_HISTORY_DAYS:
        return False, (
            f"usable commit ranges span {history_days} days "
            f"(need {TIA_MIN_HISTORY_DAYS})"
        )
    if distinct_paths < TIA_MIN_DISTINCT_PATHS:
        return False, (
            f"only {distinct_paths} distinct changed paths observed "
            f"(need {TIA_MIN_DISTINCT_PATHS})"
        )
    return True, None


def compute_tia_readiness(
    rows: Any,
    *,
    window_days: int = TIA_READINESS_WINDOW_DAYS,
    scan_cap: int = TIA_READINESS_SCAN_CAP,
) -> dict[str, Any]:
    """Pure readiness math over ONE project's ``run_commit_ranges`` rows.

    ``rows`` is any iterable of objects exposing ``resolved_at``, ``source``,
    ``base_source`` and ``commits`` (SQLAlchemy ``Row`` objects satisfy this
    directly, which is why the DB wrapper below passes them straight through).

    Mirrors ``value_metrics_service.resolve_availability``: when the corpus
    isn't there yet we return ``available: false`` plus a concrete
    ``insufficient_data_reason`` naming what is missing, never a number that
    implies more than the data supports.
    """
    source_breakdown: dict[str, int] = {}
    base_source_breakdown: dict[str, int] = {}
    runs_scanned = 0
    runs_with_range = 0
    usable_runs = 0
    runs_with_strong_anchor = 0
    commits_total = 0
    commits_with_files = 0
    distinct_paths: set[str] = set()
    paths_capped = False
    first_usable: Optional[datetime] = None
    last_usable: Optional[datetime] = None

    for row in rows:
        runs_scanned += 1
        source = getattr(row, "source", None) or SOURCE_UNAVAILABLE
        base_source = getattr(row, "base_source", None)
        if base_source not in BASE_SOURCES:
            base_source = BASE_SOURCE_UNAVAILABLE
        source_breakdown[source] = source_breakdown.get(source, 0) + 1
        base_source_breakdown[base_source] = base_source_breakdown.get(base_source, 0) + 1

        commits = getattr(row, "commits", None) or []
        if source == SOURCE_UNAVAILABLE or not commits:
            continue
        runs_with_range += 1

        row_has_files = False
        for commit in commits:
            if not isinstance(commit, dict):
                continue
            commits_total += 1
            files = commit.get("files") or []
            if not files:
                continue
            commits_with_files += 1
            row_has_files = True
            for path in files:
                if len(distinct_paths) >= TIA_MAX_TRACKED_PATHS:
                    paths_capped = True
                    break
                distinct_paths.add(str(path))

        if not row_has_files:
            continue
        usable_runs += 1
        if base_source in STRONG_BASE_SOURCES:
            runs_with_strong_anchor += 1
        resolved = _as_dt(getattr(row, "resolved_at", None))
        if resolved is not None:
            first_usable = resolved if first_usable is None else min(first_usable, resolved)
            last_usable = resolved if last_usable is None else max(last_usable, resolved)

    history_days = 0.0
    if first_usable is not None and last_usable is not None:
        history_days = round((last_usable - first_usable).total_seconds() / 86400.0, 2)

    available, reason = _resolve_tia_availability(
        runs_scanned=runs_scanned,
        runs_with_range=runs_with_range,
        usable_runs=usable_runs,
        history_days=history_days,
        distinct_paths=len(distinct_paths),
    )

    return {
        "available": available,
        "insufficient_data_reason": reason,
        "window_days": window_days,
        # Corpus size
        "runs_scanned": runs_scanned,
        "runs_with_range": runs_with_range,
        "usable_runs": usable_runs,
        "runs_with_strong_base_anchor": runs_with_strong_anchor,
        # Corpus depth
        "commits_total": commits_total,
        "commits_with_files": commits_with_files,
        "file_detail_coverage": (
            round(commits_with_files / commits_total, 4) if commits_total else 0.0
        ),
        # Corpus breadth
        "distinct_paths": len(distinct_paths),
        "distinct_paths_capped": paths_capped,
        # Corpus span
        "history_days": history_days,
        "first_usable_range_at": first_usable.isoformat() if first_usable else None,
        "last_usable_range_at": last_usable.isoformat() if last_usable else None,
        # Provenance mix — a corpus anchored mostly on weak bases is real data
        # with a caveat, and the caller is entitled to see the split.
        "source_breakdown": source_breakdown,
        "base_source_breakdown": base_source_breakdown,
        "thresholds": {
            "min_usable_runs": TIA_MIN_USABLE_RUNS,
            "min_history_days": TIA_MIN_HISTORY_DAYS,
            "min_distinct_paths": TIA_MIN_DISTINCT_PATHS,
        },
        "scan_cap": scan_cap,
        "scan_capped": runs_scanned >= scan_cap,
        "usable_definition": (
            "a run whose commit range resolved (source connector|supplied) and "
            "whose range carries at least one commit with a non-empty "
            "changed-file list"
        ),
    }


async def get_tia_readiness(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    days: int = TIA_READINESS_WINDOW_DAYS,
) -> dict[str, Any]:
    """Per-project TIA-corpus readiness (read-only).

    Always scoped to ONE project — readiness is a statement about this
    project's own change/failure history, and a cross-project average would be
    meaningless for the go/no-go it exists to inform.

    Served by ``ix_run_commit_ranges_project_resolved`` (migration 0116).
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(
            RunCommitRange.resolved_at,
            RunCommitRange.source,
            RunCommitRange.base_source,
            RunCommitRange.commits,
        )
        .where(
            RunCommitRange.project_id == project_id,
            RunCommitRange.resolved_at >= since,
        )
        .order_by(RunCommitRange.resolved_at.desc())
        .limit(TIA_READINESS_SCAN_CAP)
    )
    readiness = compute_tia_readiness(result.all(), window_days=days)
    return {"project_id": str(project_id), **readiness}
