#!/usr/bin/env python3
"""
TestLookup — Development Seed Data Script
=============================================
Creates a complete, demo-ready dataset:
  - 5 users, 3 projects, managed test cases, plans, strategies, releases
  - 30 days of historical test runs per project (trends data)
  - Test case results with realistic pass/fail/skip distribution
  - Test case history (flakiness tracking)
  - AI analysis results for failed tests
  - Agent pipeline runs with stage results
  - Failure clusters and deep investigation findings
  - Release gate decisions
  - Daily coverage snapshots (30 days)
  - Completed live sessions

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
import hashlib
import os
import random
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import get_password_hash
from app.models.postgres import (
    AgentPipelineRun,
    AgentStageResult,
    AIAnalysis,
    CoverageSnapshot,
    DeepFinding,
    Defect,
    FailureCategory,
    FailureCluster,
    LaunchStatus,
    LiveSession,
    ManagedTestCase,
    Project,
    ProjectMember,
    Release,
    ReleaseDecision,
    ReleasePhase,
    Severity,
    TestCase,
    TestCaseHistory,
    TestPlan,
    TestPlanItem,
    TestRun,
    TestStatus,
    TestStrategy,
    User,
    UserRole,
)

# ─────────────────────────────────────────────────────────────────────────────
# Seed marker + catalogue
# ─────────────────────────────────────────────────────────────────────────────

SEED_MARKER = "seed_dev_data_v1"
RNG = random.Random(42)   # deterministic — same data on every reset

# Passwords are randomly generated at seed time — they are intentionally not
# stored or displayed. Use the Quick Login buttons at http://localhost:3000
# (backed by /api/v1/auth/dev-login) to access the dashboard without a password.
USERS = [
    {"email": "admin@testlookup.dev",    "username": "admin",       "full_name": "QA Admin",         "password": secrets.token_urlsafe(20), "role": UserRole.ADMIN},
    {"email": "lead@testlookup.dev",     "username": "qa_lead",     "full_name": "QA Lead",          "password": secrets.token_urlsafe(20), "role": UserRole.QA_LEAD},
    {"email": "engineer@testlookup.dev", "username": "qa_engineer", "full_name": "QA Engineer",      "password": secrets.token_urlsafe(20), "role": UserRole.QA_ENGINEER},
    {"email": "tester@testlookup.dev",   "username": "tester",      "full_name": "Tester",           "password": secrets.token_urlsafe(20), "role": UserRole.TESTER},
    {"email": "viewer@testlookup.dev",   "username": "viewer",      "full_name": "Read-Only Viewer", "password": secrets.token_urlsafe(20), "role": UserRole.VIEWER},
]

PROJECTS = [
    {"name": "E-Commerce Platform",  "slug": "ecommerce-platform", "description": f"Online shopping and payment flows · {SEED_MARKER}", "jenkins_job_pattern": "ecommerce-*"},
    {"name": "Mobile Banking App",   "slug": "mobile-banking",     "description": f"iOS/Android banking client · {SEED_MARKER}",        "jenkins_job_pattern": "mobile-*"},
    {"name": "API Gateway Service",  "slug": "api-gateway",        "description": f"Central API routing and auth service · {SEED_MARKER}", "jenkins_job_pattern": "api-gateway-*"},
]

# (title, test_type, priority, severity, feature_area, status, suite_name, is_automated)
TEST_CASES_TEMPLATE = [
    ("Verify successful user login",                    "functional",   "critical", "blocker",  "Authentication", "active",           "Auth Suite",        False),
    ("Verify login with invalid credentials",           "functional",   "critical", "blocker",  "Authentication", "active",           "Auth Suite",        False),
    ("Verify password reset via email link",            "functional",   "high",     "critical", "Authentication", "active",           "Auth Suite",        False),
    ("Verify session expiry after inactivity timeout",  "functional",   "high",     "major",    "Authentication", "active",           "Auth Suite",        False),
    ("Verify checkout with valid payment card",         "functional",   "critical", "blocker",  "Payments",       "active",           "Payment Suite",     True),
    ("Verify checkout fails gracefully on declined card","functional",  "critical", "blocker",  "Payments",       "active",           "Payment Suite",     True),
    ("Verify partial refund processing",                "functional",   "high",     "critical", "Payments",       "review_requested", "Payment Suite",     False),
    ("Verify API response time is under 200ms",         "performance",  "high",     "major",    "Performance",    "active",           "Performance Suite", True),
    ("Verify API handles 1000 concurrent requests",     "performance",  "medium",   "major",    "Performance",    "draft",            "Performance Suite", True),
    ("Verify SQL injection payloads are blocked",       "security",     "critical", "blocker",  "Security",       "active",           "Security Suite",    True),
    ("Verify XSS prevention on all input fields",       "security",     "critical", "blocker",  "Security",       "active",           "Security Suite",    True),
    ("Verify RBAC enforcement on admin endpoints",      "security",     "high",     "critical", "Security",       "active",           "Security Suite",    False),
    ("Verify search returns correct paginated results", "functional",   "medium",   "minor",    "Search",         "active",           "Search Suite",      False),
    ("Verify pagination on large data sets",            "functional",   "low",      "trivial",  "Search",         "draft",            "Search Suite",      False),
    ("Verify smoke test passes on fresh deploy",        "smoke",        "critical", "blocker",  "Smoke",          "active",           "Smoke Suite",       True),
]

STEPS_TEMPLATE = [
    {"step_number": 1, "action": "Navigate to the feature entry point", "expected_result": "Page loads successfully within 2 seconds"},
    {"step_number": 2, "action": "Perform the primary action with valid test data", "expected_result": "System responds with expected success state"},
    {"step_number": 3, "action": "Verify the outcome matches acceptance criteria", "expected_result": "All assertions pass; UI/API reflects correct state"},
]

# Suite definitions used for generating realistic test run results
SUITES = [
    {"name": "AuthSuite",        "class_prefix": "com.qa.auth",        "feature": "Authentication", "severity": "BLOCKER",  "tests": 8},
    {"name": "PaymentSuite",     "class_prefix": "com.qa.payment",     "feature": "Payments",       "severity": "CRITICAL", "tests": 10},
    {"name": "PerformanceSuite", "class_prefix": "com.qa.performance", "feature": "Performance",    "severity": "MAJOR",    "tests": 6},
    {"name": "SecuritySuite",    "class_prefix": "com.qa.security",    "feature": "Security",       "severity": "BLOCKER",  "tests": 8},
    {"name": "SearchSuite",      "class_prefix": "com.qa.search",      "feature": "Search",         "severity": "MINOR",    "tests": 6},
    {"name": "SmokeSuite",       "class_prefix": "com.qa.smoke",       "feature": "Smoke",          "severity": "BLOCKER",  "tests": 4},
]

FAILURE_MESSAGES = {
    "Authentication": [
        ("AssertionError: Expected HTTP 200 but got 401", "com.qa.auth.LoginTest.testLoginSuccess(LoginTest.java:45)\n\tat com.qa.runner.TestRunner.run(TestRunner.java:120)"),
        ("TimeoutException: Session validation timed out after 30s", "com.qa.auth.SessionTest.testSessionExpiry(SessionTest.java:88)\n\tat com.qa.runner.TestRunner.run(TestRunner.java:120)"),
        ("AssertionError: JWT token expiry mismatch — expected 3600s got 1800s", "com.qa.auth.TokenTest.testTokenExpiry(TokenTest.java:67)"),
    ],
    "Payments": [
        ("AssertionError: Expected status 200 but was 500 — payment gateway timeout", "com.qa.payment.PaymentTest.testCheckoutSuccess(PaymentTest.java:87)\n\tat com.qa.runner.TestRunner.run(TestRunner.java:120)"),
        ("ConnectionRefusedException: Payment gateway unreachable at stripe.internal:443", "com.qa.payment.PaymentTest.testChargeCard(PaymentTest.java:112)\n\tat com.qa.runner.TestRunner.run(TestRunner.java:120)"),
        ("AssertionError: Refund amount mismatch — expected 50.00 got 49.99", "com.qa.payment.RefundTest.testPartialRefund(RefundTest.java:55)"),
    ],
    "Performance": [
        ("AssertionError: API p99 latency 523ms exceeded SLA threshold of 200ms", "com.qa.performance.LatencyTest.testApiLatency(LatencyTest.java:34)"),
        ("TimeoutException: Load test failed — throughput dropped below 800 rps after 60s", "com.qa.performance.LoadTest.testConcurrentLoad(LoadTest.java:78)"),
    ],
    "Security": [
        ("AssertionError: SQL injection payload was not sanitized — response contained DB error", "com.qa.security.InjectionTest.testSQLInjection(InjectionTest.java:44)"),
        ("AssertionError: XSS payload reflected in response body without escaping", "com.qa.security.XSSTest.testReflectedXSS(XSSTest.java:67)"),
    ],
    "Search": [
        ("AssertionError: Search returned 24 results — expected 25", "com.qa.search.SearchTest.testPaginatedSearch(SearchTest.java:89)"),
        ("AssertionError: Sort order incorrect — descending timestamp violated", "com.qa.search.SearchTest.testSortOrder(SearchTest.java:112)"),
    ],
    "Smoke": [
        ("AssertionError: Health check returned HTTP 503 — backend dependency unavailable", "com.qa.smoke.SmokeTest.testHealthCheck(SmokeTest.java:28)"),
    ],
}

FAILURE_CATEGORIES_BY_FEATURE = {
    "Authentication": FailureCategory.PRODUCT_BUG,
    "Payments":       FailureCategory.INFRASTRUCTURE,
    "Performance":    FailureCategory.INFRASTRUCTURE,
    "Security":       FailureCategory.PRODUCT_BUG,
    "Search":         FailureCategory.TEST_DATA,
    "Smoke":          FailureCategory.INFRASTRUCTURE,
}

AI_ANALYSIS_TEMPLATES = {
    FailureCategory.PRODUCT_BUG: {
        "root_cause_summary": "The test failure indicates a regression in the application logic. The API returned an unexpected status code, suggesting that a recent code change broke the expected behaviour. Review recent commits to the affected service.",
        "recommended_actions": [
            "Review commits merged in the last 48 hours for the affected module",
            "Run the failing test in isolation with DEBUG logging enabled",
            "Open a Jira bug ticket with P1 priority and assign to the owning team",
            "Add a regression test to prevent recurrence",
        ],
        "confidence_score": 87,
        "backend_error_found": True,
        "pod_issue_found": False,
        "is_flaky": False,
        "requires_human_review": False,
    },
    FailureCategory.INFRASTRUCTURE: {
        "root_cause_summary": "The failure is caused by an infrastructure-level issue — a dependency service (payment gateway, database, or external API) was unavailable or responded with an error during the test window. This is not a code regression.",
        "recommended_actions": [
            "Check OpenShift pod status for the affected dependency service",
            "Review infrastructure alerts and Prometheus dashboards for the test window",
            "Retry the test run after confirming dependency health",
            "Add circuit-breaker retries in the test harness for transient infra failures",
        ],
        "confidence_score": 82,
        "backend_error_found": False,
        "pod_issue_found": True,
        "is_flaky": False,
        "requires_human_review": False,
    },
    FailureCategory.TEST_DATA: {
        "root_cause_summary": "The test failed due to a test data inconsistency. The expected value in the assertion does not match the current database state, likely because shared test data was modified by a parallel test run.",
        "recommended_actions": [
            "Isolate test data per test run using unique identifiers or database transactions",
            "Review test data setup / teardown in the test fixture",
            "Check for parallel test execution conflicts in the CI pipeline",
        ],
        "confidence_score": 74,
        "backend_error_found": False,
        "pod_issue_found": False,
        "is_flaky": True,
        "requires_human_review": True,
    },
    FailureCategory.FLAKY: {
        "root_cause_summary": "Historical analysis shows this test has a 34% flakiness rate over the past 14 days. The failure is non-deterministic and likely caused by timing dependencies, shared state, or external service latency. Quarantine recommended.",
        "recommended_actions": [
            "Add the test to the flaky quarantine list to unblock CI",
            "Investigate async timing issues — add explicit waits instead of sleep()",
            "Review shared state or singleton usage in the test class",
            "Schedule a dedicated spike to rewrite the test with deterministic assertions",
        ],
        "confidence_score": 91,
        "backend_error_found": False,
        "pod_issue_found": False,
        "is_flaky": True,
        "requires_human_review": False,
    },
}

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
        {"type": "functional",   "priority": "critical", "tools": ["pytest", "playwright"],     "coverage_target_pct": 90, "rationale": "Core business flows must be verified before every release"},
        {"type": "performance",  "priority": "high",     "tools": ["locust"],                    "coverage_target_pct": 70, "rationale": "SLA requires p99 < 500ms at 500 concurrent users"},
        {"type": "security",     "priority": "high",     "tools": ["owasp-zap", "bandit"],       "coverage_target_pct": 80, "rationale": "SOC2 compliance requires automated security scanning"},
        {"type": "smoke",        "priority": "critical", "tools": ["pytest"],                    "coverage_target_pct": 100, "rationale": "Gate every production deployment"},
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
        {"name": "dev",     "type": "development", "purpose": "Developer testing and integration verification"},
        {"name": "staging", "type": "staging",     "purpose": "QA regression and performance validation"},
        {"name": "prod",    "type": "production",  "purpose": "Post-deploy smoke tests only"},
    ],
    "automation_approach": "Playwright for UI flows, pytest for API, Locust for load. CI runs full regression on every PR; smoke on every deploy.",
    "defect_management": "All defects logged in Jira with priority/severity. Blockers block the release. Critical defects require hotfix within 24 hours.",
}

RELEASES_TEMPLATE = [
    {
        "name": "v1.0.0 — Initial Release",
        "version": "1.0.0",
        "status": "in_progress",
        "planned_date_offset": 7,
        "phases": [
            {"name": "Feature Freeze",   "phase_type": "code_freeze", "status": "completed",   "order_index": 1},
            {"name": "QA Regression",    "phase_type": "qa_testing",  "status": "in_progress", "order_index": 2},
            {"name": "UAT Sign-off",     "phase_type": "uat",         "status": "pending",      "order_index": 3},
            {"name": "Production Deploy","phase_type": "production",  "status": "pending",      "order_index": 4},
        ],
    },
    {
        "name": "v1.1.0 — Performance Improvements",
        "version": "1.1.0",
        "status": "planning",
        "planned_date_offset": 35,
        "phases": [
            {"name": "Development Complete", "phase_type": "development", "status": "pending", "order_index": 1},
            {"name": "QA Regression",        "phase_type": "qa_testing",  "status": "pending", "order_index": 2},
            {"name": "Release",              "phase_type": "production",  "status": "pending", "order_index": 3},
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(n: float) -> datetime:
    return _now() - timedelta(days=n)


def _fingerprint(test_name: str, class_name: str) -> str:
    return hashlib.sha256(f"{test_name}::{class_name}".encode()).hexdigest()[:64]


def _pass_rate_for_day(day_index: int, total_days: int) -> float:
    """
    Simulate a realistic trend: stable high pass rate historically,
    degrading slightly in the last week to show interesting dashboard data.
    day_index=0 is the oldest run; day_index=total_days-1 is the most recent.
    """
    if day_index >= total_days - 3:
        return RNG.uniform(0.62, 0.78)   # last 3 runs: notable failures
    if day_index >= total_days - 7:
        return RNG.uniform(0.80, 0.90)   # last week: some degradation
    return RNG.uniform(0.91, 0.98)       # historical: stable


async def _already_seeded(db: AsyncSession) -> bool:
    result = await db.execute(
        select(Project).where(Project.description.contains(SEED_MARKER)).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _wipe_seed_data(db: AsyncSession) -> None:
    print("Wiping existing seed data...")

    result = await db.execute(
        select(Project.id).where(Project.description.contains(SEED_MARKER))
    )
    project_ids = [row[0] for row in result.all()]

    if project_ids:
        # Collect run IDs for cascade deletes
        run_ids_result = await db.execute(
            select(TestRun.id).where(TestRun.project_id.in_(project_ids))
        )
        run_ids = [r[0] for r in run_ids_result.all()]

        if run_ids:
            await db.execute(delete(ReleaseDecision).where(ReleaseDecision.test_run_id.in_(run_ids)))
            await db.execute(delete(FailureCluster).where(FailureCluster.test_run_id.in_(run_ids)))
            await db.execute(delete(DeepFinding).where(DeepFinding.test_run_id.in_(run_ids)))
            await db.execute(delete(AgentPipelineRun).where(AgentPipelineRun.test_run_id.in_(run_ids)))

            tc_ids_result = await db.execute(
                select(TestCase.id).where(TestCase.test_run_id.in_(run_ids))
            )
            tc_ids = [r[0] for r in tc_ids_result.all()]
            if tc_ids:
                await db.execute(delete(AIAnalysis).where(AIAnalysis.test_case_id.in_(tc_ids)))
                await db.execute(delete(Defect).where(Defect.test_case_id.in_(tc_ids)))
                await db.execute(delete(TestCaseHistory).where(TestCaseHistory.test_run_id.in_(run_ids)))
                await db.execute(delete(TestCase).where(TestCase.test_run_id.in_(run_ids)))

            await db.execute(delete(TestRun).where(TestRun.project_id.in_(project_ids)))

        await db.execute(delete(CoverageSnapshot).where(CoverageSnapshot.project_id.in_(project_ids)))
        await db.execute(delete(LiveSession).where(LiveSession.project_id.in_(project_ids)))

        for pid in project_ids:
            release_ids_result = await db.execute(
                select(Release.id).where(Release.project_id == pid)
            )
            for rid in [r[0] for r in release_ids_result.all()]:
                await db.execute(delete(ReleasePhase).where(ReleasePhase.release_id == rid))
            await db.execute(delete(Release).where(Release.project_id == pid))

            plan_ids_result = await db.execute(
                select(TestPlan.id).where(TestPlan.project_id == pid)
            )
            for plid in [r[0] for r in plan_ids_result.all()]:
                await db.execute(delete(TestPlanItem).where(TestPlanItem.plan_id == plid))
            await db.execute(delete(TestPlan).where(TestPlan.project_id == pid))

            await db.execute(delete(TestStrategy).where(TestStrategy.project_id == pid))
            await db.execute(delete(ManagedTestCase).where(ManagedTestCase.project_id == pid))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id == pid))

        await db.execute(delete(Project).where(Project.id.in_(project_ids)))

    seeded_emails = [u["email"] for u in USERS]
    await db.execute(delete(User).where(User.email.in_(seeded_emails)))

    await db.commit()
    print("  Wipe complete.")


# ─────────────────────────────────────────────────────────────────────────────
# Seed functions
# ─────────────────────────────────────────────────────────────────────────────

async def _seed_users(db: AsyncSession) -> dict[UserRole, User]:
    print("Creating demo users...")
    users: dict[UserRole, User] = {}
    for u in USERS:
        existing = (await db.execute(select(User).where(User.email == u["email"]))).scalar_one_or_none()
        if existing:
            users[u["role"]] = existing
            print(f"  {u['email']} already exists — skipped")
            continue
        user = User(
            id=uuid.uuid4(),
            email=u["email"],
            username=u["username"],
            full_name=u["full_name"],
            hashed_password=get_password_hash(u["password"]),
            role=u["role"],
            is_active=True,
            must_change_password=False,
        )
        db.add(user)
        users[u["role"]] = user
        print(f"  {u['email']}  role={u['role']}")
    await db.flush()
    return users


async def _seed_projects(db: AsyncSession, users: dict[UserRole, User]) -> list[Project]:
    print("Creating demo projects...")
    projects: list[Project] = []
    for p in PROJECTS:
        existing = (await db.execute(select(Project).where(Project.slug == p["slug"]))).scalar_one_or_none()
        if existing:
            projects.append(existing)
            print(f"  {p['name']} already exists — skipped")
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
        for role_key, user in users.items():
            db.add(ProjectMember(id=uuid.uuid4(), project_id=project.id, user_id=user.id, role=str(role_key)))
        print(f"  {p['name']} ({p['slug']})")
    await db.flush()
    return projects


async def _seed_managed_test_cases(db: AsyncSession, project: Project, author: User) -> list[ManagedTestCase]:
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


async def _seed_test_plan(db: AsyncSession, project: Project, cases: list[ManagedTestCase], created_by: User) -> None:
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
        db.add(TestPlanItem(id=uuid.uuid4(), plan_id=plan.id, test_case_id=tc.id, order_index=idx + 1, execution_status="not_run"))
    await db.flush()


async def _seed_strategy(db: AsyncSession, project: Project, created_by: User) -> None:
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


async def _seed_releases(db: AsyncSession, project: Project, created_by: User) -> None:
    for rel_def in RELEASES_TEMPLATE:
        release = Release(
            id=uuid.uuid4(),
            project_id=project.id,
            name=rel_def["name"],
            version=rel_def["version"],
            status=rel_def["status"],
            planned_date=_now() + timedelta(days=rel_def["planned_date_offset"]),
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


async def _seed_test_runs(
    db: AsyncSession,
    project: Project,
    admin_user: User,
    num_days: int = 30,
) -> list[TestRun]:
    """
    Create `num_days` historical test runs (one per day).
    Each run contains test cases across all suites.
    Returns the list of runs (oldest → newest).
    """
    runs: list[TestRun] = []
    project_slug = project.slug.replace("-", "_")
    branches = ["main", "main", "main", "develop", "feature/payments-v2"]

    for day_idx in range(num_days):
        age_days = num_days - 1 - day_idx   # 0 = today, num_days-1 = oldest
        run_start = _days_ago(age_days + RNG.uniform(0, 0.3))
        pass_rate = _pass_rate_for_day(day_idx, num_days)
        build_num = f"build-{2000 + day_idx}"
        branch = RNG.choice(branches)

        total = sum(s["tests"] for s in SUITES)
        passed = 0
        failed = 0
        skipped = 0
        duration_ms = 0

        test_cases: list[TestCase] = []

        for suite in SUITES:
            for tc_idx in range(suite["tests"]):
                tc_name = f"test{suite['feature'].replace(' ', '')}Case{tc_idx + 1:02d}"
                class_name = f"{suite['class_prefix']}.{suite['name']}Test"
                full_name = f"{class_name}.{tc_name}"
                fp = _fingerprint(tc_name, class_name)

                # Determine status based on pass rate (flaky tests have 20% chance of being the failure)
                roll = RNG.random()
                if roll < pass_rate:
                    status = TestStatus.PASSED
                    passed += 1
                    error_msg = None
                    trace = None
                    fail_cat = None
                    duration = RNG.randint(200, 3500)
                elif roll < pass_rate + (1 - pass_rate) * 0.15:
                    status = TestStatus.SKIPPED
                    skipped += 1
                    error_msg = None
                    trace = None
                    fail_cat = None
                    duration = RNG.randint(10, 50)
                else:
                    status = TestStatus.FAILED
                    failed += 1
                    msgs = FAILURE_MESSAGES.get(suite["feature"], [("AssertionError: unexpected failure", "at com.qa.UnknownTest.run(Unknown.java:1)")])
                    err_pair = RNG.choice(msgs)
                    error_msg, trace = err_pair
                    fail_cat = FAILURE_CATEGORIES_BY_FEATURE.get(suite["feature"], FailureCategory.UNKNOWN)
                    duration = RNG.randint(3000, 15000)

                duration_ms += duration
                tc = TestCase(
                    id=uuid.uuid4(),
                    test_run_id=None,   # set after run is created
                    test_fingerprint=fp,
                    test_name=tc_name,
                    full_name=full_name,
                    suite_name=suite["name"],
                    class_name=class_name,
                    package_name=suite["class_prefix"],
                    status=status,
                    duration_ms=duration,
                    severity=Severity(suite["severity"]),
                    feature=suite["feature"],
                    failure_category=fail_cat,
                    error_message=error_msg,
                    tags=[suite["feature"].lower().replace(" ", "-")],
                    has_attachments=status == TestStatus.FAILED,
                )
                test_cases.append(tc)

        actual_pass_rate = round(passed / total * 100, 1) if total > 0 else 0.0
        run_status = LaunchStatus.PASSED if failed == 0 else LaunchStatus.FAILED
        run_end = run_start + timedelta(milliseconds=duration_ms)

        run = TestRun(
            id=uuid.uuid4(),
            project_id=project.id,
            build_number=build_num,
            jenkins_job=f"{project_slug}-regression-pipeline",
            trigger_source=RNG.choice(["push", "push", "schedule", "manual"]),
            branch=branch,
            commit_hash=uuid.uuid4().hex[:12],
            status=run_status,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
            broken_tests=0,
            pass_rate=actual_pass_rate,
            duration_ms=duration_ms,
            ocp_pod_name=f"test-runner-{uuid.uuid4().hex[:8]}",
            ocp_namespace="qa-testing",
            minio_prefix=f"{project.slug}/runs/{build_num}",
            start_time=run_start,
            end_time=run_end,
            created_at=run_start,
        )
        db.add(run)
        await db.flush()

        # Attach test cases to run
        for tc in test_cases:
            tc.test_run_id = run.id
            db.add(tc)
        await db.flush()

        runs.append(run)

    return runs


async def _seed_test_case_history(
    db: AsyncSession,
    runs: list[TestRun],
) -> None:
    """Create TestCaseHistory rows for all test cases (enables flakiness queries)."""
    for run in runs:
        tc_result = await db.execute(
            select(TestCase).where(TestCase.test_run_id == run.id)
        )
        tcs = tc_result.scalars().all()
        for tc in tcs:
            db.add(TestCaseHistory(
                id=uuid.uuid4(),
                test_case_id=tc.id,
                test_run_id=run.id,
                test_fingerprint=tc.test_fingerprint,
                status=tc.status,
                duration_ms=tc.duration_ms,
                failure_category=tc.failure_category,
                created_at=run.start_time or run.created_at,
            ))
    await db.flush()


async def _seed_ai_analysis(
    db: AsyncSession,
    runs: list[TestRun],
    llm_provider: str = "ollama",
    llm_model: str = "qwen2.5:7b",
) -> None:
    """Create AIAnalysis rows for all failed test cases in the provided runs."""
    for run in runs:
        tc_result = await db.execute(
            select(TestCase).where(
                TestCase.test_run_id == run.id,
                TestCase.status == TestStatus.FAILED,
            )
        )
        failed_tcs = tc_result.scalars().all()
        for tc in failed_tcs:
            cat = tc.failure_category or FailureCategory.UNKNOWN
            template = AI_ANALYSIS_TEMPLATES.get(cat, AI_ANALYSIS_TEMPLATES[FailureCategory.PRODUCT_BUG])
            db.add(AIAnalysis(
                id=uuid.uuid4(),
                test_case_id=tc.id,
                root_cause_summary=template["root_cause_summary"],
                failure_category=cat,
                backend_error_found=template["backend_error_found"],
                pod_issue_found=template["pod_issue_found"],
                is_flaky=template["is_flaky"],
                confidence_score=template["confidence_score"] + RNG.randint(-5, 5),
                recommended_actions=template["recommended_actions"],
                evidence_references=[
                    {"source": "splunk", "excerpt": f"ERROR {tc.error_message[:60] if tc.error_message else 'unknown'}"},
                    {"source": "ocp_events", "excerpt": "Pod restart detected 2m before test failure"},
                ],
                llm_provider=llm_provider,
                llm_model=llm_model,
                requires_human_review=template["requires_human_review"],
            ))
    await db.flush()


async def _seed_agent_pipeline_runs(
    db: AsyncSession,
    runs: list[TestRun],
) -> list[AgentPipelineRun]:
    """Create AgentPipelineRun + AgentStageResult for the last N runs."""
    pipeline_runs: list[AgentPipelineRun] = []
    stages = ["ingestion", "anomaly", "analysis", "summary", "triage"]

    for run in runs:
        started = run.end_time or run.created_at
        pr = AgentPipelineRun(
            id=uuid.uuid4(),
            test_run_id=run.id,
            workflow_type="offline",
            status="completed",
            started_at=started,
            completed_at=started + timedelta(seconds=RNG.randint(45, 180)),
            created_at=started,
        )
        db.add(pr)
        await db.flush()

        stage_start = started
        for stage in stages:
            stage_duration = RNG.randint(8, 35)
            stage_end = stage_start + timedelta(seconds=stage_duration)
            result_data: dict = {}
            if stage == "ingestion":
                result_data = {"tests_ingested": run.total_tests, "suites_found": len(SUITES), "source": "minio"}
            elif stage == "anomaly":
                result_data = {"anomalies_detected": run.failed_tests, "baseline_pass_rate": 92.3, "current_pass_rate": run.pass_rate}
            elif stage == "analysis":
                result_data = {"cases_analysed": run.failed_tests, "llm_calls": run.failed_tests, "avg_confidence": 84}
            elif stage == "summary":
                result_data = {"summary_generated": True, "word_count": RNG.randint(180, 320)}
            elif stage == "triage":
                result_data = {"jira_tickets_created": max(0, run.failed_tests - 2), "auto_resolved": 1}

            db.add(AgentStageResult(
                id=uuid.uuid4(),
                pipeline_run_id=pr.id,
                stage_name=stage,
                status="completed",
                started_at=stage_start,
                completed_at=stage_end,
                result_data=result_data,
            ))
            stage_start = stage_end

        pipeline_runs.append(pr)

    await db.flush()
    return pipeline_runs


async def _seed_defects(
    db: AsyncSession,
    runs: list[TestRun],
    project: Project,
) -> None:
    """Create Defect records for failed test cases in the most recent runs."""
    jira_counter = 1000
    for run in runs:
        tc_result = await db.execute(
            select(TestCase).where(
                TestCase.test_run_id == run.id,
                TestCase.status == TestStatus.FAILED,
            )
        )
        failed_tcs = tc_result.scalars().all()
        for tc in failed_tcs:
            jira_key = f"QA-{jira_counter}"
            jira_counter += 1
            db.add(Defect(
                id=uuid.uuid4(),
                test_case_id=tc.id,
                project_id=project.id,
                jira_ticket_id=jira_key,
                jira_ticket_url=f"https://jira.example.com/browse/{jira_key}",
                jira_status=RNG.choice(["Open", "In Progress", "In Review"]),
                ai_confidence_score=RNG.randint(72, 95),
                failure_category=tc.failure_category or FailureCategory.UNKNOWN,
                resolution_status="OPEN",
            ))
    await db.flush()


async def _seed_failure_clusters(
    db: AsyncSession,
    runs: list[TestRun],
    pipeline_runs: list[AgentPipelineRun],
) -> None:
    """Create FailureCluster + DeepFinding for the most recent runs."""
    run_to_pr = {pr.test_run_id: pr for pr in pipeline_runs}

    cluster_labels = [
        ("Payment Gateway Timeout",     "java.lang.ConnectionRefusedException: stripe.internal:443 refused"),
        ("Auth Token Expiry Bug",        "AssertionError: JWT token expiry mismatch — expected 3600s got 1800s"),
        ("Performance SLA Breach",       "AssertionError: API p99 latency 523ms exceeded SLA threshold of 200ms"),
        ("Security Policy Misconfiguration", "AssertionError: SQL injection payload was not sanitized"),
        ("Search Index Staleness",       "AssertionError: Search returned stale results — index lag detected"),
    ]

    for run in runs:
        tc_result = await db.execute(
            select(TestCase).where(
                TestCase.test_run_id == run.id,
                TestCase.status == TestStatus.FAILED,
            )
        )
        failed_tcs = tc_result.scalars().all()
        if not failed_tcs:
            continue

        # Group failed tests into 2-3 clusters
        num_clusters = min(len(failed_tcs), RNG.randint(2, 3))
        tc_chunks: list[list[TestCase]] = [[] for _ in range(num_clusters)]
        for i, tc in enumerate(failed_tcs):
            tc_chunks[i % num_clusters].append(tc)

        pr = run_to_pr.get(run.id)
        for cl_idx, chunk in enumerate(tc_chunks):
            if not chunk:
                continue
            label, rep_err = cluster_labels[cl_idx % len(cluster_labels)]
            cluster_id = f"cl_{cl_idx + 1:03d}"
            fc = FailureCluster(
                id=uuid.uuid4(),
                test_run_id=run.id,
                pipeline_run_id=pr.id if pr else None,
                cluster_id=cluster_id,
                label=label,
                representative_error=rep_err,
                member_test_ids=[str(tc.id) for tc in chunk],
                size=len(chunk),
                cohesion_score=round(RNG.uniform(0.72, 0.95), 2),
                created_at=run.end_time or run.created_at,
            )
            db.add(fc)
            await db.flush()

            db.add(DeepFinding(
                id=uuid.uuid4(),
                test_run_id=run.id,
                cluster_id=cluster_id,
                root_cause=f"Root cause identified: {label}. The failure pattern indicates a systemic issue affecting {len(chunk)} test(s). A recent deployment or configuration change is the most likely trigger.",
                failure_category=FAILURE_CATEGORIES_BY_FEATURE.get(
                    chunk[0].feature or "Smoke", FailureCategory.PRODUCT_BUG
                ),
                confidence_score=RNG.randint(76, 94),
                causal_chain=[
                    {"step": 1, "service": "api-gateway",    "finding": "Increased 5xx error rate 15 minutes before test run"},
                    {"step": 2, "service": "payments-svc",   "finding": "Connection pool exhaustion detected in pod logs"},
                    {"step": 3, "service": "test-runner",    "finding": "Tests failed due to upstream service unavailability"},
                ],
                evidence=[
                    {"source": "splunk",    "excerpt": f"ERROR {rep_err[:80]}"},
                    {"source": "prometheus","excerpt": "payments-svc error_rate=0.34 (baseline: 0.02)"},
                ],
                affected_services=["payments-svc", "api-gateway"],
                # AI-F4: demo rows are tagged so the findings endpoint (and the
                # no-seed-only-data guard) can distinguish them from real
                # pipeline output (origin="pipeline").
                log_evidence={"origin": "seed"},
                recommended_actions=[
                    "Increase connection pool size in payments-svc Helm values",
                    "Add retry logic with exponential backoff to test harness",
                    "Alert on error_rate > 0.1 before test suite execution",
                ],
                created_at=run.end_time or run.created_at,
            ))

    await db.flush()


async def _seed_release_decisions(
    db: AsyncSession,
    runs: list[TestRun],
    qa_lead: User,
) -> None:
    """Create ReleaseDecision records for all runs (release gate data)."""
    for run in runs:
        if run.pass_rate is None:
            continue
        pr = run.pass_rate

        if pr >= 95:
            recommendation = "GO"
            risk_score = RNG.randint(5, 20)
            blocking = []
            conditions = []
            reasoning = f"Pass rate {pr:.1f}% exceeds the 95% quality gate threshold. No critical failures detected. All blocker-severity tests passed."
        elif pr >= 80:
            recommendation = "CONDITIONAL_GO"
            risk_score = RNG.randint(35, 55)
            blocking = []
            conditions = [
                "Fix or waive the 2 failing payment integration tests before deploy",
                "Confirm infrastructure issue is isolated to staging environment",
            ]
            reasoning = f"Pass rate {pr:.1f}% is below the 95% threshold but above the 80% minimum. Non-blocker failures only. Conditional approval granted."
        else:
            recommendation = "NO_GO"
            risk_score = RNG.randint(65, 88)
            blocking = [
                f"Pass rate {pr:.1f}% is below the minimum 80% quality gate",
                "Critical payment flow failures detected — blocker severity",
                "Security test failures present — cannot ship with known vulnerabilities",
            ]
            conditions = []
            reasoning = f"Pass rate {pr:.1f}% is critically low. Blocker-severity failures in payment and security suites prevent release. Immediate triage required."

        db.add(ReleaseDecision(
            id=uuid.uuid4(),
            test_run_id=run.id,
            recommendation=recommendation,
            risk_score=risk_score,
            blocking_issues=blocking,
            conditions_for_go=conditions,
            reasoning=reasoning,
        ))

    await db.flush()


async def _seed_coverage_snapshots(
    db: AsyncSession,
    project: Project,
    managed_cases: list[ManagedTestCase],
    num_days: int = 30,
) -> None:
    """Create daily coverage snapshots for the last `num_days` days."""
    total = len(managed_cases)
    automated = sum(1 for tc in managed_cases if tc.is_automated)

    for day_idx in range(num_days):
        snap_date = _days_ago(num_days - 1 - day_idx)
        # Simulate gradual automation growth over the period
        auto_count = max(1, automated - (num_days - 1 - day_idx) // 5)
        suite_names = list({tc.suite_name for tc in managed_cases if tc.suite_name})
        suite_coverage = {
            s: {"total": sum(1 for tc in managed_cases if tc.suite_name == s),
                "automated": max(1, sum(1 for tc in managed_cases if tc.suite_name == s and tc.is_automated))}
            for s in suite_names
        }
        db.add(CoverageSnapshot(
            id=uuid.uuid4(),
            project_id=project.id,
            snapshot_date=snap_date,
            total_suites=len(suite_names),
            total_tests=total,
            automated_count=auto_count,
            suite_coverage=suite_coverage,
        ))

    await db.flush()


async def _seed_live_sessions(
    db: AsyncSession,
    project: Project,
) -> None:
    """Create a completed demo live session for the project."""
    session_id = f"live-{project.slug}-demo"
    token_hash = hashlib.sha256(f"demo-token-{project.slug}".encode()).hexdigest()
    started = _days_ago(0.5)
    completed = started + timedelta(minutes=18)

    db.add(LiveSession(
        id=uuid.uuid4(),
        project_id=project.id,
        run_id=session_id,
        client_name=f"demo-runner-{project.slug}",
        machine_id=f"ci-agent-{uuid.uuid4().hex[:6]}",
        build_number="build-live-demo",
        framework="pytest",
        branch="main",
        commit_hash=uuid.uuid4().hex[:12],
        session_token_hash=token_hash,
        status="completed",
        release_name="v1.0.0",
        total_tests=sum(s["tests"] for s in SUITES),
        events_received=sum(s["tests"] for s in SUITES) * 3,
        started_at=started,
        last_event_at=completed - timedelta(seconds=5),
        completed_at=completed,
        created_at=started,
    ))
    await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main(reset: bool = False, wipe_only: bool = False) -> None:
    engine = create_async_engine(str(settings.DATABASE_URL), echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        if wipe_only:
            await _wipe_seed_data(db)
            await db.commit()
            print("Seed data wiped successfully.")
            await engine.dispose()
            return

        if reset:
            await _wipe_seed_data(db)
        elif await _already_seeded(db):
            print("Seed data already present. Run with --reset to regenerate.")
            await engine.dispose()
            return

        # ── Core entities ────────────────────────────────────────────────────
        users = await _seed_users(db)
        projects = await _seed_projects(db, users)

        admin_user = users.get(UserRole.ADMIN) or next(iter(users.values()))
        lead_user  = users.get(UserRole.QA_LEAD, admin_user)

        # ── Per-project data ─────────────────────────────────────────────────
        NUM_HISTORICAL_RUNS = 30
        RUNS_WITH_AI       = 30   # create pipeline runs for all historical runs
        RUNS_WITH_CLUSTERS = 10   # failure clusters for last 10 runs
        RUNS_WITH_DEFECTS  = 15   # defects for last 15 runs

        for project in projects:
            print(f"\nSeeding {project.name}...")

            managed_cases = await _seed_managed_test_cases(db, project, lead_user)
            await _seed_test_plan(db, project, managed_cases, lead_user)
            await _seed_strategy(db, project, admin_user)
            await _seed_releases(db, project, admin_user)

            print(f"  {len(managed_cases)} managed test cases · 1 plan · 1 strategy · {len(RELEASES_TEMPLATE)} releases")

            # Historical runs (trends / overview data)
            runs = await _seed_test_runs(db, project, admin_user, num_days=NUM_HISTORICAL_RUNS)
            print(f"  {len(runs)} test runs created")

            # Test case history (flakiness tracking)
            await _seed_test_case_history(db, runs)
            print(f"  Test case history populated")

            # AI analysis for all failed tests
            await _seed_ai_analysis(db, runs)

            # Agent pipeline runs (AI Pipelines page)
            pipeline_runs = await _seed_agent_pipeline_runs(db, runs[-RUNS_WITH_AI:])
            print(f"  {len(pipeline_runs)} agent pipeline runs created")

            # Defects for recent runs
            await _seed_defects(db, runs[-RUNS_WITH_DEFECTS:], project)

            # Failure clusters + deep findings for most recent runs
            await _seed_failure_clusters(db, runs[-RUNS_WITH_CLUSTERS:], pipeline_runs[-RUNS_WITH_CLUSTERS:])
            print(f"  Failure clusters and deep findings seeded")

            # Release gate decisions for all runs
            await _seed_release_decisions(db, runs, lead_user)

            # Coverage snapshots (Coverage page)
            await _seed_coverage_snapshots(db, project, managed_cases, num_days=NUM_HISTORICAL_RUNS)
            print(f"  {NUM_HISTORICAL_RUNS} coverage snapshots created")

            # Live session (Live tab)
            await _seed_live_sessions(db, project)
            print(f"  Live session created")

        await db.commit()

    await engine.dispose()

    print()
    print("=" * 62)
    print("Seed complete — demo accounts ready")
    print("=" * 62)
    print(f"  {'Role':<16} {'Email':<28}")
    print(f"  {'-'*16} {'-'*28}")
    for u in USERS:
        print(f"  {str(u['role']):<16} {u['email']:<28}")
    print()
    print("  Log in via the Quick Login buttons at http://localhost:3000")
    print("  (DEV_AUTO_LOGIN_ENABLED=true in .env allows role-based login")
    print("   without a password — no credentials required in dev mode)")
    print()
    print("  Dashboard -> http://localhost:3000")
    print("  API Docs  -> http://localhost:8000/docs")
    print("=" * 62)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed TestLookup dev database")
    parser.add_argument("--reset", action="store_true", help="Wipe existing seed data and re-seed")
    parser.add_argument("--wipe-only", action="store_true", help="Wipe seed data without re-seeding")
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset, wipe_only=args.wipe_only))
