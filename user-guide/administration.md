# Administration & settings

The admin surface splits into three concerns: **people & projects**, **connecting TestLookup to your world**, and **operating the instance**. Most pages under `/settings/*` require admin access and are project-aware (the top-bar project selector scopes what you're configuring).

## People & projects

- **Projects** (`/projects`) — create, describe, and archive test projects. Each project is the tenancy boundary: its own runs, owners, policies, API keys, and caches. Creating a project also auto-provisions its default QA-lead user (see [Test management](test-management.md#the-default-qa-lead)).
- **Users** (`/users`) — accounts, roles (ADMIN / QA_LEAD / QA_ENGINEER / …), avatar, and API-key expiry. Roles gate what pages show: e.g. only admins get "All Projects", only leads+ get the Mine/Team triage toggle.
- **Project members & ownership** — per-project membership lives on the project's members tab; failure-routing ownership lives in [Test management & ownership](test-management.md#ownership-who-gets-the-failure).
- **Profile** (`/settings/profile`) — your own name, email, avatar color, password.
- **SSO** (`/settings/sso`) — single sign-on configuration (per-tab config/domain settings).

## Connecting to your world

- **API Keys** (`/settings/api-keys`) — create keys for the CLI, SDKs, CI, and MCP; scope and expiry per key.
- **Integrations** (`/settings/integrations`, `/settings/github`) — outbound integrations (GitHub, Jira, notification channels). Everything here is **subordinate to `AI_OFFLINE_MODE`**: in the default offline install these are inert regardless of flags — enable outbound mode deliberately before expecting webhooks or ticket creation to fire.
- **Webhooks** (`/settings/webhooks`) — outbound event webhooks with delivery history.
- **Notifications** (`/settings/notifications`) — channel + per-event preferences; what lands in the bell menu vs email.
- **Digests** (`/settings/digests`) — scheduled summary digests (daily/weekly roll-ups to a channel). Email digests can optionally carry an **attached analysis report** (see below).
- **Attached analysis report** — check *Attach analysis report* on a daily/weekly **email** digest subscription and each delivery carries `testlookup-report-<project>-<date>[-weekly].html`: a single self-contained HTML file (inline styles + SVG charts, no external assets, works offline) with the full window analysis — executive summary (unique tests across the window, same semantics as the Summary Report/Coverage pages, plus a day-by-day sparkline on weekly), runs table, new-vs-recurring failures with top failing tests and clusters, the failure-kind triad, flaky & quarantine debt, slowest tests, the latest release-gate verdict, open linked defects (including the "closed in Jira but still failing" badge), and failures grouped by owning team. Daily digests cover 1 day; weekly cover 7. The subscription must be project-scoped; a failed report build never blocks the digest itself (it arrives with an apology note instead). Slack/Teams digests are unchanged apart from a one-line pointer at the email attachment. The same document is downloadable on demand from the [Summary Report](dashboards.md#summary-report-reportssummary) page or `GET /api/v1/projects/{id}/reports/analysis?window=1d|7d` (QA Engineer+).
- **Integration Health** (`/settings/integration-health`) — the status board for everything above: which integrations are configured, reachable, and delivering. Check here first when "the webhook didn't fire".

### GitHub (`/settings/github`)

Per-project: one repo (`owner/name` + API base URL for GitHub Enterprise), one PAT (stored encrypted; needs `repo` scope or fine-grained `checks:write` + `issues:write`). Gated by the `github_checks` feature flag and, as always, `AI_OFFLINE_MODE`. Two outbound surfaces:

- **Check runs** — every ingested run with a full 40-char commit SHA posts a check run (pass/fail counts + deep link to Run Intelligence) next to the commit's CI results. The check carries up to **50 per-test annotations** (GitHub's per-request cap — overflow is noted in the check text): newly-failed tests first (vs the same baseline the PR comment uses), then remaining failures by cluster size, with known-flaky failures annotated at `warning` level instead of `failure`. File/line locations are best-effort parsed from stack traces (Python and JS/TS traces with repo-relative paths resolve; Java surefire frames carry only a bare file name, so those failures — and anything else without a derivable location — are listed in the check's text instead of being pinned to a guessed path). The check summary shows the same **newly failed / known flaky / fixed** counts as the PR comment. **Flaky-aware conclusion:** a run whose failures are *all* known-flaky/quarantined concludes `neutral` (titled "N failures — all known-flaky/quarantined") instead of `failure` — mirroring the `ci-verdict` semantics on the Checks surface; any real failure still concludes `failure`. This behavior is always on when check runs are enabled (no separate toggle).
- **PR summary comment** — when a run arrives with PR context (repo + PR number, [auto-detected by the SDKs/CLI in CI](getting-results-in.md#ci-context-prs)) matching the configured repo, TestLookup keeps **one sticky comment** on that PR: **newly failed** tests vs the baseline run (latest completed run on `main`/`master`, falling back to the latest run on another branch), **known flaky** failures (quarantined tests + flaky-coach detections — labeled as likely not caused by the PR), and tests the PR **fixed**, plus a still-failing-on-baseline count. Re-runs update the same comment — never a second one. The **PR summary comment** mode selector controls it: `off`, `failures_only` (default — but a PR that went red→green still gets its existing comment updated to green), or `always`.

Delivery problems land in the page's **Last error** banner and on Integration Health.

### GitLab (`/settings/gitlab`)

Per-project GitLab integration for self-managed instances and gitlab.com — the same shape as GitHub, for teams whose VCS is GitLab. One **base URL** (defaults to `https://gitlab.com`; set `https://gitlab.mycorp.com` for a self-managed instance — the API root is `{base}/api/v4`), one **project path** (`group/project`, or a numeric project id), and one **PAT** (stored encrypted via the secret service, never returned by the API — the config only reports `has_token`; needs `api` scope, or a project/group access token with the same). Gated by the `gitlab` feature flag and, as always, `AI_OFFLINE_MODE`. Two outbound surfaces, both posted server-side after a run finalizes (no GitLab token in your CI):

- **MR note** — when a run arrives with merge-request context (`CI_PROJECT_PATH` + `CI_MERGE_REQUEST_IID`, [auto-detected by the SDKs/CLI in GitLab CI](getting-results-in.md#ci-context-prs)) matching the configured project path, TestLookup keeps **one sticky note** on that MR: **newly failed** tests vs the baseline run, **known flaky** failures, and tests the MR **fixed**, plus a still-failing-on-baseline count — the *identical* body, baseline selection, and classification as the GitHub PR summary comment (they never disagree). Re-runs update the same note (keyed by a hidden marker) — never a second one. The **Merge-request comment** mode selector controls it: `off`, `failures_only` (default — a red→green MR still gets its existing note updated to green), or `always`.
- **Commit status** — when enabled (**Commit status** toggle, on by default), every ingested run with a commit SHA posts a pipeline commit status on that commit (`success` when green, `failed` on any failure) with a deep link back to Run Intelligence, so the MR's pipeline widget reflects TestLookup's verdict.

The repo guard is strict: an MR IID is project-scoped, so a run's `ci_repo` must equal the configured project path (case-insensitive) before anything is posted. Use the **Test connection** button to confirm the base URL + PAT resolve the project (it calls `GET /api/v4/projects/:path` and reports the resolved numeric id). CI recipe: [`reference/testlookup-gitlab-ci.yml`](reference/testlookup-gitlab-ci.yml) covers both the JUnit-artifact upload and the live-streaming SDK path. Delivery problems land in **Last error** and on Integration Health.

## Operating the instance

- **AI Settings / AI Evaluation** (`/settings/ai`, `/settings/ai-eval`) — covered in [AI features](ai-features.md#configuring-the-ai-tier-settingsai-settingsai-eval): mode, local model, budgets, and the AI-quality dashboard.
- **Feature Flags** (`/settings/feature-flags`) — runtime toggles (e.g. `manual_upload`, quarantine features). Admin-only; flags are cached, so allow a moment for changes to propagate.
- **Audit Dashboard** (`/settings/audit`) — the tenant audit flow: who did what, when — gate overrides, quarantine decisions, admin mutations. Backed by append-oriented audit storage, so it survives UI changes.
- **Storage** (`/settings/storage`) — object-storage (MinIO/S3) status for reports, compliance packs, and RAG documents.
- **Project Data** (`/settings/project-data`) — per-project data management: retention and cleanup operations. Admin-only and deliberate — actions here delete data.
- **Seed Data** (`/settings/seed-data`) — load the demo dataset into a project (the same data `make quickstart` ships), useful for evaluating features or training a team without real CI wired up.
- **Performance** (`/settings/performance`) — instance performance diagnostics.
- **Billing** (`/settings/billing`) — usage/spend views (AI spend also surfaces in the [Intelligence Hub](ai-features.md#run-intelligence-intelligence-runsidintelligence)).

## Data retention & purge

Per-project retention policies keep disk usage bounded without giving up your audit trail. Policies are **off by default** — nothing is ever purged until an ADMIN enables a project's policy. Everything is served by `GET/PUT /api/v1/projects/{id}/retention-policy` plus the `preview` and `purge` sub-endpoints.

Four retention classes, each with its own clock (days):

| Class | Default | What it covers |
|---|---|---|
| Raw events | 90 | Live-stream event archives on runs (`event_archive`), raw ingest payloads (Allure/TestNG/REST) and live event documents in MongoDB. |
| Runs | 365 | Test runs and everything that hangs off them in PostgreSQL (test cases, analyses, clusters, decisions — the full cascade), plus run-scoped MongoDB documents (execution logs, pod events, run summaries, AI analysis traces). |
| Artifacts | 180 | Object storage: raw report uploads under the run's prefix and pipeline intermediate artifacts. |
| Audit | 2555 (~7 y) | Access/test-management audit rows, AI provenance records, the immutable pipeline event log, and **expired** compliance packs. Must be ≥ the runs window — audit records always outlive the runs they describe. |

Bounds: raw events/artifacts 7–3650, runs 30–3650, audit 365–3650 days.

**Preview-first workflow (recommended):**
1. `POST .../retention-policy/preview` — a synchronous dry run that returns the per-class cutoffs and exactly what a purge would delete right now (runs, test cases, per-collection Mongo docs, object counts, audit rows, expired packs). Preview works while the policy is still disabled and writes nothing — it's how you decide what windows to set.
2. Tune the windows with `PUT .../retention-policy` and enable the policy.
3. Either wait for the nightly sweep (02:00 UTC, enabled projects only) or trigger `POST .../retention-policy/purge` with the project's name typed as `confirmation_name` (mismatch → 422; disabled policy → 409). The purge runs in the background worker.

Every execute-mode purge — scheduled or manual — writes a **purge-audit record** (`settings_audit_log`, key `retention_purge:<project_id>`) with the cutoffs, per-store deletion counts, duration, and any errors. The latest one is surfaced as `last_purge` on the policy GET.

**What is NEVER purged:**
- `settings_audit_log` itself — it holds the purge-audit records and all settings/secret change history; it has no retention window at all.
- Compliance packs **before** their own `retention_expires_at` (default ~7 years, set at generation time). The audit-class pass only removes packs whose own expiry has passed.
- Anything in a project whose policy is disabled.

Notes: the purge is re-entrant — if a sweep is interrupted mid-way, the next run picks up the remainder (deletions across PostgreSQL/MongoDB/object storage are idempotent). Setting the raw-events window below 15 days will strip live-run event archives that the live-run recovery safety net could otherwise still replay. For a full, immediate wipe of a project use **Project Data** reset instead — retention is for steady-state aging, not resets.

## Backup, restore & upgrades

Day-2 operations are one command each — the scripts live in `scripts/ops/` and drive everything through `docker compose exec/run`, so the host needs nothing beyond docker + bash (on Windows, run them through `make`, which already uses Git Bash). For how much machine and disk to plan for, see the [sizing & capacity guide](sizing.md).

### Backup — `make backup`

Produces **one timestamped archive** under `./backups/` (gitignored) containing a `pg_dump` of PostgreSQL, a `mongodump` of MongoDB, a tar of the MinIO data volume, and a `manifest.json` recording the app version, git SHA, and Alembic migration head. **Redis and ChromaDB are excluded by design** — Redis is the broker/cache (stale queue entries must not be replayed into a restored database) and ChromaDB is rebuilt from Postgres by the hourly reindex beat.

```bash
make backup                      # → backups/testlookup-backup-<UTC>.tar.gz
QUIESCE=1 make backup            # stop the app layer during the backup (maximally consistent)
BACKUP_DIR=/mnt/nas make backup  # custom destination
```

The database dumps are transactionally consistent even while the stack is live. The MinIO volume tar is file-level — if CI is actively uploading attachments, prefer a quiet moment or `QUIESCE=1`. Copy the archives off-host on your own schedule (cron + rsync is plenty).

### Restore — `make restore`

```bash
make restore FILE=backups/testlookup-backup-<ts>.tar.gz
```

Restore stops the app containers, replaces the PostgreSQL/MongoDB/MinIO data, flushes Redis, restarts the stack (the backend applies any pending migrations on boot), waits for readiness, runs the smoke check, and prints a verdict plus a post-restore checklist (ChromaDB reindexes itself within the hour; the checklist includes the command to trigger it immediately).

**The migration-safety gate:** restore *refuses to run* when the deployed code's migration head is **newer** than the backup's — that means you're rolling data back in time under code that has since migrated forward, which is usually an accident. It also refuses when the backup's head is unknown to the deployed code (backup from a *newer* version — upgrade first). Both refusals explain themselves and can be overridden with `FORCE=1` when the rollback is intentional:

```bash
make restore FILE=backups/<name>.tar.gz FORCE=1 CONFIRM=yes   # non-interactive override
```

### Upgrade — `make preflight` then `make upgrade`

```bash
make preflight TAG=v0.2.0   # read-only: current vs target images, pending migrations, disk headroom, newest backup
make backup                 # your rollback path — do not skip this
make upgrade TAG=v0.2.0     # pull, migrate, restart in dependency order, health-check, smoke-test
```

`make upgrade` pulls the target images (`TAG=` applies to the release stack, `docker-compose.release.yml`; the dev stack rebuilds from source instead), runs `alembic upgrade head` *before* replacing the app containers so a migration failure is loud and early, recreates services in dependency order, and verifies health with the same smoke check as `make smoke`. On failure it prints step-by-step rollback instructions referencing your pre-upgrade backup. **There is deliberately no automatic rollback**: data migrations are not safely auto-reversible (downgrades can drop backfilled data or fail halfway), so the trustworthy rollback is the pre-upgrade backup restored onto the previous image tag — which is exactly what the printed instructions walk you through.

## Admin checklists

**New instance** (after [GETTING_STARTED](../GETTING_STARTED.md)):
1. Create your project(s) and invite users with the right roles.
2. Create API keys for CI and hand teams the [ingestion guide](getting-results-in.md).
3. Configure suite owners / ownership rules so triage routes to people.
4. Review the default release-gate policy (`/policies`) and adjust bands/rules.
5. Leave `AI_OFFLINE_MODE` on until you deliberately need outbound integrations.

**Something isn't arriving?** Integration Health → Audit Dashboard → the [ingestion troubleshooting list](getting-results-in.md#troubleshooting), in that order.
