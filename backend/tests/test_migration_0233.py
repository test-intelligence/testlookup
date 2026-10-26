"""Migration 0233 (W17b ADM-01 / ADM-02): shape only, no database.

Pinned: the chain 0232 -> 0233 with 0233 the only head; no application import;
the existing ``user_invitations`` table gets its unique ``token_hash`` index
CONCURRENTLY (the concurrent-index guard) and its plaintext tokens cleared;
``password_reset_tokens`` is created with a hash column only; the downgrade
undoes both; the ORM declares the same names. The PostgreSQL behaviour runs in
``tests/integration/test_invitations_postgres.py`` and
``tests/integration/test_password_reset_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0233 = VERSIONS / "0233_invitation_tokens_and_password_resets.py"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, M0233)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(step: str) -> MagicMock:
    module = _load(f"m0233_{step}")
    module.op = MagicMock()
    getattr(module, step)()
    return module.op


def _revisions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        values: dict[str, object] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in {"revision", "down_revision"} and isinstance(node.value, ast.Constant):
                    values[node.targets[0].id] = node.value.value
        if "revision" in values:
            out[str(values["revision"])] = values.get("down_revision")  # type: ignore[assignment]
    return out


def test_the_revision_and_the_chain():
    m = _load("m0233")
    assert (m.revision, m.down_revision) == ("0233", "0232")
    assert "Revision ID: 0233" in m.__doc__ and "Revises: 0232" in m.__doc__
    revisions = _revisions()
    parents = [p for p in revisions.values() if p]
    assert parents.count("0232") == 1
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1 and heads[0] >= "0233", heads


def test_it_imports_no_application_code():
    tree = ast.parse(M0233.read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
    }
    assert not any(name.startswith("app") for name in imported), imported


def test_the_upgrade_hashes_invitations_and_creates_the_reset_table():
    op = _run("upgrade")
    added = [(c.args[0], c.args[1].name, c.args[1].nullable) for c in op.add_column.call_args_list]
    assert added == [
        ("user_invitations", "token_hash", True),
        ("user_invitations", "used_at", True),
        ("user_invitations", "revoked_at", True),
        ("user_invitations", "accepted_user_id", True),
        ("user_invitations", "project_memberships", True),
    ]
    sql = [str(c.args[0]) for c in op.execute.call_args_list]
    assert "UPDATE user_invitations SET token = NULL" in sql
    assert any(s.startswith("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_user_invitations_token_hash") for s in sql)
    assert not op.create_unique_constraint.called  # would lock the table; the index is built concurrently
    op.alter_column.assert_called_once()
    assert op.alter_column.call_args.kwargs["nullable"] is True
    (table_call,) = op.create_table.call_args_list
    assert table_call.args[0] == "password_reset_tokens"
    columns = {c.name for c in table_call.args[1:] if hasattr(c, "name")}
    assert {"token_hash", "user_id", "expires_at", "used_at", "issued_by_user_id", "request_ip_hash"} <= columns
    assert "token" not in columns


def test_the_downgrade_undoes_it():
    op = _run("downgrade")
    op.drop_table.assert_called_once_with("password_reset_tokens")
    sql = [str(c.args[0]) for c in op.execute.call_args_list]
    assert sql[0] == "DROP INDEX CONCURRENTLY IF EXISTS uq_user_invitations_token_hash"
    assert {c.args[1] for c in op.drop_column.call_args_list} == {
        "token_hash", "used_at", "revoked_at", "accepted_user_id", "project_memberships",
    }
    assert op.alter_column.call_args.kwargs["nullable"] is False


def test_the_orm_declares_the_same_shape():
    from app.models.postgres import PasswordResetToken, UserInvitation

    inv = UserInvitation.__table__
    assert inv.c.token.nullable and inv.c.token_hash.nullable
    assert any(ix.name == "uq_user_invitations_token_hash" and ix.unique for ix in inv.indexes)
    (fk,) = inv.c.accepted_user_id.foreign_keys
    assert fk.name == "fk_user_invitations_accepted_user" and fk.ondelete == "SET NULL"
    reset = PasswordResetToken.__table__
    assert reset.name == "password_reset_tokens" and not reset.c.token_hash.nullable
    assert "token" not in reset.c
