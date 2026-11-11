---
title: Add an NUnit 3 XML sample to samples/
labels: [good first issue, samples]
---

## What

TestLookup parses NUnit 3 result XML (`"nunit"` in the parser table at `backend/app/worker/tasks.py:1930`, parser `parse_nunit_xml` at `backend/app/services/nunit_parser.py:253`). `samples/README.md:19` says there is no sample for it. There is no sample for any .NET format today (NUnit 3, TRX or xUnit.net).

## Why it helps

.NET teams can try an upload with a file in their own format and see the expected shape. CONTRIBUTING.md lists new sample files as PRs that land quickly.

## Where to start

- How the format is detected: `backend/app/routers/ingest.py:674-678`. The root element `<test-run` is enough.
- How results map to statuses, from the module docstring at `backend/app/services/nunit_parser.py:30-45`: `result="Failed"` is `FAILED`; `Failed` with `label="Error"` is `BROKEN`; `Inconclusive` is `SKIPPED`. `duration` is in seconds. The suite name comes from the nearest `TestFixture` ancestor.
- A shape reference: `backend/tests/fixtures/timing/nunit3-cart.xml`. Write your own scenario; do not copy that file.
- The existing samples and their README rows: `samples/README.md:11-17`.

## Done when

- [ ] A new `samples/nunit/` XML file with 5–8 test cases in one or two fixtures on a made-up feature, covering at least passed, failed, an unexpected exception (`label="Error"`) and skipped or ignored. Failure messages and stack traces are short and synthetic (`samples/README.md:44`: samples are synthetic and CC0).
- [ ] A row for it in the table at `samples/README.md:11-17`, "NUnit 3 XML" removed from the "no samples" sentence at line 19, and "four of the formats" at line 3 updated.
- [ ] A backend test shows that `_detect_format` (`backend/app/routers/ingest.py:654`) returns `"nunit"` for the file, and that `parse_nunit_xml` returns the number of tests and failures the README row states. If the "every sample parses" test from the starter issue `05-backend-samples-parse-test.md` has merged, add a case there instead.
- [ ] From `backend/`: `python -m pytest <your test file> -q -p no:testlookup` passes.
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

The same steps work for a TRX or xUnit.net v2 sample. Please open a separate issue for each.

## Size

About 1–2 hours; no Docker stack needed.
