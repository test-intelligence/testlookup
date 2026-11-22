---
name: testlookup-sdk-quality
description: Review and improve TestLookup SDK and reporter tests. Use for Python, JavaScript, Java, Go, framework plugins, CI metadata, live producer behavior, and cross-language wire parity.
---

# TestLookup SDK and Reporter Quality

## Trace producer behavior

- Read `client/README.md`, the language package under `client`, `sdk-vectors/run_context.json`, and the corresponding backend ingest or stream contract. Identify the package version, framework lifecycle point, config precedence, project identity and wire fields.
- Verify CI source identity, timestamps and durations, attempt counts, terminal outcomes and cleanup when delivery fails. For live reporters, check buffering and the final flush under the current retry contract. Verify the framework's own exit status when reporter delivery fails and match it to the package's documented behavior.
- Where the supported SDKs share a contract, verify the relevant vectors across languages. For HTTP capture use `sdk-vectors/http_exchange.json` to derive expected redaction and omission behavior in synthetic fixtures.

## Verify packages and real producers

- Follow the SDK jobs in `.github/workflows/ci.yml`. Use `python -m pytest client/tests` for Python, `npm test` under `client/js`, `mvn test` under `client/java`, and `go test ./...` under `client/go` where applicable. Exercise the artifact consumed by the runner when package metadata or registration changes.
- Use `e2e-runner/reporters` and `e2e-runner/live/test_sdk_frameworks.py` for a native producer loaded into a real framework. Compare producer facts with receipt completion, persisted test names/statuses, counts, project and build. Treat a fixture's deliberate failing leaf as data for the test.
- Report language, framework/version, package, wire contract and durable evidence. Update the shared fixtures and release inventory when introducing a new reporter or observable contract.
