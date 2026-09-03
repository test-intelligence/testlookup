"""S0 — the active release, and provenance on the run→release link.

Two changes that have to land together.

**1. ``releases.is_active`` supersedes ``is_default``.**

``is_default`` marked one permanent row per project that every unlabelled run
fell into forever. Because it never rotated, membership of that row carried no
information about which release was actually underway — a run from the 2.1
cycle and a run from the 2.6 cycle sat in the same bucket with nothing to
separate them.

``is_active`` is the same partial-unique shape (at most one per project) but is
a *rotating* pointer: it moves as releases ship, so an unlabelled run lands in
whatever release was genuinely in flight. ``activated_at`` / ``deactivated_at``
record the interval each release held the flag, so attribution can later resolve
*as of* a run's execution time rather than reading a mutable pointer at ingest.

``is_default`` is deliberately NOT dropped here. It is kept as a dormant column
for at least one release so this migration has a real downgrade — the flag can
be read back if S0 is rolled back. A later slice drops it.

**2. ``release_test_run_links.link_source``.**

The link table records *that* a run belongs to a release but never *how* it came
to. A QA lead deliberately assigning an RC build and a nightly falling into the
default bucket are the same row shape, so every release metric built on this
table silently mixes assertion with inference.

This column ships in the same migration as ``is_active`` on purpose. Without it,
the rotating active release is a strictly *worse* disguise than the bucket it
replaces: today's junk drawer at least self-labels as ``Default Release (…)``,
whereas a rotating pointer lands untagged runs under a real version number with
nothing able to tell them apart from runs a human tagged.

Backfill honesty
----------------
Existing ``is_default`` rows become the project's active release — there is no
better candidate, and leaving projects with no active release would violate the
invariant this slice introduces. But they are marked truthfully:

  * ``is_auto_named = true`` — they were auto-created by ``release_linker`` and
    were never named by a human, so the "name your release" prompt covers them.
  * every link that already pointed at them gets ``link_source =
    'default_fallback'`` — the pre-existing membership is inference, and the
    scorecard must be able to say so.

Links to any other release predate this column with no recoverable provenance;
they are stamped ``'unknown'`` rather than guessed at. ``'unknown'`` is a
distinct value from ``'default_fallback'`` precisely so a backfilled row is
never mistaken for a measured one.
"""

from alembic import op
import sqlalchemy as sa


revision = "0150"
down_revision = "0149"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── releases: the active pointer + naming/activation provenance ──────────
    op.add_column(
        "releases",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "releases",
        sa.Column(
            "is_auto_named",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "releases",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "releases",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── release_test_run_links: how did this link come to be? ────────────────
    # Nullable with no server_default: a NULL here means "written before this
    # column existed and not covered by the backfill below", which is a
    # different statement from any of the enumerated sources. Code that writes
    # a link from now on always supplies a value.
    op.add_column(
        "release_test_run_links",
        sa.Column("link_source", sa.String(length=30), nullable=True),
    )

    # ── Backfill ─────────────────────────────────────────────────────────────
    # Order matters: stamp the links against is_default BEFORE flipping
    # is_active, so the "which links belong to the old bucket" question is
    # answered from the column that still means what it meant.
    op.execute(
        """
        UPDATE release_test_run_links AS l
           SET link_source = 'default_fallback'
          FROM releases AS r
         WHERE r.id = l.release_id
           AND r.is_default IS TRUE
        """
    )
    op.execute(
        """
        UPDATE release_test_run_links
           SET link_source = 'unknown'
         WHERE link_source IS NULL
        """
    )

    # Every project's default release becomes its active release, flagged as
    # never-named-by-a-human so the UI prompt and the cross-project count both
    # cover it from day one.
    op.execute(
        """
        UPDATE releases
           SET is_active = TRUE,
               is_auto_named = TRUE,
               activated_at = COALESCE(activated_at, created_at)
         WHERE is_default IS TRUE
        """
    )

    # Indexes are created in 0153, not here. Everything above this point
    # is one atomic transaction; a CONCURRENTLY build cannot run inside a
    # transaction, so putting one here would commit every preceding
    # statement first — and a failure at the build would then leave a
    # migration that cannot be re-run, because add_column is not
    # idempotent. `alembic upgrade head` would be wedged until someone
    # hand-repaired the schema. Same split as 0082/0098.


def downgrade() -> None:
    # The index is dropped by 0153's downgrade, which owns it.
    op.drop_column("release_test_run_links", "link_source")
    op.drop_column("releases", "deactivated_at")
    op.drop_column("releases", "activated_at")
    op.drop_column("releases", "is_auto_named")
    op.drop_column("releases", "is_active")
    # is_default was never dropped, so a downgraded deployment falls straight
    # back to the pre-S0 fallback with no data loss.
