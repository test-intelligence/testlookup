"""The reviewed set of agent-action adapters (W15 AGT-03, owner decision D16).

Each adapter is registered here BY NAME, explicitly -- no discovery, no plugin
loading: an effect exists only when a person has read its module and listed it
below. ``tests/test_agent_action_adapter_registry.py`` pins the set against a
reviewed allowlist, so adding one fails until that list is edited too.

D16 scope: three internal, reversible adapters (``quarantine.apply``,
``failure.reassign``, ``defect.promote`` -- never Jira) and the no-effect
``decision.acknowledge``. ``register`` itself refuses an external one.

``adapter_base.get_adapter`` imports this module lazily on first use.
"""
from __future__ import annotations

from app.services.agent_action_adapters.acknowledge_adapter import DecisionAcknowledgeAdapter
from app.services.agent_action_adapters.adapter_base import ActionAdapter, register, registered_types
from app.services.agent_action_adapters.defect_adapter import DefectPromoteAdapter
from app.services.agent_action_adapters.quarantine_adapter import QuarantineApplyAdapter
from app.services.agent_action_adapters.reassign_adapter import FailureReassignAdapter


def _register_once(adapter: ActionAdapter) -> None:
    # A re-import (a test reloading the package) must not trip register's
    # duplicate refusal; the first registration of a type is the one that counts.
    if adapter.action_type not in registered_types():
        register(adapter)


_register_once(DecisionAcknowledgeAdapter())
_register_once(DefectPromoteAdapter())
_register_once(FailureReassignAdapter())
_register_once(QuarantineApplyAdapter())
