"""W24 ADM-05: every table is classified for the project export, against the REAL metadata.

``services/project_export_entities.py`` is the review point for every future
project-scoped table: a new table that is not classified there — exported (with
its rules), excluded (with a reason) or not project-scoped — fails here naming
it. Pure: walks ``app.models.postgres.Base.metadata``, no database.

Guards (contract §2.1): (1) disjoint + complete; (2) a project-scoped table is
never NOT_PROJECT_SCOPED; (3) every exclusion reason is in the vocabulary;
(4) the ``test_runs`` cascade closure is excluded as ``run_history``; (5) every
reset-map table is classified; (6) the plan is topologically sorted and every
NOT NULL project FK of an exported row can be satisfied; (7) every named column
exists, no user id and no excluded-table id is copied raw, and no new NOT NULL
column without a default silently breaks older archives; (8) the pinned counts;
(9) fail-open.
"""
from __future__ import annotations

from sqlalchemy import LargeBinary

from app.models.postgres import Base
from app.services import project_export_entities as e
from app.services.project_export_vocab import EXCLUSION_REASONS
from app.services.project_reset_service import _FULL_RESET_TABLES

TABLES = Base.metadata.tables
_FIX = "classify it in project_export_entities.py (exported / excluded with a reason / not project-scoped)"

# Guard (7) schema-evolution pin: the NOT NULL exported columns WITHOUT a server
# or Python default that schema_version 1 archives always carry. An older archive
# lacks any column added later, so a NEW NOT NULL column without a default on an
# exported table would make it unimportable: give the column a server default,
# or bump SCHEMA_VERSION and extend this pin deliberately.
SCHEMA_V1_REQUIRED_COLUMNS = frozenset({
    "test_suites.name", "managed_test_cases.title",
    "test_case_versions.version", "test_case_versions.title", "test_case_versions.status",
    "test_case_requirement_links.requirement_key", "test_case_requirement_links.requirement_key_norm",
    "canonical_test_cases.test_fingerprint", "canonical_test_cases.test_name",
    "test_plans.name", "test_strategies.name", "releases.name", "release_phases.name",
    "release_gate_policies.version", "release_gate_policies.name", "release_gate_policies.rules",
    "release_attribution_rules.name", "release_attribution_rules.match_field",
    "release_attribution_rules.match_pattern", "release_attribution_rules.target_release_name",
    "quality_gates.name",
    "team_notification_channels.team_name", "team_notification_channels.channel_type",
    "team_notification_channels.target",
    "webhook_subscriptions.name", "webhook_subscriptions.target_url",
    "github_integrations.repo_owner", "github_integrations.repo_name",
    "service_ownership_rules.match_type", "service_ownership_rules.match_pattern",
    "service_ownership_rules.service_name", "service_ownership_rules.team_name",
    "workflow_definitions.workflow_id", "workflow_definitions.version", "workflow_definitions.name",
    "workflow_definitions.base", "workflow_definitions.definition",
    "project_workflow_defaults.base", "project_workflow_defaults.workflow_id",
    "agent_configs.agent_id", "run_cadences.mode", "run_cadences.timezone",
    "classification_hints.match_field", "classification_hints.pattern", "classification_hints.category",
})


def _fk_columns(table_name: str) -> list[tuple[str, str]]:
    return [(c.name, fk.column.table.name) for c in TABLES[table_name].columns for fk in c.foreign_keys]


# ── (1) disjoint and complete ────────────────────────────────────────────────

def test_1_every_table_is_classified_exactly_once():
    exported = list(e.EXPORTED_TABLES)
    assert len(exported) == len(set(exported)), "a table appears twice in EXPORTED"
    groups = {"EXPORTED": set(exported), "EXCLUDED": set(e.EXCLUDED), "NOT_PROJECT_SCOPED": set(e.NOT_PROJECT_SCOPED)}
    names = list(groups)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = sorted(groups[a] & groups[b])
            assert not both, f"{both} classified in both {a} and {b} — keep ONE class in project_export_entities.py"
    classified = set().union(*groups.values())
    unclassified = sorted(set(TABLES) - classified)
    assert not unclassified, "\n".join(f"{t}: {_FIX}" for t in unclassified)
    unknown = sorted(classified - set(TABLES))
    assert not unknown, f"{unknown} are classified in project_export_entities.py but are not tables of Base.metadata"


# ── (2) a project-scoped table cannot hide as NOT_PROJECT_SCOPED ───────────────

def test_2_project_scoped_tables_are_exported_or_excluded():
    scoped = e.project_scoped_tables(Base.metadata)
    hidden = sorted(scoped & set(e.NOT_PROJECT_SCOPED))
    assert not hidden, (
        f"{hidden} reach a project through project_id / an FK, so they are project-scoped: "
        "export them or exclude them with a reason in project_export_entities.py"
    )
    assert scoped <= set(e.EXPORTED_TABLES) | set(e.EXCLUDED)
    assert "projects" not in scoped and "projects" in e.EXPORTED_TABLES


def test_2_the_no_fk_project_keyed_tables_are_named():
    # contract §0.7: keyed by project id with NO FK — named explicitly so "no FK" is not a hole
    for table in ("integration_health_checks", "integration_probe_results", "settings_audit_log", "secret_refs"):
        assert table in e.NOT_PROJECT_SCOPED, table


# ── (3) every exclusion reason is in the vocabulary ──────────────────────────

def test_3_every_exclusion_reason_is_in_the_vocabulary():
    bad = {t: r for t, r in e.EXCLUDED.items() if r not in EXCLUSION_REASONS}
    assert not bad, f"unknown exclusion reasons {bad}; valid: {EXCLUSION_REASONS}"
    assert all(why.strip() for why in e.NOT_PROJECT_SCOPED.values())


# ── (4) run history is phase 2 ───────────────────────────────────────────────

def test_4_the_test_runs_cascade_closure_is_excluded_as_run_history():
    closure = e.run_history_tables(Base.metadata)
    wrong = sorted(t for t in closure if e.EXCLUDED.get(t) != "run_history")
    assert not wrong, (
        f"{wrong} cascade from test_runs, so they are run history (phase 2): "
        "exclude them as 'run_history' in project_export_entities.py"
    )
    assert not closure & set(e.EXPORTED_TABLES)


# ── (5) the reset map stays the source the backlog named ─────────────────────

def test_5_every_reset_map_table_is_exported_or_excluded():
    for name, _model in _FULL_RESET_TABLES:
        assert name in e.EXPORTED_TABLES or name in e.EXCLUDED, f"reset-map table {name}: {_FIX}"


# ── (6) the plan can be inserted in order ────────────────────────────────────

def test_6_plan_is_topologically_sorted():
    assert [s.table for s in e.plan()] == list(e.EXPORTED_TABLES)
    assert e.plan()[0].table == "projects" and e.plan()[0].scope == e.SCOPE_ROOT
    assert e.order_problems() == []
    # every fks / parent edge points at an EXPORTED table that precedes the child
    position = {t: i for i, t in enumerate(e.EXPORTED_TABLES)}
    for i, spec in enumerate(e.plan()):
        parents = dict(spec.fks)
        if spec.parent is not None:
            table, column = spec.parent
            assert spec.fks.get(column) == table, f"{spec.table}: scope {spec.scope} needs fks[{column!r}] = {table!r}"
        for column, parent in parents.items():
            assert parent in position, f"{spec.table}.{column} → {parent}, which is not EXPORTED"
            assert position[parent] < i, f"{spec.table} is planned before its parent {parent}"


def test_6_order_problems_names_a_child_planned_before_its_parent():
    swapped = (e.EXPORTED[0], e.spec_for("test_plan_items"), e.spec_for("test_plans"))
    assert any("test_plan_items" in p and "test_plans" in p for p in e.order_problems(swapped))


def test_6_every_not_null_project_fk_resolves_to_an_exported_row():
    scoped = e.project_scoped_tables(Base.metadata)
    for spec in e.EXPORTED:
        table = TABLES[spec.table]
        for column, ref in _fk_columns(spec.table):
            if ref not in scoped or column in spec.drop or table.c[column].nullable:
                continue
            assert ref in e.EXPORTED_TABLES, (
                f"{spec.table}.{column} is NOT NULL and points at {ref}, which is not exported: "
                "export the parent or drop the column in project_export_entities.py"
            )


def test_6_scope_matches_the_table_shape():
    for spec in e.EXPORTED:
        cols = TABLES[spec.table].c
        if spec.scope == e.SCOPE_PROJECT:
            assert "project_id" in cols, f"{spec.table} has no project_id: give it a parent: scope"
        elif spec.parent is not None:
            assert "project_id" not in cols, f"{spec.table} has project_id: select it by project_column"
            assert spec.parent[0] in e.EXPORTED_TABLES
        else:
            assert spec.scope == e.SCOPE_ROOT and spec.table == "projects", spec.table


# ── (7) columns exist; no raw user / excluded id travels; schema evolution ───

def test_7_every_named_column_exists_on_its_table():
    for spec in e.EXPORTED:
        cols = set(TABLES[spec.table].c.keys())
        named = {
            "drop": spec.drop, "user_refs": spec.user_refs, "cleared": spec.cleared, "fks": tuple(spec.fks),
            "self_fks": spec.self_fks, "inert": tuple(spec.inert), "natural_key": spec.natural_key,
        }
        for kind, columns in named.items():
            missing = sorted(set(columns) - cols)
            assert not missing, f"{spec.table}.{kind} names {missing}, which are not columns of {spec.table}"
        assert len(set(spec.drop) & (set(spec.user_refs) | set(spec.cleared) | set(spec.fks) | set(spec.inert))) == 0, (
            f"{spec.table}: a dropped column cannot also be remapped / cleared / forced"
        )


def test_7_no_user_id_is_copied_raw():
    for spec in e.EXPORTED:
        covered = set(spec.user_refs) | set(spec.cleared) | set(spec.inert) | set(spec.drop)
        for column, ref in _fk_columns(spec.table):
            if ref == "users":
                assert column in covered, (
                    f"{spec.table}.{column} references users: put it in user_refs, cleared, inert or drop "
                    "in project_export_entities.py (no user id is ever copied raw)"
                )
        for column in spec.user_refs + spec.cleared:
            assert (column, "users") in _fk_columns(spec.table), f"{spec.table}.{column} is not a users FK"


def test_7_every_fk_column_is_accounted_for():
    for spec in e.EXPORTED:
        for column, ref in _fk_columns(spec.table):
            if ref == "users" or column in spec.drop:
                continue
            if ref in e.EXCLUDED or ref in e.NOT_PROJECT_SCOPED:
                raise AssertionError(
                    f"{spec.table}.{column} points at {ref}, which is not exported: drop it in "
                    "project_export_entities.py (no dangling source id)"
                )
            if ref == "projects":
                assert column == "project_id" or spec.table == "projects", f"{spec.table}.{column} → projects"
            elif ref == spec.table:
                assert column in spec.self_fks, f"{spec.table}.{column} is a same-table ref: add it to self_fks"
            else:
                assert spec.fks.get(column) == ref, f"{spec.table}.{column} → {ref}: add it to fks (remapped)"


def test_7_a_new_not_null_column_without_a_default_is_a_deliberate_decision():
    found = set()
    for spec in e.EXPORTED:
        filled = set(spec.fks) | set(spec.self_fks) | set(spec.user_refs) | set(spec.cleared) | set(spec.inert)
        for c in TABLES[spec.table].columns:
            if c.name in spec.drop or c.nullable or c.server_default is not None or c.default is not None:
                continue
            if c.name in filled or c.name in {"id", "project_id"}:
                continue
            found.add(f"{spec.table}.{c.name}")
    new = sorted(found - SCHEMA_V1_REQUIRED_COLUMNS)
    assert not new, (
        f"{new} are NOT NULL without a default on an EXPORTED table: older archives lack them. "
        "Give each a server default, or bump SCHEMA_VERSION and extend SCHEMA_V1_REQUIRED_COLUMNS"
    )
    assert not SCHEMA_V1_REQUIRED_COLUMNS - found, "a pinned column gained a default or left the export: update the pin"


def test_7_no_exported_table_has_a_binary_column():
    for spec in e.EXPORTED:
        binary = [c.name for c in TABLES[spec.table].columns if isinstance(c.type, LargeBinary)]
        assert not binary, f"{spec.table} has bytea columns {binary}; the row format has no bytea"


def test_7_inert_values_and_scrubbers_are_well_formed():
    for spec in e.EXPORTED:
        assert spec.scrub is None or spec.scrub in e.SCRUBBERS, spec.table
        for column, value in spec.inert.items():
            assert isinstance(value, (bool, str, e.ImportValue)), f"{spec.table}.{column} = {value!r}"
    assert e.spec_for("webhook_subscriptions").inert["disabled_reason"] == e.WEBHOOK_PAUSED_BY_PERSON
    reason_len = TABLES["webhook_subscriptions"].c["disabled_reason"].type.length
    assert len(e.WEBHOOK_PAUSED_BY_PERSON) <= reason_len


def test_7_a_not_null_user_ref_is_named_so_the_import_never_writes_null_there():
    required = {(s.table, c) for s in e.EXPORTED for c in e.required_user_refs(s)}
    assert required == {("release_gate_policies", "created_by")}, (
        f"NOT NULL user_refs columns are {sorted(required)}: the import maps a miss there to the importer — "
        "confirm that is right for the new one, then update this pin"
    )
    for spec in e.EXPORTED:
        for column in spec.cleared:
            assert TABLES[spec.table].c[column].nullable, f"{spec.table}.{column} is cleared to NULL but is NOT NULL"


def test_7_projects_row_columns_are_the_settings_plus_the_cleared_assignments():
    spec = e.spec_for("projects")
    assert set(e.exported_columns(spec)) == set(e.PROJECT_ROW_COLUMNS) | set(spec.cleared)


# ── (8) the pinned counts (a deliberate edit when a table is added) ──────────

def test_8_pinned_counts():
    counts = (len(e.EXPORTED), len(e.EXCLUDED), len(e.NOT_PROJECT_SCOPED))
    # W25a HYG-04 (deliberate): + suite_membership_parity_runs under NOT_PROJECT_SCOPED (23 → 24)
    assert counts == (30, 107, 24), (
        f"EXPORTED / EXCLUDED / NOT_PROJECT_SCOPED = {counts}: a table was added or moved — "
        "classify it in project_export_entities.py and update this pin deliberately"
    )
    assert sum(counts) == len(TABLES)
    assert sum(1 for r in e.EXCLUDED.values() if r == "run_history") == 37 + 8


# ── (9) fail-open ────────────────────────────────────────────────────────────

def test_9_the_walk_finds_what_it_must():
    assert len(e.project_scoped_tables(Base.metadata)) >= 130
    assert "test_runs" in e.run_history_tables(Base.metadata)
    assert len(e.run_history_tables(Base.metadata)) >= 30
