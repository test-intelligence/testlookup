"""Tools: list_project_activity, get_activity_event — the project activity ledger, read-only (W18 DEV-04).

They read the routes the project's Activity tab reads, so an agent asked "what
changed in Payment Service this week?" answers with the same events in the same
order. A page that has more says so ("N more — pass cursor=…"): no silent cap.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import client as api  # type: ignore[import]
import release_resolver  # type: ignore[import]

_SINCE = re.compile(r"^(\d+)([hdw])$")


def since_to_iso(value: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    if not value:
        return None
    m = _SINCE.match(value.strip())
    if not m:
        return value
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    return ((now or datetime.now(timezone.utc)) - delta).isoformat()


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_project_activity(
        project_id: str,
        category: Optional[list[str]] = None,
        since: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> dict:
        """
        A project's activity ledger, newest first: who changed what and when
        (runs, releases and their gate verdicts, defects, quarantine, settings,
        members, ...). The same events, in the same order, as the project's
        Activity tab.

        Args:
            project_id: Project id or exact project name.
            category: Optional categories to keep (e.g. ["release", "defect"]).
            since: "7d", "24h", "2w" or an ISO date/time.
            limit: Events per page (1-200, default 50).
            cursor: The `next_cursor` of a previous answer, to read the next page.

        Returns {ok, items, next_cursor, more}. When `more` is true there are
        older events: call again with cursor=next_cursor.
        """
        try:
            pid = await release_resolver.resolve_project(project_id)
        except release_resolver.UnresolvedName as exc:
            return release_resolver.unresolved_payload(exc)
        except Exception as exc:
            return api.error_payload(exc)
        params = {"limit": max(1, min(int(limit), 200)), "category": category,
                  "since": since_to_iso(since), "cursor": cursor}
        try:
            body = await api.get(f"/api/v1/projects/{pid}/activity", params=params) or {}
        except Exception as exc:
            return api.error_payload(exc)
        items = [
            {k: e.get(k) for k in ("id", "occurred_at", "event_type", "category", "summary", "actor", "entity", "release_id", "has_diff")}
            for e in body.get("items", []) if isinstance(e, dict)
        ]
        nxt = body.get("next_cursor")
        out = {"ok": True, "project_id": pid, "items": items, "next_cursor": nxt, "more": bool(nxt)}
        if nxt:
            out["note"] = f"{len(items)} shown; more — pass cursor={nxt}"
        return out

    @mcp.tool()
    async def get_activity_event(project_id: str, event_id: str) -> dict:
        """
        One activity event with its before / after diff (changed fields; secret
        values are never stored).

        Args:
            project_id: Project id or exact project name.
            event_id: The event id (from list_project_activity).
        """
        try:
            pid = await release_resolver.resolve_project(project_id)
        except release_resolver.UnresolvedName as exc:
            return release_resolver.unresolved_payload(exc)
        except Exception as exc:
            return api.error_payload(exc)
        try:
            event = await api.get(f"/api/v1/projects/{pid}/activity/{event_id}")
        except Exception as exc:
            return api.error_payload(exc)
        return {"ok": True, **(event or {})}
