# Getting Started with TestLookup

This walkthrough takes you from `git clone` to seeing your first failure clustered and explained. Total time: **under 15 minutes** with no LLM setup.

## Prerequisites

- Docker Desktop 4.x+ with Docker Compose v2
- 4 GB RAM, 2 vCPU, 20 GB free disk
- A terminal (bash, zsh, PowerShell)

## Step 1 -- Clone and start

```bash
git clone https://github.com/anandtopu/testlookup.git
cd testlookup
make quickstart          # generates .env (random local secrets) + boots + loads sample data
```

`make quickstart` writes a `.env` with random local secrets for you (no manual `openssl` steps) and keeps the connection strings in sync, so the stack starts on the first try. Wait for the `Demo ready!` message — the first boot takes 3-5 minutes (Docker image builds + database migrations + seed data).

Then verify everything came up healthy:

```bash
make smoke               # checks backend readiness, the API schema, and the frontend
```

A green `Smoke check passed` means you're ready. (Prefer to configure secrets by hand? `cp .env.example .env`, edit the secrets, then `make dev`.)

**Don't want to clone at all?** Pull the pre-built release images with a single remote command — it downloads the stack into `./testlookup`, writes a local `.env`, and starts everything:

```bash
curl -fsSL https://raw.githubusercontent.com/anandtopu/testlookup/main/install.sh | bash
```

This runs `docker-compose.release.yml` (pinned `ghcr.io/anandtopu/testlookup/*` images, no source build). Add demo data with `TL_PROFILE=demo`, or pin a build with `TESTLOOKUP_VERSION=<sha-or-tag>`.

## Step 2 -- Open the dashboard

Open http://localhost:3000 in your browser. You should see the TestLookup login page.

**Dev login (local only):** The seed script creates a dev-login shortcut. Click "Dev Login" or POST to `http://localhost:8000/api/v1/auth/dev-login?role=admin` to get a JWT without registering.

## Step 3 -- Explore the seed data

The seed script pre-loads three demo projects with 30 days of test run history, AI analysis results, failure clusters, and release gate decisions. Click any project in the sidebar to explore:

- **Overview** -- pass/fail trends and recent runs
- **Test Runs** -- click a run to see its test cases
- **Run Intelligence** -- the single-pane summary with failure clusters, regression diff, and risk score
- **Release Gate** -- the GO / CONDITIONAL_GO / NO_GO recommendation with reasons

## Step 4 -- Ingest your own test results

Upload a sample JUnit XML file:

```bash
# Get a token
TOKEN=$(curl -sf -X POST http://localhost:8000/api/v1/auth/dev-login?role=admin | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# Get the first project ID
PROJECT=$(curl -sf http://localhost:8000/api/v1/projects -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')

# Upload a sample file
curl -X POST http://localhost:8000/api/v1/ingest/file \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@samples/junit/auth-suite.xml" \
    -F "project_id=$PROJECT" \
    -F "build_number=my-first-run" \
    -F "format=auto"
```

You'll get a `202 Accepted` response with a `run_id`. The backend parses the file, creates test cases, computes aggregates, and triggers AI analysis (in rules mode by default -- no LLM needed).

Refresh the dashboard. Your new run should appear under the project's **Test Runs** page.

## Step 5 -- Try the CLI

```bash
# Install the CLI (inside the backend container)
make shell-backend
pip install -e /app/cli/

# List projects
testlookup projects list

# List recent runs
testlookup runs list --project $PROJECT --size 5

# Get intelligence for a run
testlookup intelligence show <run-id>
```

## Step 6 -- Try the MCP server

If you use Claude Desktop, Cursor, or another MCP client:

```json
{
  "mcpServers": {
    "testlookup": {
      "command": "python",
      "args": ["/absolute/path/to/testlookup/mcp/server.py"],
      "env": {
        "TESTLOOKUP_API_URL": "http://localhost:8000",
        "TESTLOOKUP_USERNAME": "admin",
        "TESTLOOKUP_PASSWORD": "admin"
      }
    }
  }
}
```

Then ask your AI assistant: *"List all QA projects in TestLookup"* or *"Show me the top failure clusters from today's runs"*.

## Step 7 -- Try the API directly

Swagger UI is at http://localhost:8000/api-docs (the user documentation lives at /docs). Key endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/ingest/file` | Upload test result files |
| GET | `/api/v1/projects` | List projects |
| GET | `/api/v1/runs` | List test runs |
| GET | `/api/v1/runs/{id}/intelligence` | Run Intelligence summary |
| GET | `/api/v1/runs/{id}/decision-trail` | AI decision trail |
| GET | `/api/v1/search` | Global search |

## Step 8 -- Shut down

```bash
make stop           # stops all containers (data preserved)
make clean          # stops + deletes all volumes (fresh start)
```

## What's next

- **Enable AI-assisted triage:** `make dev-llm` + `docker compose exec ollama ollama pull qwen2.5:7b`, then set `ANALYSIS_MODE=llm` in Settings
- **Connect your CI pipeline:** see `samples/README.md` for format examples, or the [SDK setup guide](README_FULL.md)
- **Explore experimental features:** open **Settings -> Feature Flags** -- experimental features ship flag-off and are listed there with their defaults
- **Contribute:** see [CONTRIBUTING.md](CONTRIBUTING.md)

## Tested on

- macOS 14 (Apple Silicon) with Docker Desktop 4.x
- Ubuntu 22.04 / 24.04 with Docker Engine + Compose v2
- Windows 11 with WSL2 + Docker Desktop

Minimum resource floor verified: 4 GB RAM, 2 vCPU (core mode). Full mode with Ollama `qwen2.5:7b` needs 8 GB RAM.
