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

1. **Generate an API key.** TestLookup → *Settings → API Keys →
   Generate streaming key*. Copy the `testlookup.properties` content from the
   modal and paste it over the placeholder file in this directory (the
   modal pre-fills `testlookup.endpoint`, `testlookup.api.key`,
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

## What you'll see

- **TestLookup → Live Execution** — the run appears immediately under the
  launch label `Smoke suite` (from `testlookup.launch`). Pass/fail counts
  tick up as the parametrized tests stream in.
- **Runs / Overview / Coverage / Failures / Trends** — once pytest exits,
  the plugin sends a `run_complete` event, the server finalises the session,
  and the run lands on every other page within a few seconds (AI analysis
  enqueues asynchronously).

## CI wiring

Most CI runners want build / branch / commit injected per-run. Override
those without touching the file:

```bash
TESTLOOKUP_BUILD=$BUILD_NUMBER \
TESTLOOKUP_BRANCH=$GIT_BRANCH \
TESTLOOKUP_COMMIT=$GIT_COMMIT \
TESTLOOKUP_LAUNCH="Smoke suite ($GIT_BRANCH)" \
pytest
```

The `TESTLOOKUP_*` env vars override matching `testlookup.*` keys in the
properties file. See `docs/integration/pytest.md` for the full precedence
chain.

## Programmatic alternative

If you're not on pytest (Robot, behave, custom runner), use the `LiveStream`
class directly — see `../livestream_script.py` in this examples folder.
