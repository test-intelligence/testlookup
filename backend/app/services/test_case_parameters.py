"""Authored test-case parameters at the Test Management boundary (W28 TCM-01).

A parameter row is ``{"name", "value", "mode", "masked"}``. A row marked secret
(``masked: true``, or ``mode`` ``masked`` / ``hidden``) never leaves the server
with its value: the canonical test detail already applied that rule
(``runs_service._safe_authored_parameters``), while ``/test-management/cases``
and the case history returned the stored value raw. Both now go through
:func:`mask_parameters`.

The edit form cannot prefill a value it never received, so a PATCH that sends
a masked row back with ``value: null`` means "unchanged": :func:`merge_masked_parameters`
keeps the stored secret. Without it, every save of an edited case would erase
the secrets it did not show.

Pure functions: no I/O, no logging (a value must never reach a log line).
"""
from __future__ import annotations

from typing import Any

_MASKED_MODES = frozenset({"masked", "hidden"})


def _is_masked(row: dict) -> bool:
    mode = str(row.get("mode") or "").lower()
    return bool(row.get("masked")) or mode in _MASKED_MODES


def mask_parameters(value: Any) -> list[dict] | None:
    """Copy authored parameters with every masked row's value removed.

    ``None`` (and any non-list) reads ``None``; a list yields a copy of each
    dict item (non-dict items are dropped); a masked row gets ``value: None``
    and ``masked: True``. Names stay, so the reader sees that a secret exists.
    """
    if not isinstance(value, list):
        return None
    result: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if _is_masked(row):
            row["value"] = None
            row["masked"] = True
        result.append(row)
    return result


def merge_masked_parameters(stored: Any, incoming: list[dict]) -> list[dict]:
    """The parameters to store for an edit that sent ``incoming``.

    An incoming masked row whose value is ``None`` and whose name matches a
    stored MASKED row keeps the stored value (the form never had it). Every
    other incoming row is taken as sent: a typed value replaces the stored
    one, and a stored row the request left out is removed. A value is never
    copied onto an unmasked row, in either direction.
    """
    secrets: dict[str, Any] = {}
    if isinstance(stored, list):
        for item in stored:
            if not isinstance(item, dict) or not _is_masked(item):
                continue
            name = item.get("name")
            if isinstance(name, str) and name not in secrets:
                secrets[name] = item.get("value")
    merged: list[dict] = []
    for item in incoming:
        row = dict(item)
        name = row.get("name")
        if (
            _is_masked(row)
            and row.get("value") is None
            and isinstance(name, str)
            and name in secrets
        ):
            row["value"] = secrets[name]
        merged.append(row)
    return merged
