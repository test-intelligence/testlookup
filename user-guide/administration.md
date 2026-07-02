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
- **Digests** (`/settings/digests`) — scheduled summary digests (daily/weekly roll-ups to a channel).
- **Integration Health** (`/settings/integration-health`) — the status board for everything above: which integrations are configured, reachable, and delivering. Check here first when "the webhook didn't fire".

## Operating the instance

- **AI Settings / AI Evaluation** (`/settings/ai`, `/settings/ai-eval`) — covered in [AI features](ai-features.md#configuring-the-ai-tier-settingsai-settingsai-eval): mode, local model, budgets, and the AI-quality dashboard.
- **Feature Flags** (`/settings/feature-flags`) — runtime toggles (e.g. `manual_upload`, quarantine features). Admin-only; flags are cached, so allow a moment for changes to propagate.
- **Audit Dashboard** (`/settings/audit`) — the tenant audit flow: who did what, when — gate overrides, quarantine decisions, admin mutations. Backed by append-oriented audit storage, so it survives UI changes.
- **Storage** (`/settings/storage`) — object-storage (MinIO/S3) status for reports, compliance packs, and RAG documents.
- **Project Data** (`/settings/project-data`) — per-project data management: retention and cleanup operations. Admin-only and deliberate — actions here delete data.
- **Seed Data** (`/settings/seed-data`) — load the demo dataset into a project (the same data `make quickstart` ships), useful for evaluating features or training a team without real CI wired up.
- **Performance** (`/settings/performance`) — instance performance diagnostics.
- **Billing** (`/settings/billing`) — usage/spend views (AI spend also surfaces in the [Intelligence Hub](ai-features.md#run-intelligence-intelligence-runsidintelligence)).

## Admin checklists

**New instance** (after [GETTING_STARTED](../GETTING_STARTED.md)):
1. Create your project(s) and invite users with the right roles.
2. Create API keys for CI and hand teams the [ingestion guide](getting-results-in.md).
3. Configure suite owners / ownership rules so triage routes to people.
4. Review the default release-gate policy (`/policies`) and adjust bands/rules.
5. Leave `AI_OFFLINE_MODE` on until you deliberately need outbound integrations.

**Something isn't arriving?** Integration Health → Audit Dashboard → the [ingestion troubleshooting list](getting-results-in.md#troubleshooting), in that order.
