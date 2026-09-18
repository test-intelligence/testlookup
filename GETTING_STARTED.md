# Getting started with TestLookup

Use a disposable local environment for this walkthrough. It needs Git, Docker/Compose v2, GNU Make and Bash; on Windows run these commands from Git Bash or WSL. If another checkout is already running a stack, read the [isolation notes](docs/operations/development.md) first: Compose services use fixed container names and ports.

## Start the core demo

```bash
git clone https://github.com/test-intelligence/testlookup.git
cd testlookup
make quickstart
```

The Makefile creates a local `.env` if absent and starts/seeds the core stack. Inspect logs if building, migrations, health or seeding fail; initial time depends on downloads and hardware. Existing environment values are preserved. For optional Ollama/ChromaDB and pinned model downloads use `make dev-llm`.

Open [the dashboard](http://localhost:3000) and [API docs](http://localhost:8000/api-docs). Local demo login is available only under development settings. Complete normal password/MFA steps if the installation requires them. See [local development](docs/operations/development.md) for host-based iteration.

## Import and inspect a report

1. Select an authorized project and open its upload flow.
2. Supply a supported report, build label and meaningful environment/CI metadata. The [ingestion guide](docs/pipelines/ingestion.md) lists formats and bounds.
3. Save the accepted run/task IDs and wait for file parsing and persistence. HTTP 202 means accepted, not completed.
4. Open the run's results and intelligence. Inspect actual test outcome, AI pipeline state and any missing/degraded stages separately.
5. Review a generated report when applicable, then use comparison/release/report features under the configured policy.

For copyable API requests, see the [API guide](docs/api/README.md); for CI integrations, see [SDK/CLI/MCP](docs/architecture/integrations.md).

## Verify and stop

```bash
make smoke
docker compose ps
docker compose logs --tail=100 backend worker beat
make stop
```

Smoke/readiness checks do not prove every asynchronous queue or model works. A representative upload-to-report journey is stronger evidence. `make stop` preserves volumes; `make clean` deletes them and is not a routine shutdown command.

Continue with the [product journeys](docs/product/workflows.md), [developer handoff](docs/handoff/README.md) or [deployment guide](docs/operations/deployment.md).
