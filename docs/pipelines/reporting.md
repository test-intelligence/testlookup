# Reporting, summaries and release decisions

[Documentation home](../README.md)

“Report” covers several products. They have different inputs, aggregation and approval semantics.

| Output | Inputs and behavior | Code |
|---|---|---|
| Run intelligence | Combines run counts, analyses, clusters, baseline diff, risks/actions and stored summary/decision views | [run intelligence](../../backend/app/services/run_intelligence_service.py) |
| AI run summary | Structured executive/incident/evidence/action layers plus provenance | [summary agent](../../backend/app/agents/summary_agent.py), [summary assembler](../../backend/app/services/summary_assembler.py) |
| Decision report | Evidence-bound assembled decision document and separately tracked generation/verification attempts | [decision report](../../backend/app/services/decision_report_service.py), [critic](../../backend/app/agents/decision_report_critic_agent.py) |
| Project summary | Window or latest-per-suite aggregation, pass/fail/skip/flaky views | [summary report](../../backend/app/services/summary_report_service.py) |
| Downloadable HTML/PDF | Composed report and sanitized renderer, with review-aware export policy | [composition](../../backend/app/services/report_composition_service.py), [reports route](../../backend/app/routers/reports.py) |
| Evidence/compliance ZIP | Related artifacts and audit evidence under scope/policy controls | [evidence bundle](../../backend/app/services/evidence_bundle_service.py), [compliance](../../backend/app/services/compliance_pack_service.py) |
| Release/phase decision | Policy and evidence rollup with stored snapshot/reasons/history | [release gate decisions](../../backend/app/services/release_gate_decision_service.py), [phase gate](../../backend/app/services/release_phase_gate_service.py) |

## Aggregation and evidence

Project summary `window` mode includes selected-window runs; `latest` chooses the latest per suite. Weighting is by test counts, not an average of run percentages. The evaluated-test denominator is passed + failed + broken, so skipped tests do not inflate pass rate. Distribution percentages and unique-test metrics can use other explicitly labeled bases. Preserve the response's denominator/basis rather than reconstructing percentages in each client.

Run aggregates may precede live detail rows; per-suite breakdown reads cases while headline totals can use run aggregates. Mixed visibility during finalization is expected and should not be mistaken for a durable equality invariant. A stored accepted report and the latest failed generation attempt are separate objects; displaying the latest attempt must not silently replace a valid report's authority.

## Report lifecycle and distribution

```mermaid
flowchart LR
  Evidence[Scoped persisted evidence] --> Generate[Generate or assemble]
  Generate --> Validate[Contract and critic verification]
  Validate --> Review[Review request and envelope]
  Review --> Accepted[Human accepted]
  Review --> Pending[Pending review]
  Review --> Rejected[Rejected or superseded]
  Accepted --> Policy[Distribution policy]
  Pending --> Policy
  Rejected --> Policy
  Policy --> Export[Download share notification or CI projection]
```

With `REVIEW_GATE_ENFORCED=false`, policy refusals are generally observed/audited for rollout. With enforcement active, accepted/non-AI output can be distributed. A pending draft needs project permission or an authorized interactive `include_unreviewed` request and receives a draft watermark plus audit. Rejected/superseded output cannot use that pending-draft exception. HTTP responses can expose `X-TestLookup-AI-Generated` and `X-TestLookup-Review-State`; clients should preserve review meaning in downloads, search snippets, notification payloads and MCP output. [distribution service](../../backend/app/services/report_distribution_policy.py).

Share links use their own token/access/expiry semantics and remain subject to report policy. They are implemented in this snapshot. Compliance packs are exports of evidence; generating one is not a legal compliance certification.

## Release decisions

Run-level release signals and release/phase-level policy decisions are related but distinct. Release-level persisted verdicts include `GO`, `CONDITIONAL_GO`, `NO_GO`, `NOT_EVALUATED`. Insufficient evidence is a real state; do not coerce it to green. Review gating can project `PENDING_REVIEW` on outward-facing verdicts when configured.

Current release decisions are append-only records with previous rows demoted, policy/evidence snapshot, denominator and reasons retained. Partial unique indexes enforce at most one current decision for release/phase scope, with a flush between demotion and promotion. Overrides and recomputation must preserve authority/history. Always check which release, phase, policy version, run set, environment and baseline the verdict describes.

## Export and notification failures

Export generation can fail separately from analysis, and delivery can fail separately from generation. Long-running work belongs on workers or renderer thread offload where implemented. Diagnose storage, renderer errors, report availability, review refusal, destination policy and retry/DLQ records independently. Do not bypass current report review by replaying an old webhook payload; delivery paths must re-evaluate policy.
