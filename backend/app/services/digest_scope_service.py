"""What a digest subscription is about, judged in one place (W09 NTF-03 / NTF-04).

Three questions, three entry points:

* :func:`validate_subscription` -- at create / PATCH: may this subscription be
  stored, and with which values? A ``PER_RELEASE`` row names a release of ITS
  project; a ``PER_SUITE`` row names a suite; a saved view is one of the
  project's report views the owner may read. Every refusal is one of the fixed
  texts below, which the router answers as a 422. The digest SCHEMAS carry no
  validator on purpose (``tests/test_email_integration.py`` and
  ``tests/regression/test_digest_schedule_vocab.py`` build them without a
  scope, and a PATCH of only ``schedule`` cannot be judged without the stored
  row), so the router calls this on the MERGED state.
* :func:`resolve_digest_scope` -- at send and preview time: the filter the
  subscription's saved view applies, re-read for its owner (a view can be
  deleted, unshared or moved since it was bound). Never raises on a view that
  fails the rule: the digest covers the whole project and says why.
* :func:`decorate_subscriptions` -- the list's labels and "not delivering"
  problems, at most two statements for a whole page of rows.

Statements run only when there is something to look up: a subscription with no
release and no view costs nothing here, which is what lets the IDOR tests drive
create with ``MagicMock`` payloads and an ``AsyncMock`` session
(``tests/regression/test_digests_project_scope_idor.py``).

Services flush, routers commit: nothing in this module commits.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.release_filter import is_unattributed
from app.models.postgres import DigestSubscription, Release, SavedView
from app.models.viz_contracts import MAX_RELEASES, MAX_SUITES, MAX_WINDOW_DAYS
from app.services.analytics_scope import suite_keys

logger = structlog.get_logger(__name__)

#: Where a digest's scope came from (``DigestScopeInfo.source``).
SCOPE_SOURCES = ("project", "saved_view", "saved_view_deleted", "saved_view_unavailable")
#: Why a subscription cannot deliver what it says (``scope_problem``).
SCOPE_PROBLEMS = ("unscoped", "release_missing", "saved_view_deleted", "saved_view_unavailable")
#: The schedules whose CONTENT a saved view filters (``generate_digest``). The
#: event-driven schedules are scoped by their own schedule instead.
VIEW_SCHEDULES = ("DAILY", "WEEKLY")
#: ``filters.kind`` of a named report view (``components/reports/savedViewsModel.ts``).
REPORT_VIEW_KIND = "report_view"


class DigestScopeError(ValueError):
    """A subscription the API refuses to store; ``str(exc)`` is the 422 text."""


NEEDS_RELEASE = "A per-release digest needs a release: choose one."
BAD_RELEASE = "Choose a release of this project."
RELEASE_PROJECT = "A per-release digest belongs to one project: pick it in the top bar."
NEEDS_SUITE = "A per-suite digest needs a suite name."
VIEW_SCHEDULE = "A saved view filters Daily and Weekly digests only."
VIEW_PROJECT = "A saved view belongs to one project: pick it in the top bar."
BAD_VIEW = "Choose one of this project's saved report views."


@dataclass(frozen=True)
class DigestScope:
    """The filter a digest applies, after re-authorising the view for its owner."""

    source: str = "project"
    #: Releases of the subscription's project, canonical UUID strings, in the view's order.
    release_ids: tuple[str, ...] = ()
    release_names: tuple[str, ...] = ()
    #: ``analytics_scope.suite_keys`` of the view's suites: trimmed, lower-cased, de-duplicated.
    suites: tuple[str, ...] = ()
    #: The view's window; ``generate_digest`` applies it only when it is shorter than the period.
    window_days: Optional[int] = None
    view_id: Optional[str] = None
    view_name: Optional[str] = None
    #: Ids in the view that are not (or no longer) releases of this project.
    dropped_release_count: int = 0
    # D15 / GATE-08b leaves room here: a later story reads the release-level
    # verdict of release_ids[0] (or of a PER_RELEASE subscription's
    # scope_value) without a migration. W09 adds no verdict field and no read.

    @property
    def is_filtered(self) -> bool:
        return bool(self.release_ids or self.suites or self.window_days)


PROJECT_SCOPE = DigestScope()


# ── small pure helpers ──────────────────────────────────────────────────────


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    """A UUID from a UUID or its text; ``None`` for anything else, including
    the Unattributed sentinel (it names no release)."""
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or is_unattributed(text):
        return None
    try:
        return uuid.UUID(text)
    except ValueError:
        return None


def _text(value: Any) -> str:
    """A stripped string, or ``""`` for anything that is not one."""
    return value.strip() if isinstance(value, str) else ""


def view_is_readable(view: Any, *, project_id: Any, owner_id: Any) -> bool:
    """The ``BAD_VIEW`` rule over a loaded ``saved_views`` row.

    A report view (``filters.kind == "report_view"``) of THIS project that the
    owner saved or that is shared with the project. A widget-layout row, a
    legacy filter-only row, another project's view and a teammate's private
    view all fail it.
    """
    pid = _as_uuid(project_id)
    if view is None or pid is None or _as_uuid(getattr(view, "project_id", None)) != pid:
        return False
    filters = view.filters if isinstance(view.filters, dict) else {}
    if filters.get("kind") != REPORT_VIEW_KIND:
        return False
    owner = _as_uuid(owner_id)
    return (owner is not None and _as_uuid(view.user_id) == owner) or bool(view.is_shared)


def scope_info(scope: DigestScope) -> dict:
    """The ``DigestScopeInfo`` dict (lists, not tuples) a digest carries as ``scope``."""
    return {
        "source": scope.source,
        "view_id": scope.view_id,
        "view_name": scope.view_name,
        "release_ids": list(scope.release_ids),
        "release_names": list(scope.release_names),
        "suites": list(scope.suites),
        "window_days": scope.window_days,
        "dropped_release_count": scope.dropped_release_count,
    }


def slack_text(value: str) -> str:
    """Slack's own escaping rule, for text sent as Slack / Teams markup.

    ``&``, ``<`` and ``>`` become entities, so a view named ``<!channel>``
    reads as text instead of pinging a channel, and a ``<url|label>`` in a
    release name is not a link.
    """
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _load_view(db: AsyncSession, *, project_id: uuid.UUID, view_id: uuid.UUID) -> Optional[SavedView]:
    """One statement: the view, only when it belongs to ``project_id``."""
    return (
        await db.execute(
            select(SavedView).where(SavedView.id == view_id, SavedView.project_id == project_id)
        )
    ).scalar_one_or_none()


# ── create / PATCH ──────────────────────────────────────────────────────────


async def validate_subscription(
    db: AsyncSession,
    *,
    project_id: Any,
    schedule: str,
    scope_type: Optional[str],
    scope_value: Optional[str],
    saved_view_id: Any,
    owner_id: Any,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """The values to STORE: ``(scope_type, scope_value, saved_view_name)``.

    Raises :class:`DigestScopeError` with one of the fixed texts. ``project_id``
    is the subscription's project as the router already authorised it
    (``resolve_project_scope`` runs FIRST, on create and on PATCH alike).
    Unknown, foreign, malformed and Unattributed release ids share one text, and
    so do unknown, foreign, unreadable and non-report views: the answer never
    tells a caller whether an id exists elsewhere.
    """
    pid = _as_uuid(project_id)
    stored_type, stored_value = scope_type, scope_value

    if schedule == "PER_RELEASE":
        if pid is None:
            raise DigestScopeError(RELEASE_PROJECT)
        wanted = _text(scope_value)
        if not wanted:
            raise DigestScopeError(NEEDS_RELEASE)
        release_id = _as_uuid(wanted)
        if release_id is None:
            raise DigestScopeError(BAD_RELEASE)
        found = (
            await db.execute(
                select(Release.id).where(Release.id == release_id, Release.project_id == pid)
            )
        ).scalar_one_or_none()
        if found is None:
            raise DigestScopeError(BAD_RELEASE)
        stored_type, stored_value = "release", str(release_id)
    elif schedule == "PER_SUITE":
        wanted = _text(scope_value)
        if not wanted:
            raise DigestScopeError(NEEDS_SUITE)
        # No catalogue check: a suite that has not run yet is a legitimate subscription.
        stored_type, stored_value = "suite", wanted

    if saved_view_id is None:
        return stored_type, stored_value, None
    if schedule not in VIEW_SCHEDULES:
        raise DigestScopeError(VIEW_SCHEDULE)
    if pid is None:
        raise DigestScopeError(VIEW_PROJECT)
    view_uuid = _as_uuid(saved_view_id)
    if view_uuid is None:
        raise DigestScopeError(BAD_VIEW)
    view = await _load_view(db, project_id=pid, view_id=view_uuid)
    if view is None or not view_is_readable(view, project_id=pid, owner_id=owner_id):
        raise DigestScopeError(BAD_VIEW)
    return stored_type, stored_value, view.name


# ── send / preview ──────────────────────────────────────────────────────────


def _view_release_candidates(filters: dict) -> tuple[list[uuid.UUID], int]:
    """The view's release ids, parsed, de-duplicated, in order, at most
    ``MAX_RELEASES``; plus how many could not be used at all."""
    raw = filters.get("release_ids")
    if not isinstance(raw, list):
        single = filters.get("release_id")
        raw = [single] if single is not None else []
    wanted: list[uuid.UUID] = []
    dropped = 0
    for item in raw:
        rid = _as_uuid(item)
        if rid is not None and rid in wanted:
            continue
        if rid is None or len(wanted) >= MAX_RELEASES:
            dropped += 1
            continue
        wanted.append(rid)
    return wanted, dropped


def _view_suites(filters: dict) -> tuple[str, ...]:
    raw = filters.get("suites")
    if not isinstance(raw, list):
        return ()
    return suite_keys([item for item in raw if isinstance(item, str)])[:MAX_SUITES]


def _view_window(filters: dict) -> Optional[int]:
    window = filters.get("window")
    if isinstance(window, bool) or not isinstance(window, int):
        return None
    return window if 1 <= window <= MAX_WINDOW_DAYS else None


async def resolve_digest_scope(
    db: AsyncSession,
    *,
    project_id: Any,
    owner_id: Any,
    saved_view_id: Any,
    saved_view_name: Optional[str],
) -> DigestScope:
    """The scope a Daily / Weekly digest (or a preview) applies.

    No view and no remembered name: :data:`PROJECT_SCOPE`, with NO statement.
    A remembered name without an id: the view was deleted (the FK is
    ``ON DELETE SET NULL``). A view that fails the ``BAD_VIEW`` rule for its
    owner today: ``saved_view_unavailable``, named by the snapshot only (an
    owner who lost access does not learn the view's current name). Otherwise
    the view's releases are re-checked against THIS project in one ``IN``
    statement -- the rest are dropped and counted, never applied.
    """
    if saved_view_id is None:
        if saved_view_name:
            return DigestScope(source="saved_view_deleted", view_name=saved_view_name)
        return PROJECT_SCOPE

    unavailable = DigestScope(
        source="saved_view_unavailable",
        view_id=str(saved_view_id),
        view_name=saved_view_name or None,
    )
    pid = _as_uuid(project_id)
    view_uuid = _as_uuid(saved_view_id)
    if pid is None or view_uuid is None:
        return unavailable
    view = await _load_view(db, project_id=pid, view_id=view_uuid)
    if view is None or not view_is_readable(view, project_id=pid, owner_id=owner_id):
        return unavailable

    filters = view.filters if isinstance(view.filters, dict) else {}
    wanted, dropped = _view_release_candidates(filters)
    names: dict[uuid.UUID, str] = {}
    if wanted:
        rows = (
            await db.execute(
                select(Release.id, Release.name).where(
                    Release.project_id == pid, Release.id.in_(wanted)
                )
            )
        ).all()
        names = {_as_uuid(row[0]) or row[0]: str(row[1] or "") for row in rows}
    kept = [rid for rid in wanted if rid in names]
    return DigestScope(
        source="saved_view",
        release_ids=tuple(str(rid) for rid in kept),
        release_names=tuple(names[rid] for rid in kept),
        suites=_view_suites(filters),
        window_days=_view_window(filters),
        view_id=str(view.id),
        view_name=view.name,
        dropped_release_count=dropped + len(wanted) - len(kept),
    )


# ── the list's labels and problems ──────────────────────────────────────────


def _label_and_problem(
    sub: Any,
    releases: dict[uuid.UUID, tuple[Any, str]],
    views: dict[uuid.UUID, Any],
) -> tuple[Optional[str], Optional[str]]:
    schedule = getattr(sub, "schedule", None)
    label: Optional[str] = None
    problem: Optional[str] = None
    if schedule == "PER_RELEASE":
        wanted = _text(getattr(sub, "scope_value", None))
        if not wanted:
            problem = "unscoped"
        else:
            release_id = _as_uuid(wanted)
            hit = releases.get(release_id) if release_id is not None else None
            if hit is not None and _as_uuid(hit[0]) == _as_uuid(getattr(sub, "project_id", None)):
                label = hit[1]
            else:
                problem = "release_missing"
    elif schedule == "PER_SUITE":
        wanted = _text(getattr(sub, "scope_value", None))
        if wanted:
            label = wanted
        else:
            problem = "unscoped"
    if problem is not None:
        return label, problem

    view_id = _as_uuid(getattr(sub, "saved_view_id", None))
    if view_id is None:
        if getattr(sub, "saved_view_name", None):
            problem = "saved_view_deleted"
    elif not view_is_readable(
        views.get(view_id),
        project_id=getattr(sub, "project_id", None),
        owner_id=getattr(sub, "user_id", None),
    ):
        problem = "saved_view_unavailable"
    return label, problem


def _set_labels(sub: Any, label: Optional[str], problem: Optional[str]) -> None:
    """Plain attributes on the row (not mapped columns: nothing is written)."""
    setattr(sub, "scope_label", label)
    setattr(sub, "scope_problem", problem)


async def decorate_subscriptions(
    db: AsyncSession, subs: Sequence[DigestSubscription]
) -> Sequence[DigestSubscription]:
    """Set ``scope_label`` / ``scope_problem`` on each row and return the rows.

    Plain attributes, not columns (the ``saved_views._with_release``
    precedent): nothing is written, and ``from_attributes`` serialises them.
    At most TWO statements for the whole list, run in a SAVEPOINT so a failure
    cannot abort the request's transaction; ZERO when no row is
    ``PER_RELEASE`` with a release id and none has a view. A failure leaves
    both attributes ``None`` on every row and logs one warning.
    """
    rows = list(subs)
    for sub in rows:
        _set_labels(sub, None, None)
    try:
        release_ids = {
            rid
            for sub in rows
            if getattr(sub, "schedule", None) == "PER_RELEASE"
            and (rid := _as_uuid(getattr(sub, "scope_value", None))) is not None
        }
        view_ids = {
            vid for sub in rows if (vid := _as_uuid(getattr(sub, "saved_view_id", None))) is not None
        }
        releases: dict[uuid.UUID, tuple[Any, str]] = {}
        views: dict[uuid.UUID, Any] = {}
        if release_ids or view_ids:
            async with db.begin_nested():
                if release_ids:
                    found = (
                        await db.execute(
                            select(Release.id, Release.project_id, Release.name).where(
                                Release.id.in_(release_ids)
                            )
                        )
                    ).all()
                    releases = {
                        _as_uuid(row[0]) or row[0]: (row[1], str(row[2] or "")) for row in found
                    }
                if view_ids:
                    loaded = (
                        await db.execute(select(SavedView).where(SavedView.id.in_(view_ids)))
                    ).scalars().all()
                    views = {_as_uuid(view.id) or view.id: view for view in loaded}
        labelled = [_label_and_problem(sub, releases, views) for sub in rows]
    except Exception as exc:  # noqa: BLE001 -- a label must never fail the list
        logger.warning("digest_scope_labels_failed", error_type=type(exc).__name__)
        return subs
    for sub, (label, problem) in zip(rows, labelled):
        _set_labels(sub, label, problem)
    return subs
