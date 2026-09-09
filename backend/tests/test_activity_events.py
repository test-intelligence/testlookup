"""Registry and vocabulary tests for the activity ledger (epic ACT).

The registry is the contract three other things are built on — the DB CHECK
constraints, the migration, and the frontend filter list. These tests are what
stop those four copies from drifting apart.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.services.activity import events as E

REPO_BACKEND = pathlib.Path(__file__).resolve().parent.parent


# ── Registry shape ───────────────────────────────────────────────────────────


def test_registry_is_not_empty() -> None:
    """Guards against an import that silently yields an empty mapping — the
    shape in which every other test here would vacuously pass."""
    assert len(E.ACTIVITY_EVENTS) > 50


def test_every_event_name_is_well_formed() -> None:
    bad = [n for n in E.ACTIVITY_EVENTS if not E.is_valid_event_name(n)]
    assert not bad, f"Malformed event names (want '<object>.<verb>', <=60 chars): {bad}"


def test_no_event_name_differs_only_by_case() -> None:
    """`quarantined` vs `QUARANTINED` matched nothing for months (#735). The
    ledger's vocabulary is lowercase, and nothing may shadow another name."""
    lowered = [n.lower() for n in E.ACTIVITY_EVENTS]
    assert len(lowered) == len(set(lowered))


def test_no_connection_noise_events_are_registered() -> None:
    """The reason the existing Audit Dashboard is unreadable.

    Observed live 2026-09-05: the first page of /settings/audit was ws_connect
    / ws_disconnect pairs and duplicated scheduler purges. Recording connection
    churn at the same rank as a role change is not a display bug that can be
    filtered away later — it is a decision made at write time, so it is
    forbidden here at write time.
    """
    noise = [
        n
        for n in E.ACTIVITY_EVENTS
        if "ws_" in n or "heartbeat" in n or "websocket" in n
    ]
    assert not noise, f"Connection-churn events must never be registered: {noise}"


def test_every_spec_uses_declared_vocabularies() -> None:
    for name, spec in E.ACTIVITY_EVENTS.items():
        assert spec.category in E.ACTIVITY_CATEGORIES, f"{name}: bad category"
        assert spec.entity_type in E.ENTITY_TYPES, f"{name}: bad entity_type"
        assert spec.write_mode in {"outcome", "attempt"}, f"{name}: bad write_mode"
        assert spec.actor_types <= set(E.ACTOR_TYPES), f"{name}: bad actor_types"
        assert spec.actor_types, f"{name}: no actor may emit this event"


def test_project_deleted_is_deliberately_absent() -> None:
    """project_activity_events cascades on project delete, so a
    ``project.deleted`` row would be removed by the very thing it records.
    That event belongs in access_audit_logs (ON DELETE SET NULL).

    If someone adds it here, this test tells them why not to.
    """
    assert "project.deleted" not in E.ACTIVITY_EVENTS


# ── Cross-file parity ────────────────────────────────────────────────────────


def test_names_shared_with_webhook_catalog_are_identical() -> None:
    """One vocabulary serves webhooks, notifications and the ledger.

    These four names already existed in the outbound webhook catalog. If the
    ledger spelled them differently, a user filtering the feed for the thing
    their webhook fired on would find nothing.
    """
    from app.services.webhook_service import SUPPORTED_EVENTS

    shared = {
        "run.completed",
        "defect.promoted",
        "defect.create_requested",
        "release.decided",
    }
    for name in shared:
        assert name in SUPPORTED_EVENTS, f"{name} vanished from the webhook catalog"
        assert name in E.ACTIVITY_EVENTS, f"{name} missing from the activity registry"


def _literals_from(source: str, pattern: str) -> set[str]:
    """Pull the quoted words out of one declaration.

    Accepts either quote style: the ORM CheckConstraints are written with
    single-quoted SQL literals inside a double-quoted Python string, while the
    migration uses plain double-quoted Python strings.
    """
    match = re.search(pattern, source, re.S)
    assert match, f"Could not locate vocabulary with pattern {pattern!r}"
    found = set(re.findall(r"['\"]([a-z_]+)['\"]", match.group(1)))
    assert found, f"Vocabulary matched but parsed empty for pattern {pattern!r}"
    return found


def test_vocabularies_agree_everywhere() -> None:
    """The registry, the ORM CHECK constraints and migration 0165 must agree.

    Three copies exist on purpose: models must not import services, and a
    migration must not import application code that can change under it. The
    cost of that correctness is drift, so this test is the thing that pays it.
    """
    model_src = (REPO_BACKEND / "app" / "models" / "postgres.py").read_text(
        encoding="utf-8"
    )
    migration_src = (
        REPO_BACKEND / "migrations" / "versions" / "0165_project_activity_events.py"
    ).read_text(encoding="utf-8")

    model_categories = _literals_from(
        model_src, r'"category IN \((.*?)\)",\s*\n\s*name="ck_pae_category"'
    )
    model_actors = _literals_from(
        model_src, r'"actor_type IN \((.*?)\)",\s*\n\s*name="ck_pae_actor_type"'
    )
    migration_categories = _literals_from(migration_src, r"_CATEGORIES = \((.*?)\)")
    migration_actors = _literals_from(migration_src, r"_ACTOR_TYPES = \((.*?)\)")

    registry_categories = set(E.ACTIVITY_CATEGORIES)
    registry_actors = set(E.ACTOR_TYPES)

    assert model_categories == registry_categories, (
        "ORM CheckConstraint categories drifted from events.ACTIVITY_CATEGORIES: "
        f"{model_categories ^ registry_categories}"
    )
    assert migration_categories == registry_categories, (
        "Migration 0165 categories drifted from events.ACTIVITY_CATEGORIES: "
        f"{migration_categories ^ registry_categories}"
    )
    assert model_actors == registry_actors, (
        f"ORM actor_type CHECK drifted: {model_actors ^ registry_actors}"
    )
    assert migration_actors == registry_actors, (
        f"Migration 0165 actor_type drifted: {migration_actors ^ registry_actors}"
    )


def test_every_category_has_at_least_one_event() -> None:
    """A declared category with no events puts an empty group in the filter UI."""
    used = {spec.category for spec in E.ACTIVITY_EVENTS.values()}
    unused = set(E.ACTIVITY_CATEGORIES) - used
    assert not unused, f"Categories declared but never emitted: {unused}"


# ── Summary rendering ────────────────────────────────────────────────────────


def test_summary_renders_with_full_context() -> None:
    spec = E.lookup("run.completed")
    summary = E.render_summary(
        spec,
        entity_label="Build #4312",
        actor_name="ci-jenkins",
        context={"failed": 3, "total": 812},
    )
    assert summary == "Build #4312 completed — 3 failed of 812"


def test_summary_survives_missing_context_keys() -> None:
    """A producer that forgets a key gets a vaguer sentence, never a KeyError.

    The summary is display text. Raising here would take down the mutation the
    event was describing, which is the opposite of the ledger's contract.
    """
    spec = E.lookup("run.completed")
    summary = E.render_summary(
        spec, entity_label="Build #1", actor_name=None, context=None
    )
    assert "Build #1" in summary
    assert "—" in summary


def test_summary_is_capped_at_column_width() -> None:
    spec = E.lookup("project.updated")
    summary = E.render_summary(
        spec, entity_label="x" * 900, actor_name="a", context={"changed": "y" * 900}
    )
    assert len(summary) <= 500


def test_entity_href_resolves_known_types_and_skips_unknown() -> None:
    assert E.entity_href("run", "abc") == "/runs/abc"
    assert E.entity_href("export", "abc") is None
    assert E.entity_href("not_a_type", "abc") is None


def test_lookup_rejects_unregistered_names() -> None:
    with pytest.raises(E.UnknownActivityEvent):
        E.lookup("totally.invented")


def test_events_by_category_covers_the_whole_registry() -> None:
    grouped = E.events_by_category()
    flat = [e["event_type"] for group in grouped.values() for e in group]
    assert sorted(flat) == sorted(E.ACTIVITY_EVENTS)
