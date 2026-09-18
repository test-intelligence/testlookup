# M18 — Integration, digest and notification settings

## Result

**PARTIAL — six production defects are fixed and the exact executable was
deployed before testing; secret migration, local-time scheduling, draft tests,
seed isolation, and credentialed provider exercises remain open.** Candidate
`f7d2aaf0` was deployed as `build-20260918-055620`. `/health/version` returned
the full revision, every application deployment was ready on the candidate
tag and expected digest, all health endpoints were green, and Alembic reported
`0191 (head)`.

## What was proved

- Jira, Splunk, OpenShift, GitHub, Slack, and Teams probes receive one resolved
  global integration configuration. Encrypted database secrets take precedence
  over legacy inline and environment defaults.
- On-demand probes use the saved endpoints and credentials. Their responses
  retain the existing healthy, authentication, degraded, timeout, and down
  distinctions without returning credentials.
- Runtime email and digest delivery resolve the encrypted SMTP password from
  the same secret authority as the settings test endpoint, and encryption-key
  failures stop delivery instead of selecting stale environment credentials.
- Global integration writes implement the established tri-state contract:
  omission preserves a secret, a non-empty value rotates it, and an empty value
  expires it. Reloaded responses expose only the corresponding set flags.
- A scheduled digest claims its next slot before provider I/O for duplicate
  suppression, but advances `last_delivered_at` and `delivery_count` only after
  success. A known provider failure schedules a 15-minute retry and retains the
  previous delta watermark and original first-delivery window. Disabled SMTP is
  a delivery failure, and the final update cannot overwrite a concurrent
  subscription schedule change.
- Explicit null or an empty list clears feature-flag project and role scopes;
  omitted fields still preserve the current allow-list.

## Verification

- Homelab authority: revision
  `f7d2aaf02631eefe8e20f7292a35e8a61b0d3f10`; tag
  `build-20260918-055620`; backend and worker digest
  `sha256:ad5ba0a0c8827fc22eea11af9d3fdd6fbc5afbe93d2da9100955c0df0f9e7788`;
  frontend digest
  `sha256:a3bd457a2ff0f6c3815e7e3f53f27861a0e8030e33a1d8f5284ddcd9b242cffe`;
  MCP digest
  `sha256:0dbe7c85653c85043e4ae28d35d60cb52f8bf31176acc6370c5626f88d6c7263`.
- Focused M18 backend suite: **67 passed**. Broader connector, digest, feature
  flag, authorization, and persistence suite: **154 passed**.
- Backend Ruff passed; mypy held at **367/367**. TypeScript passed; ESLint
  reported **0 errors** and 19 existing warnings. Focused settings UI suite:
  **11 passed**.
- Quality gate: all **43 guards** green. Quality-gate self-tests: **238
  passed**.
- Mutation harness: **12 unsafe changes killed**. Every mutation selector was
  asserted to apply exactly once and the original bytes were restored.
- Independent final review: **APPROVE** for executable `f7d2aaf0` and evidence
  commit `4d7f8cf4`; no blocking finding remains.

## Defects fixed

EXP-BUG-108 through EXP-BUG-113.

## Deviations and remaining gaps

The shared homelab has no disposable provider accounts, notification sinks, or
project fixture. No saved credential, feature flag, digest subscription, or
seed data was mutated there. Jira sandbox work remains blocked. Safe local
tests covered provider status mapping and retry decisions against synthetic
clients after the exact sources were deployed.

Personal Slack and Teams webhook overrides remain plaintext
`NotificationPreference` columns and API response fields; migrating them needs
an encrypted preference-scoped secret design and backfill. Digest subscriptions
still lack IANA timezone, local time, and weekday fields, so DST and calendar
schedule behavior is not implemented. Connector tests remain saved-config
tests rather than non-persisting draft tests. GitHub still needs GitLab's
load-failure and dirty-state protections. Seed reset/wipe still identifies
some users by reserved email instead of explicit seed ownership. Jira issue and
knowledge runtime clients still read process environment values even though
the health probe now uses saved settings. These product gaps keep M18 partial.
