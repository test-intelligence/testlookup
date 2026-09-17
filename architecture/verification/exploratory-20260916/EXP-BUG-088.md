# EXP-BUG-088 — Invocation SSE was unusable and could miss public changes

**Mission / severity:** M12, P0.

The event route inherited global Bearer/API-key authentication even though
browser `EventSource` cannot send that header, so a valid stream ticket still
received 401. Ticket allocation also accepted a token collision, and stream
deduplication ignored changed errors and other public fields. The event route is
now mounted on a ticket-authenticated public router, allocation retries
collisions, and change detection serializes the full public response.

Unit tests cover binding, replay, fail-closed storage, collisions and observable
changes. A mounted-app regression fails if global header auth returns. The
deployed journey opened SSE without headers, observed a frame, then proved both
single-use replay and real Redis TTL expiry return 401.
