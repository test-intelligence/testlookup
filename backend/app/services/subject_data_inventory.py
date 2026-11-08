"""Where TestLookup holds a person's data, how each place is exported and erased (W31 STRAT-12; contract §2.1).

The heart of the wave: ONE list of every location that names a user — every
column with a FK to ``users.id``, every non-FK column that copies a username /
e-mail / name, the one non-ORM table, every Mongo collection, object prefix,
user-keyed Redis key, the Chroma families and the log sinks. Each location
says how the per-person export carries it (``export_as`` → archive member
``category``) and what the erasure does to it (``erase``), with a one-sentence
``reason`` wherever the answer is "keep" or "nothing to do".

* G-B's export iterates ``INVENTORY`` (``export_as != "none"``); a location of
  category ``PROFILE_CATEGORY`` is written into ``profile.json``, every other
  category is a ``<category>/part-NNNN.jsonl`` member directory; a location's
  ``child_tables`` (CASCADE children, e.g. chat messages) are exported into the
  member of the same name and erased with it.
* G-C's preview and erasure iterate it in this order (deletes, rewrites,
  references, kept records, the other stores).
* ``tests/test_subject_data_inventory.py`` is the guard: a NEW FK to
  ``users.id`` or a new name-like column that is not classified here fails CI
  with "classify table.column in subject_data_inventory.py".

Pure data: imports only ``dataclasses`` / ``typing``; no ``app.*`` import, no
session, no I/O (``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Location:
    key: str                  # "table.column" | "users.*" | "mongo:<coll>" | "objects:<prefix>" | "redis:<pattern>" | "chroma:<family>" | "logs"
    store: str                # STORES
    table: Optional[str]
    column: Optional[str]
    match: str                # MATCHES: "fk" (column = :uid) | "value" | "mention" | "self" | "id_key" | "content" | "none"
    export_as: str            # subject_rights_vocab.EXPORT_AS
    category: Optional[str]   # archive member directory for export_as != "none" (CATEGORIES)
    erase: str                # subject_rights_vocab.ERASE_ACTIONS
    label: str                # the preview's sentence ("Chat conversations")
    reason: str               # one sentence; REQUIRED for keep_audit / keep_record / out_of_scope / no_personal_data
    drop: tuple[str, ...] = ()        # columns never exported (hashes, tokens)
    columns: tuple[str, ...] = ()     # export_as == "metadata": the ONLY columns exported
    open_item: bool = False           # counted in preview.open_items (an assignment / ownership a lead may reassign)
    # ── additive (W31 G-A; the contract's shapes only grow) ──
    extra: tuple[str, ...] = ()        # derived values the export ADDS to each row (a join), as named in the row
    child_tables: tuple[str, ...] = () # CASCADE children exported (member of the same name) and erased with these rows
    where: str = ""                    # the row filter in words / SQL (for the export, the preview count and the erasure)


STORES = ("postgres", "mongo", "objects", "redis", "chroma", "logs")
MATCHES = ("fk", "value", "mention", "self", "id_key", "content", "none")
PROFILE_CATEGORY = "profile"           # → profile.json (one object), not a member directory
#: §1.3's archive members (+ PROFILE_CATEGORY): every category a location may name.
CATEGORIES = (
    PROFILE_CATEGORY, "memberships", "sso_identities", "scim_groups", "sessions", "api_keys",
    "notification_preferences", "notifications", "digest_subscriptions", "saved_views", "ui_dismissals",
    "invitations", "chat_sessions", "chat_messages", "comments", "reviews", "feedback", "timeline_notes",
    "assignments", "activity", "usage", "audit_access", "audit_settings", "audit_test_management",
    "audit_identity", "references",
)
#: == scripts/quality_gate.py ``_AUDIT_TABLES`` (the protected backend.audit-write-discipline gate; the guard
#: reads the gate read-only and asserts equality). Never UPDATEd or DELETEd by W31.
AUDIT_TABLES = (
    "access_audit_logs", "identity_events", "settings_audit_log", "test_case_audit_logs", "project_activity_events",
)
#: Legal / signed records kept under Art. 17(3) outside the gate's five tables (contract D-W31-4).
ART17_KEEP_TABLES = ("release_decisions", "retention_holds", "compliance_packs", "deletion_jobs")
ART17_KEEP_PREFIXES = ("exports/", "compliance/")

# A location's ``reason`` is shown to the administrator in the erase preview: plain sentences, no code names
# (local demo, 2026-11-04). The audit rows are append-only under the protected backend.audit-write-discipline gate.
_AUDIT = "Art. 17(3)(b)(e): an audit record, which is never changed; it is kept until its own retention period ends"
_REF = "The project's record keeps pointing at the pseudonymised account, which every surface shows as \"Erased user\""


def _row(table: str, column: str, *, export_as: str, category: Optional[str], erase: str, label: str,
         reason: str = "", match: str = "fk", **more: Any) -> Location:
    return Location(key=f"{table}.{column}", store="postgres", table=table, column=column, match=match,
                    export_as=export_as, category=category, erase=erase, label=label, reason=reason,
                    **more)


def _ref(table: str, column: str, label: str, *, open_item: bool = False) -> Location:
    """A D-location: the row is the project's record, left byte-for-byte; exported as a pointer."""
    return _row(table, column, export_as="reference", category="assignments" if open_item else "references",
                erase="reference", label=label, reason=_REF, open_item=open_item)


def _store(key: str, store: str, *, match: str, erase: str, label: str, reason: str) -> Location:
    return Location(key=key, store=store, table=None, column=None, match=match, export_as="none", category=None,
                    erase=erase, label=label, reason=reason)


_INVITATION_COLUMNS = ("role", "created_at", "used_at", "expires_at", "project_memberships")

# ── A. Rows that exist only for the person — DELETE (15 FK columns + A4 + A17) ──────────────────────────────
_A: tuple[Location, ...] = (
    _row("api_keys", "user_id", export_as="metadata", category="api_keys", erase="delete", label="API keys",
         columns=("name", "key_hint", "scopes", "project_id", "is_active", "expires_at", "last_used_at", "created_at"),
         drop=("key_hash", "minted_by_key_id"),
         where="the lock-out deactivates them first; the Postgres phase deletes them"),
    _row("refresh_token_records", "user_id", export_as="metadata", category="sessions", erase="delete",
         label="Sessions and devices", columns=("issued_at", "expires_at", "revoked_at", "replay_detected"),
         drop=("jti_hash", "rotated_to_id"), where="the lock-out deletes them"),
    _row("mfa_recovery_codes", "user_id", export_as="metadata", category=PROFILE_CATEGORY, erase="delete",
         label="Two-factor recovery codes", columns=("used_at", "created_at"), drop=("code_hash",),
         where="profile.json carries only mfa.recovery_codes_left (unused codes); deleted via mfa_service.disable_mfa"),
    _row("secret_refs", "key_name", match="value", export_as="none", category=None, erase="delete",
         label="Two-factor secret", where="scope = 'user_totp' AND key_name = :user_id::text (the encrypted TOTP seed)"),
    _row("federated_identities", "user_id", export_as="full", category="sso_identities", erase="delete",
         label="Single sign-on links", extra=("sso_display_name",),
         where="a later IdP login provisions a NEW account, never the erased row"),
    _row("scim_group_members", "user_id", export_as="metadata", category="scim_groups", erase="delete",
         label="Directory (SCIM) group memberships", columns=("group_id",), extra=("group_display_name",)),
    _row("project_members", "user_id", export_as="metadata", category="memberships", erase="delete",
         label="Project memberships", columns=("project_id", "role", "created_at", "source"), extra=("project_name",),
         where="the membership:{id} cache is dropped in the redis phase"),
    _row("notification_preferences", "user_id", export_as="full", category="notification_preferences",
         erase="delete", label="Notification preferences",
         where="slack_webhook_url / teams_webhook_url export as \"[REDACTED]\" when set"),
    _row("notification_logs", "user_id", export_as="metadata", category="notifications", erase="delete",
         label="Their notifications (inbox)",
         columns=("channel", "event_type", "title", "body", "status", "is_read", "sent_at", "created_at", "project_id"),
         drop=("delivery_key", "delivery_metadata", "delivery_token", "error_detail"),
         where="a pending row would otherwise still try to deliver"),
    _row("digest_subscriptions", "user_id", export_as="full", category="digest_subscriptions", erase="delete",
         label="Digest subscriptions"),
    _row("saved_views", "user_id", export_as="full", category="saved_views", erase="delete",
         label="Private saved views",
         where="delete only is_shared = false; a SHARED view is the project's record — kept, its owner reads \"Erased user\""),
    _row("user_ui_dismissals", "user_id", export_as="full", category="ui_dismissals", erase="delete",
         label="Dismissed hints"),
    _row("chat_sessions", "user_id", export_as="full", category="chat_sessions", erase="delete",
         label="Chat conversations", child_tables=("chat_messages",),
         where="chat_messages cascade with their session"),
    _row("password_reset_tokens", "user_id", export_as="none", category=None, erase="delete",
         label="Password-reset links", drop=("token_hash", "request_ip_hash")),
    _row("product_usage_events", "user_id", export_as="metadata", category="usage", erase="delete",
         label="Product usage events", columns=("event_name", "event_payload", "project_id", "created_at")),
    _row("user_invitations", "accepted_user_id", export_as="metadata", category="invitations", erase="delete",
         label="The invitation they accepted", columns=_INVITATION_COLUMNS, drop=("email", "token", "token_hash")),
    _row("user_invitations", "email", match="value", export_as="metadata", category="invitations", erase="delete",
         label="Invitations addressed to them", columns=_INVITATION_COLUMNS, drop=("email", "token", "token_hash"),
         where="lower(email) = lower(:email) AND accepted_user_id IS NULL (pending / expired)"),
)

# ── B. Values that name the person outside audit rows — PSEUDONYMISE in place (9) ───────────────────────────
_B: tuple[Location, ...] = (
    Location(key="users.*", store="postgres", table="users", column=None, match="self", export_as="full",
             category=PROFILE_CATEGORY, erase="pseudonymise", label="Name, username and e-mail", reason="",
             drop=("hashed_password", "mfa_last_used_step"),
             where="username → erased-<hex8>, email → <pseudonym>@erased.invalid, full_name → \"Erased user\", "
                   "avatar_color NULL, hashed_password → the marker, MFA off, login fields cleared, is_active false, "
                   "erased_at set; id / role / created_at kept"),
    _row("project_insights", "dismissed_by_name", match="value", export_as="none", category=None,
         erase="pseudonymise", label="Insights they dismissed (name shown)", where="dismissed_by_id = :uid → \"Erased user\""),
    _row("report_share_links", "created_by_name", match="value", export_as="none", category=None,
         erase="pseudonymise", label="Share links they created (name shown)",
         where="created_by_id = :uid → \"Erased user\" (the link itself stays: the project's share)"),
    _row("agent_investigations", "cancelled_by", match="value", export_as="none", category=None,
         erase="pseudonymise", label="Investigations they cancelled",
         where="cancelled_by IN (:username, :email) → the pseudonym (routers/agent_investigations.py)"),
    _row("agent_pipeline_runs", "error", match="value", export_as="none", category=None, erase="pseudonymise",
         label="Pipelines they cancelled before start",
         where="error = 'cancelled: by ' || :email → 'cancelled: by ' || the pseudonym (pipeline_cancellation.request_cancel; "
               "workflow_run_state's CANCELLED_ERROR_PREFIX)"),
    _row("test_case_comments", "content", match="mention", export_as="none", category=None, erase="pseudonymise",
         label="Comments that mention them",
         where="(?<![\\w.@-])@<old username>(?![\\w.-]), case-insensitive → @<pseudonym>; nothing else changes"),
    _row("webhook_deliveries", "event_payload", match="value", export_as="none", category=None,
         erase="pseudonymise", label="Webhook deliveries that name them",
         where="event_payload->>'requested_by' IN (:username, :email) (defect.create_requested, "
               "defect_jira_service) → the pseudonym"),
    _row("notification_logs", "title", match="value", export_as="none", category=None, erase="pseudonymise",
         label="Other people's notifications that name them",
         where="a title composed with their display name (MENTION_TITLE \"{author} mentioned you on …\") → "
               "\"Erased user\"; rows of other recipients only (their own inbox is deleted)"),
    _row("notification_logs", "body", match="mention", export_as="none", category=None, erase="pseudonymise",
         label="Other people's notifications that mention them",
         where="the comment clip a mention notification carries: @<old username> → @<pseudonym> (B6's token rule); "
               "a composed \"owner: <full name>\" → \"Erased user\""),
)

_LEGAL = "Art. 17(3)(e): a legal / signed record — rewriting it would break the evidence; kept until its own expiry"


def _audit(table: str, column: str, label: str, *, category: Optional[str] = None, match: str = "fk",
           where: str = "") -> Location:
    return _row(table, column, match=match, export_as="full" if category else "none", category=category,
                erase="keep_audit", label=label, reason=_AUDIT, where=where)


# ── C. Audit and legal records — KEEP under Art. 17(3) (13 FK columns + 9 snapshots; never written) ──────────
_C: tuple[Location, ...] = (
    _audit("access_audit_logs", "actor_user_id", "Access changes they made", category="audit_access",
           where="ONE member: rows where actor_user_id = :uid OR target_user_id = :uid; a row they acted in carries "
                 "the target's id only"),
    _audit("access_audit_logs", "target_user_id", "Access changes made to them", category="audit_access",
           where="the same member as access_audit_logs.actor_user_id"),
    _audit("access_audit_logs", "actor_name", "Their name on access records", match="value"),
    _audit("identity_events", "user_id", "Sign-in and provisioning events", category="audit_identity",
           where="ONE member: rows where user_id = :uid OR actor_id = :uid (detail / ip_address included)"),
    _audit("identity_events", "actor_id", "Identity events they caused", category="audit_identity",
           where="the same member as identity_events.user_id"),
    _audit("identity_events", "actor_name", "Their name on identity events", match="value"),
    _audit("settings_audit_log", "actor_id", "Configuration changes they made", category="audit_settings"),
    _audit("settings_audit_log", "actor_name", "Their name on configuration changes", match="value"),
    _audit("test_case_audit_logs", "actor_id", "Test-content changes they made", category="audit_test_management"),
    _audit("test_case_audit_logs", "actor_name", "Their name on test-content changes", match="value"),
    _audit("project_activity_events", "actor_id", "Activity records", category="activity",
           where="the product feed shows \"Erased user\" at READ time (E-9); context JSON (e.g. approved_by) kept"),
    _audit("project_activity_events", "actor_name", "Their name on activity records", match="value"),
    _audit("project_activity_events", "actor_ref", "Their reference on activity records", match="value"),
    _row("release_decisions", "overridden_by", export_as="reference", category="references", erase="keep_audit",
         label="Release overrides they made", reason=_LEGAL),  # compliance.md names release decisions an audit source
    _row("release_decisions", "override_audit", match="value", export_as="none", category=None, erase="keep_audit",
         label="Their name in release-override trails", where="override_audit[].actor_name (JSON)",
         reason=f"{_LEGAL} (a compliance pack snapshots the trail)"),
    _row("retention_holds", "placed_by_id", export_as="reference", category="references", erase="keep_audit",
         label="Legal holds they placed", reason=f"{_LEGAL}; a legal hold is itself a legal act"),
    _row("retention_holds", "released_by_id", export_as="reference", category="references", erase="keep_audit",
         label="Legal holds they released", reason=f"{_LEGAL}; a legal hold is itself a legal act"),
    _row("retention_holds", "placed_by_name", match="value", export_as="none", category=None, erase="keep_audit",
         label="Their name on legal holds they placed", reason=f"{_LEGAL}; a legal hold is itself a legal act"),
    _row("retention_holds", "released_by_name", match="value", export_as="none", category=None, erase="keep_audit",
         label="Their name on legal holds they released", reason=f"{_LEGAL}; a legal hold is itself a legal act"),
    _row("compliance_packs", "generated_by_user_id", export_as="reference", category="references", erase="keep_audit",
         label="Compliance packs they generated", reason=f"{_LEGAL} (signed evidence)"),
    _row("deletion_jobs", "requested_by_id", export_as="reference", category="references", erase="keep_audit",
         label="Run-data deletions they requested", reason=f"{_LEGAL} (deletion evidence)"),
    _row("deletion_jobs", "acknowledged_by_id", export_as="reference", category="references", erase="keep_audit",
         label="Run-data archives they collected", reason=f"{_LEGAL} (deletion evidence)"),
)


def _own(table: str, column: str, category: str, label: str, **more: Any) -> Location:
    """A D-location exported IN FULL (the person's own words); the row stays the project's record."""
    return _row(table, column, export_as="full", category=category, erase="reference", label=label, reason=_REF,
                **more)


# ── D. The project's records that point at the person — PSEUDONYMISED BY REFERENCE (rows unchanged) ─────────
_D_OWN_WORDS: tuple[Location, ...] = (
    _own("test_case_comments", "author_id", "comments", "Comments they wrote", extra=("test_case_title",),
         where="their comments only, never the thread's other replies"),
    _own("test_case_reviews", "reviewer_id", "reviews", "Reviews assigned to them", open_item=True,
         where="open while status is pending"),
    _ref("test_case_reviews", "requested_by_id", "Reviews they requested"),
    _own("ai_feedback", "user_id", "feedback", "AI feedback they gave"),
    _own("decision_report_feedback", "user_id", "feedback", "Decision-report feedback they gave"),
    _own("timeline_notes", "created_by_id", "timeline_notes", "Timeline notes they wrote"),
)
_D_WORK: tuple[Location, ...] = (
    _ref("test_cases", "assigned_to_user_id", "Test cases assigned to them", open_item=True),
    _ref("test_cases", "triage_updated_by_user_id", "Test cases they triaged"),
    _ref("managed_test_cases", "assignee_id", "Authored test cases assigned to them", open_item=True),
    _ref("managed_test_cases", "reviewer_id", "Authored test cases they review", open_item=True),
    _ref("managed_test_cases", "author_id", "Test cases they authored"),
    _ref("managed_test_cases", "approved_by_id", "Test cases they approved"),
    _ref("managed_test_cases", "deprecated_by_id", "Test cases they deprecated"),
    _ref("managed_test_cases", "archived_by_id", "Test cases they archived"),
    _ref("test_case_versions", "changed_by_id", "Test-case versions they saved"),
    _ref("test_case_versions", "assignee_id", "Test-case versions assigned to them"),
    _ref("defects", "assignee_user_id", "Defects assigned to them", open_item=True),
    _ref("defects", "approved_by", "Defects they approved"),
    _ref("test_plans", "assigned_to_id", "Test plans assigned to them", open_item=True),
    _ref("test_plans", "created_by_id", "Test plans they created"),
    _ref("test_plan_items", "executed_by_id", "Plan items they executed"),
    _ref("test_strategies", "created_by_id", "Test strategies they created"),
    _ref("test_strategies", "approved_by_id", "Test strategies they approved"),
    _ref("releases", "owner_user_id", "Releases they own", open_item=True),
    _ref("releases", "created_by_id", "Releases they created"),
    _ref("test_suite_owners", "owner_user_id", "Suites they own", open_item=True),
    _ref("flaky_quarantine_requests", "owner_user_id", "Quarantined tests they own", open_item=True),
    _ref("flaky_quarantine_requests", "approved_by_user_id", "Quarantines they approved"),
    _ref("flaky_quarantine_requests", "rejected_by_user_id", "Quarantines they rejected"),
    _ref("knowledge_sources", "owner_id", "Knowledge sources they own", open_item=True),
    _ref("projects", "manager_user_id", "Projects they manage", open_item=True),
    _ref("projects", "default_qa_lead_user_id", "Projects where they are the default QA lead"),
    _ref("projects", "archived_by_user_id", "Projects they archived"),
)
_D_AI: tuple[Location, ...] = (
    _ref("agent_action_ledger", "actor_user_id", "Agent actions they triggered"),
    _ref("agent_action_ledger", "approved_by", "Agent actions they approved"),
    _ref("agent_configs", "updated_by", "Agent configurations they changed"),
    _ref("agent_investigations", "requested_by", "Investigations they requested"),
    _ref("agent_invocations", "requested_by", "Agent runs they requested"),
    _ref("agent_pipeline_runs", "requested_by", "Pipelines they requested"),
    _ref("ai_analysis_versions", "actor_user_id", "AI analysis versions they caused"),
    _ref("ai_eval_baselines", "created_by", "AI evaluation baselines they created"),
    _ref("ai_eval_datasets", "created_by", "AI evaluation datasets they created"),
    _ref("ai_eval_gate_runs", "evaluated_by", "AI gate runs they evaluated"),
    _ref("ai_eval_reviewer_quality", "evaluated_by", "Reviewer-quality checks they evaluated"),
    _ref("ai_eval_runs", "requested_by", "AI evaluations they requested"),
    _ref("ai_eval_shadow_pairs", "labelled_by", "Shadow pairs they labelled"),
    _ref("classification_hints", "created_by", "Classification hints they added"),
    _ref("decision_report_eval_cycles", "evaluated_by", "Decision-report evaluations they ran"),
    _ref("generation_batches", "created_by_id", "Test-generation batches they started"),
    _ref("workflow_definitions", "created_by", "Workflows they created"),
    _ref("workflow_definitions", "updated_by", "Workflows they changed"),
    _ref("workflow_definitions", "eval_regression_accepted_by", "Workflow regressions they accepted"),
)
_D_CONFIG: tuple[Location, ...] = (   # configuration attribution; the matching settings_audit_log row is C4
    _ref("app_settings", "updated_by", "Settings they last changed"),
    _ref("feature_flags", "updated_by_user_id", "Feature flags they last changed"),
    _ref("secret_refs", "updated_by", "Secrets they last changed"),
    _ref("scim_tokens", "created_by_id", "SCIM tokens they created"),
    _ref("compliance_signing_keys", "created_by_user_id", "Signing keys they created"),
    _ref("compliance_signing_keys", "retired_by_user_id", "Signing keys they retired"),
    _ref("instance_retention_policy", "enabled_by_id", "The retention policy they enabled"),
    _ref("instance_retention_policy", "updated_by_user_id", "The retention policy they last changed"),
    _ref("project_retention_policies", "updated_by_user_id", "Project retention policies they changed"),
    _ref("project_llm_quota", "updated_by_user_id", "AI quotas they changed"),
    _ref("project_workflow_defaults", "updated_by", "Workflow defaults they changed"),
    _ref("github_integrations", "updated_by_user_id", "GitHub integrations they changed"),
    _ref("gitlab_integrations", "updated_by_user_id", "GitLab integrations they changed"),
    _ref("webhook_subscriptions", "updated_by_user_id", "Webhooks they changed"),
    _ref("value_metric_assumptions", "updated_by_user_id", "Value assumptions they changed"),
    _ref("tenant_onboarding_status", "completed_by", "Onboarding they completed"),
)
_D_RECORDS: tuple[Location, ...] = (
    _ref("release_attribution_rules", "created_by_id", "Release attribution rules they created"),
    # W36 STRAT-03 (migration 0255): the epic link row is the project's record, like a classification hint's author
    _ref("systemic_cluster_epics", "created_by", "Jira epics they filed for failure clusters"),
    _ref("release_gate_decisions", "created_by_id", "Release gate decisions they recorded"),
    _ref("release_gate_policies", "created_by", "Release gate policies they created"),
    _ref("release_gate_policies", "activated_by", "Release gate policies they activated"),
    _ref("release_outcomes", "marked_by_user_id", "Release outcomes they marked"),
    _ref("release_test_run_links", "linked_by_id", "Runs they linked to releases"),
    _ref("run_cadences", "created_by_id", "Run cadences they created"),
    _ref("run_cadences", "paused_by_id", "Run cadences they paused"),
    _ref("run_comparison_reports", "created_by_user_id", "Run comparisons they created"),
    _ref("run_tombstones", "deleted_by_id", "Runs they deleted"),
    _ref("report_exports", "requested_by_id", "Report exports they requested"),
    _ref("report_share_links", "created_by_id", "Share links they created"),
    _ref("project_insights", "dismissed_by_id", "Insights they dismissed"),
    _ref("project_data_jobs", "requested_by_id", "Project exports and imports they ran"),
    _ref("suite_membership_parity_runs", "requested_by_id", "Membership parity runs they requested"),
    _ref("suite_run_reviews", "reviewer_user_id", "Suite runs they reviewed"),
    _ref("test_execution_reviews", "reviewed_by_user_id", "Test executions they reviewed"),
    _ref("review_requests", "requested_by", "Review requests they raised"),
    _ref("review_requests", "reviewed_by", "Review requests they decided"),
    _ref("service_ownership_rules", "created_by", "Service ownership rules they created"),
    _ref("test_case_requirement_links", "created_by_id", "Requirement links they created"),
    _ref("dismissed_duplicate_pairs", "dismissed_by_user_id", "Duplicate pairs they dismissed"),
    _ref("canonical_test_cases", "retirement_confirmed_by_id", "Test retirements they confirmed"),
    _ref("password_reset_tokens", "issued_by_user_id", "Password-reset links they issued for others"),
    _ref("user_invitations", "invited_by_id", "Invitations they sent (the invitee's e-mail is not exported)"),
)
# W31's own table (migration 0253) — the person's export / erasure requests; ids, counts and codes only
_SUBJECT_JOBS: tuple[Location, ...] = (
    _row("subject_rights_jobs", "subject_user_id", export_as="none", category=None, erase="keep_record",
         label="Their export and erasure requests",
         reason="The erasure's own record and resumable state (ids, phases, counts, codes — never a name); "
                "the export jobs' archives are removed and the jobs read \"expired\""),
    _row("subject_rights_jobs", "requested_by_id", export_as="none", category=None, erase="reference",
         label="Export and erasure requests they made", reason=_REF),
    # W38 (W31 F-1, migration 0256, 2026-11-07): the person's erasure REQUESTS — ids, statuses, codes, dates; no
    # free text (D-2). Kept (D-7, like subject_rights_jobs.subject_user_id): after the erasure the row reads
    # "Erased user" through the queue's displays.
    _row("subject_erasure_requests", "subject_user_id", export_as="none", category=None, erase="keep_record",
         label="Their erasure requests",
         reason="The record that the person asked for erasure and how it was decided (ids, statuses, decline "
                "codes, dates — never a name or free text); the queue shows the person as \"Erased user\""),
    _row("subject_erasure_requests", "decided_by_id", export_as="none", category=None, erase="reference",
         label="Erasure requests they decided", reason=_REF),
)

_NOT_ACCOUNT = ("Out of scope: a CI- or admin-supplied handle / contact, not TestLookup account data "
                "(a GitHub handle is not a username; the guide says so — D-W31-13)")

# ── E. The other stores, the one non-ORM table, and what is out of scope ──────────────────────────────────
_E_POSTGRES: tuple[Location, ...] = (
    _row("auth_token_revocations", "user_id", export_as="none", category=None, erase="keep_record",
         label="The sign-out cutoff", where="non-ORM table (migration 0159; core/token_revocation.py)",
         reason="The sign-out itself: the time before which every token of this account is refused (an id and a "
                "time, no name)"),
    _row("test_runs", "ci_actor", match="value", export_as="none", category=None, erase="out_of_scope",
         label="CI actor on runs", reason=_NOT_ACCOUNT),
    _row("test_cases", "owner", match="value", export_as="none", category=None, erase="out_of_scope",
         label="Test-owner labels", reason=_NOT_ACCOUNT),
    _row("projects", "component_owner_map", match="value", export_as="none", category=None, erase="out_of_scope",
         label="Component owner maps", reason=_NOT_ACCOUNT),
    _row("service_ownership_rules", "team_contact", match="value", export_as="none", category=None,
         erase="out_of_scope", label="Team contacts on ownership rules", reason=_NOT_ACCOUNT),
)
_E_OBJECTS: tuple[Location, ...] = (
    _store("objects:subject-exports/", "objects", match="id_key", erase="delete", label="Their data exports",
           # subject_export_service.remove_exports_of: each export job's object removed, the job "expired"
           reason="Their export archives are removed and the export jobs read \"expired\""),
    _store("objects:project-exports/", "objects", match="content", erase="delete",
           label="Project exports that list them",
           # a live export whose directory/users.jsonl (W37: or members / suite-owner list) lists them: object
           # removed, job "expired", error_code subject_erased — their e-mail and member line leave with it
           reason="Project exports still in storage that list them — with their e-mail address when the export "
                  "included members — are removed; export the project again"),
    _store("objects:project-imports/", "objects", match="content", erase="keep_record",
           label="Uploaded project archives",
           # W24's janitor removes it at its 1-hour TTL; never re-read
           reason="An uploaded project archive may list them, with their e-mail address when it includes members; "
                  "it is removed within the hour, unread"),
    _store("objects:exports/", "objects", match="content", erase="keep_audit", label="Retention archives",
           # the manifest's requested_by.name; gone at the 72-hour collect TTL
           reason="Art. 17(3)(e): signed deletion evidence that names who asked; removed when its 72-hour download "
                  "window ends"),
    _store("objects:compliance/", "objects", match="content", erase="keep_audit", label="Compliance packs",
           reason="Art. 17(3)(e): signed release evidence with audit actor names; rewriting breaks the signature"),
    _store("objects:report-exports/", "objects", match="none", erase="no_personal_data", label="Summary-report exports",
           reason="Reports carry run and test figures, no account names"),
    _store("objects:training-exports", "objects", match="none", erase="no_personal_data", label="Training exports",
           reason="Redacted training rows, no account names"),  # training/exporter.py
    _store("objects:pipeline/", "objects", match="none", erase="out_of_scope",
           label="Pipeline artefacts, ingest uploads, knowledge documents",
           reason="Out of scope: CI / test output, not account data; retention purges it"),
)
_MONGO_COLLECTIONS = (
    "raw_allure_json", "rest_api_payloads", "ai_analysis_payloads", "run_summaries", "decision_evidence_snapshots",
    "decision_reports", "decision_report_attempts", "live_execution_events", "ai_run_versions", "pipeline_event_log",
)
_E_MONGO: tuple[Location, ...] = tuple(
    _store(f"mongo:{name}", "mongo", match="none", erase="no_personal_data", label=f"Documents in {name}",
           reason="No writer stores a user id, username or e-mail here; the erasure's sentinel scan proves it")
    for name in _MONGO_COLLECTIONS
)
_E_REDIS: tuple[Location, ...] = (
    _store("redis:membership:{id}", "redis", match="id_key", erase="delete", label="Cached project memberships",
           reason="Deleted in the redis phase (best effort; expires within 5 minutes anyway)"),
    _store("redis:user:{id}", "redis", match="id_key", erase="delete", label="Cached reads",
           reason="Deleted in the redis phase (best effort; expires within 5 minutes anyway)"),
    _store("redis:auth:tokens_valid_from:{id}", "redis", match="id_key", erase="keep_record",
           label="Legacy sign-out cutoff", reason="An id and a time only; it expires on its own"),
    _store("redis:testlookup:idempotency:{id}:*", "redis", match="id_key", erase="keep_record",
           label="Request idempotency keys", reason="Ids only; they expire on their own"),
    _store("redis:dlq requested_by", "redis", match="id_key", erase="keep_record",
           label="Dead-letter entries they requested", reason="The pseudonymous id only; the oldest are dropped"),
    # worker/tasks.py notify_test_suite_owner: the recipient's e-mail inside a 300-second dedup key
    _store("redis:testlookup:dedup:notify_owner:*", "redis", match="value", erase="keep_record",
           label="Owner-notification dedup keys",
           reason="Their e-mail address inside a key that stops a notification being sent twice; it expires on its "
                  "own within 5 minutes"),
    # services/ml/training_job.py: "Train now" stores triggered_by = "user:<username>" (lock 1 h, last run 30 days).
    # Matched on the OLD username, so the rewrite runs while the Postgres phase still holds it (best effort).
    _store("redis:ml:retrain:last", "redis", match="value", erase="pseudonymise",
           label="The last model training they started",
           # triggered_by "user:<old username>" -> "user:<pseudonym>" (KEEPTTL)
           reason="It names who started it; that now reads as the new username"),
    _store("redis:ml:retrain:lock", "redis", match="value", erase="pseudonymise",
           label="A model training they queued",
           # triggered_by "user:<old username>" -> "user:<pseudonym>" (KEEPTTL; the lock lives <= 1 hour)
           reason="It names who queued it; that now reads as the new username (it lasts at most an hour)"),
)
_E_OTHER: tuple[Location, ...] = (
    _store("chroma:*", "chroma", match="none", erase="no_personal_data", label="Search and memory embeddings",
           reason="The 7 search-index families hold embeddings and run and test details, no person's name"),
    _store("logs", "logs", match="none", erase="out_of_scope", label="Server log lines",
           reason="Out of scope: the operator's log retention; going forward no line carries a name (E-10)"),
)

#: §2.1, in preview order: deletes, rewrites, references, kept records, the other stores.
INVENTORY: tuple[Location, ...] = (
    _A + _B + _D_OWN_WORDS + _D_WORK + _D_AI + _D_CONFIG + _D_RECORDS + _SUBJECT_JOBS + _C
    + _E_POSTGRES + _E_OBJECTS + _E_MONGO + _E_REDIS + _E_OTHER
)

#: Tables that name a user but are not in ``Base.metadata`` (raw SQL only) → why they are covered.
NON_ORM_TABLES: dict[str, str] = {
    "auth_token_revocations": "migration 0159; core/token_revocation.py reads and writes it by raw SQL — user_id + "
                              "jti + valid_from (ids and times); classified as auth_token_revocations.user_id",
}

#: Name-like columns (the §2.1 pattern) that are NOT a person's data → why.
_TEST = "a test / suite / class / package name from CI — not a person"
_KIND = "a kind / trigger / source vocabulary value — not a person"
_LEASE = "a worker lease id — not a person"
_MODEL = "a model / agent / stage / provider name — not a person"
_TEAM = "a team / service / repository / group name — not a person"
NOT_A_PERSON: dict[str, str] = {
    "agent_investigations.triggered_by": f"{_KIND} (manual | auto:newly_failing | auto:gate_no_go)",
    "agent_invocations.stage_name": _MODEL,
    "agent_pipeline_runs.lease_owner": _LEASE,
    "agent_stage_results.stage_name": _MODEL,
    "ai_eval_baselines.agent_name": _MODEL,
    "ai_eval_baselines.model_name": _MODEL,
    "ai_eval_runs.model_name": _MODEL,
    "canonical_test_cases.test_name": _TEST,
    "canonical_test_cases.class_name": _TEST,
    "defect_candidates.owner_team": _TEAM,
    "defects.owner_team": _TEAM,
    "digest_subscriptions.saved_view_name": "a saved view's name; the row is the person's own digest, deleted with "
                                            "digest_subscriptions.user_id",
    "fix_attempts.test_name": _TEST,
    "flaky_coach_results.test_name": _TEST,
    "flaky_coach_results.suite_name": _TEST,
    "flaky_detection_state.test_name": _TEST,
    "flaky_quarantine_requests.test_name": _TEST,
    "flaky_quarantine_requests.suite_name": _TEST,
    "flaky_quarantine_requests.reviewer_notes": "free text the reviewer wrote — the project's record (E-6); its author "
                                                "is the approved_by / rejected_by reference",
    "flaky_score.test_name": _TEST,
    "github_integrations.repo_owner": _TEAM,
    "github_integrations.repo_name": _TEAM,
    "live_sessions.client_name": "the CI client's name (pytest plugin, SDK) — not a person",
    "live_sessions.release_name": "a release name — not a person",
    "live_sessions.launch_name": "a launch name — not a person",
    "live_sessions.suite_name": _TEST,
    "managed_test_cases.suite_name": _TEST,
    "model_versions.model_name": _MODEL,
    "perf_baselines.test_name": _TEST,
    "perf_baselines.suite_name": _TEST,
    "product_usage_events.event_name": "a product event name — not a person",
    "project_activity_events.actor_type": f"{_KIND} (user | system | agent | api_key | service_account)",
    "project_data_jobs.project_name": "a project's name — not a person",
    "release_attribution_rules.target_release_name": "a release name — not a person",
    "review_requests.created_by": f"{_KIND} (\"system\" — every writer: review_request_service, online_drift_service)",
    "review_requests.superseded_by": "a review request id — not a person",
    "run_cadences.suite_name": _TEST,
    "run_cadences.expected_by": "a time of day — not a person",
    "run_comparison_reports.suite_name": _TEST,
    "run_comparison_reports.model_name": _MODEL,
    "scim_groups.display_name": _TEAM,
    "semantic_reindex_jobs.lease_owner": _LEASE,
    "service_ownership_rules.service_name": _TEAM,
    "service_ownership_rules.team_name": _TEAM,
    "sso_configurations.display_name": "an identity provider's name — not a person",
    "suite_executions.suite_name": _TEST,
    "suite_membership_events.suite_name": _TEST,
    "suite_membership_events.test_name": _TEST,
    "suite_memberships.suite_name": _TEST,
    "suite_memberships.test_name": _TEST,
    "suite_memberships.class_name": _TEST,
    "suite_run_reviews.suite_name": _TEST,
    "systemic_flake_cluster_member.test_name": _TEST,
    "team_notification_channels.team_name": _TEAM,
    "test_case_versions.suite_name": _TEST,
    "test_cases.test_name": _TEST,
    "test_cases.full_name": f"{_TEST} (the test's fully qualified name)",
    "test_cases.suite_name": _TEST,
    "test_cases.class_name": _TEST,
    "test_cases.package_name": _TEST,
    "test_cases.service_name": _TEAM,
    "test_health_recommendations.test_name": _TEST,
    "test_runs.ocp_pod_name": "a Kubernetes pod name — not a person",
    "test_runs.primary_suite_name": _TEST,
    "test_suite_owners.suite_name": _TEST,
    "timeline_notes.suite_name": _TEST,
}

#: Name-like columns that DO hold the person but travel with a row another location already classifies →
#: that location's key (the row is deleted / exported / kept with it).
COVERED_BY: dict[str, str] = {
    "federated_identities.external_email": "federated_identities.user_id",
    "federated_identities.external_display_name": "federated_identities.user_id",
    "notification_preferences.email_override": "notification_preferences.user_id",
    "users.email": "users.*",
    "users.username": "users.*",
    "users.full_name": "users.*",
    "users.avatar_color": "users.*",
}

_SET = "settings_audit_log.actor_name"
_ACC = "access_audit_logs.actor_name"
_IDE = "identity_events.actor_name"
#: Every place ``backend/app`` copies a username / e-mail / full name into a persisted target (the guard's AST
#: scan, check 3: a model constructor keyword ``Model.kw``, a snapshot-named keyword ``kw:<name>`` of a writer
#: helper, an attribute ``attr:<name>`` of a fetched row, a ``dict:<key>`` entry of a JSON value) → the location
#: that classifies what it writes. A NEW writer is red until it is classified here.
WRITERS: dict[tuple[str, str], str] = {
    ("app/routers/activity.py", "AccessAuditLog.actor_name"): _ACC,
    ("app/routers/agent_investigations.py", "kw:cancelled_by"): "agent_investigations.cancelled_by",
    ("app/routers/agent_invoke.py", "kw:requested_by"): "agent_pipeline_runs.error",
    ("app/routers/agents.py", "kw:requested_by"): "agent_pipeline_runs.error",
    ("app/routers/analytics.py", "kw:triggered_by"): "transient: a Celery task argument rendered into the owner "
                                                     "e-mail's \"Flagged by\" line; never stored",
    ("app/routers/api_keys.py", "kw:actor_name"): _IDE,
    ("app/routers/app_settings.py", "kw:actor_name"): _IDE,
    ("app/routers/auth.py", "User.email"): "users.*",
    ("app/routers/auth.py", "User.full_name"): "users.*",
    ("app/routers/auth.py", "User.username"): "users.*",
    ("app/routers/auth.py", "attr:full_name"): "users.*",
    ("app/routers/mfa.py", "kw:actor_name"): _IDE,
    ("app/routers/projects.py", "kw:actor_name"): _SET,
    ("app/routers/releases.py", "AccessAuditLog.actor_name"): _ACC,
    ("app/routers/reports.py", "AccessAuditLog.actor_name"): _ACC,
    ("app/routers/retention.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/routers/retention.py", "kw:actor_name"): "retention_holds.placed_by_name",
    ("app/routers/scim.py", "kw:actor_name"): _IDE,
    ("app/routers/scim_groups_admin.py", "kw:actor_name"): _IDE,
    ("app/routers/sso.py", "kw:actor_name"): _IDE,
    ("app/routers/summary_report.py", "AccessAuditLog.actor_name"): _ACC,
    ("app/routers/users.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/routers/users.py", "User.email"): "users.*",
    ("app/routers/users.py", "User.full_name"): "users.*",
    ("app/routers/users.py", "User.username"): "users.*",
    ("app/routers/users.py", "UserInvitation.email"): "user_invitations.email",
    ("app/routers/users.py", "attr:email"): "users.*",
    ("app/routers/users.py", "attr:full_name"): "users.*",
    ("app/routers/users.py", "attr:username"): "users.*",
    ("app/services/access_audit_service.py", "AccessAuditLog.actor_name"): _ACC,
    ("app/services/activity/mentions.py", "NotificationLog.title"): "notification_logs.title",
    ("app/services/activity/service.py", "kw:actor_name"): "project_activity_events.actor_name",
    ("app/services/agent_action_ledger_service.py", "dict:approved_by"): "project_activity_events.actor_id",
    ("app/services/compliance_pack_service.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/services/compliance_signing.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/services/defect_jira_service.py", "AccessAuditLog.actor_name"): _ACC,
    ("app/services/defect_jira_service.py", "dict:requested_by"): "webhook_deliveries.event_payload",
    ("app/services/feature_flags.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/services/flaky_quarantine_service.py", "kw:actor_name"): _SET,
    ("app/services/github_checks_service.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/services/gitlab_integration_service.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/services/insights_service.py", "attr:dismissed_by_name"): "project_insights.dismissed_by_name",
    ("app/services/llm_cost_budget.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/services/release_council_service.py", "dict:actor_name"): "release_decisions.override_audit",
    ("app/services/release_service.py", "AccessAuditLog.actor_name"): _ACC,
    ("app/services/report_export_service.py", "AccessAuditLog.actor_name"): _ACC,
    ("app/services/retention_hold_service.py", "RetentionHold.placed_by_name"): "retention_holds.placed_by_name",
    ("app/services/retention_hold_service.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/services/retention_hold_service.py", "attr:released_by_name"): "retention_holds.released_by_name",
    ("app/services/retention_instance_service.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/services/settings_audit_service.py", "SettingsAuditLog.actor_name"): _SET,
    ("app/services/share_link_service.py", "ReportShareLink.created_by_name"): "report_share_links.created_by_name",
    ("app/services/test_management_audit_service.py", "TestCaseAuditLog.actor_name"): "test_case_audit_logs.actor_name",
    ("app/services/webhook_service.py", "kw:actor_name"): _SET,
}
#: A WRITERS value that is not a location key starts with this: the value is never persisted.
TRANSIENT = "transient: "
#: Functions that build a RESPONSE (read-time name resolvers), never a persisted value: the scan skips them.
READ_RESOLVERS = frozenset({
    "requester_names", "displays", "erased_display", "read_defaults",
})


def by_key(key: str) -> Location:
    """The location keyed ``key`` (KeyError for an unknown key)."""
    for location in INVENTORY:
        if location.key == key:
            return location
    raise KeyError(key)


def postgres_locations(*, erase: Optional[str] = None, export: bool = False) -> tuple[Location, ...]:
    """The Postgres locations in inventory order — only those erased with ``erase`` when given, only the
    exported ones (``export_as != "none"``) when ``export``."""
    return tuple(
        location for location in INVENTORY
        if location.store == "postgres"
        and (erase is None or location.erase == erase)
        and (not export or location.export_as != "none")
    )
