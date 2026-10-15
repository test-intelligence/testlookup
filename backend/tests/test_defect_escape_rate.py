"""DEF-03 unit: the escape-rate payload (``analytics_service.build_escape_payload``) and its reasons.

Below the floor (5 attributed defects) the rate is ``None`` -- never ``0`` --
and the payload says why; the SQL half is pinned on PostgreSQL in
``tests/integration/test_defect_escape_rate_postgres.py``.
"""
from __future__ import annotations

import pytest

from app.services.analytics_service import (
    ESCAPE_UNATTRIBUTED_REASON,
    build_escape_payload,
    escape_rate_reason,
)
from app.services.defect_status import ESCAPE_RATE_MIN_SAMPLE

OUTCOMES = {"incidents": 2, "rollbacks": 1, "releases": 1}


def _payload(**kw):
    base = {"in_window": 12, "attributed": 12, "escaped": 5, "window_days": 30, "outcomes": OUTCOMES}
    base.update(kw)
    return build_escape_payload(**base)


def test_the_payload_has_exactly_the_contract_keys():
    assert set(_payload()) == {
        "rate", "escaped", "attributed", "in_window", "measured", "reason", "sample_floor", "window_days", "outcomes",
    }
    assert _payload()["outcomes"] == OUTCOMES


def test_a_measured_rate_is_a_percentage_with_one_decimal():
    payload = _payload()
    assert payload["measured"] is True and payload["reason"] is None
    assert payload["rate"] == 41.7  # 5 of 12
    assert payload["sample_floor"] == ESCAPE_RATE_MIN_SAMPLE == 5


def test_zero_escapes_measured_is_zero_percent():
    assert _payload(escaped=0)["rate"] == 0.0


@pytest.mark.parametrize("attributed", [0, 1, 4])
def test_below_the_floor_is_not_measured_and_never_zero(attributed):
    payload = _payload(attributed=attributed, escaped=0)
    assert payload["measured"] is False and payload["rate"] is None
    assert payload["reason"] == (
        f"{attributed} defect(s) reported in the last 30 days affect a release that has shipped; 5 are needed."
    )


def test_exactly_the_floor_is_measured():
    assert _payload(attributed=5, escaped=1)["rate"] == 20.0


def test_no_defect_in_the_window():
    payload = _payload(in_window=0, attributed=0, escaped=0, window_days=7)
    assert payload["reason"] == "No defects were reported in the last 7 days."
    assert escape_rate_reason(in_window=0, attributed=0, window_days=7) == payload["reason"]


def test_the_unattributed_sentinel_reason_wins():
    payload = _payload(attributed=0, escaped=0, reason=ESCAPE_UNATTRIBUTED_REASON)
    assert payload["measured"] is False and payload["rate"] is None
    assert payload["reason"] == 'Escapes are counted against shipped releases; "Unattributed" has none.'


def test_missing_outcome_counts_read_zero():
    assert _payload(outcomes={})["outcomes"] == {"incidents": 0, "rollbacks": 0, "releases": 0}
