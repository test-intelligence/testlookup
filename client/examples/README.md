# Client integration examples

Self-contained, runnable sample projects. Pick the one that matches your
test framework, replace the placeholder API key, run it.

| Sample                              | Framework        | What it demonstrates                                          |
|-------------------------------------|------------------|---------------------------------------------------------------|
| [python/pytest_smoke](./python/pytest_smoke)         | pytest           | Auto-registering plugin via `testlookup.properties` (no imports in tests) |
| [python/livestream_script.py](./python/livestream_script.py) | none (custom)    | Programmatic `LiveStream` for non-pytest runners (Robot, behave, …) |
| [java/testng_smoke](./java/testng_smoke)             | TestNG (Maven)   | TestNG `<listener>` + classpath properties file              |

All samples follow the same shape — drop-in dependency, declarative
`testlookup.properties`, register the framework hook, run. The full
five-step contract and the canonical `testlookup.*` key reference live in
`../../docs/integration/`.

## Common prerequisites

Every sample needs:

1. **A reachable TestLookup server.** Sample `testlookup.endpoint` is
   `https://testlookup.local`; change it for staging / prod.
2. **A project-scoped API key with the `stream:write` scope.** Mint it from
   *Settings → API Keys → Generate streaming key* in the TestLookup UI. The
   modal has a copy-pasteable `testlookup.properties` block that replaces
   the placeholder file in each sample.
3. **The active project picker set to the project the key is bound to** —
   the API key carries the `project_id`, but the UI shows runs scoped to
   the selected project.

## What to expect when you run

For every sample, immediately after launching the test command:

- The run appears under **Live Execution** with the launch name from
  `testlookup.launch`. Pass / fail / skip counters tick up live.

After the test command exits:

- The session finalises (the SDK posts a `run_complete` event automatically).
- Within a few seconds the run lands on **Runs**, **Overview**, **Coverage**,
  **Failures**, and **Trends**.
- The AI analysis pipeline kicks off asynchronously — defects may appear on
  **Defects** a minute or two later.

## Troubleshooting

- **"401 Unauthorized" on the first batch** — the API key is wrong, expired,
  or missing the `stream:write` scope. Re-generate it.
- **"403: API key lacks the 'stream:write' scope"** — same fix; the scope
  must be selected (or absent — empty scopes mean full access).
- **"Streaming requires a project-scoped API key"** — the key was minted as
  user-scoped. Project-scoped keys can only be created by an Administrator
  via the UI; ask one to mint one for your project.
- **Run shows in Live Execution but not in Runs / Overview** — the
  `run_complete` event didn't reach the server. The Python and Java SDKs
  send it automatically on context-manager exit / suite finish; if you
  build raw JSON requests, send `{"event_type": "run_complete"}` in the
  final batch.
