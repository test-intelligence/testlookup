# Getting started with TestLookup

This walkthrough takes you from nothing to your own test results in TestLookup on one machine. It uses a disposable local stack with demo data. To run TestLookup for a team, pick a mode in the [deployment guide](docs/operations/deployment.md) afterwards.

## 1. Check the prerequisites

- Docker Engine or Docker Desktop with Compose v2 (`docker compose version`).
- Git, GNU Make and Bash. On Windows, run the commands from Git Bash or WSL.
- About 8 GB of free RAM for the core stack; local models need more ([sizing](user-guide/sizing.md)).
- Free ports 3000, 8000, 5433, 6379 and 9000. The Compose services use fixed container names, so run one stack per machine; see [local development](docs/operations/development.md) if another checkout already runs one.

No Git or Make? Use the no-clone installer instead: `curl -fsSL https://raw.githubusercontent.com/test-intelligence/testlookup/main/install.sh | bash` ([details](docs/operations/deploy-compose.md#release-images-without-a-clone)), then continue at step 3.

## 2. Start the demo stack

```bash
git clone https://github.com/test-intelligence/testlookup.git
cd testlookup
make quickstart
```

`make quickstart`:

1. creates `.env` from `.env.example` with random local secrets (an existing `.env` is kept);
2. builds and starts PostgreSQL, MongoDB, Valkey, the S3-compatible store, the backend, workers, scheduler, frontend and MCP server;
3. waits until `http://localhost:8000/health/ready` answers, while the seeder loads three demo projects with history;
4. uploads the sample JUnit reports from `samples/junit/`.

The first build downloads images and dependencies, so allow several minutes. To include local models (Ollama and ChromaDB) use `make dev-llm`, which also pulls the pinned models; to start without demo uploads use `make dev`.

## 3. Sign in and look around

Open [localhost:3000](http://localhost:3000). The local `.env` runs in development mode, so the login page offers Quick Login buttons that need no password. The no-clone installer runs in production mode instead: sign in as `admin` with the password it printed (also stored in `.env` as `INITIAL_ADMIN_PASSWORD`) and choose a new one.

Worth a look first:

- **Overview**: pass rate, new failures, flaky tests and the latest verdict for the selected project.
- **Runs**: every imported run; open one to see its results, AI verdict and risk score.
- **Failures**: failing tests grouped by error signature and classified by kind.
- **Release gate**: the GO / CONDITIONAL_GO / NO_GO recommendation and why.
- **Ask AI**: questions about a run or project, answered from its stored evidence (needs a model; see step 5).

The in-app guide at [localhost:3000/docs](http://localhost:3000/docs) explains every page. The API reference is at [localhost:8000/api-docs](http://localhost:8000/api-docs).

## 4. Import your own report

Any of these formats work: JUnit XML, TestNG, NUnit, xUnit.net, TRX, pytest, Robot Framework, Cucumber, Allure, Cypress and Playwright. `format=auto` detects the format from the content. Limits and format details: [ingestion guide](docs/pipelines/ingestion.md).

**From the UI.** On **Runs**, choose **Upload**, pick the project, the file and a build label. If there is no Upload button, an admin can turn on the `manual_upload` feature flag under **Settings → Feature flags**.

**From the CLI.** The CLI is installed from this repository (it is not on PyPI):

```bash
pip install -e cli/
export TESTLOOKUP_URL=http://localhost:8000
export TESTLOOKUP_API_KEY=qai_your_api_key_here   # Settings → API Keys
testlookup projects list
testlookup upload file results.xml -p <project-id> -b <build>
```

**From CI with curl.** The file-ingest endpoint is `POST /api/v1/ingest/file`, a multipart form with `file`, `project_id`, `build_number` and `format`, authenticated with a project API key:

```bash
curl -sS --fail-with-body -X POST http://localhost:8000/api/v1/ingest/file \
  -H "X-API-Key: $TL_API_KEY" \
  -F file=@results.xml -F project_id=<project-id> -F build_number=<build> -F format=auto
```

The empty dashboard of a new project shows the same commands with its project ID filled in. HTTP 202 means the upload was accepted and queued, not finished: the response carries a `task_id`, and `GET /api/v1/ingest/uploads/{task_id}` reports parsing progress. The run appears under **Runs** when the workers have stored it. For JSON batches, live streaming from test SDKs and authentication options, see the [API guide](docs/api/README.md) and the [CLI, SDK and MCP guide](user-guide/cli-sdk-mcp.md).

## 5. Optional: local models

```bash
make dev-llm
```

This starts Ollama and ChromaDB and pulls the Makefile's pinned chat and embedding models. Analysis falls back to the ML and rules engines whenever no model is available, so everything in steps 3 and 4 works without it. `AI_OFFLINE_MODE=true` (the default) keeps hosted model providers blocked; [AI features](user-guide/ai-features.md) explains the options.

## 6. Check health and stop

```bash
make smoke                                    # PASS/FAIL for the API, frontend and demo data
docker compose ps
docker compose logs --tail=100 backend worker beat
make stop                                     # stops everything; data is kept
```

A green smoke check proves the services answer, not that every queue and model works; an upload that reaches a finished run is the stronger check. `make clean CONFIRM=yes` deletes all data volumes and is not a routine shutdown.

## Next steps

- Run it for a team: [deployment guide](docs/operations/deployment.md) (Compose release images, Kubernetes, OpenShift, managed clouds, air-gapped).
- Operate it: [operator guide](user-guide/README.md), [administration](user-guide/administration.md), [troubleshooting](user-guide/troubleshooting.md).
- Wire up CI: [getting results in](user-guide/getting-results-in.md).
- Understand the system: [ARCHITECTURE.md](ARCHITECTURE.md).
- Contribute: [CONTRIBUTING.md](CONTRIBUTING.md).
