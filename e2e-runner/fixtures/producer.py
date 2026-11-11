"""Small upstream pytest project used to produce the release JUnit fixture."""

import pytest


def test_report_pass():
    assert 2 + 2 == 4


def test_report_failure():
    assert "observed" == "expected"


@pytest.mark.skip(reason="Fixture includes a deliberately skipped upstream test")
def test_report_skipped():
    pass
