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
import json
from pathlib import Path

from app.core.viz_flags import (
    VIZ_ADVANCED_CHARTS,
    VIZ_CHART_DATA_API,
    VIZ_CUSTOMIZE,
    VIZ_FLAG_KEYS,
    VIZ_LIVE_FLAG_KEYS,
    VIZ_MULTI_FILTERS,
    VIZ_REPORT_CONTEXT,
    VIZ_RETIRED_FLAG_KEYS,
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


# -- 0196 (Phase D, F1): the rows nothing reads are retired ----------------------------------

MIGRATION_0196 = MIGRATION.parent / "0196_retire_shipped_viz_flags.py"
CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "viz" / "flags.json"


def _load_0196():
    spec = importlib.util.spec_from_file_location("migration_0196", MIGRATION_0196)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(monkeypatch, step: str) -> list:
    """One 0196 step with ``op.execute`` captured: the statements it would send."""
    from alembic import op

    sent: list = []
    monkeypatch.setattr(op, "execute", sent.append, raising=False)
    getattr(_load_0196(), step)()
    return sent


def _contract_flags() -> list[dict]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))["flags"]


def test_migration_0196_follows_0195_alone():
    module = _load_0196()
    assert (module.revision, module.down_revision) == ("0196", "0195")
    children = [
        path.name
        for path in MIGRATION.parent.glob("*.py")
        if 'down_revision = "0195"' in path.read_text(encoding="utf-8")
    ]
    assert children == [MIGRATION_0196.name], f"0195 has more than one child: {children}"


def test_0196_retires_exactly_the_keys_flags_json_marks():
    marked = [flag["key"] for flag in _contract_flags() if flag.get("retired_by") == "0196"]
    assert list(VIZ_RETIRED_FLAG_KEYS) == marked
    assert _load_0196()._FLAG_KEYS == VIZ_RETIRED_FLAG_KEYS
    # The removed panel's two are still read (report chrome, multi-filter
    # runtime): their rows go with that code, not here.
    assert set(VIZ_LIVE_FLAG_KEYS) == {VIZ_REPORT_CONTEXT, VIZ_MULTI_FILTERS}
    assert set(VIZ_LIVE_FLAG_KEYS) | set(VIZ_RETIRED_FLAG_KEYS) == set(VIZ_FLAG_KEYS)


def test_0196_upgrade_deletes_only_those_rows(monkeypatch):
    sent = _statements(monkeypatch, "upgrade")
    assert {" ".join(s.text.split()) for s in sent} == {"DELETE FROM feature_flags WHERE key = :key"}
    assert [s.compile().params["key"] for s in sent] == list(VIZ_RETIRED_FLAG_KEYS)


def test_0196_downgrade_restores_the_0195_state(monkeypatch):
    descriptions = {flag["key"]: flag["description"] for flag in _contract_flags()}
    restored = {}
    for statement in _statements(monkeypatch, "downgrade"):
        sql = " ".join(statement.text.split())
        assert sql.startswith("INSERT INTO feature_flags ") and sql.endswith("ON CONFLICT (key) DO NOTHING"), sql
        assert ", 100, now(), now())" in sql  # rollout_percent 100, as 0192 / 0195
        params = statement.compile().params
        assert params["description"] == descriptions[params["key"]]  # 0192's words
        restored[params["key"]] = params["enabled"]
    # As 0195 left them: the shipped three on for everyone, viz_customize off.
    assert restored == {
        VIZ_CHART_DATA_API: True,
        VIZ_ADVANCED_CHARTS: True,
        VIZ_CUSTOMIZE: False,
        VIZ_THREE_D: True,
    }
