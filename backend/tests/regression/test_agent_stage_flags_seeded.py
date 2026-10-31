"""The deep pipeline's stage flags have rows, and every place that names them agrees (AIC-07, AGT-16).

Before W04 no migration created the six stage-flag rows the planner evaluates
(``contract_validation``, ``log_intelligence``, ``regression_watchman``,
``change_ownership``, ``defect_commander``, ``async_decision_report_supersession``),
so Settings -> Feature flags did not list them and an admin could not switch a
stage on; ``AIQ_GAP_REFINEMENT_ENABLED`` was an environment switch nobody set.
Migrations 0208 / 0209 seed them. This file reads the REAL migration files and
holds them to:

* the keys the code evaluates (a typo'd seed would be a row that gates nothing,
  while the real stage stays off with no row);
* the legacy env fallbacks (``feature_flags.LEGACY_ENV_VAR_MAP``), so a row
  seeded OFF cannot silently switch off a stage an operator enabled by env;
* owner decision D20 (``defect_commander``, the one mutating stage, is never
  seeded ON) and the fresh-install rule for the four read-only stages;
* the operator catalogue (``docs/operations/feature-flags.md``), which must
  state each row's seed and default.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO = Path(__file__).resolve().parents[3]
VERSIONS = REPO / "backend" / "migrations" / "versions"
APP = REPO / "backend" / "app"
CATALOGUE = REPO / "docs" / "operations" / "feature-flags.md"
GROUPS_TS = REPO / "frontend" / "src" / "config" / "featureFlagGroups.ts"

READ_ONLY = ("contract_validation", "log_intelligence", "regression_watchman", "change_ownership")


def _migration(name: str):
    spec = importlib.util.spec_from_file_location(f"m_{name[:4]}_seeded", VERSIONS / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _m0208():
    return _migration("0208_seed_agent_stage_flags.py")


def _m0209():
    return _migration("0209_seed_aiq_gap_refinement_flag.py")


def _seeded() -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for key, description, fresh_on, legacy in _m0208()._FLAGS:
        rows[key] = {"migration": "0208", "description": description, "fresh_on": fresh_on, "legacy": legacy}
    for key, description, legacy in _m0209()._FLAGS:
        rows[key] = {"migration": "0209", "description": description, "fresh_on": True, "legacy": legacy}
    return rows


def _code_reads(key: str) -> bool:
    pattern = re.compile(r"\bis_enabled\(\s*[\"']" + re.escape(key) + r"[\"']")
    return any(pattern.search(path.read_text(encoding="utf-8", errors="ignore")) for path in APP.rglob("*.py"))


def test_the_seven_stage_flags_are_seeded():
    assert set(_seeded()) == {*READ_ONLY, "defect_commander", "async_decision_report_supersession", "aiq_gap_refinement"}


@pytest.mark.parametrize("key", sorted({*READ_ONLY, "defect_commander", "async_decision_report_supersession", "aiq_gap_refinement"}))
def test_every_seeded_key_is_a_flag_the_code_evaluates(key):
    assert _code_reads(key), f"{key} is seeded but nothing calls is_enabled({key!r}): a row that gates nothing"


def test_the_legacy_env_columns_match_the_feature_flags_map():
    from app.services.feature_flags import LEGACY_ENV_VAR_MAP

    for key, row in _seeded().items():
        assert row["legacy"] == LEGACY_ENV_VAR_MAP.get(key), (
            f"{key}: the migration reads {row['legacy']!r} but the runtime fallback is "
            f"{LEGACY_ENV_VAR_MAP.get(key)!r}"
        )


def test_defaults_follow_d20_and_the_fresh_install_rule():
    rows = _seeded()
    for key in READ_ONLY:
        assert rows[key]["fresh_on"] is True, key
    assert rows["defect_commander"]["fresh_on"] is False
    assert rows["defect_commander"]["legacy"] is None, "D20: nothing may seed the mutating stage ON"
    assert rows["async_decision_report_supersession"]["fresh_on"] is False
    # 0209 needs no fresh-install predicate: ON for every install.
    assert "users" not in _m0209().INSERT_SQL
    assert _m0208().FRESH_INSTALL in _m0208().INSERT_SQL


def test_the_mutating_stage_says_so_in_its_description():
    rows = _seeded()
    assert rows["defect_commander"]["description"].startswith("MUTATING ")
    for key in READ_ONLY:
        assert "read-only" in rows[key]["description"], key
    for row in rows.values():
        assert row["description"].isascii()


def _catalogue_row(text: str, key: str) -> str:
    """The key's row in the five-column catalogue table (Flag | Seeded by |
    Default | Fallback | What it gates), not its row in the fallback table."""
    for line in text.splitlines():
        if line.startswith(f"| `{key}` |") and len(line.strip("|").split("|")) >= 5:
            return line
    raise AssertionError(f"docs/operations/feature-flags.md has no catalogue row for `{key}`")


def test_the_catalogue_states_each_rows_seed_and_default():
    text = CATALOGUE.read_text(encoding="utf-8")
    for key, row in _seeded().items():
        line = _catalogue_row(text, key)
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert cells[1] == row["migration"], f"`{key}`: seeded-by column says {cells[1]!r}"
        default = cells[2].lower()
        if key in READ_ONLY:
            assert "on" in default and "new install" in default and "off" in default, (key, default)
        elif key == "aiq_gap_refinement":
            assert "every install" in default, default
        else:
            assert default.startswith("**off**"), (key, default)
        expected_fallback = f"`{row['legacy']}`" if row["legacy"] else "none"
        assert cells[3] == expected_fallback, (key, cells[3])
    assert "None is seeded" not in text, "the stage flags are seeded now"


def test_the_fallback_table_lists_the_new_env_fallback():
    text = CATALOGUE.read_text(encoding="utf-8")
    assert "| `aiq_gap_refinement` | `AIQ_GAP_REFINEMENT_ENABLED` |" in text


def test_the_settings_page_groups_every_seeded_stage_flag():
    """G-E's ``featureFlagGroups.ts`` lists the group the page renders; a seeded
    stage flag missing from it would sit ungrouped among unrelated flags."""
    if not GROUPS_TS.is_file():
        pytest.skip("frontend/src/config/featureFlagGroups.ts not present yet (G-E)")
    source = GROUPS_TS.read_text(encoding="utf-8")
    for key in _seeded():
        assert f"'{key}'" in source or f'"{key}"' in source, key
