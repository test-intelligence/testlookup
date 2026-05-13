"""Unit tests for compute_suite_attribution — the pure helper that picks the
primary suite and the sorted distinct list from grouped (suite_name, count) rows."""
from app.services.ingestion import compute_suite_attribution


def test_empty_input_returns_none_pair():
    assert compute_suite_attribution([]) == (None, None)


def test_drops_null_and_empty_suite_names():
    primary, names = compute_suite_attribution(
        [(None, 5), ("", 3), ("Smoke", 10)],
    )
    assert primary == "Smoke"
    assert names == ["Smoke"]


def test_returns_only_nones_when_every_row_is_dropped():
    assert compute_suite_attribution([(None, 9), ("", 1)]) == (None, None)


def test_primary_is_highest_count():
    primary, names = compute_suite_attribution(
        [("Regression", 5), ("Smoke", 12), ("Integration", 3)],
    )
    assert primary == "Smoke"
    assert names == ["Integration", "Regression", "Smoke"]


def test_tie_broken_alphabetically():
    # Both suites have 7 cases; "Acceptance" wins on alphabetical order.
    primary, names = compute_suite_attribution(
        [("Smoke", 7), ("Acceptance", 7)],
    )
    assert primary == "Acceptance"
    assert names == ["Acceptance", "Smoke"]


def test_names_are_deduped_and_sorted():
    # Defensive: same suite appears twice (shouldn't from GROUP BY,
    # but the helper should still emit a clean list).
    primary, names = compute_suite_attribution(
        [("Smoke", 5), ("Smoke", 2), ("Auth", 3)],
    )
    assert names == ["Auth", "Smoke"]
    # Primary is the most-frequent unique row regardless of dedup —
    # in this case Smoke wins on raw count 5 vs Auth 3.
    assert primary == "Smoke"


def test_single_suite_passes_through():
    primary, names = compute_suite_attribution([("OnlyOne", 42)])
    assert primary == "OnlyOne"
    assert names == ["OnlyOne"]
