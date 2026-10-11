# pytest sample

Smallest possible pytest project that streams to TestLookup live.

## What's in here

```
pytest_smoke/
├── testlookup.properties   ← config; auto-detected by the plugin
├── pytest.ini              ← standard pytest config (no TestLookup hooks)
├── requirements.txt
└── test_smoke.py           ← regular pytest tests; no TestLookup imports
```

## Run it

1. **Generate an API key.** As an ADMIN, TestLookup → *Settings → Streaming
   API keys*. Copy the `testlookup.properties` content from the dialog that
   shows the new key and paste it over the placeholder file in this directory
   (it pre-fills `testlookup.endpoint`, `testlookup.api.key`,
   `testlookup.project`).

2. **Install:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run:**

   ```bash
   pytest
   ```

That's it. `test_smoke.py` has no TestLookup imports — the
`testlookup-reporter` package registers a pytest plugin that auto-activates
because it finds `testlookup.properties` in the cwd.

> **Known issue.** The plugin currently sends the API key from
> `testlookup.api.key` as `Authorization: Bearer`, and the server accepts API
> keys only in the `X-API-Key` header, so session creation fails with 401.
> Until that is fixed, run the plugin with a JWT access token
> (`TESTLOOKUP_TOKEN=<token> pytest`, token from `POST /api/v1/auth/login`)
> or use `LiveStream` as in `../livestream_script.py` and `../pytest_realistic`.
> Details: [client/README.md](../../../README.md#pytest-plugin).

## What you'll see

- **TestLookup → Runs → Live** — the run appears immediately under the
  launch label `Smoke suite` (from `testlookup.launch`). Pass/fail counts
  tick up as the parametrized tests stream in.
- **Runs / Overview / Coverage / Failures / Trends** — once pytest exits,
  the plugin closes the live session, the server finalises the run, and it
  lands on every other page within a few seconds (AI analysis enqueues
  asynchronously).

## CI wiring

Most CI runners want build / branch injected per-run. Override those
without touching the file:

```bash
TESTLOOKUP_BUILD=$BUILD_NUMBER \
TESTLOOKUP_BRANCH=$GIT_BRANCH \
TESTLOOKUP_LAUNCH="Smoke suite ($GIT_BRANCH)" \
pytest
```

These are the defaults of the plugin's `--testlookup-build`,
`--testlookup-branch` and `--testlookup-launch` options and win over the
properties file. The plugin does not send a commit SHA. The full key
reference and precedence rules are in
[client/README.md](../../../README.md#configuration).

## Programmatic alternative

If you're not on pytest (Robot, behave, custom runner), use the `LiveStream`
class directly — see `../livestream_script.py` in this examples folder.
