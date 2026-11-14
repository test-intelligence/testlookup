"""A flag that a migration turns ON for a new install is not documented as off.

Migration 0234 switched ``github_checks`` and ``gitlab`` on for a new install
(no users and no projects yet), by the rule 0201 introduced for
``manual_upload``. The operator catalogue ``docs/operations/feature-flags.md``
still listed both as **off**, as did the integration page, the operator guide
and the in-app guide. An operator reading them could not tell why a fresh
install posted GitHub check runs it was told were disabled.

``test_feature_flag_catalogue_is_complete.py`` proves every flag has a row; it
does not read the row's default. This test does, for the one rule the
catalogue cannot get right by copying the seeding migration: a later
"fresh install" migration flipping an earlier seed.

Every migration that uses the fresh-install predicate must appear in
``FRESH_INSTALL_ON`` below with the keys it turns on, so a new one fails here
until its author records it (and, through the assertion, documents it).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOGUE = REPO_ROOT / "docs" / "operations" / "feature-flags.md"
MIGRATIONS = REPO_ROOT / "backend" / "migrations" / "versions"

#: The predicate every "on for a new install" migration uses (0201 pattern).
FRESH_INSTALL_PREDICATE = (
    "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
)

#: Migration revision -> flag keys it turns ON for a new install. Read from each
#: migration; 0208 also seeds ``defect_commander`` and
#: ``async_decision_report_supersession`` with the predicate but with
#: ``fresh_on=False``, so they stay off and are not listed.
FRESH_INSTALL_ON: dict[str, tuple[str, ...]] = {
    "0201": ("manual_upload",),
    "0204": ("perf_regression_detection",),
    "0207": ("llm_cost_budget",),
    "0208": (
        "contract_validation",
        "log_intelligence",
        "regression_watchman",
        "change_ownership",
    ),
    "0212": ("release_phase_gate_enforcement", "release_compliance_pack"),
    "0224": ("outbound_webhooks",),
    "0229": ("test_case_requirement_traceability",),
    "0234": ("github_checks", "gitlab"),
}


def _migrations_using_the_predicate() -> set[str]:
    found = set()
    for path in sorted(MIGRATIONS.glob("*.py")):
        if FRESH_INSTALL_PREDICATE in path.read_text(encoding="utf-8"):
            found.add(path.name.split("_", 1)[0])
    return found


def _catalogue_default_cells() -> dict[str, str]:
    """``key -> Default cell`` for every row of the flag catalogue tables.

    Only rows after the ``## Flag catalogue`` heading count: the
    environment-fallback table earlier on the page lists some keys in a
    two-column table that has no Default column.
    """
    text = CATALOGUE.read_text(encoding="utf-8")
    _, sep, catalogue = text.partition("\n## Flag catalogue")
    assert sep, "docs/operations/feature-flags.md lost its '## Flag catalogue' section"
    cells: dict[str, str] = {}
    for line in catalogue.splitlines():
        match = re.match(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|", line)
        if not match:
            continue
        columns = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(columns) >= 3:
            cells.setdefault(match.group(1), columns[2])
    return cells


def test_every_fresh_install_migration_is_recorded() -> None:
    """A new fresh-install migration fails here until FRESH_INSTALL_ON names it."""
    unrecorded = _migrations_using_the_predicate() - set(FRESH_INSTALL_ON)
    assert not unrecorded, (
        "these migrations use the fresh-install predicate but are not in FRESH_INSTALL_ON; "
        f"record the flag keys each one turns on: {sorted(unrecorded)}"
    )
    stale = set(FRESH_INSTALL_ON) - _migrations_using_the_predicate()
    assert not stale, (
        f"FRESH_INSTALL_ON names migrations that no longer use the predicate: {sorted(stale)}"
    )


def test_the_recorded_keys_appear_in_their_migration() -> None:
    """Guards the table itself: each key it lists is written in that migration."""
    for revision, keys in FRESH_INSTALL_ON.items():
        (path,) = list(MIGRATIONS.glob(f"{revision}_*.py"))
        source = path.read_text(encoding="utf-8")
        missing = [
            key for key in keys if f"'{key}'" not in source and f'"{key}"' not in source
        ]
        assert not missing, f"{path.name} does not mention {missing}"


def test_flags_turned_on_for_new_installs_are_documented_as_on() -> None:
    cells = _catalogue_default_cells()
    wrong = {}
    for revision, keys in FRESH_INSTALL_ON.items():
        for key in keys:
            default = cells.get(key)
            assert default is not None, (
                f"docs/operations/feature-flags.md has no catalogue row for `{key}`"
            )
            if "new install" not in default.lower():
                wrong[key] = f"{revision}: Default reads {default!r}"
    assert not wrong, (
        "docs/operations/feature-flags.md gives these flags a default that does not say they are on "
        f"for a new install: {wrong}"
    )
