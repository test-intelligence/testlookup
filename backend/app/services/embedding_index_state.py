"""The embedding index's durable state: which generation is ACTIVE, which one is
being built, which ones are retiring (W12 SRCH-02).

One ``app_settings`` row, key ``embedding_index``; an absent row is the legacy
generation ``("", default)``, idle. Readers go through
``chroma_collections.current_generations()`` (memo + Redis); writers lock the
row (:func:`lock_state`), stage a new value (:func:`stage_state`) and commit in
their router or Celery task. **Stage-only**: nothing here commits.

The value::

    {"version": 1,
     "active":  {"prefix": "", "provider": "default", "model": "all-MiniLM-L6-v2"},
     "target":  null | {...},               # the generation being built
     "status":  "idle" | "building" | "failed",
     "fence":   7,                          # bumped by every new request: a stale build stops
     "families": {"test_case_search": {"state", "processed", "total", "cursor"}, ...},
     "retiring": [{..., "switched_at": iso}],   # + "replaced": true for a target replaced before it was active
     "requested_at", "requested_by", "switched_at", "last_error",
     "lease":   null | {"owner": str, "until": iso}}   # + "released": true when handed back between slices

``last_error`` is a sentence of at most 300 characters: the exception TYPE and a
fixed phrase, never a host, a key or a query.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from app.services.chroma_collections import (
    COLLECTION_FAMILIES,
    FAMILY_STATES,
    LEGACY_GENERATION,
    Generation,
    Generations,
    prefix_of,
)
from app.services.embedding_function import (
    CONFIGURED_UNSUPPORTED_NOTE,
    EMBEDDING_PROVIDERS,
    EmbedderSpec,
)

EMBEDDING_STATE_KEY = "embedding_index"           # app_settings.key
EMBEDDING_INDEX_STATUSES = ("idle", "building", "failed")
REBUILD_ALREADY_RUNNING = "A re-embed is already running. Wait for it, or change the setting again to replace it."
REBUILD_NOTHING_TO_DO = "Nothing to re-embed: the configured embedder and prefix are already in use."
STATE_UNREADABLE = "Could not read the embedding index state."

STATE_VERSION = 1
LAST_ERROR_MAX = 300


def _legacy_dict() -> dict[str, str]:
    return LEGACY_GENERATION.as_dict()


@dataclass(frozen=True, slots=True)
class EmbeddingIndexState:
    version: int = STATE_VERSION
    active: dict = field(default_factory=_legacy_dict)
    target: dict | None = None
    status: str = "idle"
    families: dict = field(default_factory=dict)
    retiring: list = field(default_factory=list)
    requested_at: str | None = None
    switched_at: str | None = None
    last_error: str | None = None
    lease: dict | None = None
    fence: int = 0
    requested_by: str | None = None

    def active_generation(self) -> Generation:
        try:
            return Generation.from_dict(self.active)
        except ValueError:
            return LEGACY_GENERATION

    def target_generation(self) -> Generation | None:
        if self.target is None:
            return None
        try:
            return Generation.from_dict(self.target)
        except ValueError:
            return None

    def retiring_generations(self) -> tuple[Generation, ...]:
        out: list[Generation] = []
        for item in self.retiring:
            try:
                gen = Generation.from_dict(item)
            except ValueError:
                continue
            if gen not in out:
                out.append(gen)
        return tuple(out)

    def progress(self) -> tuple[int, int | None]:
        """Documents written into the target so far, and the total (None when any is uncounted)."""
        processed = 0
        total: int | None = 0
        for fam in COLLECTION_FAMILIES:
            entry = self.families.get(fam.base)
            if not isinstance(entry, Mapping) or entry.get("state") == "skipped":
                continue
            processed += _as_int(entry.get("processed"))
            entry_total = entry.get("total")
            if total is not None and isinstance(entry_total, int) and not isinstance(entry_total, bool):
                total += entry_total
            else:
                total = None
        return processed, (total if self.families else None)

    def generations(self) -> Generations:
        processed, total = self.progress()
        return Generations(
            active=self.active_generation(),
            target=self.target_generation(),
            retiring=self.retiring_generations(),
            status=self.status,
            processed=processed,
            total=total,
            fence=self.fence,
        )

    def as_value(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "active": dict(self.active),
            "target": dict(self.target) if self.target is not None else None,
            "status": self.status,
            "fence": self.fence,
            "families": {k: dict(v) for k, v in self.families.items() if isinstance(v, Mapping)},
            "retiring": [dict(item) for item in self.retiring if isinstance(item, Mapping)],
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "switched_at": self.switched_at,
            "last_error": self.last_error,
            "lease": dict(self.lease) if self.lease is not None else None,
        }


DEFAULT_STATE = EmbeddingIndexState()


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _as_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _valid_generation_dict(value: Any) -> dict | None:
    try:
        parsed: dict[str, Any] = Generation.from_dict(value).as_dict()
    except (ValueError, TypeError):
        return None
    if isinstance(value, Mapping):
        if isinstance(value.get("switched_at"), str):
            parsed["switched_at"] = value["switched_at"]
        if value.get("replaced") is True:
            # A retiring TARGET that was replaced before it ever became active:
            # nothing reached it through a stale cache, so the retire task
            # drops it without a catch-up copy into the active generation.
            parsed["replaced"] = True
    return parsed


def parse_state(value: Any) -> EmbeddingIndexState:
    """Pure. Anything malformed -> the default (legacy, idle) state.

    A malformed ``active`` (or version) discards the whole value; a malformed
    optional field (target, families, retiring, lease) is dropped on its own.
    """
    if not isinstance(value, Mapping) or value.get("version", STATE_VERSION) != STATE_VERSION:
        return DEFAULT_STATE
    active = _valid_generation_dict(value.get("active", _legacy_dict()))
    if active is None:
        return DEFAULT_STATE
    active.pop("switched_at", None)
    active.pop("replaced", None)
    target = _valid_generation_dict(value.get("target")) if value.get("target") is not None else None
    if target is not None:
        target.pop("switched_at", None)
        target.pop("replaced", None)
    status = value.get("status")
    if status not in EMBEDDING_INDEX_STATUSES:
        status = "idle"
    if target is None and status in ("building", "failed"):
        status = "idle"   # a build (or its failure) always names its target
    families: dict[str, dict] = {}
    raw_families = value.get("families")
    if isinstance(raw_families, Mapping):
        known = {f.base for f in COLLECTION_FAMILIES}
        for base, entry in raw_families.items():
            if base in known and isinstance(entry, Mapping):
                state = entry.get("state") if entry.get("state") in FAMILY_STATES else "pending"
                total = entry.get("total")
                families[base] = {
                    "state": state,
                    "processed": _as_int(entry.get("processed")),
                    "total": total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None,
                    "cursor": _as_int(entry.get("cursor")),
                }
    retiring: list[dict] = []
    raw_retiring = value.get("retiring")
    if isinstance(raw_retiring, list):
        for item in raw_retiring:
            parsed = _valid_generation_dict(item)
            if parsed is not None:
                retiring.append(parsed)
    lease = value.get("lease")
    if not (isinstance(lease, Mapping) and isinstance(lease.get("owner"), str) and isinstance(lease.get("until"), str)):
        lease = None
    last_error = _as_text(value.get("last_error"))
    return EmbeddingIndexState(
        version=STATE_VERSION,
        active=active,
        target=target,
        status=status,
        families=families,
        retiring=retiring,
        requested_at=_as_text(value.get("requested_at")),
        switched_at=_as_text(value.get("switched_at")),
        last_error=last_error[:LAST_ERROR_MAX] if last_error else None,
        lease=dict(lease) if lease is not None else None,
        fence=_as_int(value.get("fence")),
        requested_by=_as_text(value.get("requested_by")),
    )


async def read_state(db: Any) -> EmbeddingIndexState | None:
    """ONE statement on the caller's session. NEVER raises: ``None`` when the read failed.

    An absent row is the default state (legacy, idle), not ``None``.
    """
    try:
        from sqlalchemy import select

        from app.models.postgres import AppSetting

        result = await db.execute(select(AppSetting).where(AppSetting.key == EMBEDDING_STATE_KEY))
        row = result.scalar_one_or_none()
        return parse_state(getattr(row, "value", None) if row is not None else None)
    except Exception:  # noqa: BLE001 - an unreadable state is "unknown", never a guess
        return None


async def lock_state(db: Any) -> tuple[Any, EmbeddingIndexState]:
    """``SELECT … FOR UPDATE`` of the state row (writers only).

    The row is first inserted when absent (``ON CONFLICT DO NOTHING``), so two
    first writers serialise on it too; the insert commits only with the
    caller's transaction.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.postgres import AppSetting

    await db.execute(
        pg_insert(AppSetting)
        .values(key=EMBEDDING_STATE_KEY, value=DEFAULT_STATE.as_value(), is_secret_backed=False)
        .on_conflict_do_nothing(index_elements=["key"])
    )
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == EMBEDDING_STATE_KEY).with_for_update()
    )
    row = result.scalar_one_or_none()
    return row, parse_state(getattr(row, "value", None) if row is not None else None)


def base_generation(state: EmbeddingIndexState) -> Generation:
    """What a settings change builds on: the target while one exists, else the active one."""
    return state.target_generation() or state.active_generation()


#: The worker's lease length: a build nobody has held for this long has no worker.
BUILD_STALL_SECONDS = 540


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def build_is_stalled(state: EmbeddingIndexState, *, now: datetime) -> bool:
    """True when a ``building`` state has no worker and none is about to take it.

    * a lease held by a slice -> stalled once it has EXPIRED (the worker died
      mid-slice: a live one renews it after every family slice);
    * a lease handed back between slices (``released``), or no lease yet (the
      build is queued) -> stalled when nothing claimed it within
      :data:`BUILD_STALL_SECONDS` of the hand-back / the request.

    ``POST /settings/ai/embedding-index/rebuild`` re-enqueues a stalled build
    (fence + 1) instead of answering 409. Pure.
    """
    if state.status != "building" or state.target_generation() is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    lease = state.lease
    if isinstance(lease, Mapping):
        until = _parse_iso(lease.get("until"))
        if until is None:
            return True
        if lease.get("released") is True:
            return (now - until).total_seconds() >= BUILD_STALL_SECONDS
        return until <= now
    requested = _parse_iso(state.requested_at)
    return requested is None or (now - requested).total_seconds() >= BUILD_STALL_SECONDS


def _iso(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat()


def request_target(
    state: EmbeddingIndexState,
    target: Generation,
    *,
    actor_id: str | None,
    now: datetime,
) -> EmbeddingIndexState:
    """Ask for ``target``. Equal to the active generation -> any target is cleared
    (idle); otherwise the target is REPLACED and the fence bumped, so a running
    build of an older request stops at its next checkpoint.

    A replaced (or cleared) target's partly built collections join
    ``retiring`` -- the retire task drops them after the grace period -- and a
    generation that is wanted again (the new target, or the active one) leaves
    ``retiring``, so nothing in use is ever dropped.
    """
    active = state.active_generation()
    old_target = state.target_generation()
    retiring = list(state.retiring)
    if old_target is not None and old_target != target and old_target != active:
        retiring.append({**old_target.as_dict(), "switched_at": _iso(now), "replaced": True})
    retiring = [item for item in retiring if _generation_or_none(item) not in (target, active)]
    if target == active:
        return replace(
            state,
            target=None,
            status="idle",
            families={},
            retiring=retiring,
            last_error=None,
            lease=None,
            requested_at=None,
            requested_by=None,
            fence=state.fence + 1 if state.target is not None else state.fence,
        )
    return replace(
        state,
        target=target.as_dict(),
        status="building",
        families={},
        retiring=retiring,
        last_error=None,
        lease=None,
        requested_at=_iso(now),
        requested_by=str(actor_id) if actor_id is not None else None,
        fence=state.fence + 1,
    )


def _generation_or_none(value: Any) -> Generation | None:
    try:
        return Generation.from_dict(value)
    except ValueError:
        return None


def stage_state(db: Any, row: Any, state: EmbeddingIndexState, *, actor_id: Any) -> None:
    """Add or update the state row; the caller commits."""
    from app.models.postgres import AppSetting

    updated_by = _as_uuid(actor_id)
    if row is None:
        db.add(AppSetting(key=EMBEDDING_STATE_KEY, value=state.as_value(), updated_by=updated_by,
                          is_secret_backed=False))
        return
    row.value = state.as_value()
    if updated_by is not None:
        row.updated_by = updated_by


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def failure_sentence(exc: BaseException) -> str:
    """``last_error`` for a failed build: the exception TYPE and a fixed phrase.

    Never ``str(exc)``: a ChromaDB or Ollama message can carry a host, a key or
    a document's text.
    """
    from app.services.embedding_function import EmbedderRefused

    name = type(exc).__name__
    if isinstance(exc, EmbedderRefused):
        phrase = "the embedder was refused by this deployment's AI policy"
    elif isinstance(exc, (ConnectionError, TimeoutError, OSError)) or "Connect" in name or "Timeout" in name:
        phrase = "the embedding service or the vector store did not answer"
    else:
        phrase = "the build stopped with an error"
    return f"{phrase} ({name})"[:LAST_ERROR_MAX]


def configured_generation(
    ai_config: Mapping[str, Any], storage_values: Mapping[str, Any]
) -> tuple[Generation, str | None]:
    """The CONFIGURED generation and its prefix note (``PREFIX_INVALID_NOTE`` or None)."""
    from app.core.config import settings

    spec = EmbedderSpec.from_settings(ai_config.get("embedding_provider"), ai_config.get("embedding_model"))
    stored = storage_values.get("chroma_collection", settings.CHROMA_COLLECTION)
    prefix, note = prefix_of(stored if isinstance(stored, str) else None)
    return Generation(prefix, spec), note


async def read_embedding_index(
    db: Any,
    *,
    ai_config: Mapping[str, Any],
    storage_values: Mapping[str, Any],
) -> dict | None:
    """An ``EmbeddingIndexRead`` body, or ``None`` when anything fails (never raises)."""
    try:
        state = await read_state(db)
        if state is None:
            return None
        active = state.active_generation()
        configured, prefix_note = configured_generation(ai_config, storage_values)
        note: str | None = None
        if configured.embedder.provider not in EMBEDDING_PROVIDERS:
            note = CONFIGURED_UNSUPPORTED_NOTE.format(
                provider=configured.embedder.provider[:40], active=active.label
            )
        elif prefix_note:
            note = prefix_note
        target = state.target_generation()
        families: list[dict] = []
        if target is not None:
            for fam in COLLECTION_FAMILIES:
                entry = state.families.get(fam.base)
                if isinstance(entry, Mapping):
                    families.append({
                        "family": fam.base,
                        "state": entry.get("state", "pending"),
                        "processed": _as_int(entry.get("processed")),
                        "total": entry.get("total"),
                    })
                else:
                    families.append({
                        "family": fam.base,
                        "state": "pending" if fam.rebuild in ("postgres", "copy") else "skipped",
                        "processed": 0,
                        "total": None,
                    })
        processed, total = state.progress()
        return {
            "active": active.read_model(),
            "configured": configured.read_model(),
            "in_sync": configured.embedder.key == active.embedder.key and configured.prefix == active.prefix,
            "status": state.status,
            "target": target.read_model() if target is not None else None,
            "families": families,
            "processed": processed,
            "total": total,
            "requested_at": state.requested_at,
            "switched_at": state.switched_at,
            "last_error": state.last_error,
            "configured_note": note,
            # Optional on the wire: a building state with no worker (Re-embed now resumes it).
            "stalled": build_is_stalled(state, now=datetime.now(timezone.utc)),
        }
    except Exception:  # noqa: BLE001 - GET /settings/ai answers embedding_index: null
        return None
