"""The Fixer — budgeted, validated-fix agent (Agentic plan AI-2).

Selects flaky/quarantined tests, generates test-code-only candidate fixes,
validates them by rerunning the test in a sandbox, and (suggest mode only)
opens a DRAFT PR. See ``workflow.run_fixer_run`` for the orchestrator.
"""
from app.agents.fixer.workflow import run_fixer_run

__all__ = ["run_fixer_run"]
