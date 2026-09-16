# Sol execution runbook

Companion to [the execution package](EXPLORATORY_EXECUTION_PACKAGE.md).
Commands below are instructions for future execution, not commands run while
authoring this plan. Use the working directory specified for each block.
Inspect current scripts before use if origin/main has advanced.

## 1. Dependencies and preflight

Required: Git/GitHub CLI with repository access; Python 3.11 and backend test
dependencies; Node matching current CI and `npm ci` lockfile install; Playwright
Chromium/Firefox/WebKit; real disposable PostgreSQL, MongoDB and Redis; Docker
or Podman and Git Bash/WSL for shell deployment; kubectl with the correct homelab
context. Java/Maven and Go are required for the complete SDK regression lanes.
Use the current workflow toolchain pins, not guessed latest versions.

Live dependencies: actual application/ingress URL and backend URL; separate
role accounts; secrets supplied through the existing private environment/secret
manager; sandbox delivery destinations and IdP where enabled; local model tags
and sufficient RAM; working trace/metric collection; backup storage and restore
target. Discover these from configured infrastructure and nonsecret manifests.
If credentials or intended homelab context are missing, ask for only that missing
dependency; do not print `.env`, kubeconfig, secret objects or password values.

The code currently implements review enforcement as a boolean environment
switch, not the owner's requested user-configured release-date activation. Treat
that as a visible product gap and never simulate it by scheduling a hidden host
task. The live suite also mixes true deployment-backed specs with mocked route
tests; audit each file before counting it as deployed E2E evidence.

Initial Git inspection from repository root:

```powershell
git status --short -uno
git branch --show-current
git fetch origin
git log -5 --oneline origin/main
gh pr list --repo test-intelligence/testlookup --state open
git diff origin/main --stat
```

Stop after any failed command; PowerShell does not automatically fail a native
command on nonzero exit. Capture `$LASTEXITCODE` immediately. A reusable runner
may throw on any nonzero result and record command/duration/log; do not pipe
away the real exit status. Retry transient network errors with bounded backoff
after verifying connectivity; do not resubmit ambiguous mutations blindly.

Planning files are already on `codex/exploratory-quality-release`. Preserve them.
Do not checkout/reset over dirty files or stash other people's changes. For the
clean-main baseline, use a detached disposable worktree at refreshed origin/main
inside an approved workspace path, or an equivalent clean local checkout.
This does not create another feature branch. Reuse installed dependencies via
explicit paths where appropriate; use each checkout's own source/config.

If baseline main is red, report the failing command/output and stop new fixes as
the owner required. If only infrastructure is absent, mark affected checks
BLOCKED; finish independent read-only inventory while resolving the dependency.
After baseline passes, review/commit only planning docs on the single branch,
then merge any origin/main advancement into it; never rebase or force-push.

## 2. Baseline and regression commands

Run handover Appendix B in full at entry, then the full current CI-equivalent
suite before candidate publication. The current workflow is the authoritative
list of service env, flags and additional checks. Do not use the historical
373 mypy count or 42-guard count to permit regressions: preserve current
`backend/mypy-baseline.txt` per-file limits and the current 43-guard registry.

### Backend, from `backend/`

Set `DATABASE_URL`, `TESTLOOKUP_POSTGRES_TEST_DSN`, `MONGO_URI`, `REDIS_URL`,
`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, application test secrets and the
current Redis integration enable flag using a **disposable test stack**.
Copy names/semantics from `.github/workflows/ci.yml`; never point the general
pytest suite or migrations at the shared homelab database. Some test fixtures
delete/reset data. Verify DSN destination before execution without printing it.

```powershell
../.venv311/Scripts/python -m ruff check app/ tests/
../.venv311/Scripts/python -X utf8 ../scripts/mypy_ratchet.py --check-stale
../.venv311/Scripts/python -X utf8 -m pytest tests/test_agent_configs.py tests/test_agent_config_resolver.py tests/test_agent_invocations.py tests/test_agent_invocation_idempotency.py tests/test_agent_invocation_sync_sse.py tests/test_agent_invocation_retry_cancel.py tests/test_reviews_api.py tests/test_agent_catalog.py tests/test_agent_api_docs.py tests/test_architectural_authorization.py tests/test_architectural_transaction_boundaries.py tests/services/test_workflow_run_state.py tests/services/test_retry_policy.py tests/services/test_pipeline_lease.py -p no:randomly -p no:testlookup --basetemp=.pytest-tmp-exploratory-key -q
../.venv311/Scripts/python -m app.services.agent_api_docs --check
../.venv311/Scripts/python -m app.services.prompt_eval_recordings --check
../.venv311/Scripts/python -m alembic heads
```

Full regression (real services configured and migrated as in CI):

```powershell
../.venv311/Scripts/python -X utf8 -m pytest tests/ -p no:randomly -p no:testlookup --basetemp=.pytest-tmp-exploratory-full --tb=short --cov=app --cov-report=xml --cov-report=term-missing --cov-fail-under=74 --junit-xml=test-results.xml
```

Also execute the **explicit protected integration selection** from the current
`postgres-integration` job in `.github/workflows/ci.yml`, including the ingestion
lineage fixture, review/lease/idempotency/outbox tests and PostgreSQL/Mongo/Redis
race tests. It has extra service setup and opt-ins; a green default suite with
these skipped is insufficient. Record selected versus executed/skipped counts.
Execute the late-ack redelivery probe and release ingestion boundary/Compose
queue proofs when their mission/change requires them. Keep costly scale proofs
local when possible instead of triggering extra workflow runs.

### Root-level guards, from repository root

```powershell
.venv311/Scripts/python -X utf8 scripts/quality_gate.py
.venv311/Scripts/python -X utf8 -m pytest scripts/test_quality_gate.py scripts/test_mypy_ratchet.py scripts/test_ci_security.py -p no:randomly -p no:testlookup --basetemp=.pytest-tmp-exploratory-guards -q
.venv311/Scripts/python scripts/release/check_image_drift.py
.venv311/Scripts/python -m pytest scripts/release/test_image_drift.py scripts/release/tests -p no:testlookup --basetemp=.pytest-tmp-exploratory-release -q
```

Capture quality guard count from actual output. A new guard requires fixture
self-tests, a Developer Guide row and consistent counts. Authorization and
transaction ratchets always run. Agent/review route changes require generated
API artifacts refreshed with `python -m app.services.agent_api_docs` from
backend followed by `--check`. Do not regenerate unrelated baselines.

### Frontend, from `frontend/`

```powershell
npm ci
npx playwright install chromium firefox webkit
npm run type-check
npm run lint
npm run test -- --coverage
npm run build
npm run check:bundle
npm run check:theme
npx playwright test --config playwright.docs.config.ts
npm run test:e2e:ci
```

Use compatible native Playwright dependencies or the project's supported
container environment where needed. Record pre-existing warnings separately;
do not turn new warnings into an accepted baseline without investigation.

**Live browser invocation:** populate `PLAYWRIGHT_BASE_URL` and
`VITE_API_BASE_URL` with verified origins (backend origin, not a guessed `/api`
suffix), and `E2E_ADMIN_PASSWORD` privately. Existing global setup tries dev-login
first, then the `admin` form login. Never enable dev-login on a shared homelab to
make tests pass. Use dedicated account fixtures/storage states for non-admin
personas; keep `.auth/` data local and untracked. Review each existing spec for
mock helpers, conditional skips and destructive operations before full execution.

```powershell
npx playwright test --config playwright.config.ts --project chromium --retries=0 --trace on
```

Run that full command only after the fixture/scope audit; until then pass an
explicit reviewed spec path. Repeat the required mission/browser matrix with
`--project firefox` and `--project webkit`. Collect traces privately on first
attempt; reruns are diagnostic and cannot erase a flaky first failure. New real
E2E tests must have deterministic fixture setup/cleanup and fail if prerequisites
are missing; never `test.skip()` the defect reproduction because data is absent.

### MCP / CLI / SDKs

From `mcp/` (matching its CI measurement domains):

```powershell
../.venv311/Scripts/python -m pytest tests -p no:testlookup -q --basetemp=.pytest-tmp-exploratory-mcp --cov=client --cov=config --cov=prompts --cov=resources --cov=review_notice --cov=server --cov=token_verifier --cov=tools --cov-report=term-missing --cov-fail-under=41
```

From root, install package dependencies using current CI requirements, then
keep SDK and CLI measurement invocations separate:

```powershell
.venv311/Scripts/python -m pytest client/tests -p no:testlookup -q --basetemp=.pytest-tmp-exploratory-sdk
.venv311/Scripts/python -m pytest cli/tests -p no:testlookup -q --basetemp=.pytest-tmp-exploratory-cli --cov=testlookup_cli --cov-report=term-missing --cov-fail-under=62
```

Other SDKs: `mvn --batch-mode --no-transfer-progress test` in `client/java`;
`go vet ./...` then `go test ./...` in `client/go`; `npm ci`, `npm run type-check`,
`npm run build`, `npm test` in `client/js`. Include current CI security, container,
manifest and deployment checks; do not claim all-CI parity if a tool is unavailable.

## 3. Defect and safe fix workflow

1. **Capture and classify.** Complete the defect template. P0: disclosure,
   destructive corruption, review/authority bypass or lost accepted work;
   P1: blocked core journey or wrong decision; P2: recoverable functional/UX
   issue; P3: minor presentation. Priority depends on real impact/reproducibility,
   not the number of failing assertions. Stop the unsafe experiment on P0.
2. **Reproduce.** Minimize data and steps, record actual source/image. Confirm
   expected behavior from code contract and requirements. A known unimplemented
   product feature becomes a documented gap/decision, not an invented quick fix.
3. **Assign developer.** One defect, explicit owned files, single branch;
   no side changes. Coordinator serializes migrations/shared files/settings.
4. **Write BOTH regressions before fixing.** Unit test exercises the smallest
   real decision/transform/component seam. E2E uses public entry point through
   a deployed application and its real persistence/queue as required by the bug.
   Use Playwright for UI defects; public API/CLI/MCP-to-durable-outcome E2E is
   valid for non-UI defects. External third-party sandbox or local protocol sink
   may stand in for delivery destination; TestLookup's affected path stays real.
   A mocked browser HTTP response or service-only PostgreSQL test does not
   satisfy the mandatory real E2E requirement on its own.
5. **Show RED.** Execute both against the defective code/image with valid
   fixtures. Preserve the intended failing assertion and stack. Dependency
   errors, collection failures and unrelated timeouts are not regression proof.
   For infrastructure defects use a focused unit test of the responsible script/
   manifest behavior plus a disposable deployed-system regression; do not waive
   either layer merely because it is not application code.
6. **Fix minimally.** Preserve package invariants, auth and transaction authority;
   services add/flush, routers commit. Re-run both tests to GREEN against the
   actual fixed build, checking database/side-effect outcomes, not just response.
   Before commit, build defective/fixed working trees only into disposable test
   stacks and record source plus diff hash and image digest. Shared homelab
   candidate deployment waits for the reviewed commit described in section 4.
7. **Mutate.** Reverse each important fix condition separately. Assert expected
   original source exists exactly once (or precise AST target), assert changed
   source differs, run focused tests, require intended assertion failure, then
   restore source in `finally`. Hash-compare restoration and run green baseline
   again. Require every proposed mutation to be applied and killed; no “not
   applicable” success. Use disposable build for E2E mutations that need deployed
   behavior. Never mutate shared serving homelab or another agent's files.
8. **Regression.** Run affected unit/integration/browser/client tests plus
   adjacent Mxx variants, authz negatives and mandatory guards. Full regression
   after each completed fix/iteration before advancing its release state; cache
   dependency installation, not test outcomes. The final frozen candidate must
   have a complete uninterrupted full regression record. Repeat a suite only
   when changes/failures/new risks justify it, not while waiting for CI.
9. **Document.** Changelog, exact test names, bug→requirement→mission links,
   architecture §12 shipped note with deviations/gaps where relevant, API docs,
   migration/guard docs and coverage inventory. No fabricated issue or PR IDs.
10. **Independent pre-commit review.** Reviewer first analyzes changed files,
    then integration/auth/transactions/worker races/schema/UI/tests. Resolve all
    findings and obtain approval of exact diff. Reviewer checks RED/GREEN and
    mutation evidence, whether E2E is truly deployed, cleanup and skip behavior.
    The author cannot self-approve. Planning-only documents get a content review;
    they do not need artificial unit/E2E tests.
11. **Commit locally.** Coordinator checks branch, staged paths and diff, commits
    only approved files, records commit SHA, then repeats original live mission
    and neighboring risk pairs. Keep all fixes on this branch. No PR per defect.

## 4. Homelab deployment and verification

### Discover and record before mutation

The repository's supported script is `homelabsetup/deploy-homelab.sh`, invoked
from Git Bash/WSL with Docker/Podman. Its namespace is hard-coded `testlookup`;
registry `registry.local:30500`; documented ingress `http://testlookup.local`.
These are configuration defaults, **not verified live destinations**. The
GCP `deploy-staging.yml` workflow is not the homelab deployment path.

```powershell
kubectl config current-context
kubectl get nodes
kubectl -n testlookup get deployments,statefulsets,pods,services,ingress,pvc
kubectl -n testlookup get deployments -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .spec.template.spec.containers[*]}{.name}{"="}{.image}{" "}{end}{"\n"}{end}'
kubectl -n testlookup get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .status.containerStatuses[*]}{.name}{"="}{.imageID}{" "}{end}{"\n"}{end}'
```

Establish context and namespace ownership before deployment. Read the entire
deploy script; it also prepares fanout cutover, applies infra/config, migrates,
seeds/admin provisions and may modify MCP credentials. Existing secrets are
normally retained; fresh bootstrap may print credentials. Keep raw deploy logs
private and redact before attaching. Do not automatically regenerate secrets,
bootstrap registry/nodes, disable TLS validation or alter device trust settings
to resolve a failed test. Use existing supported credentials/context.

Record current deployment images/digests, rollout revisions, replica counts,
nonsecret config, schema head, queue depths and PVCs. Confirm a usable backup
and rehearse restore on a disposable target using `k8s/components/backup/README.md`
or the applicable `scripts/ops/backup.sh` / `restore.sh` procedure. Inspect each
script's required inputs first; never restore over shared data as a rehearsal.
If backup/restore target is unavailable, pause destructive and migration-dependent
steps and continue other missions. A saved YAML manifest is not a data backup.

### Deploy the candidate

Use a clean reviewed commit; record `git rev-parse HEAD` and `git rev-parse HEAD^{tree}`.
Do not deploy uncommitted fix code. From Git Bash on the confirmed homelab target:

```bash
bash homelabsetup/deploy-homelab.sh --skip-registry --skip-models --skip-dns
```

Set `CONTAINER_ENGINE=podman` only if using the configured Podman runtime.
Do not use `--skip-models` until required exact tags are installed. These flags
skip existing infrastructure setup, **not builds**. Script generates an immutable
timestamp build tag for backend/frontend/MCP, stages SDKs, performs the migration
job before an existing application rollout, then applies the homelab overlay.
Record the build tag→commit/tree→registry digest mapping and verify that all
application workers use the expected backend image.

**Critical distinction:** `--skip-build` resolves the newest common registry tag.
It does not select a requested SHA or prove that your candidate is deployed.
Do not use it for candidate or rollback selection. Do not assume exporting
`BUILD_TAG` overrides the script's internal timestamp generation. The script
temporarily replaces `BUILD_TAG_PLACEHOLDER`; confirm it is restored at rest.

Poll individual application rollout status with a bounded timeout, compare
every pod's image ID against candidate digests, inspect migration-job success
and actual Alembic head. Old healthy pods answering ingress do not prove rollout.
Verify `/health/live`, readiness path from actual probes, frontend asset loading,
worker queues/beat, MCP and datastore access. Record failed pods/events with
sanitized logs. Do not allow a nonzero script/rollout result to count as success.

### Verify and roll back

Run M26, all new E2E and full applicable live matrix; include M04 lineage,
M02 project negatives, M07–M08 review distribution, M14 recovery, M19 keyboard,
M22 clients, and monitoring. Use a dedicated isolated stack for disruptive
work: changing namespace alone is insufficient because manifests, hostnames,
queues, credentials and persistent stores must also be isolated. Do not pass
an unsupported namespace option to the deploy script.

Rollback trigger: migration/rollout failure, P0/P1 regression, lost accepted work,
or performance/recovery budget breach that persists after stopping load. Freeze
further writes/injection, capture evidence and restore the exact recorded prior
application digests and compatible config using verified deployment/container
names. A bounded `kubectl set image deployment/<recorded-name>
<recorded-container>=<recorded-image@sha256:digest>` is the operation; substitute
from the manifest, never guess names. Verify rollout and critical smoke again.

Image rollback is sufficient only if current schema is backward compatible.
For incompatible migration, use the rehearsed downgrade/backup recovery plan on
the intended target with data-loss implications resolved beforehand; never run
a blanket `alembic downgrade -1` or teardown. Record RPO/RTO actually achieved,
queue reconciliation and recovery evidence. Fixes must return to the branch and
receive tests/review before another deployment.

### Performance and chaos budget

Before M24 record starting resources, fixture size, test duration and baseline
latency/throughput. Prefer actual product SLOs where defined. Otherwise use these
explicit **exploratory budgets, not claimed product SLOs**: max five concurrent
synthetic users; one fault at a time initially; each injected outage ≤60 seconds;
ten-minute load windows followed by recovery observation. Abort on any accepted
data loss, scope/review bypass, unrelated user impact, sustained resource use
above 85% for two minutes, or free disk below 20%. Start lower on constrained VM.

Flag >20% p95 regression against the same environment/dataset after two comparable
runs for investigation; do not silently bless an already unhealthy baseline.
Recovery must return queue depth and latency to baseline envelope within ten
minutes or the recorded workload/deadline bound, whichever was justified before
the test. An original job deadline is not silently extended to make recovery pass.
Large 50k ingestion and paired-model evals need a separate resource preflight;
pause if memory/storage inadequate rather than inducing OOM on shared services.

## 5. One PR, CI and one merge

After all local and homelab gates pass:

1. Verify every confirmed defect has its unit/E2E/red/green/mutation/review record;
   collect final code review and sanitized evidence manifest. No unresolved
   confirmed defect is silently deferred under this programme's completion claim.
2. `git fetch origin`; if main advanced, `git merge origin/main` on this branch,
   resolve without rebase, review conflicts and repeat affected/full final gates.
   A changed candidate requires a new homelab build and evidence.
3. Stage only named intended files. `git diff --check`, staged diff review,
   secret/artifact inspection and branch check precede commit/push. No `git add -A`.
4. Push once ready: `git push -u origin codex/exploratory-quality-release`.
   Use a UTF-8 file for PR body describing problem/outcome, defect/test matrix,
   validation, homelab digests and residual gaps. Then:

```powershell
gh pr create --repo test-intelligence/testlookup --base main --head codex/exploratory-quality-release --title "test: verify exploratory journeys and fix regressions" --body-file <actual-pr-body-file>
gh pr checks <actual-pr-number>
gh pr view <actual-pr-number> --json headRefOid,mergeStateStatus,reviewDecision,statusCheckRollup
```

Replace angle-bracket placeholders with recorded values; they are not literal
commands. Reuse an existing matching PR if one already exists. Batch fixes
locally before a follow-up push. Do not modify required checks or use skip-CI to
save costs. Avoid redundant manual workflow dispatch; inspect first when event
delivery appears stuck. Diagnose Codacy/CI findings using the actual failing
job; fix and validate on this same branch. Wait with bounded intervals and report
meaningful changes, not unchanged polling.

5. Confirm all required checks, including Codacy, pass on the latest PR head,
   approvals/branch protection satisfied, and that head matches homelab evidence.
   No pending required check is green; never use admin bypass. Then:

```powershell
gh pr merge <actual-pr-number> --merge
```

6. Verify merged state, fetch/pull main safely and inspect main CI. Compare merge
   tree with tested candidate. If identical tree, preserve the verified candidate
   digest and map it to merge SHA; if different, rebuild/deploy/verify final main.
   Do not overwrite a verified deployment with unverified `latest`. Record any
   infrastructure-specific SHA attestation requirement and satisfy it with the
   actual final build. Repeat live critical smoke and final telemetry/queue checks.
7. Final report: PR and merge SHA, fixes/test pairs, reviewed evidence, actual
   homelab image/schema, deviations/gaps and completion state. If a new defect
   appears after merge, report it immediately; do not create a second PR while
   claiming this one-merge programme is complete. Further repair needs a clearly
   identified follow-up scope with the owner.

GitHub may necessarily run separate PR and main workflows. Cost minimization
means fewer publications and redundant runs, not a guarantee of only one Actions
run or weakening the merge gate.
