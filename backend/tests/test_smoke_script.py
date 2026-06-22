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
