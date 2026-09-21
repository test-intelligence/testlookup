"""Feature-flag keys for the Visualization Upgrade.

The list of record is ``contracts/viz/flags.json``. Migration 0192 seeds these
six keys disabled, ``frontend/src/config/vizFlags.ts`` mirrors them, and
``tests/test_viz_contracts.py`` holds this module and the migration to that
file -- same keys, same order -- so a flag cannot be renamed on one side only.
"""

from __future__ import annotations

VIZ_REPORT_CONTEXT = "viz_report_context"
VIZ_MULTI_FILTERS = "viz_multi_filters"
VIZ_CHART_DATA_API = "viz_chart_data_api"
VIZ_ADVANCED_CHARTS = "viz_advanced_charts"
VIZ_CUSTOMIZE = "viz_customize"
VIZ_THREE_D = "viz_three_d"

VIZ_FLAG_KEYS: tuple[str, ...] = (
    VIZ_REPORT_CONTEXT,
    VIZ_MULTI_FILTERS,
    VIZ_CHART_DATA_API,
    VIZ_ADVANCED_CHARTS,
    VIZ_CUSTOMIZE,
    VIZ_THREE_D,
)
