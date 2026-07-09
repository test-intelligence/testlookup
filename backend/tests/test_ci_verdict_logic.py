"""Tests for the ``testlookup ci-verdict`` partition logic (US-5.2).

The pure logic lives in ``cli/testlookup_cli/verdict_logic.py`` — a
stdlib-only module loaded here by file path (same importlib convention as
``test_ci_context_detection.py``) so the backend suite, the only CI-run
location, covers the CLI package without installing it.

Also pins the client-side fingerprint formula against the backend's
``make_test_fingerprint`` so the two copies cannot drift silently.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERDICT_PATH = _REPO_ROOT / "cli" / "testlookup_cli" / "verdict_logic.py"


def _load_copy(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vl = _load_copy(_VERDICT_PATH, "_cli_verdict_logic")


def _failure(test_name: str, suite: str = "auth", cls: str | None = "com.acme.LoginTest"):
    return {
        "test_name": test_name,
        "suite_name": suite,
        "class_name": cls,
        "status": "FAILED",
    }


def _entry(test_name: str, suite: str = "auth", cls: str | None = "com.acme.LoginTest"):
    return {
        "fingerprint": vl.compute_fingerprint(test_name, cls),
        "test_name": test_name,
        "suite_name": suite,
        "class_name": cls,
        "status": "QUARANTINED",
    }


# ── Fingerprint parity with the backend ─────────────────────────────────────


def test_fingerprint_matches_backend_make_test_fingerprint():
    from app.services.ingestion import make_test_fingerprint

    for name, cls in (
        ("test_login", "com.acme.LoginTest"),
        ("test_login", None),
        ("test with spaces", "pkg.Cls"),
        ("тест_юникод", "пакет.Класс"),
    ):
        assert vl.compute_fingerprint(name, cls) == make_test_fingerprint(name, cls)


# ── Partitioning ────────────────────────────────────────────────────────────


def test_fingerprint_match_suppresses_failure():
    real, quarantined = vl.partition_failures(
        [_failure("test_login")], [_entry("test_login")]
    )
    assert real == []
    assert len(quarantined) == 1
    assert quarantined[0]["matched_by"] == "fingerprint"


def test_name_tuple_fallback_when_class_name_differs():
    # The report's class_name differs from the quarantine row's, so the
    # fingerprint misses — the (test_name, suite_name) tuple still matches.
    failure = _failure("test_login", suite="auth", cls="RenamedClass")
    real, quarantined = vl.partition_failures([failure], [_entry("test_login", suite="auth")])
    assert real == []
    assert quarantined[0]["matched_by"] == "name"


def test_name_fallback_requires_suite_match_too():
    failure = _failure("test_login", suite="checkout", cls="RenamedClass")
    real, quarantined = vl.partition_failures([failure], [_entry("test_login", suite="auth")])
    assert quarantined == []
    assert len(real) == 1


def test_server_supplied_fingerprint_takes_precedence_over_recompute():
    entry = _entry("test_login")
    failure = {**_failure("something_else", cls=None), "test_fingerprint": entry["fingerprint"]}
    real, quarantined = vl.partition_failures([failure], [entry])
    assert real == []
    assert quarantined[0]["matched_by"] == "fingerprint"


def test_empty_manifest_leaves_all_failures_real():
    failures = [_failure("test_a"), _failure("test_b")]
    real, quarantined = vl.partition_failures(failures, [])
    assert len(real) == 2
    assert quarantined == []


def test_mixed_partition():
    failures = [_failure("test_flaky"), _failure("test_real")]
    real, quarantined = vl.partition_failures(failures, [_entry("test_flaky")])
    assert [f["test_name"] for f in real] == ["test_real"]
    assert [f["test_name"] for f in quarantined] == ["test_flaky"]


# ── Exit-code contract ──────────────────────────────────────────────────────


def test_all_quarantined_exits_zero():
    assert vl.verdict_exit_code(real_count=0, quarantined_count=5) == vl.EXIT_PASS


def test_no_failures_exits_zero():
    assert vl.verdict_exit_code(0, 0) == vl.EXIT_PASS
    assert vl.verdict_exit_code(0, 0, strict=True) == vl.EXIT_PASS


def test_real_failure_exits_one():
    assert vl.verdict_exit_code(1, 0) == vl.EXIT_REAL_FAILURES
    assert vl.verdict_exit_code(1, 9) == vl.EXIT_REAL_FAILURES


def test_strict_blocks_on_quarantined_only_failures():
    assert vl.verdict_exit_code(0, 1, strict=True) == vl.EXIT_REAL_FAILURES
    assert vl.verdict_exit_code(0, 1, strict=False) == vl.EXIT_PASS


def test_infra_error_code_is_two():
    # ci-verdict maps API/network errors to a distinct code so CI can tell
    # "tests are red" from "TestLookup is down".
    assert vl.EXIT_INFRA_ERROR == 2


def test_build_verdict_document_shape():
    real, quarantined = vl.partition_failures(
        [_failure("test_flaky"), _failure("test_real")], [_entry("test_flaky")]
    )
    doc = vl.build_verdict(real, quarantined, strict=False)
    assert doc["verdict"] == "fail"
    assert doc["exit_code"] == vl.EXIT_REAL_FAILURES
    assert doc["counts"] == {
        "real_failures": 1,
        "quarantined_failures": 1,
        "total_failures": 2,
    }
    assert doc["real_failures"][0]["test_name"] == "test_real"

    doc_pass = vl.build_verdict([], quarantined, strict=False)
    assert doc_pass["verdict"] == "pass"
    assert doc_pass["exit_code"] == vl.EXIT_PASS
