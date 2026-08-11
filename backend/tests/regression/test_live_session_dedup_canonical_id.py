"""One live run must not appear twice under two identities.

``list_active_sessions`` merges Redis state with two DB queries and dedups on
``run_id``. Redis keys a run by whatever the SDK supplied — frequently a slug
like ``local-abc12345`` — while the DB rows carry the UUID persisted for it, so
the comparison never matched and the run was listed twice.

Measured live (after the deleted-project fix trimmed the list to two rows, both
of which turned out to be the *same* run):

===============  =======================================  ==============
build_number     run_id                                   source
===============  =======================================  ==============
``sdk-probe-1``  ``sdk-probe-1``                          Redis (slug)
``sdk-probe-1``  ``bd337e00-38ae-50ee-b2e5-0f21077a711b`` DB (UUID)
===============  =======================================  ==============

``canonical_test_run_uuid`` is the module's existing mapping for exactly this,
and its own docstring records three earlier call sites that drifted apart the
same way — this is a fourth.

**This is the opposite of the older dedup bug.** The ledger's rule "dedup by
``run_id``, NOT ``build_number``" still holds: two genuinely different runs may
share a build number. Here one run carried two identities.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.services import stream_service  # noqa: E402
from app.services.stream_service import canonical_test_run_uuid  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(stream_service.list_active_sessions)


class TestTheMappingItself:
    def test_a_uuid_maps_to_itself(self):
        u = uuid.uuid4()
        assert canonical_test_run_uuid(str(u)) == u

    def test_a_slug_maps_deterministically(self):
        """Two sightings of the same slug must land on one id, or the dedup
        would still fail."""
        assert canonical_test_run_uuid("local-abc12345") == canonical_test_run_uuid(
            "local-abc12345"
        )

    def test_different_slugs_do_not_collide(self):
        assert canonical_test_run_uuid("local-aaa") != canonical_test_run_uuid(
            "local-bbb"
        )

    def test_a_slug_and_its_persisted_uuid_agree(self):
        """The actual defect: the DB row for a slug-keyed run is stored under
        the UUID5 derived from that slug, so both forms must converge."""
        slug = "sdk-probe-1"
        derived = uuid.uuid5(uuid.NAMESPACE_DNS, slug)
        assert canonical_test_run_uuid(slug) == derived
        assert canonical_test_run_uuid(str(derived)) == derived


class TestTheDedupUsesIt:
    def test_the_redis_set_is_canonicalised(self):
        assert "canonical_test_run_uuid(str(session.get(\"run_id\")))" in SOURCE, (
            "the Redis run ids are not canonicalised, so a slug-keyed run will "
            "not match its own DB row and gets listed twice"
        )

    def test_the_live_session_loop_is_canonicalised(self):
        assert "canonical_test_run_uuid(str(session.run_id))" in SOURCE

    def test_the_testrun_loop_compares_uuids(self):
        """``TestRun.id`` is already a UUID — comparing ``str(run.id)`` against
        a set of UUIDs silently never matches."""
        assert "if run.id in seen_run_ids:" in SOURCE
        assert "seen_run_ids.add(run.id)" in SOURCE

    def test_no_raw_string_comparison_remains(self):
        assert "if str(run.id) in seen_run_ids" not in SOURCE
        assert "if session.run_id in seen_run_ids" not in SOURCE
