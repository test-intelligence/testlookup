"""Job roles through SSO, SCIM, the MFA policy, feature flags and dev-login (enterprise roles E00-S05/S06).

* SSO: a group mapped to a job role ADDS it as an extra role beside the rank the groups resolve to; the IdP
  manages extra roles only when its mapping names a job role. ``default_role`` stays a rank; AGENT is never
  mappable.
* SCIM groups: the highest rank wins the membership's role; job-role groups add extra roles, and leaving the
  group removes them. A project only job-role groups grant gets VIEWER as its role.
* MFA: ``required_roles`` require MFA whatever the floor; the floor must be a rank.
* Feature flags: an extra role passes a role allow-list.
* Dev-login: every slug has defaults; AGENT is not a slug.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models.postgres import UserRole
from app.services import scim_group_service as sgs
from app.services import sso_service
from app.services.mfa_service import MfaPolicy, clean_required_roles, policy_from_dict, policy_requires_mfa

pytestmark = pytest.mark.regression

MAPPING = {"qa": "QA_ENGINEER", "leads": "QA_LEAD", "release": "RELEASE_MANAGER", "devs": "DEVELOPER",
           "bots": "AGENT"}


# -- SSO ----------------------------------------------------------------------

def test_job_role_groups_add_extra_roles_and_never_the_rank():
    groups = ["qa", "release", "devs", "bots"]
    assert sso_service.resolve_role_from_groups(groups, MAPPING, UserRole.VIEWER) == UserRole.QA_ENGINEER
    assert sso_service.resolve_additional_roles_from_groups(groups, MAPPING) == ["RELEASE_MANAGER", "DEVELOPER"]


def test_only_job_role_groups_resolve_to_the_default_rank():
    assert sso_service.resolve_role_from_groups(["release"], MAPPING, UserRole.TESTER) == UserRole.TESTER
    assert sso_service.resolve_additional_roles_from_groups(["release"], MAPPING) == ["RELEASE_MANAGER"]


def test_the_idp_manages_extra_roles_only_when_the_mapping_names_one():
    assert sso_service.mapping_names_job_roles(MAPPING) is True
    assert sso_service.mapping_names_job_roles({"qa": "QA_ENGINEER", "bots": "AGENT"}) is False
    assert sso_service.mapping_names_job_roles(None) is False


def test_the_preview_names_extra_roles_and_their_groups():
    out = sso_service.preview_role_from_groups(["qa", "release", "devs", "devs2"], {**MAPPING, "devs2": "DEVELOPER"},
                                               UserRole.VIEWER)
    assert out["role"] == "QA_ENGINEER"
    assert out["additional_roles"] == ["RELEASE_MANAGER", "DEVELOPER"]
    assert out["additional_matched_groups"] == {"RELEASE_MANAGER": ["release"], "DEVELOPER": ["devs", "devs2"]}


async def test_the_preview_route_accepts_job_roles_but_not_agent_or_a_job_default(monkeypatch):
    from app.models.schemas import SSORolePreviewRequest
    from app.routers import sso

    config = SimpleNamespace(role_mapping={}, default_role="VIEWER")
    db = MagicMock()

    async def _execute(*_a, **_k):
        r = MagicMock()
        r.scalar_one_or_none.return_value = config
        return r

    db.execute = _execute
    call = sso.preview_sso_role
    ok = await call(uuid.uuid4(), SSORolePreviewRequest(groups=["release"], role_mapping={"release": "RELEASE_MANAGER"}),
                    db=db, current_user=None)
    assert ok["additional_roles"] == ["RELEASE_MANAGER"]
    for bad in ({"role_mapping": {"bots": "AGENT"}}, {"default_role": "DEVELOPER"}):
        with pytest.raises(HTTPException) as exc:
            await call(uuid.uuid4(), SSORolePreviewRequest(groups=["x"], **bad), db=db, current_user=None)
        assert exc.value.status_code == 400


def test_sso_mappable_roles_are_the_ranks_then_the_job_roles():
    from app.routers.sso import _MAPPABLE_ROLES

    assert _MAPPABLE_ROLES == ("VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN",
                               "RELEASE_MANAGER", "PROJECT_MANAGER", "PRODUCT_MANAGER", "DEVELOPER")


# -- SCIM groups -----------------------------------------------------------------

class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_a, **_k):
        return _Rows(self.rows)


async def test_scim_job_role_groups_add_extra_roles_beside_the_highest_rank():
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    rows = [  # ordered by group name, as the query orders them
        (p1, "DEVELOPER", "a-devs"),
        (p1, "QA_ENGINEER", "b-qa"),
        (p1, "RELEASE_MANAGER", "c-release"),
        (p1, "TESTER", "d-testers"),
        (p2, "PRODUCT_MANAGER", "e-product"),
    ]
    grants = await sgs.desired_grants(_Db(rows), uuid.uuid4())
    assert grants[p1] == ("QA_ENGINEER", "scim:b-qa", ("RELEASE_MANAGER", "DEVELOPER"))
    assert grants[p2] == ("VIEWER", "scim:e-product", ("PRODUCT_MANAGER",))


def test_scim_groups_map_to_ranks_below_admin_and_the_job_roles():
    assert sgs.PROJECT_ROLES == ("VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD",
                                 "RELEASE_MANAGER", "PROJECT_MANAGER", "PRODUCT_MANAGER", "DEVELOPER")
    assert "AGENT" not in sgs.PROJECT_ROLES and "ADMIN" not in sgs.PROJECT_ROLES


async def test_scim_reconcile_adds_and_removes_extra_roles(monkeypatch):
    user_id, project = uuid.uuid4(), uuid.uuid4()
    member = SimpleNamespace(project_id=project, role="QA_ENGINEER", source="scim:b-qa", additional_roles=None)

    class _Session:
        def __init__(self):
            self.added, self.deleted = [], []

        async def execute(self, *_a, **_k):
            r = MagicMock()
            r.scalars.return_value.all.return_value = [member]
            return r

        def add(self, obj):
            self.added.append(obj)

        async def delete(self, obj):
            self.deleted.append(obj)

        async def flush(self):
            pass

    want = {project: ("QA_ENGINEER", "scim:b-qa", ("RELEASE_MANAGER",))}

    async def _desired(db, uid):
        return want

    monkeypatch.setattr(sgs, "desired_grants", _desired)
    changes: list = []
    counts = await sgs.reconcile_user(_Session(), user_id, changes)
    assert member.additional_roles == ["RELEASE_MANAGER"] and counts["updated"] == 1
    assert changes == [("member.role_changed", project, user_id, "QA_ENGINEER")]
    want[project] = ("QA_ENGINEER", "scim:b-qa", ())  # left the release group
    await sgs.reconcile_user(_Session(), user_id)
    assert member.additional_roles is None


# -- MFA -----------------------------------------------------------------------

def _person(role, additional=None):
    return SimpleNamespace(id=uuid.uuid4(), role=role, additional_roles=additional)


def test_required_roles_need_mfa_whatever_the_floor():
    policy = MfaPolicy(require_mfa=True, required_for_role="ADMIN", required_roles=("DEVELOPER",))
    assert policy_requires_mfa(_person("DEVELOPER"), policy) is True
    assert policy_requires_mfa(_person("QA_ENGINEER", ["DEVELOPER"]), policy) is True
    assert policy_requires_mfa(_person("QA_LEAD"), policy) is False
    assert policy_requires_mfa(_person("ADMIN"), policy) is True


def test_required_roles_are_cleaned_when_read():
    assert clean_required_roles(["DEVELOPER", "AGENT", "nope", "QA_LEAD", "DEVELOPER"]) == ("QA_LEAD", "DEVELOPER")
    assert clean_required_roles("DEVELOPER") == ()
    stored = policy_from_dict({"require_mfa": True, "required_for_role": "QA_LEAD", "required_roles": ["DEVELOPER"]})
    assert stored.required_roles == ("DEVELOPER",)
    assert policy_from_dict({"require_mfa": True}).required_roles == ()
    assert stored.as_dict()["required_roles"] == ["DEVELOPER"]


async def test_the_policy_route_refuses_a_job_role_floor_and_agent(monkeypatch):
    from app.models.schemas import MfaPolicyUpdate
    from app.routers import app_settings
    from app.services import mfa_service

    async def _load(db):
        return MfaPolicy()

    monkeypatch.setattr(mfa_service, "load_policy", _load)
    for payload in (MfaPolicyUpdate(required_for_role=UserRole.RELEASE_MANAGER),
                    MfaPolicyUpdate(required_roles=[UserRole.AGENT])):
        with pytest.raises(HTTPException) as exc:
            await app_settings.update_mfa_policy(payload, current_user=_person(UserRole.ADMIN), db=MagicMock())
        assert exc.value.status_code == 400


# -- feature flags -------------------------------------------------------------

def test_an_extra_role_passes_a_role_allow_list():
    from app.services.feature_flags import _evaluate

    flag = {"key": "x", "enabled_global": True, "enabled_roles": ["RELEASE_MANAGER"], "rollout_percent": 100}
    assert _evaluate(flag, user=_person("QA_ENGINEER", ["RELEASE_MANAGER"]), project_id=None) is True
    assert _evaluate(flag, user=_person("QA_ENGINEER"), project_id=None) is False
    assert _evaluate(flag, user=_person("RELEASE_MANAGER"), project_id=None) is True


# -- dev-login -----------------------------------------------------------------

def test_every_dev_login_slug_has_defaults_and_agent_is_not_one():
    from app.routers.auth import _DEV_ROLE_DEFAULTS, _DEV_ROLE_MAP

    assert set(_DEV_ROLE_MAP.values()) == set(_DEV_ROLE_DEFAULTS)
    assert set(_DEV_ROLE_MAP.values()) == {r for r in UserRole if r is not UserRole.AGENT}
    for defaults in _DEV_ROLE_DEFAULTS.values():
        assert defaults["email"].endswith("@testlookup.dev")


def test_the_dev_seed_has_every_role_but_agent_and_one_multi_job_person():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "seed_dev_data.py"
    source = path.read_text(encoding="utf-8")
    for role in UserRole:
        if role is UserRole.AGENT:
            assert "UserRole.AGENT" not in source
        else:
            assert f'"role": UserRole.{role.name}' in source, role
    assert '"additional_roles": [UserRole.RELEASE_MANAGER.value]' in source
    assert importlib.util.find_spec("app") is not None


def test_the_demo_seed_has_all_five_ranks():
    """D-8: scripts/seed_data.py seeds a TESTER too (never run it on the homelab: it resets these passwords)."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "scripts" / "seed_data.py").read_text(encoding="utf-8")
    for rank in ("ADMIN", "QA_LEAD", "QA_ENGINEER", "TESTER", "VIEWER"):
        assert f'"role": "{rank}"' in source, rank
