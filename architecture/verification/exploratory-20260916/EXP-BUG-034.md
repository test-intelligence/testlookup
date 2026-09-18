# EXP-BUG-034 — delivery history retained stale review projections

**Mission / severity:** M08, P1.

Notification and webhook relays refreshed review-gated bytes only in memory.
Their history rows kept the originally queued body or payload, so history could
show a draft after a rejected redacted delivery, or pending content after an
accepted delivery.

Successful token-fenced transitions now persist the exact notification title,
body and public metadata or webhook event payload that reached the provider.
Failed attempts keep the private retry context needed for the next live review
check.

**Green evidence:** exact homelab candidate `843a6565`, focused relay tests and
notification/webhook history mutations.
