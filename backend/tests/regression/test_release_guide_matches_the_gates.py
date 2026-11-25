"""The Releases guide states the gate behaviour the code has (batch 6, D-138).

- D-138: the guide said a band "can turn a GO into a CONDITIONAL GO", which named
  only the mildest case. ``classify_with_policy`` maps a pass rate below the
  orange lower bound to NO_GO, the orange band to CONDITIONAL, and yellow or
  green to GO; the guide's table must say the same.
- Release-level Conditional Go: ``release_rollup_service.decide`` and the defect
  criterion return only GO, NO_GO and NOT_EVALUATED, and the release gate ranks
  no conditional verdict. If one is ever added, this fails until the guide's
  release-list sentence and the concepts entry are updated too.
"""
from __future__ import annotations

import pathlib

import pytest

from app.services import release_gate_service
from app.services.metrics_service import classify_with_policy

REPO = pathlib.Path(__file__).resolve().parents[3]
GUIDE = REPO / "frontend" / "src" / "content" / "guide"

pytestmark = pytest.mark.skipif(not GUIDE.is_dir(), reason="frontend not present in this checkout")

BANDS = {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0}


def _verdict(pass_rate: float) -> str:
    return classify_with_policy(
        pass_rate=pass_rate, active_defects_p0=0, flaky_count=0, new_failures_24h=0,
        bands=BANDS, hard_caps={"max_p0_defects": 0},
    )["verdict"]


def _page(name: str) -> str:
    return (GUIDE / name).read_text(encoding="utf-8")


def test_band_table_in_the_guide_matches_the_classifier():
    assert _verdict(96.0) == "GO"  # yellow
    assert _verdict(92.0) == "CONDITIONAL"  # orange
    assert _verdict(80.0) == "NO_GO"  # below orange
    page = _page("releases.md")
    assert "| At or above the yellow lower bound | **GO** (the verdict is left as it is) |" in page
    assert "| From the orange lower bound up to the yellow one | **CONDITIONAL GO** |" in page
    assert "| Below the orange lower bound | **NO-GO** |" in page


def test_p0_hard_cap_forces_no_go_as_the_guide_says():
    out = classify_with_policy(
        pass_rate=100.0, active_defects_p0=1, flaky_count=0, new_failures_24h=0,
        bands=BANDS, hard_caps={"max_p0_defects": 0},
    )
    assert out["verdict"] == "NO_GO"
    assert "**Max P0 defects** hard cap also forces **NO-GO**" in _page("releases.md")


def test_release_gate_has_no_conditional_verdict_and_the_guide_agrees():
    assert set(release_gate_service._VERDICT_RANK) == {"GO", "NO_GO", "NOT_EVALUATED"}
    line = next(
        ln for ln in _page("releases.md").splitlines() if "each release's **recorded** gate verdict" in ln
    )
    assert "Conditional" not in line.split(".")[0]
    concepts = _page("concepts.md")
    assert "A release gate has no CONDITIONAL_GO" in concepts
    assert "producing GO / CONDITIONAL_GO / NO_GO" not in concepts
