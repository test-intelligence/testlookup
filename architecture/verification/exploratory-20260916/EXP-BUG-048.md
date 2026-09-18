# EXP-BUG-048 — immutable notification still depended on Mongo availability

**Mission / severity:** M08, P2.

The worker queried the mutable run summary even when the durable operation
already carried all source bytes. A Mongo outage could therefore delay or fail a
self-contained notification.

Mongo is now read only for legacy operations without frozen source bytes. The
immutable-source regression makes the Mongo accessor fail if the worker reaches
it, and the notification mutation harness proves that guard is required.

**Green evidence:** exact candidate `b3a0d5ee` deployed as
`build-20260917-084451`; immutable notification regression and the notification
replay mutation harness.
