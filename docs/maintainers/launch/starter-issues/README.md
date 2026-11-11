# Starter issue drafts

These are drafts for a maintainer to create by hand on GitHub; nothing has been posted.

Each file has a front-matter `title` and `labels`, then the issue body. Paths and line numbers were checked against commit `347f5abd`. Before you post an issue, check that its lines have not moved. In the repository, the issue templates use `bug`, `enhancement` and `conduct`, and CONTRIBUTING.md refers to `good first issue`. The area labels used here (`backend`, `frontend`, `cli`, `sdk`, `docs`, `samples`, `mcp`, `tests`, `go`, `javascript`) may not exist on GitHub yet.

| File | Title | Labels | Area | Size |
|---|---|---|---|---|
| [01-cli-output-alias.md](01-cli-output-alias.md) | Accept --output on the five CLI commands that only take --format | good first issue, cli | CLI (`cli/`) | 1–2 hours |
| [02-docs-cli-json-only-commands.md](02-docs-cli-json-only-commands.md) | List the --json-only CLI commands in the CLI README output section | good first issue, docs, cli | Docs (`cli/README.md`) | 1 hour |
| [03-frontend-history-timeline-attempts.md](03-frontend-history-timeline-attempts.md) | Use the shared retry helper in the test history timeline | good first issue, frontend, bug | Frontend (`frontend/`) | 1–2 hours |
| [04-backend-parser-timing-unit-tests.md](04-backend-parser-timing-unit-tests.md) | Add unit tests for set_case_timing and epoch_ms | good first issue, backend, tests | Backend (`backend/`) | 1–2 hours |
| [05-backend-samples-parse-test.md](05-backend-samples-parse-test.md) | Test that every file in samples/ is detected and parsed | good first issue, backend, samples, tests | Backend + samples | Half a day |
| [06-samples-pytest-json-report.md](06-samples-pytest-json-report.md) | Add a pytest-json-report sample to samples/ | good first issue, samples | Samples (`samples/`) | 1–2 hours |
| [07-samples-nunit3.md](07-samples-nunit3.md) | Add an NUnit 3 XML sample to samples/ | good first issue, samples | Samples (`samples/`) | 1–2 hours |
| [08-go-nil-safe-test-reporter.md](08-go-nil-safe-test-reporter.md) | Make the Go TestReporter safe to call when reporting is off | good first issue, sdk, go, bug | Go SDK (`client/go/`) | 1–2 hours |
| [09-js-jest-sample-project.md](09-js-jest-sample-project.md) | Add a Jest sample project under client/examples/js | good first issue, sdk, javascript | JS SDK examples (`client/examples/`) | Half a day |
| [10-mcp-deep-tools-tests.md](10-mcp-deep-tools-tests.md) | Add behaviour tests for the deep-investigation MCP tools | good first issue, mcp, tests | MCP (`mcp/`) | 1–2 hours |
| [11-docs-vscode-mcp-setup.md](11-docs-vscode-mcp-setup.md) | Add a VS Code section to the agent cookbook's MCP setup | good first issue, docs, mcp | Docs (`user-guide/`) | 1–2 hours |

Notes for the maintainer:

- 06 and 07 can use the test from 05 once it has merged. Each says so, and each also works on its own.
- 07 ends with a note that the same steps work for TRX and xUnit.net v2. Robot Framework, Cucumber and TestNG also have parsers and no sample (`samples/README.md:19`), so they can become further issues of the same shape.
- None of these needs the full Docker stack. 09 and 11 have an optional end-to-end check against a running instance.
