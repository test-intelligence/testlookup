"""Agent-action adapters (W15 AGT-03, owner decision D16).

``adapter_base`` holds the protocol every adapter implements, the registry
skeleton (``register`` / ``get_adapter`` / ``registered_types``) and the two
execute-time checks the executor and the PATCH share (``load_approver``,
``act_refusal``). ``agent_action_registry`` registers the four adapters
explicitly: ``quarantine.apply``, ``failure.reassign``, ``defect.promote``
(internal, reversible, each opt-in per project) and ``decision.acknowledge``
(no effect). No external adapter exists; ``register`` refuses one.

Every module of this package is commit-free: the executor's claim / finish
helpers and the routers commit.
"""
