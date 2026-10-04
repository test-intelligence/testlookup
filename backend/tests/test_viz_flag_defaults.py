"""
Regression tests for migration 0195 (Visualization Upgrade, Phase D): the three
shipped viz flags are on by default, and the two the owner removed stay off.

The migration is plain data-UPDATE SQL, so its contract is pinned statically
(no live DB needed), as for 0099: exactly which keys it flips, that it clears
the allow-lists ``enabled_global`` sits in front of, and that upgrade() and
downgrade() point in opposite directions.
"""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from app.core.viz_flags import (
    VIZ_ADVANCED_CHARTS,
    VIZ_CHART_DATA_API,
    VIZ_CUSTOMIZE,
    VIZ_FLAG_KEYS,
    VIZ_MULTI_FILTERS,
    VIZ_REPORT_CONTEXT,
    VIZ_THREE_D,
)

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0195_enable_viz_flags_by_default.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0195", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0195_enables_exactly_the_shipped_viz_flags():
    module = _load_migration()
    assert set(module._FLAG_KEYS) == {VIZ_CHART_DATA_API, VIZ_ADVANCED_CHARTS, VIZ_THREE_D}
    assert set(module._FLAG_KEYS) <= set(VIZ_FLAG_KEYS)
    assert module.revision == "0195"
    assert module.down_revision == "0194"


def test_the_removed_report_context_panel_stays_off():
    # Owner decision 2026-10-04: the report-context panel (and its filter bar)
    # is removed permanently. A "turn every viz flag on" edit must not bring it
    # back; viz_customize is read by nothing and is left alone too.
    module = _load_migration()
    for key in (VIZ_REPORT_CONTEXT, VIZ_MULTI_FILTERS, VIZ_CUSTOMIZE):
        assert key not in module._FLAG_KEYS


def test_migration_0195_upgrade_enables_for_everyone_and_downgrade_disables():
    module = _load_migration()
    upgrade_src = inspect.getsource(module.upgrade)
    downgrade_src = inspect.getsource(module.downgrade)
    assert "enabled_global = true" in upgrade_src
    # enabled_global is only the kill switch in front of the allow-lists: a
    # flag left allow-listed to one project would still be off elsewhere.
    assert "enabled_projects = NULL" in upgrade_src
    assert "enabled_roles = NULL" in upgrade_src
    assert "rollout_percent = 100" in upgrade_src
    assert "enabled_global = false" in downgrade_src
    # Guard against the classic copy-paste inversion.
    assert "enabled_global = false" not in upgrade_src
    assert "enabled_global = true" not in downgrade_src
