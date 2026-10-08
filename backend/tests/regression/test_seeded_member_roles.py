"""Seeded project memberships carry the bare role value, never ``'UserRole.X'``.

``scripts/seed_dev_data.py`` wrote each membership as ``str(UserRole.X)`` --
``'UserRole.QA_LEAD'`` -- which no membership check matches, so a seeded QA
lead got 403 updating the triage status of a failure in their own Inbox (the
UX redesign's browser E2E pass, 2026-10-07; every seeded install, the homelab
demo included). The seed writes ``.value`` now, and migration 0198 repairs the
rows it already wrote, as 0045 did once.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from app.models.postgres import UserRole

BACKEND = Path(__file__).resolve().parents[2]
SEED = BACKEND / "scripts" / "seed_dev_data.py"
MIGRATION_0198 = BACKEND / "migrations" / "versions" / "0198_clean_seeded_member_roles.py"


def test_str_of_the_role_enum_is_not_its_value():
    """Why ``str()`` was wrong: the enum's own ``__str__`` names the class."""
    assert str(UserRole.QA_LEAD) != UserRole.QA_LEAD.value
    assert UserRole.QA_LEAD.value == "QA_LEAD"


def test_the_seed_writes_every_membership_role_as_its_value():
    tree = ast.parse(SEED.read_text(encoding="utf-8"))
    roles = [
        kw.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ProjectMember"
        for kw in node.keywords
        if kw.arg == "role"
    ]
    assert roles, "the seed creates project memberships"
    for value in roles:
        # role=<something>.value -- not str(<enum>), not the bare enum.
        assert isinstance(value, ast.Attribute) and value.attr == "value", ast.unparse(value)


def _load_0198():
    spec = importlib.util.spec_from_file_location("migration_0198", MIGRATION_0198)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0198_follows_0197():
    module = _load_0198()
    assert (module.revision, module.down_revision) == ("0198", "0197")


def test_migration_0198_strips_the_prefix_from_every_role_column(monkeypatch):
    from alembic import op

    sent: list = []
    monkeypatch.setattr(op, "execute", sent.append, raising=False)
    _load_0198().upgrade()
    assert sent == [
        f"UPDATE {table} SET role = REPLACE(role, 'UserRole.', '') WHERE role LIKE 'UserRole.%'"
        for table in ("users", "project_members", "user_invitations")
    ]
