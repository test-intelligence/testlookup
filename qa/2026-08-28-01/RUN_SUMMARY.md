# QA Run 2026-08-28-01

Status: DEPLOYMENT VERIFIED — source fixes reviewed, tested, built, and deployed to homelab; GitHub publication and merge are pending remote PR checks.

Confirmed defects: 2 (both S3). Source fixes: 2. Changed regression tests: 12/12 pass together. Quality gates: 30/30 pass. Homelab baseline: healthy. Full suites retain documented pre-existing Windows harness failures and one intermittent frontend full-suite timeout.

Homelab deployment completed with immutable image tag `build-20260829-015431`; all application and infrastructure workloads became Ready. Live probes passed for liveness, readiness, dependency details, OpenAPI, and frontend smoke. No code has been merged to `main` yet; the next action is to publish the reviewed branch, wait for required GitHub checks, and merge only if those checks remain green.

Known baseline exceptions remain documented: Windows-only backend shell/WSL harness failures and one frontend full-suite timeout that passes in isolation. They are outside the changed code paths. Build provenance fields still report `unknown`; the exact image tag and frontend registry digest were recorded during deployment.
