"""Resolve a release (and a project) given by NAME to its id — never by guessing (W18 DEV-05).

Release names are not unique: two projects can each have "Sprint 12", and one
project can have two (only the external identity is unique). So:

* a UUID, or ``unattributed``, passes through untouched (no request);
* otherwise the caller's releases are listed (one project's when ``--project``
  is given) and matched on the exact name, then the exact version,
  case-insensitively;
* exactly one match is the answer; none is an error naming the closest names;
  more than one is an error listing ``name (version, status, project) id`` for
  each, so the user can paste the id. Both errors exit 4 — the same code an
  unreadable release already gets from ``analytics`` ("this release cannot be
  used"): a CI step must stop, never pick one.

Projects resolve the same way (UUID passthrough, else exact name, case-insensitive)
so every ``--project`` accepts what the UI shows.
"""
from __future__ import annotations

import difflib
import uuid
from typing import Any, Optional

from testlookup_cli import client
from testlookup_cli.errors import EXIT_PERMISSION, CLIError

UNATTRIBUTED = "unattributed"
#: The exit code for "this release cannot be used": unreadable (403), unknown or ambiguous.
EXIT_RELEASE_UNUSABLE = EXIT_PERMISSION


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _items(body: Any) -> list[dict]:
    if isinstance(body, dict):
        body = body.get("items", [])
    return [r for r in body if isinstance(r, dict)] if isinstance(body, list) else []


def _describe(r: dict) -> str:
    version = r.get("version") or "no version"
    project = r.get("project_name") or r.get("project_id") or "?"
    return f"{r.get('name')} ({version}, {r.get('status') or 'no status'}, {project}) {r.get('id')}"


def match_release(name: str, releases: list[dict]) -> dict:
    """Pick the one release ``name`` means, or raise a ``CLIError`` (exit 4) that says why."""
    wanted = name.strip().casefold()
    by_name = [r for r in releases if str(r.get("name") or "").strip().casefold() == wanted]
    hits = by_name or [r for r in releases if str(r.get("version") or "").strip().casefold() == wanted]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        lines = "\n".join(f"  - {_describe(r)}" for r in hits)
        raise CLIError(
            f"{len(hits)} releases are called '{name}'. Pass one id (or --project to narrow):\n{lines}",
            EXIT_RELEASE_UNUSABLE,
        )
    names = sorted({str(r.get("name")) for r in releases if r.get("name")})
    close = difflib.get_close_matches(name, names, n=5, cutoff=0.4)
    hint = f" Closest: {', '.join(close)}." if close else (" No releases are visible to you." if not names else "")
    raise CLIError(f"No release named '{name}'.{hint}", EXIT_RELEASE_UNUSABLE)


async def resolve_project(value: Optional[str], *, profile_name: Optional[str] = None) -> Optional[str]:
    """A project id from an id or an exact (case-insensitive) project name."""
    if not value or is_uuid(value):
        return value
    projects = _items(await client.request("GET", "/api/v1/projects", profile_name=profile_name))
    wanted = value.strip().casefold()
    hits = [p for p in projects if str(p.get("name") or "").strip().casefold() == wanted]
    if len(hits) == 1:
        return str(hits[0]["id"])
    if hits:
        ids = ", ".join(str(p.get("id")) for p in hits)
        raise CLIError(f"{len(hits)} projects are called '{value}': {ids}. Pass the id.", EXIT_RELEASE_UNUSABLE)
    close = difflib.get_close_matches(value, [str(p.get("name")) for p in projects if p.get("name")], n=5, cutoff=0.4)
    hint = f" Closest: {', '.join(close)}." if close else ""
    raise CLIError(f"No project named '{value}'.{hint}", EXIT_RELEASE_UNUSABLE)


async def resolve_release(
    value: str, *, project_id: Optional[str] = None, profile_name: Optional[str] = None,
) -> str:
    """A release id (or ``unattributed``) from an id, ``unattributed``, a name or a version."""
    if is_uuid(value) or value.strip().casefold() == UNATTRIBUTED:
        return value.strip().casefold() if not is_uuid(value) else value
    params = {"project_id": project_id} if project_id else None
    body = await client.request("GET", "/api/v1/releases", params=params, profile_name=profile_name)
    return str(match_release(value, _items(body))["id"])


async def resolve_releases(
    values: Optional[list[str]], *, project_id: Optional[str] = None, profile_name: Optional[str] = None,
) -> Optional[list[str]]:
    """``resolve_release`` for a repeatable ``--release``; one list request however many names."""
    if not values:
        return values
    if all(is_uuid(v) or v.strip().casefold() == UNATTRIBUTED for v in values):
        return list(values)
    params = {"project_id": project_id} if project_id else None
    releases = _items(await client.request("GET", "/api/v1/releases", params=params, profile_name=profile_name))
    out = []
    for v in values:
        if is_uuid(v):
            out.append(v)
        elif v.strip().casefold() == UNATTRIBUTED:
            out.append(UNATTRIBUTED)
        else:
            out.append(str(match_release(v, releases)["id"]))
    return out
