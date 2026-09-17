# EXP-BUG-041 — summary task audited before any delivery

**Mission / severity:** M08, P1.

The summary worker committed a distribution audit before it staged recipients or
a provider succeeded. Recipientless and failed work therefore looked delivered,
and successful work produced a second relay audit.

The worker now performs projection only. The token-fenced relay remains the
single audit writer and stages the audit only after provider success in the same
outcome transaction.

**Green evidence:** exact candidate `b3a0d5ee` deployed as
`build-20260917-084451`; the no-early-audit regression and the notification
replay mutation harness.
