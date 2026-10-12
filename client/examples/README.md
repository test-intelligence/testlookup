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

## What to expect when you run

For every sample, immediately after launching the test command:

- The run appears under **Runs → Live**. Pass / fail / skip counters tick
  up live. Its launch label (and, unless `testlookup.suite` is set, its
  suite name) comes from `testlookup.launch`.

After the test command exits:

- The session finalises (the reporter closes the session, or posts a
  `run_complete` event for `LiveStream`).
- Within a few seconds the run lands on **Runs**, **Overview**, **Coverage**,
  **Failures**, and **Trends**.
- The AI analysis pipeline kicks off asynchronously; defects may appear on
  **Defects** a minute or two later.

## Troubleshooting

- **"401 Unauthorized" on the first batch**: the API key is wrong, revoked or
  expired. Re-generate it. (The reporters send a `qai_` key as `X-API-Key`;
  a hand-written request must too.)
- **"403: API key lacks the 'stream:write' scope"**: the key needs that
  scope (or no scopes at all, which means full access).
- **"Streaming requires a project-scoped API key"**: the key is not bound to
  a project. Only an ADMIN can mint project-scoped keys; ask one to create a
  key for your project.
- **Run shows on Runs → Live but not in Runs / Overview**: the run was not
  finalised. The reporters do it automatically when the run ends: the
  session-based reporters (the pytest plugin, `TestLookupReporter`, Java, JS)
  close the session with `DELETE /api/v1/stream/sessions/{id}`, and
  `LiveStream` sends a `run_complete` event. If you build raw JSON requests
  against `/api/v1/stream/ingest`, send `{"event_type": "run_complete"}` in
  the final batch.
