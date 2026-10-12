#!/usr/bin/env bash
# ============================================================
# Simulate a Jenkins test run uploading Allure results to MinIO
# Usage: ./scripts/simulate-upload.sh [project_id] [build_number]
# ============================================================
set -euo pipefail

PROJECT_ID="${1:-demo-project}"
BUILD_NUMBER="${2:-build-$(date +%s)}"
MINIO_ALIAS="local"
BUCKET="test-telemetry"
PREFIX="${PROJECT_ID}/runs/${BUILD_NUMBER}"
TMP_DIR=$(mktemp -d)

echo "🚀  Simulating upload for project=${PROJECT_ID} build=${BUILD_NUMBER}"

# Ensure mc alias exists (credentials from environment)
mc alias set ${MINIO_ALIAS} http://localhost:9000 "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" 2>/dev/null || true

# Create bucket if missing
mc mb --ignore-existing ${MINIO_ALIAS}/${BUCKET}

# ── Generate fake Allure result files ─────────────────────────
mkdir -p "${TMP_DIR}/allure"
mkdir -p "${TMP_DIR}/testng"

# Passing test
cat > "${TMP_DIR}/allure/test-pass-result.json" << 'ALLURE'
{
  "uuid": "aaa-111-pass",
  "name": "testPaymentSuccess",
  "fullName": "com.company.PaymentTest.testPaymentSuccess",
  "status": "passed",
  "start": 1710000000000,
  "stop": 1710000003200,
  "labels": [
    {"name": "suite",     "value": "PaymentSuite"},
    {"name": "testClass", "value": "com.company.PaymentTest"},
    {"name": "feature",   "value": "Payments"},
    {"name": "severity",  "value": "critical"}
  ],
  "steps": [
    {"name": "POST /api/v1/payments/charge", "status": "passed"},
    {"name": "Assert response 200",          "status": "passed"}
  ],
  "attachments": []
}
ALLURE

# Failing test
cat > "${TMP_DIR}/allure/test-fail-result.json" << 'ALLURE'
{
  "uuid": "bbb-222-fail",
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
    {"name": "owner",     "value": "qa-team"}
  ],
  "statusDetails": {
    "message": "java.lang.AssertionError: Expected status 200 but was 500",
    "trace": "java.lang.AssertionError: Expected status 200 but was 500\n\tat com.company.PaymentTest.testPaymentGatewayTimeout(PaymentTest.java:87)\n\tat sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)"
  },
  "steps": [
    {"name": "POST /api/v1/payments/charge", "status": "failed"},
    {"name": "Assert response 200",          "status": "failed"}
  ],
  "attachments": []
}
ALLURE

# Skipped test
cat > "${TMP_DIR}/allure/test-skip-result.json" << 'ALLURE'
{
  "uuid": "ccc-333-skip",
  "name": "testRefundPending",
  "fullName": "com.company.PaymentTest.testRefundPending",
  "status": "skipped",
  "start": 1710000020000,
  "stop": 1710000020100,
  "labels": [
    {"name": "suite",     "value": "PaymentSuite"},
    {"name": "testClass", "value": "com.company.PaymentTest"},
    {"name": "feature",   "value": "Payments"}
  ],
  "steps": [],
  "attachments": []
}
ALLURE

# TestNG XML
cat > "${TMP_DIR}/testng/TEST-PaymentSuite.xml" << 'TESTNG'
<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="PaymentSuite" tests="3" failures="1" skipped="1" time="18.732">
  <testcase classname="com.company.PaymentTest" name="testPaymentSuccess"           time="3.200"/>
  <testcase classname="com.company.PaymentTest" name="testPaymentGatewayTimeout"    time="5.432">
    <failure message="Expected status 200 but was 500">java.lang.AssertionError at PaymentTest.java:87</failure>
  </testcase>
  <testcase classname="com.company.PaymentTest" name="testRefundPending"            time="0.100">
    <skipped/>
  </testcase>
</testsuite>
TESTNG

# ── Upload to MinIO ────────────────────────────────────────────
echo "📤  Uploading Allure results…"
mc mirror "${TMP_DIR}/allure/"  "${MINIO_ALIAS}/${BUCKET}/${PREFIX}/allure/"
echo "📤  Uploading TestNG XML…"
mc mirror "${TMP_DIR}/testng/"  "${MINIO_ALIAS}/${BUCKET}/${PREFIX}/testng/"

# ── Write sentinel file ────────────────────────────────────────
echo "📤  Writing sentinel file to trigger ingestion…"
SENTINEL=$(cat << JSON
{
  "build_number": "${BUILD_NUMBER}",
  "project_id": "${PROJECT_ID}",
  "jenkins_job": "api-regression-pipeline",
  "trigger_source": "push",
  "branch": "main",
  "commit_hash": "abc123def456",
  "ocp_pod_name": "test-runner-$(openssl rand -hex 4)",
  "ocp_namespace": "qa-testing"
}
JSON
)
echo "${SENTINEL}" | mc pipe "${MINIO_ALIAS}/${BUCKET}/${PREFIX}/upload_complete.json"

# ── Done ───────────────────────────────────────────────────────
rm -rf "${TMP_DIR}"
echo ""
echo "✅  Upload complete!"
echo "   Project:  ${PROJECT_ID}"
echo "   Build:    ${BUILD_NUMBER}"
echo "   Prefix:   ${PREFIX}/"
echo ""
echo "   Watch backend logs:  docker compose logs -f backend worker"
echo "   Open dashboard:      http://localhost:3000"
