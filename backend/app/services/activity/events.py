"""The frozen activity-event registry.

Every event the ledger can hold is declared here, once. A producer cannot emit
a name that is not in this table: :func:`lookup` raises
:class:`UnknownActivityEvent`, and the write path turns that into a counted
drop in production rather than a 500 on the user's mutation.

Why a Python registry and not a database enum
---------------------------------------------
``project_activity_events.event_type`` is a ``VARCHAR(60)`` precisely so that
adding an event is a code change, not a migration. The trade-off is that the
column can drift out of the vocabulary, which is the documented
enum-over-String trap in this codebase — so the vocabulary is validated on the
way IN (here) instead of being trusted on the way out. A row whose event_type
was later removed from the registry still reads back fine; it renders with its
raw name and the ``unknown`` category.

Two hard rules, both test-enforced in ``tests/test_activity_events.py``
----------------------------------------------------------------------
1. Names shared with the outbound webhook catalog
   (``webhook_service.SUPPORTED_EVENTS``) are byte-identical, so one vocabulary
   serves webhooks, notifications and the ledger. A parity test fails if they
   drift.
2. No ``ws_*`` and no ``heartbeat*`` names, ever. Connection churn at the same
   rank as a role change is exactly what made the existing Audit Dashboard
   unreadable — observed live at ~60% of the first page.

Deliberately absent: ``project.deleted``. The ledger cascades on project
delete, so the row would vanish with the thing it records. That event is
written to ``access_audit_logs`` (which uses ON DELETE SET NULL) instead. See
``routers/projects.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Mapping, Optional

# ── Vocabularies ─────────────────────────────────────────────────────────────
# These are mirrored by CHECK constraints in migration 0165. Keep them in sync:
# `tests/test_activity_events.py::test_category_vocab_matches_migration` reads
# the migration source and compares.

ACTIVITY_CATEGORIES: Final[tuple[str, ...]] = (
    "runs",
    "analysis",
    "release",
    "quality",
    "configuration",
    "membership",
    "test_management",
    "integration",
    "agent",
    "system",
)

ACTOR_TYPES: Final[tuple[str, ...]] = (
    "user",
    "api_key",
    "service_account",
    "system",
    "agent",
)

ENTITY_TYPES: Final[tuple[str, ...]] = (
    "run",
    "test",
    "suite",
    "release",
    "policy",
    "attribution_rule",
    "ownership_rule",
    "quarantine",
    "defect",
    "project",
    "member",
    "integration",
    "webhook",
    "api_key",
    "retention_policy",
    "saved_view",
    "knowledge_source",
    "agent_action",
    "workflow",
    "export",
    # W07 FAIL-10: a QA lead's "<field> contains <text> -> <category>" rule.
    "classification_hint",
    # W08 TRND-04: a note pinned on a project's daily charts (a UTC day range).
    "timeline_note",
    # W09 NTF-02: a project's notification policy (one row per project; the
    # entity id is the project's).
    "notification_policy",
    # W09 FLK-02: a project's quarantine lifecycle policy (one row per project).
    "quarantine_policy",
    # W28 TCM-04: one authored test case (managed_test_cases.id). New, rather
    # than ``test``: that type is shared with flaky.detected / test.newly_failing
    # and links with ``?test=``, which /test-management does not read.
    "test_case",
    # W28 TCM-03: a project's test-case library as a whole (the entity id is
    # the project's), for a bulk edit across many cases.
    "test_library",
    # W22a ACT-05: one retention hold (retention_holds.id) on a project, a
    # release, a run or a compliance pack.
    "retention_hold",
    # W23a FAIL-11: one cross-run insight (project_insights.id), an episode of
    # one detector firing on one subject.
    "insight",
    # W23a TRND-03: one expected run cadence (run_cadences.id).
    "run_cadence",
)

#: Where a row of each entity type lives in the SPA. Used to build
#: ``entity.href`` server-side so the CLI and MCP tools get working links too,
#: instead of every client re-deriving the route map and drifting.
_ENTITY_ROUTES: Final[Mapping[str, str]] = {
    "run": "/runs/{id}",
    "test": "/test-management?test={id}",
    "suite": "/suites/{id}",
    # W05 REL-04: the release's own page. ``/releases?release={id}`` opened
    # nothing: the releases page ignores a ``release`` query parameter.
    "release": "/releases/{id}",
    "policy": "/policies/{id}",
    # W11 REL-09: the attribution rules page (one page lists every rule of the
    # project, so the link carries no id). Before W11 this was "" -- no page
    # rendered the rules, and a link to a missing route bounces to /overview.
    "attribution_rule": "/releases/attribution-rules",
    "ownership_rule": "/ownership",
    "quarantine": "/quarantine",
    "defect": "/defects?defect={id}",
    "project": "/projects",
    "member": "/users",
    "integration": "/settings/integrations",
    "webhook": "/settings/webhooks",
    "api_key": "/settings/api-keys",
    "retention_policy": "/settings/retention",
    "saved_view": "/search",
    "knowledge_source": "/settings/ai",
    "agent_action": "/settings/agent-activity",
    # Workflow definitions do not have a UI until E3.4. Keep activity entries
    # as plain text rather than linking to a route that does not exist yet.
    "workflow": "",
    "export": "",
    # W07 FAIL-10: hints are listed and edited from the Failures › Categories
    # tab's "Classification hints" modal; there is no per-hint page.
    "classification_hint": "/failures?tab=categories",
    # W08 TRND-04: notes are drawn on the Trends hero chart (and the Coverage
    # history tab) and listed from its "Notes (n)" chip; there is no per-note page.
    "timeline_note": "/trends",
    # W09 NTF-02: the "Project notification policy" card of Settings > Email
    # (SMTP) & channels (one policy per project; the card reads the top bar's).
    "notification_policy": "/settings/notifications",
    # W09 FLK-02: the Policy tab of /flaky.
    "quarantine_policy": "/flaky?tab=policy",
    # W28: /test-management opens a case from ``?case=`` (the Cases tab).
    "test_case": "/test-management?case={id}",
    "test_library": "/test-management",
    # W22a ACT-05: the Holds card of Settings › Retention lists every hold of
    # the project; there is no per-hold page.
    "retention_hold": "/settings/retention",
    # W23a FAIL-11: Inbox › Insights lists every insight of the project (the
    # Overview card shows five); there is no per-insight page.
    "insight": "/my-failures?tab=insights",
    # W23a TRND-03: Settings › Project › Expected runs lists every cadence of
    # the project; there is no per-cadence page.
    "run_cadence": "/settings/expected-runs",
}

_EVENT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z_]+\.[a-z_]+$")

_ALL_ACTORS: Final[frozenset[str]] = frozenset(ACTOR_TYPES)
_HUMAN_OR_KEY: Final[frozenset[str]] = frozenset({"user", "api_key", "service_account"})
_ANY_WRITER: Final[frozenset[str]] = frozenset(
    {"user", "api_key", "service_account", "system"}
)


class UnknownActivityEvent(KeyError):
    """Raised when a producer emits an event_type that is not registered."""


@dataclass(frozen=True, slots=True)
class ActivityEventSpec:
    """One registered event.

    ``write_mode`` picks the durability contract, and the choice is per event
    rather than per caller:

    * ``outcome`` — the ledger row shares the caller's transaction, so it dies
      with a rollback. Right when the row only makes sense if the primary
      mutation actually succeeded ("policy updated").
    * ``attempt`` — the ledger row commits on its own session and survives a
      caller rollback. Right when the ATTEMPT is the thing worth recording
      ("a full project reset was issued"), even if it later fails midway.
    """

    event_type: str
    category: str
    entity_type: str
    summary_template: str
    write_mode: str = "outcome"
    actor_types: frozenset[str] = _ALL_ACTORS


def _spec(
    event_type: str,
    category: str,
    entity_type: str,
    summary_template: str,
    write_mode: str = "outcome",
    actor_types: frozenset[str] = _ALL_ACTORS,
) -> ActivityEventSpec:
    return ActivityEventSpec(
        event_type=event_type,
        category=category,
        entity_type=entity_type,
        summary_template=summary_template,
        write_mode=write_mode,
        actor_types=actor_types,
    )


# ── The registry ─────────────────────────────────────────────────────────────
# Ordered by category so a reader can scan one domain at a time. Summary
# templates address the reader of the feed, not the developer: they name what
# happened in the words the UI already uses elsewhere.

_SPECS: tuple[ActivityEventSpec, ...] = (
    # ── runs ────────────────────────────────────────────────────────────────
    _spec("run.received", "runs", "run", "Report received for {entity_label}", "attempt"),
    _spec("run.completed", "runs", "run", "{entity_label} completed — {failed} failed of {total}"),
    _spec("run.ingest_failed", "runs", "run", "Ingestion failed for {entity_label}: {error}", "attempt"),
    _spec("run.deleted", "runs", "run", "{entity_label} was deleted", "attempt"),
    _spec("run.release_attached", "runs", "run", "{entity_label} was attached to release {release_name}"),
    _spec("run.live_started", "runs", "run", "Live run {entity_label} started"),
    _spec("run.live_finalized", "runs", "run", "Live run {entity_label} finalized"),
    _spec("run.live_recovered", "runs", "run", "Live run {entity_label} was recovered", "attempt"),
    # W23a TRND-03: staged by run_cadence_service._stage_missed_alerts on the
    # cadence beat's session (outcome: the row commits with the decided
    # windows), ONE row per cadence per check naming every newly missed window.
    # ``days`` is the local labels ("Tue 27 Oct, Wed 28 Oct"). System only.
    _spec("run_cadence.missed", "runs", "run_cadence", "{count} expected run(s) of {entity_label} missed ({days})", "outcome", frozenset({"system"})),
    # ── analysis ────────────────────────────────────────────────────────────
    # AIH-01/06: emitted by ai_history_service.capture_analysis_digest once per
    # NEW digest version (never on an idempotent pipeline replay), as outcome
    # rows on the pipeline task's session.
    _spec("analysis.completed", "analysis", "run", "AI analysis v{version} completed for {entity_label}"),
    _spec("analysis.verdict_changed", "analysis", "run", "AI verdict for {entity_label} changed {from_verdict} -> {to_verdict}"),
    _spec("analysis.failed", "analysis", "run", "AI analysis failed for {entity_label}: {error}"),
    # An attempt write: its producer (decision_report_service.publish_decision_report)
    # holds only a Mongo handle, and record(None, ...) of an outcome event is dropped.
    _spec("analysis.report_superseded", "analysis", "run", "Decision report for {entity_label} was superseded by v{version}", "attempt"),
    _spec("investigation.started", "analysis", "run", "Deep investigation started for {entity_label}"),
    _spec("investigation.completed", "analysis", "run", "Deep investigation completed for {entity_label}"),
    _spec("analysis.triggered", "analysis", "run", "AI analysis was requested for {entity_label}", "attempt"),
    _spec("analysis.bulk_triggered", "analysis", "project", "AI analysis was requested for {count} runs", "attempt"),
    _spec("analysis.regression_watch_run", "analysis", "run", "Regression Watchman classified the failures in {entity_label}", "attempt"),
    # E7.4. Both are "attempt" writes: the operator asked, and the ask is the
    # thing worth recording even if the dispatch or the worker's own stop later
    # fails. A cancel in particular must leave a trace precisely when it did
    # NOT tidily succeed.
    _spec("analysis.retried", "analysis", "run", "AI analysis for {entity_label} was retried ({mode})", "attempt"),
    _spec("analysis.cancelled", "analysis", "run", "AI analysis for {entity_label} was cancelled", "attempt"),
    # W14 AGT-12: a tracked bulk job was cancelled (one row per project of the
    # job). An attempt write, like the bulk trigger it cancels: the cancel
    # route never commits, and the ask is the fact worth recording.
    _spec("analysis.bulk_cancelled", "analysis", "project", "Bulk AI analysis {bulk_job_id} was cancelled", "attempt"),
    # E1.2: one agent invoked through the public API. An attempt write, like a
    # trigger: the request is worth recording whatever the worker does next.
    _spec("agent.invoked", "analysis", "run", "Agent {agent_id} was invoked on {entity_label}", "attempt"),
    # E8.2: a human settled the review of an AI report. Outcome writes: the row
    # commits with the review itself, and a decision that rolled back never
    # happened. Humans only -- the API refuses keys and synthetic accounts.
    _spec("review.accepted", "analysis", "run", "{actor_name} accepted the AI report for {entity_label}", "outcome", _HUMAN_OR_KEY),
    _spec("review.rejected", "analysis", "run", "{actor_name} rejected the AI report for {entity_label} ({reason_code})", "outcome", _HUMAN_OR_KEY),
    # ── release ─────────────────────────────────────────────────────────────
    _spec("release.created", "release", "release", "Release {entity_label} was created"),
    _spec("release.activated", "release", "release", "Release {entity_label} was activated"),
    _spec("release.closed", "release", "release", "Release {entity_label} was closed"),
    _spec("release.decided", "release", "release", "Release {entity_label}: {recommendation}"),
    _spec("release.outcome_marked", "release", "release", "Release {entity_label} marked as {outcome}"),
    _spec("release.decision_overridden", "release", "release", "Release decision for {entity_label} overridden to {recommendation}", "attempt"),
    _spec("release.phase_advanced", "release", "release", "Release {entity_label} advanced to {phase}"),
    # W05 REL-10: emitted by ``release_service._enforce_phase_gate`` beside its
    # access-audit row, in the request's own transaction (outcome mode: no
    # second session inside a guarded handler, and a rolled-back phase change
    # leaves no row). The reason the user typed is the point of the entry.
    _spec("release.phase_skipped", "release", "release", "Phase {phase} skipped on release {entity_label}: {reason}"),
    _spec("release.phase_gate_overridden", "release", "release", "Phase gate {phase} overridden on release {entity_label}: {reason}"),
    _spec("release.linked_run", "release", "release", "A run was linked to release {entity_label}"),
    _spec("release.unlinked_run", "release", "release", "A run was unlinked from release {entity_label}"),
    _spec("release.synced_external", "release", "release", "Release {entity_label} synced from {provider}"),
    # CRUD, distinct from the lifecycle events above. A release's name, dates
    # and status are what a gate decision is read against later, so "who
    # changed this release, and to what" is exactly the question the feed
    # exists to answer.
    _spec("release.updated", "release", "release", "Release {entity_label} changed: {changed}"),
    _spec("release.deleted", "release", "release", "Release {entity_label} was deleted", "attempt"),
    _spec("release.phase_added", "release", "release", "Phase {phase} was added to release {entity_label}"),
    _spec("release.phase_updated", "release", "release", "Phase {phase} on release {entity_label} changed: {changed}"),
    _spec("release.phase_deleted", "release", "release", "Phase {phase} was removed from release {entity_label}"),
    # W11 REL-05 / REL-07: a first schedule created in one request -- copied
    # from another release of the project, or created from a reviewed draft
    # (``source`` rules | llm | manual). Outcome mode: staged on the request
    # session and committed by the router with the phases themselves.
    _spec("release.phases_cloned", "release", "release",
          "Release {entity_label}: {count} phases copied from {source_release}"),
    _spec("release.phases_generated", "release", "release",
          "Release {entity_label}: {count} phases created ({source})"),
    # ── quality ─────────────────────────────────────────────────────────────
    _spec("quarantine.requested", "quality", "quarantine", "Quarantine requested for {entity_label}", "attempt"),
    _spec("quarantine.approved", "quality", "quarantine", "Quarantine approved for {entity_label}", "attempt"),
    _spec("quarantine.rejected", "quality", "quarantine", "Quarantine rejected for {entity_label}", "attempt"),
    _spec("quarantine.released", "quality", "quarantine", "{entity_label} was released from quarantine", "attempt"),
    _spec("defect.promoted", "quality", "defect", "Failure cluster promoted to defect {entity_label}"),
    _spec("defect.create_requested", "quality", "defect", "Defect ticket requested for {entity_label}"),
    _spec("defect.ticket_created", "quality", "defect", "Ticket {ticket_key} created for {entity_label}"),
    _spec("defect.commander_run", "quality", "run", "Defect Commander drafted a defect from a failure cluster in {entity_label}", "attempt"),
    # DEF-01 triage (W06): a person changed the status, the assignee, the
    # owner team, or removed the Jira link (``changed`` names each part, in
    # labels and names, never user ids); a review rejection is a status change.
    _spec("defect.updated", "quality", "defect", "Defect {entity_label} changed: {changed}"),
    _spec("defect.ticket_linked", "quality", "defect", "Defect {entity_label} linked to Jira {ticket_key}"),
    # W21 ACT-01 (c): no ``flaky.detected`` -- flakiness is a continuous score with no
    # detected/not verdict to emit on; removed rather than left as a dead filter.
    _spec("test.newly_failing", "quality", "test", "{entity_label} started failing"),
    _spec("test.recovered", "quality", "test", "{entity_label} recovered"),
    # W08 TRND-04: staged by routers/timeline_notes.py on the request session
    # (outcome: a rolled-back write leaves no row). ``entity_label`` is the
    # note's first 60 characters; ``updated`` only when a field really changed,
    # ``changed`` naming them (text, dates, suite).
    _spec("timeline_note.created", "quality", "timeline_note", 'Note "{entity_label}" was pinned'),
    _spec("timeline_note.updated", "quality", "timeline_note", 'Note "{entity_label}" changed: {changed}'),
    _spec("timeline_note.deleted", "quality", "timeline_note", 'Note "{entity_label}" was removed'),
    # W23a FAIL-11: staged by routers/insights.py's dismiss on the request
    # session (outcome), only when the insight was not dismissed already.
    # ``entity_label`` is the insight's subject label.
    _spec("insight.dismissed", "quality", "insight", "{actor_name} dismissed the insight: {entity_label}", "outcome", _HUMAN_OR_KEY),
    # ── configuration ───────────────────────────────────────────────────────
    _spec("project.created", "configuration", "project", "Project {entity_label} was created"),
    _spec("project.updated", "configuration", "project", "Project settings changed: {changed}"),
    # W17 ADM-04: archive hides the project and revokes its keys; restore brings it
    # back (ADMIN) with the keys and the default QA lead still revoked.
    _spec("project.archived", "configuration", "project", "Project {entity_label} was archived"),
    _spec("project.restored", "configuration", "project", "Project {entity_label} was restored"),
    _spec("project.reset", "configuration", "project", "Project data reset ({mode}) was issued", "attempt"),
    _spec("policy.created", "configuration", "policy", "Release gate policy {entity_label} was created"),
    _spec("policy.updated", "configuration", "policy", "Release gate policy {entity_label} changed: {changed}"),
    # W21 ACT-01: no ``policy.deleted`` -- gate policies are versioned and
    # never deleted (a superseded version is deactivated, which records
    # ``policy.updated``), so the name was removed rather than left as a
    # filter that matches nothing.
    _spec("policy.activated", "configuration", "policy", "Release gate policy {entity_label} was activated"),
    _spec("agent_config.updated", "configuration", "project", "Agent {entity_label} configuration changed to version {config_version}: {changed}", "outcome", _HUMAN_OR_KEY),
    _spec("workflow.created", "configuration", "workflow", "Workflow {entity_label} version {version} was created", "outcome", _HUMAN_OR_KEY),
    _spec("workflow.updated", "configuration", "workflow", "Workflow {entity_label} version {version} was updated", "outcome", _HUMAN_OR_KEY),
    _spec("workflow.deleted", "configuration", "workflow", "Workflow {entity_label} version {version} was deleted", "outcome", _HUMAN_OR_KEY),
    _spec("workflow.evaluated", "configuration", "workflow", "Workflow {entity_label} version {version} evaluation {verdict} at {coverage} coverage", "outcome", _HUMAN_OR_KEY),
    _spec("workflow.published", "configuration", "workflow", "Workflow {entity_label} version {version} was published", "outcome", _HUMAN_OR_KEY),
    _spec("workflow.forked", "configuration", "workflow", "Workflow {entity_label} was forked from {source}", "outcome", _HUMAN_OR_KEY),
    # W14 AGT-08: a project's default workflow per base. Outcome writes staged
    # on the request session and committed by the defaults routes.
    _spec("workflow.default_set", "configuration", "workflow", "Workflow {entity_label} is the {base} default for new runs", "outcome", _HUMAN_OR_KEY),
    _spec("workflow.default_cleared", "configuration", "workflow", "The {base} default workflow {entity_label} was cleared", "outcome", _HUMAN_OR_KEY),
    _spec("ai_eval.tier_compared", "configuration", "project", "Agent {entity_label} tier comparison {verdict} with {sample_count} paired samples", "outcome", _HUMAN_OR_KEY),
    _spec("ai_eval.reviewer_evaluated", "configuration", "project", "Agent {entity_label} reviewer evaluation {verdict} with {sample_count} semantic samples", "outcome", _HUMAN_OR_KEY),
    # W16 AGT-09 / AGT-13. Outcome writes on the route's one commit: the
    # candidate run row (POST /ai-eval/runs/evaluate/{dataset_id} with a body;
    # entity = the body's project, label = the dataset name) and a person's
    # shadow-pair label (entity = the pair's project, label = the agent label).
    _spec("ai_eval.candidate_requested", "configuration", "project", "Candidate evaluation of {entity_label} queued: {tier} on {item_count} items", "outcome", _HUMAN_OR_KEY),
    _spec("ai_eval.shadow_pair_labelled", "configuration", "project", "A {entity_label} shadow pair was labelled: {verdict}", "outcome", _HUMAN_OR_KEY),
    _spec("attribution_rule.created", "configuration", "attribution_rule", "Attribution rule {entity_label} was created"),
    _spec("attribution_rule.updated", "configuration", "attribution_rule", "Attribution rule {entity_label} changed: {changed}"),
    _spec("attribution_rule.deleted", "configuration", "attribution_rule", "Attribution rule {entity_label} was deleted"),
    _spec("attribution_rule.enabled", "configuration", "attribution_rule", "Attribution rule {entity_label} was enabled"),
    _spec("attribution_rule.disabled", "configuration", "attribution_rule", "Attribution rule {entity_label} was disabled"),
    # W07 FAIL-10: staged by routers/classification_hints.py on the request
    # session (outcome: a rolled-back write leaves no row); ``updated`` only
    # when a field really changed, ``changed`` naming the fields.
    _spec("classification_hint.created", "configuration", "classification_hint", "Classification hint {entity_label} was created"),
    _spec("classification_hint.updated", "configuration", "classification_hint", "Classification hint {entity_label} changed: {changed}"),
    _spec("classification_hint.deleted", "configuration", "classification_hint", "Classification hint {entity_label} was deleted"),
    _spec("ownership_rule.created", "configuration", "ownership_rule", "Ownership rule {entity_label} was created"),
    _spec("ownership_rule.updated", "configuration", "ownership_rule", "Ownership rule {entity_label} changed: {changed}"),
    _spec("ownership_rule.deleted", "configuration", "ownership_rule", "Ownership rule {entity_label} was deleted"),
    _spec("ownership_rules.bulk_imported", "configuration", "ownership_rule", "Imported {count} ownership rules"),
    _spec("codeowners.imported", "configuration", "ownership_rule", "Imported {count} rules from CODEOWNERS"),
    _spec("team_channel.set", "configuration", "ownership_rule", "Notification channel set for team {entity_label}"),
    _spec("team_channel.removed", "configuration", "ownership_rule", "Notification channel removed for team {entity_label}"),
    _spec("retention_policy.updated", "configuration", "retention_policy", "Retention policy changed: {changed}"),
    # W09 NTF-02 / FLK-02: staged by the two policy PUTs on the request session
    # (outcome: a rolled-back save leaves no row), only when a field changed or
    # the PUT created the project's row. ``changed`` joins human labels
    # ("every run, state changes"; "SLA days, auto-release"), or reads "saved
    # the defaults as this project's policy" for a first save of the defaults.
    _spec("notification_policy.updated", "configuration", "notification_policy", "Notification policy changed: {changed}"),
    _spec("quarantine_policy.updated", "configuration", "quarantine_policy", "Quarantine policy changed: {changed}"),
    _spec("retention.purge_executed", "configuration", "retention_policy", "Retention purge removed {count} records"),
    # W22a ACT-05: staged by the two hold routes on the request session beside
    # the hold row (outcome: a rolled-back place records nothing). JWT only —
    # an API key can never place or release a hold.
    _spec("retention_hold.placed", "configuration", "retention_hold", "{actor_name} placed a retention hold on {entity_label}: {reason}", "outcome", frozenset({"user"})),
    _spec("retention_hold.released", "configuration", "retention_hold", "{actor_name} released the retention hold on {entity_label}", "outcome", frozenset({"user"})),
    # W22b ACT-03: the acknowledge route stages it beside the job's transition
    # to queued (the acknowledgement starts a deletion). JWT only.
    _spec("retention.export_acknowledged", "configuration", "retention_policy", "{actor_name} confirmed the deletion archive was saved; deleting {count} runs", "outcome", frozenset({"user"})),
    # W23a TRND-03: staged by routers/run_cadences.py on the request session
    # (outcome: a rolled-back write leaves no row). ``entity_label`` is the
    # scope ("nightly-regression" / "any run"); ``updated`` only when a field
    # really changed, ``changed`` naming them; ``paused`` carries the until /
    # reason the person typed.
    _spec("run_cadence.created", "configuration", "run_cadence", "Expected runs for {entity_label} set: {summary}", "outcome", _HUMAN_OR_KEY),
    _spec("run_cadence.updated", "configuration", "run_cadence", "Expected runs for {entity_label} changed: {changed}", "outcome", _HUMAN_OR_KEY),
    _spec("run_cadence.deleted", "configuration", "run_cadence", "Expected runs for {entity_label} removed", "outcome", _HUMAN_OR_KEY),
    _spec("run_cadence.paused", "configuration", "run_cadence", "Expected runs for {entity_label} paused until {until}: {reason}", "outcome", _HUMAN_OR_KEY),
    _spec("run_cadence.resumed", "configuration", "run_cadence", "Expected runs for {entity_label} resumed", "outcome", _HUMAN_OR_KEY),
    _spec("saved_view.created", "configuration", "saved_view", "Saved view {entity_label} was created"),
    _spec("saved_view.updated", "configuration", "saved_view", "Saved view {entity_label} changed: {changed}"),
    _spec("saved_view.deleted", "configuration", "saved_view", "Saved view {entity_label} was deleted"),
    _spec("saved_view.shared", "configuration", "saved_view", "Saved view {entity_label} was shared"),
    _spec("feature_flag.project_toggled", "configuration", "project", "Feature flag {flag_key} was {state} for this project"),
    # ── membership ──────────────────────────────────────────────────────────
    _spec("member.added", "membership", "member", "{entity_label} was added as {role}"),
    _spec("member.role_changed", "membership", "member", "{entity_label} role changed to {role}"),
    _spec("member.removed", "membership", "member", "{entity_label} was removed from the project"),
    _spec("invitation.sent", "membership", "member", "Invitation sent to {entity_label}"),
    # ── test management ─────────────────────────────────────────────────────
    _spec("test_case.created", "test_management", "test", "Test case {entity_label} was created"),
    _spec("test_case.updated", "test_management", "test", "Test case {entity_label} changed: {changed}"),
    _spec("test_case.status_changed", "test_management", "test", "Test case {entity_label} moved to {status}"),
    _spec("test_case.reviewed", "test_management", "test", "Test case {entity_label} was reviewed"),
    _spec("test_case.approved", "test_management", "test", "Test case {entity_label} was approved"),
    _spec("test_case.rejected", "test_management", "test", "Test case {entity_label} was rejected"),
    _spec("test_case.deleted", "test_management", "test", "Test case {entity_label} was deleted"),
    _spec("test_plan.created", "test_management", "test", "Test plan {entity_label} was created"),
    _spec("test_plan.updated", "test_management", "test", "Test plan {entity_label} changed: {changed}"),
    # W21 ACT-01 (c): no ``test_plan.deleted`` -- test plans have no delete route.
    _spec("suite.sync_deleted", "test_management", "suite", "{count} tests were removed from suite {entity_label}"),
    # W28 TCM-03 / TCM-04: staged by services/test_case_editing_service.py on
    # the request session (outcome: a rolled-back request leaves no row).
    # ``bulk_changed`` once per POST /cases/bulk with at least one changed case
    # (context: count, change, skipped, not_found); ``restored`` when a restore
    # saved a new version (context: from_version, to_version).
    _spec("test_case.bulk_changed", "test_management", "test_library", "Bulk edit of {count} test cases: {change}"),
    _spec("test_case.restored", "test_management", "test_case", "Test case {entity_label} restored from v{from_version}"),
    # W21d ACT-02: recorded by routers/test_management_cases.py add_comment on
    # the request session, one row per comment (outcome: it commits with the
    # comment or not at all). context: comment_id, mentions (user IDS, never
    # usernames or e-mails), mention_count, step_number; source_table
    # test_case_comments.
    _spec("test_case.commented", "test_management", "test_case",
          "{actor_name} commented on test case {entity_label}", "outcome", _HUMAN_OR_KEY),
    # W13 (outcome rows, staged on the request session and committed by the
    # router). ``suite.updated``: routers/suites.py update_suite, only when a
    # field changed (context: changed; before / after of the changed fields).
    # ``test_case.imported``: test_case_import_service.import_cases on an apply
    # with at least one created case (context: count, duplicate, invalid;
    # entity_label = the file name). ``requirement_linked`` / ``_unlinked``:
    # requirement_traceability_service add_link / remove_link (context:
    # requirement_key, link_type).
    _spec("suite.updated", "test_management", "suite", "Suite {entity_label} changed: {changed}"),
    _spec("test_case.imported", "test_management", "test_library", "Imported {count} test cases from {entity_label}"),
    _spec("test_case.requirement_linked", "test_management", "test_case",
          "Test case {entity_label} linked to {requirement_key}"),
    _spec("test_case.requirement_unlinked", "test_management", "test_case",
          "Test case {entity_label} unlinked from {requirement_key}"),
    # ── integration ─────────────────────────────────────────────────────────
    _spec("api_key.created", "integration", "api_key", "API key {entity_label} was created"),
    _spec("api_key.revoked", "integration", "api_key", "API key {entity_label} was revoked"),
    _spec("webhook.created", "integration", "webhook", "Webhook to {target_host} was created"),
    _spec("webhook.updated", "integration", "webhook", "Webhook {entity_label} changed: {changed}"),
    _spec("webhook.deleted", "integration", "webhook", "Webhook {entity_label} was deleted"),
    _spec("webhook.disabled", "integration", "webhook", "Webhook {entity_label} was disabled"),
    # W10 INT-07: staged by services/webhook_breaker.py (system actor) in the
    # delivery's transaction when a subscription is paused after N consecutive
    # failed deliveries (context: count, threshold, error).
    _spec("webhook.circuit_opened", "integration", "webhook",
          "Webhook {entity_label} paused after {count} consecutive failures", actor_types=frozenset({"system"})),
    _spec("integration.connected", "integration", "integration", "{provider} was connected"),
    _spec("integration.disconnected", "integration", "integration", "{provider} was disconnected"),
    _spec("integration.tested", "integration", "integration", "{provider} connection was tested"),
    _spec("notification_pref.updated", "integration", "integration", "Notification preferences changed: {changed}"),
    _spec("knowledge_source.added", "integration", "knowledge_source", "Knowledge source {entity_label} was added"),
    _spec("knowledge_source.synced", "integration", "knowledge_source", "Knowledge source {entity_label} was synced"),
    _spec("knowledge_source.removed", "integration", "knowledge_source", "Knowledge source {entity_label} was removed"),
    # ── agent ───────────────────────────────────────────────────────────────
    # W21 ACT-01 (c): no ``agent.investigation_run`` -- ``investigation.started`` /
    # ``investigation.completed`` (W21a) already record every investigation.
    _spec("agent.fix_proposed", "agent", "agent_action", "{actor_name} proposed a fix for {entity_label}", "outcome", frozenset({"agent"})),
    # W21 ACT-01 (c): no ``agent.fix_rejected`` -- agents do not reject fixes; a person's
    # refusal is ``agent.action_denied``.
    _spec("agent.action_approved", "agent", "agent_action", "Agent action on {entity_label} was approved", "outcome", _HUMAN_OR_KEY),
    _spec("agent.action_executed", "agent", "agent_action", "{actor_name} executed an action on {entity_label}", "outcome", frozenset({"agent"})),
    _spec("agent.action_denied", "agent", "agent_action", "Agent action on {entity_label} was denied", "outcome", _ANY_WRITER),
    _spec("agent.action_rolled_back", "agent", "agent_action", "{actor_name} undid an agent action on {entity_label}", "outcome", _HUMAN_OR_KEY),
    # ── system ──────────────────────────────────────────────────────────────
    # NOTE: there is deliberately no ``activity.exported`` event. Recording
    # an export in the ledger made a GET mutate what it reads: an export of
    # an empty project left it holding one event - the record of exporting
    # nothing - so ledger_started_at went non-null and the project claimed a
    # history it never had. Exports go to access_audit_logs with a
    # ``report_`` prefix, where this codebase already keeps them.
    _spec("compliance_pack.generated", "system", "export", "Compliance pack {entity_label} was generated"),
    _spec("report.exported", "system", "export", "Report {entity_label} was exported"),
    _spec("report.shared", "system", "export", "Report {entity_label} was shared"),
    # W08 TRND-01: POST /reports/email-trends, staged after the relay accepted
    # the message (outcome mode, committed with the request). ``context`` holds
    # the recipient COUNT and the window, never an address (personal data).
    _spec("report.emailed", "system", "export", "Report {entity_label} was emailed to {count} recipients"),
    # W21 ACT-01 (c): no ``maintenance.executed`` -- no maintenance task records itself
    # (the backlog's recommended removal); retention purges are ``retention.purge_executed``.
)

ACTIVITY_EVENTS: Final[Mapping[str, ActivityEventSpec]] = {
    spec.event_type: spec for spec in _SPECS
}


def lookup(event_type: str) -> ActivityEventSpec:
    """Return the spec for ``event_type`` or raise :class:`UnknownActivityEvent`."""
    try:
        return ACTIVITY_EVENTS[event_type]
    except KeyError as exc:
        raise UnknownActivityEvent(
            f"{event_type!r} is not a registered activity event. "
            f"Add it to app/services/activity/events.py before emitting it."
        ) from exc


class _SafeDict(dict):
    """Formatting map that renders a missing key as an em dash.

    A summary is a display string, not a contract. A producer that omits one
    context key should get a slightly vaguer sentence, never a ``KeyError``
    that takes down the mutation it was describing.
    """

    def __missing__(self, key: str) -> str:  # noqa: D105 - trivial
        return "—"


def render_summary(
    spec: ActivityEventSpec,
    *,
    entity_label: Optional[str],
    actor_name: Optional[str],
    context: Optional[Mapping[str, object]] = None,
) -> str:
    """Render a spec's template into the feed sentence.

    Never interpolates a raw ``diff``: only ``context``, which the write path
    has already redacted. Callers that handle secrets pass field NAMES in
    ``context['changed']``, never values.
    """
    values = _SafeDict(context or {})
    values.setdefault("entity_label", entity_label or "—")
    values.setdefault("actor_name", actor_name or "Someone")
    try:
        rendered = spec.summary_template.format_map(values)
    except (IndexError, ValueError):
        # A malformed template must not break a mutation. Fall back to the
        # event name, which is always meaningful enough to triage from.
        rendered = spec.event_type
    return rendered[:500]


def entity_href(entity_type: str, entity_id: str) -> Optional[str]:
    """Build the SPA link for an entity, or None when the type has no page."""
    route = _ENTITY_ROUTES.get(entity_type)
    if not route:
        return None
    return route.replace("{id}", entity_id)


def is_valid_event_name(name: str) -> bool:
    """Shape check used by the registry self-tests and the parity gate."""
    return bool(_EVENT_NAME_RE.match(name)) and len(name) <= 60


def events_by_category() -> dict[str, list[dict[str, str]]]:
    """Registry grouped for the frontend filter list (GET /activity/event-types)."""
    grouped: dict[str, list[dict[str, str]]] = {c: [] for c in ACTIVITY_CATEGORIES}
    for spec in _SPECS:
        grouped[spec.category].append(
            {"event_type": spec.event_type, "entity_type": spec.entity_type}
        )
    return grouped
