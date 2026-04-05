#!/usr/bin/env python3
"""
Simulate a Jenkins test run upload to MinIO, then trigger ingestion via webhook.
Run inside the backend container:
    docker compose exec backend python scripts/simulate_upload.py [project_id] [build_number]
"""
import json
import os
import sys
import time
import uuid

import boto3
from botocore.client import Config
import httpx

# ── Config ─────────────────────────────────────────────────────────────────
PROJECT_ID    = sys.argv[1] if len(sys.argv) > 1 else "demo-project"
BUILD_NUMBER  = sys.argv[2] if len(sys.argv) > 2 else f"build-{int(time.time())}"
BUCKET        = os.getenv("MINIO_BUCKET_NAME", "test-telemetry")
PREFIX        = f"{PROJECT_ID}/runs/{BUILD_NUMBER}"

MINIO_ENDPOINT   = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")
BACKEND_URL      = "http://localhost:8000"

print(f"🚀  Simulating upload  project={PROJECT_ID}  build={BUILD_NUMBER}")

# ── MinIO client ───────────────────────────────────────────────────────────
s3 = boto3.client(
    "s3",
    endpoint_url=f"http://{MINIO_ENDPOINT}",
    aws_access_key_id=MINIO_ACCESS_KEY,
    aws_secret_access_key=MINIO_SECRET_KEY,
    config=Config(signature_version="s3v4"),
    region_name="us-east-1",
)

# Create bucket if it doesn't exist
try:
    s3.create_bucket(Bucket=BUCKET)
    print(f"   Created bucket: {BUCKET}")
except s3.exceptions.BucketAlreadyOwnedByYou:
    pass
except Exception as e:
    if "BucketAlreadyExists" not in str(e):
        raise

# ── Sample test result files ───────────────────────────────────────────────
allure_pass = {
    "uuid": str(uuid.uuid4()),
    "name": "testPaymentSuccess",
    "fullName": "com.company.PaymentTest.testPaymentSuccess",
    "status": "passed",
    "start": 1710000000000,
    "stop": 1710000003200,
    "labels": [
        {"name": "suite",     "value": "PaymentSuite"},
        {"name": "testClass", "value": "com.company.PaymentTest"},
        {"name": "feature",   "value": "Payments"},
        {"name": "severity",  "value": "critical"},
    ],
    "steps": [
        {"name": "POST /api/v1/payments/charge", "status": "passed"},
        {"name": "Assert response 200",          "status": "passed"},
    ],
    "attachments": [],
}

allure_fail = {
    "uuid": str(uuid.uuid4()),
    "name": "testPaymentGatewayTimeout",
    "fullName": "com.company.PaymentTest.testPaymentGatewayTimeout",
    "status": "failed",
    "start": 1710000010000,
    "stop": 1710000015432,
    "labels": [
        {"name": "suite",     "value": "PaymentSuite"},
        {"name": "testClass", "value": "com.company.PaymentTest"},
        {"name": "feature",   "value": "Payments"},
        {"name": "severity",  "value": "critical"},
        {"name": "owner",     "value": "qa-team"},
    ],
    "statusDetails": {
        "message": "java.lang.AssertionError: Expected status 200 but was 500",
        "trace": (
            "java.lang.AssertionError: Expected status 200 but was 500\n"
            "\tat com.company.PaymentTest.testPaymentGatewayTimeout(PaymentTest.java:87)"
        ),
    },
    "steps": [
        {"name": "POST /api/v1/payments/charge", "status": "failed"},
        {"name": "Assert response 200",          "status": "failed"},
    ],
    "attachments": [],
}

allure_skip = {
    "uuid": str(uuid.uuid4()),
    "name": "testRefundPending",
    "fullName": "com.company.PaymentTest.testRefundPending",
    "status": "skipped",
    "start": 1710000020000,
    "stop": 1710000020100,
    "labels": [
        {"name": "suite",     "value": "PaymentSuite"},
        {"name": "testClass", "value": "com.company.PaymentTest"},
        {"name": "feature",   "value": "Payments"},
    ],
    "steps": [],
    "attachments": [],
}

testng_xml = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="PaymentSuite" tests="3" failures="1" skipped="1" time="18.732">
  <testcase classname="com.company.PaymentTest" name="testPaymentSuccess" time="3.200"/>
  <testcase classname="com.company.PaymentTest" name="testPaymentGatewayTimeout" time="5.432">
    <failure message="Expected status 200 but was 500">java.lang.AssertionError at PaymentTest.java:87</failure>
  </testcase>
  <testcase classname="com.company.PaymentTest" name="testRefundPending" time="0.100">
    <skipped/>
  </testcase>
</testsuite>
"""

# ── Upload files ───────────────────────────────────────────────────────────
uploads = [
    (f"{PREFIX}/allure/test-pass-result.json",  json.dumps(allure_pass).encode()),
    (f"{PREFIX}/allure/test-fail-result.json",  json.dumps(allure_fail).encode()),
    (f"{PREFIX}/allure/test-skip-result.json",  json.dumps(allure_skip).encode()),
    (f"{PREFIX}/testng/TEST-PaymentSuite.xml",  testng_xml.encode()),
]

print("📤  Uploading test result files…")
for key, body in uploads:
    s3.put_object(Bucket=BUCKET, Key=key, Body=body)
    print(f"   uploaded: {key}")

# ── Write sentinel file ────────────────────────────────────────────────────
sentinel = {
    "build_number":   BUILD_NUMBER,
    "project_id":     PROJECT_ID,
    "jenkins_job":    "api-regression-pipeline",
    "trigger_source": "push",
    "branch":         "main",
    "commit_hash":    "abc123def456",
    "ocp_pod_name":   f"test-runner-{uuid.uuid4().hex[:8]}",
    "ocp_namespace":  "qa-testing",
}
sentinel_key = f"{PREFIX}/upload_complete.json"
s3.put_object(Bucket=BUCKET, Key=sentinel_key, Body=json.dumps(sentinel).encode())
print(f"📤  Wrote sentinel:  {sentinel_key}")

# ── Fire webhook to trigger ingestion ─────────────────────────────────────
webhook_payload = {
    "EventName": "s3:ObjectCreated:Put",
    "Key":       sentinel_key,
    "Records":   [],
}
print("🔔  Calling webhook endpoint…")
resp = httpx.post(
    f"{BACKEND_URL}/webhooks/minio",
    json=webhook_payload,
    headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    timeout=10,
)
resp.raise_for_status()
result = resp.json()
print(f"   Response: {result}")

print()
print("✅  Simulation complete!")
print(f"   Project:  {PROJECT_ID}")
print(f"   Build:    {BUILD_NUMBER}")
print(f"   Task ID:  {result.get('task_id', 'n/a')}")
print()
print("   Watch logs:    docker compose logs -f backend worker")
print("   Dashboard:     http://localhost:3000")
