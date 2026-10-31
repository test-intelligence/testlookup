"""Reconcile the legacy suite memberships with the canonical catalog, project by project (W25a Day-0 follow-up).

Runs INSIDE the backend pod, fed on stdin (no file path is needed):

    kubectl exec -i <api-pod> -- env PYTHONPATH=/app python - < backend/scripts/reconcile_legacy_suite_memberships.py
    kubectl exec -i <api-pod> -- env PYTHONPATH=/app python - --apply < backend/scripts/reconcile_legacy_suite_memberships.py
    ... python - --apply --project-id <uuid> < backend/scripts/reconcile_legacy_suite_memberships.py

DRY RUN by default: the counts of what would be written, nothing written. ``--apply`` writes and COMMITS one
project at a time — one session per project, closed before the next (the production worker pool is 1 + 1).
Every project is visited (archived included, as the parity runner does). For each project it prints the id
PREFIX, ``app.services.suite_membership_reconcile``'s counts and the parity ``blocking`` count read afterwards
— never a test name, a suite name, a fingerprint or a secret. Exit 0.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from typing import Optional


async def _project_ids(only: Optional[uuid.UUID]) -> list[uuid.UUID]:
    from sqlalchemy import select

    from app.db.postgres import get_session_factory
    from app.models.postgres import Project

    async with get_session_factory()() as db:
        query = select(Project.id).order_by(Project.id)
        if only is not None:
            query = query.where(Project.id == only)
        return list((await db.execute(query)).scalars().all())


async def _one(project_id: uuid.UUID, *, apply: bool) -> dict[str, int]:
    from app.core.config import settings
    from app.db.postgres import get_session_factory
    from app.services.suite_membership_parity import compare_project
    from app.services.suite_membership_reconcile import reconcile_project

    async with get_session_factory()() as db:
        result = await reconcile_project(db, project_id, apply=apply)
        if apply:
            await db.commit()
        else:
            await db.rollback()
        parity = await compare_project(db, project_id, window_runs=settings.CANONICAL_DELETION_WINDOW_RUNS)
        await db.rollback()
    counts = result.counts()
    counts["blocking"] = sum(1 for d in parity.divergences if d.blocking)
    return counts


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="reconcile_legacy_suite_memberships")
    parser.add_argument("--apply", action="store_true", help="write and commit (default: dry run, nothing written)")
    parser.add_argument("--project-id", type=uuid.UUID, default=None, help="only this project")
    args = parser.parse_args(argv)

    from app.db.postgres import dispose_engine_for_loop
    from app.services.suite_membership_reconcile import ReconcileResult

    mode = "apply" if args.apply else "dry-run"
    totals = dict.fromkeys([*ReconcileResult(project_id=uuid.UUID(int=0), applied=False).counts(), "blocking"], 0)
    try:
        projects = await _project_ids(args.project_id)
        print(f"mode={mode} projects={len(projects)}")
        for project_id in projects:
            counts = await _one(project_id, apply=args.apply)
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + value
            print(f"{str(project_id)[:8]} " + " ".join(f"{key}={value}" for key, value in counts.items()))
        print(f"TOTAL mode={mode} " + " ".join(f"{key}={value}" for key, value in totals.items()))
    finally:
        await dispose_engine_for_loop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
