"""W25e HYG-06 (E-1) — publish a just-committed close's staged outbox intent now (contract §1.1).

Before W25e a closed live run's ``persist_live_session`` intent waited for the
next relay tick (once a minute), so the SDK path's finalize lag was U(0, 60) s
on cadence alone. :func:`publish_staged_now` runs right after the close's
commit and publishes that intent at once, through the relay's own claim
transition (``_lease_for_dispatch``), publisher (``_publish_downstream``) and
mark (``mark_downstream_dispatch_published``).

The relay stays the safety net. Every failure here leaves the row where the
relay already recovers it: ``pending`` (the claim never committed) or
``sending`` with a short lease and the SAME dispatch token (the publish or the
mark failed). This module never marks a row ``failed`` and never counts a
dispatch failure: a broker timeout is ambiguous — the message may have reached
the broker — and ``begin_downstream_execution`` fences a second copy carrying
the same token. A timeout is not a failure.

Amendment A-1: Celery's ``apply_async`` is synchronous. On the SDK close path a
slow or black-holed broker would block the API process's event loop for every
request, so the publish runs in a worker thread, bounded by
``PUBLISH_NOW_TIMEOUT_SECONDS`` (read at call time so a test can patch it). A
timeout is a deferral like any other publish failure.

The commits live here, outside ``app/services``, so the service's commit
allowlist (7) and the allowlist total do not move — the precedent of
``worker/suite_parity_runner.py`` and ``worker/project_data_runner.py``.
"""
from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import run_downstream_outbox

_slog = structlog.get_logger(__name__)

PUBLISH_NOW_OUTCOMES = ("published", "nothing_pending", "deferred")
LIVE_CLOSE_OPERATIONS = ("persist_live_session",)
#: How long one broker publish may take before the close stops waiting for it.
#: The thread is not killed (it cannot be); its late result is discarded and
#: the row keeps its 30 s lease and token, so a late delivery and the relay's
#: republish are fenced by ``begin_downstream_execution``.
PUBLISH_NOW_TIMEOUT_SECONDS = 3.0


async def _rollback_quietly(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:  # noqa: BLE001 — the caller's outcome is already decided
        pass


async def publish_staged_now(
    db: AsyncSession,
    *,
    run_id: uuid.UUID | str,
    operations: tuple[str, ...] = LIVE_CLOSE_OPERATIONS,
) -> str:
    """Publish a just-committed close's staged intent now instead of at the next relay tick. NEVER raises.

    Call only AFTER the caller's commit (and after ``finalize_closed_session_redis``
    on the close paths): the claim and the mark are two short transactions on
    the caller's session, which that commit released. Returns one of
    :data:`PUBLISH_NOW_OUTCOMES`: ``"published"`` when every claimed row was
    marked published, ``"nothing_pending"`` when this run had no due ``pending``
    intent of ``operations`` (or the relay holds it), else ``"deferred"`` — the
    relay publishes it at a later tick.
    """
    try:
        run_uuid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
        rows = await run_downstream_outbox.claim_staged_dispatches(
            db, run_id=run_uuid, operations=tuple(operations)
        )
        if not rows:
            await db.rollback()
            return "nothing_pending"
        # The lease is durable before the broker sees the message: a crash
        # after this point leaves 'sending' rows the relay reclaims with the
        # same token once the 30 s lease expires.
        await db.commit()
        all_marked = True
        for row in rows:
            token = row.dispatch_token
            if token is None:
                # _lease_for_dispatch always sets one; never publish a message
                # the completion fence could not recognise — leave it to the relay.
                raise RuntimeError("leased outbox row without a dispatch token")
            await asyncio.wait_for(
                asyncio.to_thread(run_downstream_outbox._publish_downstream, row),
                timeout=PUBLISH_NOW_TIMEOUT_SECONDS,
            )
            if await run_downstream_outbox.mark_downstream_dispatch_published(
                db, outbox_id=row.id, dispatch_token=token
            ):
                await db.commit()
            else:
                # The relay re-leased the row (new token) or it moved on; the
                # message we published is fenced by its stale token.
                await db.rollback()
                all_marked = False
                _slog.warning(
                    "run_downstream_publish_now_deferred",
                    run_id=str(run_id),
                    outbox_id=str(row.id),
                    reason="mark_refused",
                )
        return "published" if all_marked else "deferred"
    except Exception as exc:  # noqa: BLE001 — never raises into the close path
        await _rollback_quietly(db)
        _slog.warning(
            "run_downstream_publish_now_deferred",
            run_id=str(run_id),
            error_type=type(exc).__name__,
        )
        return "deferred"
