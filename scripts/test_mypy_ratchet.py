"""Self-test for scripts/mypy_ratchet.py (re-audit M24).

The ratchet replaced ``continue-on-error: true`` on the mypy step, so a bug
here is the same bug as the one it fixed: a new type error that nobody sees.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mypy_ratchet as mr

OUTPUT = """\
app/a.py:3: error: Incompatible types in assignment  [assignment]
app/a.py:9:5: error: Name "x" is not defined  [name-defined]
app\\b.py:1: error: Missing return statement  [return]
app/a.py:4: note: This is a note, not an error
Found 3 errors in 2 files (checked 5 source files)
"""


def test_parse_counts_errors_per_file_and_ignores_notes() -> None:
    assert mr.parse_mypy_output(OUTPUT, 1) == Counter({"app/a.py": 2, "app/b.py": 1})


def test_parse_clean_run() -> None:
    assert mr.parse_mypy_output("Success: no issues found in 5 source files\n", 0) == Counter()


@pytest.mark.parametrize("text,code", [
    ("", 1),                                   # no summary: truncated / killed
    ("app/a.py:1: error: x  [misc]\n", 1),      # errors but no summary line
    ("Found 1 error in 1 file\n", 2),           # mypy crash / usage error
])
def test_parse_fails_closed(text: str, code: int) -> None:
    with pytest.raises(mr.RatchetError):
        mr.parse_mypy_output(text, code)


def test_compare_flags_increase_and_new_file_but_not_decrease() -> None:
    current = Counter({"app/a.py": 3, "app/b.py": 1, "app/new.py": 1})
    baseline = {"app/a.py": 2, "app/b.py": 4, "app/gone.py": 1}
    regressions, improvements = mr.compare(current, baseline)
    assert regressions == [
        "app/a.py: 3 errors (baseline 2)",
        "app/new.py: 1 errors (baseline 0)",
    ]
    assert improvements == [
        "app/b.py: 1 errors (baseline 4)",
        "app/gone.py: 0 errors (baseline 1)",
    ]


def test_moving_an_error_between_files_is_still_a_regression() -> None:
    # Same TOTAL, different distribution: a total-count ratchet would pass it.
    regressions, _ = mr.compare(Counter({"app/a.py": 1, "app/b.py": 1}),
                                {"app/a.py": 2})
    assert regressions == ["app/b.py: 1 errors (baseline 0)"]


def test_baseline_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.txt"
    assert mr.update_baseline(Counter({"app/a.py": 2, "app/b.py": 1}), {}, False, path) == []
    assert mr.load_baseline(path) == {"app/a.py": 2, "app/b.py": 1}
    assert b"\r" not in path.read_bytes()


def test_update_refuses_to_raise_a_count(tmp_path: Path) -> None:
    path = tmp_path / "baseline.txt"
    path.write_text("2 app/a.py\n", encoding="utf-8")
    before = path.read_bytes()
    raised = mr.update_baseline(Counter({"app/a.py": 3}), {"app/a.py": 2}, False, path)
    assert raised == ["app/a.py: 3 > 2"]
    assert path.read_bytes() == before
    assert mr.update_baseline(Counter({"app/a.py": 3}), {"app/a.py": 2}, True, path) == []
    assert mr.load_baseline(path) == {"app/a.py": 3}


def test_main_exit_codes(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("2 app/a.py\n1 app/b.py\n", encoding="utf-8")
    log = tmp_path / "mypy.log"
    log.write_text(OUTPUT, encoding="utf-8")
    assert mr.main(["--from-log", str(log), "--baseline", str(baseline)]) == 0
    baseline.write_text("1 app/a.py\n1 app/b.py\n", encoding="utf-8")
    assert mr.main(["--from-log", str(log), "--baseline", str(baseline)]) == 1
    log.write_text("", encoding="utf-8")
    assert mr.main(["--from-log", str(log), "--baseline", str(baseline)]) == 2


def test_real_baseline_is_well_formed() -> None:
    """The committed baseline parses and names only files that exist."""
    baseline = mr.load_baseline()
    assert baseline, "backend/mypy-baseline.txt is missing or empty"
    for file, count in baseline.items():
        assert count > 0, file
        assert (mr.BACKEND / file).is_file(), f"baseline names a missing file: {file}"
