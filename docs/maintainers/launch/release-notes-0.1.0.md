# TestLookup 0.1.0 — release notes (draft)

> **Draft for the GitHub release.** Paste everything below the line into the release for tag `v0.1.0`, after the "Promote verified release" workflow has tagged the images ([RELEASING.md](../../../RELEASING.md), steps 4–7). Fill in the three image digests and the offline-bundle checksum from that run; they are marked **TO FILL**. Nothing here has been published.

---

**0.1.0 is the first tagged release of TestLookup.** If you run a build from the repository made before this tag, read [UPGRADING.md, "0.1.0 — the first public release"](https://github.com/test-intelligence/testlookup/blob/v0.1.0/UPGRADING.md#010--the-first-public-release) before you upgrade: the bundled broker moved from Redis 7.4 to Valkey 8.1, which cannot read the old Redis data volume, and a release install made before 0.1.0 keeps its development-mode `.env` (passwordless sign-in) until you switch it to production mode.

TestLookup is open-source software test intelligence. It collects the test reports your CI already produces, keeps their history per project, classifies why each test fails, and turns that evidence into a GO, CONDITIONAL_GO or NO_GO release recommendation you can audit. It runs on your own servers, and its AI analysis is offline by default.

## Install

Try it with demo data (Docker with Compose v2 and Bash; about 1 GB of images):

```bash
curl -fsSL https://raw.githubusercontent.com/test-intelligence/testlookup/v0.1.0/install.sh | TL_REF=v0.1.0 TESTLOOKUP_VERSION=v0.1.0 TL_PROFILE=demo bash
```

Open `http://localhost:3000` and sign in as `admin` with the password the installer prints. Leave out `TL_PROFILE=demo` for an empty instance. From a clone: `git checkout v0.1.0 && make quickstart`. On Kubernetes: `scripts/deploy-k8s.sh --cloud=self-hosted --image-tag=v0.1.0` (the overlays already pin `v0.1.0`). Every mode: the [deployment guide](https://github.com/test-intelligence/testlookup/blob/v0.1.0/docs/operations/deployment.md).

| Image | Tag | Digest |
|---|---|---|
| `ghcr.io/test-intelligence/testlookup/backend` | `v0.1.0` | **TO FILL** (`sha256:…` from the release manifest) |
| `ghcr.io/test-intelligence/testlookup/frontend` | `v0.1.0` | **TO FILL** |
| `ghcr.io/test-intelligence/testlookup/mcp` | `v0.1.0` | **TO FILL** |

The release does not rebuild anything: it adds the `v0.1.0` tag to the exact digests CI built and verified for the tagged commit, after a Trivy scan of those digests. Each image carries a BuildKit SBOM and provenance attestation in the registry. Images are built for linux/amd64 only.

**Offline bundle** for air-gapped installs: `testlookup-offline-0.1.0.tar.gz` and its checksum file are attached below (**TO FILL**: SHA-256). MongoDB (SSPL-1.0) and model weights are not in the bundle; see the [air-gapped install guide](https://github.com/test-intelligence/testlookup/blob/v0.1.0/user-guide/air-gapped-install.md).

## What 0.1.0 contains

- **Ingestion:** upload JUnit, TestNG, Robot Framework, NUnit 3, Visual Studio TRX, xUnit v2, Playwright JSON, pytest-json-report, Cypress/Mochawesome, Cucumber JSON and Allure results (or a zip of them), or stream live from the Python, Java (TestNG, JUnit 5 and 6), JavaScript (Jest, Mocha) and Go reporters. CI context from GitHub Actions, GitLab, Jenkins, Azure Pipelines and CircleCI.
- **Runs and failures:** run history and detail, run compare and bisect-from-green, failure clusters by error signature, defects with Jira promotion, an inbox for assigned failures and approvals, ownership rules.
- **Flaky tests:** flakiness scores from run history, quarantine proposals, and a quarantine-aware CI verdict (`testlookup ci-verdict`).
- **Release decisions:** a release gate with versioned policies, releases with phases, recorded overrides, and compliance packs signed with Ed25519 that verify offline (`testlookup compliance-pack verify`).
- **Analytics:** overview, trends, coverage, an explorer with saved views, value metrics, reports and scheduled digests.
- **AI, offline by default:** rules, ML and local-model failure classification with confidence and provenance, an agent pipeline with budgets and human review, deep investigation, Ask AI over your own data, a knowledge base, and an AI evaluation gate. `AI_OFFLINE_MODE=true` blocks hosted providers until an administrator allows them.
- **Test management:** authored test cases, plans, strategies and reviews.
- **Administration:** five roles plus project membership, scoped API keys, TOTP MFA and lockout, SAML 2.0 (with IdP-initiated single logout) and OpenID Connect single sign-on, SCIM 2.0 users and groups, retention and legal holds, personal-data export and erasure, feature flags, an activity log and an audit dashboard.
- **Interfaces:** the web app with an in-app guide, a REST API (742 documented operations), the `testlookup` CLI, and an MCP server for AI assistants that acts with the caller's own permissions.
- **Deployment:** Docker Compose (development, release images, demo, air-gapped override, monitoring add-on), Kustomize overlays for generic clusters, single-node K3s, OpenShift (including air-gapped Artifactory), EKS, GKE and AKS, plus backup, restore and upgrade tooling.

The complete list of changes is in the [CHANGELOG](https://github.com/test-intelligence/testlookup/blob/v0.1.0/CHANGELOG.md).

## Good to know before you adopt it

- **Single maintainer, no paid support.** See [GOVERNANCE.md](https://github.com/test-intelligence/testlookup/blob/v0.1.0/GOVERNANCE.md) and [SUPPORT.md](https://github.com/test-intelligence/testlookup/blob/v0.1.0/SUPPORT.md). Report vulnerabilities privately through GitHub ([SECURITY.md](https://github.com/test-intelligence/testlookup/blob/v0.1.0/SECURITY.md)).
- **The SDKs and the CLI are not on package registries yet** (PyPI, npm, Maven Central, the Go proxy). Install them from source or from your own deployment.
- **Images are linux/amd64 only and are not signed.** They carry SBOM and provenance attestations; signing is planned.
- **Not certified** against SOC 2, GDPR, HIPAA or any other framework. The [compliance guide](https://github.com/test-intelligence/testlookup/blob/v0.1.0/user-guide/compliance.md) maps controls to evidence and lists what is not covered.
- **MongoDB (SSPL-1.0) is required** and is not redistributed in the offline bundle.
- **No capacity figures yet.** The [sizing guide](https://github.com/test-intelligence/testlookup/blob/v0.1.0/user-guide/sizing.md) gives starting profiles that have not been load-tested.

## Thank you

Thanks to everyone who reported problems, reviewed changes and contributed code before 0.1.0. New here? Look for issues labelled [good first issue](https://github.com/test-intelligence/testlookup/labels/good%20first%20issue), and read [CONTRIBUTING.md](https://github.com/test-intelligence/testlookup/blob/v0.1.0/CONTRIBUTING.md) (every commit is signed off under the DCO).
