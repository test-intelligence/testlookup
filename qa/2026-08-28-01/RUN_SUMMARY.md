# QA Run 2026-08-28-01

Status: BLOCKED — source fixes reviewed and tested; deployment, verification, and GitHub merge are pending build and authentication prerequisites.

Confirmed defects: 2 (both S3). Source fixes: 2. Changed regression tests: 12/12 pass together. Quality gates: 30/30 pass. Homelab baseline: healthy. Full suites retain documented pre-existing Windows harness failures and one intermittent frontend full-suite timeout.

No code was merged to `main`, and the reviewed branch was not published because GitHub credentials are invalid/unavailable. The next action is to provide a container build path and GitHub credentials, then deploy and verify the cumulative branch before merging.
