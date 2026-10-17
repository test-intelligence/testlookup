# Administration & settings

The operator side of running TestLookup: who can do what, people and projects, sign-in (SSO, SCIM, MFA, lockout), integrations and notifications, the AI agents, commit attribution, retention, and backup, restore and upgrades.

What each settings page shows, field by field, is in the in-app guide: **Settings and administration** (`/docs/administration` on your instance, source [administration.md](../frontend/src/content/guide/administration.md)). This page does not repeat it; it covers the decisions and procedures behind those pages.

## Roles and access

Five roles, each including the abilities of those below it: `VIEWER` < `TESTER` < `QA_ENGINEER` < `QA_LEAD` < `ADMIN`. Project membership decides which projects a user sees; the role decides what they can do there.

- **The settings area** (the sidebar's **Admin**, `/settings` and every settings page except **Profile**, **My notifications** and **My API keys**) needs **`QA_LEAD` or `ADMIN`**.
- **Administrator only, read and write**: **SSO & identity**, **Feature flags**, **MFA & lockout**, **Retention & purge**, **Project data** (reset), changing user roles and project membership, generating streaming (project-bound) API keys.
- **QA leads can read, administrators change**: **AI configuration**, **Integrations** (Jira, Splunk, OpenShift, Slack, Teams), **Email (SMTP)**, **Data & storage**, **LLM cost budget**, **Gate policies**, the **AI evaluation** write actions, and the audit dashboard's CSV export.
- **QA leads**: projects, releases and their phases, linking runs to releases, ownership rules, team channels, outbound webhooks, quarantine decisions, AI-report reviews, gate overrides, agent policies, workflows.
- **QA engineers**: sending results, owning API keys, triage, corrections, defects, suites and test cases.

Some pages show a button to a QA lead that the API then refuses (for example **Save** on Email (SMTP), the audit **Export CSV**, editing gate policies, deleting a release). The API's rule is the one listed above.

## People & projects

- **Projects** (`/projects`) — create, edit and archive. Each project is the tenancy boundary: its own runs, owners, policies, API keys and caches. Creating a project makes the creator a member and auto-provisions the project's default QA-lead user (see [Test catalog governance and ownership](test-management.md#the-default-qa-lead)). **Archive** is a soft delete that also revokes the project's credentials; there is no un-archive in the app.
- **Users** (`/users`) — QA leads see the list; administrators **Add User** (a temporary password, shown once, that must be changed at first sign-in), change roles inline, edit, and activate or deactivate accounts. Roles gate what pages show: only administrators get **All Projects**, and only QA leads and administrators get the team scope in the Inbox.
- **Members & access** (`/users?tab=project-members`) — per-project membership and roles, and the project's default QA lead. Administrators add, change and remove members; QA leads can reset the default QA lead's password.
- **Self-registration** is off by default (`REGISTRATION_ENABLED=false`): the sign-in page hides **Register** and `POST /api/v1/auth/register` answers 403. Turn it on only if everyone who can reach the instance may have an account. A self-registered account is a `VIEWER` in no project and must change its password at first sign-in; it sees no data until you add it to a project. The endpoint is rate-limited to 5 a minute.
- **Invitations.** **Invite User** records an invitation, but there is not yet a page that accepts it; create accounts with **Add User** instead.
- **Development quick login.** The sign-in page's passwordless "quick login" panel exists only in development frontend builds, and the backend honours it only with `APP_ENV=development` and `DEV_AUTO_LOGIN_ENABLED`. A production build never shows it.

## Single sign-on (SAML) and SCIM

TestLookup supports **SAML 2.0** single sign-on (no OIDC or LDAP) and **SCIM 2.0** provisioning. Both are off until the deployment enables them:

| Setting | Default | Meaning |
|---|---|---|
| `SSO_ENABLED` | `false` | Turns SAML sign-in on |
| `SCIM_ENABLED` | `false` | Turns the SCIM 2.0 API (`/api/v1/scim/v2`) on |
| `SAML_SP_ENTITY_ID` | a placeholder URL | TestLookup's entity ID as the service provider; set it to your own URL |
| `SAML_BASE_URL` | `http://localhost:8000` | The public base URL used to build the ACS URL; set it to your instance's public URL |
| `PUBLIC_BASE_URL` | first `CORS_ORIGINS` entry | The URL users open; after the identity provider signs someone in, the browser returns to `<PUBLIC_BASE_URL>/sso/callback` |
| `SAML_ALLOW_IDP_INITIATED` | `false` | Accept identity-provider-initiated sign-ins (weaker replay protection) |
| `SAML_CLOCK_SKEW_SECONDS` | `60` | Allowed clock drift |
| `SSO_ADMIN_FALLBACK_ENABLED` | `true` | Administrators may still use password sign-in when SSO is required |
| `SSO_ALLOW_PRIVATE_IDP_ENDPOINTS` | `false` | Let **Test Connection** reach an identity provider on a private address |
| `SSO_MAX_PROVISIONED_ROLE` | `ADMIN` | The highest role SSO or SCIM may grant; set it lower (for example `QA_LEAD`) to refuse identity-provider-driven admin grants |

Where these go for your deployment is in the [configuration reference](../docs/operations/configuration.md).

Then, in **Settings → Security & access → SSO & identity** (administrators):

1. **SSO Configuration**: enter the identity provider's **IdP Entity ID**, **IdP SSO URL** and **IdP Certificate (PEM)**; TestLookup's **SP Entity ID** and **SP ACS URL** (`<SAML_BASE_URL>/api/v1/sso/acs`; the SP metadata is at `/api/v1/sso/metadata`); an optional **Group Attribute**; the enforcement (**Optional (password login allowed)** or **SSO Required (admin fallback only)**); and the **default role** for new SSO users (Viewer, Tester, QA Engineer or QA Lead). Mapping identity-provider groups to roles (`role_mapping`) is set through the SSO configuration API (`/api/v1/sso/configs`).
2. **Test Connection** checks the certificate and reaches the identity provider's SSO URL.
3. **Activate**. Activating an **SSO Required** configuration asks for confirmation, because password sign-in then works only for administrators (and only while `SSO_ADMIN_FALLBACK_ENABLED` is on).
4. For provisioning, **SCIM Tokens → Generate Token** (shown once) and give it to the identity provider with the SCIM base URL `<your API URL>/api/v1/scim/v2`.

Sign-in is started by TestLookup: **Sign in with SSO** sends a SAML request to the identity provider and returns the user to the page they started from. TestLookup's requests are unsigned, so an identity provider that insists on signed requests will refuse them. There is no single logout: signing out of TestLookup does not sign the user out of the identity provider.

**Identity Events** shows sign-ins, provisioning and role changes for the last 30 days; **Sync Status** shows federated users, the last SSO sign-in and SCIM sync, and recent failures. SSO-managed accounts are exempt from the MFA requirement below.

## Multi-factor authentication & account lockout

Shipped 2026-08-05 (migration 0117). Before this, MFA had to come from your IdP over SAML and there was no lockout at all.

### Enrolling

Anyone can turn on MFA for their own account (**My profile → Two-Factor Authentication**), whether or not policy requires it.

1. Start enrollment. The server returns a base32 secret and an `otpauth://` provisioning URI; the UI renders it as a QR code and also shows the secret so you can type it into an authenticator that cannot scan.
2. Enter a six-digit code from the authenticator to confirm. **Nothing changes until you do** — an abandoned enrollment leaves the account exactly as it was.
3. Confirming enables MFA and shows **ten recovery codes, once**. Only their digests are stored, so nobody — including an admin with database access — can recover them later. Save them somewhere that is not the phone holding the authenticator.

Codes are standard RFC 6238 TOTP: six digits, 30-second period, and one step of clock drift tolerated in either direction. A code that has already been accepted cannot be used again, even within its own 30-second window.

### Signing in with MFA

Password first, then a second factor. The intermediate token the server hands back between the two steps is **not** a session — it is only accepted by the verification endpoint, and it expires in five minutes and works exactly once. At the second step you can enter either an authenticator code or one of your recovery codes.

### Recovery codes

- Ten codes, single-use, formatted `XXXX-XXXX-XXXX-XXXX`. Spacing and case do not matter when you type one back.
- Using one consumes it permanently. The response tells you how many are left.
- You can reissue the whole set at any time (password + a live second factor required). **Reissuing invalidates every previous code**, including unused ones.
- Running out is not a lock-out on its own — you can reissue while you still have your authenticator. It becomes a lock-out only if you lose the authenticator *and* have no codes left, which is what breakglass is for.

### Turning MFA off

Requires your password **and** a live second factor. If workspace policy requires MFA for your role, you cannot turn it off — an admin has to change the policy or use breakglass.

### Workspace policy (admin)

**Settings → Security & access → MFA & lockout** (administrators). Workspace-wide:

| Setting | Default | What it does |
|---|---|---|
| Require MFA to sign in | off | Master switch for the requirement. Enrollment is always *available* regardless. |
| Applies to | everyone | MFA is required for the chosen role **and every role above it**, or for everyone. |
| Lock accounts after repeated failures | **on** | See below. |
| Failed attempts before lockout | 10 | Consecutive failures before the account locks (3–100). |
| Lockout duration | 15 min | How long the lock lasts (1–1440 minutes). |

Turning the requirement on takes effect immediately for new sign-ins, and within one access-token lifetime for sessions that are already live — the token-refresh path re-checks the policy and sends affected users back to the login screen, where they are handed an enrollment challenge instead of a session.

**SSO-managed accounts are exempt from the requirement.** Any account with a federated identity link, or one that was provisioned over SCIM, is skipped: your IdP already owns that account's authentication. SAML sign-in (`/api/v1/sso/acs`) does not consult this policy at all. An SSO-managed user who enrolls voluntarily is still challenged when they use the local password path.

**API keys are never challenged.** A key embedded in a CI pipeline cannot type a TOTP code, so keys keep working when you turn the policy on. The flip side is that a key is a single-factor way into the account, so treat key issuance as a sensitive grant — keys are per-user, listed under **My API keys** (and, for project-bound keys, **Streaming API keys**), and revocable. Keys *can* read MFA status but cannot enroll, disable, or reissue recovery codes.

### Account lockout

**This is on by default.** After 10 consecutive failed attempts — failed passwords and failed second factors both count — the account locks for 15 minutes and answers `429` with a `Retry-After` header even for the correct password.

- A **completely** successful sign-in resets the counter. A correct password followed by a failed second factor does not.
- Failures while the account is already locked do **not** extend the lock, so someone hammering an account cannot keep its owner out indefinitely.
- The lock expires on its own. There is nothing to clear manually in the normal case.
- Counters live on the user row in PostgreSQL, so the limit is genuinely global — it is not per-worker and it survives a restart. (The separate per-IP rate limits on the auth endpoints are in-process and per-worker; they are anti-automation, not account protection.)
- Attempts against a username that does not exist are never counted. Only the per-IP rate limit applies there.
- Every lock writes an `ACCOUNT_LOCKED` row to the identity event trail.

`identity_events` has **no retention clock** — the per-project purge does not touch it, because the table has no project scope. If a sustained attack inflates the table, prune it out-of-band.

### Breakglass — lost authenticator, no recovery codes left

There is deliberately no admin button for this: an endpoint that resets *someone else's* factor is an account-takeover primitive. It is a script, run with deployment-level access:

```bash
# Inside the backend container:
MFA_BREAKGLASS_ENABLED=true python /app/scripts/mfa_breakglass.py \
    --user alice@example.com \
    --operator "on-call: sam" \
    --reason "lost phone, recovery codes exhausted, identity verified by video"

# Kubernetes:
kubectl -n testlookup exec -it deployment/testlookup-backend -- \
    env MFA_BREAKGLASS_ENABLED=true python /app/scripts/mfa_breakglass.py \
    --user alice --operator "sam" --reason "..."

# Inspect without changing anything (no env gate needed):
python /app/scripts/mfa_breakglass.py --user alice --dry-run
```

- **`MFA_BREAKGLASS_ENABLED` must be set in the backend environment.** It is environment-only, defaults to false, and there is no in-app toggle. This mirrors `SSO_ADMIN_FALLBACK_ENABLED`.
- `--reason` is mandatory. It is written to the audit trail.
- The reset clears MFA (flag, seed, and every recovery code) and any active lockout, in one transaction with an `MFA_BREAKGLASS_RESET` identity event.
- `--keep-lockout` clears MFA but leaves an active lock in place.
- The user then signs in with their password alone. **If policy requires MFA for their role, their very next login hands them an enrollment challenge.**

### If MFA stops working for everyone at once

One failure mode is worth recognising on sight: **`503` at sign-in with "MFA is enabled but its stored secret cannot be read"**.

TOTP seeds are encrypted with a key derived from `APP_SECRET_KEY`. If that key is rotated without carrying the old one in `APP_SECRET_KEY_PREVIOUS`, every enrolled seed becomes undecryptable. TestLookup refuses those logins rather than treating an unreadable seed as "this user has no MFA". Fix it by restoring the previous key in `APP_SECRET_KEY_PREVIOUS` and restarting. If the old key is genuinely gone, use breakglass per affected user and have them re-enroll.

## Connecting to your world

Everything outbound — Jira, Slack, Teams, email to an outside relay, outbound webhooks, GitHub, GitLab, a hosted LLM — is **subordinate to `AI_OFFLINE_MODE`**, which defaults to `true`: in a default install these are inert regardless of their own settings. Turn outbound mode on deliberately, or use the narrow allow-lists (`OFFLINE_NOTIFICATION_ALLOWED_HOSTS` for the deployment-wide Slack, Teams and SMTP destinations; `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS` for email recipients). Every outbound client honours a corporate CA bundle (`HTTP_CA_BUNDLE`). The environment variables for each integration are in the [integrations reference](../docs/operations/integrations.md).

### Integrations (`/settings/integrations`)

One card per integration; QA leads can read, administrators save. Tokens and webhook URLs are stored encrypted; a value saved here wins over the environment variable, and an empty secret field clears the stored one so the environment value applies again.

| Integration | Fields | Used for |
|---|---|---|
| **Jira** | Domain, Email, Default Project Key, API Token | One-click issues, recurrence notes, status sync-back. See [Issue trackers and defect automation](defects.md) |
| **Splunk** | Base URL, API Token | Log evidence for the analysis pipeline |
| **OpenShift / Kubernetes** | API URL, Default Namespace, Service Account Token | Pod and event evidence for the analysis pipeline |
| **Slack** | Webhook URL, Default Channel | The shared notification channel and digests |
| **Microsoft Teams** | Webhook URL | The shared notification channel and digests |

There is no test button on this page; **Integration health** probes each connection.

### Email (SMTP) & channels (`/settings/notifications`)

The SMTP relay: host, port, from address, optional username and password, and **Implicit TLS** (port 465) or **STARTTLS** (587 or 25); plain SMTP is not supported. **Send test email** sends to your own address using the **saved** settings, and applies the offline allow-lists. Saving needs an administrator. Each person's event choices live on their own **My notifications** page, which can also point at a personal Slack or Teams webhook (personal and team webhooks are not covered by `OFFLINE_NOTIFICATION_ALLOWED_HOSTS`).

**Project notification policy** (the third card, `QA_LEAD`; `GET/PUT /api/v1/notifications/projects/{project_id}/transition-policy`, also the MCP `get_transition_policy` / `set_transition_policy` tools): for the project selected in the top bar, which events are sent at all, whatever each person ticked. A project with no saved policy reads "Using defaults: state changes ON, every run OFF."; a project that existed before migration 0103 has an explicit row with state changes off and every run on. Each change is recorded in the activity feed as `notification_policy.updated` ("Notification policy changed: every run").

| Switch (field) | Governs |
|---|---|
| **Notify on every run** (`per_run_events_enabled`) | `run_failed`, `run_passed`, `high_failure_rate` |
| **Notify on state changes** (`transitions_enabled`) and the seven boxes (`enabled_events`) | `test.newly_failing`, `test.recovered`, `test.newly_flaky`, `test.quarantined`, `test.unquarantined`, `test.quarantine_stale`, `test.ready_to_unquarantine` |
| **Failed runs in a row before "Test newly failing"** (`consecutive_failure_threshold`, 1-20, default 2) | When `test.newly_failing` fires |
| (not governed) | `ai_analysis_complete`, `quality_gate_failed`, `test.newly_slow`, `suite.newly_slow` |

On **My notifications**, each event the selected project's policy will not send carries a note ("Not sent in Checkout: per-run notifications are off"). If the policy cannot be read the dispatcher sends anyway (it fails open), so no note is shown.

**Quality gate failed** (`quality_gate_failed`) is delivered on a recorded transition into `NO_GO`: a release or phase gate evaluation (`POST /api/v1/releases/{id}/gate/evaluate`, or the phase route, with `record=true`) whose previous recorded verdict was not `NO_GO`; a run's release-readiness decision published as `NO_GO` by the analysis pipeline (not while the review gate withholds it as `PENDING_REVIEW`); or a QA lead's override into `NO_GO`. One delivery per subscriber and channel per decision; evaluating again while still `NO_GO` adds nothing. The in-app entry links to `/releases/<id>` (QA lead and above) or `/release-gate/<run>`; email, Slack and Teams go through the relay, offline-gated as every notification. The legacy event `flaky_test_detected` never had a producer: migration 0220 rewrote stored preferences to `test.newly_flaky`, and the API stores it as `test.newly_flaky` from now on.

### Team channels (`/settings/team-channels`)

For each team named in the ownership rules, a Slack webhook, Teams webhook or email destination (`QA_LEAD`). A failing test's alerts go to the owning team's channel; a cluster goes to the team owning more than half of its tests; anything else (no channel, unowned, mixed ownership, failed delivery) falls back to each person's own notification settings.

### Outbound webhooks (`/settings/webhooks`)

Per-project subscriptions that POST events to your own HTTPS endpoints.

> **Off by default.** Delivery needs the `outbound_webhooks` feature flag (ships **off**) and `AI_OFFLINE_MODE=false`. The page does not show a banner when the flag is off: **Test** answers "No delivery enqueued — check that outbound_webhooks is enabled, AI_OFFLINE_MODE is off…" and **Replay** answers `503`.

- **Events**: `run.completed`, `defect.promoted`, `defect.create_requested` (the Jira-dialog fallback, see [defects.md](defects.md#no-jira-use-the-webhook-fallback)), `release.decided`, `flaky.quarantined`, `quota.exceeded`.
- **Headers**: `X-TestLookup-Event` (the event type), `X-TestLookup-Delivery` (a delivery id), and, when the subscription has a secret, `X-TestLookup-Signature`: the hex HMAC-SHA256 of the raw request body, keyed with the secret. Verify it before trusting a payload.
- **Retries**: 408, 425, 429, 5xx and network errors are retried with backoff up to **Max retries** (default 5, at most 10); other 4xx responses fail at once. **Recent deliveries** lists the last 20 attempts, with **Replay** for failed ones.
- Creating, editing, testing and replaying need `QA_LEAD`.

### Digests (`/settings/digests`)

Scheduled summaries to email, Slack or Teams: **Daily** (dispatched at 07:00 UTC), **Weekly**, **Per Run** (all, failed only, or degraded only), **Per Release**, **Per Suite**, and **Weekly Retro** (see below).

- **Per Release** names one release of the subscription's project (`scope_type: "release"`, `scope_value`: the release id). It is sent after the AI summary of each run whose primary release, or any release it is linked to (`release_test_run_links`, a secondary link included), is that release. The API refuses a Per Release subscription without a release, with another project's release, or without a project (`422`); a Per Suite one needs a suite name. Subscriptions saved before this check that cannot deliver show **Not delivering — choose a release** (or **choose a suite**, or **its release no longer exists**) with the fix in their row.
- **Saved views.** A Daily or Weekly digest may name a saved **report view** of its project (`saved_view_id`; the views saved on Trends, Failures and the other reports, or made on the Saved Views tab). Its content is then filtered by the view's releases (the runs' primary release), suites (each run's effective suite) and window (when shorter than the period); the digest says so ("Filtered by view: …"), drops any release that no longer belongs to the project with a note, and leaves out the project-wide sections (flaky debt by team, hours saved). A view of another project, another user's unshared view, or a widget layout is refused (`422`). The release ids are re-checked against the project at every send.
- **Deleted views.** The subscription keeps the view's name (`saved_view_name`, migration 0221). When the view is deleted (`saved_view_id` becomes `NULL`) or its owner can no longer read it, the digest covers the whole project and says so in the row ("View deleted — this digest covers the whole project"), the preview and the delivered digest, until someone picks another view.
- **Preview Digest** takes the same view (`GET /api/v1/digests/preview?saved_view_id=…`).

**Attached analysis report**: tick *Attach analysis report* on a daily or weekly **email** digest for one project, and each delivery carries `testlookup-report-<project>-<date>[-weekly].html`: a single self-contained HTML file (inline styles and SVG charts, works offline) with the full window analysis — executive summary (unique tests across the window), runs, new-vs-recurring failures with top failing tests and clusters, the failure-kind split, flaky and quarantine debt, slowest tests, the latest release-gate verdict, open linked defects (including the "closed in Jira but still failing" badge), and failures grouped by owning team. Daily covers 1 day, weekly 7. A failed report build never blocks the digest itself. The same document is downloadable from the Summary report or `GET /api/v1/projects/{id}/reports/analysis?window=1d|7d` (`QA_ENGINEER` and above).

### Weekly retro digest (`weekly_retro_digest` flag)

A per-team "week in review". Off by default; enable the `weekly_retro_digest` flag, then pick the **Weekly Retro** schedule on a digest subscription. It is sent on Mondays shortly after 07:00 UTC.

Each edition composes the week's **top 5 failures** (ranked by count × severity), **recovered flaky tests** (quarantines released since last week), **new regressions** against the previous week's baseline, and a short **written summary**. The summary goes through the same LLM factory as everything else, so an air-gapped deployment gets a template-based summary with the same numbers. With the flag off, retro subscriptions are simply skipped.

### Integration health (`/settings/integration-health`)

The status board for everything above: Jira, Splunk, GitHub, OpenShift, Slack, Teams, SMTP, the local model server and the vector store, probed every 15 minutes (**Probe All Now** to probe at once). Check here first when "the webhook didn't fire", then the audit dashboard.

### GitHub (`/settings/github`)

Per-project: one repo (`owner/name` + API base URL for GitHub Enterprise), one PAT (stored encrypted; needs `repo` scope or fine-grained `checks:write` + `issues:write`). Gated by the `github_checks` feature flag (ships **off**) and, as always, `AI_OFFLINE_MODE`. Two outbound surfaces:

- **Check runs** — every ingested run with a full 40-character commit SHA posts a check run (pass/fail counts + a deep link to the run's Analysis tab) next to the commit's CI results. The check carries up to **50 per-test annotations** (GitHub's per-request cap — overflow is noted in the check text): newly failed tests first, then remaining failures by cluster size, with known-flaky failures annotated at `warning` level instead of `failure`. File/line locations are parsed from stack traces where possible (Python and JS/TS traces with repo-relative paths resolve; Java surefire frames carry only a bare file name, so those failures are listed in the check's text instead). **Flaky-aware conclusion:** a run whose failures are *all* known-flaky or quarantined concludes `neutral` ("N failures — all known-flaky/quarantined") instead of `failure`, mirroring `testlookup ci-verdict`; any real failure still concludes `failure`.
- **PR summary comment** — when a run arrives with PR context (repo + PR number, [auto-detected by the SDKs/CLI in CI](getting-results-in.md#ci-context-prs)) matching the configured repo, TestLookup keeps **one sticky comment** on that PR: **newly failed** tests vs the baseline run (latest completed run on `main`/`master`, falling back to the latest run on another branch), **known flaky** failures (labelled as likely not caused by the PR), and tests the PR **fixed**, plus a still-failing-on-baseline count. Re-runs update the same comment. The **PR summary comment** mode selector: `off`, `failures_only` (default — a PR that went red→green still gets its existing comment updated), or `always`.

Delivery problems land in the page's **Last error** banner and on Integration health.

### GitLab (`/settings/gitlab`)

Per-project GitLab integration for self-managed instances and gitlab.com. One **base URL** (defaults to `https://gitlab.com`; the API root is `{base}/api/v4`), one **project path** (`group/project`, or a numeric project id), and one **PAT** (stored encrypted, never returned; needs `api` scope, or a project/group access token with the same). Gated by the `gitlab` feature flag (ships **off**) and `AI_OFFLINE_MODE`. Two surfaces, both posted server-side after a run finalizes (no GitLab token in your CI):

- **MR note** — when a run arrives with merge-request context (`CI_PROJECT_PATH` + `CI_MERGE_REQUEST_IID`, [auto-detected in GitLab CI](getting-results-in.md#ci-context-prs)) matching the configured project path, TestLookup keeps **one sticky note** on that MR with the same body, baseline and classification as the GitHub PR comment. Mode selector: `off`, `failures_only` (default) or `always`.
- **Commit status** — when enabled (on by default), every ingested run with a commit SHA posts a pipeline commit status (`success` when green, `failed` on any failure) with a link back to the run.

An MR IID is project-scoped, so a run's `ci_repo` must equal the configured project path (case-insensitive) before anything is posted. **Test connection** confirms the base URL and PAT resolve the project. CI recipe: [`reference/testlookup-gitlab-ci.yml`](reference/testlookup-gitlab-ci.yml).

## Operating the instance

- **AI configuration, AI evaluation, LLM cost budget** — see [AI configuration](ai-features.md).
- **Feature flags** (`/settings/feature-flags`, administrators) — every flag row, with a global switch, rollout percentage and optional project and role lists. Values are cached for about 30 seconds. Several workflows ship **off** — `flaky_auto_quarantine`, `outbound_webhooks`, `github_checks`, `gitlab` — while `manual_upload` (dashboard upload), `ask_ai_chat`, `llm_cost_budget`, `release_phase_gate_enforcement` (a reason is asked for when a phase is completed over a failing gate or skipped) and `release_compliance_pack` ship **on** for a new install. An instance upgraded from a release where one of these was seeded off keeps that value; turn it on here. The full list and evaluation rules: [feature-flag reference](../docs/operations/feature-flags.md).
- **Audit dashboard** (`/settings/audit`) — who did what, when: gate overrides, quarantine decisions, settings and identity changes, report exports. Filter by category and 7/30/90 days. **Export CSV** needs an administrator.
- **Activity log** (`/activity`) — the per-project event ledger (runs, analysis, releases, configuration, membership, integrations, agents), searchable and filterable by actor type, with a before/after diff per event. **Export CSV** needs `QA_LEAD` on the project.
- **Data & storage** (`/settings/storage`) — connection status for PostgreSQL, MongoDB and the cache, the object-storage target (endpoint, bucket, SSL) and the vector store (host, port, collection). Values saved here override the environment defaults; QA leads read, administrators save.
- **Project data** (`/settings/project-data`, administrators) — **reset only**: **Delete test runs**, or **Wipe project data** (also the catalogue, releases, policies, baselines, test cases, plans, strategies and knowledge sources; the project, members, API keys, AI and SSO configuration and flag overrides stay). Both require typing the project name. There is no export or import; use [backups](#backup-restore--upgrades) for that.
- **Performance** (`/settings/performance`) — read-only latency budgets, search and pool configuration, and reference scale scenarios.
- **Seed data** — loads demo data; only in development builds against a backend with `APP_ENV=development`. In production use `make quickstart` on a separate demo instance instead.

## AI agents — autonomy, budgets, and the activity ledger

Two agents can act on a project's failures beyond the analysis that classifies every run: the **Investigator** (reasons about failure clusters) and the **Fixer** (proposes a code change for a flaky test). Their analysis-mode settings are in [AI configuration](ai-features.md).

**Defaults**: the Investigator is **enabled in Shadow mode** (it reasons and records what it would do, changing nothing); the Fixer is **disabled**. Moving either further is a deliberate decision.

### `/settings/ai-agents` — policy and budgets

Per project, per agent (saving needs `QA_LEAD`):

| Control | What it does |
|---|---|
| **Enabled** | Whether the agent runs at all |
| **Autonomy mode** | **Shadow** — run and record what it *would* have done; proposes nothing. **Suggest** — proposes actions that need explicit human approval; nothing executes without it. The proposals are served by the agent-actions API (`/api/v1/projects/{id}/agent-actions`, `QA_LEAD`); there is no approval page in the app yet. **Act** is not available yet |
| **Budgets** | Investigator: max runs a day, LLM calls, tokens and seconds per run (defaults 10 runs a day, 30 calls, 60,000 tokens, 300 seconds). Fixer: max tests per run, attempts per test, validation reruns, open PRs |
| **Schedule** (Fixer) | **Off (manual only)**, **Daily** or **Weekly** |

**Start in Shadow.** The agent does its full reasoning and records the actions it *would* have taken, so you can compare proposed-vs-taken over real failures before granting more. The page shows how many shadow runs have completed.

### `/settings/agent-activity` — what actually happened

Every agent run is ledgered: agent, mode, trigger, status, **actions proposed vs actions taken**, tokens, cost and duration, with the Fixer's attempts below. In Shadow mode, taken is always zero — the gap is the evidence for promoting the agent. Each run also records the digest of the prompt set that produced it, so a decision can be reproduced against the prompts as they were.

### The Fixer never merges

When enabled, the Fixer proposes a test-code-only patch and validates it by **re-running the failing test in an ephemeral sandbox**; only in **Suggest** mode, and only for a validated patch, does it open a **draft** pull request. It cannot merge, and a patch that fails validation is recorded as a failed attempt. See [Fixer](fixer.md).

> **Needs a reachable language model.** With no model the Investigator's hypothesis stages stay `pending` and the run reports `failed` — an honest unavailable state rather than a silent no-op.

## Commit attribution & TIA readiness

Every run can carry a **commit range** — the commits that landed between a baseline and the run's own commit. It powers suspect ranking on a failure (**Suspects** on the Failures page), and it is the raw material for any future test-impact analysis.

**Where the range comes from** (`source`):

| `source` | Meaning |
|---|---|
| `supplied` | Your CI/SDK pushed the commit list on ingest. No outbound VCS call — the air-gapped path. Always wins over the connector. |
| `connector` | Fetched from the configured GitHub integration (subject to `AI_OFFLINE_MODE` and the `github_checks` flag). |
| `unavailable` | Neither path yielded anything. Recorded explicitly, with no invented range. |

**What the range is measured *from*** (`base_source`):

| `base_source` | Strength | Meaning |
|---|---|---|
| `supplied` | strong | You told us the base ref. |
| `green_baseline` | strong | The last fully-green prior run on the branch. |
| `last_completed_run` | **weak** | The last *completed* prior run, pass or fail, used when the project has no all-green run. Treat it as "roughly the window", not a clean before/after. |
| `unavailable` | — | No base could be determined. |

The API exposes `base_anchor_is_strong` alongside the label.

**Changed-file detail.** `COMMIT_RANGE_FILE_FETCH_LIMIT` (default **25**) caps how many commits in a connector-fetched range get their changed-file list, because each is a separate GitHub API call. An authenticated PAT allows 5,000 GitHub REST calls an hour and one resolve costs `1 + limit` calls, so 25 supports about 190 runs an hour. Raise it only if you are deliberately building a test-impact corpus.

**Is there enough data to model with yet?** `GET /api/v1/metrics/tia-readiness?project_id=…&days=90` answers that for one project from its own runs: `available: true`, or `available: false` with an `insufficient_data_reason` naming the single thing to fix next. The thresholds it applies are returned with the answer.

## Data retention & purge

Per-project retention policies keep disk usage bounded without giving up your audit trail. Policies are **off by default** — nothing is ever purged until an administrator enables a project's policy under **Settings → Project → Retention & purge**. The API is `GET/PUT /api/v1/projects/{id}/retention-policy` plus the `preview` and `purge` sub-endpoints.

Four retention classes, each with its own clock (days):

| Class | Default | What it covers |
|---|---|---|
| Raw events | 90 | Live-stream event archives on runs (`event_archive`), raw ingest payloads (Allure/TestNG/REST) and live event documents in MongoDB. |
| Runs | 365 | Test runs and everything that hangs off them in PostgreSQL (test cases, analyses and their versions, clusters, decisions — the full cascade), plus run-scoped MongoDB documents (execution logs, pod events, run summaries and their versions, AI analysis traces). |
| Artifacts | 180 | Object storage: raw report uploads under the run's prefix and pipeline intermediate artifacts. |
| Audit | 2555 (~7 y) | Access/test-management audit rows, AI provenance records, the immutable pipeline event log, and **expired** compliance packs. Must be ≥ the runs window. |

Bounds: raw events/artifacts 7–3650, runs 30–3650, audit 365–3650 days.

**AI history.** Every re-analysis of a run keeps the earlier answers as versions: the run's analysis digest and run summary (MongoDB `ai_run_versions`) and each test result's analysis (PostgreSQL `ai_analysis_versions`). They are on the **runs** clock — they go when their run goes, by the policy purge or a single-run delete. Two caps bound them on the runs you keep:

| Cap | Default | Bounds | What it limits |
|---|---|---|---|
| `ai_versions_per_run` | 20 | 1–100 | Versions kept per run, per kind (analysis digest, run summary) |
| `ai_versions_per_test` | 10 | 1–100 | Versions kept per test result |

When a cap is exceeded the **oldest** versions go first, with two exceptions that are never pruned: **version 1** (the first answer) and **any version a published decision report cites**. If the decision reports cannot be read at that moment, nothing is pruned (the next sweep tries again). The caps apply as versions are written, whether or not the policy is enabled; the nightly sweep (enabled projects only) catches up on versions over the cap, for example after you lower one. The preview counts them as **AI history versions**: the versions of runs past the runs window, plus the versions over a cap. Set a cap back to 20 / 10 to restore the defaults.

**Preview-first workflow (recommended):**
1. **Preview purge** (`POST .../retention-policy/preview`) — a dry run returning the per-class cutoffs and exactly what a purge would delete now. It works while the policy is still disabled and writes nothing.
2. Tune the windows and enable the policy (**Save policy**).
3. Wait for the nightly sweep (02:00 UTC, enabled projects only) or **Purge now**, typing the project's name to confirm (`confirmation_name`; a mismatch is `422`, a disabled policy `409`). The purge runs in the background worker.

Every purge — scheduled or manual — writes a **purge-audit record** (`settings_audit_log`, key `retention_purge:<project_id>`) with the cutoffs, per-store deletion counts, duration and errors; the latest is `last_purge` on the policy.

**What is never purged:** `settings_audit_log` itself; compliance packs before their own `retention_expires_at` (default ~7 years); anything in a project whose policy is disabled.

The purge is re-entrant — an interrupted sweep is completed by the next run. Setting the raw-events window below 15 days strips live-run event archives that the live-run recovery safety net could otherwise still replay. For a full, immediate wipe of a project use **Project data** instead — retention is for steady-state ageing, not resets.

## Backup, restore & upgrades

Day-2 operations on Docker Compose are one command each — the scripts live in `scripts/ops/` and drive everything through `docker compose exec/run`, so the host needs nothing beyond docker and bash (on Windows, run them through `make`, which uses Git Bash). For how much machine and disk to plan for, see [Sizing & capacity](sizing.md). On Kubernetes, see the backup component in [k8s/components/backup/README.md](../k8s/components/backup/README.md).

### Backup — `make backup`

Produces **one timestamped archive** under `./backups/` (gitignored) containing a `pg_dump` of PostgreSQL, a `mongodump` of MongoDB, a tar of the object-storage data volume (the `minio` service), and a `manifest.json` recording the app version, git SHA and Alembic migration head. **The cache/queue broker (Valkey) and ChromaDB are excluded by design** — the broker holds queue entries that must not be replayed into a restored database, and ChromaDB is rebuilt from PostgreSQL by the hourly reindex.

```bash
make backup                      # → backups/testlookup-backup-<UTC>.tar.gz
QUIESCE=1 make backup            # stop the app layer during the backup (maximally consistent)
BACKUP_DIR=/mnt/nas make backup  # custom destination
```

The database dumps are transactionally consistent even while the stack is live. The object-storage tar is file-level — if CI is actively uploading attachments, prefer a quiet moment or `QUIESCE=1`. Copy the archives off-host on your own schedule.

### Restore — `make restore`

```bash
make restore FILE=backups/testlookup-backup-<ts>.tar.gz
```

Restore stops the app containers, replaces the PostgreSQL, MongoDB and object-storage data, flushes the broker, restarts the stack (the backend applies any pending migrations on boot), waits for readiness, runs the smoke check, and prints a verdict plus a post-restore checklist (ChromaDB reindexes itself within the hour; the checklist includes the command to trigger it immediately).

**The migration-safety gate:** restore *refuses to run* when the deployed code's migration head is **newer** than the backup's, and when the backup's head is unknown to the deployed code (a backup from a newer version — upgrade first). Both refusals explain themselves and can be overridden with `FORCE=1` when the rollback is intentional:

```bash
make restore FILE=backups/<name>.tar.gz FORCE=1 CONFIRM=yes   # non-interactive override
```

### Upgrade — `make preflight` then `make upgrade`

```bash
make preflight TAG=v0.2.0   # read-only: current vs target images, pending migrations, disk headroom, newest backup
make backup                 # your rollback path — do not skip this
make upgrade TAG=v0.2.0     # pull, migrate, restart in dependency order, health-check, smoke-test
```

`make upgrade` pulls the target images (`TAG=` applies to the release stack, `docker-compose.release.yml`; the dev stack rebuilds from source instead), runs `alembic upgrade head` *before* replacing the app containers so a migration failure is loud and early, recreates services in dependency order, and verifies health with the same smoke check as `make smoke`. On failure it prints step-by-step rollback instructions referencing your pre-upgrade backup. **There is deliberately no automatic rollback**: data migrations are not safely auto-reversible, so the trustworthy rollback is the pre-upgrade backup restored onto the previous image tag. Version-specific notes are in [UPGRADING.md](../UPGRADING.md).

## Admin checklists

**New instance** (after [GETTING_STARTED](../GETTING_STARTED.md)):
1. Sign in as the first administrator and change the generated password.
2. Decide on outbound access: leave `AI_OFFLINE_MODE` on until you deliberately need integrations or a hosted model.
3. Create your project(s) (**Projects → New Project**) and add users with the right roles (**Users**, **Members & access**). Self-registration stays off unless you set `REGISTRATION_ENABLED=true` (see [People & projects](#people--projects)).
4. Create API keys for CI (**Streaming API keys**, or a CI user's **My API keys**) and hand teams [Connecting CI and test frameworks](getting-results-in.md).
5. Configure suite owners and ownership rules so triage routes to people ([Test catalog governance and ownership](test-management.md)).
6. Review the default release-gate policy (**Releases → Gate policies**) and adjust bands and rules ([Gate policies and CI gating](release-gates.md)).
7. Turn on the off-by-default workflows you want (quarantine, webhooks, budgets, GitHub/GitLab) under **Feature flags**.
8. Set retention per project, and schedule `make backup` off-host.

**Something isn't arriving?** Integration health → Audit dashboard → [Troubleshooting](troubleshooting.md#a-webhook-jira-issue-or-chat-notification-did-not-fire), in that order.
