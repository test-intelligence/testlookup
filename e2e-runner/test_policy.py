from policy import evaluate
from run import client_evidence, main
import json
import sys
import xml.etree.ElementTree as ET


def case(priority, status, test_id):
    return {"priority": priority, "status": status, "id": test_id}


def test_client_failure_report_keeps_only_safe_location_and_counts():
    failure = ET.Element("failure")
    failure.text = (
        "e2e-runner/live/test_data_journeys.py:162: in test_managed_case_lifecycle\n"
        "E   assert (1, 0, 0) == (1, 1, 1)\nAuthorization: Bearer synthetic-private-token"
    )
    assert client_evidence("test_managed_case_lifecycle", failure) == (
        "clients.xml · test_managed_case_lifecycle · e2e-runner/live/test_data_journeys.py:162"
        " · observed vs expected (1, 0, 0) / (1, 1, 1)"
    )


def test_missing_or_retried_p0_blocks():
    for state in ("BLOCKED", "SKIPPED", "FLAKY", "FAILED"):
        assert evaluate([case("P0", state, "a")], [], "regression")[0] == "NO_GO"


def test_unexecuted_native_cli_formats_are_independent_required_cases(
    monkeypatch, tmp_path
):
    directory = tmp_path / "report"
    monkeypatch.setattr(
        sys, "argv", ["run.py", "--tier", "regression", "--out", str(directory)]
    )
    monkeypatch.setenv("TL_TARGET", "ephemeral")
    monkeypatch.delenv("TL_MANIFEST", raising=False)
    assert main() == 1
    report = json.loads((directory / "results.json").read_bytes())
    formats = "junit testng nunit trx xunit pytest robot playwright cypress allure cucumber".split()
    entries = {row["id"]: row for row in report["cases"]}
    assert {key for key in entries if key.startswith("TL-CLI-003 ")} == {
        "TL-CLI-003 " + fmt for fmt in formats
    }
    for fmt in formats:
        row = entries["TL-CLI-003 " + fmt]
        assert (row["priority"], row["status"], row["layer"]) == (
            "P0",
            "BLOCKED",
            "CLI",
        )
        assert "TL-INGEST-001 " + fmt in entries


def test_infra_and_empty_evidence_block():
    assert evaluate([], [], "regression")[0] == "NO_GO"
    assert (
        evaluate([case("P0", "PASSED", "a")], ["missing shard"], "regression")[0]
        == "NO_GO"
    )


def test_required_priorities_and_real_waiver():
    green = [case("P0", "PASSED", "a"), case("P1", "PASSED", "b")]
    assert evaluate(green, [], "regression")[0] == "GO"
    assert evaluate(green, [], "smoke")[0] == "NO_GO"
    red = green + [case("P1", "FAILED", "c")]
    assert evaluate(red, [], "regression")[0] == "NO_GO"
    waiver = {
        "test_id": "c",
        "owner": "qa",
        "approved_by": "release lead",
        "defect_id": "TEST-1",
        "expires": "2099-01-01",
    }
    assert evaluate(red, [], "regression", [waiver])[0] == "CONDITIONAL_GO"
    assert (
        evaluate(red, [], "regression", [{**waiver, "expires": "2000-01-01"}])[0]
        == "NO_GO"
    )
