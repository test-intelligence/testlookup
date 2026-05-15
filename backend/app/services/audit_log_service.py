"""Audit-log helpers — attempt vs outcome contract.

Provides two write paths so callers can pick the right durability vs
atomicity trade-off:

* ``record_outcome(db, ...)`` — uses the caller's injected session.
  The audit row lives or dies with the primary mutation: a rollback of
  the primary mutation also rolls back the audit row. Use this when the
  audit row only makes sense in the context of a SUCCESSFUL primary
  mutation — e.g. "feature flag toggled to ON" should not be recorded
  if the toggle itself was rolled back.

* ``record_attempt(...)`` — opens a fresh ``AsyncSessionLocal()``.
  The audit row commits independently and SURVIVES a caller rollback.
  Use this when the act of *attempting* the mutation is audit-worthy
  on its own — e.g. "admin issued a project reset (mode=full)" should
  be recorded even if the reset later fails midway.

Both helpers swallow transient DB failures via ``async_retry`` against
``DB_RETRYABLE_EXCEPTIONS`` and emit a structured WARNING on terminal
failure so dropped audit rows are greppable.

This module is the foundation called out as P2-6 in
``docs/DATABASE_AUDIT_2026-05-16.md``. Migrating every existing audit
call site to use these helpers is intentionally NOT in scope for the
helper-introduction PR — flaky_quarantine_service and webhook_service
already use the in-line equivalent of ``record_attempt`` (fresh
session + retry), so this module formalises the pattern. New audit
write sites should use these helpers from day one; legacy sites
migrate when their owning module is touched for feature work.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.resilience import DB_RETRYABLE_EXCEPTIONS, async_retry

logger = structlog.get_logger(__name__)


def _build_changed_fields(
    before: Optional[dict],
    after: Optional[dict],
    explicit: Optional[Iterable[str]] = None,
) -> Sequence[str]:
    """Derive the changed-fields list from before/after dicts.

    If the caller passes an explicit list (legacy contract for some
    services), honour it. Otherwise diff the dicts."""
    if explicit is not None:
        return list(explicit)
    if before is not None and after is not None:
        return sorted(
            k for k in (set(before) | set(after))
            if before.get(k) != after.get(k)
        )
    if after is not None:
        return sorted(after.keys())
    if before is not None:
        return sorted(before.keys())
    return []


async def record_outcome(
    db: AsyncSession,
    *,
    setting_key: str,
    action: str,
    actor_id: Optional[Any] = None,
    actor_name: Optional[str] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    changed_fields: Optional[Iterable[str]] = None,
) -> None:
    """Write a SettingsAuditLog row on the caller's session.

    The audit row participates in the caller's transaction — it commits
    when the caller commits, and rolls back when the caller rolls back.
    Use for "this is what happened" audit semantics.

    Never raises; emits a WARNING on failure so the absence is logged.
    """
    from app.models.postgres import SettingsAuditLog

    try:
        entry = SettingsAuditLog(
            setting_key=setting_key,
            action=action,
            actor_id=actor_id,
            actor_name=actor_name,
            changed_fields=list(_build_changed_fields(before, after, changed_fields)),
        )
        db.add(entry)
    except Exception as exc:
        logger.warning(
            "audit_log_outcome_dropped",
            setting_key=setting_key,
            action=action,
            error_type=type(exc).__name__,
            error=str(exc),
        )


async def record_attempt(
    *,
    setting_key: str,
    action: str,
    actor_id: Optional[Any] = None,
    actor_name: Optional[str] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    changed_fields: Optional[Iterable[str]] = None,
    max_retries: int = 2,
) -> None:
    """Write a SettingsAuditLog row on a fresh session.

    The audit row commits independently of any caller transaction.
    Use for "this attempt happened" audit semantics — the row survives
    a caller rollback so operators reconciling state can see what was
    tried even if it failed.

    Retries transient DB faults via ``async_retry`` against
    ``DB_RETRYABLE_EXCEPTIONS``. Never raises; emits a structured
    WARNING (``audit_log_attempt_dropped``) on terminal failure.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import SettingsAuditLog

    entry_kwargs = dict(
        setting_key=setting_key,
        action=action,
        actor_id=actor_id,
        actor_name=actor_name,
        changed_fields=list(_build_changed_fields(before, after, changed_fields)),
    )

    async def _do_write() -> None:
        async with AsyncSessionLocal() as audit_db:
            try:
                audit_db.add(SettingsAuditLog(**entry_kwargs))
                await audit_db.commit()
            except Exception:
                await audit_db.rollback()
                raise

    try:
        await async_retry(
            _do_write,
            max_retries=max_retries,
            base_delay=0.1,
            max_delay=2.0,
            retryable_exceptions=DB_RETRYABLE_EXCEPTIONS,
            operation_name=f"audit_attempt:{action}",
        )
    except Exception as exc:
        logger.warning(
            "audit_log_attempt_dropped",
            setting_key=setting_key,
            action=action,
            error_type=type(exc).__name__,
            error=str(exc),
        )
