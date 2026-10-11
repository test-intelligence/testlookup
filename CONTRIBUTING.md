# Contributing to TestLookup

Thanks for your interest in contributing. TestLookup is pre-1.0 open-source
software under the Apache License 2.0. Contributions of every size are welcome:
bug reports, documentation fixes, sample data, tests and features.

## Before you start

1. **Search the issues** first; the issue tracker is the live list of what is
   being worked on, planned and on hold.
2. **Small PRs ship faster.** For a large change, open an issue to discuss the
   approach before writing code.
3. **Security issues go through a different channel**: GitHub private
   vulnerability reporting, as described in [SECURITY.md](SECURITY.md). Never
   open a public issue for a vulnerability.
4. **Questions** belong in the places listed in [SUPPORT.md](SUPPORT.md).

Looking for a first contribution? Issues labelled `good first issue` are
scoped to one area and have a pointer to the code involved; documentation
fixes and new sample result files under `samples/` are also good starts.

## Development setup

### Prerequisites

- Docker + Docker Compose v2
- Python 3.11 (backend and CLI)
- Node 20+ (frontend)
- `make` (the Makefile targets are the supported entry points; on Windows use
  Git Bash or WSL)
- 8 GB RAM for the core stack; 16 GB with the local LLM

### Core mode (no LLM)

```bash
git clone https://github.com/test-intelligence/testlookup.git
cd testlookup
make dev                       # generates .env if missing, then boots postgres + mongo
                               # + valkey + the S3 store + backend + frontend
```

`make quickstart` does the same and loads demo data. Backend API reference:
`http://localhost:8000/api-docs` (Swagger). In-app user documentation:
`http://localhost:3000/docs`. Frontend: `http://localhost:3000`.

### Full mode (with local LLM)

```bash
make dev-llm                   # also starts Ollama + ChromaDB and pulls the pinned models
```

The default local model is `qwen2.5:7b-instruct` (Apache-2.0); see
[user-guide/offline-model-pack.md](user-guide/offline-model-pack.md).

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

Frontend unit tests must not make real network requests: the vitest setup
fails any test that tries. Test data belongs in fixtures next to the test or
in `samples/`; keep it synthetic (no real names, hosts, keys or customer data).

## Code style

- **Python:** `ruff` for linting and formatting (`backend/ruff.toml`), `mypy`
  for type-checking (`backend/mypy.ini`). CI lints with `ruff check app/ tests/`
  from `backend/`.
- **TypeScript:** `eslint` + `prettier` + `tsc --strict`
  (`frontend/eslint.config.js`, `frontend/tsconfig.json`).
- **Pydantic v2 only.** No `@validator`, no `class Config`.
- **Async everywhere** in the backend. All DB/HTTP/service methods must be
  `async def`.
- **SWR for all data fetching** in the frontend. Pages import hooks, hooks wrap
  `useSWR`.
- **No secrets, private host names or local paths in commits.** `.env` is
  gitignored; `.env.example` holds placeholders only. The quality gate refuses
  real-looking API keys, private network addresses and Windows profile paths.

Run `make format` before committing.

## Commit messages

Write commit messages that explain *why*, not *what*. The diff shows the *what*.

- Short imperative subject, max 72 chars: `fix webhook retry loop skipping DLQ transition`
- Blank line after subject
- Body explains motivation, edge cases, or alternatives considered
- End with a `Signed-off-by:` line (`git commit -s`, see [Licensing](#licensing-dco))

PRs that follow Conventional Commits (`fix:`, `feat:`, `docs:`, `chore:`,
`refactor:`, `test:`) make release notes easier to write. A user-visible change
also adds a line to the `[Unreleased]` section of [CHANGELOG.md](CHANGELOG.md).

## Before you push

Running the tests is not the same as running what CI runs. CI also builds the
production bundle, enforces a coverage ratchet, and runs several guards that
live outside the test suites — `check:bundle`, `check:theme`,
`prompt_eval_recordings --check`, `agent_api_docs --check`, the handoff
reference checks and `scripts/quality_gate.py` (architecture rules, the
local-only path list, private markers and the dependency license allowlist).

So run the list, not your memory of it:

```bash
make push-check                # everything CI runs; about 30 minutes
make push-check-quick          # skips the slow suites; a good first pass
```

`make push-check-quick` is genuinely weaker and says so when it finishes — it
does not predict CI. To have the full check run on every `git push`:

```bash
make install-hooks
```

That points `core.hooksPath` at the tracked `.githooks/`.

**Docs-only pushes skip the checks automatically**, because CI skips its test
workflow for them too. "Docs-only" is read straight from the `paths-ignore`
lists in `.github/workflows/ci.yml`, with GitHub's glob rules: `*.md` means
root-level markdown only, so an edit to `architecture/*.md` still runs
everything, as it does in CI. Pass `--full` to run the checks anyway.

**Where local and CI legitimately differ.** Three checks cannot be made
identical, and `scripts/push_check.py` documents how it handles each:

- **Handoff references.** `generate_handoff_reference.py --check` can fail
  locally when the local FastAPI/Pydantic emits slightly different OpenAPI
  than CI's. The gate applies the equivalent test that *is* reliable: the
  committed references must differ from `origin/main` by **additions only**.
- **The mypy ratchet.** Local and CI mypy can disagree on some files. Compare
  the files your change touched, not the total.
- **Environment-dependent tests.** A few suites need services a laptop does
  not run. They are listed in `KNOWN_LOCAL_FAILURES` with a reason and are
  reported but not fatal. Anything *not* on that list fails the push.

## Pull request process

1. **Fork** the repo and branch from `main`.
2. **Make your change** in small, self-contained commits, each signed off.
3. **Run `make push-check`** (see [Before you push](#before-you-push)).
4. **Open a PR** against `main` and fill in the template: what, why, how you
   tested, CHANGELOG entry, screenshots for UI changes.
5. **Respond to review.** Maintainers aim to respond within a week; see
   [GOVERNANCE.md](GOVERNANCE.md) for how decisions are made.
6. **Squash before merge** if your PR history has fixup commits.

### PRs likely to land quickly

- Bug fixes with a failing test that the change makes pass
- Documentation fixes
- New sample result files for `samples/`
- New MCP client integration guides
- Performance improvements backed by a benchmark

### PRs that need discussion first

- New external integrations
- Schema migrations
- Changes to the feature flag system
- Changes to the MCP server tool surface
- Anything touching authentication or tenant isolation
- New dependencies (they must be under a license on
  `scripts/license-allowlist.json`; copyleft, source-available and
  non-commercial licenses are not accepted)

## Proposing a new feature

1. Open an issue with the **Feature request** form.
2. Wait for maintainer feedback before writing code.
3. If the approach is agreed, open a draft PR early and link the issue.

## Feature stability

Every user-facing feature is one of:

- **Core** — on by default.
- **Experimental** — in the repository behind a feature flag that is off by
  default. It may change or be removed.

The feature-flag catalogue ([docs/operations/feature-flags.md](docs/operations/feature-flags.md))
lists every flag and its default; a running instance shows them under
**Settings -> Feature flags**.

## Licensing (DCO)

TestLookup is licensed under the Apache License 2.0 (see [LICENSE](LICENSE)).
By contributing, you agree that your contribution is licensed under the same
terms.

We use the **Developer Certificate of Origin** (DCO 1.1) instead of a CLA.
Every commit must carry a `Signed-off-by` line with your real name and an
e-mail address you control, certifying that you wrote the change or otherwise
have the right to submit it under the project's license:

```bash
git commit -s -m "fix: ..."              # adds: Signed-off-by: Your Name <you@example.com>
git commit --amend -s --no-edit          # sign off the last commit
git rebase --signoff main                # sign off every commit on your branch
```

A pull request whose commits are not all signed off cannot be merged. The
public repository checks this automatically on every pull request.

Full DCO text: https://developercertificate.org/

## Code of Conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md).
Report unacceptable behaviour to the maintainers as described there.
