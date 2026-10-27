"""Migration 0237 (W20 ADM-08b): shape only, no database — chain 0236 -> 0237, the four OIDC columns, the five
SAML columns made NULL-able under a CHECK by provider type, a downgrade that refuses while OIDC rows exist, and
the ORM in step."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

M = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0237_sso_oidc.py"
SAML = ["idp_entity_id", "idp_sso_url", "idp_certificate", "sp_entity_id", "sp_acs_url"]
OIDC = ["oidc_issuer", "oidc_client_id", "oidc_scopes", "oidc_groups_claim"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, M)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.op = MagicMock()
    return mod


def test_chain_and_upgrade_shape():
    mod = _load("m0237_up")
    assert (mod.revision, mod.down_revision) == ("0237", "0236")
    mod.upgrade()
    assert [c.args[1].name for c in mod.op.add_column.call_args_list] == OIDC
    altered = [(c.args[1], c.kwargs["nullable"]) for c in mod.op.alter_column.call_args_list]
    assert altered == [(name, True) for name in SAML]
    (check,) = mod.op.create_check_constraint.call_args_list
    assert check.args[0] == "ck_sso_config_fields_by_provider"
    for name in SAML + ["oidc_issuer", "oidc_client_id"]:
        assert f"{name} IS NOT NULL" in check.args[2]


def test_downgrade_refuses_while_an_oidc_row_exists():
    mod = _load("m0237_guard")
    mod.op.get_bind.return_value.execute.return_value.scalar.return_value = 1
    with pytest.raises(RuntimeError, match="OIDC"):
        mod.downgrade()
    mod.op.drop_constraint.assert_not_called()


def test_downgrade_restores_not_null_and_drops_the_columns():
    mod = _load("m0237_down")
    mod.op.get_bind.return_value.execute.return_value.scalar.return_value = 0
    mod.downgrade()
    assert [(c.args[1], c.kwargs["nullable"]) for c in mod.op.alter_column.call_args_list] == [(n, False) for n in SAML]
    assert sorted(c.args[1] for c in mod.op.drop_column.call_args_list) == sorted(OIDC)


def test_the_orm_matches():
    from app.models.postgres import SSOConfiguration

    cols = SSOConfiguration.__table__.c
    assert all(cols[name].nullable for name in SAML + OIDC)
    assert any(getattr(c, "name", "") == "ck_sso_config_fields_by_provider" for c in SSOConfiguration.__table__.constraints)
