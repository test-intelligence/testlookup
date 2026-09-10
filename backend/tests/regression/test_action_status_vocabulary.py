"""``str()`` on an ActionStatus member is not its stored vocabulary.

Found while gating the DefectCommander Jira write (re-audit C4); this is a
separate, pre-existing defect on the service promotion path.

``ActionStatus`` is a ``(str, PyEnum)`` mixin. The member *is* the string
``"approved"`` for concatenation and DB binding, but ``str(member)`` goes
through ``Enum.__str__`` and renders ``"ActionStatus.APPROVED"``. The promotion
service did ``initial_status = str(policy_result["initial_status"])`` and then:

* compared it to ``ActionStatus.APPROVED`` — always False, so an approved
  promotion could never file its Jira ticket;
* compared it to ``ActionStatus.PENDING_REVIEW`` — always False, so the
  ``pending_review`` promotion metric never incremented (this codebase's
  "declaration is not emission" class);
* wrote it to ``Defect.approval_status``, a ``String(20)`` column, as a
  21-character value — an overflow on every promotion;
* returned it to API callers as a status outside the documented vocabulary.

The repo already has a ``backend.status-enum-vocab`` quality gate for exactly
this class, but it only inspects columns literally named ``status`` — which is
re-audit finding L3, and why this went unnoticed.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.models.postgres import Defect
from app.services.action_policy import ActionStatus


def test_str_on_a_member_is_not_the_stored_value():
    """Pin the trap itself, so the fix below has something to protect."""
    assert str(ActionStatus.APPROVED) == "ActionStatus.APPROVED"
    assert ActionStatus.APPROVED.value == "approved"
    assert str(ActionStatus.APPROVED) != ActionStatus.APPROVED


@pytest.mark.parametrize("member", list(ActionStatus))
def test_every_status_value_fits_the_column_and_its_repr_would_not(member):
    """The overflow half of the defect, for every member not just one."""
    column_length = Defect.__table__.columns["approval_status"].type.length
    assert len(member.value) <= column_length

    if len(str(member)) > column_length:
        # e.g. "ActionStatus.PENDING_REVIEW" is 27 > 20 — this is what the
        # service used to write.
        assert str(member) != member.value


@pytest.mark.parametrize("member", list(ActionStatus))
def test_normalised_value_compares_equal_to_the_member(member):
    """The idiom the fix uses must actually restore the comparisons."""
    normalised = ActionStatus(member).value
    assert normalised == member
    assert normalised == member.value


def _promotion_source() -> str:
    from app.services import defect_promotion_service

    return inspect.getsource(defect_promotion_service)


def test_promotion_service_never_stringifies_the_policy_status():
    """The exact line that caused it must not come back."""
    source = _promotion_source()
    assert 'str(policy_result["initial_status"])' not in source, (
        "defect_promotion_service stringifies the policy status again; that "
        "renders 'ActionStatus.APPROVED', which overflows String(20) and is "
        "never equal to the member it is compared against"
    )
    assert 'ActionStatus(policy_result["initial_status"]).value' in source, (
        "the policy status is no longer normalised through the enum"
    )


def test_agent_path_uses_the_same_normalisation():
    from app.agents import defect_commander

    source = inspect.getsource(defect_commander)
    assert 'str(policy_result["initial_status"])' not in source
    assert 'ActionStatus(policy_result["initial_status"]).value' in source


def test_no_status_comparison_is_left_against_a_stringified_member():
    """A sweep, so a second site cannot reintroduce the same shape."""
    source = _promotion_source()
    offenders = re.findall(r"str\(\s*\w*status\w*\s*\)", source, re.IGNORECASE)
    assert not offenders, (
        f"stringified status values found in defect_promotion_service: {offenders}"
    )
