# Releases and gates

Grouping runs into a release and reading the readiness recommendation.

> **Important.** TestLookup does not decide whether you ship. It produces a **recommendation** with its reasoning and evidence attached. The decision stays with you.

## Releases

A release is a named grouping of test runs. Associating runs gives the gate something to assess.

## The recommendation

The release-risk stage produces one of three values, plus the reasoning behind it:

| Value | Means |
|---|---|
| **GO** | No blocking signals found in what was assessed |
| **CONDITIONAL_GO** | Proceed only if the listed conditions are handled |
| **NO_GO** | Blocking issues present |

Alongside it you get a **risk score**, **dimension scores**, **blocking issues**, **conditions for go**, and the **version of the scoring model** used — so a recommendation from last month can be read in the terms that produced it.

## The seven risk dimensions

The composite risk score is built from seven dimensions. Each is scored from the run's own evidence:

| Dimension | Reads as |
|---|---|
| **User impact** | Product-bug share weighted by open defect pressure |
| **Environment sensitivity** | How much of the failure is environmental |
| **Reproducibility** | Whether the failure repeats or wanders |
| **Regression likelihood** | Whether this looks like something that used to pass |
| **Historical recurrence** | Whether this has happened before |
| **Blast radius** | How far across suites the failure reaches |
| **Diagnosis confidence** | How sure the analysis is — low confidence *raises* risk |

That last one is worth pausing on: **uncertainty counts against you.** An analysis that cannot tell you what went wrong makes the release riskier, not safer.

## The decision rules, in order

The engine evaluates these in sequence and takes the first that matches:

| Condition | Result |
|---|---|
| Pass rate below **70% of your configured bar** | **NO-GO** — catastrophic, whatever the composite says |
| Composite risk at or above the NO-GO threshold | **NO-GO** |
| Composite risk at or above the GO threshold | **CONDITIONAL GO** |
| Pass rate below your configured bar | **CONDITIONAL GO** |
| Otherwise | **GO** |

The first rule is a hard floor. A catastrophic pass rate produces NO-GO regardless of how the composite scored — no combination of otherwise-healthy dimensions can talk the gate out of it.

## Pass-rate bands can only make it stricter

Where pass-rate bands are configured they can **tighten** a verdict but never loosen one. A band can turn a GO into a CONDITIONAL GO; it can never unblock a NO-GO.

## How it is produced

Scoring is deterministic: dimensions are scored, combined into a risk score, and mapped to a recommendation. Narrative text around it may be model-written, but **the verdict is arithmetic**, not generated.

> **Note.** If the scorer fails, the stage substitutes `CONDITIONAL_GO` at risk 50 with "manual review required" — and records that it did so. A substituted verdict is stored in the same shape as a computed one, so the pipeline logs the substitution explicitly. If you see a bare CONDITIONAL_GO at exactly risk 50, check whether it was computed.

## Reading it honestly

- The gate assesses **the signals it has**. A suite that does not test something cannot report risk in it.
- `GO` means "nothing blocking was found", not "this release is safe".
- Blocking issues are the part to read. The single word at the top is a summary of them.

## Overrides

A recommendation can be overridden by an authorised user. The override is recorded with who made it — the point is an auditable decision, not a silent one.

Required role: `QA_LEAD` or higher to override.

## Related

- [How decisions are made](/docs/decisions)
- [Reports and outputs](/docs/reports)
