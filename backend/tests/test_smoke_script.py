"""Adoption regression — the `scripts/smoke.py` decision logic.

Pins the pure `summarize()` core (CI can't run a live stack): core-check
failures fail the smoke and exit non-zero; informational checks never do.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SMOKE = Path(__file__).resolve().parents[2] / "scripts" / "smoke.py"
_spec = importlib.util.spec_from_file_location("tl_smoke", _SMOKE)
smoke = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can resolve cls.__module__ via sys.modules.
sys.modules["tl_smoke"] = smoke
_spec.loader.exec_module(smoke)

Check = smoke.Check
summarize = smoke.summarize


def test_all_core_ok_passes():
    ok, text = summarize([
        Check("readiness", True, True),
        Check("liveness", True, True),
        Check("api", True, True),
        Check("frontend", True, True),
    ])
    assert ok is True
    assert "passed" in text.lower()
    assert text.count("[PASS]") == 4


def test_a_core_failure_fails():
    ok, text = summarize([
        Check("readiness", True, True),
        Check("frontend", False, True, "ConnectionRefusedError"),
    ])
    assert ok is False
    assert "FAILED" in text
    assert "[FAIL] frontend" in text
    assert "ConnectionRefusedError" in text


def test_informational_failure_does_not_fail_smoke():
    ok, text = summarize([
        Check("readiness", True, True),
        Check("liveness", True, True),
        Check("api", True, True),
        Check("frontend", True, True),
        Check("demo data", False, False, "0 projects"),  # info-only
    ])
    assert ok is True
    assert "[info] demo data" in text


def test_empty_checks_is_vacuously_ok():
    ok, _ = summarize([])
    assert ok is True


def test_probe_handles_unreachable_host_without_raising():
    # Unroutable port — must return (0, '', err), never raise.
    status, body, err = smoke.probe("http://127.0.0.1:9/__nope__")
    assert status == 0 and body == "" and err


# ── The checks themselves, against a faked stack ─────────────────────────────
#
# /health/details needs a sign-in, and the smoke check holds no credential: it
# reads the per-dependency status from the public readiness probe instead.

_READY = '{"status": "ready", "checks": {"postgres": {"status": "ok"}, "mongo": {"status": "ok"}, "redis": {"status": "ok"}}}'
_NOT_READY = (
    '{"status": "not_ready", "checks": {"postgres": {"status": "ok"}, "mongo": {"status": "ok"}, '
    '"redis": {"status": "error", "detail": "check failed; see the server log"}}}'
)


def _fake_stack(monkeypatch, answers: dict[str, tuple[int, str, str]]) -> list[str]:
    asked: list[str] = []

    def _probe(url: str, *, method: str = "GET"):
        asked.append(url)
        for suffix, answer in answers.items():
            if url.endswith(suffix):
                return answer
        return 200, "<!doctype html><div id=root></div>", ""

    monkeypatch.setattr(smoke, "probe", _probe)
    return asked


def test_the_checks_call_only_routes_that_need_no_sign_in(monkeypatch):
    asked = _fake_stack(monkeypatch, {"/health/ready": (200, _READY, "")})
    checks = smoke.run_checks()
    assert not [u for u in asked if "/health/details" in u or "/health/ingestion" in u]
    ok, text = summarize(checks)
    assert ok is True
    assert "[info] dependency details -- postgres=ok, mongo=ok, redis=ok" in text


def test_a_failed_readiness_names_the_dependency(monkeypatch):
    _fake_stack(monkeypatch, {"/health/ready": (503, _NOT_READY, "HTTP 503")})
    ok, text = summarize(smoke.run_checks())
    assert ok is False
    assert "[FAIL] backend readiness (/health/ready)" in text
    assert "redis=error" in text


def test_a_turned_off_api_reference_is_reported_not_failed(monkeypatch):
    _fake_stack(monkeypatch, {
        "/health/ready": (200, _READY, ""),
        "/api-docs/openapi.json": (404, "", "HTTP 404"),
    })
    ok, text = summarize(smoke.run_checks())
    assert ok is True
    assert "[info] API schema (/api-docs/openapi.json)" in text and "API_DOCS_ENABLED" in text


def test_a_broken_api_reference_still_fails(monkeypatch):
    _fake_stack(monkeypatch, {
        "/health/ready": (200, _READY, ""),
        "/api-docs/openapi.json": (500, "", "HTTP 500"),
    })
    ok, text = summarize(smoke.run_checks())
    assert ok is False
    assert "[FAIL] API schema (/api-docs/openapi.json)" in text
