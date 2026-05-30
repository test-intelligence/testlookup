# Jenkins pipelines

Five declarative pipelines for a Jenkins running on a Windows machine
(same box as the developer environment). Each Jenkinsfile is
self-contained — wire it to a Pipeline job pointing at this repo with
the appropriate `Script Path`.

| File | Purpose | Typical trigger | Wall-clock |
|------|---------|-----------------|-----------|
| `deploy-homelab.Jenkinsfile`  | Build + push images, apply K3s manifests, run admin-user seed | Manual / tag push  | ~10 min |
| `test-unit.Jenkinsfile`       | Backend pytest + frontend vitest, parallel                    | Per PR             | ~4 min  |
| `test-integration.Jenkinsfile`| FastAPI HTTP-wiring tests (DB mocked via dependency_overrides) | Per PR             | ~6 min  |
| `test-e2e.Jenkinsfile`        | Playwright against the full docker compose stack              | Nightly + manual   | ~20 min |
| `quality-gates.Jenkinsfile`   | `scripts/quality_gate.py` + its self-test                     | Per PR             | <1 min  |

## Required tools on the Windows agent

Install these once on the agent and add them to `PATH`:

| Tool | Why | Notes |
|------|-----|-------|
| **Git Bash**       | The deploy script and pipelines invoke `bash`. | `winget install Git.Git` |
| **Docker Desktop** | `docker compose` for integration / e2e stack; `docker build` for the deploy. | Enable WSL2 backend if possible. |
| **kubectl**        | Deploy pipeline applies the homelab overlay. | `winget install Kubernetes.kubectl` |
| **Python 3.11**    | Backend tests + quality gate. | The pipelines use `py -3.11`. |
| **Node 20+**       | Frontend tests + Playwright + Vite build. | `winget install OpenJS.NodeJS.LTS` |
| **curl**           | Smoke-check + readiness loops. | Ships with Git for Windows. |

A one-shot check after installing:

```cmd
where docker && where kubectl && where bash && where curl && py -3.11 --version && node --version
```

## Jenkins credentials referenced

Add these under **Manage Jenkins → Credentials → System → Global**:

| ID                          | Type         | Used by                  | Notes |
|-----------------------------|--------------|--------------------------|-------|
| `homelab-kubeconfig`        | Secret file  | `deploy-homelab`         | The K3s admin `kubeconfig`. Bound into `$KUBECONFIG` by the pipeline. |
| `homelab-admin-password`    | Secret text  | `deploy-homelab`         | Optional. Defaults to `Admin@2026!` if unset — the deploy script handles that. |

The unit / integration / e2e / quality-gate pipelines do not need
credentials — they're hermetic.

## Wiring a Jenkinsfile to a job

For each pipeline:

1. **New Item → Pipeline** (not Multibranch, unless you want one job
   per branch). Name it after the Jenkinsfile, e.g. `testlookup-deploy-homelab`.
2. **Pipeline definition → Pipeline script from SCM**.
3. **SCM**: Git. URL: your repo. Credentials: a deploy key with read
   access.
4. **Script Path**: `jenkins/<filename>.Jenkinsfile`.
5. **Save → Build Now**. The first build self-discovers parameters
   from the Jenkinsfile.

### Recommended trigger map

| Pipeline | Suggested trigger |
|----------|-------------------|
| `deploy-homelab`  | Manual + tag push (`v*.*.*`) — match the GitHub Actions deploy workflows. |
| `test-unit`       | Per-push, per-PR. Cheap enough that running on every commit is fine. |
| `test-integration`| Per-push, per-PR. |
| `test-e2e`        | Cron `H 2 * * *` (nightly ~2am) + manual. Per-PR is overkill at 20 min. |
| `quality-gates`   | Per-push, per-PR. Cheap, fast, high signal. |

For multibranch / GitHub webhooks, install the
**GitHub Branch Source** plugin and use a **Multibranch Pipeline**
container item — point each Jenkinsfile at a `Jenkinsfile` symlink
or use **Configure → Branch Sources → Behaviours → Filter by name
with regular expression** to scope.

## Parameter cheat sheet

### `deploy-homelab`

| Param | Default | When to flip |
|-------|---------|--------------|
| `SKIP_BUILD`        | `false` | Re-apply manifests without rebuilding images. |
| `SKIP_REGISTRY`     | `true`  | Default safe — registry rarely changes. |
| `SKIP_MODELS`       | `true`  | Default safe — Ollama models cached on PVC. Flip off ONLY on a fresh cluster with network egress to `registry.ollama.ai`. |
| `SKIP_MIRROR_CHECK` | `false` | Bypass the K3s `registries.yaml` precheck. Use ONLY when running against a non-K3s cluster. |
| `BACKEND_URL`       | `http://testlookup.local` | Public ingress URL probed during the smoke check. |

### `test-unit`

No parameters. Runs both trees in parallel.

### `test-integration`

| Param | Default | When to flip |
|-------|---------|--------------|
| `WITH_STACK` | `false` | Bring up docker compose first. Required only for testcontainers / live-DB tests added later — the existing suite mocks via `dependency_overrides`. |
| `PYTEST_K`   | `""`    | `-k` expression to scope (e.g. `auth or webhooks`). Empty = full suite. |

### `test-e2e`

| Param | Default | When to flip |
|-------|---------|--------------|
| `PLAYWRIGHT_PROJECT`     | `chromium` | One of `chromium / firefox / webkit / ""` (all three). |
| `PLAYWRIGHT_GREP`        | `""`       | Regex on test titles. |
| `KEEP_STACK_ON_FAILURE`  | `false`    | Skip the teardown so you can `docker exec` into the broken stack. Remember to tear down by hand. |

### `quality-gates`

No parameters. The ratchet is the entire point.

## Local-test the pipelines before pushing to Jenkins

Each Jenkinsfile can be linted with the `replay` feature or the CLI
linter. With Jenkins running on `localhost:8080`:

```bash
# Lint syntax + structural soundness (no execution).
curl -X POST -F "jenkinsfile=<jenkins/deploy-homelab.Jenkinsfile" \
  http://localhost:8080/pipeline-model-converter/validate
```

Or, from inside Jenkins: open any one-shot Pipeline job → **Pipeline
Syntax → Declarative Linter** and paste the file content.

## Failure-mode notes

- **`homelab.build-tag-placeholder` quality gate** fails if a previous
  `deploy-homelab` run was killed before its EXIT trap fired, leaving
  the substituted `BUILD_TAG` committed in
  `k8s/overlays/homelab/kustomization.yaml`. The deploy Jenkinsfile's
  `post { failure { ... } }` block shows a `git diff` of that file so
  the operator can spot the dirty state. Fix: `git checkout --
  k8s/overlays/homelab/kustomization.yaml`.
- **NordVPN-blocks-Java-LAN** is the classic recurring red herring for
  TestNG SDK runs against the homelab — `ConnectException: Permission
  denied: getsockopt`. Not Jenkins's problem, but if you wire SDK runs
  into a job and see this error, disable NordVPN on the agent or
  whitelist `java.exe`. See `memory/feedback_nordvpn_blocks_java_lan.md`.
- **Playwright flakes** under `test-e2e`: the pipeline keeps backend
  logs from a failed run as a build artifact (`backend.log`). 90% of
  the time the answer is in there.
