"""Seed the six Visualization Upgrade rollout flags, disabled.

Every viz surface ships behind one of these, so the rows exist before the first
surface lands. Seeded DISABLED, like ``release_compliance_pack`` (0066) and
``release_phase_gate_enforcement`` (0158) -- a deployment that does nothing sees
zero behaviour change. ``rollout_percent`` is 100 like theirs, so turning a viz
flag on is one edit: the ``enabled_global`` switch.

The keys and descriptions are ``contracts/viz/flags.json``, restated here
because a migration must not read repo files at run time. ``app.core.viz_flags``
and ``frontend/src/config/vizFlags.ts`` carry the same keys, and
``tests/test_viz_contracts.py`` holds all of them to that file.

Revision ID: 0192
Revises: 0191
"""

from alembic import op
import sqlalchemy as sa


revision = "0192"
down_revision = "0191"
branch_labels = None
depends_on = None

_FLAGS = (
    (
        "viz_report_context",
        "Report context header and metrics strip on report pages (VIZ-E3).",
    ),
    (
        "viz_multi_filters",
        "Multi-select release and suite filters, chips, summary and URL sync (VIZ-E3).",
    ),
    (
        "viz_chart_data_api",
        "Generic chart-data, heatmap, coverage-map, failure-groups and rows endpoints (VIZ-E2).",
    ),
    (
        "viz_advanced_charts",
        "Heatmap, coverage map, failure groups, scatter, Sankey and explorer views (VIZ-E5).",
    ),
    (
        "viz_customize",
        "Chart customisation panel and saved-views manager (VIZ-E6).",
    ),
    (
        "viz_three_d",
        "Opt-in 3D scatter view (VIZ-508).",
    ),
)


def upgrade() -> None:
    for key, description in _FLAGS:
        op.execute(
            sa.text(
                "INSERT INTO feature_flags "
                "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :key, :description, false, 100, now(), now()) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(key=key, description=description)
        )


def downgrade() -> None:
    for key, _description in _FLAGS:
        op.execute(
            sa.text("DELETE FROM feature_flags WHERE key = :key").bindparams(key=key)
        )
