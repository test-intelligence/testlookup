# Administration

Every page in the settings index, who can open it, and what it changes. Settings that need a QA lead or an administrator are marked; most of them are open to QA leads, and only a few are for administrators alone.

## Who can open what

- **Your own pages** — **Profile** and **My notifications** — are open to every role, from the account menu. **My API keys** is for `QA_ENGINEER` and above.
- **Admin** in the sidebar, the settings index (`/settings`), and every other settings page need **`QA_LEAD` or `ADMIN`**. Anyone else who opens one is sent to Home.
- On several of those pages a QA lead can **look** but only an **administrator** can **change** things: AI configuration, Jira and the other integrations, email (SMTP), data and storage, gate policies, LLM cost budgets, and the audit export. A few pages are **administrator only** altogether: **SSO & identity**, **Feature flags**, **MFA & lockout**, **Retention & purge**, **Project data** and creating streaming API keys.

The sections below follow the groups of the settings index.

## The settings index

**Admin** opens `/settings`, which lists every settings page you can open, grouped as **Account**, **Project**, **Release governance**, **Integrations**, **AI**, **Security & access** and **System**, each with a one-line description. Every settings page repeats the same groups as a side navigation, so you can move between them directly. Pages that work on one project use the project selected in the top bar; most ask you to pick one in **All Projects**.

## Account

### Profile

**My profile** (`/settings/profile`): your full name and avatar colour (username and email are read-only), **Change Password** (current password, then the new one twice, at least 8 characters), and **Two-Factor Authentication**:

1. **Enable two-factor authentication** shows a QR code and the secret for your authenticator app.
2. Enter the code the app shows, then **Verify and enable**.
3. Save the ten recovery codes shown **once** (**Download .txt**); each works once if you lose the authenticator.

Once enabled, the section shows how many recovery codes remain, with **Regenerate recovery codes** (which invalidates the old ones) and **Turn off two-factor authentication**; both ask for your password and a current code. If your organisation requires two-factor sign-in for your role, you cannot turn it off. Accounts that sign in through SSO manage their second factor with their identity provider instead.

### My notifications

**My notifications** (`/settings/my-notifications`) decides what reaches you, per channel: **Email**, **Slack** and **Microsoft Teams**.

- Turn a channel on, and optionally give your own email address or your own Slack or Teams webhook URL; blank uses the shared channel an administrator configured.
- **Notify me when**: run failed or passed, high failure rate (with a threshold, 80% by default), AI analysis complete, quality gate failed, flaky test detected, and the transition events (a test newly failing, recovered, newly flaky, quarantined, released from quarantine).
- **Save**, then **Send test** to check delivery. **Remove** deletes the channel's settings.

These are your defaults for every project. **View notification history** lists recent notifications, with **Mark all read**. The bell in the top bar shows the same notifications.

### API keys

There are two kinds, each with its own page:

- **My API keys** (`/settings/my-api-keys`, `QA_ENGINEER` and above): keys you own, for your own scripts and pipelines. **Generate key**: a name, an optional expiry (1–365 days; blank never expires) and optional scopes (`stream:write`, `project:write`, `project:admin`; none means full access).
- **Streaming API keys** (**Project → Streaming API keys**, one project selected): keys bound to the project for CI result streaming, limited to `stream:write`, expiring after 90 days by default. Only an **administrator** can generate them; an administrator sees every key bound to the project, anyone else sees their own.

A key carries the permissions of the user who created it, narrowed by its scopes, and is shown **once**. Revoke it immediately on suspicion of exposure: you can revoke the keys you own. API keys are never asked for a second factor, so treat issuing one as a sensitive grant.

## Project

### Projects

**Projects** (`/projects`, `QA_LEAD` and above): **New Project** asks for a name, a slug (fixed after creation), and optionally a description, Jira project key and OpenShift namespace. The creator becomes a member, and the project gets a default QA-lead account that failures fall back to. Edit a project for its dates, tags, Jira key, Splunk index, OpenShift namespace and Jenkins job pattern.

**Archive** hides a project without deleting it, and revokes its credentials. **Archiving is a soft delete** — rows remain but the project disappears from listings and its runs stop appearing in run lists. There is no un-archive in the app.

> **Warning.** Archived projects are invisible but not gone. If historical runs "disappeared", check whether the project was archived before assuming data loss. To actually remove data, use **Retention & purge** or **Project data**.

### Users and roles

Five roles, each including those below it:

| Role | Can |
|---|---|
| `VIEWER` | Read dashboards and reports |
| `TESTER` | Read runs and failures |
| `QA_ENGINEER` | Send results, own API keys, triage, correct classifications, file defects, manage suites and test cases |
| `QA_LEAD` | Everything above, plus releases, ownership, quarantine and AI-report decisions, overrides, and most settings pages |
| `ADMIN` | Everything, including user roles, SSO, feature flags and the administrator-only settings |

Users see only projects they are a member of. Project membership, not role alone, decides visibility.

**Users** (`/users`): QA leads see the list. Administrators **Add User** (a temporary password is shown once, and the user must change it at first sign-in), change a user's role inline, edit, and activate or deactivate accounts.

**Members & access** (`/users?tab=project-members`): pick a project to see its members and its **Default QA Lead**. Administrators add and remove members, set each member's project role, and change the default QA lead; **Reset QA Lead password** is available to QA leads.

Self-registration is off unless the deployment turns it on (`REGISTRATION_ENABLED`). When it is on, the sign-in page offers **Register**; such an account is a Viewer in no project until someone adds it here.

### Ownership

**Ownership** (`/ownership`, `QA_LEAD` and above, one project): rules that map tests to a service and team, which decide who is assigned a failure and which team channel hears about it. **Add Rule**: a match type (**Suite Name**, **Component / Class**, **Package**, **Full Path**, **Feature / Epic Label**), a pattern (glob), the service and team name, a team contact, and a priority (higher is tried first). **Import CODEOWNERS** (paste the file or fetch it from GitHub) creates path rules, replacing earlier imported ones. Rules can be disabled without deleting them.

### Team channels

**Team channels** (`/settings/team-channels`, `QA_LEAD` and above, one project): for each team named in the ownership rules, a **Slack webhook**, **Teams webhook** or **Email** destination. A failing test's alerts go to the channel of the team that owns it; a failure cluster goes to the team owning more than half of its tests. If a team has no channel, the test has no owner, ownership is mixed, or delivery fails, alerts fall back to each person's own notification settings.

### Retention and purge

**Retention & purge** (`/settings/retention`, administrators, one project): how long the project keeps raw events, runs and their analysis, artifacts, and audit records (each in days; audit must be at least as long as runs). Nothing is purged until you tick **Enable scheduled purging for this project** and **Save policy**; the purge then runs nightly at 02:00 UTC.

- **Preview purge** is a dry run: it shows what would be deleted now, without deleting anything.
- **Purge now** (policy enabled) asks you to type the project's name, then queues the purge.
- **Delete by criteria** removes a selected subset.

**AI history** keeps every AI analysis of a run as a version: up to 20 per run (per kind: the analysis digest and the run summary) and 10 per test result by default, each settable from 1 to 100. The oldest go first; version 1, the newest and any version a published decision report cites never do. The caps apply whenever a version is stored, even with scheduled purging off, and AI history goes with its run.

Purging is irreversible. The operator guide in the repository (`user-guide/administration.md`) lists exactly what each class covers.

### Project data

**Project data** (`/settings/project-data`, administrators, one project) resets a project. It does not export or import data.

- **Delete test runs** removes every run and what hangs off it, keeping suites, the catalogue, releases, policies, baselines and knowledge sources.
- **Wipe project data** (**Reset project to fresh state**) also removes the catalogue, releases, policies, baselines, test cases, plans, strategies and knowledge sources. The project, its members, API keys, AI and SSO configuration and flag overrides stay.

Both ask you to type the project's name.

## Release governance

**Gate policies** (`/policies`) — the rules the release gate applies. QA leads can read them; creating, publishing and simulating policies needs an administrator. See [Releases and gates](/docs/releases#gate-policies).

## Notifications and webhooks

Where each kind of message is configured:

| You want | Page |
|---|---|
| Your own alerts, by email, Slack or Teams | **Account → My notifications** |
| A team's alerts in the team's own channel | **Project → Team channels** |
| The mail server and the shared Slack and Teams channels | **Integrations → Email (SMTP) & channels** and **Integrations → Jira, Splunk, OCP, Slack, Teams** |
| Scheduled summaries | **Integrations → Digests & views** |
| Events sent to your own systems | **Integrations → Outbound webhooks** |

Notification content can include test error text — consider that when choosing a channel.

## Integrations

Everything here that reaches outside the deployment is **switched off by offline mode**, which is on by default: with it on, nothing outbound is sent, whatever the page says. An administrator decides that in the deployment's configuration.

### Jira, Splunk, OpenShift, Slack and Teams

**Integrations** (`/settings/integrations`): QA leads can read it; administrators change it and **Save Integrations**. One card per integration, each with **Enabled**: **Jira** (domain, email, default project key, API token), **Splunk** (base URL, API token), **OpenShift / Kubernetes** (API URL, default namespace, service-account token), **Slack** (webhook URL, default channel) and **Microsoft Teams** (webhook URL). Tokens are stored encrypted and shown only as **(set)**. A value saved here overrides the deployment's environment setting. Use **Integration health** to check a connection.

### GitHub and GitLab

**GitHub** (`/settings/github`) and **GitLab** (`/settings/gitlab`), per project: a repository and an access token, after which runs carrying pull-request (or merge-request) context get a single sticky comment with newly failed, known-flaky and fixed tests, and commits get a check run (GitHub) or commit status (GitLab). Both are off by default (`github_checks` and `gitlab` feature flags). Setup and token scopes are in the operator guide in the repository (`user-guide/administration.md`).

### Outbound webhooks

**Outbound webhooks** (`/settings/webhooks`, one project) send events to your own HTTPS endpoints: `run.completed`, `defect.promoted`, `defect.create_requested`, `release.decided`, `flaky.quarantined` and `quota.exceeded`. **New webhook**: a name, the target URL, an HMAC secret (deliveries are then signed in the `X-TestLookup-Signature` header) and the events. Each subscription shows delivered and failed counts, its last error, **Test**, and **Recent deliveries** with **Replay** for failed ones. Failed deliveries are retried with backoff up to **Max retries** (5 by default). Creating and changing subscriptions needs `QA_LEAD`.

> **Off by default.** Outbound webhooks are switched by the `outbound_webhooks` feature flag, which ships **off**, and by offline mode. The page does not say when the flag is off; **Test** answers "No delivery enqueued" until both allow delivery. See the feature-flag reference in the repository docs (`docs/operations/feature-flags.md`).

### Email (SMTP) and channels

**Email (SMTP) & channels** (`/settings/notifications`): the mail server (**Enable SMTP email delivery**, host, port, from address, optional username and password, and **Implicit TLS** or **STARTTLS**; port 465 picks implicit TLS and 587 or 25 STARTTLS, and plain SMTP is not supported). **Send test email** sends to your own address using the **saved** settings, so save first. QA leads can read and test it; saving needs an administrator. The **Shared channels** card shows whether the shared Slack and Teams channels are delivering, with a link to edit them under Integrations. Per-event choices are on each person's **My notifications**.

### Digests and views

**Digests & views** (`/settings/digests`): scheduled summaries to email, Slack or Teams — **Daily**, **Weekly**, **Per Run** (all runs, failed only, or degraded only), **Per Release** or **Per Suite**, and **Weekly Retro** when the `weekly_retro_digest` flag is on. For a daily or weekly email to one project, **Attach analysis report** adds the HTML analysis report (see [Reports and outputs](/docs/reports#summary-report-files)). Pause, resume or unsubscribe each digest. **Preview Digest** shows what one will look like. The **Saved Views** tab lists named views.

### Integration health

**Integration health** (`/settings/integration-health`): whether each connection works — Jira, Splunk, GitHub, OpenShift, Slack, Teams, SMTP, the local model server and the vector store — as **healthy**, **degraded**, **down**, **auth error**, **timeout**, **unknown** or **skipped**, with latency. Tabs: **Current Status**, **Trends (7d)** (uptime and failure counts) and **Probe History**. Connections are probed every 15 minutes; **Probe All Now** or **Probe now** on one card checks immediately. Start here when "the webhook didn't fire".

## AI

### AI configuration

**AI configuration** (`/settings/ai`): QA leads can read it; administrators change it.

- **Analysis Engine**: **Auto**, **LLM (AI Agent)**, **Machine Learning** or **Rules-Based**, each showing whether it is ready.
- **Model Availability & Fallback Chain**: whether the model server answers and each required model is installed.
- **LLM Provider** and **Embedding**: provider, model, temperature, max tokens.
- **Pipeline Settings**: confidence threshold, timeout, offline mode, and toggles.
- **Knowledge-Grounded Generation**: **Enable Knowledge RAG** for generating test cases from documents. While the `rag_faithfulness_gate` feature flag is on, **Faithfulness evaluator** chooses how drafts are scored: the single-prompt judge (the configured model, works offline) or Ragas (the configured model too, several calls per case, each inside the cost cap; needs offline mode off and the `ragas` package, which only an image built with `INSTALL_RAGAS=1` has). The badge shows the one in use.
- **Cloud API Keys** for hosted providers, stored encrypted.

Two switches are worth understanding:

- **Analysis mode and provider** — whether AI-assisted analysis uses a language model at all. Without one, analysis falls back to ML or rules and still works. Ask AI needs **Auto** or **LLM**.
- **Offline mode** — a hard ceiling on outbound calls, on by default. With it on, a model is only reached on your own network, and integrations that would reach outside the deployment are gated.

> **Important.** Offline mode is a boundary, not a preference. It is on unless your administrator turned it off; check it here before assuming test data stays local. See [Security and privacy](/docs/security).

### AI agents and agent activity

**AI agents** (`/settings/ai-agents`, one project) sets the policy for the two agents that can act on failures:

- **Investigator** — investigates failure clusters. On by default, in **Shadow** mode: it reasons and records what it *would* do, without acting or proposing. **Suggest** proposes actions that need a person's explicit approval (there is no approval page in the app yet). **Act** is not available yet. Budgets: runs a day, model calls, tokens and seconds per run.
- **Fixer** — proposes fixes for flaky tests, validated by re-running the test, as draft pull requests that a person must merge. Off by default. Its card sets the mode, a schedule (**Off (manual only)**, **Daily**, **Weekly**) and budgets.

Saving a policy needs `QA_LEAD`. **Agent activity** (`/settings/agent-activity`) lists every agent run: agent, mode, trigger, status, tokens, cost and duration, with the **Actions proposed** and **Actions taken** for each, and the Fixer's attempts. In shadow mode, actions taken is always zero; the gap is the evidence for promoting an agent.

### Workflow editor and pipeline runs

**Workflow editor** and **Pipeline runs** are listed here too; see [AI agents and the pipeline](/docs/ai-agents#pipeline-runs).

### AI evaluation

**AI evaluation** (`/settings/ai-eval`) measures how well the AI answers: **Quality Overview** (agreement between people and the AI, training-label health, drift, recent evaluation runs), **Evaluation Datasets** (build one from your team's corrections, then **Run Evaluation**), and **Evaluation Gate** (run the pre-release gate for classification, root cause, duplicate detection or release decisions). QA leads can read it; running evaluations and changing datasets needs an administrator.

### LLM cost budget

**LLM cost budget** (`/settings/billing`) shows each project's language-model spend this month against its budget (**OK**, **SOFT_WARN**, **CAPPED** or **UNLIMITED**). Administrators set a project's monthly budget: the included amount, a hard cap, a soft-warn threshold, and what happens past it (**Soft warn**, **Auto-downgrade to ML**, **Auto-downgrade to rules** or **Hard block**). At the hard cap, model calls are blocked, and Ask AI says the project's budget for the month is used up.

> **On for new installs.** Budgets are recorded and enforced while the `llm_cost_budget` feature flag is on: on for a new install since W04, off on an install upgraded with it off until an admin turns it on. With it off, the page shows no spend and every project as **UNLIMITED**.

## Security & access

### SSO and identity

**SSO & identity** (`/settings/sso`, administrators) configures SAML 2.0 single sign-on. Tabs:

- **SSO Configuration** — display name, the identity provider's entity ID, SSO URL and certificate, TestLookup's own entity ID and ACS URL, an optional group attribute for role mapping, the enforcement (**Optional (password login allowed)** or **SSO Required (admin fallback only)**) and the default role for new SSO users. **Test Connection** checks the certificate and reaches the identity provider; **Activate** turns a configuration on.
- **SCIM Tokens** — tokens your identity provider uses to provision users automatically (shown once; revocable).
- **Identity Events** — sign-ins, provisioning and role changes from the last 30 days.
- **Sync Status** — federated users, last SSO sign-in, last SCIM sync and recent failures.

SSO also has to be enabled in the deployment's configuration; the operator guide covers it. **Sign in with SSO** sends people to the identity provider and brings them back to the page they started from. There is no single logout: signing out of TestLookup leaves the identity-provider session alone.

### MFA and lockout

**MFA & lockout** (`/settings/mfa-policy`, administrators): **Require MFA to sign in**, for everyone or for a role and above; and account lockout after repeated failed sign-ins (10 attempts and 15 minutes by default). Accounts that sign in through SSO are exempt from the requirement.

### Audit

**Audit dashboard** (`/settings/audit`): who changed what — access, settings, test management, identity, notification, release and report events — filterable by category and by the last 7, 30 or 90 days, and a **Project Observability** tab. **Export CSV** needs an administrator. Audit rows are append-only by design: a correction adds a record rather than editing one.

### Activity log

**Activity log** (`/activity`, one project): everything that happened in the project, in order — runs, analysis, releases, quality, configuration, membership, test management, integrations and agent actions. Search it (three characters or more), filter by category, by who did it (a user, an API key, a service account, the system or an agent) and by window, and open an event to see what changed. **Export CSV** needs `QA_LEAD` on the project.

## System

### Data and storage

**Data & storage** (`/settings/storage`): whether the databases and the cache are connected, and where results and artifacts are stored — the object storage (endpoint, bucket, SSL) and the vector store (host, port, collection). QA leads can read it; administrators change it.

### Performance

**Performance** (`/settings/performance`) is read-only: the latency budgets per operation, the search and connection-pool configuration, and reference scale scenarios.

### Feature flags

**Feature flags** (`/settings/feature-flags`, administrators) lists every flag with its description, a global on/off switch, a rollout percentage, and its scope. **Edit** limits a flag to listed projects or roles; **New Flag** adds one.

A flag is evaluated as: **the global switch is the master switch**, then the project list and the role list **narrow** an enabled flag (an empty list means all), and the rollout percentage must be above zero.

> **Note.** Setting only the project list does nothing — with the global switch off the flag is off everywhere. To enable a flag for one project you need all three: global on, that project listed, rollout 100.

A flag with no record at all evaluates **off**, except for a few older flags that a deployment setting can still switch on. Several workflows ship **off** and say so on their page: quarantine proposals (`flaky_auto_quarantine`), outbound webhooks (`outbound_webhooks`), release phase enforcement (`release_phase_gate_enforcement`), compliance packs (`release_compliance_pack`), GitHub (`github_checks`) and GitLab (`gitlab`). **Upload report** (`manual_upload`), Ask AI (`ask_ai_chat`) and, on a new install, LLM cost budgets (`llm_cost_budget`) ship on. The full list is the feature-flag reference in the repository docs (`docs/operations/feature-flags.md`). Changes can take up to about 30 seconds to reach every page.

### `defect_commander`

The one pipeline stage that mutates state beyond its own records. Enabled, deep runs create defect records. Ticket filing additionally requires Jira to be enabled.

> **Warning.** Enable this only when you want defects created automatically. Test it on one project first.

### Seed data

**Seed data** loads, resets or deletes demo data. It exists only in development builds against a development server.

## Related

- [Security and privacy](/docs/security)
- [API, CLI, SDKs and MCP](/docs/integrations)
- [Finding your way around](/docs/navigation)
