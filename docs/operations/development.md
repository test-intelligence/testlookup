# Local development and onboarding

[Documentation home](../README.md)

## Isolate your checkout and services

Use a separate clone/worktree for concurrent branches. This handoff was authored in `testlookup_docs` on `codex/documentation-handoff`, leaving the active `testlookup_new` checkout unchanged. A separate filesystem checkout alone does not isolate Compose: several services have fixed container names and ports. Run one stack at a time or create a deliberate full override for names, ports, networks and data volumes.

## Container-first setup

Install Docker/Compose v2, Git, GNU Make and Bash. On Windows use Git Bash/WSL for Make recipes (`SHELL := bash`). From a fresh checkout:

```bash
make quickstart
docker compose ps
docker compose logs --tail=100 backend worker beat seed-init
```

The `.env` Make target calls `scripts/gen-dev-env.sh` only when absent. It generates local secrets and aligned database URLs. Verify `.env` remains ignored. Use `make dev` for core or `make dev-llm` for the optional local model services and pinned model pulls. Do not reuse dev quick-login settings for staging/production.

Open UI port 3000 and API docs port 8000. Inspect `/health/ready` plus actual queue consumers; a running container is not proof it processes every queue. The seed task is separate and can take time. [Makefile](../../Makefile), [env example](../../.env.example), [deployment profiles](deployment.md).

## Host-based code iteration

Use Python 3.11+ with a project-specific virtual environment, and the Node/npm version used by CI/package requirements. Backend dependencies are in `backend/requirements.txt`; use the frontend lockfile with `npm ci`. The CLI and Python reporter are separately packaged.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m pip install -e ./cli
cd frontend
npm ci
npm run dev
```

On Windows activation is `.venv\Scripts\Activate.ps1` in PowerShell. Configure backend settings with real local service URLs and secrets, then run from `backend`:

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Database/Redis/Mongo/object-storage services and workers are still required for full flows. Do not run migrations against the other agent's or an external shared database by accident. Vite proxies local API traffic; built UI uses same-origin unless `VITE_API_BASE_URL` is supplied. Use [setup scripts](../../scripts) for platform-specific setup after inspecting their behavior.

## First engineering exercise

Read `bootstrap.py`, locate `/api/v1/ingest` in the generated reference, follow `ingestion_pipeline.py` into finalization/outbox, then open the UI run detail/intelligence consumer. Submit a small report in a disposable environment and observe acceptance, durable run status and pipeline/review state separately. Change a harmless presentation label and run the relevant frontend check before touching core state transitions.

## Extension checklist

For a new API: add typed request/response, resource auth and role/key checks, service logic, audit/activity where required, meaningful negative-path tests, and a client wrapper. For persistence: add Alembic migration and update ORM/serializers. For worker work: verify queue subscription, bounded retries/idempotency, connection budget and status visibility. For a new agent/tool: register capability, contract, policy, budget and evaluation coverage. Regenerate docs and run quality guards. [Testing](testing.md), [maintenance](../handoff/maintenance.md).

Avoid `make clean` as routine shutdown; it deletes volumes and requires confirmation. Use `make stop` for ordinary teardown. For databases worth keeping, exercise [backup/restore](deployment.md) before upgrades.
