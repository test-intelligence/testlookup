"""retire the last Visualization Upgrade flag rows

Revision ID: 0197
Revises: 0196

Phase D of the Visualization Upgrade, M1-M3. ``viz_report_context`` (the
report-context panel) and ``viz_multi_filters`` (the multi-select release and
suite filters) were turned off for good on 2026-10-04 (owner decision), and
the code that read them -- the report chrome, the multi-filter runtime, its
stores and URL sync -- is now deleted, so their rows go too. With 0196 this
retires all six rows 0192 seeded; no viz flag is left.

``contracts/viz/flags.json`` stays the record of 0192's seed and marks these
two ``"retired_by": "0197"``; ``app.core.viz_flags.VIZ_RETIRED_BY_0197`` is
held to those marks and this migration to that tuple.

The downgrade re-inserts both as 0196 left them: off, rollout 100.
"""
from alembic import op
import sqlalchemy as sa

revision = "0197"
down_revision = "0196"
branch_labels = None
depends_on = None

#: (key, description) as 0192 seeded them; both were off when retired.
_RETIRED = (
    ("viz_report_context", "Report context header and metrics strip on report pages (VIZ-E3)."),
    ("viz_multi_filters", "Multi-select release and suite filters, chips, summary and URL sync (VIZ-E3)."),
)
_FLAG_KEYS = tuple(key for key, _description in _RETIRED)


def upgrade() -> None:
    for key in _FLAG_KEYS:
        op.execute(sa.text("DELETE FROM feature_flags WHERE key = :key").bindparams(key=key))


def downgrade() -> None:
    for key, description in _RETIRED:
        op.execute(
            sa.text(
                "INSERT INTO feature_flags "
                "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :key, :description, false, 100, now(), now()) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(key=key, description=description)
        )
