"""The hard-cap field docs must describe what the classifier actually does.

JR-06. The release gate's hard caps treat ``0`` asymmetrically — and
deliberately. ``max_p0_defects=0`` means "none allowed" and blocks on the first
P0; ``max_flaky_count=0`` and ``max_new_failures_24h=0`` *disable* their caps,
because a literal zero would over-fire on real projects. That reasoning is
recorded in ``tests/test_classify_with_policy.py`` and is not in dispute here.

What was wrong is that the reasoning lived **only** in a test docstring, while
``PolicyHardCaps`` — a Pydantic model, therefore the OpenAPI documentation a
policy author reads — said two things the code does not do:

1. "``None`` (or 0 where ``ge=0``) disables the cap" — false for
   ``max_p0_defects``, where 0 is the *strictest* setting, not an off switch.
2. "exceeding it downgrades the resolved band by one step" — false. The
   implementation pins the band straight to red and forces NO_GO; the
   one-step behaviour was replaced precisely because a yellow band still
   mapped to GO, which made a "hard cap" advisory at best.

Both misread dangerously in a release gate. Under (1) an author sets
``max_p0_defects=0`` believing they have switched P0 blocking *off* and has in
fact switched it to maximum. Under (2) a breach reads as a nudge rather than a
block.

Measured before the fix::

    caps = {max_p0_defects: 0, max_flaky_count: 0, max_new_failures_24h: 0}
    100% pass, 25 flaky, 50 new failures -> green  GO     downgrades []
    100% pass, 1 P0                      -> red    NO_GO  downgrades [p0_defects:1>0]
    max_flaky_count=1, 25 flaky          -> red    NO_GO  downgrades [flaky:25>1]

Tightening the flaky cap from 1 to 0 turns enforcement **off**. The behaviour is
correct and intended; the documentation was not.

These tests assert the docs against the *live* behaviour rather than against a
fixed string, so the two cannot drift apart again without failing here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from app.models.schemas import PolicyHardCaps  # noqa: E402
from app.services.metrics_service import classify_with_policy  # noqa: E402

BANDS = {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0}


def _describe(field: str) -> str:
    return (PolicyHardCaps.model_fields[field].description or "").lower()


class TestTheDocsAndTheCodeAgreeAboutZero:
    def test_p0_zero_blocks_and_the_docs_say_so(self):
        result = classify_with_policy(
            pass_rate=100.0, active_defects_p0=1, flaky_count=0,
            new_failures_24h=0, bands=BANDS,
            hard_caps={"max_p0_defects": 0, "max_flaky_count": 0,
                       "max_new_failures_24h": 0},
        )
        # Behaviour: 0 is the strictest setting.
        assert result["verdict"] == "NO_GO"
        assert result["downgrades"] == ["p0_defects:1>0"]
        # Docs must not call that "disabled".
        text = _describe("max_p0_defects")
        assert "none" in text
        assert "cannot be disabled" in text

    def test_flaky_zero_disables_and_the_docs_say_so(self):
        result = classify_with_policy(
            pass_rate=100.0, active_defects_p0=0, flaky_count=9999,
            new_failures_24h=0, bands=BANDS,
            hard_caps={"max_p0_defects": 0, "max_flaky_count": 0,
                       "max_new_failures_24h": 0},
        )
        # Behaviour: 0 is an off switch.
        assert result["verdict"] == "GO"
        assert result["downgrades"] == []
        assert "disables this cap" in _describe("max_flaky_count")

    def test_new_failures_zero_disables_and_the_docs_say_so(self):
        result = classify_with_policy(
            pass_rate=100.0, active_defects_p0=0, flaky_count=0,
            new_failures_24h=9999, bands=BANDS,
            hard_caps={"max_p0_defects": 0, "max_flaky_count": 0,
                       "max_new_failures_24h": 0},
        )
        assert result["verdict"] == "GO"
        assert "disables this cap" in _describe("max_new_failures_24h")

    def test_the_docs_tell_you_how_to_actually_forbid_flakiness(self):
        # The trap is that the stricter-LOOKING value is the permissive one, so
        # the description has to name the value that does what the author meant.
        result = classify_with_policy(
            pass_rate=100.0, active_defects_p0=0, flaky_count=25,
            new_failures_24h=0, bands=BANDS,
            hard_caps={"max_p0_defects": 0, "max_flaky_count": 1,
                       "max_new_failures_24h": 0},
        )
        assert result["verdict"] == "NO_GO"
        assert "set 1" in _describe("max_flaky_count")


class TestABreachPinsToRedNotOneStep:
    def test_a_green_run_goes_straight_to_red(self):
        # Not green -> yellow. The old one-step wording made a hard cap
        # advisory, because yellow still mapped to GO.
        result = classify_with_policy(
            pass_rate=100.0, active_defects_p0=1, flaky_count=0,
            new_failures_24h=0, bands=BANDS,
            hard_caps={"max_p0_defects": 0, "max_flaky_count": 0,
                       "max_new_failures_24h": 0},
        )
        assert result["band"] == "red"
        assert result["verdict"] == "NO_GO"

    def test_the_class_docstring_no_longer_claims_one_step(self):
        doc = (PolicyHardCaps.__doc__ or "").lower()
        assert "one step" not in doc, (
            "the model still documents a one-step downgrade; a breach pins to red"
        )
        assert "red" in doc and "no_go" in doc

    def test_the_class_docstring_no_longer_claims_zero_always_disables(self):
        doc = (PolicyHardCaps.__doc__ or "").lower()
        # The old blanket sentence was "None (or 0 where ge=0) disables the cap",
        # which is false for max_p0_defects.
        assert "0 where" not in doc
        assert "not mean the same thing for every cap" in doc


class TestTheBandEdgesAreInclusive:
    """Measured while looking for an off-by-one; recorded because it is the
    question JR-06 asks and the answer is 'no defect'."""

    @pytest.mark.parametrize(
        "pass_rate,expected",
        [
            (99.0, "green"),    # green_min, inclusive
            (98.99, "yellow"),
            (95.0, "yellow"),   # yellow_min, inclusive
            (94.99, "orange"),
            (90.0, "orange"),   # orange_min, inclusive
            (89.99, "red"),
        ],
    )
    def test_each_min_is_inclusive_of_its_own_band(self, pass_rate, expected):
        result = classify_with_policy(
            pass_rate=pass_rate, active_defects_p0=0, flaky_count=0,
            new_failures_24h=0, bands=BANDS,
            hard_caps={"max_p0_defects": 0, "max_flaky_count": 0,
                       "max_new_failures_24h": 0},
        )
        assert result["band"] == expected
