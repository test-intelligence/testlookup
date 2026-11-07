"""W24 ADM-05: every table is classified for the project export, against the REAL metadata.

``services/project_export_entities.py`` is the review point for every future
project-scoped table: a new table that is not classified there — exported (with
its rules), excluded (with a reason) or not project-scoped — fails here naming
it. Pure: walks ``app.models.postgres.Base.metadata``, no database.

Guards (W24 contract §2.1, W30 contract §2.1 — the primes are W30's deliberate
edits): (1) disjoint + complete; (2) a project-scoped table is never
NOT_PROJECT_SCOPED; (3') every exclusion reason is in the vocabulary and no
table carries the retired ``run_history``; (4') every table of the ``test_runs``
cascade closure is a HISTORY entity or excluded with a history reason — never a
catalog entity — and the 19 former run-history exclusions keep their reasons;
(5) every reset-map table is classified; (6') both plans are topologically
sorted (catalog = phase 1's 29 in phase 1's order) and every NOT NULL project FK
of an exported row can be satisfied; (7') every named column exists, no user
id and no excluded-table id is copied raw, every FK is accounted for, no global
unique value is copied, and no new NOT NULL column without a default silently
breaks older archives; (8) the pinned counts; (9) fail-open; (10) every history
entity has a window and every predicate is known; (11) the history status
vocabularies match the enums.
"""
from __future__ import annotations

from sqlalchemy import JSON, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.enums import PipelineRunStatus
from app.models.postgres import Base, LaunchStatus
from app.services import project_export_entities as e
from app.services.project_export_vocab import EXCLUSION_REASONS, HISTORY_RUN_STATUSES, TERMINAL_PIPELINE_STATUSES
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

# Guard (7') the same pin for the HISTORY entities (W30): a schema_version 2 archive always carries these.
SCHEMA_V2_HISTORY_REQUIRED_COLUMNS = frozenset({
    "test_runs.build_number", "test_cases.test_fingerprint", "test_cases.test_name",
    "test_case_history.test_fingerprint", "test_case_history.status",
    "suite_executions.suite_name", "suite_executions.suite_key", "suite_executions.source",
    "suite_executions.run_created_at", "suite_run_reviews.suite_name",
    "test_steps.ordinal", "test_steps.name", "test_steps.status",
    "test_step_runs.ordinal", "test_step_runs.name", "test_step_runs.status", "test_attachments.name",
    "failure_clusters.cluster_id", "failure_clusters.label", "agent_stage_results.stage_name",
    "ai_analysis_versions.test_fingerprint", "ai_analysis_versions.version", "ai_analysis_versions.trigger",
    "ai_analysis_versions.content_sha256", "failure_attribution.test_fingerprint", "failure_attribution.verdict",
    "deep_findings.cluster_id", "release_decisions.recommendation",
    "run_baselines.selection_reason", "run_baselines.classification", "run_diffs.diff_payload",
    "run_intelligence_snapshots.payload", "test_health_recommendations.test_name",
    "test_health_recommendations.health_score", "evidence_artifacts.artifact_type", "evidence_artifacts.source_system",
    "release_outcomes.outcome_kind", "release_outcomes.reason", "release_gate_decisions.verdict",
    "timeline_notes.starts_on", "timeline_notes.ends_on", "timeline_notes.text",
})

# Guard (6') phase 1's 29 catalog entities, in phase 1's order: a schema-1 export writes exactly these.
PHASE_1_PLAN = (
    "projects", "test_suites", "managed_test_cases", "test_case_versions", "test_case_requirement_links",
    "dismissed_duplicate_pairs", "canonical_test_cases", "test_plans", "test_plan_items", "test_strategies",
    "releases", "release_phases", "release_gate_policies", "release_attribution_rules",
    "quarantine_lifecycle_policies", "notification_transition_policies", "team_notification_channels",
    "webhook_subscriptions", "github_integrations", "gitlab_integrations", "service_ownership_rules",
    "project_retention_policies", "workflow_definitions", "project_workflow_defaults", "agent_configs",
    "run_cadences", "value_metric_assumptions", "classification_hints", "project_llm_quota",
)

# Guard (4') the 19 former run_history tables that stay EXCLUDED, with their W30 reasons (contract §2.1 X-1 … X-19).
# Each is a queue a sweep picks up, a credential / access grant, a training signal or the source's Jira — moving one
# to EXPORTED is a deliberate contract change, never a drive-by.
FORMER_RUN_HISTORY_EXCLUDED = {
    "run_downstream_outbox": "ingest_machinery", "agent_child_dispatch_outbox": "ingest_machinery",
    "decision_report_supersession_requests": "ingest_machinery", "live_ingestion_attempts": "ingest_machinery",
    "live_event_receipts": "ingest_machinery", "live_projection_checkpoints": "ingest_machinery",
    "live_sessions": "ingest_machinery", "agent_invocations": "ingest_machinery",
    "agent_investigations": "ai_history", "agent_memory_entries": "artefact_or_index", "ai_feedback": "ai_history",
    "decision_report_feedback": "ai_history", "workflow_replay_corpus": "ai_history",
    "report_share_links": "access_grant", "run_comparison_reports": "derived", "defect_candidates": "external_tracker",
    "defects": "external_tracker", "suite_membership_events": "derived", "run_tombstones": "audit_or_activity",
}
_HISTORY_EXCLUSION_REASONS = {
    "ingest_machinery", "ai_history", "artefact_or_index", "access_grant", "derived", "external_tracker",
    "audit_or_activity",
}
# Guard (10) a "<kind>:<column>" window's column must be an fks edge to this table.
_WINDOW_TARGETS = {
    "run": "test_runs", "case": "test_cases", "pipeline": "agent_pipeline_runs", "release": "releases",
    "canonical": "canonical_test_cases",
}


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


def test_3_the_retired_run_history_reason_is_carried_by_no_table():
    # W30: "run_history" stays in the vocabulary (a phase-1 manifest's excluded block names it) but retires
    still = sorted(t for t, r in e.EXCLUDED.items() if r == "run_history")
    assert not still, (
        f"{still} are excluded as the retired 'run_history': export them as history or "
        "classify it in project_export_entities.py with a specific reason"
    )
    assert "run_history" in EXCLUSION_REASONS and {"ingest_machinery", "external_tracker"} <= set(EXCLUSION_REASONS)


# ── (4') run history: exported as HISTORY, or excluded with a history reason ──

def test_4_the_test_runs_cascade_closure_is_history_or_excluded_with_a_history_reason():
    closure = e.run_history_tables(Base.metadata)
    for table in sorted(closure):
        if table in e.EXPORTED_TABLES:
            assert e.spec_for(table).history is True, (
                f"{table} cascades from test_runs but is a CATALOG entity: a schema-1 export would carry run "
                "history — make it a history entity (history=True) or classify it in project_export_entities.py "
                "as excluded with a reason"
            )
        else:
            reason = e.EXCLUDED.get(table)
            assert reason in _HISTORY_EXCLUSION_REASONS, (
                f"{table} cascades from test_runs and is excluded as {reason!r}: classify it in "
                f"project_export_entities.py as a history entity or excluded with one of {sorted(_HISTORY_EXCLUSION_REASONS)}"
            )


def test_4_the_former_run_history_exclusions_keep_their_reasons():
    for table, reason in FORMER_RUN_HISTORY_EXCLUDED.items():
        assert table not in e.EXPORTED_TABLES, (
            f"{table} is EXPORTED, but it is {reason} (contract §2.1 X-rows): it must never travel — "
            "classify it in project_export_entities.py as excluded"
        )
        assert e.EXCLUDED.get(table) == reason, (
            f"{table} must stay excluded as {reason!r} (contract §2.1); classify it in project_export_entities.py"
        )


def test_4_history_entities_are_exactly_the_history_tuple():
    assert [s.table for s in e.EXPORTED if s.history] == list(e.HISTORY_TABLES)
    assert all(not s.history for s in e.CATALOG) and all(s.history for s in e.HISTORY)
    assert len(e.HISTORY_TABLES) == 27


# ── (5) the reset map stays the source the backlog named ─────────────────────

def test_5_every_reset_map_table_is_exported_or_excluded():
    for name, _model in _FULL_RESET_TABLES:
        assert name in e.EXPORTED_TABLES or name in e.EXCLUDED, f"reset-map table {name}: {_FIX}"


# ── (6) the plan can be inserted in order ────────────────────────────────────

def test_6_both_plans_are_phase_1_then_history():
    # (6') the catalog plan is phase 1's 29 in phase 1's order; the history plan appends HISTORY_TABLES
    assert [s.table for s in e.plan()] == list(PHASE_1_PLAN) == list(e.CATALOG_TABLES)
    assert [s.table for s in e.plan(include_history=False)] == list(PHASE_1_PLAN)
    assert [s.table for s in e.plan(include_history=True)] == list(PHASE_1_PLAN) + list(e.HISTORY_TABLES)
    assert e.order_problems(e.CATALOG) == [] and e.order_problems(e.EXPORTED) == []


def test_6_history_fks_are_second_pass_columns_of_catalog_entities_pointing_at_history():
    history = set(e.HISTORY_TABLES)
    for spec in e.EXPORTED:
        for column, target in spec.history_fks.items():
            assert not spec.history, f"{spec.table}.{column}: history_fks belong to CATALOG entities (C-1)"
            assert target in history, f"{spec.table}.{column} → {target}: a history_fks target is a history entity"
            assert column not in spec.fks and column not in spec.drop, f"{spec.table}.{column} is a second-pass column"
    assert dict(e.spec_for("canonical_test_cases").history_fks) == {
        "first_seen_run_id": "test_runs", "last_seen_run_id": "test_runs", "deleted_at_run_id": "test_runs",
    }


def test_6_schema_1_columns_are_phase_1s_and_schema_2_adds_the_history_fks():
    spec = e.spec_for("canonical_test_cases")
    v1 = e.exported_columns(spec)
    assert v1 == e.exported_columns(spec, schema_version=1)
    assert not {"first_seen_run_id", "last_seen_run_id", "deleted_at_run_id"} & set(v1)
    assert set(e.exported_columns(spec, schema_version=2)) - set(v1) == set(spec.history_fks)
    for other in e.CATALOG:
        if not other.history_fks:
            assert e.exported_columns(other, schema_version=2) == e.exported_columns(other)
    try:
        e.exported_columns(spec, schema_version=3)
    except ValueError:
        pass
    else:
        raise AssertionError("an unsupported schema_version must raise")


def test_6_plan_is_topologically_sorted():
    assert [s.table for s in e.plan(include_history=True)] == list(e.EXPORTED_TABLES)
    assert e.plan()[0].table == "projects" and e.plan()[0].scope == e.SCOPE_ROOT
    assert e.order_problems() == []
    # every fks / parent edge points at an EXPORTED table that precedes the child
    position = {t: i for i, t in enumerate(e.EXPORTED_TABLES)}
    for i, spec in enumerate(e.plan(include_history=True)):
        parents = dict(spec.fks)
        parents.update({c: t for c, t in spec.null_unless.items() if t != spec.table and c not in spec.history_fks})
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
            "history_fks": tuple(spec.history_fks), "object_refs": spec.object_refs,
            "json_id_paths": tuple(spec.json_id_paths), "null_unless": tuple(spec.null_unless),
        }
        for kind, columns in named.items():
            missing = sorted(set(columns) - cols)
            assert not missing, f"{spec.table}.{kind} names {missing}, which are not columns of {spec.table}"
        remapped = (
            set(spec.user_refs) | set(spec.cleared) | set(spec.fks) | set(spec.inert) | set(spec.history_fks)
            | set(spec.null_unless) | set(spec.object_refs) | set(spec.json_id_paths)
        )
        assert len(set(spec.drop) & remapped) == 0, (
            f"{spec.table}: a dropped column cannot also be remapped / cleared / forced"
        )
        assert not set(spec.fks) & set(spec.null_unless), f"{spec.table}: a column is fks OR null_unless, not both"


def test_7_history_column_rules_have_the_right_types_and_targets():
    exported = set(e.EXPORTED_TABLES)
    for spec in e.EXPORTED:
        table = TABLES[spec.table]
        fk_targets = dict(_fk_columns(spec.table))
        for column, target in spec.null_unless.items():
            assert target in exported, f"{spec.table}.{column} → {target}: a null_unless target must be EXPORTED"
            if column in fk_targets:
                assert fk_targets[column] == target, f"{spec.table}.{column} is an FK to {fk_targets[column]}, not {target}"
            else:
                assert isinstance(table.c[column].type, UUID), f"{spec.table}.{column}: a no-FK null_unless is a UUID"
            assert table.c[column].nullable, f"{spec.table}.{column} is written NULL by the export but is NOT NULL"
        for column, target in spec.json_id_paths.items():
            assert isinstance(table.c[column].type, (JSON, JSONB)), f"{spec.table}.{column}: json_id_paths are JSON"
            assert target in exported and e.spec_for(target).history, f"{spec.table}.{column} → {target}"
        for column in spec.object_refs:
            assert isinstance(table.c[column].type, String), f"{spec.table}.{column}: an object ref is a string"


def test_7_no_global_unique_value_of_a_history_row_is_copied():
    # (7') a copy inside the SAME deployment would collide: a unique index not scoped by project_id, by the row's
    # own id or by an exported parent (fks / self_fks / null_unless / history_fks) is dropped (forced NULL)
    for spec in e.HISTORY:
        table = TABLES[spec.table]
        scoping = {"id", "project_id"} | set(spec.fks) | set(spec.self_fks) | set(spec.null_unless)
        uniques = [tuple(c.name for c in ix.columns) for ix in table.indexes if ix.unique]
        uniques += [tuple(c.name for c in con.columns) for con in table.constraints
                    if con.__class__.__name__ == "UniqueConstraint"]
        for columns in uniques:
            if set(columns) & scoping:
                continue
            kept = sorted(set(columns) - set(spec.drop))
            assert not kept, (
                f"{spec.table}{list(columns)} is a GLOBAL unique index: drop {kept} in project_export_entities.py "
                "(forced NULL) — an imported copy would collide with its source"
            )
    assert "idempotency_key" in e.spec_for("evidence_artifacts").drop


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
                remapped_to = spec.fks.get(column) or spec.history_fks.get(column) or spec.null_unless.get(column)
                assert remapped_to == ref, (
                    f"{spec.table}.{column} → {ref}: add it to fks / history_fks / null_unless (remapped)"
                )


def _required_without_default(specs: tuple[e.EntitySpec, ...]) -> set[str]:
    found = set()
    for spec in specs:
        filled = set(spec.fks) | set(spec.self_fks) | set(spec.user_refs) | set(spec.cleared) | set(spec.inert)
        for c in TABLES[spec.table].columns:
            if c.name in spec.drop or c.nullable or c.server_default is not None or c.default is not None:
                continue
            if c.name in filled or c.name in {"id", "project_id"}:
                continue
            found.add(f"{spec.table}.{c.name}")
    return found


def test_7_a_new_not_null_column_without_a_default_is_a_deliberate_decision():
    for specs, pin, version in ((e.CATALOG, SCHEMA_V1_REQUIRED_COLUMNS, "SCHEMA_V1_REQUIRED_COLUMNS"),
                                (e.HISTORY, SCHEMA_V2_HISTORY_REQUIRED_COLUMNS, "SCHEMA_V2_HISTORY_REQUIRED_COLUMNS")):
        found = _required_without_default(specs)
        new = sorted(found - pin)
        assert not new, (
            f"{new} are NOT NULL without a default on an EXPORTED table: older archives lack them. "
            f"Give each a server default, or bump SCHEMA_VERSION and extend {version}"
        )
        assert not pin - found, f"a pinned column gained a default or left the export: update {version}"


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
    # W25d HYG-02 (deliberate): quality_gates leaves EXPORTED (30 → 29); contract_violations (run_history),
    # tenant_metric_snapshots (derived) and ai_provenance_records (ai_history) leave EXCLUDED (107 → 104)
    # W30 ADM-05 phase 2 (deliberate): 27 history entities join EXPORTED (29 → 56) — the 25 run_history tables
    # that travel + test_attachments / evidence_artifacts (artefact_or_index); EXCLUDED 104 → 77 (the 19 former
    # run_history tables that stay out now carry specific reasons)
    # W31 STRAT-12 (deliberate): + subject_rights_jobs under NOT_PROJECT_SCOPED (24 → 25)
    # W34 STRAT-13 (deliberate): + drop_folder_builds under NOT_PROJECT_SCOPED (25 → 26)
    # W36 STRAT-03 (deliberate): + systemic_cluster_epics under EXCLUDED, external_tracker like defects (77 → 78)
    assert counts == (56, 78, 26), (
        f"EXPORTED / EXCLUDED / NOT_PROJECT_SCOPED = {counts}: a table was added or moved — "
        "classify it in project_export_entities.py and update this pin deliberately"
    )
    assert sum(counts) == len(TABLES)
    assert (len(e.CATALOG), len(e.HISTORY)) == (29, 27)
    assert sum(1 for r in e.EXCLUDED.values() if r == "run_history") == 0   # W30: the reason retired


# ── (9) fail-open ────────────────────────────────────────────────────────────

def test_9_the_walk_finds_what_it_must():
    assert len(e.project_scoped_tables(Base.metadata)) >= 130
    assert "test_runs" in e.run_history_tables(Base.metadata)
    assert len(e.run_history_tables(Base.metadata)) >= 30


# ── (10) every history entity has a window; every predicate is known ─────────

def test_10_every_history_entity_has_a_window_and_catalog_entities_none():
    for spec in e.EXPORTED:
        if not spec.history:
            assert spec.window is None and spec.select_where is None, f"{spec.table}: a catalog entity has no window"
            assert not spec.null_unless or spec.history_fks, f"{spec.table}: null_unless on a catalog entity is C-1 only"
            continue
        assert spec.window_parts is not None, f"{spec.table} is a history entity without a window"
        kind, column = spec.window_parts
        assert kind in e.WINDOW_KINDS, f"{spec.table}: window kind {kind!r} not in {e.WINDOW_KINDS}"
        cols = TABLES[spec.table].c
        if kind == e.WINDOW_RUNS:
            assert spec.table == "test_runs" and column is None
        elif kind == e.WINDOW_PROJECT:
            assert column is None and "project_id" in cols, f"{spec.table}: a project window needs project_id"
        else:
            assert column is not None and column in cols, f"{spec.table}: window {spec.window!r} names no column"
            assert spec.fks.get(column) == _WINDOW_TARGETS[kind], (
                f"{spec.table}: window {spec.window!r} needs fks[{column!r}] = {_WINDOW_TARGETS[kind]!r}"
            )


def test_10_every_select_where_names_a_known_predicate():
    used = {}
    for spec in e.EXPORTED:
        if spec.select_where is None:
            continue
        assert spec.select_where in e.HISTORY_PREDICATES, f"{spec.table}: unknown predicate {spec.select_where!r}"
        column, values = e.HISTORY_PREDICATES[spec.select_where]
        assert column in TABLES[spec.table].c and values, spec.table
        used[spec.table] = spec.select_where
    assert used == {"test_runs": "exported_run_status", "agent_pipeline_runs": "terminal_pipeline"}


def test_10_nothing_can_claim_an_imported_pipeline():
    # §0.10: the lease columns never travel and cancel_requested arrives false
    spec = e.spec_for("agent_pipeline_runs")
    assert {"lease_owner", "lease_expires_at", "fencing_token", "heartbeat_at", "next_retry_at"} <= set(spec.drop)
    assert spec.inert == {"cancel_requested": False}
    assert "lease_expires_at" in e.spec_for("agent_stage_results").drop


# ── (11) the history status vocabularies match the enums ─────────────────────

def test_11_history_status_vocabularies_match_the_enums():
    assert set(HISTORY_RUN_STATUSES) == {s.value for s in LaunchStatus} - {"IN_PROGRESS"}
    assert set(TERMINAL_PIPELINE_STATUSES) < {s.value for s in PipelineRunStatus}
    assert not {"pending", "running", "retry_wait"} & set(TERMINAL_PIPELINE_STATUSES)
