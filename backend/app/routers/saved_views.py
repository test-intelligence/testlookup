"""Saved Views router — CRUD for persisted filter/scope configurations (ENT-05)."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import SavedView, User
from app.models.schemas import (
    SAVED_VIEW_PAGES,
    SavedViewCreate,
    SavedViewRelease,
    SavedViewResponse,
    SavedViewUpdate,
)
from app.services import saved_view_release
from app.services.activity.service import ActorRef, record as record_activity

logger = logging.getLogger("routers.saved_views")

router = APIRouter(prefix="/api/v1/saved-views", tags=["Saved Views"])


async def _require_view_project_access(
    db: AsyncSession,
    current_user: User,
    view: SavedView,
) -> None:
    """Keep direct UUID reads and owner mutations inside current membership."""
    if view.project_id is None:
        if view.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )
        return
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    await resolve_project_scope(db, current_user, str(view.project_id))


def _reader_scope(scoped, allowed):
    """The set of projects this reader may resolve a stored release against.

    ``resolve_project_scope`` returns ``allowed=None`` for an ADMIN *and* for a
    non-admin pinned to one verified project — the two are distinguished by
    ``scoped``. Passing ``allowed`` straight through would therefore treat a
    pinned non-admin as unrestricted, and a view whose own project matches the
    active one could carry a release belonging to a THIRD project past the
    access check. Narrow to the verified project instead; only a true admin
    (nothing pinned, nothing restricted) gets None.
    """
    if scoped is not None:
        return {scoped}
    return allowed


async def _with_release(
    db: AsyncSession,
    view: SavedView,
    *,
    active_project_id,
    accessible_project_ids,
) -> SavedView:
    """Attach this reader's verdict on the view's stored release, and return
    the row.

    Sets a plain attribute rather than building a ``SavedViewResponse`` here.
    ``release`` is not a mapped column, so this never reaches the database, and
    ``from_attributes`` picks it up when FastAPI serialises against
    ``response_model`` — which keeps ONE validation pass, at the layer that
    already did it, and keeps these handlers returning what they always
    returned. Building the response model inside the handler instead changed
    the contract for every direct caller: two existing tests construct a mock
    row and never populate ``id`` or ``created_at``, because until now nothing
    validated it this early.

    Costs one SELECT per view that actually carries a release;
    ``resolve_for_reader`` returns before touching the database when it does
    not, so a workspace with no release-bearing views issues exactly the
    queries it did before this existed.
    """
    verdict = await saved_view_release.resolve_for_reader(
        db,
        view.filters,
        view_project_id=view.project_id,
        active_project_id=active_project_id,
        accessible_project_ids=accessible_project_ids,
    )
    view.release = SavedViewRelease(**verdict)
    return view


async def _unset_other_defaults(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID | None,
    page: str | None,
    keep_id: uuid.UUID | None,
) -> None:
    """VIZ-609: exactly one default per user, project and page.

    The create path used to unset every default of the user in the project,
    whatever the page (so a Trends default silently cleared the Coverage one),
    and PATCH did not unset anything (so two defaults could coexist). Both now
    call this, scoped by all three. ``None`` matches ``IS NULL``: a global view
    and a page-less view are their own scope.
    """
    query = select(SavedView).where(
        SavedView.user_id == user_id,
        SavedView.is_default == True,  # noqa: E712
        SavedView.project_id.is_(None) if project_id is None else SavedView.project_id == project_id,
        SavedView.page.is_(None) if page is None else SavedView.page == page,
    )
    if keep_id is not None:
        query = query.where(SavedView.id != keep_id)
    for other in (await db.execute(query)).scalars().all():
        other.is_default = False


async def _record_view_event(
    db: AsyncSession,
    view: SavedView,
    event_type: str,
    user: User,
    *,
    changed: list[str] | None = None,
) -> None:
    """VIZ-609: the four ``saved_view.*`` events the catalog declared and nothing
    emitted. The ledger is per project, so a global view (no project) has no
    feed to write to and records nothing."""
    if view.project_id is None:
        return
    await record_activity(
        db,
        project_id=view.project_id,
        event_type=event_type,
        actor=ActorRef.from_user(user),
        entity_id=view.id,
        entity_label=view.name,
        changed_fields=changed,
        context={"page": view.page or "", "changed": ", ".join(changed or []) or "nothing"},
    )


@router.get("", response_model=list[SavedViewResponse])
async def list_saved_views(
    project_id: uuid.UUID | None = None,
    page: SAVED_VIEW_PAGES | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List saved views: user's personal + shared views for the project."""
    # F-042: this check ran ONLY in the ``not project_id`` branch, so naming a
    # project skipped it entirely — the guard fired only where there was
    # nothing to guard. Third recurrence of the class (F-033 digests, F-040
    # chat). ``resolve_project_scope`` 403s a non-admin naming a project they
    # do not belong to; the empty return below preserves today's behaviour for
    # a non-admin who names no project at all.
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    scoped_project_id, allowed = await resolve_project_scope(
        db, current_user, str(project_id) if project_id else None
    )
    owned = SavedView.user_id == current_user.id
    shared_in_project = (SavedView.is_shared == True) & (  # noqa: E712
        SavedView.project_id.is_not(None)
    )
    query = select(SavedView).where(
        owned | shared_in_project
    )
    if project_id:
        query = query.where(
            (SavedView.project_id == project_id)
            | (owned & SavedView.project_id.is_(None))
        )
    elif allowed is not None:
        global_owned = owned & SavedView.project_id.is_(None)
        query = query.where(
            global_owned
            | (SavedView.project_id.in_(allowed) if allowed else global_owned)
        )
    # The MCP tool ``list_saved_views`` advertises "page: Optional — restrict to
    # a specific dashboard page" and sent it as a query param. FastAPI ignores
    # undeclared query params, so the filter was silently dropped and the tool
    # returned every view while its own docstring promised scoping — the same
    # silent-wrong-answer that hid behind the digests 404 (F-033/#534). Verified
    # live before the fix: ?page=trends and ?page=zzz-no-such-page both returned
    # all rows. Declaring it here makes the promise real rather than removing it.
    if page:
        # Rows created before migration 0049 stored the page only inside the
        # JSON filters object. Keep those layouts discoverable while new rows
        # use the indexed column.
        query = query.where(
            or_(
                SavedView.page == page,
                and_(
                    SavedView.page.is_(None),
                    SavedView.filters["page"].as_string() == page,
                ),
            )
        )
    query = query.order_by(SavedView.is_default.desc(), SavedView.name)
    result = await db.execute(query)
    views = result.scalars().all()
    # ``project_id`` IS the active project for this request, which is what the
    # release verdict is relative to: a view saved against another project
    # keeps its name and loses only its release.
    accessible = _reader_scope(scoped_project_id, allowed)
    return [
        await _with_release(
            db, v, active_project_id=project_id, accessible_project_ids=accessible
        )
        for v in views
    ]


@router.post("", response_model=SavedViewResponse, status_code=201)
async def create_saved_view(
    payload: SavedViewCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new saved view."""
    # F-046 (low): the GET on this router verifies project access; this POST
    # did not, so a non-member could create a view bound to another tenant's
    # project and then get a 403 reading it back. No disclosure — SavedView's
    # project_id is only used to filter the list and to unset sibling defaults,
    # so the row grants nothing. It is a write-side asymmetry introduced when
    # the read path alone was guarded, and it is fixed here so the pair agrees.
    if payload.project_id:
        from app.core.deps import resolve_project_scope  # noqa: PLC0415

        await resolve_project_scope(db, current_user, str(payload.project_id))

    view = SavedView(
        user_id=current_user.id,
        project_id=payload.project_id,
        name=payload.name,
        description=payload.description,
        # ``page`` was accepted by SavedViewCreate, returned by
        # SavedViewResponse, stored by the column, and settable through PATCH —
        # but this hand-written field list omitted it, so every create silently
        # discarded it. The caller got a 201 and a response whose ``page`` was
        # null, having just supplied one. PATCH persisted it correctly because
        # it setattr's over the payload rather than naming fields.
        page=payload.page,
        # One spelling for the release, decided here rather than by whichever
        # client wrote the view. ``store_release`` also REMOVES the key for a
        # falsy value instead of persisting ``{"release_id": null}``, which
        # reads identically to an absent key for most consumers but not all,
        # and invites a reader to treat null as a filter value.
        filters=saved_view_release.store_release(
            payload.filters, saved_view_release.extract_release(payload.filters)
        ),
        is_shared=payload.is_shared,
        is_default=payload.is_default,
    )
    # If setting as default, unset the other defaults for the same user,
    # project AND page (VIZ-609), before the new row joins the session.
    if payload.is_default:
        await _unset_other_defaults(
            db, user_id=current_user.id, project_id=payload.project_id,
            page=payload.page, keep_id=None,
        )
    db.add(view)
    await db.flush()
    await _record_view_event(db, view, "saved_view.created", current_user)
    if view.is_shared:
        await _record_view_event(db, view, "saved_view.shared", current_user)

    await db.commit()
    await db.refresh(view)
    # Resolved against the view's OWN project: the creator just chose it, so
    # that is the active project by construction. The release is still checked
    # against that project rather than waved through — a release belongs to
    # exactly one project, and nothing stops a client from posting filters
    # naming a release from a different one. (A view with no project is global;
    # `_same_project` then drops the release on its own.)
    return await _with_release(
        db,
        view,
        active_project_id=view.project_id,
        accessible_project_ids={view.project_id} if view.project_id else set(),
    )


@router.get("/{view_id}", response_model=SavedViewResponse)
async def get_saved_view(
    view_id: uuid.UUID,
    # Optional, and the release verdict is what needs it: "may I apply this
    # view's release" is only answerable relative to the project the reader is
    # currently looking at. Omitted, the release is reported as not applied
    # rather than guessed at.
    active_project_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get a single saved view."""
    result = await db.execute(select(SavedView).where(SavedView.id == view_id))
    view = result.scalar_one_or_none()
    if not view:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")

    # A shared row is visible only inside its project. ``is_shared`` is not a
    # workspace-wide capability: without this check, anybody who learned a
    # shared view UUID could read its name and stored filters across tenants.
    await _require_view_project_access(db, current_user, view)

    # Access check: owner or shared
    if view.user_id != current_user.id and not view.is_shared:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # The active project is a caller-supplied id like any other, so it is
    # access-checked before it is used — otherwise passing someone else's
    # project id would be a way to ask whether a release exists in it.
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    scoped, allowed = await resolve_project_scope(
        db, current_user, str(active_project_id) if active_project_id else None
    )
    return await _with_release(
        db,
        view,
        active_project_id=active_project_id,
        accessible_project_ids=_reader_scope(scoped, allowed),
    )


@router.patch("/{view_id}", response_model=SavedViewResponse)
async def update_saved_view(
    view_id: uuid.UUID,
    payload: SavedViewUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update a saved view (owner only)."""
    result = await db.execute(select(SavedView).where(SavedView.id == view_id))
    view = result.scalar_one_or_none()
    if not view:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")
    await _require_view_project_access(db, current_user, view)
    if view.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can edit")

    updates = payload.model_dump(exclude_unset=True)
    if "filters" in updates:
        # VIZ-609: a SHALLOW MERGE over the stored object, so neither writer
        # loses the other's keys. ``useAnalyticsView`` sends {page, instances,
        # version} and the digests' ``withRelease`` sends release_id; replacing
        # the object made each save drop the other's half. A key is removed by
        # sending it as null: ``store_release`` drops a falsy release_id, and
        # any other null is removed below.
        merged = {**(view.filters or {}), **updates["filters"]}
        merged = {key: value for key, value in merged.items() if value is not None or key == "release_id"}
        # Same canonicalisation as create. Without it the two paths store the
        # release differently and a view edited once stops carrying it — the
        # producer/consumer drift RELEASE_KEY exists to prevent.
        updates["filters"] = saved_view_release.store_release(
            merged, saved_view_release.extract_release(merged)
        )
    was_shared = bool(view.is_shared)
    changed = sorted(field for field, value in updates.items() if getattr(view, field) != value)
    for field, value in updates.items():
        setattr(view, field, value)
    if updates.get("is_default"):
        await _unset_other_defaults(
            db, user_id=view.user_id, project_id=view.project_id, page=view.page, keep_id=view.id,
        )
    if changed:
        await _record_view_event(db, view, "saved_view.updated", current_user, changed=changed)
    if view.is_shared and not was_shared:
        await _record_view_event(db, view, "saved_view.shared", current_user)

    await db.commit()
    await db.refresh(view)
    return await _with_release(
        db,
        view,
        active_project_id=view.project_id,
        accessible_project_ids={view.project_id} if view.project_id else set(),
    )


@router.delete("/{view_id}", status_code=204)
async def delete_saved_view(
    view_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Delete a saved view (owner only)."""
    result = await db.execute(select(SavedView).where(SavedView.id == view_id))
    view = result.scalar_one_or_none()
    if not view:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")
    await _require_view_project_access(db, current_user, view)
    if view.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can delete")
    # Recorded before the delete: the label is the name the reader knew it by.
    await _record_view_event(db, view, "saved_view.deleted", current_user)
    await db.delete(view)
    await db.commit()
    return None
