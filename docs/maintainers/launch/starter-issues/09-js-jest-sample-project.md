---
title: Add a Jest sample project under client/examples/js
labels: [good first issue, sdk, javascript]
---

## What

`client/examples/` has runnable sample projects for pytest and TestNG only. Its README says so directly: "The JavaScript and Go reporters have no sample project here" (`client/examples/README.md:18-19`). The user guide says the same: "Working examples for Python and Java live in client/examples/" (`user-guide/cli-sdk-mcp.md:106`). The JavaScript SDK ships a Jest reporter (`client/js/src/jest-reporter.ts`, documented at `client/js/README.md:32-51`).

## Why it helps

A copyable project is the fastest way for a Node team to see the reporter work. Today they have to assemble one from the README.

## Where to start

- The sample to mirror: `client/examples/python/pytest_smoke/` (a README, a pinned dependency file, a placeholder `testlookup.properties`, and a few tests including one that fails on purpose and one skipped).
- Installing the reporter from source: `client/js/README.md:7-24` (it is not on npm; build it, then install the packed tarball or a `file:` path).
- Jest options and behaviour: `client/js/README.md:32-51`. With no URL, credential or project, the reporter logs a warning and stays inactive, and the tests still run (line 49).
- Config file discovery for the Jest reporter: `client/js/README.md:125-137`.
- No CI wiring is needed. `client/examples/` is deliberately excluded from the CI test-suite and Dependabot guards (`scripts/quality_gate.py:5928` and `:6597`).

## Done when

- [ ] A new `client/examples/js/jest_smoke/` with `package.json` (Jest pinned to an exact version), `jest.config.js` registering `testlookup-reporter/jest-reporter`, a placeholder `testlookup.properties` like the pytest sample's (`qai_your_api_key_here`, `https://testlookup.example.com`), and 4–6 tests with one intentional failure and one skipped test.
- [ ] A `README.md` in that folder with the same sections as the pytest sample: what's in here, run it (build and install the reporter from `client/js`, then `npm test`), what you'll see, CI wiring.
- [ ] With no configuration (move the properties file away and unset `TESTLOOKUP_*`), `npm test` runs the tests and the reporter stays inactive.
- [ ] With a real server and key, the run shows on **Runs → Live**. This check needs a running TestLookup (for example `make dev`); say in the PR whether you did it.
- [ ] The table and the "no sample project" sentence in `client/examples/README.md` (lines 6-12 and 18-19), and `user-guide/cli-sdk-mcp.md:106`, mention the new sample.
- [ ] `python scripts/check_reference_docs.py` passes (it checks links in every `client/**/README.md`; install `backend/requirements.txt` first).
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

## Size

About half a day; no Docker stack needed for the sample itself. Node 18 or newer.
