#!/usr/bin/env python3
"""
TestLookup — Development Seed Data Script
=============================================
Creates demo users, projects, managed test cases, test plans, test strategies,
and releases so the full dashboard is immediately usable after `make dev`.

Usage (inside the backend container via make):
    make seed-data           # seed (idempotent — skips if already seeded)
    make seed-data-reset     # wipe seed data then re-seed

Manual (inside container shell):
    docker compose exec backend python /app/scripts/seed_dev_data.py
    docker compose exec backend python /app/scripts/seed_dev_data.py --reset
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone

# ── path bootstrap (when run outside the container) ─────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import get_password_hash
from app.models.postgres import (
    ManagedTestCase,
    Project,
    ProjectMember,
    Release,
    ReleasePhase,
    TestPlan,
    TestPlanItem,
    TestStrategy,
    User,
    UserRole,
)

# ─────────────────────────────────────────────────────────────────────────────
# Seed catalogue
# ─────────────────────────────────────────────────────────────────────────────

SEED_MARKER = "seed_dev_data_v1"   # embedded in project.description to detect re-runs

# Passwords are randomly generated at seed time — not stored or displayed.
# Use the Quick Login buttons at http://localhost:3000 to access the dashboard
# without a password (backed by /api/v1/auth/dev-login, no credentials required).
USERS = [
    {"email": "admin@testlookup.dev",    "username": "admin",       "full_name": "QA Admin",         "password": secrets.token_urlsafe(20), "role": UserRole.ADMIN},
    {"email": "lead@testlookup.dev",     "username": "qa_lead",     "full_name": "QA Lead",          "password": secrets.token_urlsafe(20), "role": UserRole.QA_LEAD},
    {"email": "engineer@testlookup.dev", "username": "qa_engineer", "full_name": "QA Engineer",      "password": secrets.token_urlsafe(20), "role": UserRole.QA_ENGINEER},
    {"email": "tester@testlookup.dev",   "username": "tester",      "full_name": "Tester",           "password": secrets.token_urlsafe(20), "role": UserRole.TESTER},
    {"email": "viewer@testlookup.dev",   "username": "viewer",      "full_name": "Read-Only Viewer", "password": secrets.token_urlsafe(20), "role": UserRole.VIEWER},
]

PROJECTS = [
    {
        "name": "E-Commerce Platform",
        "slug": "ecommerce-platform",
        "description": f"Online shopping and payment flows · {SEED_MARKER}",
        "jenkins_job_pattern": "ecommerce-*",
    },
    {
        "name": "Mobile Banking App",
        "slug": "mobile-banking",
        "description": f"iOS/Android banking client · {SEED_MARKER}",
        "jenkins_job_pattern": "mobile-*",
    },
    {
        "name": "API Gateway Service",
        "slug": "api-gateway",
        "description": f"Central API routing and auth service · {SEED_MARKER}",
        "jenkins_job_pattern": "api-gateway-*",
    },
]

# (title, test_type, priority, severity, feature_area, status, suite_name, is_automated)
TEST_CASES_TEMPLATE = [
    ("Verify successful user login", "functional", "critical", "blocker", "Authentication", "active", "Auth Suite", False),
    ("Verify login with invalid credentials", "functional", "critical", "blocker", "Authentication", "active", "Auth Suite", False),
    ("Verify password reset via email link", "functional", "high", "critical", "Authentication", "active", "Auth Suite", False),
    ("Verify session expiry after inactivity timeout", "functional", "high", "major", "Authentication", "active", "Auth Suite", False),
    ("Verify checkout with valid payment card", "functional", "critical", "blocker", "Payments", "active", "Payment Suite", True),
    ("Verify checkout fails gracefully on declined card", "functional", "critical", "blocker", "Payments", "active", "Payment Suite", True),
    ("Verify partial refund processing", "functional", "high", "critical", "Payments", "review_requested", "Payment Suite", False),
    ("Verify API response time is under 200ms", "performance", "high", "major", "Performance", "active", "Performance Suite", True),
    ("Verify API handles 1000 concurrent requests", "performance", "medium", "major", "Performance", "draft", "Performance Suite", True),
    ("Verify SQL injection payloads are blocked", "security", "critical", "blocker", "Security", "active", "Security Suite", True),
    ("Verify XSS prevention on all input fields", "security", "critical", "blocker", "Security", "active", "Security Suite", True),
    ("Verify RBAC enforcement on admin endpoints", "security", "high", "critical", "Security", "active", "Security Suite", False),
    ("Verify search returns correct paginated results", "functional", "medium", "minor", "Search", "active", "Search Suite", False),
    ("Verify pagination on large data sets", "functional", "low", "trivial", "Search", "draft", "Search Suite", False),
    ("Verify smoke test passes on fresh deploy", "smoke", "critical", "blocker", "Smoke", "active", "Smoke Suite", True),
]

STEPS_TEMPLATE = [
    {"step_number": 1, "action": "Navigate to the feature entry point", "expected_result": "Page loads successfully within 2 seconds"},
    {"step_number": 2, "action": "Perform the primary action with valid test data", "expected_result": "System responds with expected success state"},
    {"step_number": 3, "action": "Verify the outcome matches acceptance criteria", "expected_result": "All assertions pass; UI/API reflects correct state"},
]

STRATEGY_TEMPLATE = {
    "version_label": "v1.0",
    "status": "approved",
    "objective": "Ensure the application meets all functional, performance, and security requirements before release.",
    "scope": "All features in the current sprint: authentication, payments, search, and API endpoints.",
    "out_of_scope": "Third-party payment gateway internals, infrastructure provisioning, DNS changes.",
    "test_approach": "Risk-based testing with emphasis on critical payment flows. Automation-first for regression suite; exploratory for edge cases.",
    "risk_assessment": [
        {"risk": "Payment processing failures", "likelihood": "medium", "impact": "high", "mitigation": "Dedicated payment regression suite run in sandbox environment on every PR"},
        {"risk": "Performance degradation under peak load", "likelihood": "low", "impact": "high", "mitigation": "Locust load tests run nightly against staging; p99 alert set at 450ms"},
        {"risk": "Security vulnerabilities in authentication", "likelihood": "low", "impact": "critical", "mitigation": "Automated OWASP ZAP scans on every PR; quarterly penetration test"},
    ],
    "test_types": [
        {"type": "functional", "priority": "critical", "tools": ["pytest", "playwright"], "coverage_target_pct": 90, "rationale": "Core business flows must be verified before every release"},
        {"type": "performance", "priority": "high", "tools": ["locust"], "coverage_target_pct": 70, "rationale": "SLA requires p99 < 500ms at 500 concurrent users"},
        {"type": "security", "priority": "high", "tools": ["owasp-zap", "bandit"], "coverage_target_pct": 80, "rationale": "SOC2 compliance requires automated security scanning"},
        {"type": "smoke", "priority": "critical", "tools": ["pytest"], "coverage_target_pct": 100, "rationale": "Gate every production deployment"},
    ],
    "entry_criteria": [
        "All user stories accepted by the product owner",
        "Dev environment is stable and accessible",
        "Test data has been prepared and reviewed",
        "CI pipeline is green on the release branch",
    ],
    "exit_criteria": [
        "All critical and high-priority test cases executed",
        "Zero open blocker or critical severity defects",
        "Overall pass rate >= 95%",
        "Performance SLAs verified in staging",
    ],
    "environments": [
        {"name": "dev", "type": "development", "purpose": "Developer testing and integration verification"},
        {"name": "staging", "type": "staging", "purpose": "QA regression and performance validation"},
        {"name": "prod", "type": "production", "purpose": "Post-deploy smoke tests only"},
    ],
    "automation_approach": "Playwright for UI flows, pytest for API, Locust for load. CI runs full regression on every PR; smoke on every deploy.",
    "defect_management": "All defects logged in Jira with priority/severity. Blockers block the release. Critical defects require hotfix within 24 hours.",
}

RELEASES_TEMPLATE = [
    {
        "name": "v1.0.0 — Initial Release",
        "version": "1.0.0",
        "status": "in_progress",
        "planned_date": datetime.now(timezone.utc) + timedelta(days=7),
        "phases": [
            {"name": "Feature Freeze", "phase_type": "code_freeze", "status": "completed", "order_index": 1},
            {"name": "QA Regression", "phase_type": "qa_testing", "status": "in_progress", "order_index": 2},
            {"name": "UAT Sign-off", "phase_type": "uat", "status": "pending", "order_index": 3},
            {"name": "Production Deploy", "phase_type": "production", "status": "pending", "order_index": 4},
        ],
    },
    {
        "name": "v1.1.0 — Performance Improvements",
        "version": "1.1.0",
        "status": "planning",
        "planned_date": datetime.now(timezone.utc) + timedelta(days=35),
        "phases": [
            {"name": "Development Complete", "phase_type": "development", "status": "pending", "order_index": 1},
            {"name": "QA Regression", "phase_type": "qa_testing", "status": "pending", "order_index": 2},
            {"name": "Release", "phase_type": "production", "status": "pending", "order_index": 3},
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _already_seeded(db: AsyncSession) -> bool:
    result = await db.execute(
        select(Project).where(Project.description.contains(SEED_MARKER)).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _wipe_seed_data(db: AsyncSession) -> None:
    print("🗑️  Wiping existing seed data…")
    result = await db.execute(
        select(Project.id).where(Project.description.contains(SEED_MARKER))
    )
    project_ids = [row[0] for row in result.all()]

    if project_ids:
        for pid in project_ids:
            # Phases → Releases
            release_ids_result = await db.execute(
                select(Release.id).where(Release.project_id == pid)
            )
            release_ids = [r[0] for r in release_ids_result.all()]
            if release_ids:
                for rid in release_ids:
                    await db.execute(delete(ReleasePhase).where(ReleasePhase.release_id == rid))
            await db.execute(delete(Release).where(Release.project_id == pid))

            # Plan items → Plans
            plan_ids_result = await db.execute(
                select(TestPlan.id).where(TestPlan.project_id == pid)
            )
            plan_ids = [r[0] for r in plan_ids_result.all()]
            if plan_ids:
                for plid in plan_ids:
                    await db.execute(delete(TestPlanItem).where(TestPlanItem.plan_id == plid))
            await db.execute(delete(TestPlan).where(TestPlan.project_id == pid))

            await db.execute(delete(TestStrategy).where(TestStrategy.project_id == pid))
            await db.execute(delete(ManagedTestCase).where(ManagedTestCase.project_id == pid))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id == pid))

        await db.execute(delete(Project).where(Project.id.in_(project_ids)))

    seeded_emails = [u["email"] for u in USERS]
    await db.execute(delete(User).where(User.email.in_(seeded_emails)))

    await db.commit()
    print("   ✅ Wipe complete")


# ─────────────────────────────────────────────────────────────────────────────
# Seed functions
# ─────────────────────────────────────────────────────────────────────────────

async def _seed_users(db: AsyncSession) -> dict[str, User]:
    print("👤  Creating demo users…")
    users: dict[str, User] = {}
    for u in USERS:
        existing = (await db.execute(
            select(User).where(User.email == u["email"])
        )).scalar_one_or_none()
        if existing:
            users[u["role"]] = existing
            print(f"   ⏭  {u['email']} already exists — skipped")
            continue
        user = User(
            id=uuid.uuid4(),
            email=u["email"],
            username=u["username"],
            full_name=u["full_name"],
            hashed_password=get_password_hash(u["password"]),
            role=u["role"],
            is_active=True,
        )
        db.add(user)
        users[u["role"]] = user
        print(f"   ✅  {u['email']}  role={u['role']}  password={u['password']}")
    await db.flush()
    return users


async def _seed_projects(db: AsyncSession, users: dict[str, User]) -> list[Project]:
    print("📁  Creating demo projects…")
    projects: list[Project] = []
    for p in PROJECTS:
        existing = (await db.execute(
            select(Project).where(Project.slug == p["slug"])
        )).scalar_one_or_none()
        if existing:
            projects.append(existing)
            print(f"   ⏭  {p['name']} already exists — skipped")
            continue
        project = Project(
            id=uuid.uuid4(),
            name=p["name"],
            slug=p["slug"],
            description=p["description"],
            jenkins_job_pattern=p["jenkins_job_pattern"],
            is_active=True,
        )
        db.add(project)
        projects.append(project)

        # Add all demo users as project members
        for role_key, user in users.items():
            db.add(ProjectMember(
                id=uuid.uuid4(),
                project_id=project.id,
                user_id=user.id,
                role=str(role_key),
            ))
        print(f"   ✅  {p['name']}  ({p['slug']})")
    await db.flush()
    return projects


async def _seed_test_cases(
    db: AsyncSession, project: Project, author: User
) -> list[ManagedTestCase]:
    cases: list[ManagedTestCase] = []
    for title, tc_type, priority, severity, feature_area, status, suite_name, is_automated in TEST_CASES_TEMPLATE:
        tc = ManagedTestCase(
            id=uuid.uuid4(),
            project_id=project.id,
            title=f"[{project.name}] {title}",
            description=f"Verify that {title.lower()} works correctly end-to-end.",
            objective=f"Ensure {title.lower()} meets the defined acceptance criteria.",
            preconditions="User account exists, environment is healthy, and test data is prepared.",
            steps=STEPS_TEMPLATE,
            expected_result="Feature behaves as specified in acceptance criteria with no errors.",
            test_type=tc_type,
            priority=priority,
            severity=severity,
            feature_area=feature_area,
            suite_name=suite_name,
            status=status,
            tags=[tc_type, feature_area.lower().replace(" ", "-")],
            is_automated=is_automated,
            automation_status="automated" if is_automated else "manual",
            ai_generated=False,
            version=1,
            author_id=author.id,
        )
        db.add(tc)
        cases.append(tc)
    await db.flush()
    return cases


async def _seed_test_plan(
    db: AsyncSession, project: Project, cases: list[ManagedTestCase], created_by: User
) -> None:
    plan = TestPlan(
        id=uuid.uuid4(),
        project_id=project.id,
        name=f"{project.name} — Sprint Regression Plan",
        description="Regression test plan covering all critical acceptance criteria for the current sprint.",
        objective="Verify all acceptance criteria pass with a pass rate >= 95% before release.",
        status="active",
        planned_start_date=_now() - timedelta(days=3),
        planned_end_date=_now() + timedelta(days=4),
        created_by_id=created_by.id,
        ai_generated=False,
        total_cases=len(cases),
        executed_cases=0,
        passed_cases=0,
        failed_cases=0,
        blocked_cases=0,
    )
    db.add(plan)
    await db.flush()

    for idx, tc in enumerate(cases):
        db.add(TestPlanItem(
            id=uuid.uuid4(),
            plan_id=plan.id,
            test_case_id=tc.id,
            order_index=idx + 1,
            execution_status="not_run",
        ))
    await db.flush()


async def _seed_strategy(
    db: AsyncSession, project: Project, created_by: User
) -> None:
    t = STRATEGY_TEMPLATE
    db.add(TestStrategy(
        id=uuid.uuid4(),
        project_id=project.id,
        name=f"{project.name} — Test Strategy v1.0",
        version_label=t["version_label"],
        status=t["status"],
        objective=t["objective"],
        scope=t["scope"],
        out_of_scope=t["out_of_scope"],
        test_approach=t["test_approach"],
        risk_assessment=t["risk_assessment"],
        test_types=t["test_types"],
        entry_criteria=t["entry_criteria"],
        exit_criteria=t["exit_criteria"],
        environments=t["environments"],
        automation_approach=t["automation_approach"],
        defect_management=t["defect_management"],
        ai_generated=False,
        created_by_id=created_by.id,
    ))
    await db.flush()


async def _seed_releases(
    db: AsyncSession, project: Project, created_by: User
) -> None:
    for rel_def in RELEASES_TEMPLATE:
        release = Release(
            id=uuid.uuid4(),
            project_id=project.id,
            name=rel_def["name"],
            version=rel_def["version"],
            status=rel_def["status"],
            planned_date=rel_def["planned_date"],
            created_by_id=created_by.id,
        )
        db.add(release)
        await db.flush()

        for phase_def in rel_def["phases"]:
            db.add(ReleasePhase(
                id=uuid.uuid4(),
                release_id=release.id,
                name=phase_def["name"],
                phase_type=phase_def["phase_type"],
                status=phase_def["status"],
                order_index=phase_def["order_index"],
            ))
        await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def _seed_execution_history(project_ids: list[str]) -> None:
    """Seed realistic *execution* history (runs + per-test results over ~30 days)
    so dashboards / flaky-coach / trends / failures are populated on first run.

    Best-effort and fully isolated: it runs AFTER the core seed has committed,
    opens its own sessions, and swallows any error per run — a problem here can
    never break the user/project/case seed above. Feeds synthetic runs through
    the REAL ingestion pipeline (so every derived table is correct), then
    backdates the run + its rows so the history spans the trend window.
    """
    import uuid as _uuid
    from datetime import datetime, timezone

    from sqlalchemy import update

    try:
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import TestCase, TestCaseHistory
        from app.services.demo_dataset import generate_demo_runs
        from app.services.ingestion_pipeline import (
            create_run_from_payload,
            finalize_run,
            ingest_test_results,
        )
    except Exception as exc:  # pragma: no cover — defensive
        print(f"⏭️  Skipping execution-history seed (imports unavailable): {exc}")
        return

    now = datetime.now(timezone.utc)
    print("📈  Seeding execution history (runs, failures, flaky, trends)…")
    for pid in project_ids:
        seeded = 0
        try:
            for spec in generate_demo_runs(runs=14, now=now):
                run_id = str(_uuid.uuid4())
                async with AsyncSessionLocal() as db:
                    run = await create_run_from_payload(
                        db, project_id=pid, build_number=spec.build_number,
                        run_id=run_id, branch=spec.branch, framework=spec.framework,
                        trigger_source="demo", ingestion_source="demo",
                    )
                    await ingest_test_results(db, run, spec.results)
                    ts = spec.started_at
                    run.start_time = ts
                    run.end_time = ts
                    run.created_at = ts
                    await db.execute(update(TestCase).where(
                        TestCase.test_run_id == run.id).values(created_at=ts))
                    await db.execute(update(TestCaseHistory).where(
                        TestCaseHistory.test_run_id == run.id).values(created_at=ts))
                    await db.commit()
                # finalize_run owns its own sessions (suites, aggregates, etc.)
                await finalize_run(
                    run_id=run_id, project_id=pid, build_number=spec.build_number,
                )
                seeded += 1
            print(f"   ✅  project {pid[:8]}…: {seeded} runs of history")
        except Exception as exc:  # pragma: no cover — best-effort
            print(f"   ⚠️  execution-history seed partial for {pid[:8]}… "
                  f"({seeded} runs) — {type(exc).__name__}: {exc}")


async def main(reset: bool = False) -> None:
    engine = create_async_engine(str(settings.DATABASE_URL), echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        if reset:
            await _wipe_seed_data(db)
        elif await _already_seeded(db):
            print("ℹ️  Seed data already present. Run with --reset to regenerate.")
            await engine.dispose()
            return

        users = await _seed_users(db)
        projects = await _seed_projects(db, users)

        admin_user = users.get(UserRole.ADMIN) or next(iter(users.values()))
        lead_user = users.get(UserRole.QA_LEAD, admin_user)

        print("🧪  Seeding test cases, plans, strategies, and releases…")
        for project in projects:
            cases = await _seed_test_cases(db, project, lead_user)
            await _seed_test_plan(db, project, cases, lead_user)
            await _seed_strategy(db, project, admin_user)
            await _seed_releases(db, project, admin_user)
            print(
                f"   ✅  {project.name}: "
                f"{len(cases)} test cases · 1 plan · 1 strategy · "
                f"{len(RELEASES_TEMPLATE)} releases"
            )

        await db.commit()
        project_ids = [str(p.id) for p in projects]

    await engine.dispose()

    # Execution history (runs/failures/flaky/trends) — runs after the core seed
    # committed, with its own sessions; isolated + best-effort.
    await _seed_execution_history(project_ids)

    print()
    print("=" * 62)
    print("✅  Seed complete — demo credentials")
    print("=" * 62)
    print(f"  {'Role':<16} {'Email':<28} Password")
    print(f"  {'-'*16} {'-'*28} {'-'*14}")
    for u in USERS:
        print(f"  {str(u['role']):<16} {u['email']:<28} {u['password']}")
    print()
    print("  Dashboard → http://localhost:3000")
    print("  API Docs  → http://localhost:8000/docs")
    print("=" * 62)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed TestLookup dev database")
    parser.add_argument("--reset", action="store_true", help="Wipe existing seed data and re-seed")
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
