"""Feature-flag keys for the Visualization Upgrade.

The list of record is ``contracts/viz/flags.json``. Migration 0192 seeds these
six keys disabled (0195 turns the shipped three on: chart-data API, advanced
charts, 3D; the report-context panel's two stay off), and
``tests/test_viz_contracts.py`` holds this module and the migration to that
file -- same keys, same order -- so a flag cannot be renamed on one side only.

Every row is retired now. 0196 (Phase D, F1) deleted the four nothing read
(the shipped three and ``viz_customize``); 0197 (Phase D, M1-M3) deleted the
report-context panel's two once the code reading them was removed. The file
marks each with its ``"retired_by"`` migration, and :data:`VIZ_RETIRED_BY_0196`
/ :data:`VIZ_RETIRED_BY_0197` are held to those marks. No frontend constant
mirrors them any more: nothing reads a viz flag.
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
VIZ_RETIRED_BY_0196: tuple[str, ...] = (
    VIZ_CHART_DATA_API,
    VIZ_ADVANCED_CHARTS,
    VIZ_CUSTOMIZE,
    VIZ_THREE_D,
)

#: Deleted by migration 0197: off for good since 2026-10-04, and read by no
#: code once the report chrome and the multi-filter runtime were removed.
VIZ_RETIRED_BY_0197: tuple[str, ...] = (
    VIZ_REPORT_CONTEXT,
    VIZ_MULTI_FILTERS,
)

#: Every retired key, in ``flags.json`` order: all six.
VIZ_RETIRED_FLAG_KEYS: tuple[str, ...] = tuple(
    key for key in VIZ_FLAG_KEYS if key in VIZ_RETIRED_BY_0196 + VIZ_RETIRED_BY_0197
)

#: Rows that still exist: none.
VIZ_LIVE_FLAG_KEYS: tuple[str, ...] = tuple(
    key for key in VIZ_FLAG_KEYS if key not in VIZ_RETIRED_FLAG_KEYS
)
