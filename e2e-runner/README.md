# TestLookup release checks

The regression inventory contains six P0 and seven P1 journeys. The disposable suite exercises onboarding, ingestion, triage, AI review, failure investigation, release decisions, quarantine, membership, test management, administration, search and streaming. A separate installation and rollback drill supplies the thirteenth journey. Each case has a stable ID in `catalogue.yaml`. An unexecuted required check remains visible in the release decision.

## Report ingestion coverage

| Path through TestLookup | Inputs exercised | What the check observes |
| --- | --- | --- |
| Browser upload form | JUnit XML, TestNG, NUnit 3, TRX, xUnit v2, pytest JSON, Robot XML, Playwright JSON, Cypress/Mochawesome, Allure and Cucumber JSON | Each native producer file goes through explicit format selection and automatic detection. The receipt, run, selected project, persisted names and status counts must agree. |
| Browser archive upload | ZIP of native Allure results and a multi-file selection of JUnit and Robot | The form and worker unpack the reports and materialize the expected tests. |
| Python SDK | pytest plugin and Robot Framework listener | Real framework execution and completed build readback. |
| Java SDK | JUnit Jupiter 5, JUnit Jupiter 6 and TestNG | The release fixture loads the repository's reporter JAR in each framework. |
| JavaScript/TypeScript SDK | Jest, Mocha, Playwright, Vitest, Cypress and Cucumber.js | Each runner loads its own adapter from the locally built SDK and reports a deliberately passing and failing case. |
| Go SDK | Go testing package | A compiled test binary reports through the SDK's testing helper. |

The .NET producers generate native NUnit, TRX and xUnit files for the browser path. Producer versions, inputs and regeneration instructions are in [native report fixtures](fixtures/native/README.md). The SDK producer sources are in [reporters](reporters).

The container image prepares native dependencies during the build. To run the disposable checks from an external test host, use Node, npm, Go, JDK 17 and Maven with the Python and Playwright dependencies below, then run `python e2e-runner/reporters/prepare.py`. Keep the prepared fixtures and Cypress binary cache available to that runner. Each execution creates its own named project for readback.

## Run a disposable candidate with Podman

Use Python 3.12, PyYAML 6.0.3, Git, Podman with a working Linux engine, and a Compose provider. Use the release-manifest.json from the successful upstream main CI run for the intended full SHA. It contains source_sha, version, and digest-qualified backend, frontend and MCP references. The launcher validates it with the repository's release manifest validator. The isolated runner bundles Node 26.13.0, Python 3.12.3 and Playwright 1.64.1. The initial image build requires package registry access.

```sh
python -m pip install PyYAML==6.0.3
python e2e-runner/launch.py --env ephemeral --tier smoke --manifest /path/to/release-manifest.json --out /path/to/results/rc-smoke
python e2e-runner/launch.py --env ephemeral --tier regression --manifest /path/to/release-manifest.json --out /path/to/results/rc-regression
```

Use a new output directory each time. The launcher builds the runner, provisions a randomly named project, waits for migrations and services, completes the generated local admin's forced first sign-in, runs on the private network, and removes the project and volumes in a finally block. On Windows the engine's reported forwarded port can be unreachable for an internal network. Running the browser in the same network also tests the deployed nginx route. The checked-in Compose template is the source of the stack layout. Environment credentials are generated in a temporary directory and isolated from the user's existing deployment.

The equivalent Make command is `make e2e-release ENV=ephemeral TIER=regression TL_MANIFEST=/path/to/release-manifest.json E2E_OUT=/path/to/new-results`. Podman is the default. The deployment drill uses a second disposable stack with an independently verified previous main build. See the [upgrade drill instructions](upgrade_README.md) for baseline selection and snapshot verification.

## Run a shared target

Install the matching client and browser dependencies first. Use a virtual environment so the MCP SDK matches the server source.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -c e2e-runner/constraints.txt -r e2e-runner/requirements.txt -r mcp/requirements.txt ./cli
(cd frontend && npm ci && npx playwright install chromium)
export TL_API=https://your-test-host.example
export TL_WEB=https://your-test-host.example
python e2e-runner/launch.py --env homelab --tier regression --manifest /path/to/release-manifest.json --out /path/to/results/homelab-rc
```

In PowerShell activate with `.\.venv\Scripts\Activate.ps1` and assign environment variables with `$env:TL_API` and `$env:TL_WEB`. For Compose use separate backend and frontend origins. For an external deployment use `--env external`. Supply TL_USER_VIEWER and TL_TOKEN_VIEWER, and corresponding pairs for TESTER, QA_ENGINEER, QA_LEAD and ADMIN via a credential manager. Values must identify dedicated test principals. The runner compares /auth/me to both the expected username and role. Missing values appear as BLOCKED. Shared runs use read-only requests and open the public login form.

## CI and K3s

Every new stable release tag (`vMAJOR.MINOR.PATCH`) starts the repository's `Promote verified release` workflow. After tag and CI manifest validation, the image scan and functional regression jobs run independently. Regression creates a disposable Podman deployment, exercises its five test roles, uploads HTML, Markdown, JSON and JUnit results, and removes the stack. Open the workflow run's summary for the decision. Promotion requires both jobs and checks the tested manifest hash. The trigger takes effect for tags pointing to commits that contain these workflows.

GitHub Actions and Jenkins use `e2e-runner/release_campaign.py` for every regression and full verification. Each run selects the nearest eligible distinct ancestor with a successful main CI build and an unexpired verified-images archive. The upgrade rehearsal uses its own disposable Podman stack. The ordinary journeys run on a fresh candidate stack even if baseline selection or the upgrade fails. Both outcomes feed the same JSON, JUnit, HTML and Markdown release decision.

The Jenkins agent labeled `podman` needs Python 3.11+, PyYAML, Git, the GitHub CLI, a working Podman Compose provider and registry pull access. Configure the checkout with full candidate ancestry, supply the upstream verified manifest and SHA, and install the Credentials Binding, HTML Publisher and JUnit plugins. Create the Jenkins Secret text credential `testlookup-release-actions-read` with read access to repository contents and GitHub Actions artifacts for `test-intelligence/testlookup`. The job binds it to `GH_TOKEN` for baseline discovery and verification. Private operator files remain in a temporary directory. Jenkins publishes the four public reports.

The GitHub workflow accepts a verified source SHA through dispatch or workflow_call. The nightly workflow runs the same verification. Promotion requires a passing functional decision for the candidate manifest hash.

For a shared target, set TL_DEPLOYMENT_IMAGES to a read-only JSON file containing one observation per component. Each entry includes component, candidate (the full manifest reference), running (the observed image ID), and children (verified platform digests from that candidate's registry index). Missing evidence or a digest mismatch blocks promotion.

For the in-cluster Job, build the image in this directory with repository root as context and publish it to your own registry. Supply its immutable digest, candidate ConfigMap, credential Secret and results PVC. Render the template below with the environment values set in the shell. Review the generated namespace, image and mount names before creating the Job.

```sh
envsubst < e2e-runner/job.yaml > /tmp/testlookup-release-job.yaml
kubectl --context "$HOMELAB_KUBE_CONTEXT" -n "$HOMELAB_NAMESPACE" create -f /tmp/testlookup-release-job.yaml
```

Template inputs are RUNNER_DIGEST_REF (repository@sha256:digest), TL_WEB, TL_API, MANIFEST_CONFIGMAP (contains release-manifest.json and the observed deployment-images.json), TEST_SECRET_NAME, RESULTS_PVC and a fresh RUN_ID. This template targets the persistent deployment's read-only lane.

For write journeys, give each concurrent release campaign its own disposable namespace and datastores. The AI test temporarily changes deployment-wide offline mode, and the private receiver exercises a one-shot Jira timeout. Configure the five test principals and allow `testlookup-mcp:8002` in the MCP service's host settings. Install fixtures once before starting the Job. Use the same namespace and UID that the deployment created. If a write Job is interrupted, recreate its disposable environment for the next run.

```sh
python e2e-runner/fakes_k8s.py --context "$HOMELAB_KUBE_CONTEXT" --namespace "$HOMELAB_NAMESPACE" \
  --namespace-uid "$HOMELAB_NAMESPACE_UID" --manifest "$VERIFIED_MANIFEST"
test "$(kubectl --context "$HOMELAB_KUBE_CONTEXT" get ns "$HOMELAB_NAMESPACE" -o jsonpath='{.metadata.uid}')" = "$HOMELAB_NAMESPACE_UID" || exit 1
export TL_WEB=http://testlookup-frontend TL_API=http://testlookup-backend:8000
envsubst < e2e-runner/job.yaml | kubectl set env --local -f - TL_TARGET=ephemeral \
  TL_MCP=http://testlookup-mcp:8002 TL_FAKE_URL=http://release-fixture:8080 -o yaml | \
  kubectl --context "$HOMELAB_KUBE_CONTEXT" -n "$HOMELAB_NAMESPACE" create -f -
```

The Job writes its results to the named PVC. Run the Kubernetes upgrade drill in its own disposable namespace and use `merge_upgrade.py` with `--platform kubernetes` to combine both results for the same candidate.

## Read the report

For stability measurements, add `--repeat 10` to the ephemeral regression command. Use `python e2e-runner/history.py /path/to/repeated-results --out /path/to/stability.json` to summarize completed runs of the same manifest, target and tier. The reported times cover test execution and exclude provisioning.

Start with index.html or summary.md, then results.json. Compare observed_build.revision with the verified manifest and check manifest_hash. Review each priority's blocked and retry counts. A retry pass is FLAKY. Local Playwright HTML, JSON, JUnit, client JUnit and private logs retain diagnostic detail under the run directory. Keep raw outputs private. Publish only public summary artifacts; use release-junit.xml for a sanitized CI test display.

## Add a test

Give the case a stable TL ID, priority, journey, area, role, tier and environment eligibility. Add the expected case to the run registry before execution. A new Playwright case uses frontend/tests/release. A Python client case goes in e2e-runner/live. Match its JUnit name in run.py. Register disposable-only Python cases under the ephemeral marker. Keep fixture production separate from the app parser and compare the positive data created in the run. Add a negative control on the disposable stack. Update the catalogue, README and changelog along with the case.

The sample XML fixture comes from actual pytest output. Recreate it with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest e2e-runner/fixtures/producer.py --junitxml=e2e-runner/fixtures/pytest-junit.xml`. The producer intentionally returns exit 1 with a failing leaf. That failure is input to TestLookup. It is separate from the release test's own pass or fail.
