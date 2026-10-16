"""Migration 0216 (W07 FAIL-10): ``classification_hints``.

Shape only: the chain; the table's columns, FKs and CHECKs; both indexes built
on the table this migration created (plain builds: the 0166+ concurrency rule
concerns existing tables); a downgrade that drops exactly what the upgrade
created, in reverse order; the ORM declaring the same table.

The two vocabularies have THREE copies -- the migration's frozen tuples, the
ORM's CHECK literals and the service's ``MATCH_FIELDS`` / ``HINT_CATEGORIES``
(``classification_hint_service``, G-B) -- and this one test holds them equal
(the ``tests/test_activity_events.py`` reasoning: drift between producer and
constraint is a 500 at write time, not a test failure, unless something pins it).
PostgreSQL: ``tests/integration/test_migration_0215_0216_postgres.py``.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0216_classification_hints.py"

MATCH_FIELDS = ("error_message", "test_name", "suite_name", "class_name")
CATEGORIES = ("PRODUCT_BUG", "INFRASTRUCTURE", "TEST_DATA", "AUTOMATION_DEFECT", "FLAKY")


def _module():
    spec = importlib.util.spec_from_file_location("migration_0216", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in_values(sqltext: str) -> tuple[str, ...]:
    (body,) = re.findall(r"IN \(([^)]*)\)", sqltext)
    return tuple(v.strip().strip("'") for v in body.split(","))


def test_it_follows_0215():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0216", "0215")
    assert "Revision ID: 0216" in migration.__doc__ and "Revises: 0215" in migration.__doc__


def _upgraded():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    return migration


def test_upgrade_creates_the_table_with_its_columns_and_checks():
    migration = _upgraded()
    (table, *items), _ = migration.op.create_table.call_args
    assert table == "classification_hints"
    columns = {item.name: item for item in items if isinstance(item, sa.Column)}
    assert list(columns) == [
        "id", "project_id", "match_field", "pattern", "category", "note",
        "enabled", "created_by", "created_at", "updated_at",
    ]
    assert columns["id"].primary_key
    for name, length in (("match_field", 20), ("pattern", 200), ("category", 30), ("note", 500)):
        assert columns[name].type.length == length
    assert [c for c in columns if columns[c].nullable] == ["note", "created_by"]
    (project_fk,) = list(columns["project_id"].foreign_keys)
    assert project_fk.target_fullname == "projects.id" and project_fk.ondelete == "CASCADE"
    (user_fk,) = list(columns["created_by"].foreign_keys)
    assert user_fk.target_fullname == "users.id" and user_fk.ondelete == "SET NULL"
    assert str(columns["enabled"].server_default.arg) == "true"
    checks = {item.name: str(item.sqltext) for item in items if isinstance(item, sa.CheckConstraint)}
    assert set(checks) == {"ck_classification_hints_match_field", "ck_classification_hints_category"}
    assert _in_values(checks["ck_classification_hints_match_field"]) == MATCH_FIELDS
    assert _in_values(checks["ck_classification_hints_category"]) == CATEGORIES


def test_upgrade_builds_both_indexes_on_the_new_table():
    migration = _upgraded()
    calls = migration.op.create_index.call_args_list
    assert [c.args[0] for c in calls] == [
        "ix_classification_hints_project_enabled", "uq_classification_hints_project_rule",
    ]
    enabled, rule = calls
    assert enabled.args[1:] == ("classification_hints", ["project_id", "enabled"])
    assert not enabled.kwargs.get("unique")
    table, (project, field, lowered) = rule.args[1], rule.args[2]
    assert (table, project, field) == ("classification_hints", "project_id", "match_field")
    assert str(lowered) == "lower(pattern)"
    assert rule.kwargs["unique"] is True
    assert not migration.op.execute.called


def test_downgrade_drops_both_indexes_then_the_table():
    migration = _module()
    migration.op = MagicMock()
    migration.downgrade()
    assert [c.args for c in migration.op.drop_index.call_args_list] == [
        ("uq_classification_hints_project_rule",), ("ix_classification_hints_project_enabled",),
    ]
    assert all(c.kwargs == {"table_name": "classification_hints"} for c in migration.op.drop_index.call_args_list)
    migration.op.drop_table.assert_called_once_with("classification_hints")
    names = [call[0] for call in migration.op.method_calls]
    assert names == ["drop_index", "drop_index", "drop_table"]


def test_the_orm_declares_the_same_table():
    from app.models.postgres import ClassificationHint

    table = ClassificationHint.__table__
    assert table.name == "classification_hints"
    assert [c.name for c in table.columns] == [
        "id", "project_id", "match_field", "pattern", "category", "note",
        "enabled", "created_by", "created_at", "updated_at",
    ]
    indexes = {i.name: i for i in table.indexes}
    assert [c.name for c in indexes["ix_classification_hints_project_enabled"].columns] == ["project_id", "enabled"]
    rule = indexes["uq_classification_hints_project_rule"]
    assert rule.unique is True
    assert [getattr(e, "name", str(e)) for e in rule.expressions] == ["project_id", "match_field", "lower(pattern)"]
    checks = {
        c.name: str(c.sqltext) for c in table.constraints if isinstance(c, sa.CheckConstraint)
    }
    assert _in_values(checks["ck_classification_hints_match_field"]) == MATCH_FIELDS
    assert _in_values(checks["ck_classification_hints_category"]) == CATEGORIES


def test_the_three_copies_of_each_vocabulary_are_equal():
    """Migration (frozen), ORM CHECK literals, and the service (G-B's ``classification_hint_service``)."""
    from app.models import postgres

    migration = _module()
    assert migration._MATCH_FIELDS == postgres._HINT_MATCH_FIELDS == MATCH_FIELDS
    assert migration._CATEGORIES == postgres._HINT_CATEGORIES == CATEGORIES
    # Every category is a FailureCategory other than UNKNOWN.
    assert set(CATEGORIES) == {c.value for c in postgres.FailureCategory} - {"UNKNOWN"}
    service = pytest.importorskip(
        "app.services.classification_hint_service",
        reason="waiting for G-B: classification_hint_service.py",
    )
    assert tuple(service.MATCH_FIELDS) == MATCH_FIELDS
    assert tuple(service.HINT_CATEGORIES) == CATEGORIES
