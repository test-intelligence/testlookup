"""Duplicate authored-test-case detection (Phase 4 of granular test detail).

Revision ID: 0094
Revises: 0093
Create Date: 2026-06-10

Tiered, offline-first, per-project duplicate detection over the AUTHORED
``managed_test_cases`` (NOT the execution ``test_cases``). Adds:

- ``managed_test_cases.dup_fingerprint`` — a normalised content hash (Tier 0
  exact-match blocking key); nullable + indexed for fast equality bucketing.
- ``duplicate_test_case_candidates`` — one row per detected near-duplicate PAIR,
  canonically ordered ``case_a_id < case_b_id`` so a pair is recorded once and
  idempotent on ``(project_id, case_a_id, case_b_id)``. Carries the explainable
  payload: ``band`` (exact|strong|possible), ``score``, human-readable
  ``reason``, ``method`` (fingerprint|structural|semantic), and per-component
  ``component_scores`` (JSONB). ``status`` drives the review queue
  (open|merged|dismissed). MERGE IS NON-DESTRUCTIVE this phase — resolving a
  pair only flips ``status``; it never deletes cases or redirects fingerprints.
- ``dismissed_duplicate_pairs`` — suppression list so a dismissed pair stays
  dismissed across re-detection runs (same canonical ordering + uniqueness).

Canonical ordering is enforced at the DB level with a CHECK
(``case_a_id < case_b_id``) on both pair tables so a swapped-order duplicate of
the same logical pair can never be inserted.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── managed_test_cases.dup_fingerprint ───────────────────────
    # Tier 0 exact-match blocking key: a normalised content hash. Nullable
    # (historical rows + create paths populate it lazily) + indexed so the
    # detector can bucket exact matches with a single equality scan.
    op.add_column(
        "managed_test_cases",
        sa.Column("dup_fingerprint", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_mtc_dup_fingerprint",
        "managed_test_cases",
        ["dup_fingerprint"],
    )

    # ── duplicate_test_case_candidates ───────────────────────────
    op.create_table(
        "duplicate_test_case_candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_a_id",
            UUID(as_uuid=True),
            sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_b_id",
            UUID(as_uuid=True),
            sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("band", sa.String(20), nullable=False),       # exact | strong | possible
        sa.Column("score", sa.Float(), nullable=False),         # 0.0 - 1.0
        sa.Column("reason", sa.Text(), nullable=True),          # human-readable explanation
        sa.Column("method", sa.String(20), nullable=False),     # fingerprint | structural | semantic
        sa.Column("component_scores", JSONB(), nullable=True),  # {title, steps, jaccard, semantic, ...}
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'open'")),  # open | merged | dismissed
        # Canonical ordering guarantee — a pair is recorded exactly once.
        sa.UniqueConstraint("project_id", "case_a_id", "case_b_id", name="uq_dup_candidate_pair"),
        sa.CheckConstraint("case_a_id < case_b_id", name="ck_dup_candidate_canonical_order"),
    )
    op.create_index(
        "ix_dup_candidate_project_status",
        "duplicate_test_case_candidates",
        ["project_id", "status"],
    )

    # ── dismissed_duplicate_pairs ────────────────────────────────
    # Suppression list. A dismissed pair must stay dismissed across re-detection
    # runs, so the detector consults this table before staging a candidate.
    op.create_table(
        "dismissed_duplicate_pairs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_a_id",
            UUID(as_uuid=True),
            sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_b_id",
            UUID(as_uuid=True),
            sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dismissed_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "case_a_id", "case_b_id", name="uq_dismissed_dup_pair"),
        sa.CheckConstraint("case_a_id < case_b_id", name="ck_dismissed_dup_canonical_order"),
    )
    op.create_index(
        "ix_dismissed_dup_project",
        "dismissed_duplicate_pairs",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dismissed_dup_project", table_name="dismissed_duplicate_pairs")
    op.drop_table("dismissed_duplicate_pairs")

    op.drop_index("ix_dup_candidate_project_status", table_name="duplicate_test_case_candidates")
    op.drop_table("duplicate_test_case_candidates")

    op.drop_index("ix_mtc_dup_fingerprint", table_name="managed_test_cases")
    op.drop_column("managed_test_cases", "dup_fingerprint")
