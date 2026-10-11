"""Fixtures for the route/action sweeps (frontend/tests/sweeps), printed as JSON.

Run INSIDE the backend container of the deployment to sweep -- it reads that
deployment's database and mints short-lived tokens, so no password is ever
typed into the app:

    kubectl exec -i deploy/testlookup-backend -c backend -- \\
        python - --project <project-id> --users admin,qa_lead,viewer < scripts/sweep_fixtures.py \\
        > /tmp/sweep-fixtures.json          # outside the repo: it holds tokens

The JSON names one real id for every parameterised route (a failing run and
one of its failed tests, a passing run, a suite, a canonical test, a release,
an agent pipeline run, a gate policy) and a token per user. Tokens expire
after ``--minutes`` (default 60).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import timedelta

from sqlalchemy import select, text

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import User
from app.services.auth_session_tokens import issue_access_jwt

QUERIES = {
    "failedRun": "select id from test_runs where project_id = :p and failed_tests > 0 order by created_at desc limit 1",
    "passedRun": "select id from test_runs where project_id = :p and coalesce(failed_tests, 0) = 0 "
                 "and total_tests > 0 order by created_at desc limit 1",
    "suite": "select id from test_suites where project_id = :p order by created_at desc limit 1",
    "canonical": "select id from canonical_test_cases where project_id = :p limit 1",
    "release": "select id from releases where project_id = :p order by created_at desc limit 1",
    "pipeline": "select a.id from agent_pipeline_runs a join test_runs r on r.id = a.test_run_id "
                "where r.project_id = :p order by a.created_at desc limit 1",
    "policy": "select id from release_gate_policies where project_id = :p limit 1",
}


async def main(project: str, users: list[str], minutes: int) -> None:
    pid = uuid.UUID(project)
    async with AsyncSessionLocal() as db:
        ids: dict[str, str | None] = {"project": str(pid)}
        for key, sql in QUERIES.items():
            row = (await db.execute(text(sql), {"p": pid})).first()
            ids[key] = str(row[0]) if row else None
        for run_key, test_key, status in (("failedRun", "failedTest", "('FAILED','BROKEN')"),
                                          ("passedRun", "passedTest", "('PASSED')")):
            if ids[run_key]:
                row = (await db.execute(text(
                    f"select id from test_cases where test_run_id = :r and status in {status} limit 1"),
                    {"r": ids[run_key]})).first()
                ids[test_key] = str(row[0]) if row else None
        tokens = {}
        for name in users:
            user = (await db.execute(select(User).where(User.username == name))).scalar_one()
            tokens[name] = await issue_access_jwt(db, str(user.id), timedelta(minutes=minutes))
    print(json.dumps({"ids": ids, "tokens": tokens}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--users", default="admin")
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()
    asyncio.run(main(args.project, [u for u in args.users.split(",") if u], args.minutes))
