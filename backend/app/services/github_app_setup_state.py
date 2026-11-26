"""Short-lived, single-use authorization transactions for GitHub App setup.

The browser-visible state is an opaque 256-bit value.  Redis stores only its
SHA-256 digest and the administrator-approved project, deployment App, API
base, and repository.  Callback redemption uses atomic GETDEL, so every
success or refusal burns the transaction and a retry must start a fresh setup.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import asdict, dataclass
from typing import Optional

from app.db.redis_client import get_redis

STATE_TTL_SECONDS = 600
_ISSUE_ATTEMPTS = 3
_KEY = "testlookup:github-app-setup:{digest}"
_STATE_RE = re.compile(r"[A-Za-z0-9_-]{43}")


class SetupStateUnavailable(Exception):
    """The setup transaction store is unavailable; callers must fail closed."""


@dataclass(frozen=True)
class SetupTransaction:
    project_id: uuid.UUID
    user_id: uuid.UUID
    app_id: str
    api_base_url: str
    repo_owner: str
    repo_name: str


def _key(state: str) -> str:
    digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    return _KEY.format(digest=digest)


async def issue_setup_transaction(transaction: SetupTransaction) -> str:
    """Park ``transaction`` for ten minutes and return its opaque state."""
    payload = json.dumps({key: str(value) for key, value in asdict(transaction).items()}, separators=(",", ":"))
    try:
        redis = get_redis()
        for _ in range(_ISSUE_ATTEMPTS):
            state = secrets.token_urlsafe(32)
            if await redis.set(_key(state), payload, ex=STATE_TTL_SECONDS, nx=True):
                return state
    except Exception as exc:  # noqa: BLE001 -- unavailable state must never fall back to client-only trust
        raise SetupStateUnavailable() from exc
    raise SetupStateUnavailable()


async def consume_setup_transaction(state: str) -> Optional[SetupTransaction]:
    """Atomically burn and return a live setup transaction, or ``None``.

    Redis errors are distinguished from an unknown, expired, replayed, or
    malformed state so the route can report temporary unavailability while
    still failing closed.
    """
    if not _STATE_RE.fullmatch(state):
        return None
    try:
        raw = await get_redis().getdel(_key(state))
    except Exception as exc:  # noqa: BLE001 -- no state store means no authorized setup
        raise SetupStateUnavailable() from exc
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return SetupTransaction(
            project_id=uuid.UUID(str(data["project_id"])),
            user_id=uuid.UUID(str(data["user_id"])),
            app_id=str(data["app_id"]),
            api_base_url=str(data["api_base_url"]),
            repo_owner=str(data["repo_owner"]),
            repo_name=str(data["repo_name"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
