"""Project activity ledger (epic ACT).

A project-scoped, append-only product feed of everything that happens inside a
project: runs received and completed, AI analysis outcomes, release decisions,
quarantine transitions, and every configuration, integration and membership
change.

This is **not** a replacement for the compliance audit tables
(``settings_audit_log``, ``access_audit_logs``, ``test_case_audit_logs``,
``identity_events``). Those keep their role and their retention guarantees. The
ledger is a derived read surface that links back to them via
``source_table`` / ``source_id``.

Public surface:

* :func:`app.services.activity.service.record` — the ONLY write path.
* :mod:`app.services.activity.events` — the frozen event registry.
* :mod:`app.services.activity.query` — keyset reads and export serialisation.
"""
from app.services.activity.events import (
    ACTIVITY_CATEGORIES,
    ACTIVITY_EVENTS,
    ACTOR_TYPES,
    ENTITY_TYPES,
    ActivityEventSpec,
    UnknownActivityEvent,
    entity_href,
    lookup,
    render_summary,
)

__all__ = [
    "ACTIVITY_CATEGORIES",
    "ACTIVITY_EVENTS",
    "ACTOR_TYPES",
    "ENTITY_TYPES",
    "ActivityEventSpec",
    "UnknownActivityEvent",
    "entity_href",
    "lookup",
    "render_summary",
]
