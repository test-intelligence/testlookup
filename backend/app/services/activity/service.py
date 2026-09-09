"""The single write path into the project activity ledger.

Everything that lands in ``project_activity_events`` goes through
:func:`record`. That is not style — it is what keeps four invariants true in
one place instead of at ninety call sites:

1. **The event is registered.** An unregistered name cannot enter the table.
2. **Secrets are redacted at WRITE time**, never at read time. A value that
   never entered the row cannot leak through an export, a CLI, an MCP tool, or
   a future endpoint nobody has written yet.
3. **The durability contract matches the event**, via the registry's
   ``write_mode`` — see :class:`~app.services.activity.events.ActivityEventSpec`.
4. **A ledger failure never fails the user's mutation.** Every path here
   swallows, counts and logs. The ledger is a read surface; losing a row is a
   reporting bug, and taking down a policy update to record that a policy was
   updated would be a much worse one.

Why this does not call ``audit_log_service.record_outcome``
-----------------------------------------------------------
Those helpers are hard-wired to ``SettingsAuditLog`` (they take
``setting_key``). This module reimplements the same attempt-vs-outcome
contract, including the ``async_retry`` over ``DB_RETRYABLE_EXCEPTIONS`` and
the structured drop warning, against ``ProjectActivityEvent``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.activity.events import (
    ActivityEventSpec,
    UnknownActivityEvent,
    lookup,
    render_summary,
)

logger = structlog.get_logger("services.activity")

#: How long after an identical (project, group_key, event_type) row a repeat is
#: treated as the same occurrence and dropped. Exists because Celery redelivers:
#: a retried ``finalize_run`` would otherwise write a second "run completed".
_DEDUP_WINDOW = timedelta(seconds=60)

def _strict_default() -> bool:
    """Raise under pytest, drop-and-count in production.

    A typo in an event name is a bug that CI must catch, and a condition that
    production must survive. Those want opposite behaviour, so the default is
    read from the environment rather than pinned: ``PYTEST_CURRENT_TEST`` is set
    by pytest for the duration of each test, and by nothing else.
    """
    import os

    return "PYTEST_CURRENT_TEST" in os.environ


class ActorRef:
    """Who did the thing.

    Deliberately a plain class rather than a Pydantic model: this is built on
    every mutation, including inside Celery tasks that have no request context,
    and it must never be able to raise a validation error on the write path.
    """

    __slots__ = ("actor_type", "actor_id", "actor_name", "actor_ref")

    def __init__(
        self,
        actor_type: str,
        *,
        actor_id: Optional[uuid.UUID] = None,
        actor_name: Optional[str] = None,
        actor_ref: Optional[str] = None,
    ) -> None:
        self.actor_type = actor_type
        self.actor_id = actor_id
        self.actor_name = actor_name
        self.actor_ref = actor_ref

    # ── Constructors, one per way an action can reach the system ───────────

    @classmethod
    def from_user(cls, user: Any) -> "ActorRef":
        """A signed-in human, a service account, or an API-key principal.

        The three are distinguished here rather than by the caller, because
        every caller has the same ``current_user`` object and would otherwise
        have to re-derive the distinction (and drift).
        """
        if user is None:
            return cls.system("anonymous")

        name = (
            getattr(user, "full_name", None)
            or getattr(user, "username", None)
            or getattr(user, "email", None)
        )
        # An API key authenticates AS a user row, so the key marker is the only
        # thing separating "Priya clicked a button" from "Priya's CI token did".
        key_prefix = getattr(user, "api_key_prefix", None)
        if key_prefix:
            return cls(
                "api_key",
                actor_id=getattr(user, "id", None),
                actor_name=name,
                actor_ref=str(key_prefix)[:120],
            )
        if getattr(user, "is_service_account", False):
            return cls(
                "service_account",
                actor_id=getattr(user, "id", None),
                actor_name=name,
            )
        return cls("user", actor_id=getattr(user, "id", None), actor_name=name)

    @classmethod
    def system(cls, ref: str) -> "ActorRef":
        """A scheduler, a beat task, or an internal sync. ``ref`` names which."""
        return cls("system", actor_name=ref, actor_ref=ref[:120])

    @classmethod
    def agent(cls, agent_name: str) -> "ActorRef":
        """A governed AI agent (Investigator, Fixer, DefectCommander)."""
        return cls("agent", actor_name=agent_name, actor_ref=agent_name[:120])


def _redact(payload: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """Redact at write time. Import is local to avoid a circular import."""
    if payload is None:
        return None
    try:
        from app.services.redaction_service import redact_dict

        redacted = redact_dict(dict(payload))
        # redact_dict returns a str when the whole payload is sensitive.
        return redacted if isinstance(redacted, dict) else {"redacted": str(redacted)}
    except Exception as exc:
        # Never let a redaction failure put an UNREDACTED payload in the table.
        # Dropping the payload loses detail; keeping it could leak a secret.
        logger.warning(
            "activity_redaction_failed_payload_dropped",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return {"redacted": "payload dropped: redaction failed"}


def _count_written(category: str) -> None:
    """Count a ledger row that landed.

    Declaration is not emission: these counters exist so an operator can tell a
    quiet ledger from a broken one, and a declared-but-never-incremented
    counter exports a confident 0.0 that reads as health.

    Referenced by NAME rather than looked up with ``getattr`` on purpose. A
    dynamic lookup is invisible to the ``backend.metrics-are-emitted`` gate,
    and — worse — a typo in the string would silently no-op forever, which is
    the exact failure the counter exists to detect.
    """
    try:
        from app.core.metrics import activity_events_written_total

        activity_events_written_total.labels(category=category).inc()
    except Exception:  # pragma: no cover - telemetry must never break a write
        pass


def _count_dropped(reason: str) -> None:
    """Count a ledger row that did not land, and why. See :func:`_count_written`."""
    try:
        from app.core.metrics import activity_events_dropped_total

        activity_events_dropped_total.labels(reason=reason).inc()
    except Exception:  # pragma: no cover - telemetry must never break a write
        pass


def _build_diff(
    before: Optional[Mapping[str, Any]],
    after: Optional[Mapping[str, Any]],
    changed_fields: Optional[Sequence[str]],
) -> Optional[dict]:
    """Assemble the redacted before/after payload.

    When the caller passes only ``changed_fields`` (the contract for
    secret-bearing objects: API keys, webhook secrets, SMTP, integration
    tokens), no values are stored at all — just the names.
    """
    if before is None and after is None:
        if not changed_fields:
            return None
        return {"changed_fields": list(changed_fields)}

    computed: list[str]
    if changed_fields is not None:
        computed = list(changed_fields)
    elif before is not None and after is not None:
        computed = sorted(
            k for k in (set(before) | set(after)) if before.get(k) != after.get(k)
        )
    else:
        computed = sorted((after or before or {}).keys())

    return {
        "before": _redact(before),
        "after": _redact(after),
        "changed_fields": computed,
    }


async def _is_duplicate(
    db: AsyncSession, project_id: uuid.UUID, event_type: str, group_key: str
) -> bool:
    """Has this exact grouped event already landed inside the dedup window?"""
    from app.models.postgres import ProjectActivityEvent

    cutoff = datetime.now(timezone.utc) - _DEDUP_WINDOW
    existing = await db.execute(
        select(ProjectActivityEvent.id)
        .where(
            ProjectActivityEvent.project_id == project_id,
            ProjectActivityEvent.event_type == event_type,
            ProjectActivityEvent.group_key == group_key,
            ProjectActivityEvent.occurred_at >= cutoff,
        )
        .limit(1)
    )
    return existing.scalar_one_or_none() is not None


def _build_row(
    *,
    spec: ActivityEventSpec,
    project_id: uuid.UUID,
    actor: ActorRef,
    entity_id: str,
    entity_label: Optional[str],
    entity_type: Optional[str],
    release_id: Optional[uuid.UUID],
    target_type: Optional[str],
    target_id: Optional[str],
    context: Optional[Mapping[str, Any]],
    diff: Optional[dict],
    source_table: Optional[str],
    source_id: Optional[uuid.UUID],
    request_id: Optional[str],
    group_key: Optional[str],
    occurred_at: Optional[datetime],
) -> Any:
    from app.models.postgres import ProjectActivityEvent

    safe_context = _redact(context)
    summary = render_summary(
        spec,
        entity_label=entity_label,
        actor_name=actor.actor_name,
        context=safe_context,
    )
    return ProjectActivityEvent(
        id=uuid.uuid4(),
        project_id=project_id,
        release_id=release_id,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        category=spec.category,
        event_type=spec.event_type,
        schema_version=1,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        actor_name=(actor.actor_name or None) and str(actor.actor_name)[:200],
        actor_ref=(actor.actor_ref or None) and str(actor.actor_ref)[:120],
        entity_type=entity_type or spec.entity_type,
        entity_id=str(entity_id)[:120],
        entity_label=entity_label and str(entity_label)[:300],
        target_type=target_type,
        target_id=target_id and str(target_id)[:120],
        summary=summary,
        diff=diff,
        context=safe_context,
        source_table=source_table,
        source_id=source_id,
        request_id=request_id and str(request_id)[:64],
        group_key=group_key and str(group_key)[:120],
    )


async def record(
    db: Optional[AsyncSession],
    *,
    project_id: uuid.UUID | str,
    event_type: str,
    actor: ActorRef,
    entity_id: uuid.UUID | str,
    entity_label: Optional[str] = None,
    entity_type: Optional[str] = None,
    release_id: Optional[uuid.UUID] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    context: Optional[Mapping[str, Any]] = None,
    before: Optional[Mapping[str, Any]] = None,
    after: Optional[Mapping[str, Any]] = None,
    changed_fields: Optional[Sequence[str]] = None,
    source_table: Optional[str] = None,
    source_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
    group_key: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    strict: Optional[bool] = None,
) -> None:
    """Record one activity event. Never raises in production.

    ``db`` is the caller's session for ``outcome`` events and may be ``None``
    for ``attempt`` events, which open their own. Passing a session for an
    attempt event is fine and common — the session is simply not used for the
    write, so the row still survives the caller's rollback.

    Secret-bearing callers pass ``changed_fields`` and omit ``before``/``after``
    so no value is ever stored.
    """
    strict = _strict_default() if strict is None else strict

    try:
        spec = lookup(event_type)
    except UnknownActivityEvent:
        _count_dropped("unregistered_event")
        logger.warning("activity_event_unregistered", event_type=event_type)
        if strict:
            raise
        return

    if actor.actor_type not in spec.actor_types:
        _count_dropped("actor_type_not_allowed")
        logger.warning(
            "activity_actor_type_not_allowed",
            event_type=event_type,
            actor_type=actor.actor_type,
        )
        if strict:
            raise ValueError(
                f"{event_type} cannot be emitted by actor_type={actor.actor_type}"
            )
        return

    try:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
    except (ValueError, AttributeError, TypeError):
        _count_dropped("bad_project_id")
        logger.warning("activity_bad_project_id", project_id=str(project_id))
        if strict:
            raise
        return

    diff = _build_diff(before, after, changed_fields)

    def _make_row() -> Any:
        return _build_row(
            spec=spec,
            project_id=pid,
            actor=actor,
            entity_id=str(entity_id),
            entity_label=entity_label,
            entity_type=entity_type,
            release_id=release_id,
            target_type=target_type,
            target_id=target_id,
            context=context,
            diff=diff,
            source_table=source_table,
            source_id=source_id,
            request_id=request_id,
            group_key=group_key,
            occurred_at=occurred_at,
        )

    if spec.write_mode == "attempt":
        await _write_attempt(_make_row, spec, pid, group_key, strict)
        return

    # ── outcome: share the caller's transaction ────────────────────────────
    if db is None:
        _count_dropped("no_session_for_outcome")
        logger.warning("activity_outcome_without_session", event_type=event_type)
        if strict:
            raise ValueError(f"{event_type} is an outcome event and needs a session")
        return

    try:
        if group_key and await _is_duplicate(db, pid, spec.event_type, group_key):
            _count_dropped("duplicate")
            return
        db.add(_make_row())
        _count_written(spec.category)
    except Exception as exc:
        _count_dropped("outcome_write_failed")
        logger.warning(
            "activity_event_dropped",
            event_type=event_type,
            mode="outcome",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        if strict:
            raise


async def _write_attempt(
    make_row: Any,
    spec: ActivityEventSpec,
    project_id: uuid.UUID,
    group_key: Optional[str],
    strict: bool,
) -> None:
    """Write on a fresh session so the row survives a caller rollback."""
    from app.db.postgres import AsyncSessionLocal
    from app.services.resilience import DB_RETRYABLE_EXCEPTIONS, async_retry

    async def _do_write() -> None:
        async with AsyncSessionLocal() as own_db:
            try:
                if group_key and await _is_duplicate(
                    own_db, project_id, spec.event_type, group_key
                ):
                    return
                own_db.add(make_row())
                await own_db.commit()
            except Exception:
                await own_db.rollback()
                raise

    try:
        await async_retry(
            _do_write,
            max_retries=2,
            retryable_exceptions=DB_RETRYABLE_EXCEPTIONS,
            operation_name="activity_event_write",
        )
        _count_written(spec.category)
    except Exception as exc:
        _count_dropped("attempt_write_failed")
        logger.warning(
            "activity_event_dropped",
            event_type=spec.event_type,
            mode="attempt",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        if strict:
            raise
