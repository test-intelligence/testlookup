"""Promote ``ai_eval_runs.item_results`` from JSON to JSONB.

Revision ID: 0084
Revises: 0083
Create Date: 2026-05-16

Database audit 2026-05-16 (docs/DATABASE_AUDIT_2026-05-16.md, finding P3-2)
flagged this column as inconsistent with its sibling
``ai_eval_gate_runs.manifest`` / ``.gate_results`` / ``.blocking_gates`` /
``.version_changes`` — all of which use JSONB. Both tables hold related
eval-run data and the column types should match so future eval-drift
dashboards can use ``@>`` containment predicates and GIN indexes
uniformly across both tables.

Same shape as migration 0083 (``notification_preferences.events``) —
``USING item_results::jsonb`` preserves every existing row intact since
a JSON list parses as a valid JSONB list with no shape change. The
downgrade is symmetric.
"""
from alembic import op


revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_eval_runs "
        "ALTER COLUMN item_results TYPE JSONB USING item_results::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ai_eval_runs "
        "ALTER COLUMN item_results TYPE JSON USING item_results::json"
    )
