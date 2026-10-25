"""``decision.acknowledge``: record that a person acknowledged a release item (W15 AGT-03, §0.22).

No effect (``side_effect="none"``): executing it only records the decision on
the ledger row. It is the catalogue type of the decision report's LEGACY ledger
type ``decision_report_action`` (``ACTION_TYPE_ALIASES``), so the pending rows
every deep pipeline left behind become acknowledgeable. It needs the accepted
AI report (the executor's review gate) but neither Act nor the per-type
allowlist, and it is not reversible: there is nothing to undo.

Commit-free (``STAGE_ONLY_SERVICES``); ``commits_effect = False``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agent_action_adapters.adapter_base import ActionOutcome, ActionRefused


class DecisionAcknowledgeAdapter:
    action_type = "decision.acknowledge"
    commits_effect = False

    async def preflight(self, db: AsyncSession, *, action: Any, payload: Any, approver: Any) -> None:
        return None

    async def execute(self, db: AsyncSession, *, action: Any, payload: Any, approver: Any) -> ActionOutcome:
        return ActionOutcome(result_payload={"acknowledged": True})

    async def rollback(
        self, db: AsyncSession, *, action: Any, rollback_payload: Any, actor: Any,
    ) -> ActionOutcome:
        # Not reversible: the PATCH refuses an Undo before it reaches an adapter.
        raise ActionRefused("action_not_reversible")
