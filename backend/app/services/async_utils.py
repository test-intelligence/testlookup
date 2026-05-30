"""Async interop helpers for services that support sync and async adapters."""
from __future__ import annotations

import inspect
from typing import Any


async def await_if_needed(value: Any) -> Any:
    """Await ``value`` when an adapter returns an awaitable, otherwise return it."""
    if inspect.isawaitable(value):
        return await value
    return value
