"""Migration 0234 (W19 INT-06 / INT-05): shape only, no database.

Pinned: the chain 0233 -> 0234 with 0234 the only head; no application import; no
index; ``auth_kind`` NOT NULL with server default ``pat`` and a nullable BIGINT
``installation_id``; the flag flip touches only ``github_checks`` / ``gitlab``,
only on a fresh install and only from the seeded value; the downgrade reverses
it; the ORM declares the same columns.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M = VERSIONS / "0234_github_app_auth.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, M)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(step):
    mod = _load(f"m0234_{step}")
    mod.op = MagicMock()
    getattr(mod, step)()
    return mod.op


def _heads():
    revs = {}
    for path in VERSIONS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        vals = {n.targets[0].id: n.value.value for n in tree.body
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id in ("revision", "down_revision") and isinstance(n.value, ast.Constant)}
        if "revision" in vals:
            revs[str(vals["revision"])] = vals.get("down_revision")
    parents = set(p for p in revs.values() if p)
    return revs, sorted(r for r in revs if r not in parents)


def test_chain_and_docstring():
    m = _load("m0234")
    assert (m.revision, m.down_revision) == ("0234", "0233") and "Revision ID: 0234" in m.__doc__
    revs, heads = _heads()
    assert list(revs.values()).count("0233") == 1 and len(heads) == 1 and heads[0] >= "0234"


def test_no_app_import_and_no_index():
    tree = ast.parse(M.read_text(encoding="utf-8"))
    names = {(n.module or "") if isinstance(n, ast.ImportFrom) else a.name
             for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert not any(x.startswith("app") for x in names)
    op = _run("upgrade")
    assert not op.create_index.called


def test_upgrade_columns_and_flag_flip():
    op = _run("upgrade")
    cols = {c.args[1].name: c.args[1] for c in op.add_column.call_args_list}
    assert isinstance(cols["auth_kind"].type, sa.String) and cols["auth_kind"].nullable is False
    assert cols["auth_kind"].server_default.arg == "pat"
    assert isinstance(cols["installation_id"].type, sa.BigInteger) and cols["installation_id"].nullable
    sql = str(op.execute.call_args.args[0])
    assert "'github_checks', 'gitlab'" in sql and "enabled_global = false AND NOT EXISTS (SELECT 1 FROM users)" in sql


def test_downgrade_reverses():
    op = _run("downgrade")
    assert [c.args[1] for c in op.drop_column.call_args_list] == ["installation_id", "auth_kind"]
    assert "enabled_global = false, updated_at" in str(op.execute.call_args.args[0])


def test_orm_matches():
    from app.models.postgres import GitHubIntegration

    t = GitHubIntegration.__table__
    assert t.c.auth_kind.nullable is False and t.c.auth_kind.server_default.arg == "pat"
    assert isinstance(t.c.installation_id.type, sa.BigInteger) and t.c.installation_id.nullable
