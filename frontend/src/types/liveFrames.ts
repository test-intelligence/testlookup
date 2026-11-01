/**
 * W25e HYG-06 (E-2 / E-3): the live WebSocket frame vocabulary.
 *
 * The constants mirror `backend/app/streams/live_frames.py` value for value
 * (pinned by `backend/tests/test_live_frames_vocab_frontend_parity.py`, which
 * reads this file as TEXT — keep every constant a plain literal on one line).
 *
 * A client that sends `"protocol": 2` in its auth frame receives `live_batch`
 * frames: every data event the server folded inside one interval, in
 * last-update order. A client that asks for nothing keeps the legacy frame
 * types. Both are paced to at most one data frame per `LIVE_FRAME_INTERVAL_MS`.
 * Test names come from customer data: render them as React text, never as HTML.
 */

export const LIVE_PROTOCOL_BATCH = 2
export const LIVE_BATCH_FRAME = 'live_batch'
export const LIVE_RECONCILE_FRAME = 'reconcile_required'
export const LIVE_FRAME_INTERVAL_MS = 200
export const LIVE_RESULTS_PER_FRAME_MAX = 200
export const LIVE_COALESCED_EVENT_TYPES = ['live_test_result', 'live_warning'] as const

export interface LiveResultItem {
  test_name: string
  status: string
  sequence_id?: string
}

export interface LiveBatchEntry {
  type: string
  run_id?: string
  sequence_id?: string
  coalesced?: number
  results?: LiveResultItem[]
  results_dropped?: number
  [field: string]: unknown
}

export interface LiveBatchFrame {
  type: 'live_batch'
  protocol: 2
  sequence_id: string
  events: LiveBatchEntry[]
  counts: { events: number; entries: number; results_listed: number; results_dropped: number }
}
