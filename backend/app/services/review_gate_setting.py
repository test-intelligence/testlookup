"""The instance review-gate enforcement date (W23c AGT-10, contract section 2.1 G-1 ... G-4).

One ``app_settings`` row, key ``review_gate``, value ``{"enforced_from": <ISO-8601
UTC> | null}``. No row means unset, which is audit only: today's behaviour (D17;
nothing is written on upgrade and no migration names the key).

``report_distribution_policy.gate_enforced()`` is synchronous and has nine call
sites, so it cannot read Postgres. It reads a process-local snapshot instead:

* the snapshot is refreshed by :func:`refresh_snapshot` at the top of every
  async entry point of the policy module (and by the two sync-call contexts),
  at most every ``SNAPSHOT_TTL_SECONDS``;
* a refresh reads the Redis mirror ``CACHE_KEY`` first and, on a miss, the row
  on its OWN short session (never the caller's: the gate's callers run on
  request sessions mid-transaction and on fake sessions in tests), then fills
  the mirror;
* a PUT sets the deciding pod's snapshot at once and deletes the mirror, so
  every other pod reads the new value on its next refresh: within 30 s;
* a refresh that fails or takes longer than ``LOAD_TIMEOUT_SECONDS`` keeps the
  last value. A process that never loaded reads "unset" (today's behaviour).

The environment flag ``REVIEW_GATE_ENFORCED=true`` stays the hard override.

Stage-only: nothing here commits. Log lines carry error types and counts, never
a date a user typed.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.services.review_gate_vocab import (
    APP_SETTINGS_KEY,
    CACHE_KEY,
    CACHE_TTL_SECONDS,
    LOAD_TIMEOUT_SECONDS,
    SNAPSHOT_TTL_SECONDS,
    WOULD_REFUSE_ACTION,
    WOULD_REFUSE_WINDOW_DAYS,
)

__all__ = [
    "GateSnapshot",
    "WOULD_REFUSE_TIMEOUT_SECONDS",
    "after_commit",
    "current_snapshot",
    "enforced_now",
    "gate_state",
    "load_setting",
    "load_setting_record",
    "pin_snapshot_for_tests",
    "refresh_snapshot",
    "reset_snapshot_for_tests",
    "seed_snapshot",
    "stage_setting",
    "would_refuse_summary",
]

_slog = structlog.get_logger(__name__)

#: The two would-refuse read statements together (contract section 2.1 G-4).
WOULD_REFUSE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class GateSnapshot:
    """What this process last knew about the stored date."""

    loaded: bool
    enforced_from: Optional[datetime]
    fetched_at: float  # _clock() (monotonic) of the last refresh attempt


_NEVER_LOADED = GateSnapshot(False, None, -math.inf)


class _GateState:
    """This process's snapshot. A class, so a test can stand two "pods" side by side."""

    def __init__(self) -> None:
        self.snapshot: GateSnapshot = _NEVER_LOADED
        self.pinned: bool = False


_STATE = _GateState()


def _clock() -> float:
    return time.monotonic()


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def gate_state(
    enforced_from: Optional[datetime], *, env: bool, now: datetime
) -> tuple[bool, str]:
    """PURE: ``(enforced, source)`` with ``source`` one of ``GATE_SOURCES``."""
    if env:
        return True, "env"
    if enforced_from is None:
        return False, "unset"
    if _utc(enforced_from) <= _utc(now):
        return True, "date"
    return False, "scheduled"


def current_snapshot() -> GateSnapshot:
    return _STATE.snapshot


def enforced_now(now: Optional[datetime] = None) -> bool:
    """SYNC: the env flag, or this process's snapshot date at or before ``now``."""
    from app.core.config import settings  # noqa: PLC0415

    if bool(settings.REVIEW_GATE_ENFORCED):
        return True
    enforced_from = _STATE.snapshot.enforced_from
    if enforced_from is None:
        return False
    return _utc(enforced_from) <= _utc(now or datetime.now(timezone.utc))


def _parse_value(value: Any) -> Optional[datetime]:
    """The stored ``{"enforced_from": iso | None}``; ValueError when malformed."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("review_gate_value_not_an_object")
    raw = value.get("enforced_from")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("review_gate_enforced_from_not_a_string")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("review_gate_enforced_from_naive")
    return _utc(parsed)


def _serialise(enforced_from: Optional[datetime]) -> dict[str, Optional[str]]:
    return {"enforced_from": _utc(enforced_from).isoformat() if enforced_from else None}


def _value_or_none(value: Any) -> Optional[datetime]:
    """A malformed stored value reads as unset (audit only), with a warning."""
    try:
        return _parse_value(value)
    except (TypeError, ValueError) as exc:
        _slog.warning("review_gate_value_malformed", error_type=type(exc).__name__)
        return None


async def _read_record(db: Any) -> tuple[Optional[datetime], Optional[datetime]]:
    from app.models.postgres import AppSetting  # noqa: PLC0415

    row = (
        await db.execute(
            select(AppSetting.value, AppSetting.updated_at).where(
                AppSetting.key == APP_SETTINGS_KEY
            )
        )
    ).first()
    if row is None:
        return None, None
    return _value_or_none(row[0]), row[1]


async def load_setting(db: Any) -> Optional[datetime]:
    """The stored instant, read on ``db`` (the request session). Reads only."""
    enforced_from, _updated_at = await _read_record(db)
    return enforced_from


async def load_setting_record(db: Any) -> tuple[Optional[datetime], Optional[datetime]]:
    """``(enforced_from, updated_at)`` of the row, read on ``db``. Reads only."""
    return await _read_record(db)


async def _redis_mirror() -> tuple[bool, Optional[datetime]]:
    """``(hit, value)`` from the Redis mirror; any Redis error is a miss."""
    try:
        from app.db.redis_client import get_redis  # noqa: PLC0415

        cached = await get_redis().get(CACHE_KEY)
    except Exception as exc:  # noqa: BLE001 -- Redis down: read Postgres instead
        _slog.info("review_gate_cache_unavailable", error_type=type(exc).__name__)
        return False, None
    if not cached:
        return False, None
    try:
        return True, _parse_value(json.loads(cached))
    except (TypeError, ValueError) as exc:
        _slog.warning("review_gate_cache_malformed", error_type=type(exc).__name__)
        return False, None


async def _fill_mirror(enforced_from: Optional[datetime]) -> None:
    try:
        from app.db.redis_client import get_redis  # noqa: PLC0415

        await get_redis().setex(
            CACHE_KEY, CACHE_TTL_SECONDS, json.dumps(_serialise(enforced_from))
        )
    except Exception as exc:  # noqa: BLE001 -- the mirror is an accelerator only
        _slog.info("review_gate_cache_not_filled", error_type=type(exc).__name__)


async def _read_own_session() -> Optional[datetime]:
    """ONE read of the row on a short session of its own. Raises on a DB error."""
    from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        enforced_from, _updated_at = await _read_record(session)
    return enforced_from


async def _load_shared() -> Optional[datetime]:
    hit, value = await _redis_mirror()
    if hit:
        return value
    value = await _read_own_session()
    await _fill_mirror(value)
    return value


async def refresh_snapshot(*, force: bool = False) -> GateSnapshot:
    """Bring this process's snapshot up to date. Never raises.

    A no-op while the snapshot is younger than ``SNAPSHOT_TTL_SECONDS`` (unless
    ``force``) and while a test pinned it. Otherwise the Redis mirror, else the
    row on an own session, bounded together by ``LOAD_TIMEOUT_SECONDS``. A
    failure keeps the last value and stamps the attempt, so a dead store is
    retried once per TTL, not on every call.
    """
    state = _STATE
    if state.pinned:
        return state.snapshot
    last = state.snapshot
    if not force and _clock() - last.fetched_at < SNAPSHOT_TTL_SECONDS:
        return last
    try:
        value = await asyncio.wait_for(_load_shared(), timeout=LOAD_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 -- the gate must never raise into a caller
        _slog.warning("review_gate_refresh_failed", error_type=type(exc).__name__)
        state.snapshot = GateSnapshot(last.loaded, last.enforced_from, _clock())
        return state.snapshot
    state.snapshot = GateSnapshot(True, value, _clock())
    return state.snapshot


def seed_snapshot(enforced_from: Optional[datetime]) -> None:
    """Set this process's snapshot from a value just read or written on the
    request session (the GET, and :func:`after_commit`). A test pin wins."""
    state = _STATE
    if state.pinned:
        return
    state.snapshot = GateSnapshot(
        True, _utc(enforced_from) if enforced_from else None, _clock()
    )


async def stage_setting(
    db: Any, *, enforced_from: Optional[datetime], actor: Any
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Upsert the row and stage its settings-audit row. The caller commits.

    Returns ``(before, after)``. The same value again writes nothing and audits
    nothing, so a double click leaves one audit row.
    """
    from app.models.postgres import AppSetting  # noqa: PLC0415
    from app.services.settings_audit_service import log_settings_change  # noqa: PLC0415

    after = _utc(enforced_from) if enforced_from else None
    current = (
        await db.execute(
            select(AppSetting.value)
            .where(AppSetting.key == APP_SETTINGS_KEY)
            .with_for_update()
        )
    ).first()
    before = _value_or_none(current[0]) if current is not None else None
    if before == after:
        return before, after
    value = _serialise(after)
    actor_id = getattr(actor, "id", None)
    await db.execute(
        pg_insert(AppSetting)
        .values(key=APP_SETTINGS_KEY, value=value, updated_by=actor_id)
        .on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={"value": value, "updated_by": actor_id, "updated_at": func.now()},
        )
    )
    await log_settings_change(db, APP_SETTINGS_KEY, "updated", actor, ["enforced_from"])
    return before, after


async def after_commit(enforced_from: Optional[datetime]) -> None:
    """After the PUT's commit: this pod's snapshot at once, and the Redis mirror
    deleted so every other pod reads Postgres on its next refresh. Never raises."""
    seed_snapshot(enforced_from)
    try:
        from app.db.redis_client import get_redis  # noqa: PLC0415

        await asyncio.wait_for(get_redis().delete(CACHE_KEY), timeout=LOAD_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 -- the mirror expires on its own (CACHE_TTL_SECONDS)
        _slog.warning("review_gate_cache_not_deleted", error_type=type(exc).__name__)


def _not_measured() -> dict[str, Any]:
    return {
        "measured": False,
        "window_days": WOULD_REFUSE_WINDOW_DAYS,
        "since": None,
        "decisions": None,
        "runs": None,
        "by_channel": None,
    }


async def would_refuse_summary(db: Any, *, now: datetime) -> dict[str, Any]:
    """The would-refuse audit rows of the last 30 days. Reads only, never cached.

    Decisions (one row per channel per attempt) AND distinct runs, by channel.
    ``since`` is clipped to the oldest audit row, so a trail shorter than 30
    days is said. Any error or the 2 s bound: ``measured: False`` with every
    number ``None`` -- absence is not health, never 0.
    """
    from app.models.postgres import AccessAuditLog  # noqa: PLC0415

    window_start = _utc(now) - timedelta(days=WOULD_REFUSE_WINDOW_DAYS)
    in_window = (
        AccessAuditLog.action == WOULD_REFUSE_ACTION,
        AccessAuditLog.created_at >= window_start,
    )
    run_id = AccessAuditLog.after_value["run_id"].as_string()
    channel = AccessAuditLog.after_value["channel"].as_string()
    oldest = select(func.min(AccessAuditLog.created_at)).scalar_subquery()

    async def _read() -> tuple[Any, list[Any]]:
        totals = (
            await db.execute(
                select(
                    oldest.label("oldest"),
                    func.count().label("decisions"),
                    func.count(distinct(run_id)).label("runs"),
                ).where(*in_window)
            )
        ).one()
        per_channel = (
            await db.execute(
                select(channel.label("channel"), func.count().label("decisions"))
                .where(*in_window)
                .group_by(channel)
                .order_by(func.count().desc(), channel)
            )
        ).all()
        return totals, list(per_channel)

    try:
        totals, per_channel = await asyncio.wait_for(
            _read(), timeout=WOULD_REFUSE_TIMEOUT_SECONDS
        )
        oldest_at = totals[0]
        since = window_start
        if oldest_at is not None and _utc(oldest_at) > window_start:
            since = _utc(oldest_at)
        by_channel = [
            {
                "channel": str(row[0]) if row[0] is not None else "unknown",
                "decisions": int(row[1]),
            }
            for row in per_channel
        ]
        return {
            "measured": True,
            "window_days": WOULD_REFUSE_WINDOW_DAYS,
            "since": since,
            "decisions": int(totals[1] or 0),
            "runs": int(totals[2] or 0),
            "by_channel": by_channel,
        }
    except Exception as exc:  # noqa: BLE001 -- "Not measured", never a false 0
        _slog.warning("review_gate_would_refuse_not_measured", error_type=type(exc).__name__)
        return _not_measured()


def pin_snapshot_for_tests(enforced_from: Optional[datetime]) -> None:
    """Tests: a loaded snapshot that never expires and that no refresh, PUT or
    GET replaces. ``backend/tests/conftest.py`` pins ``None`` for the session."""
    _STATE.snapshot = GateSnapshot(
        True, _utc(enforced_from) if enforced_from else None, math.inf
    )
    _STATE.pinned = True


def reset_snapshot_for_tests() -> None:
    """Tests: back to a never-loaded, unpinned process (the real refresh runs)."""
    _STATE.snapshot = _NEVER_LOADED
    _STATE.pinned = False
