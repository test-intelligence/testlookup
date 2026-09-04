"""Regression: the release filter inside a saved view (S5-3f-ii).

Storage needed no migration — ``SavedView.filters`` is already JSON. What needs
guarding is what happens when somebody ELSE, or a LATER somebody, opens the view.

Three ways a stored release id goes wrong
------------------------------------------
**Wrong project.** Releases belong to exactly one project, so a view saved
against project A and opened while B is active would filter every page by an id
nothing matches — an empty page with the view's name showing, which is the
"product looks broken" failure `releaseStore` exists to prevent, arriving by a
different route.

**Wrong reader.** ``is_shared`` makes a view visible to every member of the
project, so the release id inside it is read by people who never chose it. A
stored id is still an id somebody else supplied, and it is validated against the
READER's access rather than trusted because it was stored.

**Gone.** A release can be deleted after the view is saved. Filtering by a
dangling id shows an empty page and blames the data.

In every case the release is DROPPED and the view still opens. Losing one filter
is recoverable; refusing to open a saved view because one field went stale is
not.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.services import saved_view_release as svr

PROJECT_A = uuid.uuid4()
PROJECT_B = uuid.uuid4()
RELEASE = uuid.uuid4()


def _session(release=None):
    class _Result:
        def scalar_one_or_none(self):
            return release

    class _Session:
        async def execute(self, *a, **kw):
            return _Result()

    return _Session()


def _release_row(project_id=PROJECT_A, release_id=RELEASE):
    return SimpleNamespace(id=release_id, project_id=project_id)


def _resolve(filters, view_project=PROJECT_A, active_project=PROJECT_A,
             release=None, accessible=None):
    return asyncio.run(
        svr.resolve_for_reader(
            _session(release),
            filters,
            view_project_id=view_project,
            active_project_id=active_project,
            accessible_project_ids=accessible,
        )
    )


class TestStoringIsOneSpellingAndRemovesRatherThanNulls:
    def test_a_release_is_stored_under_one_key(self):
        stored = svr.store_release({"days": 30}, str(RELEASE))

        assert stored[svr.RELEASE_KEY] == str(RELEASE)
        # Additive: the view's other filters survive.
        assert stored["days"] == 30

    def test_clearing_removes_the_key_rather_than_storing_null(self):
        stored = svr.store_release({svr.RELEASE_KEY: str(RELEASE), "days": 30}, None)

        # A key present with a null value and a key absent read identically to
        # most consumers but not to all, and `{"release_id": null}` invites a
        # reader to treat null as a filter value.
        assert svr.RELEASE_KEY not in stored
        assert stored["days"] == 30

    def test_extracting_from_an_empty_view_is_none_not_an_error(self):
        assert svr.extract_release(None) is None
        assert svr.extract_release({}) is None


class TestAViewSavedForAnotherProject:
    def test_the_release_is_dropped_with_a_reason(self):
        result = _resolve(
            {svr.RELEASE_KEY: str(RELEASE)},
            view_project=PROJECT_A,
            active_project=PROJECT_B,
        )

        # Applying it filters by an id nothing in project B matches: an empty
        # page with the view's name showing.
        assert result["applied"] is False
        assert result["release_id"] is None
        assert "different project" in result["reason"]

    def test_the_view_itself_is_not_refused(self):
        result = _resolve(
            {svr.RELEASE_KEY: str(RELEASE), "days": 30},
            view_project=PROJECT_A,
            active_project=PROJECT_B,
        )

        # Losing one filter is recoverable; refusing to open a saved view
        # because one field went stale is not.
        assert result["reason"] is not None
        assert "release_id" in result

    def test_a_global_view_with_no_active_project_still_drops_it(self):
        # The case the explicit None guard exists for, and the only one that
        # distinguishes it from the string comparison below it: with BOTH sides
        # None, `str(None) == str(None)` is True, so without the guard a global
        # view would apply its release with no project pinned at all.
        result = _resolve(
            {svr.RELEASE_KEY: str(RELEASE)}, view_project=None, active_project=None
        )

        assert result["applied"] is False
        assert "different project" in result["reason"]

    def test_a_global_view_carrying_a_release_drops_it(self):
        # A view with no project claims to apply everywhere, while a release
        # belongs to exactly one project. The combination is incoherent, so the
        # release goes and the rest of the view opens.
        result = _resolve(
            {svr.RELEASE_KEY: str(RELEASE)}, view_project=None, active_project=PROJECT_A
        )

        assert result["applied"] is False


class TestASharedViewIsValidatedAgainstTheReader:
    def test_a_release_the_reader_cannot_reach_is_dropped(self):
        result = _resolve(
            {svr.RELEASE_KEY: str(RELEASE)},
            release=_release_row(project_id=PROJECT_A),
            accessible={PROJECT_B},
        )

        # `is_shared` means somebody else's stored id is being read. It is
        # validated against the reader, not trusted because it was stored.
        assert result["applied"] is False
        assert "do not have access" in result["reason"]

    def test_a_release_the_reader_can_reach_is_applied(self):
        result = _resolve(
            {svr.RELEASE_KEY: str(RELEASE)},
            release=_release_row(project_id=PROJECT_A),
            accessible={PROJECT_A},
        )

        assert result["applied"] is True
        assert result["release_id"] == str(RELEASE)

    def test_an_admin_with_no_restriction_set_is_not_blocked(self):
        # `accessible_project_ids=None` is the admin path in this codebase, and
        # it must not be read as "an empty set of projects".
        result = _resolve(
            {svr.RELEASE_KEY: str(RELEASE)},
            release=_release_row(project_id=PROJECT_A),
            accessible=None,
        )

        assert result["applied"] is True


class TestAReleaseThatNoLongerExists:
    def test_a_deleted_release_is_dropped_with_a_reason(self):
        result = _resolve({svr.RELEASE_KEY: str(RELEASE)}, release=None)

        # Filtering by a dangling id shows an empty page and blames the data.
        assert result["applied"] is False
        assert "no longer exists" in result["reason"]

    def test_a_malformed_stored_id_is_dropped_rather_than_raising(self):
        result = _resolve({svr.RELEASE_KEY: "not-a-uuid"})

        # Saved-view JSON is not validated on write, so a hand-edited or
        # migrated row can carry anything. Raising here would make the view
        # unopenable.
        assert result["applied"] is False
        assert "not a valid release id" in result["reason"]


class TestTheUnattributedBucketTravelsToo:
    def test_it_survives_when_the_project_matches(self):
        result = _resolve({svr.RELEASE_KEY: "unattributed"})

        # It names no release, so there is nothing to look up — but it IS
        # project-scoped in effect, because the runs it returns are.
        assert result["applied"] is True
        assert result["release_id"] == "unattributed"

    def test_it_is_dropped_for_a_different_project(self):
        result = _resolve(
            {svr.RELEASE_KEY: "unattributed"},
            view_project=PROJECT_A,
            active_project=PROJECT_B,
        )

        assert result["applied"] is False

    def test_it_does_not_hit_the_database(self):
        # Looking it up would 422 it as a malformed UUID, which is how a
        # sentinel silently stops working when a new reader forgets it exists.
        class _Exploding:
            async def execute(self, *a, **kw):  # pragma: no cover
                raise AssertionError("the bucket names no release to look up")

        result = asyncio.run(
            svr.resolve_for_reader(
                _Exploding(),
                {svr.RELEASE_KEY: "UNATTRIBUTED"},
                view_project_id=PROJECT_A,
                active_project_id=PROJECT_A,
            )
        )

        assert result["applied"] is True


class TestAViewWithNoRelease:
    def test_nothing_is_applied_and_nothing_is_reported(self):
        result = _resolve({"days": 30})

        # The overwhelmingly common case. Reporting a reason here would train
        # readers to ignore the message that matters.
        assert result["applied"] is False
        assert result["release_id"] is None
        assert result["reason"] is None
