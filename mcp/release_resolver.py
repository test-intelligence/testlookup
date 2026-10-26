"""Release (and project) names -> ids for MCP tools, never by guessing (W18 DEV-05).

The CLI's ``testlookup_cli.release_resolver`` with the same rules: a UUID or
``unattributed`` passes through without a request; otherwise the caller's
releases are listed (one project's when given) and matched on the exact name,
then the exact version, case-insensitively. No match or several matches is an
``UnresolvedName`` whose text lists the closest names or every candidate with
its id, so the agent can ask the user or retry with an id.
"""
from __future__ import annotations

import difflib
import uuid
from typing import Any, Optional

import client as api  # type: ignore[import]

UNATTRIBUTED = "unattributed"


class UnresolvedName(Exception):
    """A name that matched nothing, or more than one thing."""


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


def match_release(name: str, releases: list[dict]) -> dict:
    wanted = name.strip().casefold()
    hits = [r for r in releases if str(r.get("name") or "").strip().casefold() == wanted]
    hits = hits or [r for r in releases if str(r.get("version") or "").strip().casefold() == wanted]
    if len(hits) == 1:
        return hits[0]
    if hits:
        choices = "; ".join(
            f"{r.get('name')} ({r.get('version') or 'no version'}, {r.get('status') or 'no status'}, "
            f"{r.get('project_name') or r.get('project_id')}) id={r.get('id')}"
            for r in hits
        )
        raise UnresolvedName(f"{len(hits)} releases are called '{name}': {choices}. Pass one id, or a project_id.")
    names = sorted({str(r.get("name")) for r in releases if r.get("name")})
    close = difflib.get_close_matches(name, names, n=5, cutoff=0.4)
    raise UnresolvedName(f"No release named '{name}'." + (f" Closest: {', '.join(close)}." if close else ""))


async def resolve_project(value: Optional[str]) -> Optional[str]:
    if not value or is_uuid(value):
        return value
    projects = _items(await api.get("/api/v1/projects"))
    hits = [p for p in projects if str(p.get("name") or "").strip().casefold() == value.strip().casefold()]
    if len(hits) == 1:
        return str(hits[0]["id"])
    if hits:
        raise UnresolvedName(f"{len(hits)} projects are called '{value}': {', '.join(str(p['id']) for p in hits)}.")
    close = difflib.get_close_matches(value, [str(p.get("name")) for p in projects if p.get("name")], n=5, cutoff=0.4)
    raise UnresolvedName(f"No project named '{value}'." + (f" Closest: {', '.join(close)}." if close else ""))


async def resolve_releases(values: Optional[list[str]], project_id: Optional[str] = None) -> Optional[list[str]]:
    if not values:
        return values
    if all(is_uuid(v) or str(v).strip().casefold() == UNATTRIBUTED for v in values):
        return [v if is_uuid(v) else UNATTRIBUTED for v in values]
    releases = _items(await api.get("/api/v1/releases", params={"project_id": project_id} if project_id else None))
    return [
        v if is_uuid(v) else UNATTRIBUTED if str(v).strip().casefold() == UNATTRIBUTED else str(match_release(v, releases)["id"])
        for v in values
    ]


async def resolve_release(value: str, project_id: Optional[str] = None) -> str:
    return (await resolve_releases([value], project_id))[0]


def unresolved_payload(exc: UnresolvedName) -> dict:
    """The tools' error shape (``{ok: false, status_code, detail}``) for a name that did not resolve."""
    return {"ok": False, "status_code": 400, "detail": str(exc), "code": "unresolved_name"}
