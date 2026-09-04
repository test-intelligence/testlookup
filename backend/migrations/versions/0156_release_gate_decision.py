"""release gate decision — a verdict per release, append-only

Revision ID: 0156
Revises: 0155
Create Date: 2026-09-04

A NEW table with zero rows, so every index is created inline and in the
transaction. The CONCURRENTLY treatment migration 0153 needed exists because
building an index on a populated table takes a lock the application cannot
afford; there is nothing here to lock out.

Two partial unique indexes, matching the model:

* one CURRENT release-level verdict per release (``phase_id IS NULL``), and
* one CURRENT verdict per phase (``phase_id IS NOT NULL``).

They are separate rather than one index over ``(release_id, phase_id)`` because
Postgres treats NULLs as distinct: a single index would leave the release-level
rows — the ones with a NULL phase — entirely unconstrained, which is precisely
the case the first index exists to enforce.

Both are partial on ``is_current`` so history rows are constrained by neither.
An unfiltered unique index would collapse the append-only history this table is
built to keep, allowing exactly one verdict per release ever.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0156"
down_revision = "0155"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "release_gate_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Phase rows arrive with S6b. The column ships now so that slice does
        # not need a second migration against a table that by then has rows.
        sa.Column(
            "phase_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_phases.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # GO | CONDITIONAL_GO | NO_GO | NOT_EVALUATED.
        sa.Column("verdict", sa.String(length=20), nullable=False),
        # ── snapshot ────────────────────────────────────────────────────────
        sa.Column("denominator", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("run_ids", sa.JSON(), nullable=True),
        sa.Column("status_rollup", sa.JSON(), nullable=True),
        sa.Column("attribution_mix", sa.JSON(), nullable=True),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_gate_policies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("policy_snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "baseline_release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("blocking_reasons", sa.JSON(), nullable=True),
        sa.Column("conditions_for_go", sa.JSON(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_rgd_release_current",
        "release_gate_decisions",
        ["release_id"],
        unique=True,
        postgresql_where=sa.text("is_current IS TRUE AND phase_id IS NULL"),
    )
    op.create_index(
        "ix_rgd_phase_current",
        "release_gate_decisions",
        ["release_id", "phase_id"],
        unique=True,
        postgresql_where=sa.text("is_current IS TRUE AND phase_id IS NOT NULL"),
    )
    op.create_index(
        "ix_rgd_release_created",
        "release_gate_decisions",
        ["release_id", "created_at"],
    )


def downgrade() -> None:
    # Indexes go with the table; dropping them first would be redundant. The
    # table is new in this revision, so the downgrade is a true inverse and
    # loses nothing that existed before it.
    op.drop_index("ix_rgd_release_created", table_name="release_gate_decisions")
    op.drop_index("ix_rgd_phase_current", table_name="release_gate_decisions")
    op.drop_index("ix_rgd_release_current", table_name="release_gate_decisions")
    op.drop_table("release_gate_decisions")
