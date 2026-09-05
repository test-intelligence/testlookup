"""S1 — release identity, and one primary release per run.

Three changes, each fixing something the release dimension cannot be built on.

**1. Release identity (``sort_key``, ``release_type``, baseline, cutoff dates).**

A release was a case-insensitive name and nothing else, so "compare 2.4.0
against its predecessor" had no way to order two releases and no way to say
which one *is* the predecessor. ``sort_key`` is a total order over version
strings (see ``services/release_sort_key.py`` for the encoding and why a plain
zero-pad is not enough); ``baseline_release_id`` lets a hotfix point at its
parent rather than at whatever shipped most recently.

**2. ``is_primary`` on the run→release link.**

The link table is many-to-many and correctly so: a hotfix build genuinely can
be validated for both 2.3.1 and 2.4.0. But every reader assumed one row per
run — ``runs_service.fetch_release_map`` builds a dict keyed on ``test_run_id``,
so a multi-linked run silently keeps whichever row Postgres returned last and
the run list renders a non-deterministic release badge.

This is **not** hypothetical and this migration is a repair, not a precaution.
``link_run_to_release`` dedupes on ``(release_id, test_run_id)`` and never
removes a run's prior link, so any run that was auto-attributed and then
re-labelled by hand through the Run Detail control or the Link Run modal
already carries two rows today.

``is_primary`` (partial-unique per run) gives analytics a deterministic single
release while leaving the secondary memberships intact for the cherry-pick case.

**3. ``project_id`` on the link, and an index on ``test_run_id``.**

Nothing constrained a link to join a run and a release in the *same* project, so
a caller with QA_LEAD on project A who knew a run id from project B could pull
B's results into A's release — and from there into A's release scorecard and its
signed compliance pack. The service layer now rejects that; this migration adds
the denormalized ``project_id`` and sweeps for rows that already violate it.

**What this does NOT do, despite what this paragraph used to say.** The column
is added bare — no foreign key, no CHECK — so the database enforces nothing
about it; the guarantee is the service layer's alone. Claiming otherwise here
was worse than silence, because it is the sentence a reader consults before
deciding whether they still need an application-level check.

Real database enforcement needs a COMPOSITE foreign key
(``(release_id, project_id) -> releases(id, project_id)`` and the same for
``test_runs``), which in turn needs a unique constraint on ``(id, project_id)``
on both of those tables. That is a migration of its own against two large
tables, not a clause in this one.

And ``release_test_run_links`` had an index on ``release_id`` only. The hot read
is the other direction: ``fetch_release_map`` runs an ``IN`` over the run ids on
every run-list render and sequentially scanned.

The primary tie-break
---------------------
For a run with several links, "which one is primary?" is decided by evidence
quality, not by arrival order:

  1. An **assertion** beats an inference — a link a client named
     (``explicit_client``) or a human chose (``manual_ui``) wins over anything
     the system worked out. This is why S0's ``link_source`` had to land first:
     without it this migration would have nothing to rank by and would fall
     back to arrival order, which would systematically pick the *automatic*
     link, since ingest always runs before a human re-labels.
  2. Then a specific inference beats a fallback (``default_fallback`` /
     ``unknown`` rank last).
  3. Then earliest ``linked_at``, then ``id`` — so the result is deterministic
     and the backfill is idempotent.
"""

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision = "0151"
down_revision = "0150"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Release identity ─────────────────────────────────────────────────────
    op.add_column("releases", sa.Column("release_type", sa.String(length=20), nullable=True))
    op.add_column("releases", sa.Column("sort_key", sa.String(length=64), nullable=True))
    op.add_column("releases", sa.Column("target_environment", sa.String(length=100), nullable=True))
    op.add_column("releases", sa.Column("cutoff_start_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("releases", sa.Column("cutoff_end_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "releases",
        sa.Column(
            "baseline_release_id",
            sa.UUID(as_uuid=True),
            # SET NULL, not CASCADE: deleting 2.3.0 must not delete 2.4.0. The
            # sibling release FKs on this schema are CASCADE, so leaving the
            # ondelete unspecified would inherit exactly the wrong behaviour.
            sa.ForeignKey("releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── Link provenance and scope ────────────────────────────────────────────
    op.add_column(
        "release_test_run_links",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "release_test_run_links",
        sa.Column(
            "linked_by_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "release_test_run_links",
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=True),
    )

    bind = op.get_bind()

    # ── Pre-flight: report, do not abort ─────────────────────────────────────
    # A migration that fails on unexpected data at 3am is worse than one that
    # reports what it found and applies a stated rule. Both counts are logged
    # so the numbers are on the record before anything is rewritten.
    multi = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM (
                SELECT test_run_id FROM release_test_run_links
                 GROUP BY test_run_id HAVING count(*) > 1
            ) AS m
            """
        )
    ).scalar_one()
    logger.info("0151 pre-flight: %s run(s) carry more than one release link", multi)

    # ── sort_key is deliberately NOT backfilled here ────────────────────────
    # An earlier draft reimplemented the encoder in SQL. It was wrong, and
    # wrong in the one way that matters: the guard regex accepted only bare
    # digits and dots, so every pre-release ("2.4.0-rc1") and every build
    # ("2.4.0+sha") fell through to the TEXT band — sorting them after every
    # real version instead of immediately before their own GA. That is the
    # precise inversion release_sort_key.py exists to prevent, reintroduced by
    # the migration that was supposed to establish the ordering.
    #
    # Two implementations of one encoding is the drift this codebase has been
    # bitten by before, and SQL is the copy that cannot be unit-tested. So the
    # column is left NULL and populated by ``reconcile_release_sort_keys``,
    # which calls ``compute_sort_key`` itself — one encoder, one behaviour.
    #
    # NULL is safe in the interim: nothing reads sort_key yet, and the sweep
    # runs hourly.

    # ── Backfill: link project_id ────────────────────────────────────────────
    op.execute(
        """
        UPDATE release_test_run_links AS l
           SET project_id = r.project_id
          FROM releases AS r
         WHERE r.id = l.release_id
        """
    )

    # Cross-project links cannot be repaired automatically — which project the
    # link "should" have been in is unknowable. Report them so an operator can
    # decide, and leave the rows alone.
    crossed = bind.execute(
        sa.text(
            """
            SELECT count(*)
              FROM release_test_run_links l
              JOIN test_runs tr ON tr.id = l.test_run_id
             WHERE tr.project_id <> l.project_id
            """
        )
    ).scalar_one()
    if crossed:
        logger.warning(
            "0151: %s link(s) join a run and a release in DIFFERENT projects — "
            "left in place for manual review; the service layer now rejects new ones",
            crossed,
        )

    # ── Backfill: is_primary, by the tie-break in this module's docstring ────
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY test_run_id
                       ORDER BY
                           CASE link_source
                               WHEN 'explicit_client'  THEN 0
                               WHEN 'manual_ui'        THEN 0
                               WHEN 'rule_match'       THEN 1
                               WHEN 'cutoff_window'    THEN 1
                               WHEN 'active_release'   THEN 2
                               ELSE 3
                           END,
                           linked_at ASC,
                           id ASC
                   ) AS rn
              FROM release_test_run_links
        )
        UPDATE release_test_run_links AS l
           SET is_primary = TRUE
          FROM ranked
         WHERE ranked.id = l.id AND ranked.rn = 1
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
    # Indexes are dropped by 0153's downgrade, which owns them.
    op.drop_column("release_test_run_links", "project_id")
    op.drop_column("release_test_run_links", "linked_by_id")
    op.drop_column("release_test_run_links", "is_primary")

    op.drop_column("releases", "baseline_release_id")
    op.drop_column("releases", "cutoff_end_at")
    op.drop_column("releases", "cutoff_start_at")
    op.drop_column("releases", "target_environment")
    op.drop_column("releases", "sort_key")
    op.drop_column("releases", "release_type")
