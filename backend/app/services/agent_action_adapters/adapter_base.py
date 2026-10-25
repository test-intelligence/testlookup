"""The adapter protocol, the registry skeleton and the shared execute-time checks.

W15 AGT-03, contract §1.3. Commit-free: nothing here commits, opens a session or
reaches outside the caller's session.

* ``ActionAdapter`` — what each adapter implements (``preflight`` reads only and
  raises ``ActionRefused(code)``; ``execute`` stages / flushes or calls a
  committing target service, then ``commits_effect`` is True; ``rollback`` is an
  idempotent compare-and-set).
* ``register`` / ``get_adapter`` / ``registered_types`` — the registry.
  ``get_adapter`` imports ``agent_action_registry`` (which registers the four
  adapters) lazily; while that module does not exist it answers None, i.e. the
  executor's honest ``action_executor_not_configured``.
* ``load_approver`` — re-authorises the approver at execute / undo time exactly
  as the PATCH's dependencies authorised the click.
* ``act_refusal`` — the LIVE Act gate (mode + per-type tick), on the caller's
  session only.
"""
from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import _ROLE_ORDER, _normalize_user_role
from app.models.postgres import ProjectMember, User, UserRole
from app.services.agent_action_types import (
    ACTION_ERROR_SENTENCES,
    ACTION_TYPES,
    ActionTypeSpec,
    canonical_action_type,
)
from app.services.agent_config_resolver import resolve_for_project
from app.services.ai_config_resolver import get_effective_ai_config

REGISTRY_MODULE = "app.services.agent_action_adapters.agent_action_registry"
#: The agent whose LIVE config gates every effectful type (the only proposer of one).
ACT_GATE_AGENT_ID = "investigator"


class ActionRefused(Exception):
    """A refusal with a code of ``ACTION_ERROR_SENTENCES`` (or a PATCH-only code)."""

    def __init__(self, code: str, *, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code

    @property
    def message(self) -> str:
        return ACTION_ERROR_SENTENCES.get(self.code, self.code)


@dataclass(frozen=True)
class ActionOutcome:
    result_payload: dict[str, Any]                  # typed facts only (ids, statuses)
    rollback_payload: Optional[dict[str, Any]] = None
    result_link: Optional[dict[str, Any]] = None    # AgentActionResultLink fields


@runtime_checkable
class ActionAdapter(Protocol):
    action_type: str      # a key of ACTION_TYPES; side effect / payload / role come from the spec
    commits_effect: bool  # True when a target service commits on its own (quarantine)

    async def preflight(self, db: AsyncSession, *, action: Any, payload: Any, approver: User) -> None:
        ...

    async def execute(
        self, db: AsyncSession, *, action: Any, payload: Any, approver: User
    ) -> ActionOutcome:
        ...

    async def rollback(
        self, db: AsyncSession, *, action: Any, rollback_payload: dict[str, Any], actor: User
    ) -> ActionOutcome:
        ...


_ADAPTERS: dict[str, ActionAdapter] = {}
_registry_loaded = False


def register(adapter: ActionAdapter) -> None:
    """Register one adapter; ValueError for an unknown type, a duplicate or an external effect."""
    action_type = getattr(adapter, "action_type", None)
    if not isinstance(action_type, str) or action_type not in ACTION_TYPES:
        raise ValueError(f"unknown agent action type: {action_type!r}")
    spec = ACTION_TYPES[action_type]
    if spec.side_effect == "external":
        raise ValueError(f"{action_type}: external adapters are not allowed (D16)")
    if action_type in _ADAPTERS:
        raise ValueError(f"{action_type}: an adapter is already registered")
    _ADAPTERS[action_type] = adapter


def _load_registry() -> None:
    global _registry_loaded
    if _registry_loaded:
        return
    try:
        importlib.import_module(REGISTRY_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != REGISTRY_MODULE:
            raise
        return  # not landed yet: no adapter (checked again on the next call)
    _registry_loaded = True


def get_adapter(action_type: Any) -> Optional[ActionAdapter]:
    """The adapter of a stored ``action_type`` (aliases resolved); None = no executor."""
    name = canonical_action_type(action_type)
    if name is None:
        return None
    _load_registry()
    return _ADAPTERS.get(name)


def registered_types() -> tuple[str, ...]:
    _load_registry()
    return tuple(sorted(_ADAPTERS))


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _refuse_approver() -> ActionRefused:
    return ActionRefused("approver_not_authorized", status_code=403)


async def load_approver(
    db: AsyncSession,
    *,
    user_id: Any,
    project_id: Any,
    required_role: UserRole,
) -> User:
    """The approver, re-authorised NOW (demoted / removed / deactivated / synthetic → refused).

    Mirrors the PATCH's dependencies: an active, non-synthetic user whose
    instance role is at least ``required_role`` (``require_role``) and who is
    an instance ADMIN or a member of ``project_id`` (``require_project_access``).
    The user is LOADED BY ID — no request principal, no API-key binding.
    """
    user_uuid = _as_uuid(user_id)
    project_uuid = _as_uuid(project_id)
    if user_uuid is None or project_uuid is None:
        raise _refuse_approver()
    approver = (
        await db.execute(select(User).where(User.id == user_uuid))
    ).scalar_one_or_none()
    if approver is None or not bool(approver.is_active) or bool(getattr(approver, "is_synthetic", False)):
        raise _refuse_approver()
    try:
        role = _normalize_user_role(approver.role)
        rank = _ROLE_ORDER.index(role)
    except ValueError:
        raise _refuse_approver() from None
    if rank < _ROLE_ORDER.index(required_role):
        raise _refuse_approver()
    instance_admin = role == UserRole.ADMIN
    if instance_admin:
        return approver
    member = (
        await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.user_id == user_uuid,
                ProjectMember.project_id == project_uuid,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise _refuse_approver()
    return approver


async def act_refusal(
    db: AsyncSession,
    *,
    project_id: Any,
    spec: ActionTypeSpec,
    proposing_agent_id: Any,
) -> Optional[str]:
    """None when the type may run now; else the refusal code (the LIVE config).

    ``side_effect == "none"`` needs no Act. Otherwise: the proposer must be one
    of ``spec.proposers`` (``proposer_not_permitted``), the investigator's live
    mode must be ``act`` (``act_mode_required``) and the type must be ticked in
    ``extensions.investigator.act.allowed_action_types``
    (``action_type_not_allowed``). Reads through the CALLER's session only.
    """
    if spec.side_effect == "none":
        return None
    if not isinstance(proposing_agent_id, str) or proposing_agent_id not in spec.proposers:
        return "proposer_not_permitted"
    project_uuid = _as_uuid(project_id)
    if project_uuid is None:
        return "act_mode_required"
    resolved = await resolve_for_project(
        db,
        project_uuid,
        ACT_GATE_AGENT_ID,
        global_ai_config=await get_effective_ai_config(db=db),
    )
    if resolved.config.mode != "act":
        return "act_mode_required"
    extension = resolved.config.extensions.investigator
    allowed = extension.act.allowed_action_types if extension is not None else []
    if spec.action_type not in allowed:
        return "action_type_not_allowed"
    return None
