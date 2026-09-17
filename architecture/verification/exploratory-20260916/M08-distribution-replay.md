# M08 — distribution, drafts and notification replay

**Result:** PARTIAL — all executable policy, replay, exact-subject and audit
variants passed; real external receivers and a dedicated enforcement-on stack
are unavailable.

**Window:** 2026-09-17T03:58Z–2026-09-17T08:48Z

**Final executable candidate:** `b3a0d5eee86bda958e7b2a0b01ffb7e9463505d0`,
deployed before final testing as `build-20260917-084451`, built at
`2026-09-17T08:44:52Z`. All application deployments and workers rolled to the
candidate and were ready. `/health/version` reported the exact revision. The
backend manifest digest was
`sha256:60b128c197ace68e2a171cdedbc9d759b4b32f1494333c7a89f210ebdc3d01c1`;
frontend and MCP retained their content-identical immutable digests.

## Consumer and state matrix

| Subject | Consumers exercised | Pending, project off | Pending, project on | Accepted | Rejected / superseded |
|---|---|---|---|---|---|
| Run summary | JSON export, HTML/PDF report/share, notification, digest | withheld when enforced; would-refuse audit when off | watermarked and audited | original content | narrative withheld |
| Decision report version | versioned export/read paths | exact pipeline and evidence pending state | watermarked where the consumer supports drafts | only that version's pipeline-and-evidence review authorizes it | terminal content cannot borrow a newer review |
| Investigator narrative | notification/digest excerpt | review-subject notice | watermarked excerpt | exact investigation narrative | narrative withheld |
| Release decision | readiness projection and `release.decided` webhook | `PENDING_REVIEW` | original value plus `draft_watermark` | original value | blockers, conditions and draft recommendation redacted |
| PR/MR labels | GitHub/GitLab comment builders | labels stripped | labels plus draft note | labels included | labels stripped while enforced |

The automated PR/MR comments are created before the AI pipeline and are not
reposted when review later becomes accepted. That is the documented product
gap; no post-review delivery was invented for this mission.

## Findings and fixes

Twenty-six defects are recorded as EXP-BUG-023 through EXP-BUG-048. JSON intelligence
exports now include the draft watermark and commit their access audit. Immutable
decision report versions resolve review authority from their own pipeline and
evidence hash. Rejected and superseded release and notification projections
remove model narratives and original verdicts. The interactive advisory release
escape is restricted to QA Lead and Admin callers and records the actor-bound
distribution audit. Release readiness uses the persisted decision pipeline.

Durable notification attempts preserve the original AI source only as internal
delivery context and reapply the current review state before every provider
attempt. `release.decided` deliveries likewise store their run and pipeline
subject, reconstruct the original recommendation, recheck exact review state,
honour `allow_unreviewed_distribution`, and stage the appropriate audit with a
successful token-fenced delivery transition. Notification audits are likewise
staged only after provider success and token ownership is confirmed. Both
relays persist the exact successful projection in delivery history. Retry after
acceptance restores accepted content; a later rejection or supersession cannot
leak the queued narrative. Withheld notification prefixes retain deterministic
failure facts without inventing a release verdict. The relay rebuilds that
prefix instead of trusting legacy queued metadata. Summary outbox and durable
delivery rows carry the producing pipeline and evidence hash, and enforced
legacy rows without both identifiers fail closed. `release.decided` starts
after the immutable DecisionReport exists, stores its evidence hash, and binds
every retry to pipeline plus evidence. The summary worker no longer records a
distribution audit before recipients are staged or a provider succeeds.

The queued notification operation now freezes its original executive narrative,
panel and AI provenance instead of reloading mutable run-wide summary bytes.
Agent release events originate only after immutable DecisionReport publication,
carry the critic's pipeline and evidence hash, refuse a concurrent pipeline
replacement or hash mismatch, and source all decision fields from that report.
Human override events continue to use the committed human value without requiring
an agent report. Decision-row locking orders an agent event against a concurrent
override. Each override carries its own validated audit ordinal, timestamp and
frozen council snapshot, so overlapping emitters cannot collapse into the newest
value. Self-contained summary operations no longer query Mongo.

## Verification

- 251 focused distribution, review-envelope, notification, outbox,
  decision-report and webhook tests passed against exact deployed `b3a0d5ee`.
- Six mutation wrappers passed and killed 48 mutations. Their scripts assert every replacement applies
  exactly once, require each wrong behavior to fail, and restore source bytes.
  Covered mutations include watermark removal, skipped audit commit, run-wide
  review lookup, terminal narrative restoration, advisory authorization bypass,
  stale notification/webhook retries, lost webhook run identity, ignored project
  draft settings, invented withheld verdicts, pre-provider audits, stale history,
  dropped evidence bindings, missing-subject authorization, pre-delivery
  summary audits, omitted successful-delivery audit, mutable queued source bytes,
  pre-report release emission, dropped critic pipeline binding, report-hash
  mismatch, override dependence on an agent report, delayed agent publication
  after override, mutable override scopes/snapshots, a missing decision lock,
  an accepted mismatched override-audit timestamp and unnecessary Mongo access
  for immutable notification operations.
- Ruff passed for `backend/app` and `backend/tests`; the mypy ratchet held at 369
  errors in 114 files; all 43 quality guards passed.
- Homelab deployment completed migration and rollout checks before the test run.
  PostgreSQL, MongoDB and Redis readiness stayed healthy and every backend worker
  used `build-20260917-084451`.
- Independent final read-only review first found eight authority, redaction,
  audit and history defects. A second pass found four remaining exact-subject,
  legacy-row and audit defects. A third pass found two release-event ordering
  defects; all fourteen findings were repaired before the final approval pass.

## Deviations and remaining gaps

The shared homelab remains `REVIEW_GATE_ENFORCED=false`, matching the owner's
release-date activation decision. There is no isolated deployment where the
flag can be enabled without changing other users' behavior. Enforcement-on
behavior is therefore proven by executable policy/regression tests, not claimed
as a deployed real-sink pass.

No dedicated SMTP inbox, Slack workspace, Teams channel, public webhook
receiver, GitHub repository or GitLab project is configured for this execution.
The SSRF boundary correctly rejects an in-cluster private receiver, so real sink
payload bytes, attachment hashes and receiver counts remain BLOCKED rather than
being inferred from mocked manager calls. SMTP is also disabled in the shared
configuration. Public-share access-count concurrency and digest send/watermark
crash recovery remain follow-up reliability scenarios for a sink-enabled
environment.

No review accept/reject credential rule, state-machine transition, stored agent
configuration, capability schema or migration changed. The webhook event gained
the additive `pipeline_run_id`, `evidence_bundle_sha256` and optional
`draft_watermark` fields needed to bind and label its review subject.
