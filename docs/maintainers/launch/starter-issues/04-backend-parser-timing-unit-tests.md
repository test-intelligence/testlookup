---
title: Add unit tests for set_case_timing and epoch_ms
labels: [good first issue, backend, tests]
---

## What

`backend/app/services/parser_timing.py` has two small pure helpers that five report parsers share: `set_case_timing()` (lines 20-54) and `epoch_ms()` (lines 57-62). They are imported by `allure_parser.py:22`, `cucumber_parser.py:82`, `playwright_parser.py:78`, `pytest_parser.py:38` and `robot_parser.py:78` (all under `backend/app/services/`). No test imports `parser_timing` directly. Today they are only exercised through whole-report parser tests.

## Why it helps

The rules in these helpers are easy to break without noticing. Examples: a key with no value must be left out, never stored as `None`, and a boolean is not a worker id. Direct tests make those rules explicit and give a fast failure.

## Where to start

- `backend/app/services/parser_timing.py:20-62`.
- The constants it uses are in `backend/app/services/report_timings.py`: `WORKER_ID_MAX_CHARS = 64` (line 139), `CASE_TIMING_SOURCE_KEY` (line 129), `TIMING_SOURCE_REPORT` / `TIMING_SOURCE_DERIVED` (lines 28 and 30). `epoch_ms()` relies on `to_utc()` (lines 53-73) and `_from_epoch_ms()` (lines 76-83). The latter scales 10-digit second epochs to milliseconds and refuses numeric epochs before 2000.
- Test to copy: the "shared helpers" section of `backend/tests/services/test_report_timing_xml_parsers.py` (from line 376). It tests the sibling helpers in `report_timings` the same way.

## Done when

- [ ] A new `backend/tests/services/test_parser_timing.py` covers `set_case_timing()`:
  - a key whose value is `None` is not added;
  - `timing_source` is set only when at least one key was added, and takes the `source` argument (`"derived"`);
  - `worker_id=True` is ignored; a whitespace-only worker id is ignored; a worker id longer than 64 characters is cut to 64; a worker id of `0` is kept as `"0"`;
  - the function returns the same dict it was given.
- [ ] It covers `epoch_ms()`: `None` and unreadable text give `None`; an ISO string ending in `Z`, a naive `datetime` (read as UTC) and a 10-digit second epoch give the expected millisecond value; a numeric epoch before the year 2000 (for example `1000`) gives `None`. (The 2000 floor applies to numeric epochs only; `to_utc()` does not bound a `datetime` or ISO string.)
- [ ] From `backend/`: `python -m pytest tests/services/test_parser_timing.py -q -p no:testlookup` passes. (`-p no:testlookup` turns off the TestLookup pytest reporter in case you have the client SDK installed.)
- [ ] `ruff check app/ tests/` from `backend/` is clean.
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

No production code changes are needed. If a test shows a real bug, describe it in the PR and stop there; do not change the helper in the same PR.

## Size

About 1–2 hours; no Docker stack needed. Python 3.11 with `backend/requirements.txt` installed.
