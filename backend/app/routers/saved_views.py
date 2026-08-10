"""Saved Views router — CRUD for persisted filter/scope configurations (ENT-05)."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import SavedView, User
from app.models.schemas import SavedViewCreate, SavedViewResponse, SavedViewUpdate

logger = logging.getLogger("routers.saved_views")

router = APIRouter(prefix="/api/v1/saved-views", tags=["Saved Views"])


@router.get("", response_model=list[SavedViewResponse])
async def list_saved_views(
    project_id: uuid.UUID | None = None,
    page: str | None = None,
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
    if scoped_project_id is None and allowed is not None:
        return []
    query = select(SavedView).where(
        (SavedView.user_id == current_user.id) | (SavedView.is_shared == True)  # noqa: E712
    )
    if project_id:
        query = query.where(
            (SavedView.project_id == project_id) | (SavedView.project_id.is_(None))
        )
    # The MCP tool ``list_saved_views`` advertises "page: Optional — restrict to
    # a specific dashboard page" and sent it as a query param. FastAPI ignores
    # undeclared query params, so the filter was silently dropped and the tool
    # returned every view while its own docstring promised scoping — the same
    # silent-wrong-answer that hid behind the digests 404 (F-033/#534). Verified
    # live before the fix: ?page=trends and ?page=zzz-no-such-page both returned
    # all rows. Declaring it here makes the promise real rather than removing it.
    if page:
        query = query.where(SavedView.page == page)
    query = query.order_by(SavedView.is_default.desc(), SavedView.name)
    result = await db.execute(query)
    return result.scalars().all()


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
        filters=payload.filters,
        is_shared=payload.is_shared,
        is_default=payload.is_default,
    )
    db.add(view)

    # If setting as default, unset other defaults for same scope
    if payload.is_default:
        existing = await db.execute(
            select(SavedView).where(
                SavedView.user_id == current_user.id,
                SavedView.project_id == payload.project_id,
                SavedView.is_default == True,  # noqa: E712
            )
        )
        for old_view in existing.scalars().all():
            old_view.is_default = False

    await db.commit()
    await db.refresh(view)
    return view


@router.get("/{view_id}", response_model=SavedViewResponse)
async def get_saved_view(
    view_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get a single saved view."""
    result = await db.execute(select(SavedView).where(SavedView.id == view_id))
    view = result.scalar_one_or_none()
    if not view:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")
    # Access check: owner or shared
    if view.user_id != current_user.id and not view.is_shared:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return view


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
    if view.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can edit")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(view, field, value)

    await db.commit()
    await db.refresh(view)
    return view


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
    if view.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can delete")
    await db.delete(view)
    await db.commit()
    return None
