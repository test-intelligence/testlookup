# Release gates & policies

The release gate answers one question: **is this build safe to ship?** Every scored run gets a verdict:

| Verdict | Meaning |
|---|---|
| **GO** | Signals are healthy; nothing in the window argues against shipping |
| **CONDITIONAL_GO** | Shippable with caveats — something tripped a warning; read the evidence before proceeding |
| **NO_GO** | A blocking signal fired — a hard policy rule, a red pass-rate band, or high-risk dimensions |

The verdict is deterministic and auditable: the decision stores a snapshot of every input it read, so you can always see *why* it said what it said. (For the internals, see [architecture/RELEASE_GATE.md](../architecture/RELEASE_GATE.md).)

## Reading the Release Gate page

Open **Release Gate** (`/release-gate`, or `/release-gate/:runId` for a specific run):

- **Release decision flow** — the verdict and the chain of evidence that produced it.
- **Risk Dimension Breakdown** — each scored dimension (pass-rate health, failure recurrence, flakiness, …) with its score, weight, and contribution, so you can see exactly which dimension moved the verdict. The recurrence dimension measures *real* recurrence — how many analysed failures keep coming back — not model confidence.
- **Export PDF / Share Report** — a portable decision record for release meetings.

**Releases** (`/releases`) shows the same gate status per tracked release, so a release manager can watch several candidates at once.

## How the verdict is computed (plain-language version)

Four kinds of signal feed the decision:

1. **Policy rules** — your project policy's rules each evaluate to an action: `BLOCK`, `WARN`, or `INFO`. Any `BLOCK` forces **NO_GO**; a `WARN` downgrades GO to **CONDITIONAL_GO**. Rules only ever push the verdict *down* — passing more rules never upgrades a failing gate.
2. **Pass-rate bands** — the pass rate is classified into your configured bands. A red band acts as a *floor*: it can drag a green composite down, but a green band never lifts a red one.
3. **Risk dimensions** — the weighted scores in the breakdown above.
4. **Flaky intelligence** — tests in active **quarantine** are excluded from the failure signal, so a known-flaky test being worked on doesn't permanently pin you at NO_GO. (That's the payoff of the [flaky workflow](triaging-failures.md#when-its-flaky).)

When signals disagree, the gate takes the *worse* answer. There is no averaging a red signal away.

## Configuring policies

**Policies** (`/policies`) is the editor; each project resolves to one *effective* policy (yours, or the defaults if you haven't created one). New policy: `/policies/new`; edit: click one in the list. A policy has three parts:

- **Thresholds** — the numeric lines (minimum pass rate, maximum failure counts, …).
- **Weights** — how much each risk dimension contributes to the composite.
- **Rules** — individual checks, each with a severity: `BLOCK` (hard gate), `WARN` (downgrade), or `INFO` (recorded, no verdict effect). Start new rules at `WARN`, watch a few runs, then promote to `BLOCK` once you trust them.
- **Pass-rate bands** — the green/amber/red pass-rate ranges used for the floor.

Policy changes apply to future evaluations — historical verdicts keep the snapshot they were decided with.

## Overriding a verdict

Sometimes a human knows better — a NO_GO caused by a test environment outage, say. A permitted user can **override** the verdict on the Release Gate page, and the override:

- requires a **written reason**,
- is recorded **alongside** the computed verdict (the machine's opinion is never erased), and
- is audit-logged.

Use overrides for judgment calls, not as a recurring workaround — if you're overriding the same rule weekly, change the policy instead.

## Gating a CI pipeline

The same verdict is available over the REST API and MCP server, so a pipeline can block a deploy step on it: upload results ([Getting results in](getting-results-in.md)), wait for processing, then fetch the run's gate decision and fail the job on `NO_GO`. The `testlookup` CLI's `runs`/`reports` commands cover the same from shell scripts.

## Tips

- **Read CONDITIONAL_GO, don't rubber-stamp it.** It exists precisely for the "shippable but look first" case — the evidence list is the look.
- **A permanent NO_GO usually means un-triaged flaky tests.** Quarantine the genuinely flaky ones (with the coach's evidence) rather than loosening thresholds.
- **Keep INFO rules around.** They're free telemetry on rules you're considering promoting.
