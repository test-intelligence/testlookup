"""The release filter inside a saved view (S5-3f-ii).

No migration: ``SavedView.filters`` is already a JSON document, so the release
rides inside it. What needs care is not storage, it is the two ways a stored
release id goes wrong when somebody else opens the view.

A view outlives the state it was saved in
------------------------------------------
Releases belong to exactly one project, so a saved view carrying release R is
only meaningful in R's project. A view saved against project A and applied while
project B is active would filter every page by an id nothing matches, and the
page would render empty while the view's name sat in the picker — the same
"product looks broken" failure `releaseStore` was built to prevent, arriving by
a different route.

So a stored release is applied only when the view's project matches the active
one, and the release is dropped rather than the view being refused. Losing one
filter is recoverable; refusing to open a saved view because one of its fields
went stale is not.

A SHARED view is read by people who did not save it
----------------------------------------------------
``is_shared`` makes a view visible to every member of the project. The release
id inside it is therefore read by people who never chose it, so it must be
validated against the READER's access rather than trusted because it was stored.
That is the same reasoning as ``resolve_release_query_scope``: the row grants
nothing, but a stored id is still an id somebody else supplied.

Deleted releases
----------------
A release can be deleted after a view is saved. The stored id then names
nothing, and the honest response is to drop the filter and say so, not to filter
by a dangling id and show an empty page.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.release_filter import is_unattributed
from app.models.postgres import Release

#: The key the release lives under inside ``SavedView.filters``.
#:
#: One spelling, defined once. A literal repeated across the writer and the
#: reader is how a saved view silently stops carrying its release — the same
#: producer/consumer drift the Unattributed sentinel is centralised to avoid.
RELEASE_KEY = "release_id"


def extract_release(filters: Optional[dict]) -> Optional[str]:
    """The release a view was saved with, if any."""
    if not filters:
        return None
    value = filters.get(RELEASE_KEY)
    return str(value) if value else None


def store_release(filters: Optional[dict], release_id: Optional[str]) -> dict:
    """Put a release into a view's filters, or take it out.

    ``None`` REMOVES the key rather than storing a null. A key present with a
    null value and a key absent read identically to most consumers but not to
    all of them, and a view that stores ``{"release_id": null}`` invites a
    reader to treat null as a filter value.
    """
    result = dict(filters or {})
    if release_id:
        result[RELEASE_KEY] = str(release_id)
    else:
        result.pop(RELEASE_KEY, None)
    return result


async def resolve_for_reader(
    db: AsyncSession,
    filters: Optional[dict],
    *,
    view_project_id: uuid.UUID | str | None,
    active_project_id: uuid.UUID | str | None,
    accessible_project_ids: Optional[set] = None,
) -> dict[str, Any]:
    """Decide whether this reader may apply the view's release, and say why not.

    Returns ``{"release_id": ..., "applied": bool, "reason": str | None}``.

    Never raises and never refuses the view. A saved view whose release has gone
    stale is still a useful view; dropping one filter and reporting it is the
    proportionate response, and it is what lets the UI tell the reader their
    result set is wider than the view's author intended.
    """
    stored = extract_release(filters)
    if stored is None:
        return {"release_id": None, "applied": False, "reason": None}

    # The Unattributed bucket names no release, so there is nothing to look up
    # or authorize — but it IS project-scoped in effect, because the runs it
    # returns are. Treat it like any other stored value for the project check.
    if is_unattributed(stored):
        if not _same_project(view_project_id, active_project_id):
            return {
                "release_id": None,
                "applied": False,
                "reason": "saved for a different project",
            }
        return {"release_id": stored, "applied": True, "reason": None}

    if not _same_project(view_project_id, active_project_id):
        # Applying it would filter by an id that matches nothing in the active
        # project, and the page would render empty with the view's name showing.
        return {
            "release_id": None,
            "applied": False,
            "reason": "saved for a different project",
        }

    try:
        release_uuid = uuid.UUID(stored)
    except (ValueError, TypeError):
        return {"release_id": None, "applied": False, "reason": "not a valid release id"}

    release = (
        await db.execute(select(Release).where(Release.id == release_uuid))
    ).scalar_one_or_none()
    if release is None:
        # Deleted since the view was saved. Filtering by a dangling id shows an
        # empty page and blames the data.
        return {"release_id": None, "applied": False, "reason": "release no longer exists"}

    if accessible_project_ids is not None and release.project_id not in accessible_project_ids:
        # A SHARED view is read by people who did not save it, so the stored id
        # is an id somebody else supplied. Validated against the reader rather
        # than trusted because it was stored.
        return {
            "release_id": None,
            "applied": False,
            "reason": "you do not have access to that release",
        }

    return {"release_id": str(release.id), "applied": True, "reason": None}


def _same_project(view_project_id, active_project_id) -> bool:
    """Whether a view's project matches the active one.

    A view with NO project is global, and a global view carrying a release is
    incoherent — the release belongs to exactly one project while the view
    claims to apply everywhere. Treated as a mismatch so the release is dropped
    and the rest of the view still opens.
    """
    if view_project_id is None or active_project_id is None:
        return False
    return str(view_project_id) == str(active_project_id)
