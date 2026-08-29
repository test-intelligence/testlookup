# Action Register — QA Run 2026-08-29-02

| ID | Priority | Status | Owner area | Finding | Recommended action | Validation |
|---|---|---|---|---|---|---|
| QA-2026-08-29-02-001 | P1 | Completed | SDK/CLI commit attribution | Explicit plain `repo_path` could collect parent checkout history. | Require `.git` metadata at an explicitly supplied checkout root in both collectors. | 161 commit-range tests and 254 CLI/SDK tests pass. |
| QA-2026-08-29-02-002 | P2 | Deferred | Integration/UAT | Stateful upload, release, quarantine, integration, export, and account flows need a running stack. | Run credentialed read/write UAT in Docker or deployed homelab and retain artifacts. | Live browser/API workflow evidence. |
| QA-2026-08-29-02-003 | P2 | Environment | Windows test harness | Git Bash child processes cannot access required `cat`/`dirname`/host `python3` under this sandbox. | Re-run backend shell/quickstart tests on Linux CI or a fully provisioned Git-for-Windows environment. | Backend suite with no shell-toolchain failures. |

