"""Migration 0235 (W20 ADM-10): shape only, no database.

Pinned: the chain 0234 -> 0235 with 0235 the only head; no application import; one nullable
``secret_refs.external_ref VARCHAR(500)``; the downgrade refuses while Vault references exist and
otherwise drops the column; the ORM declares it.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M = VERSIONS / "0235_secret_external_ref.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, M)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def test_chain_and_no_app_import():
    m = _load("m0235")
    assert (m.revision, m.down_revision) == ("0235", "0234") and "Revision ID: 0235" in m.__doc__
    revs, heads = _heads()
    assert list(revs.values()).count("0234") == 1 and len(heads) == 1 and heads[0] >= "0235"
    tree = ast.parse(M.read_text(encoding="utf-8"))
    names = {(n.module or "") if isinstance(n, ast.ImportFrom) else a.name
             for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert not any(x.startswith("app") for x in names)


def test_upgrade_adds_one_nullable_column():
    mod = _load("m0235_up")
    mod.op = MagicMock()
    mod.upgrade()
    (call,) = mod.op.add_column.call_args_list
    table, col = call.args
    assert table == "secret_refs" and col.name == "external_ref" and col.nullable
    assert isinstance(col.type, sa.String) and col.type.length == 500


@pytest.mark.parametrize("refs,drops", [(0, True), (2, False)])
def test_downgrade_refuses_while_references_exist(refs, drops):
    mod = _load(f"m0235_down_{refs}")
    mod.op = MagicMock()
    mod.op.get_bind.return_value.execute.return_value.scalar.return_value = refs
    if drops:
        mod.downgrade()
        mod.op.drop_column.assert_called_once_with("secret_refs", "external_ref")
    else:
        with pytest.raises(RuntimeError, match="2 secret"):
            mod.downgrade()
        mod.op.drop_column.assert_not_called()


def test_the_orm_declares_the_column():
    from app.models.postgres import SecretRef

    col = SecretRef.__table__.c.external_ref
    assert col.nullable and col.type.length == 500
