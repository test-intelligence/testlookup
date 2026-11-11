from policy import evaluate


def case(priority, status, test_id):
    return {"priority": priority, "status": status, "id": test_id}


def test_missing_or_retried_p0_blocks():
    for state in ("BLOCKED", "SKIPPED", "FLAKY", "FAILED"):
        assert evaluate([case("P0", state, "a")], [], "regression")[0] == "NO_GO"


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
