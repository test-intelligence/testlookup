"""Prove T5's focused tests kill each policy-migration regression."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTEST = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "--basetemp=backend/.pytest-tmp-t5-mutation",
    "backend/tests/test_migration_0187_agent_policy_configs.py",
    "backend/tests/test_investigator_api.py",
    "backend/tests/test_fixer_config.py",
)

# (file, correct source, wrong behavior, exact replacement count)
MUTATIONS = (
    (
        "backend/app/services/agent_config_service.py",
        'COMPATIBILITY_AGENT_IDS: tuple[str, ...] = ("fixer", "investigator")',
        'COMPATIBILITY_AGENT_IDS: tuple[str, ...] = ("investigator",)',
        1,
    ),
    (
        "backend/app/services/agent_investigation_service.py",
        "extension = config.extensions.investigator or config_svc.InvestigatorConfigExtension()",
        "extension = config_svc.InvestigatorConfigExtension()",
        1,
    ),
    (
        "backend/app/services/fixer_service.py",
        '"budgets": _coerce_budgets(stored.get("budgets")),',
        '"budgets": _coerce_budgets({}),',
        1,
    ),
    (
        "backend/app/routers/agent_investigations.py",
        "status_code=status.HTTP_405_METHOD_NOT_ALLOWED,",
        "status_code=status.HTTP_200_OK,",
        1,
    ),
    (
        "backend/app/routers/fixer.py",
        "status_code=status.HTTP_405_METHOD_NOT_ALLOWED,",
        "status_code=status.HTTP_200_OK,",
        1,
    ),
    (
        "backend/migrations/versions/0187_agent_policy_configs.py",
        "p.agent_id IN ('investigator', 'fixer')",
        "p.agent_id IN ('investigator')",
        1,
    ),
    (
        "backend/app/worker/tasks.py",
        'AgentConfig.config["extensions"]["fixer"]["schedule"].astext == schedule',
        'AgentConfig.config["schedule"].astext == schedule',
        1,
    ),
)


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend")
    for relative, good, bad, expected in MUTATIONS:
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        count = original.count(good)
        if count != expected:
            raise AssertionError(
                f"mutation did not apply {expected} time(s): {relative}: {good!r} (found {count})"
            )
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {relative}: {good!r}")
        path.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                PYTEST,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        finally:
            path.write_text(original, encoding="utf-8")
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {relative}: {bad!r}")
    print(f"T5 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
