# ============================================================
# TestLookup — Developer Makefile
# ============================================================
.PHONY: help dev dev-llm dev-setup dev-lite dev-lite-stop dev-logs dev-logs-seed stop restart clean backup restore preflight upgrade verify-ops-scripts migrate migrate-create migrate-down migrate-status pull-llm pull-llm-large list-llm test-backend test-backend-cov test-frontend test-e2e test-agent lint format type-check build build-push logs shell-backend shell-db simulate-upload seed-data seed-data-reset quickstart demo smoke benchmark setup-minio build-java-sdk build-java-sdk-docker mcp-install mcp-start mcp-sse mcp-sse-docker k8s-deploy-dev k8s-deploy-staging k8s-deploy-prod k8s-deploy-openshift k8s-deploy-openshift-artifactory k8s-deploy-openshift-artifactory-update k8s-mirror-images-openshift k8s-status k8s-rollout-async k8s-rollout-async-dev k8s-rollout-async-staging k8s-rollout-async-prod k8s-status-async k8s-status-openshift k8s-scale-worker

# Force bash for recipe shells. On Windows, GNU make defaults to cmd.exe which
# breaks bash builtins like `until`/`for f in glob`. Git Bash provides bash at
# /usr/bin/bash; on Linux/macOS it's at /bin/bash — both resolve via PATH.
SHELL := bash

DOCKER_COMPOSE = docker compose
BACKEND_CONTAINER = testlookup_backend
OLLAMA_CONTAINER = ollama
K8S_NAMESPACE ?= testlookup

# PostgreSQL defaults (overridable via environment — used by shell-db target)
POSTGRES_USER ?= testlookup_user
POSTGRES_DB   ?= testlookup

# Cross-platform copy command (.env bootstrap)
ifeq ($(OS),Windows_NT)
  CP_CMD = copy .env.example .env
else
  CP_CMD = cp .env.example .env
endif

help: ## Show this help message
	@echo "TestLookup — Development Commands"
	@echo "======================================"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Development ─────────────────────────────────────────────

# File target: bootstrap .env only when it does not exist. Make skips this rule
# if the file already exists. gen-dev-env.sh copies .env.example AND fills every
# required secret with a random value (keeping DATABASE_URL / MONGO_URI in sync)
# so `make dev` / `make demo` start on the first try with zero manual editing.
# Falls back to a plain copy if bash is unavailable (then edit secrets by hand).
.env:
	@bash scripts/gen-dev-env.sh || ($(CP_CMD) && echo "bash not found — copied .env.example; edit the secrets by hand before starting.")

dev: .env ## Start core stack without local LLM (Ollama/ChromaDB excluded)
	$(DOCKER_COMPOSE) up -d --build
	@echo ""
	@echo "Stack started (no local LLM — AI falls back to rules/ML engine)."
	@echo "  Dashboard  -> http://localhost:3000"
	@echo "  API Docs   -> http://localhost:8000/docs"
	@echo "  MinIO      -> http://localhost:9001  (credentials from .env)"
	@echo "  Flower     -> http://localhost:5555"
	@echo ""
	@echo "  To enable Ollama + ChromaDB: run 'make dev-llm' instead."
	@echo "  Seed data runs automatically via the seed-init container."
	@echo "  Use the Quick Login buttons at http://localhost:3000 (no password required in dev)."
	@echo "  Run 'make dev-logs-seed' to watch seed progress."

dev-llm: .env ## Start full stack including local LLM (Ollama + ChromaDB)
	$(DOCKER_COMPOSE) --profile local-llm up -d --build
	@echo ""
	@echo "Stack started with local LLM enabled."
	@echo "  Dashboard  -> http://localhost:3000"
	@echo "  API Docs   -> http://localhost:8000/docs"
	@echo "  Ollama     -> http://localhost:11434"
	@echo "  ChromaDB   -> http://localhost:8001"
	@echo ""
	@echo "  Run 'make pull-llm' to download models (required on first run)."
	@echo "  Seed data runs automatically via the seed-init container."

dev-setup: .env ## First-time full setup with local LLM: start stack + pull LLM models
	$(MAKE) dev-llm
	@echo "Pulling Ollama LLM models (this may take a while on first run)..."
	$(MAKE) pull-llm
	@echo ""
	@echo "Setup complete. Open http://localhost:3000 and use the Quick Login buttons (no password required in dev)."

dev-lite: .env ## Start minimal stack (no Ollama/ChromaDB) — for low-resource machines
	docker compose -f docker-compose.dev-lite.yml up -d --build
	@echo "Lite stack started. Dashboard: http://localhost:3000 | API: http://localhost:8000/docs"

dev-lite-stop: ## Stop lite stack
	docker compose -f docker-compose.dev-lite.yml down

dev-logs: ## Tail logs for all services
	$(DOCKER_COMPOSE) logs -f

dev-logs-seed: ## Tail seed-init container output (useful on first run)
	$(DOCKER_COMPOSE) logs -f seed-init

stop: ## Stop all services (including local-llm profile if running)
	$(DOCKER_COMPOSE) --profile local-llm down

restart: ## Restart all services
	$(DOCKER_COMPOSE) --profile local-llm restart

clean: ## Stop services and remove volumes (WARNING: deletes all data)
	@echo "This will PERMANENTLY delete all PostgreSQL/MongoDB/Redis/MinIO/Chroma data."
	@echo "Set CONFIRM=yes to proceed (e.g. 'make clean CONFIRM=yes')."
	@if [ "$(CONFIRM)" != "yes" ]; then \
		echo "Aborted — no changes made."; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) --profile local-llm down -v --remove-orphans
	@echo "All volumes removed."

# ── Ops: backup / restore / upgrade ──────────────────────────
# One-command day-2 operations (see user-guide/administration.md).
# Scripts run everything through `docker compose exec/run` — the host needs
# only docker + bash (Git Bash on Windows; `make` already forces SHELL=bash).

backup: ## Backup postgres+mongo+minio → ./backups/testlookup-backup-<UTC>.tar.gz (QUIESCE=1 stops app first)
	@QUIESCE="$(QUIESCE)" BACKUP_DIR="$(BACKUP_DIR)" bash scripts/ops/backup.sh

restore: ## Restore a backup (make restore FILE=backups/<name>.tar.gz [FORCE=1] [CONFIRM=yes])
	@FORCE="$(FORCE)" CONFIRM="$(CONFIRM)" bash scripts/ops/restore.sh "$(FILE)"

preflight: ## Pre-upgrade checks: image tags, pending migrations, disk headroom (TAG=vX.Y.Z)
	@TAG="$(TAG)" bash scripts/ops/preflight.sh

upgrade: ## One-step upgrade: pull/build, migrate, restart in order, verify health (TAG=vX.Y.Z)
	@TAG="$(TAG)" bash scripts/ops/upgrade.sh

verify-ops-scripts: ## Lint + unit-test the backup/restore/upgrade scripts (no stack needed)
	@bash scripts/ops/verify_backup_scripts.sh

# ── Database ─────────────────────────────────────────────────

migrate: ## Run pending Alembic migrations
	$(DOCKER_COMPOSE) exec backend alembic upgrade head

migrate-create: ## Create a new migration (usage: make migrate-create MSG="add_test_runs")
	$(DOCKER_COMPOSE) exec backend alembic revision --autogenerate -m "$(MSG)"

migrate-down: ## Rollback last migration
	$(DOCKER_COMPOSE) exec backend alembic downgrade -1

migrate-status: ## Show migration status
	$(DOCKER_COMPOSE) exec backend alembic current

# ── AI / LLM ─────────────────────────────────────────────────
# These targets require Ollama to be running.
# Start it first with: make dev-llm

pull-llm: ## Pull recommended local LLM models (requires: make dev-llm)
	@$(DOCKER_COMPOSE) --profile local-llm ps --services --filter status=running | grep -q "^ollama$$" || \
	  (echo "ERROR: Ollama is not running. Start it first with: make dev-llm" && exit 1)
	$(DOCKER_COMPOSE) --profile local-llm exec $(OLLAMA_CONTAINER) ollama pull qwen2.5:7b
	$(DOCKER_COMPOSE) --profile local-llm exec $(OLLAMA_CONTAINER) ollama pull nomic-embed-text
	@echo "Models downloaded."

pull-llm-large: ## Pull larger/more capable models — 16GB+ VRAM (requires: make dev-llm)
	@$(DOCKER_COMPOSE) --profile local-llm ps --services --filter status=running | grep -q "^ollama$$" || \
	  (echo "ERROR: Ollama is not running. Start it first with: make dev-llm" && exit 1)
	$(DOCKER_COMPOSE) --profile local-llm exec $(OLLAMA_CONTAINER) ollama pull qwen2.5:14b
	$(DOCKER_COMPOSE) --profile local-llm exec $(OLLAMA_CONTAINER) ollama pull llama3.2:8b
	$(DOCKER_COMPOSE) --profile local-llm exec $(OLLAMA_CONTAINER) ollama pull deepseek-coder:6.7b

list-llm: ## List downloaded LLM models (requires: make dev-llm)
	$(DOCKER_COMPOSE) --profile local-llm exec $(OLLAMA_CONTAINER) ollama list

# ── Testing ───────────────────────────────────────────────────

test-backend: ## Run backend test suite (pytest)
	$(DOCKER_COMPOSE) exec backend pytest tests/ -v --tb=short

test-backend-cov: ## Run backend tests with coverage report
	$(DOCKER_COMPOSE) exec backend pytest tests/ -v --cov=app --cov-report=html --cov-report=term

test-frontend: ## Run frontend test suite (Vitest)
	$(DOCKER_COMPOSE) exec frontend npm run test

test-e2e: ## Run end-to-end tests (Playwright)
	$(DOCKER_COMPOSE) exec frontend npm run test:e2e

test-agent: ## Run AI agent unit tests with mocked tools
	$(DOCKER_COMPOSE) exec backend pytest tests/test_agent.py -v

# ── Code Quality ─────────────────────────────────────────────

lint: ## Lint all code (ruff + eslint)
	$(DOCKER_COMPOSE) exec backend ruff check app/ tests/
	$(DOCKER_COMPOSE) exec frontend npm run lint

format: ## Auto-format all code (ruff + prettier)
	$(DOCKER_COMPOSE) exec backend ruff format app/ tests/
	$(DOCKER_COMPOSE) exec frontend npm run format

type-check: ## Run type checking (mypy + tsc)
	$(DOCKER_COMPOSE) exec backend mypy app/
	$(DOCKER_COMPOSE) exec frontend npm run type-check

quality-gate: ## Run cross-cutting invariant guards (backend / frontend / database / agents)
	python scripts/quality_gate.py

quality-gate-list: ## List every quality-gate guard with a one-line description
	python scripts/quality_gate.py --list

quality-gate-update-baseline: ## Re-snapshot the ratchet baseline (review the diff before commit)
	python scripts/quality_gate.py --update-baseline

quality-gate-test: ## Run the unit tests for the quality-gate script itself
	cd scripts && python -m pytest test_quality_gate.py -v

# ── Build ─────────────────────────────────────────────────────

build: ## Build production Docker images
	docker build -t testlookup/backend:latest --target production ./backend
	docker build -t testlookup/frontend:latest --target production ./frontend
	@echo "Production images built."

build-push: ## Build and push images to registry (set REGISTRY env var)
	docker build -t $(REGISTRY)/testlookup/backend:$(VERSION) --target production ./backend
	docker build -t $(REGISTRY)/testlookup/frontend:$(VERSION) --target production ./frontend
	docker push $(REGISTRY)/testlookup/backend:$(VERSION)
	docker push $(REGISTRY)/testlookup/frontend:$(VERSION)

# ── Kubernetes ────────────────────────────────────────────────

k8s-deploy-dev: ## Deploy to development Kubernetes cluster
	kubectl apply -k k8s/overlays/dev

k8s-deploy-staging: ## Deploy to staging Kubernetes cluster
	kubectl apply -k k8s/overlays/staging

k8s-deploy-prod: ## Deploy to production Kubernetes cluster
	kubectl apply -k k8s/overlays/prod

k8s-deploy-openshift: ## Deploy using OpenShift-compatible overlay
	kubectl apply -k k8s/overlays/openshift

ifeq ($(OS),Windows_NT)
  BASH_CMD := "C:/Program Files/Git/bin/bash.exe"
else
  BASH_CMD := bash
endif

k8s-bootstrap-homelab: ## Bootstrap K3s on 3 homelab nodes via SSH (cluster only, no app)
	$(BASH_CMD) homelabsetup/bootstrap-homelab.sh $(ARGS)

k8s-oneclick-homelab: ## One-click: bootstrap K3s + deploy TestLookup (HOMELAB_NODE{1,2,3}_PASS env or interactive)
	$(BASH_CMD) homelabsetup/bootstrap-homelab.sh --deploy $(ARGS)

k8s-teardown-homelab: ## Uninstall K3s from all 3 homelab nodes
	$(BASH_CMD) homelabsetup/bootstrap-homelab.sh --teardown $(ARGS)

k8s-deploy-homelab: ## Deploy to K3s homelab cluster (pass extra flags via ARGS, e.g. make k8s-deploy-homelab ARGS="--skip-registry")
	$(BASH_CMD) homelabsetup/deploy-homelab.sh $(ARGS)

k8s-deploy-homelab-update: ## Rebuild images and redeploy to homelab (skip registry + models)
	$(BASH_CMD) homelabsetup/deploy-homelab.sh --skip-registry --skip-models $(ARGS)

k8s-deploy-openshift-artifactory: ## Air-gapped OpenShift deploy over HTTPS, all images from one Artifactory (config: openshiftsetup/artifactory.env; flags via ARGS)
	$(BASH_CMD) openshiftsetup/deploy-openshift-artifactory.sh $(ARGS)

k8s-deploy-openshift-artifactory-update: ## Rebuild app images + redeploy to OpenShift, reuse already-mirrored infra images
	$(BASH_CMD) openshiftsetup/deploy-openshift-artifactory.sh --skip-mirror $(ARGS)

k8s-mirror-images-openshift: ## Pre-seed the configured Artifactory with all third-party infra images (run on an internet-connected host)
	$(BASH_CMD) openshiftsetup/mirror-images.sh $(ARGS)

k8s-stop-homelab: ## Graceful pause: drain testlookup workloads + stop K3s on every node (PVCs preserved)
	$(BASH_CMD) homelabsetup/stop-homelab.sh $(ARGS)

k8s-restart-homelab: ## Resume from k8s-stop-homelab: start K3s + scale workloads back to their pre-shutdown replicas
	$(BASH_CMD) homelabsetup/restart-homelab.sh $(ARGS)

k8s-status: ## Show Kubernetes deployment status
	kubectl get pods,svc,ing -n testlookup

k8s-rollout-async: ## Wait for all queue-specific worker deployments to finish rolling out
	kubectl rollout status deployment/testlookup-worker-critical  -n $(K8S_NAMESPACE) --timeout=180s
	kubectl rollout status deployment/testlookup-worker-ingestion -n $(K8S_NAMESPACE) --timeout=180s
	kubectl rollout status deployment/testlookup-worker-ai        -n $(K8S_NAMESPACE) --timeout=180s
	kubectl rollout status deployment/testlookup-worker-default   -n $(K8S_NAMESPACE) --timeout=120s
	kubectl rollout status deployment/testlookup-beat             -n $(K8S_NAMESPACE) --timeout=120s

k8s-rollout-async-dev: ## Wait for async rollout in dev namespace
	$(MAKE) k8s-rollout-async K8S_NAMESPACE=testlookup-dev

k8s-rollout-async-staging: ## Wait for async rollout in staging namespace
	$(MAKE) k8s-rollout-async K8S_NAMESPACE=testlookup-staging

k8s-rollout-async-prod: ## Wait for async rollout in prod namespace
	$(MAKE) k8s-rollout-async K8S_NAMESPACE=testlookup

k8s-status-async: ## Show all queue-specific worker deployments and HPAs in a namespace
	kubectl get deployment \
	  testlookup-worker-critical testlookup-worker-ingestion \
	  testlookup-worker-ai testlookup-worker-default testlookup-beat \
	  -n $(K8S_NAMESPACE)
	kubectl get hpa -n $(K8S_NAMESPACE)

k8s-scale-worker: ## Manually scale a specific worker queue (QUEUE=ai REPLICAS=3)
	kubectl scale deployment/testlookup-worker-$(QUEUE) --replicas=$(REPLICAS) -n $(K8S_NAMESPACE)

k8s-status-openshift: ## Show OpenShift routes (if Route API is enabled)
	kubectl get route -n $(K8S_NAMESPACE)

# ── Utilities ─────────────────────────────────────────────────

logs: ## Tail backend logs
	$(DOCKER_COMPOSE) logs -f backend worker

shell-backend: ## Open a shell in the backend container
	$(DOCKER_COMPOSE) exec backend bash

shell-db: ## Open psql in the postgres container
	$(DOCKER_COMPOSE) exec postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

create-admin: ## Create initial admin user (Docker Compose)
	$(DOCKER_COMPOSE) exec backend python /app/scripts/create_admin.py

create-admin-k8s: ## Create initial admin user (Kubernetes)
	kubectl -n testlookup exec -it deployment/testlookup-backend -- python /app/scripts/create_admin.py

simulate-upload: ## Simulate a single Jenkins test run upload to MinIO
	$(DOCKER_COMPOSE) exec backend python /app/scripts/simulate_upload.py

seed-data: ## Seed demo users, projects, test cases, plans, strategies and releases
	$(DOCKER_COMPOSE) exec backend python /app/scripts/seed_dev_data.py

seed-data-reset: ## Wipe seed data and regenerate from scratch
	$(DOCKER_COMPOSE) exec backend python /app/scripts/seed_dev_data.py --reset

smoke: ## Verify a running stack is healthy (run after make quickstart / make dev)
	@python3 scripts/smoke.py 2>/dev/null || python scripts/smoke.py

quickstart: demo ## Zero-config one-command launch: auto-generate .env + run the demo

demo: .env ## One-shot demo: start core stack, wait for health, seed data loads automatically
	@echo "==> Starting core stack (no LLM)..."
	$(DOCKER_COMPOSE) up -d --build
	@echo "==> Waiting for backend health check..."
	@until curl -sf http://localhost:8000/health/ready > /dev/null 2>&1; do sleep 2; done
	@echo "==> Backend healthy. Seed data loads automatically on first start."
	@echo "==> Uploading sample test results..."
	@for f in samples/junit/*.xml; do \
		$(DOCKER_COMPOSE) exec -T backend python -c "\
import asyncio, sys, httpx; \
asyncio.run((lambda: httpx.AsyncClient(base_url='http://localhost:8000', timeout=30).post('/api/v1/auth/dev-login?role=admin'))())" 2>/dev/null; \
		echo "  Uploading $$f"; \
		curl -sf -X POST http://localhost:8000/api/v1/ingest/file \
			-H "Authorization: Bearer $$(curl -sf -X POST http://localhost:8000/api/v1/auth/dev-login?role=admin | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))')" \
			-F "file=@$$f" \
			-F "project_id=$$(curl -sf http://localhost:8000/api/v1/projects -H "Authorization: Bearer $$(curl -sf -X POST http://localhost:8000/api/v1/auth/dev-login?role=admin | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))')" | python3 -c 'import sys,json; ps=json.load(sys.stdin); print(ps[0]["id"] if ps else "")')" \
			-F "build_number=demo-$$(date +%s)" \
			-F "format=auto" > /dev/null 2>&1 || true; \
	done
	@echo ""
	@echo "==> Demo ready!"
	@echo "    Dashboard:  http://localhost:3000"
	@echo "    API docs:   http://localhost:8000/docs"
	@echo "    MCP SSE:    http://localhost:8002/sse"
	@echo ""
	@echo "    Default login: use the dev-login endpoint or register via the API."
	@echo "    To stop: make stop"

benchmark: ## Run classification + throughput benchmarks (stack must be running)
	@echo "==> Running classification benchmark (rules mode, in-container)..."
	$(DOCKER_COMPOSE) exec backend python /app/benchmarks/classification/evaluate.py \
		--mode rules \
		--output /app/benchmarks/results/classification_rules.json
	@echo ""
	@echo "==> Running throughput benchmark..."
	$(DOCKER_COMPOSE) exec backend python /app/scripts/load_test_concurrent.py bench \
		--base-url http://localhost:8000 \
		--iterations 20 \
		--concurrency 5 \
		--check-budgets \
		--output /app/benchmarks/results/throughput.json
	@echo ""
	@echo "==> Benchmarks complete. Results in benchmarks/results/"

setup-minio: ## Manually configure MinIO bucket and webhook (runs inside Docker — no host deps)
	docker run --rm \
		--network testlookup_net \
		-v "$(CURDIR)/scripts/setup-minio.sh:/setup-minio.sh:ro" \
		-e MINIO_ENDPOINT=http://minio:9000 \
		-e BACKEND_URL=http://backend:8000 \
		--entrypoint sh \
		minio/mc /setup-minio.sh

# ── Client SDKs ──────────────────────────────────────────────

build-java-sdk: ## Build the Java SDK fat JAR (requires Maven + JDK 11+)
	cd client/java && mvn clean package -DskipTests -q
	@echo "Built: client/java/target/testlookup-reporter-1.0.0-all.jar"

build-java-sdk-docker: ## Build the Java SDK fat JAR using Docker (no local Maven needed)
	docker run --rm -v "$(CURDIR)/client/java:/app" -w /app maven:3.9-eclipse-temurin-11 mvn clean package -DskipTests -q
	@echo "Built: client/java/target/testlookup-reporter-1.0.0-all.jar"

# ── MCP Server ────────────────────────────────────────────────

mcp-install: ## Install MCP server Python dependencies
	$(MAKE) -C mcp install

mcp-start: ## Start MCP server (stdio mode — for MCP Clients)
	$(MAKE) -C mcp start

mcp-sse: ## Start MCP server (SSE mode — for web/CI clients on port 8002)
	$(MAKE) -C mcp sse

mcp-sse-docker: ## Start MCP SSE server via Docker Compose
	$(DOCKER_COMPOSE) up -d mcp
	@echo "MCP SSE server running at http://localhost:8002/sse"
