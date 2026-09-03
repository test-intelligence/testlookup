"""Regression: release ordering and the single primary link (migration 0151).

Two properties, both of which fail silently rather than loudly when broken.

**Ordering.** ``sort_key`` decides which release is "the previous one" for
release-over-release comparison. Every naive encoding gets some real version
wrong — lexicographic puts 2.10.0 before 2.9.0, a plain zero-pad sorts an RC
*after* its own GA — and the symptom is not an error, it is a comparison
against the wrong baseline.

**One primary link.** ``release_test_run_links`` is many-to-many by design, but
``fetch_release_map`` returns one release per run. Before 0151 a multi-linked
run kept whichever row Postgres returned last, so the run list's release badge
was non-deterministic. That was live, not latent: the linker never removes a
prior link, so any run auto-attributed and then re-labelled by hand already had
two rows.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.postgres import (
    ASSERTED_LINK_SOURCES,
    LinkSource,
    Release,
    ReleaseTestRunLink,
)
from app.services.release_sort_key import (
    KIND_TEXT,
    KIND_VERSION,
    NO_PRERELEASE,
    compute_sort_key,
)


# ── Ordering ─────────────────────────────────────────────────────────────────


def test_numeric_segments_sort_numerically_not_lexicographically():
    """2.10.0 comes after 2.9.0. Sorting by name gets this backwards, and it is
    the single most common way a version ordering is wrong.
    """
    assert compute_sort_key("2.9.0") < compute_sort_key("2.10.0")
    assert compute_sort_key("2.99.0") < compute_sort_key("2.100.0")
    assert compute_sort_key("9.0.0") < compute_sort_key("10.0.0")


def test_a_release_candidate_sorts_before_its_own_ga():
    """The reason "no pre-release" is encoded as a sentinel rather than as an
    empty string.

    With an empty string, ``2.4.0`` (shorter) sorts before ``2.4.0-rc1``
    (longer) — the exact inversion of what a release train means. ``~`` sorts
    after every alphanumeric, so GA outranks its candidates.
    """
    assert compute_sort_key("2.4.0-rc1") < compute_sort_key("2.4.0")
    assert compute_sort_key("2.4.0-rc1") < compute_sort_key("2.4.0-rc2")
    assert compute_sort_key("2.4.0-beta") < compute_sort_key("2.4.0")
    assert compute_sort_key("2.4.0").endswith(NO_PRERELEASE)


def test_every_parsed_version_sorts_before_every_unparseable_name():
    """The band prefix is what makes this a TOTAL order.

    Without it ``Unreleased`` and ``2.4.0`` interleave by first character, so a
    project holding both would order them essentially at random — and the
    baseline for a comparison would be whichever won that coin flip.
    """
    text_key = compute_sort_key(None, "Unreleased")
    assert text_key.startswith(KIND_TEXT)
    for version in ("0.0.1", "2.4.0", "10.0.0", "99999.0.0"):
        assert compute_sort_key(version).startswith(KIND_VERSION)
        assert compute_sort_key(version) < text_key


def test_equivalent_spellings_of_one_version_produce_one_key():
    """``2.4``, ``2.4.0``, ``v2.4.0`` and ``2.4.0+sha`` are the same release.

    Build metadata is excluded from precedence by semver, and two builds of one
    version are not two releases.
    """
    canonical = compute_sort_key("2.4.0")
    for spelling in ("2.4", "v2.4.0", "V2.4.0", " 2.4.0 ", "2.4.0+abc123", "2.4.0.0"):
        assert compute_sort_key(spelling) == canonical, spelling


def test_version_is_preferred_over_name_but_name_is_the_fallback():
    """``Release.version`` is NULL on every release the ingest linker creates —
    those carry the version, if any, in ``name``. Reading only ``version``
    would drop every auto-created release into the text band.
    """
    assert compute_sort_key("2.4.0", "some label") == compute_sort_key("2.4.0")
    assert compute_sort_key(None, "2.4.0") == compute_sort_key("2.4.0")
    assert compute_sort_key("", "2.4.0") == compute_sort_key("2.4.0")


def test_key_fits_the_column():
    """String(64) on the model. A key that overflows would raise on write for
    exactly the pathological names most likely to appear in real data.
    """
    long_name = "a really quite unreasonably long release name " * 4
    for candidate in ("99999.99999.99999.99999", long_name, "2.4.0-" + "x" * 200):
        assert len(compute_sort_key(candidate)) <= 64, candidate


def test_segments_at_the_encoding_ceiling_still_order():
    """The boundary where a fixed-width pad stops being a total order.

    A segment wider than SEGMENT_WIDTH overflows its pad, making the whole key
    longer than its siblings — so it sorts by first character rather than by
    value, silently inverting the order. Clamping keeps the width invariant;
    beyond the ceiling versions compare equal, which is documented and far
    better than compared backwards.
    """
    from app.services.release_sort_key import SEGMENT_MAX

    assert compute_sort_key("1.0.99999") < compute_sort_key("1.0.100000")
    assert compute_sort_key(f"1.0.{SEGMENT_MAX - 1}") < compute_sort_key(f"1.0.{SEGMENT_MAX}")
    # At and beyond the ceiling everything collapses to one key — equal, never
    # inverted.
    assert compute_sort_key(f"1.0.{SEGMENT_MAX}") == compute_sort_key(f"1.0.{SEGMENT_MAX + 5000}")
    assert len(compute_sort_key(f"{SEGMENT_MAX}.{SEGMENT_MAX}.{SEGMENT_MAX}.{SEGMENT_MAX}")) <= 64


def test_ordering_is_stable_across_a_realistic_release_train():
    train = [
        "1.9.0", "1.10.0", "2.0.0-rc1", "2.0.0-rc2", "2.0.0",
        "2.0.1", "2.1.0", "2.10.0", "10.0.0",
    ]
    assert sorted(train, key=compute_sort_key) == train


# ── Primary link ─────────────────────────────────────────────────────────────


def test_exactly_one_primary_link_per_run_is_a_database_constraint():
    """If this were only enforced in Python, two writers could each mark their
    own link primary and ``fetch_release_map`` would be non-deterministic
    again — the precise bug 0151 exists to close.
    """
    index = next(
        (i for i in ReleaseTestRunLink.__table__.indexes if i.name == "ix_rtr_links_primary"),
        None,
    )
    assert index is not None
    assert index.unique is True
    assert [c.name for c in index.columns] == ["test_run_id"]
    where = str(index.dialect_options["postgresql"]["where"])
    assert "is_primary" in where, (
        "must be PARTIAL — a plain unique index on test_run_id would allow a "
        "run to belong to only one release at all, breaking the cherry-pick case"
    )


def test_the_link_table_is_indexed_in_the_direction_it_is_read():
    """``fetch_release_map`` runs an IN over run ids on every run-list render.
    The pre-0151 index covered ``release_id`` only, so that scanned.
    """
    names = {i.name for i in ReleaseTestRunLink.__table__.indexes}
    assert "ix_rtr_links_test_run" in names
    assert "ix_rtr_links_release" in names, "the release-side read still needs its index"


def test_link_carries_project_scope_and_actor():
    assert "project_id" in ReleaseTestRunLink.__table__.c
    assert "linked_by_id" in ReleaseTestRunLink.__table__.c


@pytest.mark.asyncio
async def test_linking_a_run_from_another_project_is_rejected():
    """The same-project rule, asserted as BEHAVIOUR rather than as a column.

    Column-presence is not the guarantee — a denormalized ``project_id`` that
    nothing checks is decorative. What matters is that the service refuses the
    link, because this table feeds the release scorecard and the signed
    compliance pack.

    404 rather than 403 deliberately: a caller with no access to that run must
    not learn whether the id exists.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from fastapi import HTTPException

    from app.services import release_service

    release = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    foreign_run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    body = SimpleNamespace(test_run_id=str(foreign_run.id), phase_id=None)

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: foreign_run)
    )

    with patch.object(
        release_service, "get_release_or_404", AsyncMock(return_value=release)
    ):
        with pytest.raises(HTTPException) as exc:
            await release_service.link_test_run(db, str(release.id), body)

    assert exc.value.status_code == 404, (
        "a cross-project link must be refused, and must not confirm the run exists"
    )


def test_baseline_pointer_does_not_cascade():
    """Deleting 2.3.0 must not delete 2.4.0.

    The sibling release FKs on this schema are CASCADE, so an unspecified
    ondelete here would inherit exactly the wrong behaviour and a release
    deletion would silently take its successors with it.
    """
    fks = list(Release.__table__.c.baseline_release_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "SET NULL"


# ── Migration ────────────────────────────────────────────────────────────────


def _migration_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0151_release_identity_and_primary_link.py"
    ).read_text(encoding="utf-8")


def test_primary_backfill_ranks_assertions_above_inferences():
    """Arrival order would systematically pick the WRONG link.

    Ingest always attributes before a human re-labels, so ``ORDER BY
    linked_at`` alone would make the automatic link primary on every run
    somebody had corrected — silently discarding the correction. Evidence
    quality has to come first in the sort.
    """
    src = _migration_source()
    ranking = src[src.index("row_number() OVER") : src.index("AS rn")]
    for asserted in ("'explicit_client'", "'manual_ui'"):
        assert asserted in ranking
    assert ranking.index("'explicit_client'") < ranking.index("'active_release'"), (
        "an assertion must outrank an inference in the primary tie-break"
    )
    # linked_at is a tie-break WITHIN a rank, never the primary sort.
    assert ranking.index("CASE link_source") < ranking.index("linked_at")


def test_primary_backfill_is_deterministic():
    """``id`` is the final tie-break, so re-running the backfill picks the same
    row. Without it two links written in the same transaction share a
    ``linked_at`` and the choice is arbitrary — which makes the migration
    non-idempotent in exactly the case it was written for.
    """
    src = _migration_source()
    ranking = src[src.index("row_number() OVER") : src.index("AS rn")]
    assert "id ASC" in ranking


def test_migration_reports_rather_than_aborts_on_unexpected_data():
    """A migration that raises on real data at 3am is worse than one that
    reports what it found and applies a stated rule.
    """
    src = _migration_source()
    assert "pre-flight" in src
    assert "logger.info" in src
    assert "logger.warning" in src


def test_cross_project_links_are_reported_not_silently_rewritten():
    """Which project a cross-project link "should" have been in is unknowable,
    so guessing would fabricate attribution. Report and leave alone.
    """
    src = _migration_source()
    assert "tr.project_id <> l.project_id" in src
    assert "manual review" in src


def test_0151_creates_no_indexes():
    """Index builds live in 0153, and the separation is load-bearing.

    ``CREATE INDEX CONCURRENTLY`` cannot run in a transaction, so Alembic has
    to commit everything before it. Put one at the end of a migration that also
    adds columns and a failure at the build leaves the columns committed and
    the migration incomplete — the next ``alembic upgrade head`` re-runs
    ``add_column`` against a column that exists and dies. Recovering means
    hand-editing the schema.
    """
    src = _migration_source()
    up = src[: src.index("def downgrade()")]
    assert "op.create_index(" not in up, (
        "0151 must stay fully transactional — indexes belong in 0153"
    )
    assert "autocommit_block" not in up


def test_link_source_values_used_by_the_backfill_all_exist():
    """A typo'd literal in the ranking CASE would fall to the ELSE branch and
    silently rank a real assertion last — the ranking would still "work", just
    wrongly. Pin the vocabulary to the enum.
    """
    src = _migration_source()
    ranking = src[src.index("CASE link_source") : src.index("END,")]
    quoted = {
        part.split("'")[0]
        for part in ranking.split("WHEN '")[1:]
    }
    known = {s.value for s in LinkSource}
    # Without this, an empty set satisfies the subset check vacuously — a
    # ranking that parsed to nothing at all would pass.
    assert quoted, "the ranking CASE parsed to no link_source literals"
    assert quoted <= known, f"unknown link_source literals in migration: {quoted - known}"
    # And pin the other direction: a new asserted source the migration does not
    # rank would silently fall to ELSE and lose to an inference.
    assert ASSERTED_LINK_SOURCES <= quoted, (
        f"migration does not rank every asserted source: {ASSERTED_LINK_SOURCES - quoted}"
    )


def test_primary_backfill_partitions_per_run():
    """One primary per RUN, not per release or globally.

    Partitioning by anything else would mark one link primary across the whole
    table, leaving every other run with none — and the partial unique index
    would happily accept that, because "at most one" is satisfied by zero.
    """
    src = _migration_source()
    ranking = src[src.index("row_number() OVER") : src.index("AS rn")]
    assert "PARTITION BY test_run_id" in ranking
