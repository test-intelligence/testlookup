"""
GitLab integration — PMF backlog Epic 3 (US-3.1 / US-3.2).

Outbound integration mirroring ``github_checks_service`` +
``github_pr_comment_service`` for GitLab (self-managed or gitlab.com). Two
surfaces, both posted after a run finalizes:

* **Sticky MR note (US-3.2)** — one note on the run's merge request
  (``TestRun.pr_number`` == ``CI_MERGE_REQUEST_IID``), keyed by a hidden
  HTML-comment marker so re-runs UPDATE the same note instead of spamming
  the MR. The note body + newly-failed/known-flaky/fixed partition is
  **reused verbatim** from ``github_pr_comment_service`` — same baseline
  selection, same flaky set, same classification, same renderer — so the
  GitLab surface never disagrees with the GitHub one.

* **Commit status (US-3.2)** — a pipeline "check" on the run's
  ``commit_hash`` reflecting the run verdict (green → ``success``, any
  failure → ``failed``).

Design notes (mirrors GitHub)
-----------------------------

* **Per-project config** — the ``gitlab_integrations`` table holds base
  URL + ``group/project`` path + modes. The PAT lives in
  ``secret_service`` under scope ``gitlab_integration``.

* **Hard offline gate** — ``AI_OFFLINE_MODE`` is the kill switch for every
  outbound call, respected regardless of feature-flag state. The ``gitlab``
  feature flag gates the whole subsystem on top of that.

* **SSRF egress guard** — ``base_url`` is QA_LEAD-configurable and we send
  the PAT to it, so ``_ssrf_block_reason`` (REUSED from
  ``github_checks_service`` — no duplicated CIDR logic) blocks
  loopback/link-local/unspecified targets while allowing RFC1918 so a
  self-managed GitLab on a private network still works.

* **Repo-match guard** — MR IIDs are project-scoped, so the run's
  ``ci_repo`` (``CI_PROJECT_PATH``) must equal the configured
  ``project_path`` (case-insensitive) before we post an MR note.

* **Never raises** from the public entry points — ingestion triggers these
  after finalize and a GitLab outage must not block persistence. Errors
  land in ``last_error`` for Integration Health.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.http_client import http_verify
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import GitLabIntegration, Project, TestRun, User
# SSRF guard REUSED from the GitHub checks service — do NOT duplicate the
# CIDR logic; a single egress policy covers both integrations.
from app.services.github_checks_service import _ssrf_block_reason
from app.services.run_compare_service import _load_test_rows, _status_bucket

logger = structlog.get_logger("services.gitlab_integration")

# ``secret_service`` scope under which the PAT is stored. Consumers must use
# the exact string — it becomes part of the encryption key namespace.
SECRET_SCOPE = "gitlab_integration"

# Marker prefix — the first line of every MR note we own. The project id
# suffix lets two TestLookup projects report onto the same MR without
# clobbering each other's note. Distinct from the GitHub PR-comment marker so
# the two surfaces never collide.
_MR_MARKER_PREFIX = "<!-- testlookup-mr-summary:"

# MR-note listing pagination: up to 3 pages of 100.
_NOTE_PAGE_SIZE = 100
_NOTE_MAX_PAGES = 3

_HTTP_TIMEOUT = 10.0


def _secret_key(project_id: uuid.UUID | str) -> str:
    return f"project:{project_id}:pat"


def _marker(project_id: uuid.UUID | str) -> str:
    return f"{_MR_MARKER_PREFIX}{project_id} -->"


def _api_root(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/v4"


def _normalize_base_url(base_url: Optional[str]) -> str:
    """Normalize a configured base URL: default, strip, no trailing slash,
    and ALWAYS carry an explicit http(s) scheme.

    The router's write schema already rejects schemeless values (422), but
    this is the belt for any other caller: a schemeless host has no urlparse
    netloc, which silently no-ops the SSRF egress guard downstream.
    """
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return "https://gitlab.com"
    if not cleaned.lower().startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    return cleaned


def _project_ref(project_path: str) -> str:
    """URL-encode a ``group/project`` path (``/`` → ``%2F``) for the API path.
    A numeric project id passes through unchanged."""
    return quote(str(project_path).strip(), safe="")


# ── Feature-flag + offline gate ────────────────────────────────────────────


async def _post_allowed(
    db: Optional[AsyncSession] = None,
    *,
    project_id: Optional[uuid.UUID] = None,
    any_scope: bool = False,
) -> bool:
    """Compound gate: ``gitlab`` feature flag ON and offline-mode OFF.

    ``AI_OFFLINE_MODE`` is the hard kill switch for every outbound
    integration — respected regardless of feature-flag state so air-gapped
    customers don't accidentally egress traffic when an admin toggles the
    ``gitlab`` flag on.

    The flag is asked for the project whose GitLab integration would be
    called: a project allow-list on ``gitlab`` turned every post off while it
    was asked without one. The run-driven entry points exit early with
    ``any_scope=True`` (off for everyone) and ask again with the run's project
    as soon as they have loaded the run, before any other work.
    """
    if settings.AI_OFFLINE_MODE:
        return False
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled(
            "gitlab", db=db, project_id=project_id, any_scope=any_scope,
        )
    except Exception as exc:
        logger.debug("gitlab flag check failed", error=str(exc))
        return False


# ── Config CRUD ────────────────────────────────────────────────────────────


async def get_integration(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[GitLabIntegration]:
    result = await db.execute(
        select(GitLabIntegration).where(GitLabIntegration.project_id == project_id)
    )
    return result.scalar_one_or_none()


async def upsert_integration(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: User,
    enabled: bool,
    base_url: str,
    project_path: str,
    mr_comment_mode: Optional[str] = None,
    commit_status_enabled: Optional[bool] = None,
    token: Optional[str] = None,
) -> GitLabIntegration:
    """Create or update a project's GitLab integration.

    ``token`` semantics (write-only, per the config contract):

    * ``None``   — leave the existing secret alone.
    * ``""``     — clear the stored secret + flip ``has_pat=False``.
    * any value  — upsert the secret + flip ``has_pat=True``.

    Stage-only: the router handler owns the single ``commit`` (transaction
    ratchet) so the integration row, secret row, and audit entry land in one
    transaction.
    """
    row = await get_integration(db, project_id)
    if row is None:
        row = GitLabIntegration(
            project_id=project_id,
            base_url=_normalize_base_url(base_url),
            project_path=(project_path or "").strip(),
            enabled=enabled,
            updated_by_user_id=actor.id,
        )
        db.add(row)
    else:
        row.base_url = _normalize_base_url(base_url)
        row.project_path = (project_path or "").strip()
        row.enabled = enabled
        row.updated_by_user_id = actor.id
        # updated_at is owned by the column's onupdate=func.now().

    if mr_comment_mode is not None:
        row.mr_comment_mode = mr_comment_mode
    if commit_status_enabled is not None:
        row.commit_status_enabled = commit_status_enabled

    if token is not None:
        from app.services import secret_service
        if token == "":
            await secret_service.store_secret(
                db, SECRET_SCOPE, _secret_key(project_id), "", actor.id,
            )
            row.has_pat = False
        else:
            await secret_service.store_secret(
                db, SECRET_SCOPE, _secret_key(project_id), token, actor.id,
            )
            row.has_pat = True

    await db.flush()

    # Audit — staged under the router session; the router owns the commit.
    try:
        from app.models.postgres import SettingsAuditLog
        entry = SettingsAuditLog(
            setting_key=f"gitlab_integration:{project_id}",
            action="update",
            actor_id=actor.id,
            actor_name=getattr(actor, "username", None) or getattr(actor, "email", None),
            changed_fields=["base_url", "project_path", "enabled"]
            + (["mr_comment_mode"] if mr_comment_mode is not None else [])
            + (["commit_status_enabled"] if commit_status_enabled is not None else [])
            + (["token"] if token is not None else []),
        )
        db.add(entry)
    except Exception as exc:
        logger.warning("gitlab integration audit stage failed", error=str(exc))

    return row


# ── HTTP layer (patched in tests) ────────────────────────────────────────────


async def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> httpx.Response:
    async with httpx.AsyncClient(verify=http_verify(), timeout=_HTTP_TIMEOUT) as client:
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
    """Single retry on network error or 5xx — GitLab etiquette, mirroring the
    GitHub PR-comment service (best-effort cosmetic post, not the full
    ``resilience`` backoff)."""
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


def _headers(pat: str) -> dict[str, str]:
    return {
        "PRIVATE-TOKEN": pat,
        "Accept": "application/json",
        "User-Agent": "TestLookup/1.0",
    }


# ── Per-project target (W10 INT-04) ──────────────────────────────────────────

#: Why a project's GitLab target is unavailable, in the order decided.
GITLAB_UNAVAILABLE_REASONS = (
    "offline_mode", "no_integration", "integration_disabled", "no_project_path", "no_token",
)


@dataclass(frozen=True)
class GitLabTargetResolution:
    """A project's GitLab coordinates as plain values (no session, no HTTP).

    ``pat`` is set only when the target resolves (``reason`` None); it is kept
    out of ``repr`` so it never reaches a log line or an exception message."""

    project_id: str
    base_url: Optional[str]
    project_path: Optional[str]
    pat: Optional[str] = field(default=None, repr=False)
    reason: Optional[str] = None  # GITLAB_UNAVAILABLE_REASONS when unavailable


async def load_gitlab_target(
    db: AsyncSession,
    project_id: uuid.UUID | str,
    *,
    require_enabled: bool = True,
) -> GitLabTargetResolution:
    """The project's GitLab target, or the reason there is none. DB reads only.

    In order: ``AI_OFFLINE_MODE`` -> ``offline_mode`` (no read); no row ->
    ``no_integration``; ``require_enabled`` and the row disabled ->
    ``integration_disabled``; an empty project path -> ``no_project_path``; no
    stored PAT -> ``no_token``. Reads through this module's ``get_integration``
    and ``secret_service.read_secret`` (the names its tests patch). The
    ``gitlab`` feature flag is NOT asked: a health probe checks the
    configuration whether or not the posts are switched on (as
    ``test_connection`` does). Used by the Integration Health probes; W19
    INT-05 wraps it in ``github_target.resolve_git_host_target``.
    """
    pid = str(project_id)
    if settings.AI_OFFLINE_MODE:
        return GitLabTargetResolution(pid, None, None, reason="offline_mode")
    key: Any = project_id
    if isinstance(project_id, str):
        try:
            key = uuid.UUID(project_id)
        except ValueError:
            key = project_id
    row = await get_integration(db, key)
    if row is None:
        return GitLabTargetResolution(pid, None, None, reason="no_integration")
    base_url = _normalize_base_url(row.base_url)
    project_path = (row.project_path or "").strip()
    if require_enabled and not row.enabled:
        return GitLabTargetResolution(pid, base_url, project_path or None, reason="integration_disabled")
    if not project_path:
        return GitLabTargetResolution(pid, base_url, None, reason="no_project_path")

    from app.services import secret_service

    pat = await secret_service.read_secret(db, SECRET_SCOPE, _secret_key(pid))
    if not pat:
        return GitLabTargetResolution(pid, base_url, project_path, reason="no_token")
    return GitLabTargetResolution(pid, base_url, project_path, pat=pat)


# ── Connection test ──────────────────────────────────────────────────────────


async def test_connection(
    db: AsyncSession, project_id: uuid.UUID,
) -> dict[str, Any]:
    """Probe ``GET /projects/:path`` with the stored PAT to confirm the
    integration can reach GitLab. Runs even when the ``gitlab`` feature flag
    is off (setup before enable) but respects ``AI_OFFLINE_MODE`` and the
    SSRF guard. Never raises. Returns the config-contract test shape:
    ``{ok, detail, project_id_resolved}``.
    """
    if settings.AI_OFFLINE_MODE:
        return {
            "ok": False,
            "detail": "AI_OFFLINE_MODE is enabled — outbound calls are disabled",
            "project_id_resolved": None,
        }

    row = await get_integration(db, project_id)
    if row is None:
        return {
            "ok": False,
            "detail": "No GitLab integration configured for this project",
            "project_id_resolved": None,
        }
    if not (row.project_path or "").strip():
        return {
            "ok": False,
            "detail": "No project path configured for this integration",
            "project_id_resolved": None,
        }

    from app.services import secret_service
    pat = await secret_service.read_secret(db, SECRET_SCOPE, _secret_key(project_id))
    if not pat:
        return {
            "ok": False,
            "detail": "No token stored for this integration",
            "project_id_resolved": None,
        }

    url = f"{_api_root(row.base_url)}/projects/{_project_ref(row.project_path)}"

    block = await _ssrf_block_reason(url)
    if block:
        return {
            "ok": False,
            "detail": f"Target host is not allowed ({block})",
            "project_id_resolved": None,
        }

    try:
        resp = await _request("GET", url, headers=_headers(pat))
    except Exception as exc:
        return {
            "ok": False,
            "detail": f"Network error: {exc}",
            "project_id_resolved": None,
        }

    if resp.status_code == 200:
        body = {}
        try:
            body = resp.json() if resp.content else {}
        except Exception:
            body = {}
        resolved = body.get("id")
        return {
            "ok": True,
            "detail": f"OK — project '{body.get('path_with_namespace', '?')}' reachable",
            "project_id_resolved": str(resolved) if resolved is not None else None,
        }
    return {
        "ok": False,
        "detail": f"GitLab returned {resp.status_code}: {resp.text[:200]}",
        "project_id_resolved": None,
    }


# ── Error bookkeeping ────────────────────────────────────────────────────────


async def _record_outcome(
    integration_id: uuid.UUID, *, error: Optional[str],
) -> None:
    """Persist last_error / last_posted_at on the integration row — the same
    Integration Health surface the GitHub services write. Own session (worker
    path, no caller session); never raises."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(GitLabIntegration).where(
                    GitLabIntegration.id == integration_id
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
            "gitlab outcome record failed",
            integration_id=str(integration_id),
            error=str(exc),
        )


# ── MR note body (reused from the GitHub PR comment) ────────────────────────


def _build_mr_note_body(
    run: Any,
    project_id: uuid.UUID | str,
    project_name: Optional[str],
    part: Any,
    baseline: Optional[Any],
    draft_note: Optional[str] = None,
    reviewed_line: Optional[str] = None,
) -> str:
    """Render the sticky MR note markdown by REUSING the GitHub PR-comment
    renderer, then swapping its first line (the GitHub marker) for the GitLab
    marker so re-runs upsert against the right key. Zero classification /
    rendering fork — same sections, kind labels, overflow, and footer.
    ``reviewed_line`` (W23c re-post) is passed through; ``None`` keeps the
    body byte-identical."""
    from app.services import github_pr_comment_service as gh
    gh_body = gh._build_comment_body(
        run, project_id, project_name, part, baseline, draft_note=draft_note,
        reviewed_line=reviewed_line,
    )
    newline = gh_body.find("\n")
    tail = gh_body[newline:] if newline != -1 else ""
    return _marker(project_id) + tail


# ── MR-note context gathering ────────────────────────────────────────────────


class _MRNoteContext:
    __slots__ = (
        "integration_id", "api_root", "project_ref", "mr_iid", "pat",
        "mode", "marker", "body", "has_failures", "has_fixed",
    )

    def __init__(self, **kw: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kw[k])


def _skip(skip_reasons: Optional[list[str]], reason: str) -> Optional[_MRNoteContext]:
    """Note why no context is built (a ``REPOST_SKIP_REASONS`` code, read by the
    W23c re-post) and answer None, as every skip always has."""
    if skip_reasons is not None:
        skip_reasons.append(reason)
    return None


async def _gather_mr_context_or_skip(
    run_id: uuid.UUID, *, reviewed_line: Optional[str] = None,
) -> tuple[Optional[_MRNoteContext], str]:
    """``_gather_mr_context`` with the reason of a skip (``""`` with a context)."""
    reasons: list[str] = []
    ctx = await _gather_mr_context(run_id, reviewed_line=reviewed_line, skip_reasons=reasons)
    return ctx, (reasons[0] if reasons else "")


async def _gather_mr_context(
    run_id: uuid.UUID,
    *,
    reviewed_line: Optional[str] = None,
    skip_reasons: Optional[list[str]] = None,
) -> Optional[_MRNoteContext]:
    """Phase 1 — all DB reads. Returns None (with a debug log) for every
    non-participating case; the session closes before any HTTP happens.

    Reuses ``github_pr_comment_service``'s baseline selection, flaky set,
    partition, and body renderer so the MR note NEVER disagrees with the PR
    comment about what's newly failed / flaky / fixed.
    """
    from app.services import github_pr_comment_service as gh

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(TestRun).where(TestRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            logger.debug("mr_note skipped: run not found", run_id=str(run_id))
            return _skip(skip_reasons, "run_not_found")
        if not await _post_allowed(db, project_id=run.project_id):
            logger.debug(
                "mr_note skipped: gitlab flag off for the run's project",
                run_id=str(run_id),
            )
            return _skip(skip_reasons, "gitlab_off")
        if not run.pr_number or not run.ci_repo:
            logger.debug("mr_note skipped: no MR context on run", run_id=str(run_id))
            return _skip(skip_reasons, "no_pr_for_run")

        integration = await get_integration(db, run.project_id)
        if integration is None or not integration.enabled:
            logger.debug(
                "mr_note skipped: integration disabled or missing",
                run_id=str(run_id),
            )
            return _skip(skip_reasons, "no_integration" if integration is None else "integration_disabled")

        mode = (getattr(integration, "mr_comment_mode", None) or "failures_only").lower()
        if mode == "off":
            logger.debug("mr_note skipped: mode off", run_id=str(run_id))
            return _skip(skip_reasons, "mode_off")

        # Repo-match guard: MR IIDs are project-scoped, so the run's CI repo
        # must equal the configured project path (case-insensitive). Posting
        # this run's MR IID onto a different project would hit an unrelated MR.
        if run.ci_repo.strip().lower() != (integration.project_path or "").strip().lower():
            logger.debug(
                "mr_note skipped: run repo does not match integration",
                run_id=str(run_id),
                run_repo=run.ci_repo,
                integration_path=integration.project_path,
            )
            return _skip(skip_reasons, "repo_mismatch")

        from app.services import secret_service
        pat = await secret_service.read_secret(
            db, SECRET_SCOPE, _secret_key(run.project_id),
        )
        if not pat:
            logger.debug("mr_note skipped: no token", run_id=str(run_id))
            return _skip(skip_reasons, "no_token")

        project_name = None
        try:
            proj_row = await db.execute(
                select(Project.name).where(Project.id == run.project_id)
            )
            project_name = proj_row.scalar_one_or_none()
        except Exception:
            project_name = None

        baseline = await gh._select_baseline(db, run)
        right_tests = await _load_test_rows(db, run.id)
        left_tests = (
            await _load_test_rows(db, baseline.id) if baseline is not None else {}
        )
        has_baseline = baseline is not None and bool(left_tests)
        flaky_fps = await gh._flaky_fingerprints(db, run.project_id)

        from app.services.kind_evidence import kind_labels_for_test_cases
        failing_ids = [
            getattr(tc, "id", None)
            for tc in right_tests.values()
            if _status_bucket(tc.status) in ("failed", "broken")
            and getattr(tc, "id", None) is not None
        ]
        kind_labels = await kind_labels_for_test_cases(db, failing_ids)
        # E8.4: same human-review gate on AI kind labels as the GitHub comment.
        from app.services.report_distribution_policy import gate_kind_labels

        kind_labels, draft_note, label_decision = await gate_kind_labels(
            db, run_id=run.id, project_id=run.project_id,
            kind_labels=kind_labels, channel="gitlab_mr_note",
        )

        part = gh._partition_tests(
            right_tests, left_tests, flaky_fps, has_baseline=has_baseline,
            kind_labels=kind_labels,
        )
        body = _build_mr_note_body(
            run, run.project_id, project_name, part,
            baseline if has_baseline else None,
            draft_note=draft_note,
            reviewed_line=reviewed_line,
        )
        has_failures = (int(run.failed_tests or 0) + int(run.broken_tests or 0)) > 0

        from app.services.report_distribution_policy import record_distribution_detached

        await record_distribution_detached(
            label_decision, channel="gitlab_mr_note", run_id=run.id, project_id=run.project_id,
        )

        return _MRNoteContext(
            integration_id=integration.id,
            api_root=_api_root(integration.base_url),
            project_ref=_project_ref(integration.project_path),
            mr_iid=int(run.pr_number),
            pat=pat,
            mode=mode,
            marker=_marker(run.project_id),
            body=body,
            has_failures=has_failures,
            has_fixed=bool(part.fixed),
        )


async def _find_existing_note(
    api_root: str,
    project_ref: str,
    mr_iid: int,
    headers: dict[str, str],
    marker: str,
) -> Optional[int]:
    """Return the id of the MR note whose body starts with ``marker``,
    scanning up to 3 pages of 100. Raises on HTTP failure (caller records
    the error)."""
    url = f"{api_root}/projects/{project_ref}/merge_requests/{mr_iid}/notes"
    for page in range(1, _NOTE_MAX_PAGES + 1):
        resp = await _request_with_retry(
            "GET", url, headers=headers,
            params={"per_page": _NOTE_PAGE_SIZE, "page": page},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"list notes HTTP {resp.status_code}: {resp.text[:200]}"
            )
        notes = resp.json() or []
        for note in notes:
            if str(note.get("body") or "").startswith(marker):
                note_id = note.get("id")
                if note_id is None:
                    # A marker match without an id can't be updated — but it
                    # must NOT degrade into "no note found" (the caller would
                    # POST a duplicate on every re-run). Keep scanning for a
                    # usable match instead.
                    continue
                return int(note_id)
        if len(notes) < _NOTE_PAGE_SIZE:
            break
    return None


# ── MR-note public entry point ───────────────────────────────────────────────


async def post_mr_note_for_run(run_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """Upsert the sticky MR note for a finalized run.

    Best-effort and NEVER raises — the ingestion pipeline calls this after
    finalize and must not block on GitLab. Returns None for non-participating
    runs, a ``{"skipped": ...}`` dict for policy skips, ``{"posted"/"updated":
    True, ...}`` on success, and ``{"error": ...}`` on delivery failure (also
    recorded on the integration row for Integration Health).
    """
    try:
        # Off for everyone: skip without a session. The run's own project is
        # asked in ``_gather_mr_context`` as soon as the run is loaded.
        if not await _post_allowed(any_scope=True):
            logger.debug(
                "mr_note skipped: offline mode or feature flag off",
                run_id=str(run_id),
            )
            return None

        ctx = await _gather_mr_context(run_id)
        if ctx is None:
            return None

        notes_url = (
            f"{ctx.api_root}/projects/{ctx.project_ref}"
            f"/merge_requests/{ctx.mr_iid}/notes"
        )
        block = await _ssrf_block_reason(notes_url)
        if block:
            await _record_outcome(
                ctx.integration_id, error=f"blocked unsafe target: {block}",
            )
            logger.warning(
                "mr_note blocked unsafe target", run_id=str(run_id), reason=block,
            )
            return {"skipped": "blocked_unsafe_target", "reason": block}

        headers = _headers(ctx.pat)

        try:
            existing_id = await _find_existing_note(
                ctx.api_root, ctx.project_ref, ctx.mr_iid, headers, ctx.marker,
            )
        except Exception as exc:
            await _record_outcome(
                ctx.integration_id,
                error=f"{type(exc).__name__}: {str(exc)[:480]}",
            )
            logger.warning(
                "mr_note list failed", run_id=str(run_id), error=str(exc),
            )
            return {"error": str(exc)}

        # failures_only: a green run with nothing fixed posts nothing NEW —
        # but an existing marker note must be updated so an MR that went
        # red→green shows green.
        if (
            ctx.mode == "failures_only"
            and not ctx.has_failures
            and not ctx.has_fixed
            and existing_id is None
        ):
            logger.debug(
                "mr_note skipped: green run, failures_only, no prior note",
                run_id=str(run_id),
            )
            return {"skipped": "green_run_no_prior_comment"}

        try:
            if existing_id is not None:
                resp = await _request_with_retry(
                    "PUT",
                    f"{notes_url}/{existing_id}",
                    headers=headers,
                    json_body={"body": ctx.body},
                )
            else:
                resp = await _request_with_retry(
                    "POST", notes_url, headers=headers,
                    json_body={"body": ctx.body},
                )
        except Exception as exc:
            await _record_outcome(
                ctx.integration_id,
                error=f"{type(exc).__name__}: {str(exc)[:480]}",
            )
            logger.warning(
                "mr_note post failed", run_id=str(run_id), error=str(exc),
            )
            return {"error": str(exc)}

        if resp.status_code in (200, 201):
            await _record_outcome(ctx.integration_id, error=None)
            logger.info(
                "mr_note upserted",
                run_id=str(run_id),
                mr_iid=ctx.mr_iid,
                updated=existing_id is not None,
                status_code=resp.status_code,
            )
            note_id = None
            try:
                note_id = (resp.json() or {}).get("id") if resp.content else None
            except Exception:
                note_id = None
            return {
                "posted": True,
                "updated": existing_id is not None,
                "status_code": resp.status_code,
                "note_id": note_id,
            }

        await _record_outcome(
            ctx.integration_id,
            error=f"HTTP {resp.status_code}: {resp.text[:480]}",
        )
        logger.warning(
            "mr_note rejected", run_id=str(run_id), status_code=resp.status_code,
        )
        return {"error": f"HTTP {resp.status_code}", "status_code": resp.status_code}
    except Exception as exc:  # noqa: BLE001 — never raise into the pipeline
        logger.warning(
            "mr_note unhandled failure", run_id=str(run_id), error=str(exc),
        )
        return {"error": str(exc)}


async def repost_mr_note_for_run(
    run_id: uuid.UUID, *, reviewed_line: str,
) -> dict[str, Any]:
    """W23c AGT-10: EDIT the run's existing marker note with the reviewed body.

    The twin of ``github_pr_comment_service.repost_pr_summary_for_run``: the
    same ``_gather_mr_context``, SSRF guard and note search, then ONE PUT of the
    marker note. NEVER POSTs (no marker note -> ``skipped`` /
    ``no_prior_comment``). A timeout is ``timed_out`` and not written to
    ``last_error``. Never raises.
    """
    from app.services.github_pr_comment_service import _repost_outcome, _status_from_error

    try:
        if settings.AI_OFFLINE_MODE:
            return _repost_outcome("skipped", "offline_mode")
        found, reason = await _gather_mr_context_or_skip(run_id, reviewed_line=reviewed_line)
        if found is None:
            return _repost_outcome("skipped", reason or "no_integration")
        ctx: Any = found  # a __slots__ class: its attributes are set dynamically

        notes_url = f"{ctx.api_root}/projects/{ctx.project_ref}/merge_requests/{ctx.mr_iid}/notes"
        block = await _ssrf_block_reason(notes_url)
        if block:
            await _record_outcome(ctx.integration_id, error=f"blocked unsafe target: {block}")
            logger.warning("mr_note_repost_blocked", run_id=str(run_id), reason=block)
            return _repost_outcome("skipped", "blocked_unsafe_target")

        headers = _headers(ctx.pat)
        try:
            existing_id = await _find_existing_note(
                ctx.api_root, ctx.project_ref, ctx.mr_iid, headers, ctx.marker,
            )
            if existing_id is None:
                return _repost_outcome("skipped", "no_prior_comment")
            resp = await _request_with_retry(
                "PUT", f"{notes_url}/{existing_id}", headers=headers,
                json_body={"body": ctx.body},
            )
        except httpx.TimeoutException:
            logger.info("mr_note_repost_timed_out", run_id=str(run_id))
            return _repost_outcome("timed_out")
        except Exception as exc:  # noqa: BLE001 — recorded for Integration Health
            await _record_outcome(
                ctx.integration_id, error=f"{type(exc).__name__}: {str(exc)[:480]}",
            )
            logger.warning(
                "mr_note_repost_failed", run_id=str(run_id), error_type=type(exc).__name__,
            )
            return _repost_outcome(
                "failed", status_code=_status_from_error(exc), error_type=type(exc).__name__,
            )

        if resp.status_code == 200:
            await _record_outcome(ctx.integration_id, error=None)
            logger.info("mr_note_reposted", run_id=str(run_id), note_id=existing_id)
            return _repost_outcome("updated", status_code=200)
        await _record_outcome(
            ctx.integration_id, error=f"HTTP {resp.status_code}: {resp.text[:480]}",
        )
        logger.warning(
            "mr_note_repost_rejected", run_id=str(run_id), status_code=resp.status_code,
        )
        return _repost_outcome("failed", status_code=resp.status_code)
    except Exception as exc:  # noqa: BLE001 — never raise into the executor
        logger.warning(
            "mr_note_repost_unhandled", run_id=str(run_id), error_type=type(exc).__name__,
        )
        return _repost_outcome("failed", error_type=type(exc).__name__)


# ── Commit status ────────────────────────────────────────────────────────────


def _commit_status_state(run: Any) -> str:
    """Map a run verdict to a GitLab commit-status state.

    green (no failed + no broken) → ``success``; any failure → ``failed``.
    Mirrors the check-run conclusion in ``github_checks_service`` (minus the
    flaky-neutral case — GitLab commit statuses have no neutral state)."""
    failures = int(run.failed_tests or 0) + int(run.broken_tests or 0)
    return "success" if failures == 0 else "failed"


def _commit_status_payload(
    run: Any, project_name: Optional[str],
) -> dict[str, Any]:
    """Build the ``POST /statuses/:sha`` payload."""
    total = int(run.total_tests or 0)
    passed = int(run.passed_tests or 0)
    skipped = int(run.skipped_tests or 0)
    pass_rate = float(run.pass_rate or 0)
    # Over EXECUTED tests, matching the rate's own denominator — `passed/total`
    # beside the rate contradicts itself whenever anything was skipped
    # ("4/10 passed (44.4%)" invites 4/10 = 40%). Mirrors github_checks_service.
    executed = max(total - skipped, 0)
    proj_label = project_name or "TestLookup"

    base_url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    intel_path = f"/intelligence/{run.id}"
    deep_link = f"{base_url}{intel_path}" if base_url else None

    payload: dict[str, Any] = {
        "state": _commit_status_state(run),
        "name": f"TestLookup · {proj_label}",
        "description": f"{passed}/{executed} passed ({pass_rate:.1f}%)",
    }
    if deep_link:
        payload["target_url"] = deep_link
    return payload


class _CommitStatusContext:
    __slots__ = ("integration_id", "project_id", "url", "pat", "payload")

    def __init__(self, **kw: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kw[k])


async def _gather_commit_status_context(
    run_id: uuid.UUID,
) -> _CommitStatusContext | dict[str, Any]:
    """Phase 1 — all DB reads for the commit-status post. Returns a
    ``{"skipped": ...}`` dict for every non-participating case; the session
    closes before any HTTP happens (same design as ``_gather_mr_context``,
    documented there — a pooled connection must never sit pinned across a
    GitLab round-trip)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(TestRun).where(TestRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            return {"skipped": "run_not_found"}
        if not await _post_allowed(db, project_id=run.project_id):
            return {"skipped": "feature_flag_off_or_offline_mode"}
        if not run.commit_hash:
            return {"skipped": "no_commit_sha"}

        integration = await get_integration(db, run.project_id)
        if integration is None or not integration.enabled:
            return {"skipped": "integration_disabled_or_missing"}
        if not integration.commit_status_enabled:
            return {"skipped": "commit_status_disabled"}
        # Mandatory repo guard, mirroring the MR-note path: an empty
        # project_path would build ``/projects//statuses/…`` and a run
        # without a CI repo must not post onto whatever project happens to
        # be configured.
        if not (integration.project_path or "").strip():
            return {"skipped": "no_project_path"}
        if not (run.ci_repo or "").strip():
            return {"skipped": "repo_mismatch"}
        if run.ci_repo.strip().lower() != integration.project_path.strip().lower():
            return {"skipped": "repo_mismatch"}

        from app.services import secret_service
        pat = await secret_service.read_secret(
            db, SECRET_SCOPE, _secret_key(run.project_id),
        )
        if not pat:
            return {"skipped": "no_token_configured"}

        project_name: Optional[str] = None
        try:
            proj_row = await db.execute(
                select(Project.name).where(Project.id == run.project_id)
            )
            project_name = proj_row.scalar_one_or_none()
        except Exception:
            project_name = None

        # commit_hash is CI-supplied and unvalidated — quote it so ``?``,
        # ``#``, ``/`` or ``..`` can't rewrite the authenticated request path.
        url = (
            f"{_api_root(integration.base_url)}/projects/"
            f"{_project_ref(integration.project_path)}/statuses/"
            f"{quote(str(run.commit_hash), safe='')}"
        )
        return _CommitStatusContext(
            integration_id=integration.id,
            project_id=run.project_id,
            url=url,
            pat=pat,
            payload=_commit_status_payload(run, project_name),
        )


async def post_commit_status_for_run(run_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """Post a commit status to GitLab for a completed run.

    Best-effort — always returns a dict describing what happened but never
    raises. Skips (``{"skipped": "<reason>"}``) when the integration is
    disabled / offline / missing config / commit-status toggle off / no
    commit SHA. Phase 1 reads everything into a context and closes the
    session; no DB session is held across the HTTP round-trip; outcomes are
    persisted via ``_record_outcome`` (its own short session).
    """
    try:
        # Off for everyone: skip without a session. The run's own project is
        # asked in ``_gather_commit_status_context`` once the run is loaded.
        if not await _post_allowed(any_scope=True):
            return {"skipped": "feature_flag_off_or_offline_mode"}

        gathered = await _gather_commit_status_context(run_id)
        if isinstance(gathered, dict):
            return gathered
        ctx = gathered

        block = await _ssrf_block_reason(ctx.url)
        if block:
            await _record_outcome(
                ctx.integration_id, error=f"blocked unsafe target: {block}",
            )
            logger.warning(
                "gitlab_status blocked unsafe target",
                project_id=str(ctx.project_id),
                run_id=str(run_id),
                reason=block,
            )
            return {"skipped": "blocked_unsafe_target", "reason": block}

        headers = _headers(ctx.pat)

        async def _do_post() -> httpx.Response:
            return await _request(
                "POST", ctx.url, headers=headers, json_body=ctx.payload,
            )

        from app.services.resilience import async_retry
        try:
            resp: httpx.Response = await async_retry(
                _do_post,
                max_retries=3,
                base_delay=1.0,
                operation_name="gitlab_commit_status_post",
            )
        except Exception as exc:
            await _record_outcome(
                ctx.integration_id,
                error=f"{type(exc).__name__}: {str(exc)[:480]}",
            )
            logger.warning(
                "gitlab_status post failed",
                project_id=str(ctx.project_id),
                run_id=str(run_id),
                error=str(exc),
            )
            return {"error": str(exc)}

        if resp.status_code in (200, 201):
            await _record_outcome(ctx.integration_id, error=None)
            logger.info(
                "gitlab_status posted",
                project_id=str(ctx.project_id),
                run_id=str(run_id),
                status_code=resp.status_code,
                state=ctx.payload["state"],
            )
            return {
                "posted": True,
                "status_code": resp.status_code,
                "state": ctx.payload["state"],
            }

        await _record_outcome(
            ctx.integration_id,
            error=f"HTTP {resp.status_code}: {resp.text[:480]}",
        )
        logger.warning(
            "gitlab_status rejected",
            project_id=str(ctx.project_id),
            run_id=str(run_id),
            status_code=resp.status_code,
        )
        return {"error": f"HTTP {resp.status_code}", "status_code": resp.status_code}
    except Exception as exc:  # noqa: BLE001 — never raise into the pipeline
        logger.warning(
            "gitlab_status unhandled failure", run_id=str(run_id), error=str(exc),
        )
        return {"error": str(exc)}
