"""Pin the protected Phase 3 integration gate in the GitHub workflow."""
from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_phase3_database_gate_is_build_authoritative():
    workflow = _workflow()
    jobs = workflow["jobs"]
    integration = jobs["postgres-integration"]

    assert set(integration["services"]) == {"postgres", "mongo"}
    run_steps = [step["run"] for step in integration["steps"] if "run" in step]
    suite = next(run for run in run_steps if "test_agent_action_ledger_postgres.py" in run)
    assert "test_decision_report_supersession_postgres_mongo.py" in suite
    assert "tests/regression/test_notification_history_schema.py" in suite

    test_env = next(
        step["env"]
        for step in integration["steps"]
        if step.get("name") == "Run protected PostgreSQL integration suite"
    )
    assert test_env["TESTLOOKUP_POSTGRES_TEST_DSN"].startswith(
        "postgresql+asyncpg://"
    )
    assert test_env["MONGO_URI"] == "mongodb://localhost:27017"
    assert "postgres-integration" in jobs["build-images"]["needs"]
