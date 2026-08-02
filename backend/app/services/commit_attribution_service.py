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

# Bound the connector fetch — a compare across a stale baseline could span
# thousands of commits; ranking beyond ~100 adds noise, not signal.
_MAX_COMMITS = 100
# Per-commit changed-file detail is one API call each; cap the fan-out. 25 is
# deliberate: per the module docstring the ranking is a path-overlap heuristic
# whose signal decays fast across a long range — detail beyond the first 25
# commits adds API-rate-limit burn, not ranking signal.
_MAX_COMMIT_FILE_FETCHES = 25
# Concurrent per-commit detail fetches per gather batch.
_DETAIL_FETCH_CHUNK = 8
_HTTP_TIMEOUT = 10.0

# An ``unavailable`` row younger than this suppresses re-resolution — without
# it every GET /commit-range on a range-less run re-ran the connector (up to
# 1 + _MAX_COMMIT_FILE_FETCHES GitHub calls per request; rate-limit burn).
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


async def _last_green_run(db: AsyncSession, run: TestRun) -> Optional[TestRun]:
    """Most recent completed, all-green run for the same project on the same
    branch (falling back to main/master) that predates this run and carries a
    commit hash — the ``base`` of the range.

    Green = zero failed and zero broken. We require a ``commit_hash`` because
    it's the base of the compare; a green run without one can't anchor a range.
    """
    recency = func.coalesce(TestRun.end_time, TestRun.created_at).desc()
    green = (
        TestRun.project_id == run.project_id,
        TestRun.id != run.id,
        TestRun.status != "IN_PROGRESS",
        func.coalesce(TestRun.failed_tests, 0) == 0,
        func.coalesce(TestRun.broken_tests, 0) == 0,
        TestRun.commit_hash.isnot(None),
        TestRun.commit_hash != "",
    )
    # Prefer the same branch as this run.
    if run.branch:
        result = await db.execute(
            select(TestRun)
            .where(*green, func.lower(TestRun.branch) == run.branch.lower())
            .order_by(recency)
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row
    result = await db.execute(
        select(TestRun)
        .where(*green, func.lower(TestRun.branch).in_(("main", "master")))
        .order_by(recency)
        .limit(1)
    )
    return result.scalar_one_or_none()


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
        to_fetch = [m["sha"] for m in metas[:_MAX_COMMIT_FILE_FETCHES]]
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
) -> Optional[RunCommitRange]:
    """Stage (insert or update) the range row for a run. Does NOT commit —
    the caller owns the transaction (transaction-boundary ratchet).

    Uses ``INSERT .. ON CONFLICT (run_id) DO UPDATE`` so a concurrent resolve
    (finalize + lazy GET racing) can't raise ``IntegrityError`` and poison the
    caller's session. Non-supplied writes carry a ``WHERE source != supplied``
    guard so a racing supplied insert is never clobbered (supplied wins).
    """
    values: dict[str, Any] = {
        "base_commit": base_commit,
        "head_commit": head_commit,
        "base_run_id": base_run_id,
        "source": source,
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
    """Persist a caller-supplied commit list (US-8.1 air-gapped path).

    Staged under the caller's session (ingest owns the commit). Idempotent
    per run. Returns ``None`` (no row) when the supplied list is empty.
    """
    commits = normalize_supplied_range(raw_commits)
    if not commits:
        return None
    head = run.commit_hash or (commits[-1]["sha"] if commits else None)
    return await _upsert_range(
        db, run,
        base_commit=None,  # air-gapped callers push the list, not the base ref
        head_commit=head,
        base_run_id=None,
        source=SOURCE_SUPPLIED,
        commits=commits,
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
                base_run = await _last_green_run(read_db, run)
                if base_run is not None:
                    base_commit = base_run.commit_hash
                    base_run_id = base_run.id
                if base_commit and base_commit != head:
                    target = await _connector_target(read_db, run)

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
            )
            return {"source": SOURCE_CONNECTOR, "commit_count": len(commits)}

        await _upsert_range(
            db, run,
            base_commit=base_commit,
            head_commit=head,
            base_run_id=base_run_id,
            source=SOURCE_UNAVAILABLE,
            commits=[],
        )
        return {"source": SOURCE_UNAVAILABLE}
    except (httpx.HTTPError, SQLAlchemyError) as exc:
        # Best-effort for infra faults only — programming errors must surface.
        logger.warning("commit_range resolve failed", run_id=str(run_id), error=str(exc))
        return {"error": str(exc)}


# ── Read model ───────────────────────────────────────────────────────────────


def _serialize_range(row: Optional[RunCommitRange], run: TestRun) -> dict[str, Any]:
    """Shape the range for the API. Honest ``available`` flag."""
    if row is None or row.source == SOURCE_UNAVAILABLE or not row.commits:
        return {
            "run_id": str(run.id),
            "available": False,
            "source": row.source if row is not None else SOURCE_UNAVAILABLE,
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
        "has_location_signal": loc.has_signal,
        "target_test_count": len(test_cases),
        "base_commit": row.base_commit,
        "head_commit": row.head_commit or run.commit_hash,
        "caveat": MONOREPO_CAVEAT,
        "suspects": suspects,
    }
