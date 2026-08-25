# Security and privacy

What TestLookup holds, who can see it, and what may leave the deployment.

## What it holds

Test results, and test results are not neutral data. They routinely contain:

- **Error messages and stack traces** — which often include file paths, hostnames, internal URLs, and sometimes values from your test fixtures.
- **Test and suite names** — which describe your product's features.
- **Branch and build metadata** — which describes your development activity.

Treat the contents as you would your source repository.

## Isolation

- **Projects are the boundary.** Runs, tests, failures, defects and search results belong to one project.
- **Membership decides visibility.** A user sees only projects they belong to; role determines what they can do within them.
- **Scoping is enforced server-side**, including when a project is named explicitly in a request — a caller cannot read another project's runs by supplying its id.

## Authorisation

Five roles, least to most: `VIEWER`, `TESTER`, `QA_ENGINEER`, `QA_LEAD`, `ADMIN`. Endpoints handling a project resource check access to *that* project, not merely that you are signed in.

## API keys

A key carries the permissions of the user who created it, and is displayed once.

> **Warning.** A key in a build log is a leaked credential. Store keys in your CI secret store, never in the repository, a ticket, or a chat message. Revoke on any suspicion.

## What leaves the deployment

This is the part worth reading carefully.

| Configuration | What leaves |
|---|---|
| **No AI provider** | Nothing. Analysis falls back to rules |
| **Local / self-hosted model** | Nothing leaves your network |
| **External AI provider** | Failure evidence — error text, stack traces, test names — is sent to that provider for analysis |
| **Issue tracker enabled** | Defect titles and descriptions go to that tracker |
| **Webhooks / notifications** | Whatever the event carries, to the destination you configured |
| **MCP client** | Whatever the client queries may reach the model behind it |

> **Important.** With an external provider configured, **the content of your failures is sent to a third party**. That may be entirely acceptable — it is a decision to make deliberately, with the same care as any other data-processing choice, not one to discover later.

An **offline mode** setting acts as a hard ceiling on outbound integrations. Verify its value on your deployment rather than assuming a default.

## Report sharing

A shared report carries its contents, including error text and stack traces.

> **Warning.** Review a report before sharing it outside the team.

## Retention and deletion

Retention is administrative. Project deletion is a **soft delete** — data remains until purged. If you need data genuinely removed, purge it; do not rely on deletion alone.

## Audit

Administrative actions and decision overrides are recorded, append-only. A correction adds a record rather than editing one, so the trail shows both the original and the change.

## Administrator responsibilities

- Decide whether an external AI provider is acceptable, and confirm the offline setting matches that decision.
- Keep project membership current — it is the isolation boundary.
- Rotate API keys and revoke unused ones.
- Set retention deliberately.
- Review what notification and webhook destinations receive.

## Limitations

- TestLookup does not scan or redact test output. If your tests print secrets, those secrets are stored.
- Isolation is per project. A user in two projects sees both.
- Shared reports are as protected as the link.

## Related

- [Administration](/docs/administration)
- [API, CLI, SDKs and MCP](/docs/integrations)
