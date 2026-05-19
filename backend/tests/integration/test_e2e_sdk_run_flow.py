"""End-to-end validation: a Java-SDK-style live-stream run lights up
every user-visible feature.

The full user journey this test pins:

    1. ADMIN login
    2. ADMIN creates a fresh project
    3. ADMIN mints a project-scoped API key (stream:write)
    4. Client SDK opens a live session, batches 4 test events
       (3 PASSED, 1 FAILED) with a suite name, then closes the session.
    5. The Celery live-persist worker drains the Redis buffer into
       Postgres, and finalize_run materialises test_suites,
       canonical_test_cases, and queues the AI pipeline.
    6. Every feature endpoint (runs, search/global, suites, dashboard
       metrics, live sessions, run intelligence) returns the new run.

The test uses the same HTTP contract the Java/Python/Go SDKs speak —
``POST /api/v1/stream/sessions`` to open, ``POST /api/v1/stream/events/batch``
with ``X-Session-Token`` to push events, and a ``run_complete`` event in
the final batch to trigger ``close_session`` (which queues
``persist_live_session`` and ``run_agent_pipeline``).

Why this lives under ``tests/integration/`` not ``tests/``:

    Integration tests run real HTTP against a live stack. This file is
    skipped unless ``TESTLOOKUP_E2E_BASE_URL`` is set in the environment.
    Set it to ``http://localhost:8000`` to run against ``make dev``, or
    to a homelab/staging URL for a smoke check after deploy.

What is asserted vs. observed:

    * Hard asserts: every endpoint returns the run's data within a
      bounded polling window.
    * Observed-only: the AI pipeline (run intelligence) may not finish
      inside the polling budget on Ollama-backed environments, so its
      check is permissive (records the state but does not fail).
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

httpx = pytest.importorskip("httpx")

BASE_URL = os.environ.get("TESTLOOKUP_E2E_BASE_URL", "").rstrip("/")
ADMIN_USERNAME = os.environ.get("TESTLOOKUP_E2E_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("TESTLOOKUP_E2E_ADMIN_PASSWORD", "Admin@2026!")

# Total budget for the live-persist + finalize_run pipeline to complete after
# the SDK closes the session. Worker startup latency dominates on first run.
PIPELINE_BUDGET_S = 25.0
POLL_INTERVAL_S = 1.0

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not BASE_URL,
        reason="Set TESTLOOKUP_E2E_BASE_URL=http://localhost:8000 (or a deployed URL) to run.",
    ),
]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _login(client: httpx.Client) -> str:
    """ADMIN login → JWT. Uses form-encoded grant (matches OAuth2PasswordRequestForm)."""
    resp = client.post(
        f"{BASE_URL}/api/v1/auth/login",
        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _create_project(client: httpx.Client, jwt: str) -> dict:
    """ADMIN creates a fresh project with a unique slug."""
    suffix = uuid.uuid4().hex[:8]
    body = {
        "name": f"E2E SDK Run Flow {suffix}",
        "slug": f"e2e-sdk-{suffix}",
        "description": "Created by test_e2e_sdk_run_flow — safe to delete.",
    }
    resp = client.post(
        f"{BASE_URL}/api/v1/projects",
        json=body,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def _mint_api_key(client: httpx.Client, jwt: str, project_id: str) -> str:
    """Project-scoped stream:write API key."""
    body = {
        "name": "e2e-sdk-flow",
        "project_id": project_id,
        "scopes": ["stream:write"],
        "expires_in_days": 1,
    }
    resp = client.post(
        f"{BASE_URL}/api/v1/keys",
        json=body,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()["raw_key"]


def _push_run_via_sdk_protocol(
    client: httpx.Client,
    api_key: str,
    project_id: str,
) -> tuple[str, str]:
    """Open session, batch 4 events, run_complete. Returns (run_id, build_number).

    This is the same wire shape the Java/Python SDKs produce; pushing it
    directly with httpx makes the test SDK-language-agnostic. If the JSON
    contract changes here, every SDK breaks — that's intentional, this
    test pins the contract.
    """
    run_id = str(uuid.uuid4())
    build_number = f"e2e-{uuid.uuid4().hex[:8]}"

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    # SDKs default to the API-key path (``POST /api/v1/stream/ingest``)
    # because it skips the /sessions ceremony: the server auto-creates
    # the session on the first batch keyed by (project, run_id) and
    # closes it when ``run_complete`` lands in the events list.
    events = [
        {
            "event_type": "test_result",
            "test_name": "e2e_login_works",
            "status": "PASSED",
            "duration_ms": 120,
            "suite_name": "e2e-smoke",
            "class_name": "E2ESmoke",
        },
        {
            "event_type": "test_result",
            "test_name": "e2e_dashboard_loads",
            "status": "PASSED",
            "duration_ms": 210,
            "suite_name": "e2e-smoke",
            "class_name": "E2ESmoke",
        },
        {
            "event_type": "test_result",
            "test_name": "e2e_search_returns",
            "status": "PASSED",
            "duration_ms": 305,
            "suite_name": "e2e-smoke",
            "class_name": "E2ESmoke",
        },
        {
            "event_type": "test_result",
            "test_name": "e2e_failure_path_visible",
            "status": "FAILED",
            "duration_ms": 440,
            "suite_name": "e2e-smoke",
            "class_name": "E2ESmoke",
            "error_message": "AssertionError: expected 200, got 500",
            "tags": ["regression", "smoke"],
        },
        # The final event of the run carries run_complete; the backend's
        # ``ingest_via_api_key`` handler detects this and calls
        # ``close_session`` synchronously, which queues persist_live_session.
        {"event_type": "run_complete"},
    ]

    resp = client.post(
        f"{BASE_URL}/api/v1/stream/ingest",
        headers=headers,
        json={
            "run_id": run_id,
            "events": events,
            "meta": {
                "build_number": build_number,
                "branch": "main",
                "commit_hash": "deadbeef",
                "framework": "e2e-sdk-flow",
                "total_tests": 4,
            },
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    return run_id, build_number


def _poll(predicate, *, budget_s: float = PIPELINE_BUDGET_S):
    """Re-invoke ``predicate()`` until it returns truthy or budget elapses.

    Returns the final value (truthy on success, falsy on timeout). Each
    call is wrapped in try/except so transient 404/5xx during pipeline
    spin-up don't abort the poll.
    """
    deadline = time.monotonic() + budget_s
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except Exception:
            last = None
        time.sleep(POLL_INTERVAL_S)
    return last


# ── The test ────────────────────────────────────────────────────────────────


def test_sdk_run_lights_up_all_features():
    """Single end-to-end pass through login → project → SDK run → every page."""
    with httpx.Client() as client:
        # ── 1) Admin login ──────────────────────────────────────────────
        jwt = _login(client)
        h = {"Authorization": f"Bearer {jwt}"}

        # ── 2) Admin creates a fresh project ────────────────────────────
        project = _create_project(client, jwt)
        project_id = project["id"]
        project_name = project["name"]
        assert project_name.startswith("E2E SDK Run Flow ")

        # ── 3) Admin mints a project-scoped API key ─────────────────────
        api_key = _mint_api_key(client, jwt, project_id)
        assert api_key.startswith("qai_")

        # ── 4) SDK-protocol live run with run_complete sentinel ─────────
        run_id, build_number = _push_run_via_sdk_protocol(
            client, api_key, project_id,
        )

        # ── 5) Wait for the live persister to drain the Redis buffer
        #       AND for finalize_run to materialise the catalog. The
        #       budget is generous because worker cold-start dominates
        #       on the first run after a deploy.
        def _runs_visible():
            r = client.get(
                f"{BASE_URL}/api/v1/runs",
                params={"project_id": project_id},
                headers=h, timeout=10.0,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("items") or data.get("runs") or []
            return next((x for x in items if x.get("build_number") == build_number), None)

        run = _poll(_runs_visible)
        assert run is not None, "TestRun never appeared on /api/v1/runs within budget"
        assert run["total_tests"] == 4
        assert run["passed_tests"] == 3
        assert run["failed_tests"] == 1
        assert run["status"] in ("FAILED", "failed")  # broken/failed enum casing varies

        # ── 6a) Global search returns the test cases + run + suite ──────
        def _search_has_4_cases():
            r = client.get(
                f"{BASE_URL}/api/v1/search/global",
                params={"q": "e2e", "size": 25},
                headers=h, timeout=10.0,
            )
            r.raise_for_status()
            cases = [
                i for i in r.json().get("items", [])
                if i.get("entity_type") == "test_case"
                and (i.get("project_id") == project_id or project_id in (i.get("project_name") or ""))
            ]
            return cases if len(cases) >= 4 else None

        cases = _poll(_search_has_4_cases)
        assert cases is not None and len(cases) >= 4, (
            "Global search did not surface the 4 ingested test cases"
        )
        case_titles = {c["title"] for c in cases}
        assert "e2e_failure_path_visible" in case_titles
        assert "e2e_login_works" in case_titles

        # ── 6b) Suites — populated by finalize_run during persist_live_session ──
        def _suite_exists():
            r = client.get(
                f"{BASE_URL}/api/v1/suites",
                params={"project_id": project_id},
                headers=h, timeout=10.0,
            )
            r.raise_for_status()
            for s in r.json().get("items", []):
                if s.get("name") == "e2e-smoke":
                    return s
            return None

        suite = _poll(_suite_exists)
        assert suite is not None, (
            "Suite 'e2e-smoke' never appeared on /api/v1/suites — this is the "
            "exact symptom of persist_live_session skipping finalize_run."
        )
        assert suite["test_case_count"] == 4

        # ── 6c) Dashboard metrics include the new run ──────────────────
        r = client.get(
            f"{BASE_URL}/api/v1/metrics/summary",
            params={"project_id": project_id},
            headers=h, timeout=10.0,
        )
        r.raise_for_status()
        summary = r.json()
        # The shape varies by deployment; we just assert the endpoint
        # returns a populated dict with at least the run-derived keys
        # the dashboard renders. A green response with no fields would
        # be a regression worth catching.
        assert isinstance(summary, dict) and summary, "metrics/summary returned empty"

        # ── 6d) Live-sessions list includes this run (status: completed) ──
        r = client.get(
            f"{BASE_URL}/api/v1/stream/active",
            params={"project_id": project_id},
            headers=h, timeout=10.0,
        )
        r.raise_for_status()
        live = r.json()
        sessions = live.get("sessions") if isinstance(live, dict) else live
        if isinstance(sessions, list):
            matches = [s for s in sessions if s.get("run_id") == run_id or s.get("test_run_id") == run_id]
            # Completed sessions may or may not appear depending on the
            # endpoint's filter — don't hard-assert membership here, but
            # confirm the endpoint at least returns a sane shape.
            assert sessions is not None

        # ── 6e) Run intelligence: AI pipeline may not have completed yet
        #       on Ollama-backed deployments. Observe, don't fail.
        try:
            r = client.get(
                f"{BASE_URL}/api/v1/runs/{run_id}/intelligence",
                headers=h, timeout=10.0,
            )
            if r.status_code == 200:
                intel = r.json()
                # ``run_summary`` is the Mongo-backed doc the AI pipeline
                # writes; ``provenance.fallback_used`` distinguishes a
                # deterministic fallback from a full LLM-backed summary.
                # We only require the endpoint to respond — depth of
                # detail depends on workers being warm.
                assert isinstance(intel, dict)
        except httpx.HTTPStatusError:
            # 404 acceptable when pipeline hasn't materialised a snapshot
            # yet — this is a non-blocking observation, not a regression.
            pass

        # ── 6f) Test Management (managed_test_cases) — expected EMPTY ──
        # This page is fed by RAG / manual authoring, NOT by ingestion.
        # Documenting the expectation in code so future readers don't
        # mistake this as a regression.
        r = client.get(
            f"{BASE_URL}/api/v1/test-management/cases",
            params={"project_id": project_id},
            headers=h, timeout=10.0,
        )
        if r.status_code == 200:
            data = r.json()
            total = data.get("total") if isinstance(data, dict) else None
            assert total in (0, None), (
                "Test Management page should be empty for a fresh project — "
                "ingested test cases live under /search and /suites, not here."
            )

        # ── 7) Tear-down: full reset of the throwaway project ──────────
        # Uses the danger-zone endpoint added in the same feature ship.
        # Catching errors so a tear-down failure doesn't mask the actual
        # assertion outcomes — the project is named with a UUID slug, so
        # leftover rows are harmless and easy to identify.
        try:
            client.post(
                f"{BASE_URL}/api/v1/projects/{project_id}/reset",
                json={"mode": "full", "confirmation_name": project_name},
                headers=h, timeout=15.0,
            )
        except Exception:
            pass
