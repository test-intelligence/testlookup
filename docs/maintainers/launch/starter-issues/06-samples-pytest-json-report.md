---
title: Add a pytest-json-report sample to samples/
labels: [good first issue, samples]
---

## What

TestLookup parses `pytest --json-report` output (`"pytest"` in the parser table at `backend/app/worker/tasks.py:1927`, parser `parse_pytest_json` at `backend/app/services/pytest_parser.py:54`). `samples/README.md:19` says there is no sample for it. The `samples/` folder has JUnit, Allure, Cypress and Playwright only.

## Why it helps

Python teams can try an upload in their own format, and see the file shape before they wire up CI. CONTRIBUTING.md lists new sample files as PRs that land quickly.

## Where to start

- How the format is detected: `backend/app/routers/ingest.py:722-729`. The file must have top-level `"exitcode"` and `"root"` keys within its first 4 KB.
- How outcomes map to statuses: `_OUTCOME_MAP` at `backend/app/services/pytest_parser.py:44-51` (`error` becomes `BROKEN`, `xfailed` becomes `SKIPPED`).
- A shape reference: `backend/tests/fixtures/timing/pytest-json-report.json`. Write your own scenario; do not copy that file.
- The existing samples and their README rows: `samples/README.md:11-17`.

## Done when

- [ ] A new `samples/pytest/pytest-results.json` with 5–8 tests on one made-up feature, covering at least `passed`, `failed`, `skipped` and one `error` (setup error), with short synthetic tracebacks. No real names, hosts or company data (`samples/README.md:44`: samples are synthetic and CC0).
- [ ] A row for it in the table at `samples/README.md:11-17`, "pytest-json-report" removed from the "no samples" sentence at line 19, and "four of the formats" at line 3 updated.
- [ ] A backend test shows that `_detect_format` (`backend/app/routers/ingest.py:654`) returns `"pytest"` for the file, and that `parse_pytest_json` returns the number of tests and failures the README row states. If the "every sample parses" test from the starter issue `05-backend-samples-parse-test.md` has merged, add a case there instead.
- [ ] From `backend/`: `python -m pytest <your test file> -q -p no:testlookup` passes.
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

## Size

About 1–2 hours; no Docker stack needed. To see it in the UI as well, `make dev` and `testlookup upload file samples/pytest/pytest-results.json --project <project-uuid> --build demo-pytest` (optional).
