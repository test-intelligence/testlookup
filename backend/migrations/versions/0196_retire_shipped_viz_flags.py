"""retire the shipped Visualization Upgrade flag rows

Revision ID: 0196
Revises: 0195

Phase D of the Visualization Upgrade, the last step (F1). Migration 0192 seeded
six viz flags; 0195 turned the shipped three on. Since Phase D's legacy removal
(S1-S6) no code reads four of them, so their rows are deleted:

* ``viz_chart_data_api``, ``viz_advanced_charts``, ``viz_three_d`` -- shipped,
  on everywhere since 0195, asked by nothing since S6 (the seam is deleted);
* ``viz_customize`` -- seeded, never read.

Kept: ``viz_report_context`` and ``viz_multi_filters``. Both are off for good
(owner decision 2026-10-04), but the report-chrome slot and the multi-filter
runtime still read them until that code is removed; their rows go with it.

``contracts/viz/flags.json`` stays the record of what 0192 seeded and marks the
four ``"retired_by": "0196"``; ``app.core.viz_flags.VIZ_RETIRED_FLAG_KEYS`` is
held to those marks and this migration to that tuple.

The downgrade re-inserts the four rows as 0195 left them: the shipped three on
for everyone, ``viz_customize`` off. An admin's later edits to those rows are
not recoverable -- nothing read them.
"""
from alembic import op
import sqlalchemy as sa

revision = "0196"
down_revision = "0195"
branch_labels = None
depends_on = None

#: (key, description, enabled_global) as 0192 seeded them and 0195 left them.
_RETIRED = (
    (
        "viz_chart_data_api",
        "Generic chart-data, heatmap, coverage-map, failure-groups and rows endpoints (VIZ-E2).",
        True,
    ),
    (
        "viz_advanced_charts",
        "Heatmap, coverage map, failure groups, scatter, Sankey and explorer views (VIZ-E5).",
        True,
    ),
    ("viz_customize", "Chart customisation panel and saved-views manager (VIZ-E6).", False),
    ("viz_three_d", "Opt-in 3D scatter view (VIZ-508).", True),
)
_FLAG_KEYS = tuple(key for key, _description, _on in _RETIRED)


def upgrade() -> None:
    for key in _FLAG_KEYS:
        op.execute(sa.text("DELETE FROM feature_flags WHERE key = :key").bindparams(key=key))


def downgrade() -> None:
    for key, description, enabled in _RETIRED:
        op.execute(
            sa.text(
                "INSERT INTO feature_flags "
                "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :key, :description, :enabled, 100, now(), now()) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(key=key, description=description, enabled=enabled)
        )
