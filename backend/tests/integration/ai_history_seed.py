"""Seeding helpers shared by the W03 G-A live-database tests (not collected: no ``test_`` prefix).

Every helper writes through the ORM into a project of its own (unique slug), so
a test removes everything it made by deleting that project's runs and the
project (``test_cases``, ``ai_analysis``, ``ai_analysis_versions``,
``agent_pipeline_runs`` all CASCADE from the run).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import pytest
from sqlalchemy import delete


def dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def seed_project(db, label: str = "aih") -> uuid.UUID:
    from app.models.postgres import Project

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name=f"{label} {token}", slug=f"{label}-{token}"))
    await db.flush()
    return project_id


async def seed_user(db, label: str = "anna") -> uuid.UUID:
    from app.models.postgres import User, UserRole

    uid = uuid.uuid4()
    tag = uid.hex[:10]
    db.add(User(id=uid, email=f"gah-{label}-{tag}@example.com", username=f"gah_{label}_{tag}",
                full_name=label.title(), hashed_password="!unusable", role=UserRole.QA_ENGINEER.value))
    await db.flush()
    return uid


async def seed_run(
    db,
    project_id: uuid.UUID,
    n_cases: int,
    *,
    build: Optional[str] = None,
    suite: str = "checkout",
    status: str = "FAILED",
    fingerprints: Optional[Sequence[str]] = None,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    from app.models.postgres import LaunchStatus, TestCase, TestRun

    run_id = uuid.uuid4()
    db.add(TestRun(
        id=run_id, project_id=project_id, build_number=build or f"gah-{run_id.hex[:10]}",
        status=LaunchStatus.FAILED, branch="main", primary_suite_name=suite,
        total_tests=n_cases, failed_tests=n_cases, broken_tests=0, pass_rate=0.0,
    ))
    await db.flush()
    case_ids = []
    for i in range(n_cases):
        cid = uuid.uuid4()
        fp = fingerprints[i] if fingerprints else f"fp{run_id.hex[:6]}{i:04d}"
        db.add(TestCase(id=cid, test_run_id=run_id, test_name=f"test_case_{i}", suite_name=suite,
                        test_fingerprint=fp, status=status, error_message=f"boom {i}"))
        case_ids.append(cid)
    await db.flush()
    return run_id, case_ids


async def seed_pipeline(db, run_id: uuid.UUID, status: str = "completed") -> uuid.UUID:
    from app.models.postgres import AgentPipelineRun

    pid = uuid.uuid4()
    db.add(AgentPipelineRun(id=pid, test_run_id=run_id, workflow_type="offline", status=status,
                            completed_at=datetime.now(timezone.utc)))
    await db.flush()
    return pid


async def seed_analysis(db, case_id: uuid.UUID, **fields: Any) -> uuid.UUID:
    from app.models.postgres import AIAnalysis

    values = {
        "failure_category": "PRODUCT_BUG", "confidence_score": 80, "root_cause_summary": "Tax rounds twice",
        "is_flaky": False, "backend_error_found": False, "pod_issue_found": False,
        "requires_human_review": False, "llm_provider": "ollama", "llm_model": "llama3.1",
        "routing_metadata": {"analysis_mode": "llm"}, "recommended_actions": ["fix it"],
    }
    values.update(fields)
    aid = uuid.uuid4()
    db.add(AIAnalysis(id=aid, test_case_id=case_id, **values))
    await db.flush()
    return aid


async def drop_project(factory, project_id: uuid.UUID, user_ids: Sequence[uuid.UUID] = ()) -> None:
    from app.models.postgres import Project, ProjectActivityEvent, ProjectRetentionPolicy, TestRun, User

    async with factory() as db:
        await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project_id))
        await db.execute(delete(ProjectRetentionPolicy).where(ProjectRetentionPolicy.project_id == project_id))
        await db.execute(delete(TestRun).where(TestRun.project_id == project_id))
        await db.execute(delete(Project).where(Project.id == project_id))
        if user_ids:
            await db.execute(delete(User).where(User.id.in_(list(user_ids))))
        await db.commit()


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    async def to_list(self, length=None):
        return list(self._rows)


class FakeCollection:
    """Only what ``cited_pipeline_ids`` reads: published decision reports."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries: list = []

    def find(self, query, projection=None):
        self.queries.append(query)
        runs = set((query.get("test_run_id") or {}).get("$in") or [])
        return FakeCursor([
            {"pipeline_run_id": r["pipeline_run_id"]} for r in self.rows
            if (not runs or r.get("test_run_id") in runs) and r.get("status") == "published"
        ])


class FakeMongo:
    def __init__(self, reports=()):
        self.reports = FakeCollection(reports)

    def __getitem__(self, name):
        return self.reports
