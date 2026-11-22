---
name: testlookup-websocket-quality
description: Review and improve TestLookup WebSocket, SSE, and realtime tests. Use for live execution and activity feeds, stream contracts, reconnect recovery, replay, fan-out, pacing, and persisted completion.
---

# TestLookup WebSocket and Realtime Quality

## Identify the stream and its authority

- Locate the transport and consumer first. Read `docs/reference/api/live-stream.md` and `docs/reference/api/live-reporting.md` for live execution. Follow `backend/app/routers/stream.py`, `backend/app/services/ws_event_ingest.py`, `backend/app/streams` and the consuming hooks. For Activity SSE, inspect `backend/app/routers/activity.py` and `frontend/src/services/activityStream.ts`. Match the stream's current authorization and vocabulary.
- Trace scoped producer identity, admission, event identity, broker state, cross-process fan-out and materialization into the run. Locate the actual consumer and subscription lifetime. Record the guarantees for acknowledgement, deduplication, replay and snapshots in the tested protocol.
- Compare a healthy connection with a documented reconnect, duplicate delivery under the same event identity and terminal readback where applicable. Check the client's state after a resume gap or snapshot and the persisted totals after completion. Verify buffer bounds, proxy or idle timeouts, and cleanup. For fan-out changes, connect the producer and consumer to different API processes and record their topology.

## Verify across boundaries

- Use `backend/tests/test_live_protocol_schema.py` and `backend/tests/test_live_frames_vocab_frontend_parity.py` for schema and vocabulary. Use `frontend/src/hooks/liveFrameModel.test.ts` for state application. Choose `backend/tests/integration/test_live_fanout_redis.py` or `backend/tests/integration/test_live_recovery_protocol_postgres_redis.py` when changes depend on real Redis, durable state or recovery.
- Use the current `TL-STREAM-001` release journey and relevant live browser spec when a deployed proxy, independent APIs or an end-user reconnect is part of the claim. Use only an authorized isolated target for disruptions and synthetic events.
- Record protocol and source SHA, scope, worker and API process counts, acknowledgement and resume behavior, observed updates, durable totals and missing scenarios. Use the same fixture's expected statuses to reconcile the stream with a fresh REST read.
