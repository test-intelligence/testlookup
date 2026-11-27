# Source and isolated HTTP security controls

E07 adds a narrow local source policy and a bounded functional release case.
AppSec owns policy and exception review. DevEx owns the existing CI execution
and evidence retention. QA owns the disposable candidate journey. Repository
administrators own effective merge rules and their bypass policy.

## Existing checks and the selected addition

The read-only GitHub inventory for this iteration found CodeQL default setup
configured for Python and JavaScript/TypeScript, with the default query suite.
The latest observed analyses for both languages reported no analysis errors.
The effective `main` rules required the existing backend/frontend tests, quality,
database integration, Trivy, Gitleaks and DCO checks. The observed rules had no
code-scanning severity requirement. The legacy branch-protection API returned404
while effective rulesets were available. That response does not establish an
unprotected branch.

CodeQL remains the broad source analyzer. GitHub default setup supplies its
analysis configuration without a repository workflow file. Creating another
CodeQL workflow would add maintenance and duplicate that role.
[GitHub describes default setup and its triggers](https://docs.github.com/en/enterprise-cloud%40latest/code-security/concepts/code-scanning/setup-types).

The repository already runs dependency and image vulnerability checks with Trivy,
history and candidate-snapshot secret checks with Gitleaks, functional project
and role guards in backend tests, and served nginx policy contracts in
`test_frontend_security_headers.py` and `test_security_csp_value.py`. The existing
release lane supplies password onboarding, principal verification, manifest and
revision checks, image evidence and cleanup. This addition reuses those lanes.

[The local source policy](../../scripts/security/source-policy.toml) runs the
existing locked Ruff 0.17.3 engine with two explicit rules:

| Rule | Blocking source boundary |
| --- | --- |
| [S501](https://docs.astral.sh/ruff/rules/request-with-no-cert-validation/) | Recognized HTTP client calls that explicitly disable certificate validation. |
| [S506](https://docs.astral.sh/ruff/rules/unsafe-yaml-load/) | Recognized unsafe YAML loading calls. |

The scope is `backend/app`, `mcp` and `cli/testlookup_cli`. It includes Python
source/stubs, excludes test directories and local virtual environments, and
ignores inline `noqa` suppressions. The selected rules complement the shared
outbound TLS configuration and safe parser boundaries. They provide a quick
blocking policy in the existing backend job. Configured TLS exceptions and
data-flow-sensitive cases still require the existing runtime/configuration
guards and CodeQL review. JavaScript/TypeScript source analysis stays with the
active CodeQL setup. Dependency and secret scans keep their existing purposes.

## Invocation, reports and exceptions

Run from a clean candidate checkout after installing the hash-locked backend
dependencies:

```sh
python scripts/security/source_policy.py --source-sha "$GITHUB_SHA" --out "$RUNNER_TEMP/source-policy.json"
python -m pytest scripts/security/tests/test_source_policy.py -q
```

The wrapper checks the exact Git HEAD and clean source/policy paths, requires the
configured Ruff version, and hashes the source inventory before and after the
scan. Analyzer errors and malformed output fail closed. It checks policy bytes
and the clean candidate again before returning a passing result. Output uses a
fresh file and contains:

| Field | Meaning |
| --- | --- |
| `schema_version`, `source_sha` | Report schema 1 and the full candidate commit reference. |
| `source_snapshot_sha256`, `policy_sha256` | Digests of the sorted source inventory and policy/exception bytes. |
| `tool`, `checked_roots`, `checked_files` | Locked analyzer identity and actual selected source scope. |
| `snapshot_kind`, `candidate_eligible`, `status` | Clean commit eligibility and PASS/FAIL, or explicit local worktree evidence. |
| `findings` | Rule, relative path, line, column, content-bound fingerprint and blocking/accepted disposition. |
| `exceptions` | Exact fingerprint with owner, reason and UTC expiry. |

Reports omit source excerpts and raw analyzer diagnostics. Errors retain a safe
finite error code. `--allow-dirty` is an explicit local diagnostic mode. A clean
result from that dirty worktree has `status=WORKTREE_PASS` and
`candidate_eligible=false`. CI uses the strict invocation above. A local
`--ruff /path/to/verified-executable` can reuse a checksum-verified copy of the
same locked version.

[The exception register](../../scripts/security/source-policy-exceptions.json)
starts empty. An exception needs an owner, reason, expiry within 90 days and the
exact finding fingerprint, which binds rule/location and the complete source
file digest. Expired, duplicated, malformed and stale exceptions fail the gate.
A changed source file requires fresh review. AppSec records triage and the
remediation issue alongside the exception review. Broad path/rule suppressions
and inline suppressions do not grant acceptance in this lane.

The real analyzer is exercised against non-executed source fixtures: explicit
certificate-check omission in requests and HTTPX, unsafe YAML loading and a
nearby verified request. The fixtures never import their generated source,
contact their example origins or execute the configured calls.

## Bounded isolated release case

`TL-SEC-001` is required for regression/full verification on the existing
`ephemeral` target. It is registered in
[the release catalogue](../../e2e-runner/catalogue.yaml) and
[runner inventory](../../e2e-runner/run.py). Shared targets cannot execute this
case. Its fixed operations are:

1. Read the login document and check response headers, cache policy and the
   static frontend's meta CSP baseline.
2. Use the assigned synthetic VIEWER session, or the launcher's existing
   fixture-only sign-in endpoint, and verify the configured principal/role
   through `/api/v1/auth/me`.
3. Read `/api/v1/users` as VIEWER and require 403. Read one page of that endpoint
   as ADMIN and require 200 as the nearby permitted control.

The case uses the existing token-safe request wrapper and 10 second timeouts on
its direct requests. Existing helpers bound retries and the 120 second Playwright
case timeout. The ADMIN session helper uses its established request timeout.
The routes and methods are fixed. No links are followed or routes discovered.
Its only optional write is fixture session issuance in the disposable deployment.
The case creates no product objects and sends no integration notifications.

The companion required `TL-AUTH-001` performs real password onboarding, project
selection, reload and sign-out, then disables its synthetic account and removes
its project. The new case adds response protections and ordinary role refusal
to that existing authentication evidence. The launcher removes the disposable
stack and volumes in its `finally` cleanup. An interrupted or failed teardown
requires reconciliation of that run's isolated resources before reuse.

The checked response controls are CSP `frame-ancestors 'none'`, X-Frame-Options
DENY, `nosniff`, the current referrer policy, one-year HSTS header, login-document
`no-store`, and the static meta CSP's default/script/object/base/form restrictions.
The anti-framing directive must arrive in the response header.
[OWASP explains the response-header controls](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html)
and [why meta CSP cannot enforce frame-ancestors](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html).
The HSTS check observes configuration. Browser HSTS enforcement and real ingress
TLS/certificate behavior remain separate deployment acceptance, including on an
HTTPS profile.

The pure policy helper is tested with healthy input and intentionally omitted
headers. A commented CSP example cannot satisfy the meta-policy check. These
synthetic controls alter only local test input and never modify the candidate.
The public release result retains a fixed seven-control list, authenticated and
denied/permitted booleans, the candidate SHA and profile. It excludes tokens,
response bodies, headers and principal values. A passing case without its
validated attachment fails the runner. Missing execution remains BLOCKED and
prevents a clean release verdict.

Use the existing manifest-bound command to execute and retain real evidence:

```sh
python e2e-runner/launch.py --env ephemeral --tier regression --manifest /path/to/release-manifest.json --out /path/to/new-results
```

`results.json` binds the case to the tested manifest hash, candidate images,
observed build and profile. Preserve that result and its normal cleanup outcome
with the existing release artifacts. Test collection and policy unit tests
establish source coverage. A digest-bound disposable candidate run is required
to close deployed sign-in/role/header acceptance.

## Account-side acceptance still required

An administrator must select the CodeQL tool and an agreed alert/security
severity threshold in an effective `main` code-scanning ruleset, review bypass
principals, and verify the result with an authorized pull request. GitHub
supports a [required code-scanning result rule](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-code-scanning-results).
Analysis success and a required analysis job alone do not prove that selected
finding severities prevent merge. Capture the effective rule and actual blocked
check evidence after that separate administrative change. This implementation
does not change repository settings or claim that enforcement is already active.
