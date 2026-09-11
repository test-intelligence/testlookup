#!/usr/bin/env python3
"""mypy as a ratchet: the per-file error count may only go down (re-audit M24).

``mypy app/`` reported 391 errors in 117 files on 2026-09-11 (mypy 2.3.1, the
version pinned in ``backend/requirements.txt``). Fixing all of them in one change
is not a reviewable diff, and ``continue-on-error: true`` meant a NEW type error
was invisible. This script makes mypy blocking without a flag day:

* ``backend/mypy-baseline.txt`` lists ``<count> <path>`` per file that has errors
  today;
* a file whose count goes UP, or a file not in the baseline that gains an error,
  fails the build;
* a file whose count went DOWN prints a hint to tighten the baseline
  (``--update``), which can only lower counts unless ``--allow-increase`` is
  given explicitly. With ``--check-stale`` (what CI runs) it FAILS instead, so
  the PR that fixes errors must commit the lowered baseline; otherwise the
  slack would let a later PR bring the fixed errors back.

Fails closed: if mypy crashed (exit code 2), printed no summary line, or the
error lines parsed do not add up to its ``Found N errors``, the run is an
error, never "0 errors".

Usage (from ``backend/``)::

    python ../scripts/mypy_ratchet.py            # run mypy, compare
    python ../scripts/mypy_ratchet.py --check-stale   # CI: also fail on slack
    python ../scripts/mypy_ratchet.py --update   # lower the baseline after fixes
    python ../scripts/mypy_ratchet.py --from-log mypy.log   # compare a saved run
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
BASELINE = BACKEND / "mypy-baseline.txt"
# ``--platform linux``: CI runs on ubuntu, and ``sys.platform`` branches change
# what mypy checks. Pinning it makes a Windows or macOS run count exactly what
# CI counts, so the baseline means the same number everywhere.
MYPY_ARGS = ["app/", "--ignore-missing-imports", "--platform", "linux"]

# ``app/x.py:12: error: ...`` / ``app\x.py:12:5: error: ...`` (Windows, columns)
# / ``C:\r\backend\app\x.py:12: error:`` (absolute) / ``app/x.py: error:`` (no line)
_ERROR_LINE = re.compile(
    r"^(?P<path>(?:[A-Za-z]:)?[^:\s][^:]*?\.pyi?)(?::\d+(?::\d+)?)?: error: "
)
_SUMMARY = re.compile(r"^(?:Found (?P<n>\d+) errors? in \d+ files?|Success: no issues found)")


class RatchetError(Exception):
    """mypy output could not be trusted — never read as a pass."""


def normalise_path(path: str, root: Path = BACKEND) -> str:
    """Repo-relative (to ``backend/``) POSIX path of an error line's file.

    ``show_absolute_path`` prints ``C:\\...\\backend\\app\\x.py``; the baseline
    says ``app/x.py``. An absolute path outside ``root`` stays absolute, so it
    is a file the baseline does not know — a regression, never a pass.
    """
    posix = path.replace("\\", "/")
    if not (posix.startswith("/") or re.match(r"^[A-Za-z]:/", posix)):
        return posix
    base = root.resolve().as_posix().rstrip("/") + "/"
    windows = bool(re.match(r"^[A-Za-z]:/", base))
    if (posix.lower() if windows else posix).startswith(base.lower() if windows else base):
        return posix[len(base):]
    return posix


def parse_mypy_output(text: str, exit_code: int | None = None, root: Path = BACKEND) -> Counter[str]:
    """Per-file error counts from mypy's default output format.

    The error lines counted must add up to the N in mypy's own ``Found N
    errors`` (QA-B45-P1): an error line this parser cannot read would
    otherwise vanish, and a run reporting errors would read as clean.
    """
    if exit_code is not None and exit_code not in (0, 1):
        raise RatchetError(f"mypy exited {exit_code} (crash or usage error)")
    lines = text.splitlines()
    summaries = [m for m in (_SUMMARY.match(line) for line in lines) if m]
    if not summaries:
        raise RatchetError("mypy printed no summary line; refusing to treat it as clean")
    reported = int(summaries[-1].group("n") or 0)
    counts: Counter[str] = Counter()
    for line in lines:
        match = _ERROR_LINE.match(line)
        if match:
            counts[normalise_path(match.group("path"), root)] += 1
    parsed = sum(counts.values())
    if parsed != reported:
        raise RatchetError(
            f"mypy reported {reported} errors but {parsed} error lines were parsed; "
            "refusing to compare an incomplete count"
        )
    return counts


def load_baseline(path: Path = BASELINE) -> dict[str, int]:
    baseline: dict[str, int] = {}
    if not path.exists():
        return baseline
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        count, file = line.split(maxsplit=1)
        baseline[file] = int(count)
    return baseline


def compare(current: Counter[str], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    """``(regressions, improvements)`` as human-readable lines."""
    regressions: list[str] = []
    improvements: list[str] = []
    for file in sorted(set(current) | set(baseline)):
        now, allowed = current.get(file, 0), baseline.get(file, 0)
        if now > allowed:
            regressions.append(f"{file}: {now} errors (baseline {allowed})")
        elif now < allowed:
            improvements.append(f"{file}: {now} errors (baseline {allowed})")
    return regressions, improvements


def render_baseline(counts: dict[str, int]) -> str:
    header = (
        "# mypy error ratchet (re-audit M24). Generated by scripts/mypy_ratchet.py --update.\n"
        "# <count> <path>. Counts may only go DOWN; see the script docstring.\n"
    )
    body = "".join(f"{counts[f]} {f}\n" for f in sorted(counts) if counts[f] > 0)
    return header + body


def update_baseline(current: Counter[str], baseline: dict[str, int], allow_increase: bool,
                    path: Path = BASELINE) -> list[str]:
    """Write the new baseline. Returns the lines that would have raised a count
    (and refuses to write when there are any, unless ``allow_increase``)."""
    raised = [
        f"{f}: {current[f]} > {baseline.get(f, 0)}"
        for f in sorted(current)
        if current[f] > baseline.get(f, 0)
    ]
    if raised and not allow_increase and baseline:
        return raised
    path.write_bytes(render_baseline(dict(current)).encode("utf-8"))
    return []


def run_mypy() -> tuple[str, int]:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", *MYPY_ARGS],
        cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,  # exit 1 = "errors found"; parse_mypy_output judges the code
    )
    return proc.stdout + proc.stderr, proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-log", type=Path, help="compare a saved mypy output instead of running mypy")
    parser.add_argument("--update", action="store_true", help="rewrite the baseline from this run")
    parser.add_argument("--allow-increase", action="store_true", help="let --update raise a count")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--check-stale", action="store_true",
                        help="also fail when a count is BELOW the baseline (CI): the "
                             "PR that fixes errors must lock the improvement in")
    args = parser.parse_args(argv)

    if args.from_log:
        text, code = args.from_log.read_text(encoding="utf-8", errors="replace"), None
    else:
        text, code = run_mypy()
    try:
        current = parse_mypy_output(text, code)
    except RatchetError as exc:
        print(text[-4000:])
        print(f"::error::mypy ratchet: {exc}")
        return 2

    baseline = load_baseline(args.baseline)
    total, allowed = sum(current.values()), sum(baseline.values())

    if args.update:
        raised = update_baseline(current, baseline, args.allow_increase, args.baseline)
        if raised:
            print("refusing to RAISE baseline counts without --allow-increase:")
            print("\n".join(f"  {line}" for line in raised))
            return 1
        print(f"baseline written: {total} errors in {len(current)} files")
        return 0

    regressions, improvements = compare(current, baseline)
    print(f"mypy: {total} errors in {len(current)} files (baseline {allowed})")
    if improvements:
        prefix = "::error::" if args.check_stale else ""
        print(f"{prefix}fewer errors than the baseline allows; tighten it with "
              "`python ../scripts/mypy_ratchet.py --update` (from backend/) and "
              "commit backend/mypy-baseline.txt in this PR:")
        print("\n".join(f"  {line}" for line in improvements))
    if regressions:
        # Show the offending errors themselves, not only the counts.
        bad = {line.split(":", 1)[0] for line in regressions}
        for line in text.splitlines():
            match = _ERROR_LINE.match(line)
            if match and normalise_path(match.group("path")) in bad:
                print(line)
        print("::error::new mypy errors (count went up):")
        print("\n".join(f"  {line}" for line in regressions))
        return 1
    if improvements and args.check_stale:
        # QA-B45-P2: a lowered count left in the baseline is slack a later PR
        # can spend -- the fixed errors could come back and still pass.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
