---
title: Test that every file in samples/ is detected and parsed
labels: [good first issue, backend, samples, tests]
---

## What

`samples/README.md:11-17` lists five sample reports with their format, test count and failures. No test checks all of them. A few tests read single samples for timing checks (`backend/tests/services/test_report_parser_timings.py:95`, `:169`, `:217` and `backend/tests/services/test_report_timing_xml_parsers.py:165`), and no test reads `samples/junit/payment-suite.xml` at all.

This has gone wrong before. `backend/tests/routers/test_detect_format.py:93-97` records that the shipped Playwright sample was once auto-detected as Allure and "parsed to nothing".

## Why it helps

The samples are the first thing a new user uploads. A test makes sure each one still lands on the right parser and gives the counts the README promises. It also gives the next contributor who adds a sample one place to register it.

## Where to start

- Format detection: `_detect_format(filename, content)` at `backend/app/routers/ingest.py:654`. It is a pure function; `backend/tests/routers/test_detect_format.py` calls it directly.
- Parsing: `_parse_file_to_results(content, fmt, filename, run_id)` at `backend/app/worker/tasks.py:1889`. It dispatches through the `_REPORT_PARSERS` table at `backend/app/worker/tasks.py:1922` (Allure is handled separately inside `_parse_report`, `backend/app/worker/tasks.py:1936`). `backend/tests/regression/test_archive_upload.py:68-75` shows a test calling it.
- Each parsed result is a dict with a `"status"` key (`PASSED`, `FAILED`, `BROKEN`, `SKIPPED`, ...).
- Finding `samples/` from a test file: `Path(__file__).resolve().parents[3] / "samples"`, as in `backend/tests/services/test_report_parser_timings.py:25`.

## Done when

- [ ] A new `backend/tests/services/test_samples_parse.py` has one parametrized case per sample file: the path, the expected format, and the expected counts from the README table.
- [ ] Each case asserts that `_detect_format` returns the expected format and that the parsed results match the README's "Tests" and "Failures" columns.
- [ ] One more test lists every file under `samples/` except `README.md` and fails if a file has no case, so a new sample cannot be added without one.
- [ ] If a sample does not match its README row, do not change either one to make the test pass. Mark that case `xfail` with a short reason and describe the mismatch in the PR, so a maintainer can decide.
- [ ] From `backend/`: `python -m pytest tests/services/test_samples_parse.py -q -p no:testlookup` passes, and `ruff check app/ tests/` is clean.
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

## Size

About half a day; no Docker stack needed. Python 3.11 with `backend/requirements.txt` installed.
