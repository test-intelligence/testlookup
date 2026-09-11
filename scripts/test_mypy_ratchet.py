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


# QA-B45-P1: the parsed count must equal mypy's own "Found N errors".


def test_absolute_paths_under_the_root_become_baseline_relative(tmp_path: Path) -> None:
    absolute = str(tmp_path / "app" / "a.py")          # C:\...\app\a.py on Windows
    text = (f"{absolute}:1: error: x  [misc]\n{absolute}:2:3: error: y  [misc]\n"
            "Found 2 errors in 1 file (checked 1 source file)\n")
    assert mr.parse_mypy_output(text, 1, root=tmp_path) == Counter({"app/a.py": 2})


def test_a_windows_absolute_path_outside_the_root_is_counted_not_dropped(tmp_path: Path) -> None:
    # The QA reproduction: three errors, previously parsed as {} and a PASS.
    text = "C:\\r\\backend\\app\\a.py:1: error: x\n" * 3 + "Found 3 errors in 1 file\n"
    current = mr.parse_mypy_output(text, 1, root=tmp_path)
    assert sum(current.values()) == 3
    regressions, _ = mr.compare(current, {"app/a.py": 2, "app/b.py": 1})
    assert regressions, "errors under an unknown path must fail the ratchet"


def test_an_error_line_without_a_line_number_is_counted() -> None:
    text = "app/a.py: error: x  [misc]\napp/b.py:3: error: y\nFound 2 errors in 2 files\n"
    assert mr.parse_mypy_output(text, 1) == Counter({"app/a.py": 1, "app/b.py": 1})


@pytest.mark.parametrize("text", [
    "<string>:1: error: x\napp/a.py:1: error: y\nFound 2 errors in 2 files\n",   # 1 of 2 read
    "app/a.py:1: error: x\napp/a.py:2: error: y\napp/a.py:3: error: z\nFound 2 errors in 1 file\n",
    "app/a.py:1: error: x\nSuccess: no issues found in 1 source file\n",
    "Found 7 errors in 3 files\n",
])
def test_a_count_that_does_not_match_the_summary_fails_closed(text: str) -> None:
    with pytest.raises(mr.RatchetError, match="reported"):
        mr.parse_mypy_output(text, 1)


# QA-B45-P2: CI fails when the baseline has slack, so the fixing PR locks it in.


def test_check_stale_fails_on_a_count_below_the_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("3 app/a.py\n1 app/b.py\n", encoding="utf-8")
    log = tmp_path / "mypy.log"
    log.write_text(OUTPUT, encoding="utf-8")                # a.py 2, b.py 1
    args = ["--from-log", str(log), "--baseline", str(baseline)]
    assert mr.main(args) == 0                              # local: a hint only
    assert mr.main([*args, "--check-stale"]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "--update" in out and "app/a.py: 2 errors (baseline 3)" in out
    # After --update the same run passes the strict check.
    assert mr.main([*args, "--update"]) == 0
    assert mr.main([*args, "--check-stale"]) == 0


def test_check_stale_still_fails_a_regression(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("1 app/a.py\n1 app/b.py\n", encoding="utf-8")
    log = tmp_path / "mypy.log"
    log.write_text(OUTPUT, encoding="utf-8")
    assert mr.main(["--from-log", str(log), "--baseline", str(baseline), "--check-stale"]) == 1


def test_ci_runs_the_ratchet_in_strict_mode() -> None:
    ci = (mr.BACKEND.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count("run: python ../scripts/mypy_ratchet.py --check-stale\n") == 1
    assert "run: python ../scripts/mypy_ratchet.py\n" not in ci


def test_real_baseline_is_well_formed() -> None:
    """The committed baseline parses and names only files that exist."""
    baseline = mr.load_baseline()
    assert baseline, "backend/mypy-baseline.txt is missing or empty"
    for file, count in baseline.items():
        assert count > 0, file
        assert (mr.BACKEND / file).is_file(), f"baseline names a missing file: {file}"
