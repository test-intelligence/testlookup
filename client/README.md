# testlookup-reporter

Python client SDK for streaming test execution events to a TestLookup server.

## Install

```bash
# From the repo (local dev)
pip install -e ./client

# From PyPI (once published)
pip install testlookup-reporter
```

## Quick start

### LiveStream — recommended (API-key, no session ceremony)

```python
import asyncio
from testlookup_reporter import LiveStream

async def main():
    async with LiveStream(
        base_url="https://testlookup.local",
        api_key="qai_...",
        run_id="ci-build-42",
        launch_name="Smoke suite",
    ) as s:
        await s.record("test_login",  "PASSED", 120)
        await s.record("test_logout", "FAILED", 340, error="AssertionError")

asyncio.run(main())
```

The async context manager flushes on exit and posts a `run_complete` event so
the run finalises server-side and shows up on every TestLookup page (Runs,
Overview, Coverage, Failures, Trends).

## Replay-safe live identity

All bundled SDKs serialize a client-generated `batch_id` once before the first
POST and reuse it verbatim for transport and backpressure retries. The server
derives each event's canonical identity as SHA-256 of `session_id`, `batch_id`,
and its zero-based position in the batch, so event order is part of the wire
contract and cannot change on retry. Servers accept `batch_id` as optional so
older SDKs remain wire-compatible; new SDKs always send it so commit-before-ack
recovery can deduplicate ambiguous delivery without discarding distinct events.

### pytest plugin — auto-registers

Drop a `testlookup.properties` next to your `pytest.ini` and run `pytest` —
no `conftest.py` changes needed.

```
testlookup.endpoint=https://testlookup.local
testlookup.api.key=qai_...
testlookup.project=My Project
testlookup.launch=Smoke suite
```

The plugin entry point is registered via `pyproject.toml`'s
`[project.entry-points.pytest11]` so it activates as soon as the package is
installed.

## Config sources (precedence: highest first)

1. Constructor kwargs
2. JVM-style canonical env vars: `TESTLOOKUP_ENDPOINT`, `TESTLOOKUP_API_KEY`,
   `TESTLOOKUP_PROJECT`, `TESTLOOKUP_LAUNCH`, `TESTLOOKUP_BUILD`,
   `TESTLOOKUP_BRANCH`, `TESTLOOKUP_COMMIT`, `TESTLOOKUP_FRAMEWORK`
3. Legacy env vars: `TESTLOOKUP_URL`, `TESTLOOKUP_TOKEN`, `TESTLOOKUP_PROJECT_ID`
4. `testlookup.properties` (preferred — ReportPortal-compatible)
5. `testlookup.yaml` (legacy)
6. Built-in defaults

Files are searched in `./` → `./.testlookup/` → `~/.testlookup/`.

## Examples

Self-contained samples live under `client/examples/python/`:
- `pytest_smoke/` — minimal pytest project with auto-registering plugin
- `livestream_script.py` — programmatic `LiveStream` for non-pytest runners

## Documentation

Full integration guide: `docs/integration/pytest.md` in the repo.
