# Compliance & governance

For regulated teams, "we shipped it" isn't enough — you need to show *why it was safe to ship*, with evidence, after the fact. A **compliance pack** is TestLookup's answer: a point-in-time, exportable snapshot of everything behind a release decision, assembled in one action and stored durably.

> Compliance packs are an **enterprise feature** behind the `release_compliance_pack` flag. If the controls below aren't visible, an admin needs to enable the flag ([Administration → Feature Flags](administration.md#operating-the-instance)); the API returns 503 when it's off.

## What's in a pack

Generating a pack for a release captures, as of that moment:

- **The release & its decision** — the run, the GO / CONDITIONAL_GO / NO_GO verdict, and the evidence behind it (see [Release gates](release-gates.md)).
- **The policy snapshot** — the exact thresholds, weights, rules, and bands the verdict was computed against (not today's policy — the one in force then).
- **The decision trail** — the sequence of steps and decisions that produced the verdict, including any human override and its recorded reason.
- **Failure clusters** — what was failing and how it grouped.
- **Defects** — the tracked bugs linked to the release.
- **Audit events** — the who-did-what around the release (overrides, quarantine decisions, admin actions).

The pack is self-contained and reproducible: because the policy and decision are snapshotted, a pack means the same thing a year later even if your policies have changed since.

## Generating and downloading a pack

From a release (**Releases** → the release, where the compliance-pack panel lives):

1. Generate the pack for the release.
2. It's assembled server-side and stored durably (object storage), so it survives UI and data changes.
3. Download it for your audit trail / release record.

The same is available programmatically — the MCP server exposes `list_compliance_packs` and `generate_compliance_pack`, so an assistant or pipeline can produce the evidence bundle without clicking through the UI ([CLI, SDKs & MCP](cli-sdk-mcp.md#the-mcp-server)).

## Resilience by design

Pack assembly is deliberately fault-tolerant: the decision-trail and audit-event sections **self-guard** — if one can't be gathered, it embeds an error note in the pack rather than failing the whole generation. You get the pack you can get, with gaps marked, instead of nothing.

## The governance surround

A compliance pack draws on systems documented elsewhere:

- **Audit Dashboard** (`/settings/audit`) — the live who-did-what the pack snapshots ([Administration](administration.md#operating-the-instance)).
- **Decision Trail** — the per-decision provenance also visible in the AI surfaces ([AI features](ai-features.md)).
- **Release Gate & policies** — the verdict and the policy it was judged by ([Release gates](release-gates.md)).

## When to generate one

- **At each release decision** — especially any CONDITIONAL_GO or overridden verdict, where "why did we ship anyway?" is the question an auditor will ask.
- **Before a policy change** — a pack freezes the old policy's evidence for releases already judged under it.
- **On request** — audit, incident review, or a customer's compliance ask.
