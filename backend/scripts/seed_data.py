#!/usr/bin/env python3
"""
TestLookup — Comprehensive Seed Data Generator
Populates all dashboard features with realistic test data.

Usage (inside backend container):
    docker compose exec backend python /app/scripts/seed_data.py
    docker compose exec backend python /app/scripts/seed_data.py --reset

Options:
    --reset    Wipe existing seed data and regenerate from scratch

What this creates:
  • 3 projects  (Payment Service, Auth Service, Inventory Service)
  • 4 users     (admin + 3 team members across roles)
  • 12 runs × 3 projects  over 30 days  (trend charts, pass-rate history)
  • 15 test cases × 36 runs  with consistent fingerprints  (flakiness detection)
  • test_case_history records  for every test per run
  • 6 defects per project  with Jira keys
  • 5 AI-analysis records per project  with root-cause summaries
  • 1 live run via MinIO + webhook  (shows real agent pipeline)
"""

import asyncio
import hashlib
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import boto3
import httpx
from botocore.client import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Runtime config ──────────────────────────────────────────────────────────
BACKEND_URL      = "http://localhost:8000"
MINIO_ENDPOINT   = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "testlookup_minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
BUCKET           = os.getenv("MINIO_BUCKET_NAME", "test-telemetry")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")
DATABASE_URL     = os.getenv("DATABASE_URL", "")

RESET = "--reset" in sys.argv

# ── Seed users ──────────────────────────────────────────────────────────────
USERS = [
    {"email": "admin@testlookup.example.com",   "username": "admin",    "full_name": "Admin User",      "password": "Admin@2026!",  "role": "ADMIN"},
    {"email": "lead@testlookup.example.com",    "username": "qa_lead",  "full_name": "QA Lead",          "password": "Lead@2026!",   "role": "QA_LEAD"},
    {"email": "sara@testlookup.example.com",    "username": "eng_sara", "full_name": "Sara Engineer",    "password": "Sara@2026!",   "role": "QA_ENGINEER"},
    {"email": "viewer@testlookup.example.com",  "username": "viewer",   "full_name": "Viewer Read-only", "password": "View@2026!",   "role": "VIEWER"},
]

# ── Projects ────────────────────────────────────────────────────────────────
PROJECTS = [
    {
        "name": "Payment Service",
        "slug": "payment-service",
        "description": "Core payment processing API — Stripe, PayPal, Braintree integrations",
        "jira_project_key": "PAY",
        "ocp_namespace": "payment-prod",
        "jenkins_job_pattern": "payment-service-*",
    },
    {
        "name": "Auth Service",
        "slug": "auth-service",
        "description": "Authentication & authorisation — JWT, OAuth2, MFA, SSO",
        "jira_project_key": "AUTH",
        "ocp_namespace": "auth-prod",
        "jenkins_job_pattern": "auth-service-*",
    },
    {
        "name": "Inventory Service",
        "slug": "inventory-service",
        "description": "Stock management, reservations, warehouse sync & ERP integration",
        "jira_project_key": "INV",
        "ocp_namespace": "inventory-prod",
        "jenkins_job_pattern": "inventory-service-*",
    },
]

# ── Test definitions ─────────────────────────────────────────────────────────
# (test_name, class_name, suite, feature, severity, failure_rate, failure_category)
# failure_rate: 0.0=always pass  1.0=always fail  0.3-0.7=flaky

PAYMENT_TESTS = [
    ("testChargeCardSuccess",      "com.payments.ChargeTest",    "PaymentSuite",          "Payments",  "CRITICAL", 0.05, "PRODUCT_BUG"),
    ("testChargeCardDeclined",     "com.payments.ChargeTest",    "PaymentSuite",          "Payments",  "CRITICAL", 0.10, "PRODUCT_BUG"),
    ("testRefundFullAmount",       "com.payments.RefundTest",    "PaymentSuite",          "Refunds",   "CRITICAL", 0.0,  "PRODUCT_BUG"),
    ("testRefundPartialAmount",    "com.payments.RefundTest",    "PaymentSuite",          "Refunds",   "MAJOR",    0.45, "FLAKY"),
    ("testWebhookStripeRetry",     "com.payments.WebhookTest",   "PaymentSuite",          "Webhooks",  "CRITICAL", 0.50, "FLAKY"),
    ("testIdempotencyKey",         "com.payments.IdempotencyTest","PaymentSuite",         "Payments",  "MAJOR",    0.0,  "PRODUCT_BUG"),
    ("testGatewayTimeout",         "com.payments.GatewayTest",   "GatewayIntegrationSuite","Gateway", "CRITICAL", 0.90, "INFRASTRUCTURE"),
    ("testStripeConnect",          "com.payments.StripeTest",    "GatewayIntegrationSuite","Stripe",   "CRITICAL", 0.30, "FLAKY"),
    ("testPayPalCallback",         "com.payments.PayPalTest",    "GatewayIntegrationSuite","PayPal",   "MAJOR",    0.05, "PRODUCT_BUG"),
    ("testBraintreeToken",         "com.payments.BraintreeTest", "GatewayIntegrationSuite","Braintree","MAJOR",    0.20, "INFRASTRUCTURE"),
    ("testFraudDetection",         "com.payments.SecurityTest",  "SecuritySuite",         "Security",  "BLOCKER",  0.0,  "PRODUCT_BUG"),
    ("testCardEncryption",         "com.payments.SecurityTest",  "SecuritySuite",         "Security",  "CRITICAL", 0.0,  "PRODUCT_BUG"),
    ("testTokenization",           "com.payments.TokenTest",     "SecuritySuite",         "Security",  "CRITICAL", 0.10, "PRODUCT_BUG"),
    ("testCurrencyConversion",     "com.payments.CurrencyTest",  "PaymentSuite",          "Payments",  "MAJOR",    0.60, "FLAKY"),
    ("testBatchPaymentJob",        "com.payments.BatchTest",     "PaymentSuite",          "Batch",     "MINOR",    0.0,  "AUTOMATION_DEFECT"),
]

AUTH_TESTS = [
    ("testLoginSuccess",           "com.auth.LoginTest",         "AuthenticationSuite",   "Authentication","BLOCKER",  0.0,  "PRODUCT_BUG"),
    ("testLoginInvalidPassword",   "com.auth.LoginTest",         "AuthenticationSuite",   "Authentication","CRITICAL", 0.0,  "PRODUCT_BUG"),
    ("testLogoutSession",          "com.auth.SessionTest",       "AuthenticationSuite",   "Sessions",  "MAJOR",    0.0,  "PRODUCT_BUG"),
    ("testMFAEnrollment",          "com.auth.MFATest",           "AuthenticationSuite",   "MFA",       "CRITICAL", 0.40, "FLAKY"),
    ("testSSOSAMLFlow",            "com.auth.SSOTest",           "AuthenticationSuite",   "SSO",       "CRITICAL", 0.35, "FLAKY"),
    ("testSessionExpiry",          "com.auth.SessionTest",       "AuthenticationSuite",   "Sessions",  "MAJOR",    0.05, "PRODUCT_BUG"),
    ("testRBACAdmin",              "com.auth.RBACTest",          "AuthorizationSuite",    "Authorization","CRITICAL",0.0,  "PRODUCT_BUG"),
    ("testRBACReadOnly",           "com.auth.RBACTest",          "AuthorizationSuite",    "Authorization","MAJOR",   0.0,  "PRODUCT_BUG"),
    ("testJWTExpiredToken",        "com.auth.JWTTest",           "AuthorizationSuite",    "JWT",       "CRITICAL", 0.10, "PRODUCT_BUG"),
    ("testJWTInvalidSignature",    "com.auth.JWTTest",           "AuthorizationSuite",    "JWT",       "MAJOR",    0.0,  "PRODUCT_BUG"),
    ("testOAuthCodeFlow",          "com.auth.OAuthTest",         "AuthorizationSuite",    "OAuth",     "CRITICAL", 0.25, "FLAKY"),
    ("testPasswordReset",          "com.auth.PasswordTest",      "PasswordSuite",         "Password",  "CRITICAL", 0.0,  "PRODUCT_BUG"),
    ("testPasswordStrengthPolicy", "com.auth.PasswordTest",      "PasswordSuite",         "Password",  "MAJOR",    0.0,  "AUTOMATION_DEFECT"),
    ("testBruteForceProtection",   "com.auth.BruteForceTest",    "PasswordSuite",         "Security",  "BLOCKER",  0.0,  "PRODUCT_BUG"),
    ("testRateLimiting",           "com.auth.RateLimitTest",     "AuthenticationSuite",   "Security",  "CRITICAL", 0.85, "INFRASTRUCTURE"),
]

INVENTORY_TESTS = [
    ("testStockUpdateIncrement",   "com.inventory.StockTest",     "StockManagementSuite",  "Stock",    "CRITICAL", 0.0,  "PRODUCT_BUG"),
    ("testStockUpdateDecrement",   "com.inventory.StockTest",     "StockManagementSuite",  "Stock",    "CRITICAL", 0.0,  "PRODUCT_BUG"),
    ("testLowStockAlert",          "com.inventory.AlertTest",     "StockManagementSuite",  "Alerts",   "MAJOR",    0.15, "PRODUCT_BUG"),
    ("testStockReservation",       "com.inventory.ReservationTest","StockManagementSuite", "Reservations","CRITICAL",0.50,"FLAKY"),
    ("testReservationExpiry",      "com.inventory.ReservationTest","StockManagementSuite", "Reservations","MAJOR",  0.40, "FLAKY"),
    ("testProductSearchByName",    "com.inventory.SearchTest",    "SearchSuite",           "Search",   "MAJOR",    0.0,  "PRODUCT_BUG"),
    ("testProductSearchByCategory","com.inventory.SearchTest",    "SearchSuite",           "Search",   "MAJOR",    0.0,  "PRODUCT_BUG"),
    ("testFilterByPriceRange",     "com.inventory.FilterTest",    "SearchSuite",           "Search",   "MINOR",    0.0,  "PRODUCT_BUG"),
    ("testSortByPopularity",       "com.inventory.SortTest",      "SearchSuite",           "Search",   "MINOR",    0.30, "FLAKY"),
    ("testWarehouseSync",          "com.inventory.IntegrationTest","IntegrationSuite",     "Warehouse","CRITICAL", 0.88, "INFRASTRUCTURE"),
    ("testERPIntegration",         "com.inventory.IntegrationTest","IntegrationSuite",     "ERP",      "CRITICAL", 0.60, "FLAKY"),
    ("testBulkImport",             "com.inventory.BulkTest",      "StockManagementSuite",  "Bulk",     "MAJOR",    0.05, "TEST_DATA"),
    ("testInventoryReport",        "com.inventory.ReportTest",    "StockManagementSuite",  "Reports",  "MINOR",    0.0,  "PRODUCT_BUG"),
    ("testConcurrentStock",        "com.inventory.ConcurrencyTest","StockManagementSuite", "Concurrency","CRITICAL",0.75,"PRODUCT_BUG"),
    ("testExpiryTracking",         "com.inventory.ExpiryTest",    "StockManagementSuite",  "Expiry",   "MAJOR",    0.0,  "PRODUCT_BUG"),
]

PROJECT_TESTS = {
    "payment-service":   PAYMENT_TESTS,
    "auth-service":      AUTH_TESTS,
    "inventory-service": INVENTORY_TESTS,
}

# ── Failure messages ────────────────────────────────────────────────────────
FAILURE_MSGS = {
    "PRODUCT_BUG": [
        "java.lang.AssertionError: Expected status 200 but was 500\n\tat com.payments.ChargeTest.testChargeCard(ChargeTest.java:87)\n\tat sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)",
        "org.opentest4j.AssertionFailedError: expected: <APPROVED> but was: <PENDING>\n\tat com.payments.ChargeTest.assertApproved(ChargeTest.java:112)",
        "java.lang.AssertionError: Expected amount 100.00 but was 99.99 ==> currency rounding mismatch",
        "AssertionError: JWT claim 'sub' mismatch — token issued for user-123 but request bears user-456",
    ],
    "INFRASTRUCTURE": [
        "feign.RetryableException: Connection refused to payment-gateway:8443 after 3 retries\n\tat com.payments.GatewayTest.testStripeConnect(GatewayTest.java:44)",
        "org.springframework.web.reactive.function.client.WebClientRequestException: Failed to connect to redis:6379; nested: ConnectionRefused",
        "io.grpc.StatusRuntimeException: UNAVAILABLE: io exception\n\tcaused by: Connection refused: kafka:9092",
        "java.net.SocketTimeoutException: Read timed out after 30000ms waiting for response from warehouse-api",
    ],
    "FLAKY": [
        "java.lang.AssertionError: Expected exactly 1 row but got 0 — race condition in test setup\n\tat com.payments.RefundTest.testRefundPartialAmount(RefundTest.java:67)",
        "org.awaitility.core.ConditionTimeoutException: Condition with lambda expression not fulfilled within 5 seconds\n\tat com.auth.MFATest.testMFAEnrollment(MFATest.java:93)",
        "ConcurrentModificationException: test isolation failure — shared state leaked from previous test",
        "AssertionError: Expected idempotency guard to pass but received 409 Conflict on first attempt (timing issue)",
    ],
    "TEST_DATA": [
        "org.postgresql.util.PSQLException: ERROR: duplicate key value violates unique constraint 'uq_product_sku'\n\tDetail: Key (sku)=(PROD-001) already exists",
        "javax.validation.ConstraintViolationException: Validation failed for field 'price' — must not be null",
        "DataIntegrityViolationException: Column 'warehouse_id' cannot be null — test fixture missing FK",
    ],
    "AUTOMATION_DEFECT": [
        "NullPointerException: Test helper getAuthToken() returned null — auth server not started in @BeforeAll\n\tat com.payments.BatchTest.testBatchPaymentJob(BatchTest.java:22)",
        "IllegalStateException: WebDriver session timed out — Selenium Grid not available in this environment",
        "ConfigurationException: Missing test property 'payment.gateway.mock.url' — check test-application.yml",
    ],
}

# ── Jira defect templates per project ──────────────────────────────────────
DEFECT_TEMPLATES = {
    "payment-service": [
        ("PAY-1001", "Gateway timeout causes payment failure under load",             "testGatewayTimeout",         "OPEN"),
        ("PAY-1002", "Stripe webhook retry loop creates duplicate charge records",    "testWebhookStripeRetry",     "IN_PROGRESS"),
        ("PAY-1003", "Currency conversion rounds incorrectly for JPY transactions",   "testCurrencyConversion",     "OPEN"),
        ("PAY-1004", "Partial refund fails when original charge used 3DS auth",       "testRefundPartialAmount",    "OPEN"),
        ("PAY-1005", "Braintree token expires mid-flow on slow connections",          "testBraintreeToken",         "IN_PROGRESS"),
        ("PAY-1006", "Stripe Connect OAuth flow returns 500 on sandbox",              "testStripeConnect",          "OPEN"),
    ],
    "auth-service": [
        ("AUTH-501", "Rate limiter not initialising — Redis connection pool exhausted","testRateLimiting",          "OPEN"),
        ("AUTH-502", "MFA enrollment SMS provider intermittently returns 503",        "testMFAEnrollment",          "IN_PROGRESS"),
        ("AUTH-503", "SSO SAML assertion validation fails with Azure AD federation",  "testSSOSAMLFlow",            "OPEN"),
        ("AUTH-504", "OAuth code flow PKCE verifier rejected by legacy IdP",          "testOAuthCodeFlow",          "OPEN"),
        ("AUTH-505", "JWT expiry leeway allows 5-min window — security gap",          "testJWTExpiredToken",        "IN_PROGRESS"),
        ("AUTH-506", "Session expiry not propagated to refresh token table",          "testSessionExpiry",          "OPEN"),
    ],
    "inventory-service": [
        ("INV-201", "Warehouse sync fails — ERP API changed endpoint without notice", "testWarehouseSync",          "OPEN"),
        ("INV-202", "ERP integration 503 on weekends — scheduled maintenance window", "testERPIntegration",         "IN_PROGRESS"),
        ("INV-203", "Stock reservation race condition under concurrent checkouts",     "testStockReservation",       "OPEN"),
        ("INV-204", "Reservation expiry job processes same reservation twice",         "testReservationExpiry",      "OPEN"),
        ("INV-205", "Concurrent stock decrement goes below zero — missing row lock",   "testConcurrentStock",        "IN_PROGRESS"),
        ("INV-206", "Popularity sort index stale — missing nightly refresh job",      "testSortByPopularity",       "OPEN"),
    ],
}

# ── AI root-cause summaries per failed test ─────────────────────────────────
AI_SUMMARIES = {
    "testGatewayTimeout": (
        "Root cause: The payment gateway TCP connection pool is exhausted under concurrent load. "
        "Pool size (default 10) is insufficient for peak traffic of ~80 req/s. "
        "Recommend increasing pool size to 50 and adding circuit breaker with 5s timeout.",
        "INFRASTRUCTURE", 92, True, False, False,
        ["Increase connection pool size to 50 in GatewayConfig.java",
         "Implement Resilience4j circuit breaker on gateway calls",
         "Add /health/gateway endpoint to catch upstream degradation early"],
    ),
    "testWebhookStripeRetry": (
        "Root cause: Stripe webhook handler does not check idempotency key before processing. "
        "When Stripe retries (after a 20s timeout on our end), the charge is processed twice. "
        "The DB upsert guard exists but fires AFTER the charge API call.",
        "PRODUCT_BUG", 88, True, False, True,
        ["Move idempotency check before the charge API call in WebhookHandler.java",
         "Store idempotency key in Redis with 24h TTL before any external call",
         "Add integration test for webhook retry scenario with mock Stripe server"],
    ),
    "testRateLimiting": (
        "Root cause: Rate limiter uses Redis INCR with a fixed key per IP. "
        "Redis connection pool exhaustion (pool_size=5) causes all INCR calls to fail open, "
        "bypassing the rate limit entirely. Issue is intermittent — depends on pool availability.",
        "INFRASTRUCTURE", 95, False, False, False,
        ["Increase Redis pool size to 20 in RedisConfig.java",
         "Add fallback in RateLimiter to fail closed (block request) when Redis unavailable",
         "Add Redis connection pool saturation metric to Grafana dashboard"],
    ),
    "testWarehouseSync": (
        "Root cause: Warehouse API v2 endpoint requires Bearer token authentication, "
        "but our integration still sends API key in X-Api-Key header (v1 auth). "
        "ERP vendor changed auth scheme in their 2.14.0 release without backwards compatibility.",
        "INFRASTRUCTURE", 97, False, False, False,
        ["Update WarehouseClient to use OAuth2 client-credentials flow",
         "Rotate warehouse API credentials in Vault",
         "Add contract test against warehouse API schema to catch future breaking changes"],
    ),
    "testMFAEnrollment": (
        "Root cause: SMS provider (Twilio) intermittently returns HTTP 503 during peak enrollment windows. "
        "No retry logic exists in MFAService — the 503 propagates directly to the user. "
        "Affects approximately 40% of MFA enrollments between 09:00-11:00 UTC.",
        "INFRASTRUCTURE", 81, False, False, True,
        ["Implement exponential back-off retry (3 attempts) in MFAService.sendSMS()",
         "Add fallback to secondary SMS provider (MessageBird) after 2 Twilio failures",
         "Expose MFA success rate as a Prometheus metric for alerting"],
    ),
}

# ── Helpers ──────────────────────────────────────────────────────────────────
rng = random.Random(42)  # deterministic seed for reproducibility

def fingerprint(test_name: str, class_name: str) -> str:
    key = f"{class_name}::{test_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def ts(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")

def coin(rate: float) -> bool:
    """Return True (fail) with given probability."""
    return rng.random() < rate

def jitter(base_ms: int, pct: float = 0.25) -> int:
    delta = int(base_ms * pct)
    return base_ms + rng.randint(-delta, delta)


# ── MinIO client ─────────────────────────────────────────────────────────────
def make_s3():
    return boto3.client(
        "s3",
        endpoint_url=f"http://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )

def ensure_bucket(s3):
    try:
        s3.create_bucket(Bucket=BUCKET)
    except Exception as e:
        if "BucketAlreadyOwnedByYou" not in str(e) and "BucketAlreadyExists" not in str(e):
            raise


# ── Phase 1: API bootstrap (users + projects) ────────────────────────────────

def api_register(client: httpx.Client, user: dict) -> Optional[dict]:
    r = client.post("/api/v1/auth/register", json={
        "email": user["email"], "username": user["username"],
        "full_name": user["full_name"], "password": user["password"],
    })
    if r.status_code == 409:
        return None  # already exists
    r.raise_for_status()
    return r.json()


def api_login(client: httpx.Client, username: str, password: str) -> str:
    r = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def api_create_project(client: httpx.Client, project: dict) -> Optional[dict]:
    r = client.post("/api/v1/projects", json=project)
    if r.status_code == 409:
        # Already exists — fetch it
        r2 = client.get("/api/v1/projects")
        r2.raise_for_status()
        for p in r2.json():
            if p["slug"] == project["slug"]:
                return p
        return None
    r.raise_for_status()
    return r.json()


# ── Phase 2: DB seeding ──────────────────────────────────────────────────────

async def seed_db(project_map: dict[str, str]):
    """
    project_map: {slug: project_uuid_str}
    Direct DB insertions for historical runs, test cases, history, defects, AI analysis,
    and agent pipeline runs.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        if RESET:
            print("   Wiping previous seed data…")
            await db.execute(text("""
                DELETE FROM deep_findings WHERE test_run_id IN (
                    SELECT tr.id FROM test_runs tr
                    JOIN projects p ON tr.project_id = p.id
                    WHERE p.slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.execute(text("""
                DELETE FROM failure_clusters WHERE test_run_id IN (
                    SELECT tr.id FROM test_runs tr
                    JOIN projects p ON tr.project_id = p.id
                    WHERE p.slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.execute(text("""
                DELETE FROM release_decisions WHERE test_run_id IN (
                    SELECT tr.id FROM test_runs tr
                    JOIN projects p ON tr.project_id = p.id
                    WHERE p.slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.execute(text("""
                DELETE FROM agent_stage_results WHERE pipeline_run_id IN (
                    SELECT apr.id FROM agent_pipeline_runs apr
                    JOIN test_runs tr ON apr.test_run_id = tr.id
                    JOIN projects p ON tr.project_id = p.id
                    WHERE p.slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.execute(text("""
                DELETE FROM agent_pipeline_runs WHERE test_run_id IN (
                    SELECT tr.id FROM test_runs tr
                    JOIN projects p ON tr.project_id = p.id
                    WHERE p.slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.execute(text("""
                DELETE FROM ai_analysis WHERE test_case_id IN (
                    SELECT tc.id FROM test_cases tc
                    JOIN test_runs tr ON tc.test_run_id = tr.id
                    JOIN projects p ON tr.project_id = p.id
                    WHERE p.slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.execute(text("""
                DELETE FROM defects WHERE project_id IN (
                    SELECT id FROM projects WHERE slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.execute(text("""
                DELETE FROM test_case_history WHERE test_run_id IN (
                    SELECT tr.id FROM test_runs tr
                    JOIN projects p ON tr.project_id = p.id
                    WHERE p.slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.execute(text("""
                DELETE FROM test_cases WHERE test_run_id IN (
                    SELECT tr.id FROM test_runs tr
                    JOIN projects p ON tr.project_id = p.id
                    WHERE p.slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.execute(text("""
                DELETE FROM test_runs WHERE project_id IN (
                    SELECT id FROM projects WHERE slug IN ('payment-service','auth-service','inventory-service')
                )
            """))
            await db.commit()

        all_run_ids: dict[str, list[tuple[str, datetime, str]]] = {}  # slug → [(run_id, run_time, run_status)]
        for slug, proj_id in project_map.items():
            tests = PROJECT_TESTS[slug]
            print(f"   Seeding {slug}…")
            run_meta = await _seed_project_runs(db, slug, proj_id, tests)
            all_run_ids[slug] = run_meta
            await db.commit()  # commit each project so defects/AI can find the test cases

        print("   Seeding defects…")
        await _seed_defects(db, project_map)
        await db.commit()

        print("   Seeding AI analysis…")
        await _seed_ai_analysis(db, project_map)
        await db.commit()

        print("   Seeding agent pipeline runs…")
        await _seed_pipeline_runs(db, all_run_ids)
        await db.commit()

        print("   Seeding failure clusters + deep findings…")
        await _seed_failure_clusters_and_findings(db, all_run_ids)
        await db.commit()

        print("   Seeding release decisions…")
        await _seed_release_decisions(db, all_run_ids)
        await db.commit()
    await engine.dispose()


async def _seed_project_runs(db: AsyncSession, slug: str, proj_id: str, tests: list) -> list[tuple[str, datetime, str]]:
    """Create 12 historical runs for a project spread over the last 30 days.
    Returns list of (run_id, run_time, run_status) for pipeline seeding."""
    branches = ["main", "main", "main", "develop", "develop", "release/v2.1"]
    commits  = [uuid.uuid4().hex[:12] for _ in range(12)]
    pods     = [f"test-runner-{uuid.uuid4().hex[:8]}" for _ in range(12)]

    # Spread 12 runs over 30 days (every 2-3 days, offset per project to vary charts)
    project_offsets = {"payment-service": 0, "auth-service": 6, "inventory-service": 12}
    base_offset_hours = project_offsets.get(slug, 0)

    run_meta: list[tuple[str, datetime, str]] = []
    for i in range(12):
        days_ago = 30 - (i * 2)  # run 0 = 30 days ago, run 11 = 8 days ago
        run_time = now_utc() - timedelta(days=days_ago, hours=base_offset_hours + rng.randint(0, 4))
        build_number = f"build-{int(run_time.timestamp())}"
        jenkins_job  = f"{slug}-api-regression"
        branch       = rng.choice(branches)
        commit       = commits[i]
        pod_name     = pods[i]

        # Determine pass/fail counts for this run
        failed_names, case_rows = _build_test_cases(tests, str(uuid.uuid4()), run_time)
        total    = len(case_rows)
        passed   = sum(1 for c in case_rows if c["status"] == "PASSED")
        failed   = sum(1 for c in case_rows if c["status"] == "FAILED")
        broken   = sum(1 for c in case_rows if c["status"] == "BROKEN")
        skipped  = sum(1 for c in case_rows if c["status"] == "SKIPPED")
        pass_rate = round(passed / total * 100, 2) if total else 0
        duration_ms = sum(c["duration_ms"] or 0 for c in case_rows)
        end_time = run_time + timedelta(milliseconds=duration_ms)
        run_status = "PASSED" if failed == 0 and broken == 0 else "FAILED"

        run_id = str(uuid.uuid4())
        run_meta.append((run_id, run_time, run_status))

        await db.execute(text("""
            INSERT INTO test_runs (
                id, project_id, build_number, jenkins_job, trigger_source, branch,
                commit_hash, status, total_tests, passed_tests, failed_tests,
                skipped_tests, broken_tests, pass_rate, duration_ms,
                ocp_pod_name, ocp_namespace, start_time, end_time, created_at, updated_at
            ) VALUES (
                :id, :project_id, :build_number, :jenkins_job, :trigger_source, :branch,
                :commit_hash, :status, :total_tests, :passed_tests, :failed_tests,
                :skipped_tests, :broken_tests, :pass_rate, :duration_ms,
                :ocp_pod_name, :ocp_namespace, :start_time, :end_time, :created_at, :updated_at
            ) ON CONFLICT DO NOTHING
        """), {
            "id": run_id, "project_id": proj_id,
            "build_number": build_number, "jenkins_job": jenkins_job,
            "trigger_source": "push", "branch": branch, "commit_hash": commit,
            "status": run_status, "total_tests": total, "passed_tests": passed,
            "failed_tests": failed, "skipped_tests": skipped, "broken_tests": broken,
            "pass_rate": pass_rate, "duration_ms": duration_ms,
            "ocp_pod_name": pod_name, "ocp_namespace": f"{slug}-ns",
            "start_time": run_time, "end_time": end_time,
            "created_at": run_time, "updated_at": end_time,
        })

        for c in case_rows:
            c["id"] = str(uuid.uuid4())
            c["test_run_id"] = run_id
            c["created_at"] = run_time
            c["updated_at"] = end_time

            await db.execute(text("""
                INSERT INTO test_cases (
                    id, test_run_id, test_fingerprint, test_name, full_name,
                    suite_name, class_name, package_name, status, duration_ms,
                    severity, feature, failure_category, error_message,
                    has_attachments, created_at, updated_at
                ) VALUES (
                    :id, :test_run_id, :test_fingerprint, :test_name, :full_name,
                    :suite_name, :class_name, :package_name, :status, :duration_ms,
                    :severity, :feature, :failure_category, :error_message,
                    false, :created_at, :updated_at
                ) ON CONFLICT DO NOTHING
            """), c)

            await db.execute(text("""
                INSERT INTO test_case_history (
                    id, test_case_id, test_run_id, test_fingerprint,
                    status, duration_ms, failure_category, created_at
                ) VALUES (
                    :id, :tc_id, :run_id, :fp,
                    :status, :dur, :fc, :ts
                ) ON CONFLICT DO NOTHING
            """), {
                "id": str(uuid.uuid4()), "tc_id": c["id"], "run_id": run_id,
                "fp": c["test_fingerprint"], "status": c["status"],
                "dur": c["duration_ms"], "fc": c["failure_category"],
                "ts": run_time,
            })

    return run_meta


def _build_test_cases(tests: list, run_id: str, run_time: datetime) -> tuple[list, list]:
    """Build test case rows for one run based on failure rates."""
    failed_names = []
    rows = []
    base_start_ms = int(run_time.timestamp() * 1000)
    elapsed = 0

    for t in tests:
        test_name, class_name, suite, feature, severity, fail_rate, fail_cat = t
        failed = coin(fail_rate)
        # 5% of passing tests get SKIPPED to keep variety
        if not failed and coin(0.05):
            status = "SKIPPED"
        elif not failed:
            status = "PASSED"
        else:
            # 80% FAILED, 20% BROKEN for infrastructure issues
            if fail_cat == "INFRASTRUCTURE" and coin(0.5):
                status = "BROKEN"
            else:
                status = "FAILED"

        dur = jitter(2500 if status == "PASSED" else 8000)
        elapsed += dur

        pkg = ".".join(class_name.split(".")[:-1])
        fp = fingerprint(test_name, class_name)
        err = None
        fc = None

        if status in ("FAILED", "BROKEN"):
            failed_names.append(test_name)
            msgs = FAILURE_MSGS.get(fail_cat, FAILURE_MSGS["PRODUCT_BUG"])
            err = rng.choice(msgs)
            fc = fail_cat

        rows.append({
            "test_fingerprint": fp,
            "test_name": test_name,
            "full_name": f"{class_name}.{test_name}",
            "suite_name": suite,
            "class_name": class_name,
            "package_name": pkg,
            "status": status,
            "duration_ms": dur,
            "severity": severity,
            "feature": feature,
            "failure_category": fc,
            "error_message": err,
        })

    return failed_names, rows


PIPELINE_STAGES = [
    "ingestion",
    "anomaly_detection",
    "root_cause_analysis",
    "summary",
    "triage",
    "failure_clustering",
    "flaky_sentinel",
    "test_health",
    "release_risk",
]

STAGE_RESULT_DATA: dict[str, dict] = {
    "ingestion": {"tests_loaded": 15, "enriched": 15, "schema_version": "allure-2"},
    "anomaly_detection": {"regressions": 2, "new_failures": 1, "baseline_delta_pct": 12},
    "root_cause_analysis": {"analysed": 4, "high_confidence": 3, "llm_calls": 4},
    "summary": {"tokens_used": 1820, "model": "qwen2.5:7b", "sections": 5},
    "triage": {"jira_tickets_created": 2, "skipped_low_confidence": 1},
    "failure_clustering": {"clusters": 2, "deduplicated": 3},
    "flaky_sentinel": {"flaky_candidates": 2, "quarantined": 0},
    "test_health": {"antipatterns": 1, "smell_score": 0.18},
    "release_risk": {"risk_score": 42, "recommendation": "AMBER", "blocking_issues": 1},
}


async def _seed_pipeline_runs(
    db: AsyncSession,
    all_run_ids: dict[str, list[tuple[str, datetime, str]]],
) -> None:
    """Create AgentPipelineRun + AgentStageResult records for the last 8 runs per project."""
    for slug, run_meta in all_run_ids.items():
        # Seed the 8 most recent runs (indices 4-11 → days 22 to 8 days ago)
        for run_id, run_time, run_status in run_meta[4:]:
            # Most pipelines complete; ~20% fail mid-way; ~10% partially complete
            outcome_roll = rng.random()
            if outcome_roll < 0.70:
                pipeline_status = "completed"
                stages_to_complete = len(PIPELINE_STAGES)
            elif outcome_roll < 0.85:
                pipeline_status = "failed"
                stages_to_complete = rng.randint(2, 5)
            else:
                pipeline_status = "partial"
                stages_to_complete = rng.randint(5, 7)

            pipeline_id = str(uuid.uuid4())
            pipeline_start = run_time + timedelta(seconds=5)
            pipeline_duration_s = rng.randint(45, 180)
            pipeline_end = pipeline_start + timedelta(seconds=pipeline_duration_s) if pipeline_status != "running" else None
            pipeline_error = (
                "LLM timeout: root_cause_analysis exceeded 120s limit"
                if pipeline_status == "failed" and stages_to_complete <= 3
                else ("Jira API returned 503 — ticket creation skipped" if pipeline_status == "failed" else None)
            )

            await db.execute(text("""
                INSERT INTO agent_pipeline_runs (
                    id, test_run_id, workflow_type, status,
                    started_at, completed_at, error, created_at
                ) VALUES (
                    :id, :run_id, :wtype, :status,
                    :started, :completed, :error, :created
                ) ON CONFLICT DO NOTHING
            """), {
                "id": pipeline_id,
                "run_id": run_id,
                "wtype": "offline",
                "status": pipeline_status,
                "started": pipeline_start,
                "completed": pipeline_end,
                "error": pipeline_error,
                "created": pipeline_start,
            })

            # Create stage results
            stage_elapsed_s = 0
            for idx, stage_name in enumerate(PIPELINE_STAGES):
                if idx < stages_to_complete:
                    is_last_failing = (
                        pipeline_status == "failed" and idx == stages_to_complete - 1
                    )
                    stage_status = "failed" if is_last_failing else "completed"
                elif idx == stages_to_complete and pipeline_status == "partial":
                    stage_status = "skipped"
                else:
                    stage_status = "pending"

                stage_dur_s = rng.randint(3, 25)
                stage_start = pipeline_start + timedelta(seconds=stage_elapsed_s) if stage_status != "pending" else None
                stage_end = (
                    stage_start + timedelta(seconds=stage_dur_s)
                    if stage_status in ("completed", "failed") and stage_start
                    else None
                )
                if stage_status in ("completed", "failed"):
                    stage_elapsed_s += stage_dur_s

                result_data = STAGE_RESULT_DATA.get(stage_name) if stage_status == "completed" else None
                stage_error = (
                    pipeline_error if stage_status == "failed" else None
                )

                await db.execute(text("""
                    INSERT INTO agent_stage_results (
                        id, pipeline_run_id, stage_name, status,
                        started_at, completed_at, result_data, error
                    ) VALUES (
                        :id, :pipeline_id, :stage, :status,
                        :started, :completed, :result_data, :error
                    ) ON CONFLICT DO NOTHING
                """), {
                    "id": str(uuid.uuid4()),
                    "pipeline_id": pipeline_id,
                    "stage": stage_name,
                    "status": stage_status,
                    "started": stage_start,
                    "completed": stage_end,
                    "result_data": json.dumps(result_data) if result_data else None,
                    "error": stage_error,
                })


CLUSTER_TEMPLATES = [
    {
        "cluster_id": "cl_001",
        "label": "Gateway / external API connectivity failures",
        "representative_error": "feign.RetryableException: Connection refused to payment-gateway:8443 after 3 retries",
        "root_cause": (
            "Multiple tests are failing due to TCP connection pool exhaustion on the payment-gateway service. "
            "The default pool size of 10 is insufficient at peak load (~80 req/s). "
            "All affected tests share the GatewayClient bean — pool contention cascades across the suite."
        ),
        "failure_category": "INFRASTRUCTURE",
        "causal_chain": [
            {"step": 1, "service": "payment-gateway", "finding": "Connection pool exhausted — 10/10 slots occupied"},
            {"step": 2, "service": "test-runner",      "finding": "New test threads cannot acquire connections"},
            {"step": 3, "service": "feign-client",     "finding": "RetryableException thrown after 3 retries"},
        ],
        "affected_services": ["payment-gateway", "stripe-adapter", "braintree-adapter"],
        "recommended_actions": [
            "Increase GatewayClient pool size to 50 in GatewayConfig.java",
            "Add Resilience4j circuit breaker with 5-second timeout",
            "Monitor gateway pool saturation via Prometheus `hikari.connections.active` metric",
        ],
    },
    {
        "cluster_id": "cl_002",
        "label": "Flaky timing / race condition failures",
        "representative_error": "org.awaitility.core.ConditionTimeoutException: Condition not fulfilled within 5 seconds",
        "root_cause": (
            "Tests share mutable state between runs due to missing test isolation. "
            "Parallel test execution causes race conditions in setup/teardown hooks. "
            "The flakiness rate of 40-60% is consistent with a timing-sensitive shared resource."
        ),
        "failure_category": "FLAKY",
        "causal_chain": [
            {"step": 1, "service": "test-framework",  "finding": "Parallel threads share @BeforeAll static state"},
            {"step": 2, "service": "test-database",   "finding": "Leftover rows from previous test pollute assertions"},
            {"step": 3, "service": "awaitility",      "finding": "Condition timeout after 5s because state is inconsistent"},
        ],
        "affected_services": ["test-database", "mfa-service", "session-store"],
        "recommended_actions": [
            "Add @DirtiesContext or @Transactional rollback to each flaky test class",
            "Replace shared @BeforeAll setup with per-test @BeforeEach isolation",
            "Use TestContainers for a fresh DB per test class instead of shared schema",
        ],
    },
    {
        "cluster_id": "cl_003",
        "label": "Authentication / token validation failures",
        "representative_error": "AssertionError: JWT claim 'sub' mismatch — token issued for user-123 but request bears user-456",
        "root_cause": (
            "Token validation failures stem from a shared JWT signing key being rotated mid-suite. "
            "Tests that cache the token at @BeforeAll time receive an invalidated key after rotation. "
            "This surfaces as assertion failures rather than 401 errors because the exception is swallowed."
        ),
        "failure_category": "PRODUCT_BUG",
        "causal_chain": [
            {"step": 1, "service": "auth-service",  "finding": "JWT signing key rotated at 09:00 UTC daily"},
            {"step": 2, "service": "test-setup",    "finding": "Cached token from @BeforeAll signed with old key"},
            {"step": 3, "service": "api-gateway",   "finding": "Token accepted but subject claim decodes incorrectly"},
        ],
        "affected_services": ["auth-service", "api-gateway", "user-service"],
        "recommended_actions": [
            "Refresh auth tokens inside each @Test rather than caching at class level",
            "Add clock skew tolerance of 60s to JWT validator",
            "Align key rotation schedule to outside test execution windows",
        ],
    },
]


async def _seed_failure_clusters_and_findings(
    db: AsyncSession,
    all_run_ids: dict[str, list[tuple[str, datetime, str]]],
) -> None:
    """Create FailureCluster + DeepFinding records for recent failed/mixed runs."""
    for slug, run_meta in all_run_ids.items():
        # Use the most recent 6 runs (last 6 of 12)
        for run_id, run_time, run_status in run_meta[6:]:
            # Only create clusters for runs with failures (FAILED), or occasionally PASSED runs
            if run_status == "PASSED" and rng.random() > 0.3:
                continue

            # Pick 1-2 clusters per run
            num_clusters = rng.randint(1, 2)
            chosen = rng.sample(CLUSTER_TEMPLATES, num_clusters)

            for tpl in chosen:
                cluster_row_id = str(uuid.uuid4())
                # Grab 2-3 failed test case IDs from this run for member_test_ids
                r = await db.execute(text("""
                    SELECT id FROM test_cases
                    WHERE test_run_id = :run_id
                      AND status IN ('FAILED', 'BROKEN')
                    LIMIT 3
                """), {"run_id": run_id})
                member_ids = [str(row[0]) for row in r.fetchall()]
                cluster_size = max(1, len(member_ids))

                await db.execute(text("""
                    INSERT INTO failure_clusters (
                        id, test_run_id, cluster_id, label,
                        representative_error, member_test_ids, size, cohesion_score, created_at
                    ) VALUES (
                        :id, :run_id, :cluster_id, :label,
                        :repr_error, :member_ids, :size, :cohesion, :created_at
                    ) ON CONFLICT DO NOTHING
                """), {
                    "id": cluster_row_id,
                    "run_id": run_id,
                    "cluster_id": tpl["cluster_id"],
                    "label": tpl["label"],
                    "repr_error": tpl["representative_error"],
                    "member_ids": json.dumps(member_ids),
                    "size": cluster_size,
                    "cohesion": round(rng.uniform(0.65, 0.95), 2),
                    "created_at": run_time + timedelta(seconds=rng.randint(30, 120)),
                })

                confidence = rng.randint(78, 96)
                await db.execute(text("""
                    INSERT INTO deep_findings (
                        id, test_run_id, cluster_id, root_cause,
                        failure_category, confidence_score,
                        causal_chain, evidence, affected_services,
                        contract_violations, recommended_actions, created_at
                    ) VALUES (
                        :id, :run_id, :cluster_id, :root_cause,
                        :category, :confidence,
                        :causal_chain, :evidence, :affected_services,
                        :contract_violations, :recommended_actions, :created_at
                    ) ON CONFLICT DO NOTHING
                """), {
                    "id": str(uuid.uuid4()),
                    "run_id": run_id,
                    "cluster_id": tpl["cluster_id"],
                    "root_cause": tpl["root_cause"],
                    "category": tpl["failure_category"],
                    "confidence": confidence,
                    "causal_chain": json.dumps(tpl["causal_chain"]),
                    "evidence": json.dumps([
                        {"source": "stacktrace", "excerpt": tpl["representative_error"][:120]},
                        {"source": "history",    "excerpt": f"Failure rate consistent over last 14 days"},
                    ]),
                    "affected_services": json.dumps(tpl["affected_services"]),
                    "contract_violations": json.dumps([]),
                    "recommended_actions": json.dumps(tpl["recommended_actions"]),
                    "created_at": run_time + timedelta(seconds=rng.randint(60, 180)),
                })


async def _seed_release_decisions(
    db: AsyncSession,
    all_run_ids: dict[str, list[tuple[str, datetime, str]]],
) -> None:
    """Create one ReleaseDecision per recent run based on its pass/fail outcome."""
    for slug, run_meta in all_run_ids.items():
        # Use the most recent 8 runs
        for run_id, run_time, run_status in run_meta[4:]:
            # Fetch actual pass_rate from DB for realistic recommendation
            r = await db.execute(text(
                "SELECT pass_rate, failed_tests FROM test_runs WHERE id = :run_id"
            ), {"run_id": run_id})
            row = r.fetchone()
            if not row:
                continue
            pass_rate, failed_count = float(row[0] or 0), int(row[1] or 0)

            if pass_rate >= 95 and failed_count == 0:
                recommendation = "GO"
                risk_score = rng.randint(5, 20)
                blocking_issues: list = []
                conditions: list = []
                reasoning = (
                    f"All {15} tests passed with a {pass_rate:.1f}% pass rate. "
                    "No blocking issues detected. Pipeline completed successfully. "
                    "Recommend proceeding to production deployment."
                )
            elif pass_rate >= 80:
                recommendation = "CONDITIONAL_GO"
                risk_score = rng.randint(35, 55)
                blocking_issues = []
                conditions = [
                    f"Confirm {failed_count} failing test(s) are pre-existing known flaky tests",
                    "QA Lead sign-off required before production deploy",
                    "Monitor error rate in staging for 30 minutes post-deploy",
                ]
                reasoning = (
                    f"Pass rate of {pass_rate:.1f}% is acceptable but {failed_count} test(s) failed. "
                    "Failures appear to be infrastructure or flakiness related rather than regression. "
                    "Conditional approval granted pending QA lead review."
                )
            else:
                recommendation = "NO_GO"
                risk_score = rng.randint(65, 90)
                blocking_issues = [
                    f"{failed_count} test(s) failing — exceeds 20% failure threshold",
                    "Critical suite pass rate below 80% — release blocked",
                ]
                conditions = [
                    "Fix all CRITICAL and BLOCKER severity test failures",
                    "Rerun full regression suite and achieve ≥ 90% pass rate",
                ]
                reasoning = (
                    f"Pass rate of {pass_rate:.1f}% is below the 80% release threshold. "
                    f"{failed_count} test(s) failed, including potential regressions in critical paths. "
                    "Release is blocked. Investigate and resolve failures before re-running the gate."
                )

            await db.execute(text("""
                INSERT INTO release_decisions (
                    id, test_run_id, recommendation, risk_score,
                    blocking_issues, conditions_for_go, reasoning,
                    created_at, updated_at
                ) VALUES (
                    :id, :run_id, :recommendation, :risk_score,
                    :blocking_issues, :conditions, :reasoning,
                    :created_at, :updated_at
                ) ON CONFLICT (test_run_id) DO NOTHING
            """), {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "recommendation": recommendation,
                "risk_score": risk_score,
                "blocking_issues": json.dumps(blocking_issues),
                "conditions": json.dumps(conditions),
                "reasoning": reasoning,
                "created_at": run_time + timedelta(seconds=rng.randint(180, 300)),
                "updated_at": run_time + timedelta(seconds=rng.randint(180, 300)),
            })


async def _seed_defects(db: AsyncSession, project_map: dict):
    for slug, proj_id in project_map.items():
        defects = DEFECT_TEMPLATES.get(slug, [])
        for jira_key, summary, test_name, resolution in defects:
            # Find the most recent test case with this name in this project
            r = await db.execute(text("""
                SELECT tc.id FROM test_cases tc
                JOIN test_runs tr ON tc.test_run_id = tr.id
                WHERE tr.project_id = :pid AND tc.test_name = :tname
                  AND tc.status IN ('FAILED','BROKEN')
                ORDER BY tr.created_at DESC
                LIMIT 1
            """), {"pid": proj_id, "tname": test_name})
            row = r.fetchone()
            if not row:
                continue  # no failed test case found — skip this defect
            tc_id = str(row[0])

            await db.execute(text("""
                INSERT INTO defects (
                    id, test_case_id, project_id, jira_ticket_id, jira_ticket_url,
                    jira_status, ai_confidence_score, failure_category,
                    resolution_status, created_at
                ) VALUES (
                    :id, :tc_id, :proj_id, :jira_id, :jira_url,
                    :jira_status, :confidence, :category,
                    :resolution, :created_at
                ) ON CONFLICT DO NOTHING
            """), {
                "id": str(uuid.uuid4()),
                "tc_id": tc_id,
                "proj_id": proj_id,
                "jira_id": jira_key,
                "jira_url": f"https://jira.example.com/browse/{jira_key}",
                "jira_status": "In Progress" if resolution == "IN_PROGRESS" else "Open",
                "confidence": rng.randint(75, 96),
                "category": "INFRASTRUCTURE" if any(k in jira_key for k in ["AUTH-501","INV-201","INV-202"]) else "PRODUCT_BUG",
                "resolution": resolution,
                "created_at": now_utc() - timedelta(days=rng.randint(1, 20)),
            })


async def _seed_ai_analysis(db: AsyncSession, project_map: dict):
    targets = list(AI_SUMMARIES.keys())
    for slug, proj_id in project_map.items():
        tests = PROJECT_TESTS[slug]
        test_names = {t[0] for t in tests}

        for test_name, (summary, category, confidence, api_err, pod_err, flaky, actions) in AI_SUMMARIES.items():
            if test_name not in test_names:
                continue
            # Find most recent failed test case for this test in this project
            r = await db.execute(text("""
                SELECT tc.id FROM test_cases tc
                JOIN test_runs tr ON tc.test_run_id = tr.id
                WHERE tr.project_id = :pid AND tc.test_name = :tname
                  AND tc.status IN ('FAILED','BROKEN')
                ORDER BY tr.created_at DESC
                LIMIT 1
            """), {"pid": proj_id, "tname": test_name})
            row = r.fetchone()
            if not row:
                continue
            tc_id = str(row[0])

            await db.execute(text("""
                INSERT INTO ai_analysis (
                    id, test_case_id, root_cause_summary, failure_category,
                    backend_error_found, pod_issue_found, is_flaky,
                    confidence_score, recommended_actions, evidence_references,
                    llm_provider, llm_model, requires_human_review, created_at
                ) VALUES (
                    :id, :tc_id, :summary, :category,
                    :api_err, :pod_err, :flaky,
                    :confidence, :actions, :evidence,
                    :provider, :model, :review, :created_at
                ) ON CONFLICT (test_case_id) DO UPDATE SET
                    root_cause_summary = EXCLUDED.root_cause_summary,
                    confidence_score   = EXCLUDED.confidence_score
            """), {
                "id": str(uuid.uuid4()),
                "tc_id": tc_id,
                "summary": summary,
                "category": category,
                "api_err": api_err,
                "pod_err": pod_err,
                "flaky": flaky,
                "confidence": confidence,
                "actions": json.dumps(actions),
                "evidence": json.dumps([
                    {"source": "stacktrace",  "reference_id": f"mongo-{uuid.uuid4().hex[:8]}", "excerpt": "See error_message on test case"},
                    {"source": "flakiness",   "reference_id": "pg-history",                    "excerpt": f"Historical failure rate confirms issue pattern"},
                ]),
                "provider": "ollama",
                "model": "qwen2.5:7b",
                "review": confidence < 80,
                "created_at": now_utc() - timedelta(hours=rng.randint(1, 48)),
            })


# ── Phase 3: Live run via MinIO + webhook ────────────────────────────────────

def trigger_live_run(s3, token: str, slug: str):
    """Upload one real run to MinIO and fire the webhook so the agent pipeline runs."""
    print(f"   Triggering live run for {slug}…")
    tests = PROJECT_TESTS[slug]
    build_number = f"live-{int(time.time())}"
    prefix = f"{slug}/runs/{build_number}"
    run_time = now_utc()

    # Generate Allure results
    _, case_rows = _build_test_cases(tests, "n/a", run_time)
    allure_files = []
    for c in case_rows:
        start_ms = int(run_time.timestamp() * 1000)
        allure = {
            "uuid": str(uuid.uuid4()),
            "name": c["test_name"],
            "fullName": c["full_name"],
            "status": c["status"].lower(),
            "start": start_ms,
            "stop": start_ms + (c["duration_ms"] or 2000),
            "labels": [
                {"name": "suite",     "value": c["suite_name"]},
                {"name": "testClass", "value": c["class_name"]},
                {"name": "feature",   "value": c["feature"]},
                {"name": "severity",  "value": c["severity"].lower()},
            ],
            "steps": [],
            "attachments": [],
        }
        if c["error_message"]:
            allure["statusDetails"] = {
                "message": c["error_message"].split("\n")[0],
                "trace":   c["error_message"],
            }
        allure_files.append((f"{c['test_name']}-result.json", json.dumps(allure).encode()))

    # TestNG XML
    tc_xml = []
    for c in case_rows:
        dur = (c["duration_ms"] or 0) / 1000
        if c["status"] == "PASSED":
            tc_xml.append(f'  <testcase classname="{c["class_name"]}" name="{c["test_name"]}" time="{dur:.3f}"/>')
        elif c["status"] == "FAILED":
            msg = (c["error_message"] or "").split("\n")[0].replace('"', "'")
            tc_xml.append(f'  <testcase classname="{c["class_name"]}" name="{c["test_name"]}" time="{dur:.3f}">\n    <failure message="{msg}"/>\n  </testcase>')
        elif c["status"] == "BROKEN":
            msg = (c["error_message"] or "").split("\n")[0].replace('"', "'")
            tc_xml.append(f'  <testcase classname="{c["class_name"]}" name="{c["test_name"]}" time="{dur:.3f}">\n    <error message="{msg}"/>\n  </testcase>')
        else:
            tc_xml.append(f'  <testcase classname="{c["class_name"]}" name="{c["test_name"]}" time="{dur:.3f}">\n    <skipped/>\n  </testcase>')

    total_time_s = sum(c["duration_ms"] or 0 for c in case_rows) / 1000
    xml_body = "\n".join(tc_xml)
    xml_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite name="RegressionSuite" tests="{len(case_rows)}" time="{total_time_s:.3f}">\n'
        f"{xml_body}\n"
        "</testsuite>"
    )

    # Upload to MinIO
    for fname, body in allure_files:
        s3.put_object(Bucket=BUCKET, Key=f"{prefix}/allure/{fname}", Body=body)

    s3.put_object(Bucket=BUCKET, Key=f"{prefix}/testng/TEST-RegressionSuite.xml",
                  Body=xml_content.encode())

    sentinel = {
        "build_number":   build_number,
        "project_id":     slug,
        "jenkins_job":    f"{slug}-api-regression",
        "trigger_source": "push",
        "branch":         "main",
        "commit_hash":    uuid.uuid4().hex[:12],
        "ocp_pod_name":   f"test-runner-{uuid.uuid4().hex[:8]}",
        "ocp_namespace":  f"{slug}-ns",
    }
    s3.put_object(Bucket=BUCKET, Key=f"{prefix}/upload_complete.json",
                  Body=json.dumps(sentinel).encode())

    webhook_payload = {
        "EventName": "s3:ObjectCreated:Put",
        "Key":       f"{prefix}/upload_complete.json",
        "Records":   [],
    }
    with httpx.Client(base_url=BACKEND_URL, timeout=15) as client:
        r = client.post("/webhooks/minio", json=webhook_payload,
                        headers={"X-Webhook-Secret": WEBHOOK_SECRET})
        r.raise_for_status()
        result = r.json()

    passed  = sum(1 for c in case_rows if c["status"] == "PASSED")
    failed  = sum(1 for c in case_rows if c["status"] in ("FAILED","BROKEN"))
    print(f"   ✓ {slug}: {passed} passed / {failed} failed — task {result.get('task_id','?')[:8]}…")
    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n🚀  TestLookup — Seed Data Generator")
    print("=" * 50)

    # ── Phase 1: Users ────────────────────────────────────────────────────────
    print("\n[1/4] Creating users…")
    with httpx.Client(base_url=BACKEND_URL, timeout=15) as client:
        for u in USERS:
            result = api_register(client, u)
            if result:
                print(f"   ✓ Created  {u['username']} ({u['email']})")
            else:
                print(f"   ↩ Exists   {u['username']}")

    # Promote roles + reset passwords directly in DB
    print("\n   Setting roles and passwords via DB…")

    async def promote_roles():
        import bcrypt
        engine = create_async_engine(DATABASE_URL, echo=False)
        async with engine.begin() as conn:
            for u in USERS:
                hashed = bcrypt.hashpw(u["password"].encode(), bcrypt.gensalt()).decode()
                await conn.execute(text(
                    "UPDATE users SET role = :role, hashed_password = :pw WHERE username = :uname"
                ), {"role": u["role"], "uname": u["username"], "pw": hashed})
        await engine.dispose()

    asyncio.run(promote_roles())
    print("   ✓ Roles assigned")

    # ── Phase 2: Projects ─────────────────────────────────────────────────────
    print("\n[2/4] Creating projects…")
    project_map = {}  # slug → uuid str

    # Re-login (token is still valid, but get fresh one with promoted role)
    with httpx.Client(base_url=BACKEND_URL, timeout=15) as client:
        token = api_login(client, USERS[0]["username"], USERS[0]["password"])
        auth  = {"Authorization": f"Bearer {token}"}

        for p in PROJECTS:
            client.headers.update(auth)
            proj = api_create_project(client, {
                "name":               p["name"],
                "slug":               p["slug"],
                "description":        p["description"],
                "jira_project_key":   p.get("jira_project_key"),
                "ocp_namespace":      p.get("ocp_namespace"),
                "jenkins_job_pattern":p.get("jenkins_job_pattern"),
            })
            if proj:
                project_map[p["slug"]] = str(proj["id"])
                print(f"   ✓ {p['name']}  (id={proj['id'][:8]}…)")
            else:
                print(f"   ✗ Failed to get project: {p['slug']}")

    # ── Phase 3: Historical DB seed ───────────────────────────────────────────
    print(f"\n[3/4] Seeding historical data ({12 * len(project_map)} runs, {15 * 12 * len(project_map)} test cases)…")
    asyncio.run(seed_db(project_map))
    print("   ✓ Historical data complete")

    # ── Phase 4: Live run ─────────────────────────────────────────────────────
    print("\n[4/4] Triggering live runs via MinIO + webhook (agent pipeline)…")
    s3 = make_s3()
    ensure_bucket(s3)

    for slug in project_map:
        trigger_live_run(s3, token, slug)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("✅  Seed complete!")
    print()
    print("  Users:")
    for u in USERS:
        print(f"    {u['username']:12s}  {u['email']:30s}  role={u['role']}  pwd={u['password']}")
    print()
    print("  Projects created:", ", ".join(project_map.keys()))
    print()
    print("  Dashboard features now populated:")
    print("    • Overview KPIs + trend charts      (30 days × 3 projects)")
    print("    • Flaky test leaderboard             (tests with 30-60% failure rate)")
    print("    • Failure category pie chart         (PRODUCT_BUG, INFRASTRUCTURE, FLAKY…)")
    print("    • Top failing tests                  (consistent failures per run)")
    print("    • Coverage table + stacked bar chart (15 unique tests × 3 suites)")
    print("    • Defects page with Jira links       (6 defects × 3 projects)")
    print("    • AI analysis summaries              (root-cause + recommendations)")
    print("    • Agent pipeline runs                (8 runs × 3 projects = 24 pipelines)")
    print("    • Agent stage results                (9 stages per pipeline, mixed statuses)")
    print("    • Failure clusters + deep findings   (1-2 clusters per recent run)")
    print("    • Release gate decisions             (GO/NO_GO/CONDITIONAL per recent run)")
    print("    • Live agent pipeline runs           (triggered now — check worker logs)")
    print()
    print("  Open dashboard:  http://localhost:3000")
    print("  Login:           use Quick Login buttons at http://localhost:3000 (no password required in dev)")
    print()

if __name__ == "__main__":
    main()
