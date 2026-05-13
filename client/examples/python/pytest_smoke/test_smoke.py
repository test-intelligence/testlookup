"""Smoke tests demonstrating live streaming to TestLookup via pytest.

Once `testlookup.properties` sits next to pytest.ini, the testlookup-reporter
pytest plugin auto-registers and streams every test result in real time.
This file has zero TestLookup imports — that's the point.
"""
import pytest
import time


def test_login_succeeds():
    time.sleep(0.05)
    assert 1 + 1 == 2


def test_logout_succeeds():
    time.sleep(0.03)
    assert "ok" == "ok"


def test_checkout_fails_intentionally():
    """Demonstrates a FAILED status reaching the live UI with stack trace."""
    time.sleep(0.08)
    expected = 200
    actual = 500
    assert expected == actual, f"expected {expected} got {actual}"


@pytest.mark.skip(reason="Pending feature flag rollout")
def test_payment_skipped():
    """Demonstrates a SKIPPED status — pytest reports it, plugin streams it."""
    pass


@pytest.mark.parametrize("n", [1, 2, 3])
def test_parametrized_pass(n):
    assert n > 0
