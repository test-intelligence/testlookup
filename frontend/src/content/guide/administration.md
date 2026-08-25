# Administration

Settings that need `ADMIN`, and what they change.

## Users and roles

Five roles, each including those below it:

| Role | Can |
|---|---|
| `VIEWER` | Read dashboards and reports |
| `TESTER` | Read runs and failures |
| `QA_ENGINEER` | Triage, correct classifications, manage suites |
| `QA_LEAD` | Ownership, release-gate overrides |
| `ADMIN` | Everything below, plus the settings on this page |

Users see only projects they are a member of. Project membership, not role alone, decides visibility.

## Projects

Create, configure and delete. **Deletion is a soft delete** — rows remain but the project disappears from listings and its runs stop appearing in run lists.

> **Warning.** Soft-deleted projects are invisible but not gone. If historical runs "disappeared", check whether the project was deleted before assuming data loss.

## API keys

Issue and revoke at **Settings → API keys**. A key carries the permissions of the user who created it and is shown **once**. Revoke immediately on suspicion of exposure.

## AI configuration

Choose the provider and model used for AI-assisted analysis. Credentials belong in the deployment's secret configuration.

Two independent switches worth understanding:

- **Provider configuration** — whether AI-assisted analysis is possible at all. Without it, analysis falls back to rules and still works.
- **Offline mode** — a hard ceiling on outbound calls. When set, integrations that would reach outside the deployment are gated.

> **Important.** Offline mode is a boundary, not a preference. Verify its value on your deployment before assuming test data stays local. See [Security and privacy](/docs/security).

## Feature flags

Optional behaviour is gated per project. Flags are evaluated as: **`enabled_global` is the master switch**, `enabled_projects` **narrows** an already-enabled flag to specific projects, and `rollout_percent` must be non-zero.

> **Note.** Setting only `enabled_projects` does nothing — with `enabled_global` false the flag evaluates off everywhere. To enable for one project you need all three: global on, that project listed, rollout 100.

A flag with no record at all evaluates **off**. That is deliberate: absent means off, never on.

### `defect_commander`

The one pipeline stage that mutates state beyond its own records. Enabled, deep runs create defect records. Ticket filing additionally requires Jira to be enabled.

> **Warning.** Enable this only when you want defects created automatically. Test it on one project first.

## Retention and purge

How long records are kept. Purging is irreversible.

## Notifications and webhooks

Configure destinations and events. Notification content can include test error text — consider that when choosing a channel.

## Audit

Administrative actions and decision overrides are recorded. Audit rows are append-only by design: a correction adds a record rather than editing one.

## Related

- [Security and privacy](/docs/security)
- [API, CLI, SDKs and MCP](/docs/integrations)
