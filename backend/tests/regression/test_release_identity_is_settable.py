"""Regression: the S1 identity columns have a writer, and a baseline exists.

The finding
-----------
Migration 0151 added five columns to ``releases`` — ``release_type``,
``target_environment``, ``cutoff_start_at``, ``cutoff_end_at``,
``baseline_release_id`` — and **nothing could set any of them**. A grep across
every router and schema returned zero mentions. Two of those are load-bearing:

* The cutoff window is the attribution ladder's **rung 4** input. With no way
  to set it, that rung could never fire on any deployment, so the shipped
  ladder ran rung 1 → rung 5.
* ``baseline_release_id`` is what release-over-release comparison needs, so
  ``compare_to_baseline`` answered ``comparable: false, "this release has no
  baseline"`` for every release ever created — FR7 was unreachable.

The second is worse than a gap, because the model has documented the column as
*"Defaults to the previous sort_key but is overridable"* since 0151. A stated
default that no code applies is worse than no default: the docstring is what
the next reader trusts, and nothing contradicted it.

What these tests pin
--------------------
That the columns are settable, that the default is DERIVED rather than merely
documented, and that both caller-supplied values which look harmless — a
baseline from another project, an inverted window — are refused rather than
stored.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from fastapi import HTTPException  # noqa: E402

from app.routers.releases import ReleaseIn, ReleaseUpdate  # noqa: E402
from app.services import release_service  # noqa: E402

PROJECT = uuid.uuid4()
NOW = datetime.now(timezone.utc)


class _Rel:
    def __init__(self, rid=None, project_id=PROJECT, sort_key="00002.00000.00000.00000"):
        self.id = rid or uuid.uuid4()
        self.project_id = project_id
        self.sort_key = sort_key
        # The create path also asks whether the currently-active release may be
        # displaced, and this fake answers every query with the same row — so
        # it has to model that column too. Left True so activation behaves as
        # it does for an auto-named placeholder, which is not what these tests
        # are about.
        self.is_auto_named = True
        # `update_release` recomputes sort_key unconditionally and consults the
        # status, so a fake standing in for a real row has to carry them.
        self.version = "2.4.0"
        self.name = "2.4.0"
        self.status = "planning"


class _Session:
    """Returns a staged row, and RECORDS the statement that asked for it.

    Recording matters: a fake that answers every query identically cannot see
    which query was issued, so dropping the project scope or reversing the
    predecessor ordering is invisible to it. Mutation testing found exactly
    that — three query-shape mutations survived against the first version of
    this fake, which returned the staged row no matter what was asked.
    """

    def __init__(self, found=None):
        self.found = found
        self.queries = 0
        self.statements: list[str] = []

    async def execute(self, stmt=None, *a, **kw):
        self.queries += 1
        self.statements.append(str(stmt))
        found = self.found

        class _R:
            def scalar_one_or_none(self_inner):
                return found

        return _R()

    @property
    def sql(self) -> str:
        return " ".join(" ".join(self.statements).split())

    @property
    def where(self) -> str:
        """Only the predicate half of every statement.

        ``select(Release)`` names every column in its SELECT list, so
        ``"project_id" in sql`` is true even when the WHERE clause has no
        project scope at all — mutation testing survived twice on exactly that.
        The question is what is FILTERED on, not what is returned.
        """
        return " ".join(part.split("ORDER BY")[0] for part in self.sql.split("WHERE")[1:])


# ── The columns are settable ─────────────────────────────────────────────────


def test_every_s1_column_is_accepted_on_create():
    """The exact five that had no writer."""
    body = ReleaseIn(
        project_id=str(PROJECT),
        name="2.4.0",
        release_type="hotfix",
        target_environment="staging",
        cutoff_start_at=NOW,
        cutoff_end_at=NOW + timedelta(days=3),
        baseline_release_id=str(uuid.uuid4()),
    )
    assert body.release_type == "hotfix"
    assert body.target_environment == "staging"
    assert body.cutoff_start_at and body.cutoff_end_at
    assert body.baseline_release_id


def test_every_s1_column_is_accepted_on_update():
    body = ReleaseUpdate(
        release_type="rc",
        target_environment="prod",
        cutoff_start_at=NOW,
        cutoff_end_at=NOW + timedelta(days=1),
        baseline_release_id=str(uuid.uuid4()),
    )
    dumped = body.model_dump(exclude_none=True)
    for field in (
        "release_type", "target_environment",
        "cutoff_start_at", "cutoff_end_at", "baseline_release_id",
    ):
        assert field in dumped, f"{field} is dropped on update"


def test_release_type_is_a_closed_set():
    """It drives gate-policy resolution, so a typo that stores cleanly would
    silently fall back to the project default while looking configured."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ReleaseIn(project_id=str(PROJECT), name="x", release_type="Hotfix")


# ── The baseline default is derived, not just documented ─────────────────────


@pytest.mark.asyncio
async def test_the_baseline_defaults_to_the_previous_release():
    previous = _Rel(sort_key="00001.00009.00000.00000")
    db = _Session(found=previous)

    got = await release_service.resolve_baseline(
        db, PROJECT, None, sort_key="00002.00000.00000.00000"
    )
    assert got == previous.id, (
        "the model documents this default and nothing derived it, so every "
        "release compared against nothing"
    )
    # PREVIOUS, and previous WITHIN THIS PROJECT. Asserted on the statement
    # because the fake would otherwise hand back the staged row whatever was
    # asked, and both of those are query-shape properties.
    assert "project_id" in db.where, (
        "the predecessor search is not project-scoped — a release would take "
        "its baseline from another team's project"
    )
    assert "sort_key < " in db.where, "the search is not looking BACKWARDS"
    assert "DESC" in db.sql.upper(), (
        "without descending order the baseline is the OLDEST prior release, "
        "not the immediately preceding one"
    )


@pytest.mark.asyncio
async def test_the_first_release_of_a_project_has_no_baseline():
    """Not an error. Saying so is more useful than a zero delta implying a
    predecessor that does not exist."""
    db = _Session(found=None)
    assert await release_service.resolve_baseline(
        db, PROJECT, None, sort_key="00001.00000.00000.00000"
    ) is None


@pytest.mark.asyncio
async def test_a_release_with_no_sort_key_looks_backwards_at_nothing():
    """No position in the order means nothing to be "previous" to — and the
    lookup must not run at all rather than ordering against NULL."""
    db = _Session(found=_Rel())
    assert await release_service.resolve_baseline(
        db, PROJECT, None, sort_key=None
    ) is None
    assert db.queries == 0, "a release with no sort_key still queried for a predecessor"


@pytest.mark.asyncio
async def test_a_supplied_baseline_overrides_the_default():
    """A hotfix's baseline is its parent release, not whatever shipped most
    recently — which is why the column is overridable at all."""
    chosen = _Rel()
    db = _Session(found=chosen)
    got = await release_service.resolve_baseline(
        db, PROJECT, str(chosen.id), sort_key="00002.00000.00000.00000"
    )
    assert got == chosen.id
    assert "project_id" in db.where, (
        "a supplied baseline is looked up unscoped, so an id from another "
        "project would be accepted rather than refused"
    )


# ── Supplied values are validated, not trusted ───────────────────────────────


@pytest.mark.asyncio
async def test_a_baseline_from_another_project_is_refused():
    """Releases belong to exactly one project. A cross-project baseline would
    compare this release against a different team's numbers, and a comparison
    is the wrong place to discover that."""
    db = _Session(found=None)  # scoped query finds nothing
    with pytest.raises(HTTPException) as exc:
        await release_service.resolve_baseline(
            db, PROJECT, str(uuid.uuid4()), sort_key="00002.00000.00000.00000"
        )
    assert exc.value.status_code == 400
    assert "different project" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_release_cannot_be_its_own_baseline():
    """It would report a zero delta on every metric, which reads as "nothing
    changed" rather than as the nonsense it is."""
    me = _Rel()
    db = _Session(found=me)
    with pytest.raises(HTTPException) as exc:
        await release_service.resolve_baseline(
            db, PROJECT, str(me.id), sort_key="x", self_id=me.id
        )
    assert exc.value.status_code == 400
    assert "own baseline" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_malformed_baseline_is_a_400_not_a_500():
    db = _Session()
    with pytest.raises(HTTPException) as exc:
        await release_service.resolve_baseline(
            db, PROJECT, "not-a-uuid", sort_key="x"
        )
    assert exc.value.status_code == 400


# ── create_release actually persists them ────────────────────────────────────


@pytest.mark.asyncio
async def test_creating_a_release_persists_every_identity_column():
    """End to end, because the schema accepting a field proves nothing about
    the row carrying it.

    Mutation testing made the point: dropping ``cutoff_start_at`` from the
    constructed ``Release`` left every other test in this file green, since
    they all stop at the schema or at the resolver.
    """
    added = {}

    class _CreateSession(_Session):
        async def get(self, model, pk):
            return object()  # the project exists

        def add(self, obj):
            added["release"] = obj

        async def flush(self):
            return None

    predecessor = _Rel(sort_key="00002.00003.00000.00000")
    db = _CreateSession(found=predecessor)
    body = ReleaseIn(
        project_id=str(PROJECT),
        name="2.4.0",
        version="2.4.0",
        release_type="hotfix",
        target_environment="staging",
        cutoff_start_at=NOW,
        cutoff_end_at=NOW + timedelta(days=3),
    )
    await release_service.create_release(db, body)

    row = added["release"]
    assert row.release_type == "hotfix"
    assert row.target_environment == "staging"
    assert row.cutoff_start_at == NOW, (
        "the cutoff window never reached the row — rung 4 of the attribution "
        "ladder has no input again"
    )
    assert row.cutoff_end_at == NOW + timedelta(days=3)
    assert row.sort_key, "sort_key is what the baseline default looks backwards from"
    assert row.baseline_release_id == predecessor.id, (
        "create never resolved a baseline, so compare_to_baseline would keep "
        "answering 'this release has no baseline' for every new release"
    )


@pytest.mark.asyncio
async def test_updating_a_baseline_resolves_it_rather_than_storing_the_string():
    """``ReleaseUpdate.baseline_release_id`` is a STRING and the column is a
    UUID, and ``update_release`` persists by blanket ``setattr``.

    So without explicit handling the raw string lands in a UUID column, and the
    caller-supplied id is never scoped to the project either. Mutation testing
    found this: removing the resolution left every other test here green,
    because they all stop at ``resolve_baseline`` itself.
    """
    target = _Rel()
    chosen = _Rel()

    class _Queued(_Session):
        """Answers the release lookup, then the baseline lookup."""

        def __init__(self, rows):
            super().__init__()
            self._rows = list(rows)

        async def execute(self, stmt=None, *a, **kw):
            self.statements.append(str(stmt))
            row = self._rows.pop(0) if self._rows else None

            class _R:
                def scalar_one_or_none(self_inner):
                    return row

                def scalars(self_inner):
                    return self_inner

                def all(self_inner):
                    return []

            return _R()

    db = _Queued([target, chosen])
    await release_service.update_release(
        db, str(target.id), ReleaseUpdate(baseline_release_id=str(chosen.id))
    )

    assert target.baseline_release_id == chosen.id, (
        "the baseline was stored as a raw string rather than resolved to the "
        "row's UUID, and never scoped to this project"
    )
    assert isinstance(target.baseline_release_id, uuid.UUID)


@pytest.mark.asyncio
async def test_a_release_cannot_be_updated_to_be_its_own_baseline():
    """The resolver refuses it, and the update path has to ASK it to.

    Passing ``self_id=None`` from here would leave the guard intact and
    unreachable — the shape this whole audit keeps turning up.
    """
    target = _Rel()

    class _Queued(_Session):
        def __init__(self, rows):
            super().__init__()
            self._rows = list(rows)

        async def execute(self, stmt=None, *a, **kw):
            self.statements.append(str(stmt))
            row = self._rows.pop(0) if self._rows else None

            class _R:
                def scalar_one_or_none(self_inner):
                    return row

            return _R()

    db = _Queued([target, target])
    with pytest.raises(HTTPException) as exc:
        await release_service.update_release(
            db, str(target.id), ReleaseUpdate(baseline_release_id=str(target.id))
        )
    assert exc.value.status_code == 400
    assert "own baseline" in str(exc.value.detail)


# ── The cutoff window has to be a window ─────────────────────────────────────


def test_an_inverted_cutoff_window_is_refused():
    """Rung 4 selects the release whose window CONTAINS the run's execution
    time and tie-breaks on narrowness. An inverted window silently removes the
    release from that rung while still looking configured on the release page.
    """
    with pytest.raises(HTTPException) as exc:
        release_service._check_cutoff_window(NOW, NOW - timedelta(days=1))
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "start,end",
    [(None, None), (NOW, None), (None, NOW), (NOW, NOW), (NOW, NOW + timedelta(hours=1))],
)
def test_every_other_window_shape_is_allowed(start, end):
    """A half-open window is legitimate — "everything after the branch cut"
    has no end — and an instantaneous one is merely useless, not invalid."""
    release_service._check_cutoff_window(start, end)
