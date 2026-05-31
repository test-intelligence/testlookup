"""Enforce one ``test_cases`` row per (test_run_id, test_fingerprint).

Revision ID: 0089
Revises: 0088
Create Date: 2026-05-30

The file-ingestion path (``services/ingestion._upsert_test_case``) has always
treated ``(test_run_id, test_fingerprint)`` as the idempotency key — it
SELECTs the existing row and updates it in place. The live-stream bulk insert
in ``worker/tasks.persist_live_session`` did NOT: it issued a plain
``INSERT`` with no conflict handling, and there was no unique constraint to
fall back on (only the non-unique ``ix_test_cases_fingerprint`` index). A
task retry after a partial commit (the Redis event buffer is deleted only
*after* finalize_run) therefore re-inserted the same rows, producing
duplicate per-test rows that inflate counts.

This migration:
  1. De-duplicates any existing rows, keeping the most recently created row
     per ``(test_run_id, test_fingerprint)`` (ties broken by ``id``). The
     constraint can't be created while duplicates exist.
  2. Adds the unique constraint ``uq_test_cases_run_fingerprint`` so both
     ingestion paths share the same idempotency contract and
     ``on_conflict_do_nothing`` in the worker has an arbiter to target.

The non-unique single-column ``ix_test_cases_fingerprint`` index is retained
— cross-run lookups by fingerprint (flakiness history, canonical linking)
still use it.
"""
from alembic import op


revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Collapse pre-existing duplicates. Keep the newest row per
    #    (test_run_id, test_fingerprint); delete the rest. ``created_at``
    #    may be NULL on very old rows, so fall back to ``id`` ordering.
    op.execute(
        """
        DELETE FROM test_cases tc
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY test_run_id, test_fingerprint
                       ORDER BY created_at DESC NULLS LAST, id DESC
                   ) AS rn
            FROM test_cases
        ) dupes
        WHERE tc.id = dupes.id
          AND dupes.rn > 1
        """
    )

    # 2. Now the column pair is unique — enforce it.
    op.create_unique_constraint(
        "uq_test_cases_run_fingerprint",
        "test_cases",
        ["test_run_id", "test_fingerprint"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_test_cases_run_fingerprint",
        "test_cases",
        type_="unique",
    )
