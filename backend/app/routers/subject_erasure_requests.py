"""The self-service erasure REQUEST and the administrators' queue (W38: W31 F-1; contract §2).

* ``GET /api/v1/me/erasure-request`` — the caller's NEWEST request, or ``null``;
* ``POST /api/v1/me/erasure-request`` — file one (no body) → 201; refusals in order ``not_a_person`` →
  ``user_erased`` (also while an erasure is queued / running) → ``request_pending`` (409, ``detail.request_id``)
  → ``last_admin_self`` (409);
* ``POST /api/v1/me/erasure-request/withdraw`` — withdraw the pending one; 409 ``no_pending_request``;
* ``GET /api/v1/users/erasure-requests?status=pending|decided|all`` — ADMIN: the queue (pending first, oldest
  first; then decided, newest decided first; ≤ ``ERASURE_REQUESTS_LIST_LIMIT``) + ``total``;
* ``POST /api/v1/users/erasure-requests/{erasure_request_id}/decline`` — ADMIN: decline with a CODE; 404
  ``request_not_found``, 409 ``request_not_pending`` / ``own_request``, 422 ``decline_code_invalid``.

Approving a request is an administrator running the existing erasure (``POST /api/v1/users/{user_id}/erasure``,
``routers/subject_erasure.py``), which closes the pending request in its own commit (D-5).

Every route depends on ``require_signed_in_session()`` FIRST (any API-key principal is 403 ``session_required``
before an id is read); the queue routes also on ``require_role(ADMIN)``. Refusals answer ``detail = {"code",
"message"}``; a refusal writes nothing. Each mutation stages its row, its access-audit row and its bell rows in
the service and commits ONCE here. Mounted BEFORE ``users.router`` (``app/bootstrap.py``), or ``GET
/api/v1/users/{user_id}`` would parse ``erasure-requests`` as a user id.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import SubjectErasureRequest, User, UserRole
from app.models.schemas import ErasureRequestDecline, ErasureRequestList, ErasureRequestRead, MyErasureRequest
from app.services import subject_erasure_request_service as erasure_requests
from app.services import subject_rights_job_service as jobs
from app.services.subject_rights_vocab import ERASURE_REQUEST_FILTERS

router = APIRouter(prefix="/api/v1", tags=["Subject rights"])

_signed_in_session = jobs.require_signed_in_session()
_FILTER_PATTERN = "^(" + "|".join(ERASURE_REQUEST_FILTERS) + ")$"


def _refused(refusal: erasure_requests.RequestRefused) -> HTTPException:
    detail: dict[str, Any] = jobs.error_detail(refusal.code)
    if refusal.code == "request_pending":
        detail["request_id"] = str(refusal.request_id) if refusal.request_id else None
    return HTTPException(status_code=erasure_requests.REFUSAL_STATUS[refusal.code], detail=detail)


async def _fresh(db: AsyncSession, request: SubjectErasureRequest) -> SubjectErasureRequest:
    await db.refresh(request)
    return request


# ── the person ───────────────────────────────────────────────────────────────

@router.get("/me/erasure-request", response_model=Optional[MyErasureRequest])
async def get_my_erasure_request(
    current_user: User = Depends(_signed_in_session),
    db: AsyncSession = Depends(get_db),
) -> Optional[MyErasureRequest]:
    """The caller's newest erasure request (pending, withdrawn, declined or accepted), or null. Reads only."""
    request = await erasure_requests.newest_for(db, current_user.id)
    return erasure_requests.to_mine(request) if request is not None else None


# activity: none - an erasure request is not a project event; its access-audit row is the record (W38 §2)
@router.post("/me/erasure-request", response_model=MyErasureRequest, status_code=status.HTTP_201_CREATED)
async def file_my_erasure_request(
    current_user: User = Depends(_signed_in_session),
    db: AsyncSession = Depends(get_db),
) -> MyErasureRequest:
    """Ask an administrator to erase the caller's account. One pending request per person: 409
    ``request_pending`` names it in ``detail.request_id``. Every other active administrator gets a bell row."""
    try:
        request = await erasure_requests.file_request(db, person_id=current_user.id)
    except erasure_requests.RequestRefused as refusal:
        raise _refused(refusal) from None
    await db.commit()
    return erasure_requests.to_mine(await _fresh(db, request))


# activity: none - withdrawing an erasure request is not a project event; its access-audit row is the record (W38 §2)
@router.post("/me/erasure-request/withdraw", response_model=MyErasureRequest)
async def withdraw_my_erasure_request(
    current_user: User = Depends(_signed_in_session),
    db: AsyncSession = Depends(get_db),
) -> MyErasureRequest:
    """Withdraw the caller's pending erasure request; 409 ``no_pending_request`` when there is none."""
    try:
        request = await erasure_requests.withdraw_request(db, person_id=current_user.id)
    except erasure_requests.RequestRefused as refusal:
        raise _refused(refusal) from None
    await db.commit()
    return erasure_requests.to_mine(await _fresh(db, request))


# ── the administrators' queue ────────────────────────────────────────────────

@router.get("/users/erasure-requests", response_model=ErasureRequestList)
async def list_erasure_requests(
    status_filter: str = Query("all", alias="status", pattern=_FILTER_PATTERN),
    current_user: User = Depends(_signed_in_session),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ErasureRequestList:
    """Every erasure request of the instance: pending first (oldest first), then decided (newest decided
    first), with how many match in all. Reads only."""
    rows, total = await erasure_requests.list_requests(db, status_filter=status_filter)
    return ErasureRequestList(items=await erasure_requests.to_reads(db, rows, viewer=current_user), total=total)


# activity: none - declining an erasure request is not a project event; its access-audit row is the record (W38 §2)
@router.post("/users/erasure-requests/{erasure_request_id}/decline", response_model=ErasureRequestRead)
async def decline_erasure_request(
    erasure_request_id: uuid.UUID,
    payload: Optional[ErasureRequestDecline] = Body(default=None),
    current_user: User = Depends(_signed_in_session),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ErasureRequestRead:
    """Decline a pending request with one of ``DECLINE_CODES``; the person gets a bell row and reads the code's
    sentence on their Profile. Never the caller's own request (409 ``own_request``)."""
    try:
        request = await erasure_requests.decline_request(
            db, request_id=erasure_request_id, decline_code=payload.decline_code if payload else None,
            actor=current_user,
        )
    except erasure_requests.RequestRefused as refusal:
        raise _refused(refusal) from None
    await db.commit()
    (read,) = await erasure_requests.to_reads(db, [await _fresh(db, request)], viewer=current_user)
    return read
