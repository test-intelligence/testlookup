"""The self-service erasure REQUEST and the administrators' queue (W38: W31 F-1; contract §1, §2).

A request is NOT a job (D-1): it records that a person asked; ``POST
/api/v1/users/{id}/erasure`` stays the only thing that starts an erasure, and
running it closes the pending request in the SAME commit (:func:`close_pending_for`,
D-5). The person files and withdraws; an ADMIN declines with a CODE from
``DECLINE_CODES`` — no free text anywhere (D-2). One pending request per person
(``uq_ser_one_pending``, D-3).

Every mutation appends ONE ``access_audit_logs`` row (``erasure_request_filed`` /
``_withdrawn`` / ``_declined``; actor = the person / the administrator,
``target_user_id`` = the person, ``after_value`` = ``{request_id}`` (+
``decline_code``)). Filing stages an in-app bell row for every OTHER active
administrator, a decline one for the person (D-6): ``status = "sent"``,
``channel = IN_APP_CHANNEL`` and an event type outside ``NotificationEventType``
— the delivery relay claims only ``pending`` rows, so these can never be mailed
(the mentions precedent, ``services/activity/mentions.py``). Their titles and
bodies carry no name.

Lock order (no deadlock with the erasure route, which locks the user row FOR
UPDATE and then the request): the person's ``users`` row first, then the
request. The person's row is taken ``FOR NO KEY UPDATE``: it serialises two
filings / withdrawals / declines of one person and conflicts with the erasure's
``FOR UPDATE``, but NOT with the ``FOR KEY SHARE`` a foreign-key check takes — so
two administrators filing at once (each inserting a bell row that references the
other) never deadlock.

Stage-only (``STAGE_ONLY_SERVICES``): nothing here commits, rolls back or opens a
session; the routes (``routers/subject_erasure_requests.py``,
``routers/subject_erasure.py``) own the ONE commit. Log lines carry ids, codes
and counts only — never a name, a username or an e-mail. No mail path.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

import structlog
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import NotificationLog, SubjectErasureRequest, SubjectRightsJob, User, UserRole
from app.models.schemas import ErasurePendingRequest, ErasureRequestRead, MyErasureRequest
# A plain import on purpose: a from-import out of the activity package makes the backend.activity-coverage gate
# read this module as recording activity and stop checking the routes' `# activity: none` opt-outs (it records
# none: an erasure request is not a project event).
import app.services.activity.live_vocab as live_vocab
from app.services import subject_rights_job_service as jobs
from app.services.access_audit_service import log_access_change
from app.services.subject_rights_vocab import (
    AUDIT_ACTIONS,
    BELL_APP_PATHS,
    BELL_SENTENCES,
    DECIDED_REQUEST_STATUSES,
    DECLINE_CODES,
    DECLINE_SENTENCES,
    ERASURE_REQUEST_FILTERS,
    ERASURE_REQUEST_STATUSES,
    ERASURE_REQUESTS_LIST_LIMIT,
)
from app.services.user_account_guards import is_last_active_admin

_slog = structlog.get_logger(__name__)

PENDING, WITHDRAWN, DECLINED, ACCEPTED = ERASURE_REQUEST_STATUSES
ACTION_FILED, ACTION_WITHDRAWN, ACTION_DECLINED = AUDIT_ACTIONS[-3:]
#: Every refusal this module raises and the HTTP status the routes answer it with (``detail = {code, message}``).
REFUSAL_STATUS: dict[str, int] = {
    "not_a_person": 409,
    "user_erased": 409,
    "request_pending": 409,
    "last_admin_self": 409,
    "no_pending_request": 409,
    "request_not_found": 404,
    "request_not_pending": 409,
    "own_request": 409,
    "decline_code_invalid": 422,
}


class RequestRefused(Exception):
    """A refusal: ``code`` is an ``ERROR_SENTENCES`` key; ``request_id`` names the live request (request_pending)."""

    def __init__(self, code: str, *, request_id: Optional[uuid.UUID] = None) -> None:
        super().__init__(code)
        self.code = code
        self.request_id = request_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── locks and reads (a read never writes) ────────────────────────────────────

async def lock_person(db: AsyncSession, user_id: uuid.UUID) -> User:
    """The person's ``users`` row ``FOR NO KEY UPDATE``, re-read from the database (``populate_existing``)."""
    result = await db.execute(
        select(User).where(User.id == user_id).with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def pending_for(db: AsyncSession, subject_id: uuid.UUID, *, lock: bool = False) -> Optional[SubjectErasureRequest]:
    """The person's pending request (at most one: ``uq_ser_one_pending``), ``FOR UPDATE`` when ``lock``."""
    statement = select(SubjectErasureRequest).where(
        SubjectErasureRequest.subject_user_id == subject_id, SubjectErasureRequest.status == PENDING,
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(statement.limit(1))).scalar_one_or_none()


async def newest_for(db: AsyncSession, subject_id: uuid.UUID) -> Optional[SubjectErasureRequest]:
    """The person's NEWEST request of any status (``GET /me/erasure-request``), or None."""
    result = await db.execute(
        select(SubjectErasureRequest).where(SubjectErasureRequest.subject_user_id == subject_id)
        .order_by(SubjectErasureRequest.requested_at.desc(), SubjectErasureRequest.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def pending_summary(db: AsyncSession, subject_id: uuid.UUID) -> Optional[ErasurePendingRequest]:
    """``ErasurePreview.pending_request``: the pending request's id and date, or None (reads only)."""
    request = await pending_for(db, subject_id)
    return ErasurePendingRequest(id=request.id, requested_at=request.requested_at) if request else None


def to_mine(request: SubjectErasureRequest) -> MyErasureRequest:
    """The person's wire shape: the decline code's SENTENCE as ``decline_message``."""
    code = request.decline_code
    return MyErasureRequest(
        id=request.id, status=request.status, requested_at=request.requested_at, decided_at=request.decided_at,
        decline_code=code, decline_message=DECLINE_SENTENCES.get(code) if code else None,
    )


async def list_requests(db: AsyncSession, *, status_filter: str) -> tuple[list[SubjectErasureRequest], int]:
    """The queue: ``pending`` / ``decided`` / ``all`` — pending first (oldest first), then decided (newest
    decided first), at most ``ERASURE_REQUESTS_LIST_LIMIT`` rows, and how many match in all (a top-N length is
    not a count)."""
    if status_filter not in ERASURE_REQUEST_FILTERS:
        raise ValueError(f"unknown erasure request filter {status_filter!r}")
    where = []
    if status_filter == "pending":
        where.append(SubjectErasureRequest.status == PENDING)
    elif status_filter == "decided":
        where.append(SubjectErasureRequest.status.in_(DECIDED_REQUEST_STATUSES))
    is_pending = SubjectErasureRequest.status == PENDING
    rows = await db.execute(
        select(SubjectErasureRequest).where(*where)
        .order_by(
            case((is_pending, 0), else_=1),
            case((is_pending, SubjectErasureRequest.requested_at)).asc().nulls_last(),
            SubjectErasureRequest.decided_at.desc().nulls_last(),
            SubjectErasureRequest.id.desc(),
        )
        .limit(ERASURE_REQUESTS_LIST_LIMIT)
    )
    total = await db.execute(select(func.count()).select_from(SubjectErasureRequest).where(*where))
    return list(rows.scalars().all()), int(total.scalar_one())


async def to_reads(
    db: AsyncSession, requests: Sequence[SubjectErasureRequest], *, viewer: User,
) -> list[ErasureRequestRead]:
    """The queue's wire rows. Names go through the job service's ``displays()`` (full name, else username;
    "Erased user" for an erased person — never an e-mail): it reads only ``subject_user_id`` /
    ``requested_by_id``, so each request is handed over as a TRANSIENT job carrying its subject and its
    decider (never added to the session)."""
    if not requests:
        return []
    names = await jobs.displays(db, [
        SubjectRightsJob(subject_user_id=r.subject_user_id, requested_by_id=r.decided_by_id) for r in requests
    ])
    subject_ids = {r.subject_user_id for r in requests}
    flags = {
        row.id: row for row in (await db.execute(
            select(User.id, User.username, User.is_active, User.erased_at).where(User.id.in_(subject_ids))
        )).all()
    }
    out: list[ErasureRequestRead] = []
    for r in requests:
        subject = flags.get(r.subject_user_id)
        out.append(ErasureRequestRead(
            id=r.id, subject_user_id=r.subject_user_id, subject_display=names.get(r.subject_user_id),
            subject_username=subject.username if subject else None,
            subject_is_erased=bool(subject is not None and subject.erased_at is not None),
            subject_is_active=bool(subject is not None and subject.is_active),
            requester_is_you=r.subject_user_id == viewer.id, status=r.status, requested_at=r.requested_at,
            decided_at=r.decided_at, decided_by_display=names.get(r.decided_by_id) if r.decided_by_id else None,
            decline_code=r.decline_code, erasure_job_id=r.erasure_job_id,
        ))
    return out


# ── D-5: any erasure closes the pending request ──────────────────────────────

async def close_pending_for(
    db: AsyncSession, *, subject_id: uuid.UUID, job_id: uuid.UUID, decided_by_id: Optional[uuid.UUID],
) -> Optional[uuid.UUID]:
    """Mark the person's pending request ``accepted`` by the erasure ``job_id`` (``decided_by_id`` = the
    administrator; None for a SCIM-started erasure) and return its id; None when nothing was pending.

    Called by the erasure once the person's ``users`` row is locked and BEFORE its lock-out is staged (the
    lock-out's audit row then carries the returned ``request_id``), inside its ONE commit — so the request closes
    in the same transaction as the lock-out (or not at all). Flushes only."""
    request = await pending_for(db, subject_id, lock=True)
    if request is None:
        return None
    request.status = ACCEPTED
    request.decided_at = _now()
    request.decided_by_id = decided_by_id
    request.erasure_job_id = job_id
    await db.flush()
    _slog.info("erasure_request_accepted", request_id=str(request.id), job_id=str(job_id),
               subject_id=str(subject_id))
    return request.id


# ── the bell rows (D-6: in-app only, never mailed, no name) ──────────────────

def waiting_delivery_key(request_id: uuid.UUID, admin_id: uuid.UUID) -> str:
    """One bell row per administrator per request (``uq_notification_log_delivery_key``)."""
    return hashlib.sha256(f"erasure_request:{request_id}:{admin_id}".encode()).hexdigest()


def declined_delivery_key(request_id: uuid.UUID) -> str:
    """One bell row for the person per declined request."""
    return hashlib.sha256(f"erasure_request_declined:{request_id}".encode()).hexdigest()


def _bell(*, user_id: uuid.UUID, event_type: str, title: str, body: str, key: str, app_path: str,
          request_id: uuid.UUID, now: datetime) -> NotificationLog:
    # status="sent" + sent_at: the row IS the bell entry; the relay claims only "pending" rows (never mailed).
    return NotificationLog(
        user_id=user_id, project_id=None, channel=live_vocab.IN_APP_CHANNEL, event_type=event_type, title=title, body=body,
        status="sent", sent_at=now, is_read=False, delivery_key=key,
        delivery_metadata={"app_path": app_path, "request_id": str(request_id)},
    )


async def stage_admin_bells(db: AsyncSession, *, request: SubjectErasureRequest) -> int:
    """``db.add`` one bell row for every OTHER active, non-erased, human administrator (never the requester);
    returns how many. Zero is fine: the only administrator files nothing (``last_admin_self``) and another
    administrator's request may reach no one else — the queue still lists it."""
    result = await db.execute(
        select(User.id).where(
            User.role == UserRole.ADMIN,
            User.is_active.is_(True),
            User.erased_at.is_(None),
            User.is_service_account.is_(False),
            User.is_synthetic.is_(False),
            User.id != request.subject_user_id,
        ).order_by(User.id)
    )
    admin_ids = [row[0] for row in result.all()]
    now = _now()
    for admin_id in admin_ids:
        db.add(_bell(
            user_id=admin_id, event_type=live_vocab.ERASURE_REQUEST_NOTIFICATION_EVENT,
            title=BELL_SENTENCES["erasure_request_waiting"], body=BELL_SENTENCES["erasure_request_waiting_body"],
            key=waiting_delivery_key(request.id, admin_id), app_path=BELL_APP_PATHS["erasure_request_waiting"],
            request_id=request.id, now=now,
        ))
    return len(admin_ids)


def stage_declined_bell(db: AsyncSession, *, request: SubjectErasureRequest) -> None:
    """``db.add`` the person's one bell row for a declined request (body = the code's sentence)."""
    code = request.decline_code or "other"
    db.add(_bell(
        user_id=request.subject_user_id, event_type=live_vocab.ERASURE_REQUEST_DECLINED_EVENT,
        title=BELL_SENTENCES["erasure_request_declined"], body=DECLINE_SENTENCES[code],
        key=declined_delivery_key(request.id), app_path=BELL_APP_PATHS["erasure_request_declined"],
        request_id=request.id, now=_now(),
    ))


# ── the person: file / withdraw ──────────────────────────────────────────────

async def filing_refusal(db: AsyncSession, person: User) -> Optional[RequestRefused]:
    """The FIRST refusal of a filing, or None, in §2's order: ``not_a_person`` → ``user_erased`` (erased, or an
    erasure queued / running) → ``request_pending`` (with the live id) → ``last_admin_self``."""
    if bool(person.is_service_account) or bool(person.is_synthetic):
        return RequestRefused("not_a_person")
    if person.erased_at is not None or await jobs.live_job(db, subject_id=person.id, kind=jobs.KIND_ERASURE):
        return RequestRefused("user_erased")
    pending = await pending_for(db, person.id)
    if pending is not None:
        return RequestRefused("request_pending", request_id=pending.id)
    if await is_last_active_admin(db, person):
        return RequestRefused("last_admin_self")
    return None


async def file_request(db: AsyncSession, *, person_id: uuid.UUID) -> SubjectErasureRequest:
    """Stage the caller's request: lock their ``users`` row, refuse in §2's order, insert the row (the partial
    unique is the backstop of a concurrent filing → ``request_pending``), append ``erasure_request_filed`` and
    the administrators' bell rows. Raises :class:`RequestRefused`. Flushes; the route makes it durable."""
    person = await lock_person(db, person_id)
    refused = await filing_refusal(db, person)
    if refused is not None:
        raise refused
    request = SubjectErasureRequest(subject_user_id=person.id, status=PENDING, requested_at=_now())
    try:
        async with db.begin_nested():
            db.add(request)
            await db.flush()
    except IntegrityError:
        live = await pending_for(db, person.id)
        raise RequestRefused("request_pending", request_id=live.id if live else None) from None
    await log_access_change(db, ACTION_FILED, person, target_user_id=person.id,
                            after_value={"request_id": str(request.id)})
    notified = await stage_admin_bells(db, request=request)
    await db.flush()
    _slog.info("erasure_request_filed", request_id=str(request.id), subject_id=str(person.id), notified=notified)
    return request


async def withdraw_request(db: AsyncSession, *, person_id: uuid.UUID) -> SubjectErasureRequest:
    """Withdraw the caller's pending request (``decided_at`` now, no decider) + ``erasure_request_withdrawn``.
    Raises :class:`RequestRefused` ``no_pending_request``. Flushes; the route makes it durable."""
    person = await lock_person(db, person_id)
    request = await pending_for(db, person.id, lock=True)
    if request is None:
        raise RequestRefused("no_pending_request")
    request.status = WITHDRAWN
    request.decided_at = _now()
    request.decided_by_id = None
    await log_access_change(db, ACTION_WITHDRAWN, person, target_user_id=person.id,
                            after_value={"request_id": str(request.id)})
    await db.flush()
    _slog.info("erasure_request_withdrawn", request_id=str(request.id), subject_id=str(person.id))
    return request


# ── an administrator: decline ────────────────────────────────────────────────

async def decline_request(
    db: AsyncSession, *, request_id: uuid.UUID, decline_code: Optional[str], actor: User,
) -> SubjectErasureRequest:
    """Decline a pending request with a CODE: ``request_not_found`` (404) → ``request_not_pending`` →
    ``own_request`` → ``decline_code_invalid`` (422). Locks the person's ``users`` row, then the request
    (``FOR UPDATE``, the erasure's order), re-checks it, stages the decline + ``erasure_request_declined``
    (``after_value`` {request_id, decline_code}) + the person's bell row. Flushes; the route makes it durable."""
    found = await db.get(SubjectErasureRequest, request_id)
    if found is None:
        raise RequestRefused("request_not_found")
    await lock_person(db, found.subject_user_id)
    request = (await db.execute(
        select(SubjectErasureRequest).where(SubjectErasureRequest.id == request_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one()
    if request.status != PENDING:
        raise RequestRefused("request_not_pending")
    if request.subject_user_id == actor.id:
        raise RequestRefused("own_request")
    if decline_code not in DECLINE_CODES:
        raise RequestRefused("decline_code_invalid")
    request.status = DECLINED
    request.decline_code = decline_code
    request.decided_at = _now()
    request.decided_by_id = actor.id
    await log_access_change(db, ACTION_DECLINED, actor, target_user_id=request.subject_user_id,
                            after_value={"request_id": str(request.id), "decline_code": decline_code})
    stage_declined_bell(db, request=request)
    await db.flush()
    _slog.info("erasure_request_declined", request_id=str(request.id), subject_id=str(request.subject_user_id),
               decline_code=decline_code)
    return request
