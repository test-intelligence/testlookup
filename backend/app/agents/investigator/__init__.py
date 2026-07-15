"""Hypothesis-loop Investigator (Agentic plan AI-1, shadow mode).

Public entry point: :func:`app.agents.investigator.workflow.run_investigation`
(dispatched by the ``run_agent_investigation`` Celery task on the
``ai_analysis`` queue). Lifecycle, policies, triggers, and the AI-3 ledger
live in ``app/services/agent_investigation_service.py``.
"""
from app.agents.investigator.workflow import run_investigation  # noqa: F401
