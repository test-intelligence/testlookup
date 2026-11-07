"""Which tables a project export carries, and how each one travels (W24 ADM-05 phase 1; W30 phase 2).

EVERY table of ``Base.metadata`` is classified here exactly once:

* ``EXPORTED`` — the settings + catalog entities (``CATALOG``, phase 1's 29 in
  phase 1's order), THEN the run-history entities (``HISTORY``, W30, written
  only by an export WITH history — schema 2 / 3), in DEPENDENCY ORDER (parents
  first): the export writes and the import inserts in this order. Each
  :class:`EntitySpec` names the columns that never travel (``drop``), the
  authorship columns remapped by USERNAME (``user_refs``), the assignment
  columns always cleared (``cleared``), the FKs remapped through the import's
  id map (``fks`` / ``self_fks``) and the values forced on import so nothing
  in a copy can mail or call out (``inert``). A history entity also names the
  rows it travels with (``window``, ``select_where``), its pointers written
  NULL by the export when their target is outside the archive
  (``null_unless``), its stored keys rebuilt under the new root
  (``object_refs``) and its JSON id lists (``json_id_paths``).
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

from app.services.project_export_vocab import (
    CATALOG_SCHEMA_VERSION,
    HISTORY_RUN_STATUSES,
    HISTORY_SCHEMA_VERSION,
    IMPORTED_PAUSE_REASON,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    TERMINAL_PIPELINE_STATUSES,
)

# ── scope values ─────────────────────────────────────────────────────────────
SCOPE_ROOT = "root"                    # the ``projects`` row itself: WHERE id = :source
SCOPE_PROJECT = "project_column"       # WHERE project_id = :source
SCOPE_PARENT_PREFIX = "parent:"        # "parent:<parent table>.<fk column of this table>": rows of exported parents

# ── scrubber names (G-B's project_export_scrub applies them; contract §2.2) ──
SCRUB_DEFAULT = "default"              # S-5 / S-6: JSON columns → scrub_json, free text → text_pass
SCRUB_URLS = "urls"                    # SCRUB_DEFAULT + S-4 strip_url_credentials on the entity's URL column
# Not a ``*_CHANNEL`` name: test_review_gate_inline_vocab_parity reads those as review-gate distribution channels.
SCRUB_TEAM_TARGETS = "team_channel"    # SCRUB_DEFAULT + S-3: a slack / teams ``target`` → [REDACTED]
SCRUBBERS = (SCRUB_DEFAULT, SCRUB_URLS, SCRUB_TEAM_TARGETS)

# ── history windows (W30, contract §1.4 / §2.1): which rows of a history entity travel ──
WINDOW_RUNS = "runs"                   # the run window itself: test_runs WHERE id = ANY(:window)
WINDOW_PROJECT = "project"             # every row of the project (project_id = :source)
#: "<kind>:<column>" kinds — rows whose <column> points at an exported row of that kind:
#: run (∈ the run window), case (a case of a window run), pipeline (an EXPORTED pipeline),
#: release (an exported release), canonical (an exported canonical case).
WINDOW_KINDS = ("runs", "run", "case", "pipeline", "release", "canonical", "project")

#: ``select_where`` names → (column, allowed values): the extra filter a history entity's window applies.
HISTORY_PREDICATES: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "exported_run_status": ("status", HISTORY_RUN_STATUSES),          # an IN_PROGRESS run is skipped, never half-copied
    "terminal_pipeline": ("status", TERMINAL_PIPELINE_STATUSES),     # a non-terminal pipeline (+ its stage results) is skipped
}


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
    # ── W30 run history (all default empty, so phase-1 specs are unchanged) ──
    history: bool = False                                        # written only by a history export (schema 2 / 3); rows selected by ``window``
    window: str | None = None                                    # WINDOW_RUNS | WINDOW_PROJECT | "<kind>:<column>" (WINDOW_KINDS)
    history_fks: Mapping[str, str] = field(default_factory=dict, hash=False)   # CATALOG column dropped without history, remapped with it (second pass)
    object_refs: tuple[str, ...] = ()                            # columns holding a stored key / prefix → rebuilt under the new root (§2.3 R-1)
    json_id_paths: Mapping[str, str] = field(default_factory=dict, hash=False)  # JSON list of ids the product READS → the table whose remap rewrites each id
    null_unless: Mapping[str, str] = field(default_factory=dict, hash=False)    # column → table: written NULL by the EXPORT when its target is outside the archive
    select_where: str | None = None                              # a HISTORY_PREDICATES name
    # ── W37 D-1: version-gated columns ──
    schema3_columns: tuple[str, ...] = ()                        # dropped below schema 3 (a schema-1 / 2 archive never carries them), exported at schema 3

    @property
    def parent(self) -> tuple[str, str] | None:
        """``(parent table, fk column)`` for a ``parent:`` scope, else None."""
        if not self.scope.startswith(SCOPE_PARENT_PREFIX):
            return None
        table, _, column = self.scope[len(SCOPE_PARENT_PREFIX):].partition(".")
        return table, column

    @property
    def window_parts(self) -> tuple[str, str | None] | None:
        """``(kind, column)`` of ``window`` (column None for ``runs`` / ``project``); None when no window."""
        if self.window is None:
            return None
        kind, _, column = self.window.partition(":")
        return kind, (column or None)


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
        "last_executed_at", "last_execution_status",             # run history — W37 D-3: stay dropped, even with history
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
_CANONICAL_RUN_POINTERS = {"first_seen_run_id": "test_runs", "last_seen_run_id": "test_runs", "deleted_at_run_id": "test_runs"}
_CANONICAL_TEST_CASES = EntitySpec(
    "canonical_test_cases",
    # C-1 (W30): the run pointers are dropped in schema 1 (as phase 1) and remapped in schema 2 —
    # written NULL by the export when that run is outside the window, set in the second pass
    user_refs=("retirement_confirmed_by_id",),
    fks={"test_suite_id": "test_suites", "managed_test_case_id": "managed_test_cases"},
    natural_key=("test_fingerprint",),
    history_fks=dict(_CANONICAL_RUN_POINTERS), null_unless=dict(_CANONICAL_RUN_POINTERS),
)
_TEST_PLANS = EntitySpec(
    "test_plans",
    # the four counters are recomputed by the import from the items (W37 F-W30-3); W37 D-3: actual_start_date /
    # actual_end_date stay dropped (no writer exists)
    drop=("actual_start_date", "actual_end_date", "executed_cases", "passed_cases", "failed_cases", "blocked_cases"),
    user_refs=("created_by_id",), cleared=("assigned_to_id",),
)
_PLAN_EXECUTION_RESULT = {"test_case_result_id": "test_cases"}   # → a per-run case (HISTORY; unbounded in the remap)
_TEST_PLAN_ITEMS = EntitySpec(
    "test_plan_items", scope="parent:test_plans.plan_id",
    # W37 F-W30-3 (D-3): a person's manual execution result travels at schema 3 (an export with history); a
    # schema-1 / 2 archive never carries it and execution_status falls back to the column default "not_run".
    # test_case_result_id is written NULL unless its case is a case of the window, and set in the second pass.
    user_refs=("executed_by_id",),
    fks={"plan_id": "test_plans", "test_case_id": "managed_test_cases"},
    natural_key=("plan_id", "test_case_id"),
    history_fks=dict(_PLAN_EXECUTION_RESULT), null_unless=dict(_PLAN_EXECUTION_RESULT),
    schema3_columns=("executed_by_id", "executed_at", "execution_notes", "actual_duration_minutes",
                     "executed_case_version", "test_case_result_id", "execution_status"),
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
# (W25d HYG-02: quality_gates is dropped — an older archive that lists it is read through RETIRED_ENTITIES.)
_QUARANTINE_LIFECYCLE_POLICIES = EntitySpec(
    "quarantine_lifecycle_policies", inert={"auto_create_defect": False},   # a Jira call-out once runs arrive
)
_NOTIFICATION_TRANSITION_POLICIES = EntitySpec(
    "notification_transition_policies", inert={"transitions_enabled": False, "per_run_events_enabled": False},
)
_TEAM_NOTIFICATION_CHANNELS = EntitySpec(
    "team_notification_channels", inert={"is_active": False}, scrub=SCRUB_TEAM_TARGETS, natural_key=("team_name",),
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

# ── W30 run history (contract §2.1 H-1 … H-27): history=True, written only by a schema-2 export ──
_RUNS = "parent:test_runs."
_LEASE_COLUMNS = ("lease_owner", "lease_expires_at", "fencing_token", "heartbeat_at", "next_retry_at")
_H_TEST_RUNS = EntitySpec(
    # event_archive: the raw live-event archive (its own raw_events_days class) — the documents travel instead
    "test_runs", window=WINDOW_RUNS, select_where="exported_run_status",
    drop=("event_archive", "event_archive_at"), fks={"primary_release_id": "releases"},
    object_refs=("minio_prefix",), scrub=SCRUB_URLS, history=True,
)
_H_AGENT_PIPELINE_RUNS = EntitySpec(
    # §0.10: lease columns dropped and cancel_requested forced false — nothing can claim an imported pipeline
    "agent_pipeline_runs", scope=_RUNS + "test_run_id", window="run:test_run_id", select_where="terminal_pipeline",
    drop=_LEASE_COLUMNS, user_refs=("requested_by",), fks={"test_run_id": "test_runs"},
    self_fks=("parent_pipeline_run_id", "rerun_of"),
    null_unless={"parent_pipeline_run_id": "agent_pipeline_runs", "rerun_of": "agent_pipeline_runs"},
    inert={"cancel_requested": False}, history=True,
)
_H_TEST_CASES = EntitySpec(
    "test_cases", scope=_RUNS + "test_run_id", window="run:test_run_id",
    user_refs=("triage_updated_by_user_id",), cleared=("assigned_to_user_id",),
    fks={"test_run_id": "test_runs", "canonical_test_case_id": "canonical_test_cases"},
    object_refs=("minio_s3_prefix",), natural_key=("test_run_id", "test_fingerprint"), history=True,
)
_H_TEST_CASE_HISTORY = EntitySpec(
    "test_case_history", scope=_RUNS + "test_run_id", window="run:test_run_id",
    fks={"test_case_id": "test_cases", "test_run_id": "test_runs"}, history=True,
)
_H_SUITE_EXECUTIONS = EntitySpec(
    "suite_executions", window="run:test_run_id", fks={"test_run_id": "test_runs"},
    natural_key=("test_run_id", "suite_key"), history=True,
)
_H_SUITE_RUN_REVIEWS = EntitySpec(
    "suite_run_reviews", window="run:test_run_id", user_refs=("reviewer_user_id",),
    fks={"test_run_id": "test_runs"}, natural_key=("test_run_id", "suite_name"), history=True,
)
_H_TEST_EXECUTION_REVIEWS = EntitySpec(
    "test_execution_reviews", window="case:test_case_id", user_refs=("reviewed_by_user_id",),
    fks={"test_case_id": "test_cases"}, scrub=SCRUB_URLS, natural_key=("test_case_id",), history=True,
)
_H_TEST_STEPS = EntitySpec(
    "test_steps", scope="parent:canonical_test_cases.canonical_test_case_id",
    window="canonical:canonical_test_case_id",
    fks={"canonical_test_case_id": "canonical_test_cases"}, self_fks=("parent_step_id",),
    null_unless={"source_test_run_id": "test_runs"}, history=True,
)
_H_TEST_STEP_RUNS = EntitySpec(
    "test_step_runs", scope=_RUNS + "source_test_run_id", window="run:source_test_run_id",
    fks={"canonical_test_case_id": "canonical_test_cases", "source_test_run_id": "test_runs"},
    natural_key=("canonical_test_case_id", "source_test_run_id", "ordinal"), history=True,
)
_H_TEST_ATTACHMENTS = EntitySpec(
    # source_ref is a name relative to the run's result files (kept verbatim; the bytes travel as objects)
    "test_attachments", scope="parent:canonical_test_cases.canonical_test_case_id",
    window="canonical:canonical_test_case_id",
    fks={"canonical_test_case_id": "canonical_test_cases", "test_step_id": "test_steps"},
    null_unless={"source_test_run_id": "test_runs"}, history=True,
)
_H_FAILURE_CLUSTERS = EntitySpec(
    "failure_clusters", scope=_RUNS + "test_run_id", window="run:test_run_id",
    fks={"test_run_id": "test_runs"}, null_unless={"pipeline_run_id": "agent_pipeline_runs"},
    json_id_paths={"member_test_ids": "test_cases"}, history=True,
)
_H_AGENT_STAGE_RESULTS = EntitySpec(
    # AI traces are DATA: result / checkpoint / decision JSON through scrub_json; no prompt is read or written
    "agent_stage_results", scope="parent:agent_pipeline_runs.pipeline_run_id", window="pipeline:pipeline_run_id",
    drop=("lease_expires_at",), fks={"pipeline_run_id": "agent_pipeline_runs"},
    null_unless={"failure_cluster_id": "failure_clusters"}, history=True,
)
_H_AI_ANALYSIS = EntitySpec(
    "ai_analysis", scope="parent:test_cases.test_case_id", window="case:test_case_id",
    fks={"test_case_id": "test_cases"}, natural_key=("test_case_id",), history=True,
)
_H_AI_ANALYSIS_VERSIONS = EntitySpec(
    # content_sha256 kept: the reader never re-verifies it (a scrubbed version's digest may no longer match)
    "ai_analysis_versions", window="case:test_case_id", user_refs=("actor_user_id",),
    fks={"test_case_id": "test_cases", "test_run_id": "test_runs"},
    null_unless={"pipeline_run_id": "agent_pipeline_runs"}, natural_key=("test_case_id", "version"), history=True,
)
_H_FAILURE_ATTRIBUTION = EntitySpec(
    "failure_attribution", window="case:test_case_id",
    fks={"test_case_id": "test_cases", "test_run_id": "test_runs"}, natural_key=("test_case_id",), history=True,
)
_H_DEEP_FINDINGS = EntitySpec(
    # cluster_id is a string label (no FK) — kept
    "deep_findings", scope=_RUNS + "test_run_id", window="run:test_run_id", fks={"test_run_id": "test_runs"}, history=True,
)
_H_RELEASE_DECISIONS = EntitySpec(
    # a NULL-project (system) policy is not exported → policy_id written NULL
    "release_decisions", scope=_RUNS + "test_run_id", window="run:test_run_id", user_refs=("overridden_by",),
    fks={"test_run_id": "test_runs"},
    null_unless={"pipeline_run_id": "agent_pipeline_runs", "policy_id": "release_gate_policies"},
    natural_key=("test_run_id",), history=True,
)
_H_RELEASE_TEST_RUN_LINKS = EntitySpec(
    # project_id exists WITHOUT an FK constraint — written as the NEW project id like every project_id
    "release_test_run_links", window="run:test_run_id", user_refs=("linked_by_id",),
    fks={"release_id": "releases", "test_run_id": "test_runs", "phase_id": "release_phases"},
    natural_key=("release_id", "test_run_id"), history=True,
)
_H_RUN_BASELINES = EntitySpec(
    "run_baselines", scope=_RUNS + "run_id", window="run:run_id", fks={"run_id": "test_runs"},
    null_unless={"baseline_run_id": "test_runs"}, natural_key=("run_id",), history=True,
)
_H_RUN_DIFFS = EntitySpec(
    "run_diffs", scope=_RUNS + "run_id", window="run:run_id", fks={"run_id": "test_runs"},
    null_unless={"baseline_run_id": "test_runs"}, natural_key=("run_id",), history=True,
)
_H_RUN_COMMIT_RANGES = EntitySpec(
    # base_run_id is a plain UUID column (no FK) — remapped like one
    "run_commit_ranges", window="run:run_id", fks={"run_id": "test_runs"},
    null_unless={"base_run_id": "test_runs"}, natural_key=("run_id",), history=True,
)
_H_RUN_INTELLIGENCE_SNAPSHOTS = EntitySpec(
    # imported as stored, so viewing an imported run never regenerates it
    "run_intelligence_snapshots", scope=_RUNS + "run_id", window="run:run_id", fks={"run_id": "test_runs"},
    natural_key=("run_id",), history=True,
)
_H_TEST_HEALTH_RECOMMENDATIONS = EntitySpec(
    "test_health_recommendations", scope=_RUNS + "test_run_id", window="run:test_run_id",
    fks={"test_run_id": "test_runs", "test_case_id": "test_cases"}, history=True,
)
_H_EVIDENCE_ARTIFACTS = EntitySpec(
    # rows with run_id NULL are project-level evidence of the source (not in the window). idempotency_key has a
    # GLOBAL unique index → forced NULL (dropped). uri_or_ref into the pipeline-artifacts bucket → NULL (G-B's scrub).
    "evidence_artifacts", window="run:run_id", drop=("idempotency_key",), fks={"run_id": "test_runs"},
    null_unless={"test_case_id": "test_cases", "producer_pipeline_run_id": "agent_pipeline_runs"},
    scrub=SCRUB_URLS, history=True,
)
_H_RELEASE_OUTCOMES = EntitySpec(
    "release_outcomes", window="release:release_id", user_refs=("marked_by_user_id",),
    fks={"release_id": "releases"}, history=True,
)
_H_RELEASE_GATE_DECISIONS = EntitySpec(
    # imported decisions keep is_current as exported — the gate's history; NO gate is evaluated by the import
    "release_gate_decisions", scope="parent:releases.release_id", window="release:release_id",
    user_refs=("created_by_id",),
    fks={"release_id": "releases", "phase_id": "release_phases", "baseline_release_id": "releases"},
    null_unless={"policy_id": "release_gate_policies"},
    json_id_paths={"run_ids": "test_runs", "incomplete_runs": "test_runs"}, history=True,
)
_H_TIMELINE_NOTES = EntitySpec(
    "timeline_notes", window=WINDOW_PROJECT, user_refs=("created_by_id",), history=True,
)

#: Phase 1's 29 settings + catalog entities, in phase 1's order (a schema-1 export writes exactly these).
CATALOG: tuple[EntitySpec, ...] = (
    _PROJECTS,
    _TEST_SUITES, _MANAGED_TEST_CASES, _TEST_CASE_VERSIONS, _TEST_CASE_REQUIREMENT_LINKS,
    _DISMISSED_DUPLICATE_PAIRS, _CANONICAL_TEST_CASES, _TEST_PLANS, _TEST_PLAN_ITEMS, _TEST_STRATEGIES,
    _RELEASES, _RELEASE_PHASES, _RELEASE_GATE_POLICIES, _RELEASE_ATTRIBUTION_RULES,
    _QUARANTINE_LIFECYCLE_POLICIES, _NOTIFICATION_TRANSITION_POLICIES, _TEAM_NOTIFICATION_CHANNELS,
    _WEBHOOK_SUBSCRIPTIONS, _GITHUB_INTEGRATIONS, _GITLAB_INTEGRATIONS, _SERVICE_OWNERSHIP_RULES,
    _PROJECT_RETENTION_POLICIES, _WORKFLOW_DEFINITIONS, _PROJECT_WORKFLOW_DEFAULTS, _AGENT_CONFIGS,
    _RUN_CADENCES, _VALUE_METRIC_ASSUMPTIONS, _CLASSIFICATION_HINTS, _PROJECT_LLM_QUOTA,
)
#: The 27 run-history entities (W30), parents first, planned AFTER the catalog (a schema-2 export).
HISTORY: tuple[EntitySpec, ...] = (
    _H_TEST_RUNS, _H_AGENT_PIPELINE_RUNS, _H_TEST_CASES, _H_TEST_CASE_HISTORY, _H_SUITE_EXECUTIONS,
    _H_SUITE_RUN_REVIEWS, _H_TEST_EXECUTION_REVIEWS, _H_TEST_STEPS, _H_TEST_STEP_RUNS, _H_TEST_ATTACHMENTS,
    _H_FAILURE_CLUSTERS, _H_AGENT_STAGE_RESULTS, _H_AI_ANALYSIS, _H_AI_ANALYSIS_VERSIONS, _H_FAILURE_ATTRIBUTION,
    _H_DEEP_FINDINGS, _H_RELEASE_DECISIONS, _H_RELEASE_TEST_RUN_LINKS, _H_RUN_BASELINES, _H_RUN_DIFFS,
    _H_RUN_COMMIT_RANGES, _H_RUN_INTELLIGENCE_SNAPSHOTS, _H_TEST_HEALTH_RECOMMENDATIONS, _H_EVIDENCE_ARTIFACTS,
    _H_RELEASE_OUTCOMES, _H_RELEASE_GATE_DECISIONS, _H_TIMELINE_NOTES,
)
#: Dependency order (parents first) — the export writes and the import inserts in this order.
EXPORTED: tuple[EntitySpec, ...] = CATALOG + HISTORY
EXPORTED_TABLES: tuple[str, ...] = tuple(spec.table for spec in EXPORTED)
CATALOG_TABLES: tuple[str, ...] = tuple(spec.table for spec in CATALOG)
HISTORY_TABLES: tuple[str, ...] = tuple(spec.table for spec in HISTORY)

#: The settings columns copied onto the new project (contract §2.1 row 1). The
#: two ``cleared`` assignment columns travel too (counted, then NULL).
PROJECT_ROW_COLUMNS: tuple[str, ...] = (
    "description", "jira_project_key", "splunk_index", "ocp_namespace", "jenkins_job_pattern",
    "component_owner_map", "source_roots", "start_date", "end_date", "tags",
    "allow_unreviewed_distribution", "require_case_review", "automation_target_pct",
    "cluster_epic_suggestions",     # W36 STRAT-03 (0255): a setting; the epics it led to stay behind (EXCLUDED)
)

#: Project-scoped tables that do NOT travel → one EXCLUSION_REASONS key each. (W30: the former
#: ``run_history`` rows are EXPORTED as history or excluded below with a specific reason — X-1 … X-19.)
EXCLUDED: Mapping[str, str] = {
    # queues a sweep would pick up — an import must never hand one a copied row (§0.10)
    "run_downstream_outbox": "ingest_machinery",            # X-1: relay / recover_waiting_run_finalizations → finalize_run
    "agent_child_dispatch_outbox": "ingest_machinery",      # X-2: child-investigation dispatch queue
    "decision_report_supersession_requests": "ingest_machinery",   # X-3: status / next_attempt_at work queue
    "live_ingestion_attempts": "ingest_machinery",          # X-4: live-ingest batch receipts (the SOURCE's Redis ids)
    "live_event_receipts": "ingest_machinery",              # X-5: per-event projection receipts; event_id globally unique
    "live_projection_checkpoints": "ingest_machinery",      # X-6: a consumer-group checkpoint of the source's stream
    "live_sessions": "ingest_machinery",                    # X-7: an ingest session with session_token_hash (a credential hash)
    "agent_invocations": "ingest_machinery",                # X-8: an invocation REQUEST; the pipeline it started travels
    "agent_investigations": "ai_history",                   # X-9: budgeted working state reaped by reap_stale_investigations
    "agent_memory_entries": "artefact_or_index",            # X-10: backed by ChromaDB vectors that do not travel
    "ai_feedback": "ai_history",                            # X-11: a training signal — would double-count in training data
    "decision_report_feedback": "ai_history",               # X-12: the same, for decision reports
    "workflow_replay_corpus": "ai_history",                 # X-13: eval replay corpus, GLOBAL unique key
    "report_share_links": "access_grant",                   # X-14: a token_hash granting anonymous read access
    "run_comparison_reports": "derived",                    # X-15: a cached comparison, rebuilt on demand
    "defect_candidates": "external_tracker",                # X-16: promotion files a Jira ticket
    "defects": "external_tracker",                          # X-17: the SOURCE's Jira tickets; sync_jira_defect_statuses would call out
    "suite_membership_events": "derived",                   # X-18: the membership event log, rebuilt by the next ingest
    "run_tombstones": "audit_or_activity",                  # X-19: records of THIS deployment's run deletions
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
    "generation_batches": "ai_history",
    "generation_case_sources": "ai_history",
    "requirement_coverage": "ai_history",
    "review_requests": "ai_history",
    "fix_attempts": "ai_history",
    # a MinIO object or a vector index (W30: test_attachments / evidence_artifacts travel as history)
    "knowledge_sources": "artefact_or_index",
    "knowledge_chunks": "artefact_or_index",
    "knowledge_sync_events": "artefact_or_index",
    "compliance_packs": "artefact_or_index",
    # a legal act on the SOURCE's data
    "retention_holds": "legal_hold",
    # W36 STRAT-03 (migration 0255): an epic filed in the SOURCE's Jira for a systemic cluster — classified like
    # ``defects`` (X-17), the closest project-scoped Jira-link table; the copy's own clusters get their own epics
    "systemic_cluster_epics": "external_tracker",
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
    "suite_membership_parity_runs": "instance cutover evidence (HYG-04), dropped with the legacy membership tables",
    # W31 STRAT-12 (migration 0253)
    "subject_rights_jobs": "a person's export / erasure job — instance-wide, never part of a project archive",
    # W34 STRAT-13 (migration 0254)
    "drop_folder_builds": "this deployment's bucket-keyed drop-folder watcher ledger (key prefix, no FK), "
                          "never part of a project archive",
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


def export_schema_version(*, include_history: bool) -> int:
    """W37 D-1: the LOWEST schema that holds an export's content — ``CATALOG_SCHEMA_VERSION`` (1, byte-compatible
    with phase 1) for settings + catalog only, ``SCHEMA_VERSION`` (3) with run history. Schema 2 is never written.
    (G-B: members / reviews without history are schema 3 too — add their flags here.)"""
    return SCHEMA_VERSION if include_history else CATALOG_SCHEMA_VERSION


def archive_has_history(schema_version: int, *, history_block: bool) -> bool:
    """W37 D-1, THE "has history" rule of an archive: schema 2 (W30 — always), or schema >= 3 WITH a manifest
    ``history`` block (a schema-3 archive without one carries members / reviews only). Schema 1: never."""
    return schema_version == HISTORY_SCHEMA_VERSION or (schema_version > HISTORY_SCHEMA_VERSION and history_block)


def hidden_columns(spec: EntitySpec, *, schema_version: int, history: bool) -> frozenset[str]:
    """The columns of ``spec`` an archive of ``schema_version`` (with / without run history) never carries:
    ``drop``; the ``history_fks`` (second-pass pointers into history) when it has no history (schema 1, or
    schema 3 without a history block — there are no runs to point at); the ``schema3_columns`` below schema 3.
    The export leaves them out and the import refuses a row carrying one (``unknown_column``)."""
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"schema_version {schema_version!r} is not one of {SUPPORTED_SCHEMA_VERSIONS}")
    hidden = set(spec.drop)
    if not history or schema_version == CATALOG_SCHEMA_VERSION:     # schema 1 never carries history
        hidden |= set(spec.history_fks)
    if schema_version < SCHEMA_VERSION:
        hidden |= set(spec.schema3_columns)
    return frozenset(hidden)


def exported_columns(
    spec: EntitySpec, metadata: MetaData | None = None, *, schema_version: int = CATALOG_SCHEMA_VERSION,
    history: bool | None = None,
) -> tuple[str, ...]:
    """The columns written for ``spec``'s rows, table order: every column minus :func:`hidden_columns`.
    Schema 1 (the default — phase 1's columns, what every phase-1 caller reads) also drops the ``history_fks``
    (C-1's canonical run pointers) and the ``schema3_columns``; schema 2 keeps the ``history_fks``; schema 3
    keeps both. ``history`` defaults to the schema's own (1: none; 2 / 3: with history) — a schema-3 export
    WITHOUT history passes ``history=False``. A schema this deployment does not read → ValueError."""
    with_history = schema_version != CATALOG_SCHEMA_VERSION if history is None else history
    hidden = hidden_columns(spec, schema_version=schema_version, history=with_history)
    md = metadata if metadata is not None else _metadata()
    return tuple(c.name for c in md.tables[spec.table].columns if c.name not in hidden)


def required_user_refs(spec: EntitySpec, metadata: MetaData | None = None) -> tuple[str, ...]:
    """The ``user_refs`` columns that are NOT NULL (today only ``release_gate_policies.created_by``):
    a username miss cannot become NULL there, so the import maps it to ``IMPORTER`` (still counted
    ``user_not_found``)."""
    md = metadata if metadata is not None else _metadata()
    table = md.tables[spec.table]
    return tuple(c for c in spec.user_refs if not table.c[c].nullable)


def order_problems(specs: tuple[EntitySpec, ...] = EXPORTED) -> list[str]:
    """Every ``fks`` / ``parent:`` / non-self ``null_unless`` edge whose parent does not precede its
    child (empty = sorted). ``self_fks`` and ``history_fks`` are second-pass columns, not edges."""
    position = {spec.table: i for i, spec in enumerate(specs)}
    problems: list[str] = []
    for i, spec in enumerate(specs):
        parents = set(spec.fks.values())
        parents |= {t for c, t in spec.null_unless.items() if t != spec.table and c not in spec.history_fks}
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


@lru_cache(maxsize=2)
def _checked_plan(include_history: bool) -> tuple[EntitySpec, ...]:
    specs = EXPORTED if include_history else CATALOG
    problems = order_problems(specs)
    if problems:
        raise RuntimeError("project export plan is not topologically sorted: " + "; ".join(problems))
    return specs


def plan(*, include_history: bool = False) -> list[EntitySpec]:
    """The entities in dependency order (asserted: every parent precedes its children): phase 1's 29
    ``CATALOG`` entities (the default — what every phase-1 caller reads, a schema-1 export), or those 29
    + ``HISTORY`` (an export / import WITH history: schema 2 / 3)."""
    return list(_checked_plan(include_history))
