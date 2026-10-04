"""enable the shipped Visualization Upgrade flags by default

Revision ID: 0195
Revises: 0194

Phase D of the Visualization Upgrade, step one. Migration 0192 seeded the six
viz flags disabled; the charts behind three of them have shipped and are on
wherever they were shown (the owner's rule: every feature visible). This turns
those three on for every install -- fresh ones and the existing homelab alike:

* ``viz_chart_data_api`` -- the report catalogues (VIZ-E2/E4);
* ``viz_advanced_charts`` -- heatmaps, coverage map, failure groups, scatter,
  Sankey, Explorer (VIZ-E5);
* ``viz_three_d`` -- the opt-in 3D scatter (VIZ-508).

It also clears any project/role allow-list and sets the rollout to 100 %:
``enabled_global`` is only the kill switch in front of those (see
``services/feature_flags.py``), so a flag left allow-listed to one project would
still be off everywhere else.

Deliberately NOT touched (owner decision 2026-10-04: the report-context panel
is removed permanently): ``viz_report_context`` and ``viz_multi_filters`` stay
off, and ``viz_customize`` (read by nothing) stays as it is. Removing the flags
and their seam is the rest of Phase D.

As with 0099, an admin who turned one of these off deliberately has to do it
again once -- called out in the CHANGELOG. The downgrade switches the three
off; allow-lists that existed before cannot be recovered.
"""
from alembic import op
import sqlalchemy as sa

revision = "0195"
down_revision = "0194"
branch_labels = None
depends_on = None

_FLAG_KEYS = ("viz_chart_data_api", "viz_advanced_charts", "viz_three_d")


def upgrade() -> None:
    for key in _FLAG_KEYS:
        op.execute(
            sa.text(
                "UPDATE feature_flags SET enabled_global = true, enabled_projects = NULL, "
                "enabled_roles = NULL, rollout_percent = 100, updated_at = now() "
                "WHERE key = :key"
            ).bindparams(key=key)
        )


def downgrade() -> None:
    for key in _FLAG_KEYS:
        op.execute(
            sa.text(
                "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
                "WHERE key = :key"
            ).bindparams(key=key)
        )
