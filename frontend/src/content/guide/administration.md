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
- **Notify me when**: run failed or passed, high failure rate (with a threshold, 80% by default), AI analysis complete, quality gate failed, the state changes (a test newly failing, recovered, newly flaky, quarantined, released from quarantine), the quarantine lifecycle (past its SLA, ready to release) and a test or suite becoming slower.
- **Save**, then **Send test** to check delivery. **Remove** deletes the channel's settings.

**Quality gate failed** is sent when a gate's recorded verdict turns into `NO_GO`: a QA lead evaluates a release or phase gate and the verdict recorded before it was not `NO_GO`, or a run's release-readiness decision becomes `NO_GO` (published by the analysis pipeline, or set by a QA lead's override). Evaluating again while the gate stays `NO_GO` sends nothing more, and a preview never sends. The entry names the release (or the build) and the top blocking reason; in the bell it opens the release (for QA leads and administrators) or the run's release gate. It is not ticked for you by default, and the project's notification policy does not govern it.

There is no longer a **Flaky test detected** choice: it never sent anything. **Test newly flaky** is the event for a test that becomes flaky, and anyone who had ticked the old choice now has **Test newly flaky** ticked instead.

With one project selected in the top bar, an event the project's notification policy will not send carries a note under its name: `Not sent in Checkout: per-run notifications are off`, `Not sent in Checkout: state-change notifications are off` or `Not sent in Checkout: this state change is turned off`. A QA lead or administrator also gets **Change it in Email (SMTP) & channels**. In **All Projects** one line says that run and state-change events depend on each project's notification policy. If the policy cannot be read, no note is shown (the server then sends, so a note would be wrong).

These are your defaults for every project. **View notification history** lists recent notifications, with **Mark all read**. The bell in the top bar shows the same notifications.

### API keys

There are two kinds, each with its own page:

- **My API keys** (`/settings/my-api-keys`, `QA_ENGINEER` and above): keys you own, for your own scripts and pipelines. **Generate key**: a name, an optional expiry (1–365 days; blank never expires) and optional scopes (`stream:write`, `project:write`, `project:admin`; none means full access).
- **Streaming API keys** (**Project → Streaming API keys**, one project selected): keys bound to the project for CI result streaming, limited to `stream:write`, expiring after 90 days by default. Only an **administrator** can generate them; an administrator sees every key bound to the project, anyone else sees their own.

A key carries the permissions of the user who created it, narrowed by its scopes, and is shown **once**. Revoke it immediately on suspicion of exposure: you can revoke the keys you own. API keys are never asked for a second factor, so treat issuing one as a sensitive grant.

## Project

### Projects

**Projects** (`/projects`, `QA_LEAD` and above): **New Project** asks for a name, a slug (fixed after creation), and optionally a description, Jira project key and OpenShift namespace. The creator becomes a member, and the project gets a default QA-lead account that failures fall back to. Edit a project for its dates, tags and Jira key, its two integration fields, and **Require review before a test case becomes Active**: when it is on, a test case needs an approved review before it can be made Active (a QA lead or administrator can approve their own case with a reason). It is off by default for every project, so cases move straight between Draft and Active; see [Test management](/docs/test-management#authored-test-cases-and-reviews).

The two integration fields decide what the AI's log and cluster look-ups ask for in this project:

- **Splunk Index**: the index the Splunk look-ups search for this project's failures. Empty uses the deployment's index. Letters, digits, `_` and `-` only (1-80 characters).
- **OCP Namespace**: the OpenShift namespace used when a run names a pod but no namespace. Empty uses the deployment's default namespace (**Default Namespace** under [Integrations](#jira-splunk-openshift-slack-and-teams)). It must be a DNS label: lowercase letters, digits and `-`, at most 63 characters.

A value that breaks its rule is refused with the reason under the field, and nothing is saved. To clear a field, empty it and **Save Changes**. **Jenkins Job Pattern** is no longer on the form: TestLookup never used it, and a value saved earlier is kept.

**Archive** hides a project without deleting it, and revokes its credentials. **Archiving is a soft delete** — rows remain but the project disappears from listings and its runs stop appearing in run lists. There is no un-archive in the app.

> **Warning.** Archived projects are invisible but not gone. If historical runs "disappeared", check whether the project was archived before assuming data loss. To actually remove data, use **Retention & purge** or **Project data**.

### Users and roles

Five roles, each including those below it:

| Role | Can |
|---|---|
| `VIEWER` | Read dashboards and reports |
| `TESTER` | Read runs and failures; write, edit and comment on test cases and change their status (not deprecate, archive or reinstate) |
| `QA_ENGINEER` | Send results, own API keys, triage, correct classifications, file defects, manage suites and test cases |
| `QA_LEAD` | Everything above, plus releases, ownership, quarantine and AI-report decisions, overrides, deprecating, archiving and reinstating test cases, and most settings pages |
| `ADMIN` | Everything, including user roles, SSO, feature flags and the administrator-only settings |

Users see only projects they are a member of. Project membership, not role alone, decides visibility.

**Users** (`/users`): QA leads see the list. Administrators **Add User** (a temporary password is shown once, and the user must change it at first sign-in), change a user's role inline, edit, and activate or deactivate accounts.

**Members & access** (`/users?tab=project-members`): pick a project to see its members and its **Default QA Lead**. Administrators add and remove members, set each member's project role, and change the default QA lead; **Reset QA Lead password** is available to QA leads.

Self-registration is off unless the deployment turns it on (`REGISTRATION_ENABLED`). When it is on, the sign-in page offers **Register**; such an account is a Viewer in no project until someone adds it here.

### Ownership

**Ownership** (`/ownership`, `QA_LEAD` and above, one project): rules that map tests to a service and team, which decide who is assigned a failure and which team channel hears about it. **Add Rule**: a match type (**Suite Name**, **Component / Class**, **Package**, **Full Path**, **Feature / Epic Label**), a pattern (glob), the service and team name, a team contact, and a priority (higher is tried first). **Import CODEOWNERS** (paste the file or fetch it from GitHub) creates path rules, replacing earlier imported ones. Rules can be disabled without deleting them.

**Java source roots** (under the rules): a Java, Kotlin, Groovy or Scala stack trace names a class and a file (`at com.acme.pay.CheckoutTest.pays(CheckoutTest.java:42)`), not a path, so a path rule cannot own it until TestLookup knows where your test sources live. List up to 20 roots in priority order, each an optional package prefix and a repository-relative directory: `com.acme.pay` → `payments/src/test/java`, and an empty prefix (**any package**) → `src/test/java`. A frame uses the root with the longest prefix that is its package or a parent of it, else the any-package root; JDK, JUnit, TestNG, Spring, Gradle and similar library frames are skipped, so the first frame of your own code wins. **Test on recent failures** checks the edited list against the last 30 days' failures (`12 of 15 recent Java failures map to a path; 9 match a path rule`) without saving it; **Save** applies it everywhere TestLookup locates a failure: the **Coverage** badge, the owner a new failure is assigned to, the Inbox's "via CODEOWNERS" reason and the GitHub check annotations. Removing every root restores the old behaviour (Java failures unlocated). Members below QA lead see the list read-only; each change is recorded in the activity feed as `Project settings changed: Java source roots`.

### Team channels

**Team channels** (`/settings/team-channels`, `QA_LEAD` and above, one project): for each team named in the ownership rules, a **Slack webhook**, **Teams webhook** or **Email** destination. A failing test's alerts go to the channel of the team that owns it; a failure cluster goes to the team owning more than half of its tests. If a team has no channel, the test has no owner, ownership is mixed, or delivery fails, alerts fall back to each person's own notification settings.

### Retention and purge

**Retention & purge** (`/settings/retention`, administrators, one project): how long the project keeps raw events, runs and their analysis, artifacts, and audit records (each in days; audit must be at least as long as runs). Nothing is purged until you tick **Enable scheduled purging for this project** and **Save policy**; the purge then runs once a day in the project's **Purge window (UTC)** (02:00 unless you pick another hour).

- **Preview purge** is a dry run: it shows what would be deleted now, without deleting anything.
- **Purge now** (policy enabled) asks you to type the project's name, then queues the purge. It ignores the window and a pause.
- **Pause 7 days** stops the scheduled purge for a week (**Paused until …**); **Resume** ends the pause. The line beside it says when the next scheduled purge runs.
- **Delete by criteria** removes a selected subset.

**Instance defaults**: a field the project does not set uses the instance default, field by field. The card marks each one — **180 days (instance default) · Override**, **365 days (default) · Override**, or **Project value · Reset to inherited**. **Override** makes the inherited value the project's own when you save; **Reset to inherited** goes back to the instance default. Administrators set the defaults in the **Instance defaults** card at the bottom of the page (**Open**; an empty field means the built-in default; **Used by N projects** shows who inherits it). The instance default ships **off**: **Turn on for every project without its own setting…** lists every project it would switch on, and you type the phrase shown (for example **enable retention for 7 projects**) before **Turn on**. A project that explicitly turned scheduled purging off stays off. **Turn off** asks once, inline.

**Export before delete**: after a **Delete by criteria** preview, choose **Delete without an archive** (the default), **Download an archive first**, or **Keep an archive in storage**, and a tier — **Summary** (runs and test results), **Evidence** (plus failure details and reports) or **Full** (plus raw payloads and text attachments after secret redaction; binary attachments are listed, not copied). Each shows its estimated size and what the deletion reclaims. **Archive, not a backup — it cannot be re-imported.** An archive is built and verified before anything is deleted; a failed archive deletes nothing.

- **Deletion jobs** lists every deletion: status, archive (tier, verified) and bytes reclaimed.
- A **Download an archive first** job waits as **Awaiting collection**: **Collect** → **Download archive** → **I have saved it** (enabled once the whole archive has downloaded). Only then are the runs deleted. Collect within 72 hours; after that nothing is deleted and the archive is removed.
- Archives cover at most 2000 runs and 512 MB; a tier over the limit is disabled.

**Search usage rows** (the per-search records behind the Search page's usage tiles: no query text, no user) are not on a clock you set: every deployment keeps them 30 days and deletes older ones nightly, whatever the policy. The preview lists the project's rows older than that as **Search usage rows (kept 30 days)**, and a purge deletes them with the rest.

**Unlabelled shadow pairs** (live outputs of two model tiers waiting for a label on **AI evaluation › Tiers**) are on a fixed clock too: a pair nobody labelled within 30 days is deleted nightly. The preview lists them as **Unlabelled shadow pairs (kept 30 days)**, and a purge deletes them with the rest. Labelled pairs are evidence for tier changes and are kept.

**AI history** keeps every AI analysis of a run as a version: up to 20 per run (per kind: the analysis digest and the run summary) and 10 per test result by default, each settable from 1 to 100. The oldest go first; version 1, the newest and any version a published decision report cites never do. The caps apply whenever a version is stored, even with scheduled purging off, and AI history goes with its run.

**Always keep the last N runs** (default 10, 0–1000): the scheduled purge always keeps the project's newest N runs, its latest passing run and the latest passing run of each suite (0 keeps only those passing runs). It protects runs only: a kept run still loses old raw events and audit records on their own clocks. Deleting runs yourself (**Delete by criteria**) is not limited by it, and the preview says so.

**Retention holds** stop every purge and deletion of what they cover until an administrator releases them:

- **Retention holds** card: **Place hold** puts the whole project on hold (a reason, and an optional expiry date); **Release** (with a reason) lifts a live hold. The card lists **Live**, **Expired** and **Released** holds. QA leads place release and compliance-pack holds from the release page; run holds are placed through the API.
- A release hold also keeps the release's runs, their decision reports and baselines, and its compliance packs; a run hold keeps the run and its baseline.
- While a hold is live, the purge skips what it covers (the preview shows **Kept by hold**), and every deletion refuses with **Held:** and the hold's reason — **Delete by criteria**, deleting a run through the API, retiring or deleting a compliance pack, deleting a release, unlinking a run from a release, and **Project data** resets.
- Holds do not cover analysis caches, search-index documents, search usage rows, unlabelled shadow pairs or expired agent memory. An expired hold stops applying on its own.

**Reference warnings**: the preview and **Delete by criteria** list links that the deletion would leave empty, such as **3 runs lose their baseline link**. They never block a deletion. A run whose baseline was purged says so on its **Changes** tab.

Purging is irreversible. The operator guide in the repository (`user-guide/administration.md`) lists exactly what each class covers.

### Expected runs

**Expected runs** (`/settings/expected-runs`, `QA_LEAD` and above edit, one project) says when TestLookup expects a run, so a missing one is noticed. It only observes: nothing here starts, stops or pauses your CI.

- **Add expected cadence**: the scope (**Any run of the project**, or **Runs of suite** and its name — a run counts when it is labelled with the suite or has results in it), the days and an **Expected by** time in a **Time zone**, and a **Grace** (0–720 minutes, default 60). **Cron (advanced)** takes `minute hour * * day-of-week` (0 = Sunday), at most 24 times a day. One cadence for any run, and one per suite.
- A run that arrives by the time plus the grace counts for that day; a run 20 minutes late inside the grace is still that day's. Any result counts: the expectation is that a run arrives, not that it passes.
- Daylight-saving changes never cause a false miss: a time that does not exist that day, or occurs twice, counts at its later instant.
- Every 15 minutes TestLookup decides the windows that ended. A miss is recorded once in the activity feed (`2 expected run(s) of nightly-regression missed (Tue 27 Oct, Wed 28 Oct)`) and sent to whoever ticked **An expected run was missed** under **My notifications** — nobody by default.
- **Pause** (until a time at most 90 days away, with a reason) stops expecting runs and sending alerts; **Resume** expects the next run again. Days decided while paused stay paused. **Edit** changes the expectation from now on: earlier days keep what was decided. **Delete** removes the cadence and its history.
- The table shows each cadence's last 14 days (`Last 14 days: 12 met · 2 missed (Tue, Wed)`). **Trends** and **Coverage** use the same cadences; without one they say **No expected cadence configured**. Members below QA lead who open the page read it without actions.

### Project data

**Project data** (`/settings/project-data`, administrators, one project) resets a project. It does not export or import data.

- **Delete test runs** removes every run and what hangs off it, keeping suites, the catalogue, releases, policies, baselines and knowledge sources.
- **Wipe project data** (**Reset project to fresh state**) also removes the catalogue, releases, policies, baselines, test cases, plans, strategies and knowledge sources. The project, its members, API keys, AI and SSO configuration and flag overrides stay.

Both ask you to type the project's name. Both are refused while any retention hold of the project is live.

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

**Integrations** (`/settings/integrations`): QA leads can read it; administrators change it and **Save Integrations**. One card per integration, each with **Enabled**: **Jira** (domain, email, default project key, API token), **Splunk** (base URL, API token), **OpenShift / Kubernetes** (API URL, default namespace, service-account token), **Slack** (webhook URL) and **Microsoft Teams** (webhook URL). Tokens are stored encrypted and shown only as **(set)**. **Save Integrations** sends only the fields you changed; with nothing changed it says `No changes to save` and sends nothing. Use **Integration health** to check a connection.

**Which value applies**, first rule that fits:

1. **Offline mode** refuses every outbound call, whatever is saved here.
2. A **project's own value** comes before the deployment's: the project's **Splunk Index**, and for OpenShift the run's own namespace, then the project's **OCP Namespace** (see [Projects](#projects)).
3. A **value saved here** comes before the deployment's environment setting. A switch (**Enabled**) once saved always counts, so saving it off turns the integration off even when the environment turns it on.
4. The **environment** applies to a field never saved here, or saved empty. Such a field says **From the deployment environment** under it, and a token reads **(set in the environment)** instead of **(set)**. To hand a field back to the environment, empty it and save.

The Splunk and OpenShift look-ups of the AI analysis, the Jira tickets the pipeline files, and **Integration health** all read these values. A save reaches the rest of the deployment within 30 seconds. GitHub and GitLab are set per project instead ([below](#github-and-gitlab)).

A **Base URL**, **API URL** or Jira **Domain** that points at the server itself (`localhost`, `127.0.0.1`) or at a link-local address (`169.254.x.x`, where cloud metadata lives) is refused with `Target host is not allowed` under the field, and nothing is saved; the same check runs again before every call, before a token is sent. An address on your own network is allowed. A URL must start with `http://` or `https://`; the Jira domain is a host name such as `your-company.atlassian.net`, without `https://`.

**Slack** has one field: an incoming webhook always posts to the channel it was created for. The line under it says so, with a link to **Team channels**, where a team's alerts go to the team's own channel.

### GitHub and GitLab

**GitHub** (`/settings/github`) and **GitLab** (`/settings/gitlab`), per project: a repository and an access token, after which runs carrying pull-request (or merge-request) context get a single sticky comment with newly failed, known-flaky and fixed tests, and commits get a check run (GitHub) or commit status (GitLab). Both are off by default (`github_checks` and `gitlab` feature flags). Setup and token scopes are in the operator guide in the repository (`user-guide/administration.md`). Each project's connection has its own row on [Integration health](#integration-health), and a run's **Changes** tab lists its commits through the project's GitHub integration (see [Runs and run detail](/docs/runs#changes)).

### Outbound webhooks

**Outbound webhooks** (`/settings/webhooks`, one project) send events to your own HTTPS endpoints: `run.completed`, `defect.promoted`, `defect.create_requested`, `release.decided`, `flaky.quarantined` and `quota.exceeded`. **New webhook**: a name, the target URL, an HMAC secret (deliveries are then signed in the `X-TestLookup-Signature` header), the events, **Max retries** and **Pause after (consecutive failed deliveries)**. Each subscription shows delivered and failed counts, its last error, **Test**, and **Recent deliveries** with **Replay** for failed ones. Failed deliveries are retried with backoff up to **Max retries** (5 by default). Creating and changing subscriptions needs `QA_LEAD`.

**The circuit breaker.** A subscription is paused after **Pause after** deliveries in a row end failed (1-100, 10 by default). A delivery counts **once**, when it ends: after its last retry, or at once for a refused or private target, a missing signing secret or a 4xx answer. A retry still pending does not count, and a successful delivery sets the count back to zero. The row then reads **PAUSED**, with `Paused after 10 consecutive failures (a refused or unreachable target counts too). Last error: …` and **Re-enable**, which turns it back on and sets the count to zero. While it is paused, events are not sent to it; **Test** says no delivery was enqueued, and earlier failed deliveries can be replayed once it is re-enabled. The subscription's last editor and the project's QA leads are notified when it pauses, the activity log records `Webhook <name> paused after 10 consecutive failures`, and **Integration health** lists it under **Paused webhooks**. An enabled row that has started failing shows how far it is (`✓ 4 · ✗ 6 · 2/10 in a row`). Turning a subscription off yourself is not a pause: its count is kept.

> **On for new installs.** Outbound webhooks are switched by the `outbound_webhooks` feature flag, on for a new install since W10 and off on an install upgraded with it off until an administrator turns it on, and by offline mode. The page does not say when the flag is off; **Test** answers "No delivery enqueued" until both allow delivery. See the feature-flag reference in the repository docs (`docs/operations/feature-flags.md`).

### Email (SMTP) and channels

**Email (SMTP) & channels** (`/settings/notifications`): the mail server (**Enable SMTP email delivery**, host, port, from address, optional username and password, and **Implicit TLS** or **STARTTLS**; port 465 picks implicit TLS and 587 or 25 STARTTLS, and plain SMTP is not supported). **Send test email** sends to your own address using the **saved** settings, so save first. QA leads can read and test it; saving needs an administrator. The **Shared channels** card shows whether the shared Slack and Teams channels are delivering, with a link to edit them under Integrations. Per-event choices are on each person's **My notifications**.

The **Project notification policy** card decides, for the project selected in the top bar, which events are sent at all, whatever each person ticked. A project that never saved one reads `Using defaults: state changes ON, every run OFF.` QA leads and administrators change it and **Save**; each change is recorded in the activity feed (`Notification policy changed: every run`).

| Switch | Governs | Off means |
|---|---|---|
| **Notify on every run** | Run failed, Run passed and High failure rate, once per run | None of the three is sent in this project |
| **Notify on state changes** | The state changes: newly failing, recovered, newly flaky, quarantined, released from quarantine, quarantine past its SLA, ready to release | None of the seven is sent |
| The seven state-change boxes (with state changes on) | Each state change on its own | That one is not sent |
| **Failed runs in a row before "Test newly failing"** | How many consecutive failed runs make a test newly failing (1-20, 2 by default) | — |

AI analysis complete, quality gate failed and a test or suite becoming slower are not governed by the policy. A project that existed before state changes were introduced kept its old behaviour (state changes off, every run on) until someone changes its policy. In **All Projects** the card asks you to pick a project.

### Digests and views

**Digests & views** (`/settings/digests`): scheduled summaries to email, Slack or Teams — **Daily**, **Weekly**, **Per Run** (all runs, failed only, or degraded only), **Per Release** or **Per Suite**, and **Weekly Retro** when the `weekly_retro_digest` flag is on. For a daily or weekly email to one project, **Attach analysis report** adds the HTML analysis report (see [Reports and outputs](/docs/reports#summary-report-files)). Pause, resume or unsubscribe each digest. **Preview Digest** shows what one will look like.

- **Per Release** names a release: pick it from the top bar project's releases (active and in-progress first). The digest goes out after the AI summary of each run of that release, including a run linked to it as a secondary release. In **All Projects** you are asked to pick a project first.
- **Per Suite** needs a suite name.
- A subscription that cannot deliver says so in its row (`Not delivering — choose a release`, `Not delivering — choose a suite`, `Not delivering — its release no longer exists`), with the fix in the row: choose, then **Save**.

The **Saved Views** tab lists the project's saved report views: the ones saved on Trends, Failures, Coverage and the other reports, plus older empty views (`No filters — edit to add some`). Each row shows where it was saved and its scope (`Releases: 2026.10 · Suites: checkout · Last 7 days`). **New view** opens an editor at the top of the tab: a name, releases, suites (comma-separated), a window (1, 7, 14, 30 or 90 days) and **Shared with the project**; **Edit** opens the same editor in the row.

A **Daily** or **Weekly** digest can use a saved view (**Saved view (optional)**): it then covers only that view's releases, suites and window, and says so (`Filtered by view: Checkout regressions — release 2026.10 · suite checkout · last 7 days`). A release in the view that no longer belongs to the project is left out, with a note; project-wide sections (flaky debt by team, hours saved) are left out of a filtered digest. If the view is deleted, or you can no longer read it, the row says so (`View deleted — this digest covers the whole project`) and the digest covers the whole project until you **Change view**. **Preview Digest** takes the same view.

### Integration health

**Integration health** (`/settings/integration-health`): whether each connection works — Jira, Splunk, GitHub, OpenShift, Slack, Teams, SMTP, the local model server and the vector store — as **healthy**, **degraded**, **down**, **auth error**, **timeout**, **unknown** or **skipped**, with latency. Tabs: **Current Status**, **Trends (7d)** (uptime and failure counts) and **Probe History**. Connections are probed every 15 minutes; **Probe All Now** or **Probe now** on one card checks immediately. Start here when "the webhook didn't fire".

Under the deployment-wide cards, **Current Status** has one row per project integration: each project's **GitHub** (or GitHub Enterprise) and **GitLab** connection, titled with the project (`GitHub · Payment Service`) and the API it reaches (`ghe.corp.example.com/api/v3`), probed with that project's own token. A row never probed yet reads **Not probed yet**, not healthy. **Probe now** checks that one project; **Open GitHub settings** (or **Open GitLab settings**) switches to the project and opens its settings. **Paused webhooks** lists every outbound webhook the circuit breaker paused, how many failures in a row paused it, its last error and **Open Outbound webhooks**. An administrator sees every project; a QA lead sees the projects they are a member of. **Probe History** offers the project rows too; **Trends (7d)** covers the deployment-wide connections only.

## AI

### AI configuration

**AI configuration** (`/settings/ai`): QA leads can read it; administrators change it.

- **Analysis Engine**: **Auto**, **LLM (AI Agent)**, **Machine Learning** or **Rules-Based**, each showing whether it is ready.
- **Model Availability & Fallback Chain**: whether the model server answers and each required model is installed.
- **LLM Provider**: provider, model, temperature, max tokens.
- **Embedding**: the model that turns test results, knowledge chunks and agent memory into vectors for semantic search, similar failures and knowledge-grounded generation. **Provider** is **Bundled MiniLM** (`all-MiniLM-L6-v2`, runs inside TestLookup, the default), **Ollama** (a model on your own Ollama server) or **OpenAI** (refused while offline mode is on). Under it, **In use** names the embedder every search uses now. A change does not apply at once: **Save Configuration** starts a **re-embed** into new collections, the card shows its progress per index (*Re-embedding to Ollama · nomic-embed-text:v1.5: 1,200 / 2,290.*), and semantic search keeps the current model until it finishes. Cached AI analyses, the open-defect duplicate check and failure clusters start empty in the new collections and refill as they are used. If a re-embed fails, the card says why and the model in use stays in use; **Retry** tries again. When the configured embedder is not the one in use (*… is not in use yet.*), an administrator's **Re-embed now** applies it.
- **Pipeline Settings**: confidence threshold, timeout, offline mode, and toggles.
- **Knowledge-Grounded Generation**: **Enable Knowledge RAG** for generating test cases from documents. While the `rag_faithfulness_gate` feature flag is on, **Faithfulness evaluator** chooses how drafts are scored: the single-prompt judge (the configured model, works offline) or Ragas (the configured model too, several calls per case, each inside the cost cap; needs offline mode off and the `ragas` package, which only an image built with `INSTALL_RAGAS=1` has). The badge shows the one in use.
- **Cloud API Keys** for hosted providers, stored encrypted.

Two switches are worth understanding:

- **Analysis mode and provider** — whether AI-assisted analysis uses a language model at all. Without one, analysis falls back to ML or rules and still works. Ask AI needs **Auto** or **LLM**.
- **Offline mode** — a hard ceiling on outbound calls, on by default. With it on, a model is only reached on your own network, and integrations that would reach outside the deployment are gated.

> **Important.** Offline mode is a boundary, not a preference. It is on unless your administrator turned it off; check it here before assuming test data stays local. See [Security and privacy](/docs/security).

### AI agents and agent activity

**AI agents** (`/settings/ai-agents`, one project) sets the policy for the two agents that can act on failures:

- **Investigator** — investigates failure clusters. On by default, in **Shadow** mode: it reasons and records what it *would* do, without acting or proposing. **Suggest** files typed proposals (quarantine a flaky offender, reassign a failure to its suite owner, promote an internal defect) to **Inbox › Approvals › Agent actions**, where a QA lead approves or rejects each one; nothing runs. **Act** runs an approved proposal, but only of the action types ticked on the card: **Quarantine flaky offenders**, **Reassign failures to the suite owner**, **Promote an internal defect** (never Jira), each off until a QA lead ticks it. A person still approves every action, every run is audited, and **Undo** reverses it. Budgets: runs a day, model calls, tokens and seconds per run.
- **Fixer** — proposes fixes for flaky tests, validated by re-running the test, as draft pull requests that a person must merge. Off by default. Its card sets the mode, a schedule (**Off (manual only)**, **Daily**, **Weekly**) and budgets.

Saving a policy needs `QA_LEAD`. **Agent activity** (`/settings/agent-activity`) lists every agent run: agent, mode, trigger, status, tokens, cost and duration, with the **Actions proposed** and **Actions taken** for each, and the Fixer's attempts. In shadow mode, actions taken is always zero; the gap is the evidence for promoting an agent.

### Workflow editor and pipeline runs

**Workflow editor** and **Pipeline runs** are listed here too; see [AI agents and the pipeline](/docs/ai-agents#pipeline-runs).

### AI evaluation

**AI evaluation** (`/settings/ai-eval`) measures how well the AI answers, in six tabs:

- **Overview**: agreement between people and the AI, training-label health, drift, and recent evaluation runs, each marked **Legacy label agreement** or **Replay**.
- **Datasets**: build a dataset from your team's corrections or seed the golden sets. **Run label agreement** scores the labels stored in the dataset (the legacy metric). **Evaluate candidate** replays the dataset through the real engine of one tier (rules, ML, or the configured language model; under offline mode only a local one), up to 200 items, using the LLM budget and release policy of the project chosen in the top bar. **Items** shows each item's expected and candidate output.
- **Gate**: the pre-release gate for classification, root cause, duplicate detection or release decisions, on the legacy metric or on a completed candidate run. It answers **PASS**, **FAIL** or **Not enough evidence** (for example when the baseline uses the other metric or another dataset). **Set as baseline** makes a result or a candidate run the baseline.
- **Agent stack**: **Run gate** runs the four gates for one change and records it; a history row opens its manifest.
- **Tiers** (one project): the G2 tier comparison, its recorded results, and **Label live pairs**: outputs of a local SLM and a language model on the same case, recorded in shadow mode. Choose **Incumbent correct**, **Candidate correct**, **Both correct** or **Neither**; a label is final. **Include live labels** adds the labelled pairs to the comparison.
- **Reviewer** (one project): the reviewer agent's quality history, read only.

QA leads can read every tab and label live pairs (signed in, not with an API key). Evaluating a candidate, running a gate, setting a baseline, changing datasets and opening a run's items need an administrator; those controls are shown disabled with **Needs an administrator.**

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

**Live** (the last button of the filter row) shows new events as they happen: each one appears at the top of the list about a second after it is saved, if it matches your filters, without a reload. It is off until you turn it on, and your browser remembers the choice. While it is connecting or reconnecting, or when live updates are unavailable, a line under the filters says so and the list keeps refreshing every 30 seconds. A live view reconnects by itself every 15 minutes (sooner when your sign-in token runs out) and picks up where it stopped; you can have 3 live views open at once. Your own activity across projects, and what involves you, is in the Inbox's **Activity** tab ([Inbox](/docs/inbox#activity)).

## System

### Data and storage

**Data & storage** (`/settings/storage`): whether the databases and the cache are connected, and where results and artifacts are stored — the object storage (endpoint, bucket, SSL) and the vector store (host, port, **Collection prefix**). QA leads can read it; administrators change it.

**Collection prefix** (in the **ChromaDB (Vector Store)** card): up to 8 lower-case letters, digits or `-`, starting with a letter or a digit, put in front of every vector collection's name, so two deployments can share one ChromaDB server. Empty means no prefix. Like an embedding change, a new prefix re-embeds every index into new collections while search keeps using the current ones until it finishes; the card then reads *In use: collections start with "demo_".* (or *In use: collections carry no prefix.*). A value stored by an earlier version that is not a valid prefix is ignored, and the card says so.

### Performance

**Performance** (`/settings/performance`) is read-only: the latency budgets per operation, the search and connection-pool configuration, and reference scale scenarios.

### Feature flags

**Feature flags** (`/settings/feature-flags`, administrators) lists every flag with its description, a global on/off switch, a rollout percentage, and its scope. **Edit** limits a flag to listed projects or roles; **New Flag** adds one.

A flag is evaluated as: **the global switch is the master switch**, then the project list and the role list **narrow** an enabled flag (an empty list means all), and the rollout percentage must be above zero.

> **Note.** Setting only the project list does nothing — with the global switch off the flag is off everywhere. To enable a flag for one project you need all three: global on, that project listed, rollout 100.

A flag with no record at all evaluates **off**, except for a few older flags that a deployment setting can still switch on. Several workflows ship **off** and say so on their page: quarantine proposals (`flaky_auto_quarantine`), GitHub (`github_checks`) and GitLab (`gitlab`). **Upload report** (`manual_upload`) and Ask AI (`ask_ai_chat`) ship on; so do, on a new install, LLM cost budgets (`llm_cost_budget`), release phase enforcement (`release_phase_gate_enforcement`: completing a phase over a failing gate, or skipping one, asks for a reason), compliance packs (`release_compliance_pack`) and outbound webhooks (`outbound_webhooks`). An upgraded install keeps the values it had. The full list is the feature-flag reference in the repository docs (`docs/operations/feature-flags.md`). Changes can take up to about 30 seconds to reach every page.

### `defect_commander`

The one pipeline stage that mutates state beyond its own records. Enabled, deep runs create defect records. Ticket filing additionally requires Jira to be enabled.

> **Warning.** Enable this only when you want defects created automatically. Test it on one project first.

### Seed data

**Seed data** loads, resets or deletes demo data. It exists only in development builds against a development server.

## Related

- [Security and privacy](/docs/security)
- [API, CLI, SDKs and MCP](/docs/integrations)
- [Finding your way around](/docs/navigation)
