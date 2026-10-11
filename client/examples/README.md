# Client integration examples

Self-contained, runnable sample projects. Pick the one that matches your
test framework, replace the placeholder configuration, run it.

| Sample                              | Framework        | What it demonstrates                                          |
|-------------------------------------|------------------|---------------------------------------------------------------|
| [python/pytest_smoke](./python/pytest_smoke)         | pytest           | Auto-registering plugin via `testlookup.properties` (no imports in tests) |
| [python/livestream_script.py](./python/livestream_script.py) | none (custom)    | Programmatic `LiveStream` for non-pytest runners (Robot, behave, …) |
| [python/pytest_realistic](./python/pytest_realistic) | pytest           | Programmatic `LiveStream` examples for parallel suites and 100 rich test cases |
| [java/testng_smoke](./java/testng_smoke)             | TestNG (Maven)   | TestNG `<listener>` + classpath properties file              |
| [java/testng_realistic](./java/testng_realistic)     | TestNG (Maven)   | Programmatic client examples for parallel suites and 100 rich test cases |

All samples follow the same shape: install the reporter, put the
declarative `testlookup.properties` in place, register the framework hook,
run. The reporter documentation, including the full `testlookup.*` key
reference and precedence rules, is in [client/README.md](../README.md)
(Python) and [client/java/README.md](../java/README.md) (Java). The
JavaScript and Go reporters have no sample project here; see
[client/js/README.md](../js/README.md) and [client/go/README.md](../go/README.md).

## Common prerequisites

Every sample needs:

1. **A reachable TestLookup server.** The sample `testlookup.endpoint` is
   `https://testlookup.example.com`; change it to your server
   (`http://localhost:8000` for a local backend).
2. **A project-scoped API key with the `stream:write` scope.** An ADMIN
   mints it under *Settings → Streaming API keys* in the TestLookup UI. The
   dialog that shows the new key has a copy-pasteable `testlookup.properties`
   block that replaces the placeholder file in each sample.
3. **The reporter, installed from this repository.** The Python samples
   install it from `client/` through their `requirements.txt`. For the Java
   samples run `mvn -f client/java/pom.xml install` once from the repository
   root, so `io.testlookup:testlookup-reporter:1.0.0` is in your local Maven
   repository.
4. **The active project picker set to the project the key is bound to.**
   The API key carries the project, but the UI shows runs scoped to the
   selected project.

The pytest plugin used by `python/pytest_smoke` currently sends an API key
as a bearer token, which the server refuses (see the known issue in
[client/README.md](../README.md#pytest-plugin)); the `LiveStream`-based
Python samples and the Java samples send it correctly.

## What to expect when you run

For every sample, immediately after launching the test command:

- The run appears under **Runs → Live**. Pass / fail / skip counters tick
  up live. Its launch label comes from `testlookup.launch` for the Python
  samples; the TestNG listener takes the launch label only from
  `-Dtestlookup.launch` or `TESTLOOKUP_LAUNCH` (a `testlookup.launch` in the
  properties file becomes the run's suite name instead).

After the test command exits:

- The session finalises (the reporter closes the session, or posts a
  `run_complete` event for `LiveStream`).
- Within a few seconds the run lands on **Runs**, **Overview**, **Coverage**,
  **Failures**, and **Trends**.
- The AI analysis pipeline kicks off asynchronously; defects may appear on
  **Defects** a minute or two later.

## Troubleshooting

- **"401 Unauthorized" on the first batch**: the API key is wrong, expired,
  or was sent in the wrong header. Re-generate it, and check the known issue
  above if you use the pytest plugin.
- **"403: API key lacks the 'stream:write' scope"**: the key needs that
  scope (or no scopes at all, which means full access).
- **"Streaming requires a project-scoped API key"**: the key is not bound to
  a project. Only an ADMIN can mint project-scoped keys; ask one to create a
  key for your project.
- **Run shows on Runs → Live but not in Runs / Overview**: the run was not
  finalised. The Python and Java reporters do it automatically on
  context-manager exit or suite finish; if you build raw JSON requests, send
  `{"event_type": "run_complete"}` in the final batch.
