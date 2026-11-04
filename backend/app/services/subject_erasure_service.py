"""The pseudonymising erasure of ONE person (W31 STRAT-12, Art. 17 with the Art. 17(3) audit exception; contract §2.3).

The steps, each driven by ``subject_data_inventory.INVENTORY`` (the truth):

* :func:`refusal` / :func:`blockers` — E-1's refusals, in order.
* :func:`preview` — a GET of counts: READS ONLY (no flush, no INSERT / UPDATE /
  DELETE); every count is bounded by ``PREVIEW_COUNT_TIMEOUT_MS`` and a count that
  times out (or fails) reads ``{count: None, measured: False}``.
* :func:`stage_lockout` — E-2, the route's ONE transaction: the account is
  disabled, every token revoked, every session deleted, every key disabled, one
  ``subject_erasure_requested`` access-audit row (the CALLER is its actor).
* :func:`run_postgres_phase` — E-3..E-7 in the worker, one transaction: the
  A-list deletes, the B rewrites (incl. the ``@username`` mention token), the
  user row last (pseudonym, ``erased_at``). Idempotent: the pseudonym is derived
  from the id, so a re-run finds and reuses it.
* :func:`run_object_phase` / :func:`run_redis_phase` — E-8, after the Postgres
  commit; id-based and idempotent.
* :func:`erased_display` / :func:`erased_ids` — E-9, the read projection.

The five gate-protected audit tables are NEVER updated or deleted here (the
``backend.audit-write-discipline`` gate): they are only appended to through
``log_access_change``. Stage-only (``STAGE_ONLY_SERVICES``): nothing commits —
the route (``routers/subject_erasure.py``) and the runner
(``worker/subject_erasure_runner.py``) own the commits. Log lines carry job /
user ids, location keys, counts and error TYPE names — never a name, a
username, a pseudonym, an e-mail or a storage key. No mail path, no LLM.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import structlog
from sqlalchemy import and_, delete, false, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Base, ProjectDataJob, SubjectRightsJob, User
from app.models.schemas import ErasureBlocker, ErasureLocationCount, ErasurePreview
from app.services.access_audit_service import log_access_change
from app.services.sql_utils import escape_like
from app.services.subject_data_inventory import AUDIT_TABLES, INVENTORY, Location, postgres_locations
from app.services.subject_rights_job_service import (
    KIND_ERASURE,
    KIND_EXPORT,
    QUEUED,
    displays,
    error_detail,
    is_api_key_principal,
    live_job,
    to_read,
)
from app.services.subject_rights_vocab import (
    ERASED_DISPLAY,
    ERASED_EMAIL_DOMAIN,
    ERASED_HASH_MARKER,
    ERROR_SENTENCES,
    PREVIEW_COUNT_TIMEOUT_MS,
    PSEUDONYM_HEX_LENGTHS,
    PSEUDONYM_PREFIX,
)
from app.services.user_account_guards import is_last_active_admin

_slog = structlog.get_logger(__name__)

#: The access-audit actions this module appends (§1.1 ``AUDIT_ACTIONS``).
ACTION_REQUESTED = "subject_erasure_requested"
ACTION_ERASED = "subject_erased"
#: E-1's codes and the HTTP status each refusal answers with.
REFUSAL_STATUS: dict[str, int] = {
    "session_required": 403,
    "user_not_found": 404,
    "not_a_person": 409,
    "self_erasure": 409,
    "user_erased": 409,
    "erasure_running": 409,
    "export_blocks_erasure": 409,
    "last_admin": 409,
    "confirm_mismatch": 422,
}
#: E-2's lock-out deletes the sessions; their count is carried into the job's counts.
LOCKOUT_COUNT_KEY = "refresh_token_records.user_id"
#: Comments / notification bodies are rewritten in batches of this many rows (E-3 B6).
REWRITE_BATCH_ROWS = 500
#: The W24 project export member that lists the referenced users (E2).
_DIRECTORY_MEMBER = "directory/users.jsonl"
_DIRECTORY_MAX_BYTES = 64 * 1024 * 1024
_SPOOL_BYTES = 8 * 1024 * 1024
_LIVE_REVIEW_STATUSES = ("pending", "in_progress")


# ── the pseudonym and the mention token ──────────────────────────────────────

def pseudonym_for(user_id: uuid.UUID, length: int = 8) -> str:
    """``"erased-" + user_id.hex[:length]`` — deterministic, so an interrupted erasure reuses it."""
    return f"{PSEUDONYM_PREFIX}{user_id.hex[:length]}"


def erased_email_for(pseudonym: str) -> str:
    """``<pseudonym>@erased.invalid`` (RFC 2606; refused by the mail guard)."""
    return f"{pseudonym}@{ERASED_EMAIL_DOMAIN}"


def mention_pattern(username: str) -> re.Pattern[str]:
    """The ``@<username>`` token, word-bounded and case-insensitive (§2.1 B6):
    ``@alice`` matches, ``@alice2`` and ``x@alice.com`` do not."""
    return re.compile(r"(?<![\w.@-])@" + re.escape(username) + r"(?![\w.-])", re.IGNORECASE)


def rewrite_mentions(content: str, *, username: str, pseudonym: str) -> str:
    """Every ``@<username>`` token → ``@<pseudonym>``; nothing else in the text changes."""
    if not content or not username:
        return content
    replacement = "@" + pseudonym
    return mention_pattern(username).sub(lambda _match: replacement, content)


def _replace_name(content: str, name: Optional[str], replacement: str) -> str:
    """Every word-bounded, case-insensitive occurrence of ``name`` (not as an ``@`` token) → ``replacement``."""
    if not content or not name or not name.strip():
        return content
    pattern = re.compile(r"(?<![\w.@-])" + re.escape(name.strip()) + r"(?![\w-])", re.IGNORECASE)
    return pattern.sub(lambda _match: replacement, content)


# ── the read projection (E-9) ────────────────────────────────────────────────

def erased_display(*, erased_at: Optional[datetime], name: Optional[str]) -> Optional[str]:
    """``"Erased user"`` when the account is erased, else the stored / resolved ``name``."""
    return ERASED_DISPLAY if erased_at is not None else name


async def erased_ids(db: AsyncSession, ids: Iterable[Optional[uuid.UUID]]) -> set[uuid.UUID]:
    """The erased accounts among ``ids`` — ONE ``SELECT … WHERE id IN (…) AND erased_at IS NOT NULL``
    (``ix_users_erased_at``); no statement for an empty page."""
    wanted = {value for value in ids if value is not None}
    if not wanted:
        return set()
    result = await db.execute(select(User.id).where(User.id.in_(wanted), User.erased_at.isnot(None)))
    return {row[0] for row in result.all()}


# ── refusals (E-1) ───────────────────────────────────────────────────────────

def is_resumable(job: Optional[SubjectRightsJob]) -> bool:
    """An unfinished erasure the admin may RESUME (re-queue, same id) instead of a 409: one whose last
    step recorded ``erasure_incomplete`` (E-8). A job that is queued or being worked on right now (no
    error) answers ``erasure_running`` — a second POST is not a resume; a lost enqueue is re-sent by the
    janitor."""
    if job is None or job.kind != KIND_ERASURE:
        return False
    return job.error_code == "erasure_incomplete"


async def blockers(db: AsyncSession, *, subject: User, actor: User) -> list[str]:
    """Every refusal code that applies to erasing ``subject``, in E-1's order (after the session /
    existence checks, which the route answers itself). Empty → the erasure may start (or resume)."""
    codes: list[str] = []
    if bool(getattr(subject, "is_service_account", False)) or bool(getattr(subject, "is_synthetic", False)):
        codes.append("not_a_person")
    if subject.id == actor.id:
        codes.append("self_erasure")
    erasure = await live_job(db, subject_id=subject.id, kind=KIND_ERASURE)
    if subject.erased_at is not None and erasure is None:
        codes.append("user_erased")
    if erasure is not None and not is_resumable(erasure):
        codes.append("erasure_running")
    if await live_job(db, subject_id=subject.id, kind=KIND_EXPORT) is not None:
        codes.append("export_blocks_erasure")
    if await is_last_active_admin(db, subject):
        codes.append("last_admin")
    return codes


async def refusal(db: AsyncSession, *, subject: Optional[User], actor: User) -> Optional[str]:
    """The FIRST refusal code in E-1's order, or None: ``session_required`` (any API-key principal),
    ``user_not_found``, ``not_a_person``, ``self_erasure``, ``user_erased``, ``erasure_running``,
    ``export_blocks_erasure``, ``last_admin``. (``confirm_mismatch`` is :func:`confirm_matches`.)"""
    if is_api_key_principal(actor):
        return "session_required"
    if subject is None:
        return "user_not_found"
    codes = await blockers(db, subject=subject, actor=actor)
    return codes[0] if codes else None


def confirm_matches(subject: User, confirm_username: Optional[str]) -> bool:
    """The typed confirmation equals the CURRENT username exactly (case-sensitive; the pseudonym once
    the Postgres phase has committed — the resume)."""
    return confirm_username is not None and confirm_username == subject.username


def refusal_detail(code: str) -> dict[str, str]:
    """``{"code", "message"}`` of a refusal (the route's ``detail``)."""
    return error_detail(code)


# ── the pseudonym ladder (E-3 / E-4) ─────────────────────────────────────────

async def choose_pseudonym(db: AsyncSession, user: User) -> str:
    """The first ``erased-<hex n>`` (n in ``PSEUDONYM_HEX_LENGTHS``) that this row already holds or that no
    OTHER row holds as a username or e-mail. Deterministic, so a re-run reuses its own. Reads only."""
    for length in PSEUDONYM_HEX_LENGTHS:
        candidate = pseudonym_for(user.id, length)
        if candidate == user.username:
            return candidate
        taken = await db.execute(
            select(func.count()).select_from(User).where(
                User.id != user.id,
                or_(
                    func.lower(User.username) == candidate,
                    func.lower(User.email) == erased_email_for(candidate),
                ),
            )
        )
        if int(taken.scalar() or 0) == 0:
            return candidate
    raise RuntimeError("no free pseudonym")  # unreachable: 32 hex digits are the whole id


# ── the row filter of each Postgres location ─────────────────────────────────

@dataclass(frozen=True)
class Identity:
    """The person's CURRENT values, read once under the row lock — never logged, never stored."""
    user_id: uuid.UUID
    username: str
    email: str
    full_name: Optional[str]

    @classmethod
    def of(cls, user: User) -> "Identity":
        return cls(user_id=user.id, username=user.username or "", email=user.email or "",
                   full_name=user.full_name)

    @property
    def display(self) -> str:
        return (self.full_name or "").strip() or self.username


def _table(name: str) -> Any:
    return Base.metadata.tables[name]


def _contains(column: Any, value: str) -> Any:
    return column.ilike(f"%{escape_like(value)}%", escape="\\")


def match_clause(location: Location, person: Identity) -> Optional[Any]:
    """The WHERE of ``location`` for ``person`` (the preview's count, the erasure's delete / rewrite),
    or None for a location that is not matched row by row (``users.*``, the non-ORM table)."""
    if location.table is None or location.table not in Base.metadata.tables:
        return None
    t = _table(location.table)
    uid = person.user_id
    key = location.key
    if key == "saved_views.user_id":
        return and_(t.c.user_id == uid, t.c.is_shared == false())
    if key == "test_case_reviews.reviewer_id" and location.open_item:
        return and_(t.c.reviewer_id == uid, t.c.status.in_(_LIVE_REVIEW_STATUSES))
    if location.match == "fk":
        return t.c[location.column] == uid
    if key == "secret_refs.key_name":
        from app.services.mfa_service import TOTP_SECRET_SCOPE

        return and_(t.c.scope == TOTP_SECRET_SCOPE, t.c.key_name == str(uid))
    if key == "user_invitations.email":
        return and_(func.lower(t.c.email) == person.email.lower(), t.c.accepted_user_id.is_(None))
    if key == "project_insights.dismissed_by_name":
        return and_(t.c.dismissed_by_id == uid, t.c.dismissed_by_name.isnot(None))
    if key == "report_share_links.created_by_name":
        return and_(t.c.created_by_id == uid, t.c.created_by_name.isnot(None))
    if key == "agent_investigations.cancelled_by":
        return t.c.cancelled_by.in_([person.username, person.email])
    if key == "agent_pipeline_runs.error":
        # pipeline_cancellation.request_cancel stores "cancelled: by <e-mail>" (workflow_run_state's prefix)
        return t.c.error.like(f"%by {escape_like(person.email)}", escape="\\")
    if key == "webhook_deliveries.event_payload":
        return t.c.event_payload["requested_by"].as_string().in_([person.username, person.email])
    if key == "notification_logs.title":
        names = [_contains(t.c.title, person.username)]
        if person.full_name and person.full_name.strip():
            names.append(_contains(t.c.title, person.full_name.strip()))
        return and_(or_(t.c.user_id.is_(None), t.c.user_id != uid), or_(*names))
    if key == "notification_logs.body":
        bodies = [_contains(t.c.body, "@" + person.username)]
        if person.full_name and person.full_name.strip():
            bodies.append(_contains(t.c.body, "owner: " + person.full_name.strip()))
        return and_(or_(t.c.user_id.is_(None), t.c.user_id != uid), or_(*bodies))
    if key == "test_case_comments.content":
        return _contains(t.c.content, "@" + person.username)
    return None


# ── the preview (a GET: reads only) ──────────────────────────────────────────

async def _measured_count(db: AsyncSession, statement: Any, params: Optional[dict] = None) -> Optional[int]:
    """``count(*)`` of ``statement`` in a connection-level SAVEPOINT (no session flush); a timeout or any
    database error → None ("not counted" — absence is not health)."""
    connection = await db.connection()
    try:
        async with connection.begin_nested():
            result = await connection.execute(statement, params or {})
            return int(result.scalar() or 0)
    except Exception as exc:  # noqa: BLE001 - a count that cannot finish reads "not counted"
        _slog.info("subject_erasure_preview_count_unmeasured", error_type=type(exc).__name__)
        return None


async def _bounded(db: AsyncSession, timeout_ms: Optional[int]) -> None:
    """``SET LOCAL statement_timeout`` (Postgres only; ``None`` = back to the session default)."""
    connection = await db.connection()
    if connection.dialect.name != "postgresql":
        return
    if timeout_ms is None:
        await connection.execute(text("SET LOCAL statement_timeout TO DEFAULT"))
    else:
        await connection.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))


async def _count_mentions(db: AsyncSession, location: Location, person: Identity) -> Optional[int]:
    """Comments whose text really carries the ``@<username>`` token (the prefilter over-matches
    ``@alice2``); the rows are read in a SAVEPOINT and matched with :func:`mention_pattern`."""
    clause = match_clause(location, person)
    if clause is None:
        return None
    t = _table(location.table or "")
    pattern = mention_pattern(person.username)
    connection = await db.connection()
    try:
        async with connection.begin_nested():
            result = await connection.execute(select(t.c[location.column or ""]).where(clause))
            return sum(1 for (value,) in result.all() if value and pattern.search(value))
    except Exception as exc:  # noqa: BLE001
        _slog.info("subject_erasure_preview_count_unmeasured", error_type=type(exc).__name__)
        return None


async def _location_count(db: AsyncSession, location: Location, person: Identity) -> Optional[int]:
    if location.key == "users.*":
        return 1
    if location.key == "test_case_comments.content":
        return await _count_mentions(db, location, person)
    if location.table == "auth_token_revocations":
        return await _measured_count(
            db, text("SELECT count(*) FROM auth_token_revocations WHERE user_id = :uid"), {"uid": person.user_id},
        )
    clause = match_clause(location, person)
    if clause is None:
        return None
    return await _measured_count(db, select(func.count()).select_from(_table(location.table or "")).where(clause))


def _entry(location: Location, count: Optional[int]) -> ErasureLocationCount:
    return ErasureLocationCount(
        key=location.key, label=location.label, action=location.erase, count=count,
        measured=count is not None, reason=location.reason or None,
    )


async def _object_entries(db: AsyncSession, person: Identity) -> list[ErasureLocationCount]:
    """The object-store changes: the person's own export archives (counted from their job rows) and the
    live project exports that MAY list them (not counted: only reading every archive could tell)."""
    entries: list[ErasureLocationCount] = []
    by_key = {location.key: location for location in INVENTORY}
    own = await _measured_count(db, select(func.count()).select_from(SubjectRightsJob).where(
        SubjectRightsJob.subject_user_id == person.user_id, SubjectRightsJob.kind == KIND_EXPORT,
        SubjectRightsJob.storage_key.isnot(None),
    ))
    if own is None or own > 0:
        entries.append(_entry(by_key["objects:subject-exports/"], own))
    live = await _measured_count(db, select(func.count()).select_from(ProjectDataJob).where(
        ProjectDataJob.kind == "export", ProjectDataJob.status == "succeeded", ProjectDataJob.storage_key.isnot(None),
    ))
    if live is None or live > 0:
        entries.append(_entry(by_key["objects:project-exports/"], None))
    return entries


async def preview(db: AsyncSession, *, subject: User, actor: User) -> ErasurePreview:
    """What erasing ``subject`` would change, keep and leave open — READS ONLY (no flush, no write).
    Every count runs under ``PREVIEW_COUNT_TIMEOUT_MS``; a timed-out count reads ``count: None,
    measured: False``. Locations with nothing to change are left out; blockers are listed, never raised."""
    person = Identity.of(subject)
    codes = await blockers(db, subject=subject, actor=actor)
    erasure = await live_job(db, subject_id=subject.id, kind=KIND_ERASURE)
    changes: list[ErasureLocationCount] = []
    kept: list[ErasureLocationCount] = []
    open_items: list[ErasureLocationCount] = []
    await _bounded(db, PREVIEW_COUNT_TIMEOUT_MS)
    try:
        for location in INVENTORY:
            if location.store != "postgres":
                continue
            changing = location.erase in ("delete", "pseudonymise")
            keeping = location.erase in ("keep_audit", "keep_record") and location.match == "fk"
            if not (changing or keeping or location.open_item):
                continue
            count = await _location_count(db, location, person)
            if count == 0:
                continue
            bucket = changes if changing else open_items if location.open_item else kept
            bucket.append(_entry(location, count))
        changes.extend(await _object_entries(db, person))
        pseudonym = await choose_pseudonym(db, subject)
    finally:
        await _bounded(db, None)
    job_read = None
    if erasure is not None:
        names = await displays(db, [erasure])
        job_read = to_read(
            erasure, subject_display=names.get(erasure.subject_user_id),
            requested_by_display=names.get(erasure.requested_by_id) if erasure.requested_by_id else None,
            now=_utcnow(),
        )
    role = getattr(subject.role, "value", subject.role)
    return ErasurePreview(
        user_id=subject.id, username=subject.username,
        display=ERASED_DISPLAY if subject.erased_at is not None else person.display,
        role=str(role), is_active=bool(subject.is_active), pseudonym=pseudonym,
        blockers=[ErasureBlocker(code=code, message=ERROR_SENTENCES[code]) for code in codes],
        changes=changes, kept=kept, open_items=open_items, erasure_job=job_read,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _rowcount(result: Any) -> int:
    """Rows a DELETE / UPDATE touched (``CursorResult.rowcount``; 0 when the driver does not say)."""
    return int(getattr(result, "rowcount", 0) or 0)


# ── the lock-out (E-2: the route's ONE transaction) ──────────────────────────

async def _lock_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """The user row ``FOR UPDATE``, re-read from the database (``populate_existing``: the identity map
    may hold a stale copy under ``expire_on_commit=False``)."""
    result = await db.execute(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def stage_lockout(db: AsyncSession, *, subject: User, actor: User, job: SubjectRightsJob) -> None:
    """Disable the account, revoke every access token (the durable cutoff, IN this transaction), delete
    every session, disable every key, append ONE ``subject_erasure_requested`` access-audit row (actor =
    the caller) and put the job at phase ``lockout`` / ``queued``. Flushes; the route commits all of it
    together, then drops the membership cache and enqueues the worker."""
    from app.core.token_revocation import revoke_all_user_tokens

    user = await _lock_user(db, subject.id)
    was_active = bool(user.is_active)
    user.is_active = False
    await revoke_all_user_tokens(user.id, db)
    sessions = _table("refresh_token_records")
    deleted = await db.execute(delete(sessions).where(sessions.c.user_id == user.id))
    keys = _table("api_keys")
    await db.execute(update(keys).where(keys.c.user_id == user.id).values(is_active=False))
    await log_access_change(
        db, ACTION_REQUESTED, actor, target_user_id=user.id,
        before_value={"is_active": was_active}, after_value={"job_id": str(job.id)},
    )
    job.phase = "lockout"
    job.status = QUEUED
    job.counts = {**dict(job.counts or {}), LOCKOUT_COUNT_KEY: _rowcount(deleted)}
    await db.flush()
    _slog.info("subject_erasure_locked_out", job_id=str(job.id), user_id=str(user.id),
               sessions=_rowcount(deleted))


# ── the Postgres phase (E-3..E-7: the worker's ONE transaction) ──────────────

async def _delete_location(db: AsyncSession, location: Location, person: Identity, user: User) -> int:
    """One A-list location's rows deleted; returns how many. Never an audit table (the inventory guard
    pins every audit location to keep_audit; asserted again here)."""
    if location.table in AUDIT_TABLES:
        raise RuntimeError(f"refusing to delete from an audit table: {location.key}")
    if location.key == "mfa_recovery_codes.user_id":
        # A3 / A4 go through mfa_service.disable_mfa: the flag, the seed ciphertext, the replay cursor
        # and every recovery code (its DELETE); the user_totp secret_refs row is deleted next (A4).
        from app.services.mfa_service import disable_mfa

        codes = _table("mfa_recovery_codes")
        counted = await db.execute(select(func.count()).select_from(codes).where(codes.c.user_id == person.user_id))
        await disable_mfa(db, user)
        return int(counted.scalar() or 0)
    clause = match_clause(location, person)
    if clause is None:
        raise RuntimeError(f"no row filter for {location.key}")
    result = await db.execute(delete(_table(location.table or "")).where(clause))
    return _rowcount(result)


async def _update_where(db: AsyncSession, location: Location, person: Identity, **values: Any) -> int:
    clause = match_clause(location, person)
    if clause is None:
        raise RuntimeError(f"no row filter for {location.key}")
    result = await db.execute(update(_table(location.table or "")).where(clause).values(**values))
    return _rowcount(result)


async def _rewrite_rows(
    db: AsyncSession, location: Location, person: Identity, columns: tuple[str, ...], rewrite: Any,
    *, keep: tuple[str, ...] = (),
) -> int:
    """Rewrite the matching rows in Python, batched by ``REWRITE_BATCH_ROWS`` in id order (keyset); write
    back ONLY the rows whose values changed (``keep`` columns are written back as they are, so an
    ``onupdate`` timestamp does not move). Returns how many rows changed."""
    clause = match_clause(location, person)
    if clause is None:
        raise RuntimeError(f"no row filter for {location.key}")
    t = _table(location.table or "")
    changed = 0
    last_id: Optional[uuid.UUID] = None
    while True:
        statement = select(t.c.id, *(t.c[name] for name in columns)).where(clause)
        if last_id is not None:
            statement = statement.where(t.c.id > last_id)
        rows = (await db.execute(statement.order_by(t.c.id).limit(REWRITE_BATCH_ROWS))).all()
        if not rows:
            return changed
        for row in rows:
            before = {name: getattr(row, name) for name in columns}
            after = rewrite(dict(before))
            if after != before:
                values = {name: after[name] for name in columns if after[name] != before[name]}
                values.update({name: t.c[name] for name in keep})
                await db.execute(update(t).where(t.c.id == row.id).values(**values))
                changed += 1
        last_id = rows[-1].id
        if len(rows) < REWRITE_BATCH_ROWS:
            return changed


def _cancelled_by_email(value: dict, *, email: str, pseudonym: str) -> dict:
    error = value.get("error")
    if isinstance(error, str) and error.endswith("by " + email):
        value["error"] = error[: len(error) - len(email)] + pseudonym
    return value


def _payload_requested_by(value: dict, *, person: Identity, pseudonym: str) -> dict:
    payload = value.get("event_payload")
    if isinstance(payload, dict) and payload.get("requested_by") in (person.username, person.email):
        value["event_payload"] = {**payload, "requested_by": pseudonym}
    return value


def _notification_title(value: dict, *, person: Identity, pseudonym: str) -> dict:
    title = rewrite_mentions(value.get("title") or "", username=person.username, pseudonym=pseudonym)
    title = _replace_name(title, person.full_name, ERASED_DISPLAY)
    value["title"] = _replace_name(title, person.username, ERASED_DISPLAY)
    return value


def _notification_body(value: dict, *, person: Identity, pseudonym: str) -> dict:
    body = rewrite_mentions(value.get("body") or "", username=person.username, pseudonym=pseudonym)
    if person.full_name and person.full_name.strip():
        owner = re.compile(r"owner: " + re.escape(person.full_name.strip()) + r"(?![\w-])", re.IGNORECASE)
        body = owner.sub(lambda _match: "owner: " + ERASED_DISPLAY, body)
    value["body"] = body
    return value


async def _rewrite_values(db: AsyncSession, person: Identity, pseudonym: str) -> dict[str, int]:
    """B2–B6 and the four value locations G-A's re-read found (§2.1 / the After-A0 list), in inventory
    order. Every match uses the OLD values held in ``person``."""
    by_key = {location.key: location for location in INVENTORY}
    counts: dict[str, int] = {}
    counts["project_insights.dismissed_by_name"] = await _update_where(
        db, by_key["project_insights.dismissed_by_name"], person, dismissed_by_name=ERASED_DISPLAY)
    counts["report_share_links.created_by_name"] = await _update_where(
        db, by_key["report_share_links.created_by_name"], person, created_by_name=ERASED_DISPLAY)
    counts["agent_investigations.cancelled_by"] = await _update_where(
        db, by_key["agent_investigations.cancelled_by"], person, cancelled_by=pseudonym)
    counts["agent_pipeline_runs.error"] = await _rewrite_rows(
        db, by_key["agent_pipeline_runs.error"], person, ("error",),
        lambda value: _cancelled_by_email(value, email=person.email, pseudonym=pseudonym))
    counts["test_case_comments.content"] = await _rewrite_rows(
        db, by_key["test_case_comments.content"], person, ("content",),
        lambda value: {"content": rewrite_mentions(value["content"], username=person.username,
                                                   pseudonym=pseudonym)},
        keep=("updated_at",))
    counts["webhook_deliveries.event_payload"] = await _rewrite_rows(
        db, by_key["webhook_deliveries.event_payload"], person, ("event_payload",),
        lambda value: _payload_requested_by(value, person=person, pseudonym=pseudonym))
    counts["notification_logs.title"] = await _rewrite_rows(
        db, by_key["notification_logs.title"], person, ("title",),
        lambda value: _notification_title(value, person=person, pseudonym=pseudonym))
    counts["notification_logs.body"] = await _rewrite_rows(
        db, by_key["notification_logs.body"], person, ("body",),
        lambda value: _notification_body(value, person=person, pseudonym=pseudonym))
    return counts


async def _pseudonymise_row(db: AsyncSession, user: User, *, pseudonym: str, now: datetime) -> None:
    """B1, last: the row keeps id / role / created_at / the flags; every personal field is overwritten.
    The password marker is a password change, so both revocations run again here, in this transaction
    (the lock-out already did both; re-asserting them is a no-op that keeps every password-change path
    revoking — tests/regression/test_password_change_ends_sessions.py)."""
    from app.core.token_revocation import revoke_all_user_tokens
    from app.services.refresh_token_service import _revoke_family

    await revoke_all_user_tokens(user.id, db)
    await _revoke_family(db, user.id, reason="revoked")
    user.username = pseudonym
    user.email = erased_email_for(pseudonym)
    user.full_name = ERASED_DISPLAY
    user.avatar_color = None
    user.hashed_password = ERASED_HASH_MARKER
    user.mfa_enabled = False
    user.mfa_enrolled_at = None
    user.mfa_last_used_step = None
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = None
    user.must_change_password = False
    user.is_active = False
    if user.erased_at is None:
        user.erased_at = now


async def run_postgres_phase(db: AsyncSession, *, job_id: uuid.UUID, now: datetime) -> dict[str, int]:
    """E-3: lock the user row, read the CURRENT username / e-mail / name into locals, choose the pseudonym,
    then — in inventory order — the A deletes, the B rewrites, the ``ml:retrain:*`` Redis rewrite (best
    effort, matched on the old username), and the user row LAST. Returns the per-location counts (the
    lock-out's session count added in). Flushes only: the caller sets the phase and commits, and a raise
    anywhere rolls ALL of it back (the old values are still on the row; the next run starts again)."""
    job = await db.get(SubjectRightsJob, job_id, populate_existing=True)
    if job is None or job.kind != KIND_ERASURE:
        raise LookupError(f"no erasure job {job_id}")
    user = await _lock_user(db, job.subject_user_id)
    person = Identity.of(user)
    pseudonym = await choose_pseudonym(db, user)
    counts: dict[str, int] = {}
    for location in postgres_locations(erase="delete"):
        counts[location.key] = await _delete_location(db, location, person, user)
    counts.update(await _rewrite_values(db, person, pseudonym))
    counts.update(await rewrite_retrain_keys(old_username=person.username, pseudonym=pseudonym))
    await _pseudonymise_row(db, user, pseudonym=pseudonym, now=now)
    counts["users.*"] = 1
    counts[LOCKOUT_COUNT_KEY] = counts.get(LOCKOUT_COUNT_KEY, 0) + int((job.counts or {}).get(LOCKOUT_COUNT_KEY, 0))
    await db.flush()
    _slog.info("subject_erasure_postgres_staged", job_id=str(job_id), user_id=str(user.id),
               locations=len(counts), rows=sum(counts.values()))
    return counts


# ── Redis (E-8 + the two ml:retrain keys) ────────────────────────────────────

def _redis_client(redis: Any = None) -> Any:
    if redis is not None:
        return redis
    from app.db.redis_client import get_redis

    return get_redis()


async def rewrite_retrain_keys(*, old_username: str, pseudonym: str, redis: Any = None) -> dict[str, int]:
    """``ml:retrain:last`` / ``ml:retrain:lock``: ``triggered_by "user:<old username>"`` →
    ``"user:<pseudonym>"``, keeping each key's TTL (``SET XX KEEPTTL``). Matched on the OLD username, so it
    runs inside the Postgres phase. Best effort: a Redis failure is logged (type only), never fatal, and
    its keys are then absent from the counts."""
    from app.services.ml.training_job import LAST_RUN_KEY, LOCK_KEY, user_trigger

    old_trigger, new_trigger = user_trigger(old_username), user_trigger(pseudonym)
    counts: dict[str, int] = {}
    try:
        client = _redis_client(redis)
        for key in (LAST_RUN_KEY, LOCK_KEY):
            raw = await client.get(key)
            value = json.loads(raw) if raw else None
            rewritten = 0
            if isinstance(value, dict) and value.get("triggered_by") == old_trigger:
                value["triggered_by"] = new_trigger
                rewritten = 1 if await client.set(key, json.dumps(value, default=str), xx=True, keepttl=True) else 0
            counts[f"redis:{key}"] = rewritten
    except Exception as exc:  # noqa: BLE001 - best effort (the lock lives ≤ 1 h, the last run 30 days)
        _slog.warning("subject_erasure_retrain_rewrite_failed", error_type=type(exc).__name__)
        return {}
    return counts


async def run_redis_phase(*, user_id: uuid.UUID, redis: Any = None) -> dict[str, int]:
    """E-8: ``membership:{id}`` and every ``…user:{id}…`` key (the read / rate-limit caches) deleted.
    Id-based and idempotent; best effort (each expires within minutes anyway): a Redis failure is logged
    and returns no counts."""
    counts: dict[str, int] = {}
    try:
        client = _redis_client(redis)
        counts["redis:membership:{id}"] = int(await client.delete(f"membership:{user_id}") or 0)
        removed = 0
        async for key in client.scan_iter(match=f"*user:{user_id}*", count=500):
            removed += int(await client.delete(key) or 0)
        counts["redis:user:{id}"] = removed
    except Exception as exc:  # noqa: BLE001
        _slog.warning("subject_erasure_redis_failed", user_id=str(user_id), error_type=type(exc).__name__)
    return counts


# ── objects (E-8: E1 the person's exports, E2 the project exports that list them) ──

def _is_missing(exc: BaseException) -> bool:
    """The object is gone (the local provider's FileNotFoundError, S3's NoSuchKey / 404)."""
    if isinstance(exc, FileNotFoundError):
        return True
    code = str(((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code") or "")
    return code in {"NoSuchKey", "404", "NotFound"}


def _directory_lists(spool: Any, user_id: uuid.UUID) -> bool:
    """True when the archive's ``directory/users.jsonl`` names ``user_id`` (only that member is read)."""
    spool.seek(0)
    with zipfile.ZipFile(spool) as archive:
        try:
            info = archive.getinfo(_DIRECTORY_MEMBER)
        except KeyError:
            return False
        if info.file_size > _DIRECTORY_MAX_BYTES:
            raise ValueError("directory member too large")
        wanted = str(user_id)
        with archive.open(info) as member:
            for line in member:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if isinstance(entry, dict) and str(entry.get("id")) == wanted:
                    return True
    return False


async def _archive_lists(storage: Any, *, key: str, user_id: uuid.UUID, bucket: str) -> Optional[bool]:
    """Spool the stored archive and read its directory; None when the object no longer exists."""
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES, mode="w+b") as spool:
        try:
            async for chunk in storage.stream_object(key, bucket=bucket):
                spool.write(chunk)
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise
        return await asyncio.to_thread(_directory_lists, spool, user_id)


async def remove_project_exports_listing(db: AsyncSession, storage: Any, *, user_id: uuid.UUID) -> int:
    """E2: every LIVE project export (``kind='export' AND status='succeeded' AND storage_key IS NOT NULL``)
    whose ``directory/users.jsonl`` lists the person → the job ``expired`` / ``subject_erased`` (a guarded
    UPDATE … WHERE id AND status = 'succeeded', staged first) and the object removed. A missing object is
    skipped (W24's janitor expires its job); a storage fault raises (the phase is re-run)."""
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    live = (await db.execute(
        select(ProjectDataJob.id, ProjectDataJob.storage_key).where(
            ProjectDataJob.kind == "export", ProjectDataJob.status == "succeeded",
            ProjectDataJob.storage_key.isnot(None),
        ).order_by(ProjectDataJob.id)
    )).all()
    removed = 0
    for job_id, key in live:
        listed = await _archive_lists(storage, key=key, user_id=user_id, bucket=COMPLIANCE_BUCKET)
        if not listed:
            continue
        result = await db.execute(
            update(ProjectDataJob)
            .where(ProjectDataJob.id == job_id, ProjectDataJob.status == "succeeded")
            .values(status="expired", error_code="subject_erased", error=ERROR_SENTENCES["project_export_removed"],
                    storage_key=None)
            .execution_options(synchronize_session=False)
        )
        if _rowcount(result):
            await storage.delete_object(key, bucket=COMPLIANCE_BUCKET)
            removed += _rowcount(result)
    return removed


async def run_object_phase(db: AsyncSession, storage: Any, *, job_id: uuid.UUID) -> dict[str, int]:
    """E-8 objects: E1 every export of the person (G-B's ``remove_exports_of``: object removed, job
    ``expired``) and E2 the live project exports that list them. Id-based and idempotent; flushes only (the
    runner sets the phase and commits). A storage fault raises → ``erasure_incomplete``, re-run."""
    job = await db.get(SubjectRightsJob, job_id, populate_existing=True)
    if job is None or job.kind != KIND_ERASURE:
        raise LookupError(f"no erasure job {job_id}")
    from app.services import subject_export_service

    counts = {
        "objects:subject-exports/": int(
            await subject_export_service.remove_exports_of(db, storage, subject_id=job.subject_user_id)
        ),
        "objects:project-exports/": await remove_project_exports_listing(db, storage, user_id=job.subject_user_id),
    }
    await db.flush()
    return counts


# ── the closing audit row (E-11) ─────────────────────────────────────────────

async def stage_erased_audit(db: AsyncSession, *, job: SubjectRightsJob) -> None:
    """ONE ``subject_erased`` access-audit row at phase ``done``: actor = the requesting admin (from the job
    row), target = the subject, ``after_value = {"job_id", "counts"}`` — keys and counts only, never a name,
    never the pseudonym. ``record_activity`` is NOT called (no project event)."""
    actor = await db.get(User, job.requested_by_id) if job.requested_by_id else None
    counts = {str(key): int(value) for key, value in dict(job.counts or {}).items()}
    await log_access_change(
        db, ACTION_ERASED, actor, target_user_id=job.subject_user_id,
        after_value={"job_id": str(job.id), "counts": counts},
    )
