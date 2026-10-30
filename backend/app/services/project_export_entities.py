"""Which tables a project export carries, and how each one travels (W24 ADM-05 phase 1).

EVERY table of ``Base.metadata`` is classified here exactly once:

* ``EXPORTED`` — the settings + catalog entities, in DEPENDENCY ORDER (parents
  first): the export writes and the import inserts in this order. Each
  :class:`EntitySpec` names the columns that never travel (``drop``), the
  authorship columns remapped by USERNAME (``user_refs``), the assignment
  columns always cleared (``cleared``), the FKs remapped through the import's
  id map (``fks`` / ``self_fks``) and the values forced on import so nothing
  in a copy can mail or call out (``inert``).
* ``EXCLUDED`` — project-scoped tables that do not travel, each with ONE
  ``project_export_vocab.EXCLUSION_REASONS`` key.
* ``NOT_PROJECT_SCOPED`` — identity, instance settings, secrets, signing keys,
  the model registry and the two project-KEYED probe caches (no FK).

``tests/test_project_export_classification.py`` walks the REAL metadata: a new
table that is not classified here fails naming it ("classify <table> in
project_export_entities.py"). This file is the review point for every future
project-scoped table.

Imports ``app.models.postgres`` (the metadata walk) and the pure vocabulary
only: no session, no I/O (``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Mapping

from sqlalchemy import MetaData

from app.services.project_export_vocab import IMPORTED_PAUSE_REASON

# ── scope values ─────────────────────────────────────────────────────────────
SCOPE_ROOT = "root"                    # the ``projects`` row itself: WHERE id = :source
SCOPE_PROJECT = "project_column"       # WHERE project_id = :source
SCOPE_PARENT_PREFIX = "parent:"        # "parent:<parent table>.<fk column of this table>": rows of exported parents

# ── scrubber names (G-B's project_export_scrub applies them; contract §2.2) ──
SCRUB_DEFAULT = "default"              # S-5 / S-6: JSON columns → scrub_json, free text → text_pass
SCRUB_URLS = "urls"                    # SCRUB_DEFAULT + S-4 strip_url_credentials on the entity's URL column
SCRUB_TEAM_CHANNEL = "team_channel"    # SCRUB_DEFAULT + S-3: a slack / teams ``target`` → [REDACTED]
SCRUBBERS = (SCRUB_DEFAULT, SCRUB_URLS, SCRUB_TEAM_CHANNEL)


@dataclass(frozen=True)
class ImportValue:
    """A value the IMPORT computes at run time (never read from the archive)."""

    name: str


IMPORT_NOW = ImportValue("now")                     # the import transaction's clock (one value per import)
IMPORT_NOW_PLUS_PAUSE = ImportValue("now+pause")    # IMPORT_NOW + IMPORTED_PAUSE_DAYS days
IMPORTER = ImportValue("importer")                  # the requesting ADMIN's user id
IMPORT_VALUES = (IMPORT_NOW, IMPORT_NOW_PLUS_PAUSE, IMPORTER)

#: ``webhook_subscriptions.disabled_reason`` is a CODE column (VARCHAR(40),
#: ``webhook_breaker.WEBHOOK_DISABLED_REASONS``) — the 46-character
#: ``SECRET_REQUIRED`` sentence does not fit it. An imported webhook arrives as
#: a person's pause ("manual"), which the existing Re-enable path clears.
WEBHOOK_PAUSED_BY_PERSON = "manual"


@dataclass(frozen=True)
class EntitySpec:
    table: str                                                   # __tablename__
    scope: str = SCOPE_PROJECT                                   # SCOPE_ROOT | SCOPE_PROJECT | "parent:<table>.<fk column>"
    drop: tuple[str, ...] = ()                                   # columns never written (run pointers, counters, secret flags, lifecycle stamps)
    user_refs: tuple[str, ...] = ()                              # authorship → remapped by username to an ACTIVE user, else NULL ("user_not_found")
    cleared: tuple[str, ...] = ()                                # assignments → ALWAYS NULL on import ("assignment", counted when non-null)
    fks: Mapping[str, str] = field(default_factory=dict, hash=False)     # column → exported table; remapped; NOT NULL unresolved → row skipped
    self_fks: tuple[str, ...] = ()                               # same-table refs: inserted NULL, set in a second pass
    inert: Mapping[str, Any] = field(default_factory=dict, hash=False)   # column → value (or ImportValue) forced on import — counted in switched_off
    scrub: str | None = SCRUB_DEFAULT                            # a SCRUBBERS name (None = typed columns only)
    natural_key: tuple[str, ...] = ()                            # UNIQUE key inside the project (source values): a repeat → invalid_row

    @property
    def parent(self) -> tuple[str, str] | None:
        """``(parent table, fk column)`` for a ``parent:`` scope, else None."""
        if not self.scope.startswith(SCOPE_PARENT_PREFIX):
            return None
        table, _, column = self.scope[len(SCOPE_PARENT_PREFIX):].partition(".")
        return table, column


_PROJECTS = EntitySpec(
    "projects", scope=SCOPE_ROOT,
    # the copy gets its own id / name / slug (chosen on import), starts active, gets fresh stamps
    drop=("id", "name", "slug", "is_active", "archived_at", "archived_by_user_id", "created_at", "updated_at"),
    # provisioning gives the copy its own synthetic QA lead; the manager is an assignment
    cleared=("manager_user_id", "default_qa_lead_user_id"),
)
_TEST_SUITES = EntitySpec("test_suites", natural_key=("name",))
_MANAGED_TEST_CASES = EntitySpec(
    "managed_test_cases",
    drop=(
        "generation_batch_id",                                   # generation history is excluded (ai_history)
        "last_executed_at", "last_execution_status",             # run history
        "is_stale", "stale_reason",                              # knowledge sources are excluded
        "faithfulness_score", "faithfulness_evaluator", "faithfulness_evaluated_at",   # an AI evaluation of the source
    ),
    user_refs=("author_id", "approved_by_id", "deprecated_by_id", "archived_by_id"),
    cleared=("assignee_id", "reviewer_id"),
    fks={"test_suite_id": "test_suites"},
)
_TEST_CASE_VERSIONS = EntitySpec(
    "test_case_versions", scope="parent:managed_test_cases.test_case_id",
    user_refs=("changed_by_id",), cleared=("assignee_id",),
    fks={"test_case_id": "managed_test_cases", "test_suite_id": "test_suites"},
    natural_key=("test_case_id", "version"),
)
_TEST_CASE_REQUIREMENT_LINKS = EntitySpec(
    "test_case_requirement_links", user_refs=("created_by_id",),
    fks={"test_case_id": "managed_test_cases"}, natural_key=("test_case_id", "requirement_key_norm"),
)
_DISMISSED_DUPLICATE_PAIRS = EntitySpec(
    # NOTE (import): ck_dismissed_dup_canonical_order requires case_a_id < case_b_id — after the
    # remap the two NEW ids must be re-ordered (swap when a > b).
    "dismissed_duplicate_pairs", user_refs=("dismissed_by_user_id",),
    fks={"case_a_id": "managed_test_cases", "case_b_id": "managed_test_cases"},
    natural_key=("case_a_id", "case_b_id"),
)
_CANONICAL_TEST_CASES = EntitySpec(
    "canonical_test_cases",
    drop=("first_seen_run_id", "last_seen_run_id", "deleted_at_run_id"),   # run pointers
    user_refs=("retirement_confirmed_by_id",),
    fks={"test_suite_id": "test_suites", "managed_test_case_id": "managed_test_cases"},
    natural_key=("test_fingerprint",),
)
_TEST_PLANS = EntitySpec(
    "test_plans",
    drop=("actual_start_date", "actual_end_date", "executed_cases", "passed_cases", "failed_cases", "blocked_cases"),
    user_refs=("created_by_id",), cleared=("assigned_to_id",),
)
_TEST_PLAN_ITEMS = EntitySpec(
    "test_plan_items", scope="parent:test_plans.plan_id",
    # execution history; execution_status falls back to the column default "not_run"
    drop=("executed_by_id", "executed_at", "execution_notes", "actual_duration_minutes",
          "executed_case_version", "test_case_result_id", "execution_status"),
    fks={"plan_id": "test_plans", "test_case_id": "managed_test_cases"},
    natural_key=("plan_id", "test_case_id"),
)
_TEST_STRATEGIES = EntitySpec("test_strategies", user_refs=("created_by_id", "approved_by_id"))
_RELEASES = EntitySpec(
    # is_active kept — the import restores the one-active-release invariant itself (I-7)
    "releases", self_fks=("baseline_release_id",), user_refs=("created_by_id",), cleared=("owner_user_id",),
    scrub=SCRUB_URLS,
)
_RELEASE_PHASES = EntitySpec("release_phases", scope="parent:releases.release_id", fks={"release_id": "releases"})
_RELEASE_GATE_POLICIES = EntitySpec(
    # a NULL-project (system) policy is not the project's: project_id = :source selects only the project's rows
    "release_gate_policies", user_refs=("created_by", "activated_by"), natural_key=("version",),
)
_RELEASE_ATTRIBUTION_RULES = EntitySpec("release_attribution_rules", user_refs=("created_by_id",))
_QUALITY_GATES = EntitySpec("quality_gates")
_QUARANTINE_LIFECYCLE_POLICIES = EntitySpec(
    "quarantine_lifecycle_policies", inert={"auto_create_defect": False},   # a Jira call-out once runs arrive
)
_NOTIFICATION_TRANSITION_POLICIES = EntitySpec(
    "notification_transition_policies", inert={"transitions_enabled": False, "per_run_events_enabled": False},
)
_TEAM_NOTIFICATION_CHANNELS = EntitySpec(
    "team_notification_channels", inert={"is_active": False}, scrub=SCRUB_TEAM_CHANNEL, natural_key=("team_name",),
)
_WEBHOOK_SUBSCRIPTIONS = EntitySpec(
    "webhook_subscriptions",
    drop=("last_delivered_at", "last_failure_at", "last_error", "failure_count", "total_delivered",
          "consecutive_failures"),                                # delivery history
    user_refs=("updated_by_user_id",),
    inert={"enabled": False, "has_secret": False, "disabled_reason": WEBHOOK_PAUSED_BY_PERSON,
           "disabled_at": IMPORT_NOW},                            # the signing secret (secret_refs) never travels
    scrub=SCRUB_URLS,
)
_GITHUB_INTEGRATIONS = EntitySpec(
    "github_integrations",
    drop=("installation_id", "last_posted_at", "last_error", "last_error_at"),   # the SOURCE's GitHub App installation
    user_refs=("updated_by_user_id",), inert={"enabled": False, "has_pat": False}, scrub=SCRUB_URLS,
)
_GITLAB_INTEGRATIONS = EntitySpec(
    "gitlab_integrations", drop=("last_posted_at", "last_error", "last_error_at"),
    user_refs=("updated_by_user_id",),
    inert={"enabled": False, "has_pat": False, "commit_status_enabled": False}, scrub=SCRUB_URLS,
)
_SERVICE_OWNERSHIP_RULES = EntitySpec("service_ownership_rules", user_refs=("created_by",))
_PROJECT_RETENTION_POLICIES = EntitySpec(
    "project_retention_policies", drop=("purge_paused_until",), user_refs=("updated_by_user_id",),
)
_WORKFLOW_DEFINITIONS = EntitySpec(
    "workflow_definitions",
    # an evaluation is evidence of the SOURCE's models; the ck_workflow_definitions_* CHECKs need them NULL together
    drop=("eval_verdict", "eval_coverage", "eval_gate_run_id", "evaluated_at", "eval_regression_accepted",
          "eval_regression_reason", "eval_regression_accepted_by", "eval_regression_accepted_at"),
    user_refs=("created_by", "updated_by"), natural_key=("workflow_id", "version"),
)
_PROJECT_WORKFLOW_DEFAULTS = EntitySpec(
    # composite PK (project_id, base), no ``id`` column; workflow_id is a STRING fork id (no FK), kept verbatim
    "project_workflow_defaults", user_refs=("updated_by",), natural_key=("base",),
)
_AGENT_CONFIGS = EntitySpec("agent_configs", user_refs=("updated_by",), natural_key=("agent_id",))
_RUN_CADENCES = EntitySpec(
    "run_cadences", user_refs=("created_by_id",),
    # a cadence miss SENDS MAIL: the copy's cadences arrive paused for 90 days (the product's cap);
    # checked_through is NOT NULL, so "no back-dated window" = the import time
    inert={"paused_at": IMPORT_NOW, "paused_until": IMPORT_NOW_PLUS_PAUSE, "paused_reason": IMPORTED_PAUSE_REASON,
           "paused_by_id": IMPORTER, "effective_from": IMPORT_NOW, "checked_through": IMPORT_NOW},
)
_VALUE_METRIC_ASSUMPTIONS = EntitySpec("value_metric_assumptions", user_refs=("updated_by_user_id",), scrub=None)
_CLASSIFICATION_HINTS = EntitySpec("classification_hints", user_refs=("created_by",))
_PROJECT_LLM_QUOTA = EntitySpec("project_llm_quota", user_refs=("updated_by_user_id",))

#: Dependency order (parents first) — the export writes and the import inserts in this order.
EXPORTED: tuple[EntitySpec, ...] = (
    _PROJECTS,
    _TEST_SUITES, _MANAGED_TEST_CASES, _TEST_CASE_VERSIONS, _TEST_CASE_REQUIREMENT_LINKS,
    _DISMISSED_DUPLICATE_PAIRS, _CANONICAL_TEST_CASES, _TEST_PLANS, _TEST_PLAN_ITEMS, _TEST_STRATEGIES,
    _RELEASES, _RELEASE_PHASES, _RELEASE_GATE_POLICIES, _RELEASE_ATTRIBUTION_RULES, _QUALITY_GATES,
    _QUARANTINE_LIFECYCLE_POLICIES, _NOTIFICATION_TRANSITION_POLICIES, _TEAM_NOTIFICATION_CHANNELS,
    _WEBHOOK_SUBSCRIPTIONS, _GITHUB_INTEGRATIONS, _GITLAB_INTEGRATIONS, _SERVICE_OWNERSHIP_RULES,
    _PROJECT_RETENTION_POLICIES, _WORKFLOW_DEFINITIONS, _PROJECT_WORKFLOW_DEFAULTS, _AGENT_CONFIGS,
    _RUN_CADENCES, _VALUE_METRIC_ASSUMPTIONS, _CLASSIFICATION_HINTS, _PROJECT_LLM_QUOTA,
)
EXPORTED_TABLES: tuple[str, ...] = tuple(spec.table for spec in EXPORTED)

#: The settings columns copied onto the new project (contract §2.1 row 1). The
#: two ``cleared`` assignment columns travel too (counted, then NULL).
PROJECT_ROW_COLUMNS: tuple[str, ...] = (
    "description", "jira_project_key", "splunk_index", "ocp_namespace", "jenkins_job_pattern",
    "component_owner_map", "source_roots", "start_date", "end_date", "tags",
    "allow_unreviewed_distribution", "require_case_review", "automation_target_pct",
)

_RUN_HISTORY = "run_history"
#: Project-scoped tables that do NOT travel → one EXCLUSION_REASONS key each.
EXCLUDED: Mapping[str, str] = {
    # test_runs + its ON DELETE CASCADE closure (run_history_tables(), 37) — phase 2 (F-1)
    **{t: _RUN_HISTORY for t in (
        "agent_child_dispatch_outbox", "agent_investigations", "agent_invocations", "agent_memory_entries",
        "agent_pipeline_runs", "agent_stage_results", "ai_analysis", "ai_analysis_versions", "ai_feedback",
        "contract_violations", "decision_report_feedback", "decision_report_supersession_requests",
        "deep_findings", "defect_candidates", "failure_attribution", "failure_clusters", "live_event_receipts",
        "live_ingestion_attempts", "live_projection_checkpoints", "release_decisions", "release_test_run_links",
        "report_share_links", "run_baselines", "run_commit_ranges", "run_comparison_reports", "run_diffs",
        "run_downstream_outbox", "run_intelligence_snapshots", "suite_executions", "suite_run_reviews",
        "test_case_history", "test_cases", "test_execution_reviews", "test_health_recommendations", "test_runs",
        "test_step_runs", "workflow_replay_corpus",
    )},
    # other run-pointing history
    "suite_membership_events": _RUN_HISTORY,        # the per-run membership event log
    "live_sessions": _RUN_HISTORY,                  # live ingest sessions of runs
    "run_tombstones": _RUN_HISTORY,                 # records of deleted runs
    "defects": _RUN_HISTORY,                        # point at run test cases, clusters, the source's Jira tickets
    "timeline_notes": _RUN_HISTORY,                 # annotations of the run timeline
    "release_outcomes": _RUN_HISTORY,               # shipped / rolled-back evidence, read with its runs
    "release_gate_decisions": _RUN_HISTORY,         # gate decision history
    "test_steps": _RUN_HISTORY,                     # steps OBSERVED in runs
    # a person's own state
    "chat_sessions": "per_user_state",
    "chat_messages": "per_user_state",
    "saved_views": "per_user_state",
    "digest_subscriptions": "per_user_state",       # NOT copying it is what keeps the copy from mailing
    "notification_preferences": "per_user_state",   # also carries slack / teams webhook URLs
    "test_case_comments": "per_user_state",
    "test_case_reviews": "per_user_state",          # per-reviewer requests (their cases arrive draft)
    # this deployment's past
    "access_audit_logs": "audit_or_activity",
    "project_activity_events": "audit_or_activity",
    "test_case_audit_logs": "audit_or_activity",
    "product_usage_events": "audit_or_activity",
    "search_query_events": "audit_or_activity",
    "notification_logs": "audit_or_activity",
    "webhook_deliveries": "audit_or_activity",
    "agent_action_ledger": "audit_or_activity",
    # jobs, leases, outboxes
    "agent_action_dispatch_outbox": "job_or_outbox",
    "deletion_jobs": "job_or_outbox",
    "report_exports": "job_or_outbox",
    "semantic_reindex_jobs": "job_or_outbox",
    "project_data_jobs": "job_or_outbox",
    # an archive never grants access
    "project_members": "access_grant",
    "api_keys": "access_grant",
    "scim_group_mappings": "access_grant",
    "test_suite_owners": "access_grant",            # an owner must be a QA lead member; memberships are not copied
    # recomputed by a sweep / the next ingest
    "suite_memberships": "derived",                 # copying it recreates the reset guard's "already seen" defect
    "perf_baselines": "derived",
    "flaky_quarantine_requests": "derived",
    "flaky_score": "derived",
    "flaky_detection_state": "derived",
    "flaky_coach_results": "derived",
    "flaky_classifier_calibration": "derived",
    "systemic_flake_cluster": "derived",
    "systemic_flake_cluster_member": "derived",
    "coverage_snapshots": "derived",
    "tenant_metric_snapshots": "derived",
    "project_insights": "derived",
    "insight_detector_states": "derived",
    "run_cadence_windows": "derived",
    "duplicate_test_case_candidates": "derived",    # re-detected; the human dismissals travel
    "notification_test_states": "derived",
    "project_llm_usage": "derived",                 # this deployment's spend meter
    "tenant_onboarding_status": "derived",
    # evidence of the source's models
    "agent_runs": "ai_history",
    "ai_eval_gate_runs": "ai_history",
    "ai_eval_reviewer_quality": "ai_history",
    "ai_eval_runs": "ai_history",
    "ai_eval_shadow_pairs": "ai_history",
    "ai_eval_baselines": "ai_history",
    "ai_eval_item_outputs": "ai_history",
    "ai_provenance_records": "ai_history",
    "generation_batches": "ai_history",
    "generation_case_sources": "ai_history",
    "requirement_coverage": "ai_history",
    "review_requests": "ai_history",
    "fix_attempts": "ai_history",
    # a MinIO object or a vector index (phase 2 with artefacts)
    "knowledge_sources": "artefact_or_index",
    "knowledge_chunks": "artefact_or_index",
    "knowledge_sync_events": "artefact_or_index",
    "evidence_artifacts": "artefact_or_index",
    "test_attachments": "artefact_or_index",
    "compliance_packs": "artefact_or_index",
    # a legal act on the SOURCE's data
    "retention_holds": "legal_hold",
}

_IDENTITY = "identity / sign-in state of people, never project data"
_INSTANCE = "instance-wide setting or secret (never exported)"
#: Tables that belong to the DEPLOYMENT, not to a project → why.
NOT_PROJECT_SCOPED: Mapping[str, str] = {
    "users": _IDENTITY,
    "user_invitations": _IDENTITY,
    "user_ui_dismissals": _IDENTITY,
    "password_reset_tokens": _IDENTITY,
    "refresh_token_records": _IDENTITY,
    "mfa_recovery_codes": _IDENTITY,
    "federated_identities": _IDENTITY,
    "identity_events": _IDENTITY,
    "sso_configurations": "instance SSO configuration",
    "scim_groups": "instance SCIM directory",
    "scim_group_members": "instance SCIM directory",
    "scim_tokens": "instance SCIM credentials",
    "app_settings": _INSTANCE,
    "secret_refs": _INSTANCE,
    "settings_audit_log": "instance settings audit (per-project keys are only setting_key prefixes)",
    "feature_flags": "instance flags; a copy starts on the instance defaults",
    "instance_retention_policy": _INSTANCE,
    "compliance_signing_keys": "this deployment's signing keys",
    "model_versions": "the instance model registry",
    "ai_eval_datasets": "instance eval datasets",
    "decision_report_eval_cycles": "instance eval cycles",
    "integration_health_checks": "project-KEYED cache (scope_key, no FK), recomputed by the next probe",
    "integration_probe_results": "project-KEYED cache (scope_key, no FK), recomputed by the next probe",
}


def _fk_edges(metadata: MetaData) -> dict[str, list[tuple[str, str, str]]]:
    """table → [(column, referenced table, ondelete upper-cased)]."""
    edges: dict[str, list[tuple[str, str, str]]] = {}
    for name, table in metadata.tables.items():
        edges[name] = [
            (column.name, fk.column.table.name, (fk.ondelete or "").upper())
            for column in table.columns for fk in column.foreign_keys
        ]
    return edges


def project_scoped_tables(metadata: MetaData) -> set[str]:
    """Tables with a ``project_id`` column, OR an FK to ``projects.id``, OR an FK
    (any ``ondelete``) to a table already in the set — to a fixed point.
    ``projects`` itself is the root, not a member."""
    edges = _fk_edges(metadata)
    scoped = {
        name for name, table in metadata.tables.items()
        if name != "projects" and ("project_id" in table.c or any(ref == "projects" for _, ref, _ in edges[name]))
    }
    changed = True
    while changed:
        changed = False
        for name in metadata.tables:
            if name in scoped or name == "projects":
                continue
            if any(ref in scoped for _, ref, _ in edges[name]):
                scoped.add(name)
                changed = True
    return scoped


def run_history_tables(metadata: MetaData) -> set[str]:
    """``test_runs`` + every table reaching it through ``ON DELETE CASCADE`` FKs (the reset's closure)."""
    edges = _fk_edges(metadata)
    closure = {"test_runs"}
    changed = True
    while changed:
        changed = False
        for name in metadata.tables:
            if name in closure:
                continue
            if any(ref in closure and ondelete == "CASCADE" for _, ref, ondelete in edges[name]):
                closure.add(name)
                changed = True
    return closure


def spec_for(table: str) -> EntitySpec:
    """The EXPORTED spec of ``table`` (KeyError when it is not exported)."""
    return _SPECS[table]


_SPECS: dict[str, EntitySpec] = {spec.table: spec for spec in EXPORTED}


def exported_columns(spec: EntitySpec, metadata: MetaData | None = None) -> tuple[str, ...]:
    """The columns written for ``spec``'s rows: every column of the table minus ``drop``, table order."""
    md = metadata if metadata is not None else _metadata()
    return tuple(c.name for c in md.tables[spec.table].columns if c.name not in spec.drop)


def required_user_refs(spec: EntitySpec, metadata: MetaData | None = None) -> tuple[str, ...]:
    """The ``user_refs`` columns that are NOT NULL (today only ``release_gate_policies.created_by``):
    a username miss cannot become NULL there, so the import maps it to ``IMPORTER`` (still counted
    ``user_not_found``)."""
    md = metadata if metadata is not None else _metadata()
    table = md.tables[spec.table]
    return tuple(c for c in spec.user_refs if not table.c[c].nullable)


def order_problems(specs: tuple[EntitySpec, ...] = EXPORTED) -> list[str]:
    """Every ``fks`` / ``parent:`` edge whose parent does not precede its child (empty = sorted)."""
    position = {spec.table: i for i, spec in enumerate(specs)}
    problems: list[str] = []
    for i, spec in enumerate(specs):
        parents = set(spec.fks.values())
        if spec.parent is not None:
            parents.add(spec.parent[0])
        if spec.scope != SCOPE_ROOT:
            parents.add("projects")
        for parent in sorted(parents):
            if position.get(parent, len(specs)) >= i:
                problems.append(f"{spec.table} is planned before (or without) its parent {parent}")
    return problems


def _metadata() -> MetaData:
    from app.models.postgres import Base

    return Base.metadata


@lru_cache(maxsize=1)
def _checked_plan() -> tuple[EntitySpec, ...]:
    problems = order_problems(EXPORTED)
    if problems:
        raise RuntimeError("project export plan is not topologically sorted: " + "; ".join(problems))
    return EXPORTED


def plan() -> list[EntitySpec]:
    """``EXPORTED`` in dependency order (asserted: every parent precedes its children)."""
    return list(_checked_plan())
