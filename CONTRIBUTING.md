# Contributing to TestLookup

Thanks for your interest in contributing. TestLookup is pre-1.0 open-source software. External contributions are welcome, but the project is evolving quickly and some areas are more contribution-friendly than others.

## Before you start

1. **Check what's already in flight.** The issue tracker is the live list of what's being worked on, planned, and on hold.
2. **Check for existing issues.** Before filing a new bug or feature request, search existing issues.
3. **Small PRs ship faster.** If you're considering a large change, open a discussion issue first.
4. **Security issues go through a different channel.** See `SECURITY.md`.

## Development setup

### Prerequisites

- Docker + Docker Compose v2
- Python 3.11 (backend and CLI)
- Node 20+ (frontend)
- `make` (Makefile targets are the supported entry points)
- 4 GB RAM / 2 vCPU / 20 GB disk minimum (core mode); 8 GB / 4 vCPU (full mode with LLM)

### Core mode (no LLM)

```bash
git clone https://github.com/anandtopu/testlookup.git
cd testlookup
cp .env.example .env           # edit secrets before starting
make dev                       # boots postgres + mongo + redis + minio + backend + frontend
```

Backend API reference: `http://localhost:8000/api-docs` (Swagger). In-app user documentation: `http://localhost:3000/docs`. Frontend: `http://localhost:3000`.

### Full mode (with local LLM)

```bash
make dev-llm
docker compose exec ollama ollama pull qwen2.5:7b
```

### Backend shell

```bash
make shell-backend             # bash inside the backend container
```

### Frontend type-check (without dev server)

```bash
cd frontend && npm run type-check
```

## Running tests

```bash
make test-backend              # pytest inside the backend container
make test-frontend             # vitest
make test-e2e                  # playwright (needs the stack running)
make lint                      # ruff + eslint
make type-check                # mypy + tsc
```

Single backend test:

```bash
docker compose exec backend pytest tests/test_agent.py::test_name -v
```

Single frontend test:

```bash
cd frontend && npx vitest run src/hooks/useFoo.test.ts
```

## Code style

- **Python:** `ruff` for linting and formatting, `mypy` for type-checking. Config in `backend/pyproject.toml`.
- **TypeScript:** `eslint` + `prettier` + `tsc --strict`. Config in `frontend/eslint.config.js` and `frontend/tsconfig.json`.
- **Pydantic v2 only.** No `@validator`, no `class Config`.
- **Async everywhere** in the backend. All DB/HTTP/service methods must be `async def`.
- **SWR for all data fetching** in the frontend. Pages import hooks, hooks wrap `useSWR`.
- **No secrets in commits.** `.env` is gitignored; `.env.example` is not.

Run `make format` before committing.

## Commit messages

Write commit messages that explain *why*, not *what*. The diff shows the *what*.

- Short imperative subject, max 72 chars: `fix webhook retry loop skipping DLQ transition`
- Blank line after subject
- Body explains motivation, edge cases, or alternatives considered

PRs that follow Conventional Commits (`fix:`, `feat:`, `docs:`, `chore:`, `refactor:`, `test:`) make release notes easier to write.

## Pull request process

1. **Fork** the repo and branch from `main`.
2. **Make your change** in small, self-contained commits.
3. **Run the tests locally.** PRs with failing CI get bumped down the queue.
4. **Open a PR** against `main`. Describe: what, why, how you tested.
5. **Respond to review.** Maintainers aim to respond within 72 hours.
6. **Squash before merge** if your PR history has fixup commits.
7. **Sign off your commits** with `git commit -s`. See [Licensing](#licensing-dco) below.

### PRs likely to land quickly

- Bug fixes with a failing test that the change makes pass
- Documentation fixes
- New sample datasets for `samples/`
- New MCP client integration guides
- Performance improvements backed by a benchmark

### PRs that need discussion first

- New external integrations
- Schema migrations
- Changes to the feature flag system
- Changes to the MCP server tool surface
- Anything touching authentication or tenant isolation

## Proposing a new feature

1. Open a **discussion issue** with the `feature request` label.
2. Wait for maintainer feedback before writing code.
3. If approved, the issue gets a target phase from the roadmap. Then open a draft PR.

## Feature stability labels

Every user-facing feature is labelled:

- **Core** -- shipping in the OSS release, flag-on by default.
- **Experimental** -- in-repo but flag-off by default. May change or be removed.
- **Enterprise / future** -- not accepting external contributions yet.

The canonical list is the running instance itself: **Settings -> Feature Flags**.

## Licensing (DCO)

TestLookup is licensed under Apache 2.0 (see `LICENSE`). By contributing, you agree your contribution is licensed under the same terms.

We use the **Developer Certificate of Origin** (DCO). Sign your commits with `git commit -s` -- this adds a `Signed-off-by` line asserting you wrote the code or have the right to contribute it. We do not require a CLA.

Full DCO text: https://developercertificate.org/

## Code of Conduct

This project follows the [Contributor Covenant v2.1](./CODE_OF_CONDUCT.md). Unacceptable behaviour can be reported to `security@testlookup.app`.
