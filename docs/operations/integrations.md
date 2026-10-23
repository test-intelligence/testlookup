# Integrations

[Documentation home](../README.md) · [Configuration](configuration.md) · [Feature flags](feature-flags.md)

How to connect TestLookup to issue trackers, chat, email, log and cluster sources, identity providers and your own automation, and how to operate the queues behind them. Every setting named here is described in [Configuration](configuration.md); the endpoint contracts are in the generated [API reference](../reference/api-index.md).

Two rules apply to everything on this page:

- **Offline mode wins.** `AI_OFFLINE_MODE` defaults to `true`, and while it is on most outbound integrations stay silent even when configured. Read [Offline mode and egress allow-lists](#offline-mode-and-egress-allow-lists) before you wonder why nothing is sent.
- **A value saved in the UI wins over the environment.** Values saved under Settings, Integrations (`/settings/integrations`) and Settings, Email and channels (`/settings/notifications`) are stored in the database (credentials encrypted with `APP_SECRET_KEY`) and win over the environment on every runtime path: Jira (every path), Splunk, OpenShift, Slack, Teams and SMTP, and the health probes. The environment applies to a key never saved, or saved empty; a project's own value (Splunk index, OpenShift namespace) comes first. The full rule is in [Which value applies](#which-value-applies). Settings pages are QA_LEAD or ADMIN.

## Which value applies

One resolver (`integration_config_service`) decides every integration value, for the request paths, the agent tools, the workers and the health probes alike. First match wins:

| Key | Order |
|---|---|
| any outbound call | `AI_OFFLINE_MODE=true` refuses it (read live at each call, never cached) |
| Splunk index | the project's Splunk Index (`projects.splunk_index`, non-empty) → `SPLUNK_INDEX` (default `main`) |
| OpenShift namespace of a run | the run's own namespace → the project's OCP Namespace → the saved Default Namespace → `OCP_DEFAULT_NAMESPACE` |
| every other text key or switch | saved at `/settings/integrations` (a text that is non-empty after trimming, any saved boolean) → the environment variable |
| every secret | the `secret_refs` row → a legacy inline value → the environment variable (clearing a token in the UI expires it, so the environment applies again) |
| GitHub / GitLab | per project only (`/settings/github`, `/settings/gitlab`); `GITHUB_TOKEN` feeds only the deployment-wide GitHub row of Integration health |

- A text saved empty (or only spaces) counts as **never saved**, so clearing a field hands it back to the environment; a saved boolean is always a value (saved `false` beats `SPLUNK_ENABLED=true`).
- `GET /api/v1/settings/integrations` returns `environment_fields`: the names of the keys whose shown value (or `*_set`) comes from the environment because they were never saved. Never a value.
- `PUT /api/v1/settings/integrations` stores the saved row merged with the fields sent, never the environment's values, and the page sends only the fields the reader changed. Before this release the page re-sent every field it showed, so an earlier save may have copied environment values into the row (they then no longer follow the environment and are not listed in `environment_fields`); save the field empty to hand it back.
- Each process caches a project's resolved configuration for **30 seconds**; the saving process drops its cache at once, a worker sees a save within 30 seconds. Offline mode is never cached.
- **The egress check.** A Splunk base URL, an OpenShift API URL and `https://<Jira domain>` are checked at save and again before every call, before any credential is sent: a host that resolves to a loopback, link-local (`169.254.169.254` included) or unspecified address is refused (`422 Target host is not allowed` at save; the call is refused at use). RFC 1918 addresses (on-premises Splunk, OpenShift, GitHub Enterprise, GitLab) are allowed, and so is a host that does not resolve (nothing is reachable through it). The per-project GitHub and GitLab bases are checked the same way. The save also refuses a URL that is not `http(s)://` with a host and no user name or password, a Jira domain with a scheme or path, and a Default Namespace that is not a DNS label.

## Jira

TestLookup talks to **Jira Cloud** through REST API v3 with basic authentication (account email plus API token).

**What uses it**

- The one-click "Create Jira issue" on a defect, with recurrence de-duplication (a recurring failure adds a comment to the open issue instead of filing a new one) and a status sync that mirrors Jira status onto linked open (and in-progress) defects every 15 minutes. Usage is described in [user-guide/defects.md](../../user-guide/defects.md).
- Triage on the Defects page (`PATCH /api/v1/projects/{project_id}/defects/{defect_id}`): a Jira key a person links is **checked** against Jira (`GET /rest/api/3/issue/{key}`), and a status change on a linked defect is **written back** by moving the issue to a status in the matching category (`GET .../transitions`, then `POST .../transitions`; nothing is sent when the issue is already in that category or no transition leads there). The link itself is always built by the server from the configured domain (`https://<JIRA_DOMAIN>/browse/<KEY>`).
- Approving a promoted defect in Inbox › Approvals: when the promotion named a Jira project key, the approval files the ticket on that defect, or links it to the open issue already filed for the same failure (the one-click flow's de-duplication and claim, so the two paths never file one failure twice).
- Jira as a knowledge source (`knowledge_rag` flag) and the Jira fix-version release sync.
- Tickets filed by the AI pipeline's triage stage and by `POST /api/v1/integrations/jira` (the legacy client). The `defect_commander` stage (flag) and defect promotion never file at once: a promotion that names a Jira project waits for approval, and the ticket is filed when it is approved.

**Set it up**

1. Create an API token for a Jira account at the Atlassian account security page. Use a dedicated service account. It needs, in the target projects: browse projects, create issues, add comments and (for the recurrence comment and status sync) read issues.
2. Either set the environment (`JIRA_ENABLED=true`, `JIRA_DOMAIN=example.atlassian.net` without a scheme, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_DEFAULT_PROJECT_KEY`) or fill in the Jira card at `/settings/integrations`.
3. Set `AI_OFFLINE_MODE=false` (Jira is off-box).
4. Check `/settings/integration-health`, which probes Jira with the stored configuration.

**Which configuration applies.** Every Jira path (the one-click issue, the Defects-page link check and write-back, the approval filing, the knowledge connector, the release sync, the triage stage, defect promotion and `POST /api/v1/integrations/jira`) uses the configuration saved in the UI, falling back to the environment per key ([Which value applies](#which-value-applies)). The ticket link is built on the saved domain (`https://<domain>/browse/<KEY>`). A domain that resolves to a loopback, link-local or unspecified address is refused at save (`422`, `Target host is not allowed`) and before every call.

**Offline mode.** With `AI_OFFLINE_MODE=true` the one-click issue answers 503 ("outbound Jira calls are blocked"). The Defects-page link check, the write-back and the approval filing are gated by the same rule (Jira configured, enabled, and offline mode off) and make **no** call while it fails: a linked key is saved without a check (`saved_locally`, reason `offline_mode`, `disabled` or `not_configured`), a status change is saved without moving the Jira issue (`write_back: not_sent`), and an approval is committed with "Jira not filed" (`jira_status: not_filed`). Use the one-click flow's `target="webhook"` instead: it emits the [`defect.create_requested`](#outbound-webhooks) event with the full prefilled ticket so your own automation can file it. The legacy client (the triage stage, defect promotion's ticket and `POST /api/v1/integrations/jira`) is gated the same way, offline mode first: the route answers **503** with `Jira is unavailable: AI_OFFLINE_MODE is enabled, so outbound calls are blocked.` (or `… the Jira integration is disabled in Settings › Integrations.`, `… set the domain, email and API token in Settings › Integrations.`, `… Target host is not allowed.`), the triage stage files nothing, and no request leaves. It retries a create only after an error that provably sent nothing (connect error, connect or pool timeout); a read timeout or a 5xx is reported, never re-posted, so a slow Jira cannot get the same ticket twice.

**Online behaviour of the Defects-page calls.** The link check answers 422 for an issue Jira does not have (`Jira has no issue ABC-999.`) and 502 when Jira answers another error or cannot be reached; in both cases nothing is saved. The write-back runs after the change is committed and never undoes it: a refused transition (for example a workflow screen with required fields, HTTP 400) is returned as `write_back.status = "failed"` and shown to the person, with no retry. The account needs, in addition to the permissions above, "Transition issues" in the target projects. Every call has the client's 15-second timeout.

### Jira resolution webhook

When a linked Jira issue is resolved, Jira can tell TestLookup, which then marks the defect `RESOLVED` (statuses done, resolved, closed, fixed) or `INVALID` (resolutions won't fix, duplicate, invalid, not a bug, cannot reproduce) and records AI feedback on the original analysis (`CORRECT` for a fix, `INCORRECT` for an invalid one).

1. Generate a secret (`openssl rand -hex 32`) and set it as `JIRA_WEBHOOK_SECRET` in the backend environment. There is no UI field for it. While it is unset or a placeholder the endpoint answers **403** to every delivery, and a production start logs a warning if Jira is enabled.
2. In Jira (System, WebHooks) create a webhook with URL `https://testlookup.example.com/api/v1/feedback/jira-webhook`, the same secret, and the "Issue updated" event, optionally limited by JQL to the projects TestLookup files into. The URL must be reachable from Jira Cloud.
3. Jira signs each delivery with `X-Hub-Signature: sha256=<hex HMAC-SHA256 of the raw body>`. A missing or wrong signature gets **401**.

Replay protection: a delivery must carry Jira's numeric `timestamp`, be at most 7 days old and at most 15 minutes in the future; each body is accepted once (de-duplicated in Valkey/Redis for the replay window). Refused or repeated deliveries get 200 with `applied: false` so Jira does not retry them. If Valkey/Redis is unreachable the endpoint answers 503 and Jira retries later.

## Slack

Messages are posted to Slack **incoming webhooks** as Block Kit messages.

- **Deployment-wide channel:** `SLACK_ENABLED=true` and `SLACK_WEBHOOK_URL`, or the Slack card at `/settings/integrations` / `/settings/notifications`. An incoming webhook posts to the channel it was created for. `SLACK_DEFAULT_CHANNEL` is accepted for compatibility and not used: the UI's Default Channel field is gone, and the API accepts `slack_default_channel` on save and ignores it (the read still returns it, marked deprecated). `SLACK_BOT_TOKEN` is optional and only used by the integration health probe (Slack `auth.test`) when no webhook URL is set; nothing is sent with it.
- **Per team:** Settings, Team channels (`/settings/team-channels`, QA_LEAD) maps an ownership team to an `email`, `slack` or `teams` target, so a failure owned by that team goes to its channel.
- **Per user:** each user can set a personal Slack or Teams webhook under Settings, My notifications; left blank, the shared channel is used.
- An on-premises Slack-compatible server (for example Mattermost) on a private address must be named in `WEBHOOK_PRIVATE_ALLOWED_HOSTS`; webhooks otherwise refuse private addresses.
- Offline mode: the deployment-wide webhook may reach only hosts listed in `OFFLINE_NOTIFICATION_ALLOWED_HOSTS` (for example `hooks.slack.com`); team and personal webhooks never use that list and must be on-box.

## Microsoft Teams

`TEAMS_ENABLED=true` and `TEAMS_WEBHOOK_URL` (or the Teams card in the UI). TestLookup posts an Adaptive Card (`attachments` with `application/vnd.microsoft.card.adaptive`) to the URL, which works with Teams incoming webhooks created through Workflows. Team channels, personal webhooks, `WEBHOOK_PRIVATE_ALLOWED_HOSTS` and the offline rules are the same as for Slack (allow-list example: `.webhook.office.com`).

## Email (SMTP)

Email carries notifications, scheduled digests and analysis-report attachments.

1. Configure the relay under Settings, Email and channels (`/settings/notifications`) or with `SMTP_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`. The UI value wins; the password is stored encrypted and never returned (the form only says that one is saved).
2. **Pair the TLS mode with the port.** Every connection is encrypted; plain SMTP is not supported.

   | `SMTP_TLS` | Mode | Usual port |
   |---|---|---|
   | `true` | implicit TLS (SMTPS) | 465 |
   | `false` | STARTTLS (required) | 587 |

   The environment defaults (`SMTP_TLS=true`, `SMTP_PORT=587`) do not match each other: with port 587 set `SMTP_TLS=false`, or use port 465. The UI form switches the mode for you when you type 465 or 587 and warns about a mismatch; a mismatch fails with an SSL "wrong version number" error.
3. Use "Send test" on the page, then check `/settings/integration-health`; the SMTP probe connects (and logs in, if a user is set) with the same configuration and TLS rule the sender uses.
4. `SMTP_FROM` must be an address your relay may send as.

Offline mode: an on-box relay may deliver. An off-box relay must be listed in `OFFLINE_NOTIFICATION_ALLOWED_HOSTS`, and once `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS` is set, every recipient must be in those domains (`corp.example` exact, `.corp.example` subdomains). An off-box relay admitted by the host list is refused unless the recipient-domain list is also set.

## Splunk

The agent pipeline's log tools search Splunk for log lines around a failure (search jobs API, bearer token).

- The Splunk card at `/settings/integrations` (Enabled, Base URL, API Token) or `SPLUNK_ENABLED=true`, `SPLUNK_BASE_URL` (management port, for example `https://splunk.example.com:8089`), `SPLUNK_API_TOKEN` (a token whose user may run searches on the index). The saved value wins per key ([Which value applies](#which-value-applies)); the agent tools, the log-intelligence stage of a deep investigation and the health probe all use it.
- **The index** is the project's **Splunk Index** (Edit Project), else `SPLUNK_INDEX` (default `main`). Both are checked at use (letters, digits, `_`, `-`, 1-80 characters), and a project's at save, so nothing else can reach the search.
- **What runs.** Before the model is asked, the server itself runs the Splunk correlation for a failure that carries a service and a timestamp (the **Re-analyse** path, `POST /api/v1/analyze`), attaches the result to the question and records it as a tool step, so the evidence appears even with a small model that skips tools, or when the model call fails. The model then gets that result instead of a second search. The anomaly and trace tools (deep investigation) use the same configuration.
- **Offline mode refuses every Splunk call**, on-box hosts included; so does a disabled or unconfigured integration, an invalid index and a refused host, each with a fixed sentence in the evidence and no request.

## OpenShift and Kubernetes event correlation

TestLookup can pull pod events from a cluster to correlate failures with restarts, OOM kills and scheduling problems.

- The OpenShift card at `/settings/integrations` (Enabled, API URL, Default Namespace, Service Account Token) or `OCP_ENABLED=true`, `OCP_API_URL` (for example `https://api.cluster.example.com:6443`), `OCP_SA_TOKEN`, `OCP_DEFAULT_NAMESPACE`. The saved value wins per key, as for Splunk.
- The token needs read access (`get`, `list`) to `events` and `pods` in the namespaces your tests run in; a namespaced Role and RoleBinding for a dedicated service account is enough.
- **The namespace** of a run is the run's own, else the project's **OCP Namespace**, else the saved Default Namespace, else `OCP_DEFAULT_NAMESPACE`. A sentinel upload that names a pod but no namespace stores the resolved namespace on the run when it is ingested; the pod's metadata is then fetched by a worker task (`enrich_run_ocp_metadata`, `ingestion` queue) after the ingest has committed, never inside the ingest's transaction. A namespace that is not a DNS label or a pod name that is not a DNS subdomain is refused without a request.
- The server-side look-up of the pod's events runs before the model is asked, as for Splunk (the Re-analyse path, when the failure names a pod).
- **Offline mode refuses every OpenShift call**, on-box clusters included. Outbound TLS to the cluster API follows [Corporate CA and TLS interception](#corporate-ca-and-tls-interception).

## GitHub and GitLab

- **GitHub check runs and the sticky PR comment** (flag `github_checks`, off by default) are configured per project at `/settings/github` with a personal access token. Setup, token scopes and behaviour: the GitHub section of [user-guide/administration.md](../../user-guide/administration.md).
- **GitLab merge-request notes and commit statuses** (flag `gitlab`, off by default) are configured per project at `/settings/gitlab`: the GitLab section of the same page, plus the CI template [user-guide/reference/testlookup-gitlab-ci.yml](../../user-guide/reference/testlookup-gitlab-ci.yml).
- Both need `AI_OFFLINE_MODE=false`. A GitHub Enterprise or self-hosted GitLab behind an internal CA needs `HTTP_CA_BUNDLE`.
- **Commits between two runs** (the run's Changes tab, the regression diff, the build-changes agent tool and the Suspects panel) come from the project's own GitHub / GitHub Enterprise integration, offline-gated and behind the `github_checks` flag; the Changes tab says why when none can be listed. The instance-wide `GITHUB_TOKEN` and `GITHUB_REPO` are **no longer read** for commits (they bypassed the egress check and offline mode): `GITHUB_TOKEN` now feeds only the deployment-wide GitHub row of Integration health, and `GITHUB_REPO` has no reader.

## Single sign-on (SAML 2.0)

TestLookup supports **SAML 2.0** only; there is no OIDC or LDAP login. SSO is configured by an ADMIN at Settings, SSO and identity (`/settings/sso`) or through `/api/v1/sso/configs`, and needs `SSO_ENABLED=true` in the environment.

**Service-provider values for your IdP**

- `GET /api/v1/sso/metadata` (public) returns the entity ID (`SAML_SP_ENTITY_ID`), the ACS URL (`<SAML_BASE_URL>/api/v1/sso/acs`) and the NameID format (`emailAddress`). Set `SAML_BASE_URL` to the public URL that reaches the backend and `SAML_SP_ENTITY_ID` to an identifier you control (for example `https://testlookup.example.com/saml/metadata`). Single logout is not supported: the metadata lists no SLO endpoint, and signing out of TestLookup ends only the TestLookup session, not the IdP's.
- The IdP must sign assertions; TestLookup verifies the signature against the configured IdP certificate and refuses everything if none is configured.
- Send the user's email as NameID or as an `email` / `mail` attribute, an optional display name (`displayName`, `name` or `cn`), and group memberships in an attribute you name in the configuration.

**The SSO configuration** (one active configuration is used)

| Field | Meaning |
|---|---|
| `idp_entity_id`, `idp_sso_url`, `idp_certificate` (PEM) | From the IdP's metadata. `idp_slo_url` is accepted but unused (no single logout). |
| `sp_entity_id`, `sp_acs_url` | Must equal what the IdP sends: the assertion's audience must match `audience` (or the SP entity ID) and its recipient must match `sp_acs_url`. |
| `group_attribute`, `role_mapping`, `default_role` | Group-to-role mapping, for example `{"qa-leads": "QA_LEAD", "engineers": "QA_ENGINEER"}`. A user gets the highest mapped role, else `default_role` (default `VIEWER`). Roles are re-evaluated on every login. `SSO_MAX_PROVISIONED_ROLE` caps the result. |
| `enforcement_mode` | `OPTIONAL` (password login still works) or `SSO_REQUIRED` (non-admin users must use SSO; admins keep password login while `SSO_ADMIN_FALLBACK_ENABLED=true`). |

`POST /api/v1/sso/configs/{id}/test` checks that the IdP endpoint is reachable (private IdP addresses need `SSO_ALLOW_PRIVATE_IDP_ENDPOINTS=true`). New users are created just in time on their first SSO login; logins, role changes and failures are recorded as identity events (`/api/v1/identity`).

**Security settings:** responses must answer a login request minted by `GET /api/v1/sso/login-url` (`InResponseTo`) unless `SAML_ALLOW_IDP_INITIATED=true`; each assertion ID is accepted once; `SAML_CLOCK_SKEW_SECONDS` sets the tolerated clock drift.

**The browser flow (SP-initiated).**

1. **Sign in with SSO** calls `GET /api/v1/sso/login-url?next=<app path>`. It returns a redirect to the IdP carrying a SAML `AuthnRequest` (HTTP-Redirect binding). The request ID is recorded and must come back as `InResponseTo`; `RelayState` carries `next`, which must be an app-relative path.
2. The IdP posts its response to `POST /api/v1/sso/acs`. A browser (`Accept: text/html`) gets a 303 redirect to `<PUBLIC_BASE_URL>/sso/callback`, with a one-time code and `next` in the URL fragment. API clients that post without `Accept: text/html` still receive the session tokens as JSON.
3. The app exchanges the code for a session with `POST /api/v1/sso/exchange`. The code is single-use and expires after 60 seconds. A failed sign-in lands on `/login?sso_error=1`.

`PUBLIC_BASE_URL` must be the URL users open, because it is the redirect target. The `AuthnRequest` is not signed, so an IdP that requires signed AuthnRequests is not supported. Test SSO end to end with your IdP before you set `SSO_REQUIRED`, and keep the admin fallback on.

MFA: SSO users authenticate at the IdP. Workspace MFA policy, enrolment, lockout and the breakglass procedure for password users are covered in the "Multi-factor authentication & account lockout" section of [user-guide/administration.md](../../user-guide/administration.md).

## SCIM 2.0 provisioning

SCIM lets the IdP create, update and deactivate TestLookup users.

1. Set `SCIM_ENABLED=true` (otherwise every SCIM request answers 404).
2. As ADMIN, create a SCIM token at `/settings/sso` (SCIM tokens) or `POST /api/v1/scim-tokens` with a `name`, an optional `sso_config_id` and an optional `expires_days` (1-365). The token (`scim_` prefix) is shown once; only a hash is stored.
3. In the IdP set the SCIM base URL to `https://testlookup.example.com/api/v1/scim/v2` and the authentication to `Authorization: Bearer <token>`.

Supported: `/Users` (list with filter, get, create, replace, patch, delete) and the discovery endpoints `/ServiceProviderConfig`, `/ResourceTypes`, `/Schemas`. There is no `/Groups` endpoint: group memberships arrive in the user's `groups` attribute and, when the token is bound to an SSO configuration, are mapped to a role through that configuration's `role_mapping` (capped by `SSO_MAX_PROVISIONED_ROLE`); otherwise new users get `VIEWER`. A bound token also requires `externalId` on create and replace. `DELETE` deactivates the user (soft delete). Revoke a token with `DELETE /api/v1/scim-tokens/{id}`.

## Outbound webhooks

Outbound webhooks send TestLookup events to your own HTTPS endpoint. They need the `outbound_webhooks` [feature flag](feature-flags.md) **and** `AI_OFFLINE_MODE=false`. Migration 0224 turns the flag **on for a new install** (no user and no project yet); an upgraded install keeps its stored value, off unless an administrator turned it on (`PATCH /api/v1/feature-flags/outbound_webhooks` with `{"enabled_global": true}`). While either is off the subsystem is silent: nothing is queued, the "Send test" action reports that no delivery was enqueued, and replay answers 503.

**Subscriptions** are per project, managed at `/settings/webhooks` or `/api/v1/webhooks` (QA_LEAD; a project-scoped API key with admin scope also works). A subscription has a `name`, a `target_url` (`http` or `https`; it must resolve to a public address, checked at registration and at every delivery, and redirects are not followed, unless its host is listed in `OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS`, below), `events` (at least one), `enabled`, an optional signing `secret`, `max_retries` (0-10, default 5) and `breaker_threshold` (1-100, default 10; left out of an update, the stored value is kept). The read adds `consecutive_failures`, `disabled_reason` (`circuit_open` or `manual`) and `disabled_at`. `GET /api/v1/webhooks/events` lists the catalogue.

**Events**

| Event | Emitted when | Emitted today |
|---|---|---|
| `run.completed` | A run finished ingestion and post-processing. Also the event "Send test" sends, with `"is_test": true`. | yes |
| `defect.create_requested` | A user filed a one-click defect with `target="webhook"`. `data` carries the prefilled ticket: signature, summary, description, issue type, test and suite names, cluster ID, occurrences, CI context, AI analysis, deep link, extra comment, requester. | yes |
| `release.decided` | The release-risk agent wrote a decision at the end of a deep investigation, or a QA lead overrode one. Carries the recommendation (`GO`, `NO_GO`, `CONDITIONAL_GO`, or `PENDING_REVIEW` while `REVIEW_GATE_ENFORCED` holds an unreviewed AI decision), risk score, blocking issues and review state. | yes |
| `flaky.quarantined` | A QA lead approved a quarantine request (needs `flaky_auto_quarantine`). | yes |
| `quota.exceeded` | A project hit its LLM cost hard cap (needs `llm_cost_budget`). | yes |
| `defect.promoted` | A defect promoted from a failure cluster took effect: at promotion when no approval was needed, or when a QA lead approved it in Inbox › Approvals. `data` carries `defect_id`, `project_id`, `cluster_id`, `run_id`, `title`, `severity`, `jira_key` and `jira_url` (`null` until a ticket exists), `approval_status`, `deep_link` and `promoted_at`; no description, evidence or owner. Exactly one delivery per subscription per defect, however often an approval is retried. | yes |

**Request format.** Each delivery is an HTTP `POST` with a JSON body:

```json
{"event_type": "run.completed", "delivery_id": "<uuid>", "subscription_id": "<uuid>", "project_id": "<uuid>", "emitted_at": "2026-10-11T08:00:00+00:00", "data": {}}
```

Headers: `Content-Type: application/json`, `User-Agent: TestLookup-Webhook/1.0`, `X-TestLookup-Event: <event_type>`, `X-TestLookup-Delivery: <delivery_id>` and, when the subscription has a secret, `X-TestLookup-Signature`.

**Verifying the signature.** `X-TestLookup-Signature` is the lowercase hex HMAC-SHA256 of the **raw request body bytes**, keyed with the subscription secret (UTF-8). There is no `sha256=` prefix and no timestamp in the signed input, so de-duplicate on `X-TestLookup-Delivery` (stable across retries of one delivery) and compare in constant time:

```python
import hashlib
import hmac

def verify(secret: str, raw_body: bytes, header_value: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value or "")
```

**Delivery and retries.** Deliveries are queued after the event commits and sent by a Celery worker with a 10-second timeout.

- A 2xx response is success.
- `408`, `425`, `429`, `500`, `502`, `503`, `504` and network errors are retried with exponential backoff: 30 s, 60 s, 120 s, 240 s, then every 480 s. A subscription allows at most `max(1, max_retries)` attempts in total. When they are exhausted the delivery is marked `DLQ`.
- Any other status (other 4xx, 3xx redirects) is marked `FAILED` immediately; so is a target that resolves to a private address.
- Every attempt is recorded. History: `GET /api/v1/webhooks/{id}/deliveries`; replay a `FAILED` or `DLQ` delivery (as a new delivery, attempts reset) with `POST /api/v1/webhooks/{id}/deliveries/{delivery_id}/replay`.
- `release.decided` deliveries re-check the review gate at send time.

**Private test receivers.** `OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS` (comma-separated exact host names, case and a trailing dot aside; no wildcards or ranges; empty by default) lets a subscription target a private or loopback host: a listed host skips the public-only check at create, update and delivery and is reached through the shared client (`HTTP_CA_BUNDLE` honoured). Any QA lead of any project may then point a subscription at it, so name a test receiver or a trusted internal endpoint only. Offline mode still refuses every delivery. `WEBHOOK_PRIVATE_ALLOWED_HOSTS` does not apply to subscriptions; it covers Slack and Teams webhooks only.

### Outbound webhook circuit breaker

A subscription whose receiver keeps failing is paused instead of failing every event for ever.

- **What counts.** A delivery counts **once**, when it reaches a terminal status the subscription is responsible for: `DLQ` after its retries are spent, or `FAILED` at once for a refused (private or unsafe) target, a missing signing secret or a non-retryable 4xx. An attempt that will be retried does not count (a read timeout is not a failure until the retries are spent). "Subscription disabled", "webhooks disabled or offline" and the relay's own "dispatch attempts exhausted" never count. `failure_count` keeps counting every failed attempt; the breaker uses `consecutive_failures`, which a `SUCCESS` sets back to 0.
- **The trip.** When `consecutive_failures` reaches `breaker_threshold`, one guarded `UPDATE … RETURNING` in the delivery's own transaction sets `enabled = false`, `disabled_reason = 'circuit_open'` and `disabled_at`; row locks make it exactly one trip however many workers fail at once. The same transaction records the activity event `webhook.circuit_opened` ("Webhook <name> paused after N consecutive failures", system actor) and an email notification to the subscription's last editor and the project's active QA leads (their notification bell lists it; it carries no link). `testlookup_webhook_circuit_opened_total` counts trips, after the commit.
- **While paused** no event creates a delivery for it, and `POST /api/v1/webhooks/{id}/test` reports that none was enqueued. A delivery already queued is marked `FAILED` ("subscription paused after N consecutive failures"), replayable and not counted. Integration health lists the subscription under **Paused webhooks** (`GET /api/v1/integration-health/status?scope=all`, `kind: "webhook"`, `status: "paused"`).
- **Re-enable** is `PATCH /api/v1/webhooks/{id}` with the full body and `enabled: true` (the page's **Re-enable**): `consecutive_failures = 0`, `disabled_reason` and `disabled_at` cleared. Turning a subscription off by hand writes `disabled_reason = 'manual'` and keeps the count. Lowering the threshold below the current count does not trip by itself; the next terminal failure does. Then replay the failed deliveries you want.

## Inbound bucket-notification webhook (`/webhooks/minio`)

`POST /webhooks/minio` is an optional, legacy ingestion trigger: an S3 server's bucket notification announces that an upload finished, and TestLookup queues its ingestion. **The default ingestion paths do not need it**: `POST /api/v1/ingest`, `/api/v1/ingest/file`, the streaming API, the CLI and the SDKs queue ingestion themselves.

- Authentication: the shared `WEBHOOK_SECRET`, as `Authorization: Bearer <secret>` (what a MinIO webhook target's `auth_token` sends) or `X-Webhook-Secret: <secret>`. Anything else gets 403. The S3 server bundled with the Compose and Kubernetes stacks sends its event webhook without an authorization header, so it cannot use this endpoint and is not wired to it.
- Only notifications for objects in `MINIO_BUCKET_NAME` whose key ends in `upload_complete.json` are acted on. The key must have the shape `<project_id>/runs/<build_number>/upload_complete.json` with plain path segments; the result files are read from the same prefix, and the project comes from the key, never from the notification body.
- The endpoint always answers 200 quickly (`queued` or `ignored` with a reason) so the S3 server does not retry in a loop; ingestion errors surface in the worker and the dead-letter queue.

## Offline mode and egress allow-lists

`AI_OFFLINE_MODE=true` (the default) is the switch for air-gapped and data-sovereign installs. `/settings/ai` can turn it on but never off; only the environment can turn it off. While it is on:

| Path | Behaviour |
|---|---|
| LLM calls | Only local providers whose endpoint resolves to loopback or private addresses (checked again at connect time). `AI_LLM_PROVIDER_ALLOWLIST` and `AI_LLM_ALLOWED_BASE_URLS` can restrict further. |
| ChromaDB embedder | May not download model weights; point `CHROMA_ONNX_MODEL_DIR` at a side-loaded copy or semantic features fall back to keyword search. |
| Outbound webhooks, GitHub checks and PR comments, GitLab, the Fixer | Off. |
| Jira one-click issue, the Defects-page link check and status write-back, the ticket filed on approving a promoted defect, the triage stage's tickets and `POST /api/v1/integrations/jira` (503), external knowledge sync, Splunk, OpenShift, the run Changes tab's commit lookup and the build-changes tool | Off, on-box hosts included. A linked key is saved without a check, a status change and an approval are saved without a Jira call; the Splunk and OpenShift look-ups say they are disabled; the Changes tab says `offline mode`. |
| Deployment-wide Slack/Teams webhooks and the SMTP relay | Allowed when on-box, or when the host is listed in `OFFLINE_NOTIFICATION_ALLOWED_HOSTS` (comma-separated; `.example.com` matches subdomains). Email additionally obeys `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS`. A refused delivery is recorded as blocked, and the API logs each refused configured destination at startup. |
| Team and personal Slack/Teams webhooks | Allowed only when on-box; the host allow-list does not apply (it would admit every tenant on a shared host such as `hooks.slack.com`). |

Every Jira, Splunk, OpenShift and GitHub path checks `AI_OFFLINE_MODE`: the two gaps of earlier releases are closed (the legacy Jira client is gated, and the `GITHUB_TOKEN` commit lookups are gone). Air-gapped installation as a whole is covered in [user-guide/air-gapped-install.md](../../user-guide/air-gapped-install.md).

## Corporate CA and TLS interception

Every outbound HTTP client that TestLookup's own code builds (the shared and public clients, webhooks, Slack, Teams, Jira, GitHub, GitLab, CODEOWNERS and commit lookups, the Fixer, the Ollama checks and the local-LLM transports) verifies TLS with `http_verify()`:

1. `HTTP_CA_BUNDLE`: path to a PEM bundle inside the container. Verification stays on; the bundle replaces the default trust store for these clients, so include the public roots you still need (or build the bundle from the system store plus your CA).
2. Else `HTTP_VERIFY_TLS=false`: verification off, with a warning in the log. For isolated labs only.
3. Else the system trust store.

Mount the bundle (Compose volume, Kubernetes Secret or ConfigMap) and set `HTTP_CA_BUNDLE` to its path.

Hosted LLM provider SDKs build their own HTTP clients and do not read `HTTP_CA_BUNDLE`. The httpx-based ones (OpenAI, and OpenRouter through the OpenAI client, Anthropic) honour `SSL_CERT_FILE`, which the production backend image sets to the system bundle `/etc/ssl/certs/ca-certificates.crt`. To trust a corporate CA there as well, either rebuild the backend image with the certificate in `backend/certs/` and `--build-arg INSTALL_EXTRA_CA=1` (the build adds it to the system bundle), or mount a combined bundle and point `SSL_CERT_FILE` at it.

## Integration health

`/settings/integration-health` (API: `GET /api/v1/integration-health/status`, `POST /api/v1/integration-health/probe` to probe now, `/history/{provider}`, `/trends`) shows the last probe of each integration: Jira, Splunk, GitHub, OpenShift, Slack, Teams, SMTP, Ollama and ChromaDB. Probes run every 15 minutes from Celery beat, use the same stored configuration the senders use, and respect offline mode (a refused destination is reported as such, not as down). The Splunk and OpenShift probes apply the egress check first (a refused host reads `down`, "Target host is not allowed", and no token is sent).

**Per-project probes.** Every enabled per-project GitHub / GitHub Enterprise and GitLab integration is probed with its own API base and token: the targets are read on one short database session that is closed before any HTTP, then at most 5 probes run at a time (`GET {api_base}/repos/{owner}/{name}` with the PAT, or `GET {base}/api/v4/projects/{path}` with `PRIVATE-TOKEN`). Health rows are keyed `(provider, scope_key)`: `scope_key` is `''` for a deployment-wide row and the project id for a project row (migration 0223). The beat prunes the rows of integrations that were removed or turned off.

- `GET /status` answers the deployment-wide rows (unchanged); `GET /status?scope=all` adds one row per enabled integration of the reader's projects (an administrator: all; a QA lead: their own) and one `kind: "webhook"` row per paused subscription. A row never probed reads `probed: false`, `status: "unknown"`, "Not probed yet. Probe now, or wait for the 15-minute check." (never `healthy`).
- `POST /probe?provider=github&project_id=<id>` (or `provider=gitlab`) probes one project; the query `project_id` is checked against the reader's projects before anything is read (`403` otherwise), `404` when the project has no enabled integration of that kind, `422` for GitLab without a project. `POST /probe` with neither probes the deployment-wide integrations and the reader's project integrations.
- `GET /history/{provider}?project_id=<id>` reads one project's probe history (same check); without it, the deployment-wide history. `/trends`, `GET /api/v1/settings/integrations/health` and the `testlookup_integration_health{provider}` gauge stay deployment-wide; `testlookup_integration_project_health{provider,status}` counts the project rows per status (12 series, never a project label).
- Offline: every project row reads `skipped` and no token is read.

## Operator runbook: dead letters, outbox and backfills

These endpoints are for instance administrators: ADMIN through a user session or a personal key, never a project-bound API key. They act across every project. Reference: [admin DLQ](../reference/api/admin-dlq.md), [admin maintenance](../reference/api/admin-maintenance.md).

**Dead letters**

- `GET /api/v1/admin/dlq?limit=50` lists the newest entries of both dead-letter stores in Valkey/Redis: `persist_live_session` (live-session persistence that exhausted its retries; written while `INGESTION_DLQ_ENABLED=true`) and the stream store (Celery tasks that failed for good). Counts are included. A store that cannot be read is a 503, never an empty list. `GET /api/v1/admin/maintenance/dlq` returns the same view.
- `POST /api/v1/admin/dlq/{entry_id}/replay` re-dispatches one stream entry and removes it once the broker accepts the task (202). Only `ingest_test_run`, `run_agent_pipeline` and `generate_run_compare_report` entries can be replayed; others answer 409, as does a replay already in progress.
- `/health/ingestion` reports DLQ depth for alerting.

**Run outbox.** A finished run's follow-up work (`persist_live_session`, `run_notifications`, `transition_notifications`, `agent_pipeline`, `ai_summary_notifications`, `suite_comparison`, `run_completed_webhook`) is published from a durable outbox. An intent that keeps failing is marked `failed` and not retried. To put such intents back:

```bash
# 1. Dry run (the default): list what would be requeued.
curl -X POST "$TL_URL/api/v1/admin/maintenance/outbox/requeue" \
  -H "Authorization: Bearer $TL_TOKEN" -H 'Content-Type: application/json' \
  -d '{"operation": "agent_pipeline", "last_error": "broker_TypeError", "limit": 100}'
# 2. Requeue for real, in slices of at most 500, oldest first.
curl -X POST "$TL_URL/api/v1/admin/maintenance/outbox/requeue" \
  -H "Authorization: Bearer $TL_TOKEN" -H 'Content-Type: application/json' \
  -d '{"operation": "agent_pipeline", "last_error": "broker_TypeError", "limit": 100, "dry_run": false}'
```

`last_error` is required and must match exactly, so a requeue never re-runs notifications or webhooks that failed for their own reasons; `run_id` narrows it to one run. Each requeued `agent_pipeline` intent starts an AI pipeline (and spends LLM budget). A pipeline still marked running refuses the retry until the stale-pipeline reaper fails it (every 10 minutes, after 30). Every requeue, dry runs included, is written to the access audit log.

**Manual beat tasks and backfills** (`POST`, ADMIN):

| Endpoint | What it does |
|---|---|
| `/api/v1/admin/maintenance/backfill-placeholder-test-cases?max_runs_per_project=500` | Creates placeholder test-case rows for historical runs that have none. Idempotent. |
| `/api/v1/admin/maintenance/backfill-unassigned-failures` | Provisions the default QA-lead user where a project lacks one, then assigns unassigned failed and broken results through the suite-owner resolver. Writes only to unassigned rows. |
| `/api/v1/admin/maintenance/drain-active-live-sessions` | Runs the 30-second live-session drain now. |
| `GET /api/v1/admin/maintenance/ai-cache` | Health of the AI similarity cache, including documents left in the old unscoped collection. |

## AI evaluation: datasets, baselines and gates

The AI evaluation API (`/api/v1/ai-eval`; QA_LEAD or ADMIN depending on the endpoint) and the dashboard at `/settings/ai-eval` measure the quality of the AI analysis against labelled data. Design: [architecture/AI_EVALUATION.md](../../architecture/AI_EVALUATION.md) and [architecture/AI_QUALITY.md](../../architecture/AI_QUALITY.md); endpoints: [AI evaluation reference](../reference/api/ai-evaluation.md).

- **Datasets:** `POST /datasets` creates one; `POST /datasets/from-feedback` builds one from recorded feedback on analyses; `POST /golden-datasets/seed` seeds the golden sets; `GET /label-health` reports how much of the training data is human-labelled versus LLM pseudo-labels (see `ML_HUMAN_LABEL_FLOOR`, `ML_PSEUDO_LABEL_CAP`).
- **Runs and drift:** `POST /runs/evaluate/{dataset_id}` evaluates the current configuration against a dataset; `GET /runs`, `GET /drift` and `GET /dashboard` show results over time. Celery beat also runs a daily agent evaluation (04:00 UTC) and a weekly quality-drift check (Mondays 06:00 UTC).
- **Baselines and gates:** `POST /baselines` records the metrics to hold a model or agent to; `POST /pre-release-gate` compares current metrics with the baseline and returns pass or fail (use it in a release pipeline before changing `LLM_MODEL` or a prompt); `POST /agent-stack-release-gate` does the same for the whole agent stack, with history at `GET /agent-stack-release-gate/runs`; `GET /gates/{checksum}` fetches a recorded gate result. `POST /tier-comparison` and `POST /reviewer-quality` compare model tiers and human reviewers; `/report-cycles` tracks report-level evaluation cycles (published, tenant-authorised reports only, unless `AI_REPORT_EVAL_ALLOW_CALLER_CORPUS=true` in a test environment).

LLM spend caps are a separate feature: [LLM cost budget](feature-flags.md#llm-cost-budget).
