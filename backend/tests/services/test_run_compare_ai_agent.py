from app.agents.run_compare_agent import build_fallback_report


def test_fallback_report_uses_deterministic_diff_counts():
    payload = {
        "scope": "suite",
        "suite_name": "Checkout",
        "delta_pass_rate": -12.5,
        "new_failures": 2,
        "regressed": 1,
        "fixed": 3,
        "still_failing": 0,
        "duration_spikes": 1,
        "test_deltas": [
            {"classification": "new_failure", "test_name": "test_payment_declined"},
            {"classification": "fixed", "test_name": "test_cart_total"},
            {"classification": "duration_spike", "test_name": "test_order_history"},
        ],
    }

    report = build_fallback_report(payload)

    assert report["risk_level"] == "HIGH"
    assert "2 new failure(s)" in report["executive_summary"]
    assert "test_payment_declined" in report["new_risks"]
    assert "test_cart_total" in report["resolved_risks"]
    assert "test_order_history" in report["duration_concerns"]
    assert report["fallback_used"] is True
