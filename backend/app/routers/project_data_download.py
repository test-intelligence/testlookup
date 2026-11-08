"""W37 F-W30-6: a large project export downloads straight to disk through a one-time ticket (contract §2.4, D-4).

A browser navigation carries no ``Authorization`` header, so the Bearer download
(``GET /project-data-jobs/{data_job_id}/download`` in ``routers/project_data.py``, unchanged — the CLI and the
small-archive Blob download use it) cannot be the target of a plain ``<a href>``. Two routes, two routers:

* ``POST /api/v1/project-data-jobs/{data_job_id}/download-ticket`` (``router``, PROTECTED): the same
  ``require_project_data_job_access`` as the download, then the download's refusals in ITS order with ITS bodies,
  then a 60-second, single-use ticket bound to (job, caller) — 503 ``ticket_unavailable`` when Redis is down.
* ``GET /api/v1/project-data-downloads/{data_job_id}?ticket=…`` (``public_router``, in ``PUBLIC_ROUTERS``):
  ``require_project_data_download_ticket`` spends the ticket (GETDEL), compares the path job with the ticket's,
  and RE-CHECKS as the issuing user (still active, ``load_job_for`` still allows, the job still a succeeded,
  unexpired export with its object). Then it streams exactly like the Bearer download, plus
  ``Referrer-Policy: no-referrer``. Every refusal is ONE 404 ``text/plain`` sentence a browser shows.

Neither route writes SQL (the GET's only write is the Redis GETDEL). Nothing logs the ticket.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import Project, ProjectDataJob, User
from app.routers.project_data import _download_name, require_project_data_job_access
from app.services import project_data_download_ticket as tickets
from app.services import project_data_job_service as jobs
from app.services import project_export_vocab as vocab

_slog = structlog.get_logger(__name__)

#: The public download; the ticket POST answers it with ``?ticket=<ticket>``.
DOWNLOAD_URL = "/api/v1/project-data-downloads/{job_id}"
#: Every refusal of the public download (unknown / spent / expired / foreign ticket, failed re-check).
LINK_GONE = "This download link has expired or was already used. Go back to Project data and click Download again."
#: The ticket query parameter's bounds (the agent-invocation stream ticket's).
TICKET_MIN_LENGTH, TICKET_MAX_LENGTH = 16, 128

router = APIRouter(prefix="/api/v1", tags=["Project data"])


class ProjectDataDownloadTicket(BaseModel):
    """The POST's answer: navigate to ``url`` within ``expires_in`` seconds, once."""

    ticket: str
    expires_in: int
    url: str


class ArchiveUnreadable(Exception):
    """Storage gave no first byte (502 ``archive_unreadable``)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _refusal(code: str, http_status: int) -> HTTPException:
    return HTTPException(status_code=http_status, detail=jobs.error_detail(code))


def download_refusal(job: ProjectDataJob, now: datetime) -> Optional[HTTPException]:
    """The Bearer download's refusals in ITS order with ITS bodies (``project_data.download_project_export``):
    409 not an export / not ready / failed, 410 expired, 502 no stored object. None = downloadable."""
    if job.kind != jobs.KIND_EXPORT:
        return _refusal("not_an_export", status.HTTP_409_CONFLICT)
    if job.status in jobs.LIVE_STATUSES:
        return _refusal("export_not_ready", status.HTTP_409_CONFLICT)
    if job.status == jobs.FAILED:
        return _refusal("export_failed", status.HTTP_409_CONFLICT)
    if job.status == jobs.EXPIRED or job.expires_at is None or job.expires_at <= now:
        return _refusal("export_expired", status.HTTP_410_GONE)
    if not job.storage_key:
        return _refusal("archive_unreadable", status.HTTP_502_BAD_GATEWAY)
    return None


async def stream_export(
    db: AsyncSession, job: ProjectDataJob, *, extra_headers: Optional[dict[str, str]] = None,
) -> StreamingResponse:
    """The Bearer download's response for a downloadable ``job`` (``project_data.download_project_export``,
    mirrored; ``test_project_data_download.py`` pins the two byte- and header-identical): the first chunk is
    pulled before the response (``ArchiveUnreadable`` otherwise), ``application/zip``, ``attachment`` named by
    ``_download_name``, ``Cache-Control: no-store``, ``Content-Length`` = ``job.bytes``."""
    from app.db.storage import get_storage_provider
    from app.services.retention_export_service import stream_archive

    if not job.storage_key:
        raise ArchiveUnreadable()
    slug = None
    if job.project_id is not None:
        slug = (await db.execute(select(Project.slug).where(Project.id == job.project_id))).scalar_one_or_none()
    chunks = stream_archive(get_storage_provider(), key=job.storage_key)
    try:
        first = await chunks.__anext__()
    except StopAsyncIteration:
        first = b""
    except Exception as exc:  # noqa: BLE001 - any storage failure before the first byte
        _slog.warning("project_export_unreadable", job_id=str(job.id), error_type=type(exc).__name__)
        raise ArchiveUnreadable() from exc

    async def _body() -> Any:
        yield first
        async for chunk in chunks:
            yield chunk

    history = jobs.job_options(job).include_history
    headers = {
        "Content-Disposition": f'attachment; filename="{_download_name(slug, job.finished_at, history=history)}"',
        "Cache-Control": "no-store",
    }
    if job.bytes is not None:
        headers["Content-Length"] = str(int(job.bytes))
    headers.update(extra_headers or {})
    return StreamingResponse(_body(), media_type="application/zip", headers=headers)


# ── the ticket (protected) ───────────────────────────────────────────────────

# activity: none - issues a 60-second, single-use download credential; the download itself writes nothing
@router.post("/project-data-jobs/{data_job_id}/download-ticket", response_model=ProjectDataDownloadTicket)
async def issue_project_export_download_ticket(
    data_job_id: uuid.UUID,
    response: Response,
    current_user: User = Depends(get_current_active_user),
    job: ProjectDataJob = Depends(require_project_data_job_access()),
) -> ProjectDataDownloadTicket:
    """A single-use ticket for ``GET /project-data-downloads/{data_job_id}`` (a navigation cannot send
    Authorization). Refuses exactly as the download does; 503 ``ticket_unavailable`` without Redis."""
    now = _now()
    refusal = download_refusal(job, now)
    if refusal is not None:
        raise refusal
    try:
        ticket, ttl = await tickets.issue_download_ticket(
            job.id, current_user.id, expires_at=job.expires_at or now, now=now,
        )
    except tickets.ExportExpiring:
        raise _refusal("export_expired", status.HTTP_410_GONE) from None
    except tickets.TicketUnavailable:
        _slog.warning("project_export_ticket_unavailable", job_id=str(job.id))
        raise _refusal("ticket_unavailable", status.HTTP_503_SERVICE_UNAVAILABLE) from None
    response.headers["Cache-Control"] = "no-store"
    _slog.info("project_export_ticket_issued", job_id=str(job.id), expires_in=ttl)
    return ProjectDataDownloadTicket(
        ticket=ticket, expires_in=ttl, url=f"{DOWNLOAD_URL.format(job_id=job.id)}?ticket={ticket}",
    )


# ── the download (public: the ticket is the credential) ─────────────────────

class _LinkGone(Exception):
    """A refused ticket download, answered as the ONE ``LINK_GONE`` 404."""


def _plain(text: str, status_code: int) -> PlainTextResponse:
    return PlainTextResponse(
        text, status_code=status_code, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


class _TicketDownloadRoute(APIRoute):
    """A browser navigation shows the body as text: every refusal — a bad, spent, expired or foreign ticket, a
    failed re-check, and a malformed link (``RequestValidationError``) — is the ONE 404 ``text/plain``
    ``LINK_GONE``, never a JSON envelope."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def plain(request: Request) -> Response:
            try:
                return await handler(request)
            except (_LinkGone, RequestValidationError):
                return _plain(LINK_GONE, status.HTTP_404_NOT_FOUND)

        return plain


#: A navigation cannot attach the router-wide Bearer / API-key credential of PROTECTED_ROUTERS, so the ticket
#: download lives on its own PUBLIC router; its one route authenticates with the job-bound, single-use ticket
#: that the protected ``router`` issues behind ``require_project_data_job_access``.
public_router = APIRouter(prefix="/api/v1", tags=["Project data"], route_class=_TicketDownloadRoute)


def _gone(job_id: uuid.UUID, reason: str) -> _LinkGone:
    _slog.info("project_export_ticket_refused", job_id=str(job_id), reason=reason)
    return _LinkGone(reason)


def require_project_data_download_ticket():
    """The export a ticket opens, re-checked as the user it was issued to; anything else is ``_LinkGone``.

    The ticket is spent first (GETDEL), whatever follows. The path job must be the ticket's job; the issuing user
    must still be active and still pass ``load_job_for`` (``may_read_job``); the job must still be a succeeded,
    unexpired export with its object (``download_refusal``)."""

    async def _check(
        request: Request,
        ticket: str = Query(..., min_length=TICKET_MIN_LENGTH, max_length=TICKET_MAX_LENGTH),
        db: AsyncSession = Depends(get_db),
    ) -> ProjectDataJob:
        try:
            job_id = uuid.UUID(str(request.path_params.get("data_job_id")))
        except ValueError:
            raise _LinkGone("malformed") from None
        user_id = await tickets.redeem_download_ticket(ticket, job_id)
        if user_id is None:
            raise _gone(job_id, "ticket")
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise _gone(job_id, "user")
        try:
            job = await jobs.load_job_for(db, job_id, user)
        except HTTPException:
            raise _gone(job_id, "access") from None
        if job.status != jobs.SUCCEEDED or download_refusal(job, _now()) is not None:
            raise _gone(job_id, "job")
        return job

    return _check


@public_router.get("/project-data-downloads/{data_job_id}")
async def download_project_export_by_ticket(
    data_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    job: ProjectDataJob = Depends(require_project_data_download_ticket()),
) -> Response:
    """Stream the export the ticket opens, exactly as the Bearer download does, plus ``Referrer-Policy:
    no-referrer``. Every refusal is the 404 ``LINK_GONE`` text; a storage fault before the first byte is a 502
    text. Writes NO SQL."""
    try:
        response = await stream_export(db, job, extra_headers={"Referrer-Policy": "no-referrer"})
    except ArchiveUnreadable:
        return _plain(vocab.ERROR_SENTENCES["archive_unreadable"], status.HTTP_502_BAD_GATEWAY)
    _slog.info("project_export_downloaded", job_id=str(job.id), bytes=job.bytes, via="ticket")
    return response
