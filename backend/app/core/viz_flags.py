"""Feature-flag keys for the Visualization Upgrade.

The list of record is ``contracts/viz/flags.json``. Migration 0192 seeds these
six keys disabled (0195 turns the shipped three on: chart-data API, advanced
charts, 3D; the report-context panel's two stay off), and
``tests/test_viz_contracts.py`` holds this module and the migration to that
file -- same keys, same order -- so a flag cannot be renamed on one side only.

0196 (Phase D, F1) deletes the rows of the four nothing reads any more; the
file marks them ``"retired_by": "0196"`` and :data:`VIZ_RETIRED_FLAG_KEYS`
names them. ``frontend/src/config/vizFlags.ts`` mirrors only the live two
(:data:`VIZ_LIVE_FLAG_KEYS`), which go when the report-chrome and multi-filter
code that reads them is removed.
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

#: Deleted by migration 0196: shipped (on since 0195) or never read, and asked
#: by no code since Phase D. In ``flags.json`` order.
VIZ_RETIRED_FLAG_KEYS: tuple[str, ...] = (
    VIZ_CHART_DATA_API,
    VIZ_ADVANCED_CHARTS,
    VIZ_CUSTOMIZE,
    VIZ_THREE_D,
)

#: Rows that still exist after 0196: off for good, read until their code goes.
VIZ_LIVE_FLAG_KEYS: tuple[str, ...] = tuple(
    key for key in VIZ_FLAG_KEYS if key not in VIZ_RETIRED_FLAG_KEYS
)
