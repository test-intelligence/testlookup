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
    "export",
)

#: Where a row of each entity type lives in the SPA. Used to build
#: ``entity.href`` server-side so the CLI and MCP tools get working links too,
#: instead of every client re-deriving the route map and drifting.
_ENTITY_ROUTES: Final[Mapping[str, str]] = {
    "run": "/runs/{id}",
    "test": "/test-management?test={id}",
    "suite": "/suites/{id}",
    "release": "/releases?release={id}",
    "policy": "/policies/{id}",
    # No page renders attribution rules today — the API exists, the UI does
    # not. An empty route yields href=None, so the feed prints the rule's name
    # as plain text. Sending the reader to a route that does not exist would
    # bounce them to /overview, which is worse than not offering a link.
    "attribution_rule": "",
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
    "export": "",
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
    # ── analysis ────────────────────────────────────────────────────────────
    _spec("analysis.completed", "analysis", "run", "AI analysis completed for {entity_label}"),
    _spec("analysis.failed", "analysis", "run", "AI analysis failed for {entity_label}: {error}"),
    _spec("analysis.report_superseded", "analysis", "run", "Decision report for {entity_label} was superseded"),
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
    _spec("release.decision_overridden", "release", "release", "Release decision for {entity_label} overridden to {recommendation}", "attempt"),
    _spec("release.phase_advanced", "release", "release", "Release {entity_label} advanced to {phase}"),
    _spec("release.phase_skipped", "release", "release", "Phase {phase} skipped on release {entity_label}", "attempt"),
    _spec("release.phase_gate_overridden", "release", "release", "Phase gate {phase} overridden on release {entity_label}", "attempt"),
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
    # ── quality ─────────────────────────────────────────────────────────────
    _spec("quarantine.requested", "quality", "quarantine", "Quarantine requested for {entity_label}", "attempt"),
    _spec("quarantine.approved", "quality", "quarantine", "Quarantine approved for {entity_label}", "attempt"),
    _spec("quarantine.rejected", "quality", "quarantine", "Quarantine rejected for {entity_label}", "attempt"),
    _spec("quarantine.released", "quality", "quarantine", "{entity_label} was released from quarantine", "attempt"),
    _spec("defect.promoted", "quality", "defect", "Failure cluster promoted to defect {entity_label}"),
    _spec("defect.create_requested", "quality", "defect", "Defect ticket requested for {entity_label}"),
    _spec("defect.ticket_created", "quality", "defect", "Ticket {ticket_key} created for {entity_label}"),
    _spec("defect.commander_run", "quality", "run", "Defect Commander drafted a defect from a failure cluster in {entity_label}", "attempt"),
    _spec("flaky.detected", "quality", "test", "{entity_label} was detected as flaky"),
    _spec("test.newly_failing", "quality", "test", "{entity_label} started failing"),
    _spec("test.recovered", "quality", "test", "{entity_label} recovered"),
    # ── configuration ───────────────────────────────────────────────────────
    _spec("project.created", "configuration", "project", "Project {entity_label} was created"),
    _spec("project.updated", "configuration", "project", "Project settings changed: {changed}"),
    _spec("project.reset", "configuration", "project", "Project data reset ({mode}) was issued", "attempt"),
    _spec("policy.created", "configuration", "policy", "Release gate policy {entity_label} was created"),
    _spec("policy.updated", "configuration", "policy", "Release gate policy {entity_label} changed: {changed}"),
    _spec("policy.deleted", "configuration", "policy", "Release gate policy {entity_label} was deleted"),
    _spec("policy.activated", "configuration", "policy", "Release gate policy {entity_label} was activated"),
    _spec("agent_config.updated", "configuration", "project", "Agent {entity_label} configuration changed to version {config_version}: {changed}", "outcome", _HUMAN_OR_KEY),
    _spec("ai_eval.tier_compared", "configuration", "project", "Agent {entity_label} tier comparison {verdict} with {sample_count} paired samples", "outcome", _HUMAN_OR_KEY),
    _spec("attribution_rule.created", "configuration", "attribution_rule", "Attribution rule {entity_label} was created"),
    _spec("attribution_rule.updated", "configuration", "attribution_rule", "Attribution rule {entity_label} changed: {changed}"),
    _spec("attribution_rule.deleted", "configuration", "attribution_rule", "Attribution rule {entity_label} was deleted"),
    _spec("attribution_rule.enabled", "configuration", "attribution_rule", "Attribution rule {entity_label} was enabled"),
    _spec("attribution_rule.disabled", "configuration", "attribution_rule", "Attribution rule {entity_label} was disabled"),
    _spec("ownership_rule.created", "configuration", "ownership_rule", "Ownership rule {entity_label} was created"),
    _spec("ownership_rule.updated", "configuration", "ownership_rule", "Ownership rule {entity_label} changed: {changed}"),
    _spec("ownership_rule.deleted", "configuration", "ownership_rule", "Ownership rule {entity_label} was deleted"),
    _spec("ownership_rules.bulk_imported", "configuration", "ownership_rule", "Imported {count} ownership rules"),
    _spec("codeowners.imported", "configuration", "ownership_rule", "Imported {count} rules from CODEOWNERS"),
    _spec("team_channel.set", "configuration", "ownership_rule", "Notification channel set for team {entity_label}"),
    _spec("team_channel.removed", "configuration", "ownership_rule", "Notification channel removed for team {entity_label}"),
    _spec("retention_policy.updated", "configuration", "retention_policy", "Retention policy changed: {changed}"),
    _spec("retention.purge_executed", "configuration", "retention_policy", "Retention purge removed {count} records"),
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
    _spec("test_plan.deleted", "test_management", "test", "Test plan {entity_label} was deleted"),
    _spec("suite.sync_deleted", "test_management", "suite", "{count} tests were removed from suite {entity_label}"),
    # ── integration ─────────────────────────────────────────────────────────
    _spec("api_key.created", "integration", "api_key", "API key {entity_label} was created"),
    _spec("api_key.revoked", "integration", "api_key", "API key {entity_label} was revoked"),
    _spec("webhook.created", "integration", "webhook", "Webhook to {target_host} was created"),
    _spec("webhook.updated", "integration", "webhook", "Webhook {entity_label} changed: {changed}"),
    _spec("webhook.deleted", "integration", "webhook", "Webhook {entity_label} was deleted"),
    _spec("webhook.disabled", "integration", "webhook", "Webhook {entity_label} was disabled"),
    _spec("integration.connected", "integration", "integration", "{provider} was connected"),
    _spec("integration.disconnected", "integration", "integration", "{provider} was disconnected"),
    _spec("integration.tested", "integration", "integration", "{provider} connection was tested"),
    _spec("notification_pref.updated", "integration", "integration", "Notification preferences changed: {changed}"),
    _spec("knowledge_source.added", "integration", "knowledge_source", "Knowledge source {entity_label} was added"),
    _spec("knowledge_source.synced", "integration", "knowledge_source", "Knowledge source {entity_label} was synced"),
    _spec("knowledge_source.removed", "integration", "knowledge_source", "Knowledge source {entity_label} was removed"),
    # ── agent ───────────────────────────────────────────────────────────────
    _spec("agent.investigation_run", "agent", "agent_action", "{actor_name} investigated {entity_label}", "outcome", frozenset({"agent"})),
    _spec("agent.fix_proposed", "agent", "agent_action", "{actor_name} proposed a fix for {entity_label}", "outcome", frozenset({"agent"})),
    _spec("agent.fix_rejected", "agent", "agent_action", "{actor_name} rejected a fix for {entity_label}: {reason}", "outcome", frozenset({"agent"})),
    _spec("agent.action_approved", "agent", "agent_action", "Agent action on {entity_label} was approved", "outcome", _HUMAN_OR_KEY),
    _spec("agent.action_executed", "agent", "agent_action", "{actor_name} executed an action on {entity_label}", "outcome", frozenset({"agent"})),
    _spec("agent.action_denied", "agent", "agent_action", "Agent action on {entity_label} was denied", "outcome", _ANY_WRITER),
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
    _spec("maintenance.executed", "system", "project", "Maintenance task {entity_label} was executed", "attempt"),
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
