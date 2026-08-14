"""Add systemic_flake_cluster — tests that fail TOGETHER, across runs.

Phase 3 of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

## Why this is not ``failure_clusters``

``failure_clusters`` already exists, and reusing it would have been the obvious
move. It is the wrong object on both axes:

* **Scope.** It is keyed ``test_run_id`` with ``ON DELETE CASCADE`` — a grouping
  *inside one run*, which dies with that run.
* **Mechanism.** It is built by semantic embedding of error *messages*
  (``tools/embed_and_cluster.py``): "these failures read alike".

Systemic flakiness is neither. It is a grouping **across runs**, built from
**literal co-failure** — the same set of runs turning red for a group of tests,
regardless of whether their messages resemble each other at all. Two tests can
co-fail on every network blip while reporting completely different errors, and
message similarity would never join them.

So this is a separate entity with a separate lifetime, recomputed on a window.

## What it is for

75% of flaky tests fail in co-occurring clusters rather than in isolation, with
shared root causes dominated by networking and external-dependency instability.
Per-test quarantine and per-test triage mismatch that: the useful unit is the
cluster, and "these 14 tests flip together and it smells like an external
dependency" is both more actionable and more verifiable than 14 separate flags.

``cause_family`` is nullable and ``unknown`` is a legitimate value. Naming a
cause we cannot evidence would be the fabrication this whole roadmap is built
to avoid.

Revision ID: 0132
Revises: 0131
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0132"
down_revision = "0131"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "systemic_flake_cluster",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Stable within a recompute, e.g. "sfc_001".
        sa.Column("cluster_key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        # networking | external_dependency | filesystem | timeout | clock |
        # unknown. NULL/unknown is legitimate — see the module docstring.
        sa.Column("cause_family", sa.String(length=40), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        # Mean silhouette for this cluster. Clusters below the acceptance
        # threshold are never persisted, so this is stored as evidence for the
        # ones that were.
        sa.Column("cohesion", sa.Float(), nullable=True),
        # How many distinct runs the members actually co-failed in — the
        # observation count behind the cluster.
        sa.Column("co_failure_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="60"),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ux_systemic_cluster_project_key",
        "systemic_flake_cluster",
        ["project_id", "cluster_key"],
        unique=True,
    )

    op.create_table(
        "systemic_flake_cluster_member",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("systemic_flake_cluster.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Fingerprint, not test_case_id: membership is about the TEST across
        # runs, not one run's row.
        sa.Column("test_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("test_name", sa.String(length=1000), nullable=True),
        sa.Column("failure_runs", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ux_systemic_member_cluster_fingerprint",
        "systemic_flake_cluster_member",
        ["cluster_id", "test_fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ux_systemic_member_cluster_fingerprint",
        table_name="systemic_flake_cluster_member",
    )
    op.drop_table("systemic_flake_cluster_member")
    op.drop_index("ux_systemic_cluster_project_key", table_name="systemic_flake_cluster")
    op.drop_table("systemic_flake_cluster")
