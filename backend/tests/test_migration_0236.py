"""Migration 0236 (W20 ADM-09): shape only, no database — chain 0235 -> 0236, the three SCIM group tables, the
nullable ``project_members.source``, a downgrade that drops them all, and the ORM in step."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

M = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0236_scim_groups.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, M)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.op = MagicMock()
    return mod


def test_chain_and_upgrade_shape():
    mod = _load("m0236_up")
    assert (mod.revision, mod.down_revision) == ("0236", "0235")
    mod.upgrade()
    tables = [c.args[0] for c in mod.op.create_table.call_args_list]
    assert tables == ["scim_groups", "scim_group_members", "scim_group_mappings"]
    (add,) = mod.op.add_column.call_args_list
    assert add.args[0] == "project_members" and add.args[1].name == "source" and add.args[1].nullable


def test_downgrade_drops_everything():
    mod = _load("m0236_down")
    mod.downgrade()
    assert [c.args[0] for c in mod.op.drop_table.call_args_list] == ["scim_group_mappings", "scim_group_members", "scim_groups"]
    mod.op.drop_column.assert_called_once_with("project_members", "source")


def test_the_orm_matches():
    from app.models.postgres import ProjectMember, SCIMGroup, SCIMGroupMapping, SCIMGroupMember

    assert ProjectMember.__table__.c.source.nullable
    assert SCIMGroup.__table__.c.display_name.unique
    assert {c.name for c in SCIMGroupMember.__table__.primary_key} == {"group_id", "user_id"}
    assert any(getattr(c, "name", "") == "uq_scim_group_mapping" for c in SCIMGroupMapping.__table__.constraints)
