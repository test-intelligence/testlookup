/**
 * W25e HYG-06 E-3: how the live page folds one WebSocket frame into its state.
 *
 * Pure (no React, no I/O). The server paces every socket to at most one data
 * frame per `LIVE_FRAME_INTERVAL_MS`; a client that asked for protocol 2 gets
 * a `live_batch` holding every event folded inside that interval, one ENTRY
 * per run and event type, in last-update order (`backend/app/streams/live_frames.py`).
 * An older server (or a lone event) sends the legacy shapes, which are simply
 * a one-entry frame here — so both reach the same two outputs:
 *
 * - `feed`: what the event feed shows, oldest first. A test-result entry
 *   becomes one "Test result" event per result the server listed, then one
 *   `LIVE_RESULTS_OMITTED` event when it folded more than it listed — every
 *   result is either a row or counted in a row, never silently gone.
 * - `updates`: one event per entry (its latest absolute state), for
 *   `applyLiveUpdates`, which merges a whole frame into the sessions table in
 *   one pass.
 *
 * Test names come from customer data: the page renders them as React text.
 */
import type { LiveSessionState } from '@/types/live-stream'
import { LIVE_BATCH_FRAME, LIVE_RECONCILE_FRAME, type LiveBatchEntry } from '@/types/liveFrames'
import type { LiveEvent } from './useLiveExecution'

/** A feed-only synthetic event: the server folded more results than it listed. */
export const LIVE_RESULTS_OMITTED = 'live_results_omitted'

/** "1 more result not listed" / "N more results not listed". */
export function omittedResultsSentence(n: number): string {
  return n === 1 ? '1 more result not listed' : `${n} more results not listed`
}

/** A `live_run_complete` for a known run id: the hook refetches once per frame that carries one. */
export function isRunCompletion(event: Pick<LiveEvent, 'type' | 'run_id'>): boolean {
  return event.type === 'live_run_complete' && !!event.run_id
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const text = (value: unknown): string | undefined => (typeof value === 'string' ? value : undefined)

/** The entries one frame carries: a `live_batch`'s events, or the frame itself. */
function frameEntries(frame: Record<string, unknown>): LiveBatchEntry[] {
  if (frame.type !== LIVE_BATCH_FRAME) return [frame as LiveBatchEntry]
  const events: unknown[] = Array.isArray(frame.events) ? frame.events : []
  return events.filter((e): e is LiveBatchEntry => isRecord(e) && typeof e.type === 'string')
}

/** An entry's latest state as a feed / session event (its result list stays out of the 200-row feed). */
function entryState(entry: LiveBatchEntry, nowMs: number): LiveEvent {
  const state: Record<string, unknown> = { ...entry }
  delete state.results
  delete state.results_dropped
  return { ...(state as unknown as LiveEvent), timestamp: nowMs }
}

function omittedEvent(state: LiveEvent, omitted: number): LiveEvent {
  return {
    type: LIVE_RESULTS_OMITTED,
    run_id: state.run_id,
    project_id: state.project_id,
    suite_name: state.suite_name,
    omitted,
    message: omittedResultsSentence(omitted),
    timestamp: state.timestamp,
  }
}

export function expandLiveFrame(
  frame: Record<string, unknown>,
  nowMs: number,
): { feed: LiveEvent[]; updates: LiveEvent[] } {
  const feed: LiveEvent[] = []
  const updates: LiveEvent[] = []
  for (const entry of frameEntries(frame)) {
    const state = entryState(entry, nowMs)
    updates.push(state)
    // A re-read signal is acted on by the hook, never shown (as when it arrives alone).
    if (entry.type === LIVE_RECONCILE_FRAME) continue
    const isResult = entry.type === 'live_test_result'
    const listed = isResult && Array.isArray(entry.results) ? entry.results.filter(isRecord) : []
    const dropped = isResult && typeof entry.results_dropped === 'number' ? Math.max(0, Math.floor(entry.results_dropped)) : 0
    if (listed.length === 0 && dropped === 0) {
      feed.push(state)
      continue
    }
    for (const item of listed) {
      feed.push({
        ...state,
        last_test: text(item.test_name),
        last_status: text(item.status),
        sequence_id: text(item.sequence_id) ?? state.sequence_id,
      })
    }
    if (dropped > 0) feed.push(omittedEvent(state, dropped))
  }
  return { feed, updates }
}

/** One update applied to the sessions table: the hook's three branches before W25e, unchanged. */
function applyLiveUpdate(prev: LiveSessionState[], event: LiveEvent): LiveSessionState[] {
  if (event.type === 'live_run_started') {
    const exists = prev.some(s => s.run_id === event.run_id)
    if (exists) return prev
    return [
      {
        run_id: event.run_id ?? '',
        project_id: event.project_id ?? '',
        build_number: event.build_number ?? '',
        status: 'running',
        total: 0, passed: 0, failed: 0, skipped: 0, broken: 0,
        pass_rate: 0,
      },
      ...prev,
    ]
  }
  if (event.type === 'live_test_result' && event.run_id) {
    return prev.map(s =>
      s.run_id === event.run_id
        ? {
            ...s,
            passed:     event.passed  ?? s.passed,
            failed:     event.failed  ?? s.failed,
            skipped:    event.skipped ?? s.skipped,
            broken:     event.broken  ?? s.broken,
            total:      event.total   ?? s.total,
            pass_rate:  event.pass_rate ?? s.pass_rate,
            current_test: event.last_test ?? s.current_test,
            status: 'running',
          }
        : s,
    )
  }
  if (isRunCompletion(event)) {
    return prev.map(s =>
      s.run_id === event.run_id
        ? {
            ...s,
            status: 'completed',
            // Stamp the completion time so the SWR-merge race-window bridge
            // (mergeBridgedSessions) can time-box this row. Without it the
            // bridge can't tell a just-completed run from a stale one.
            completed_at: s.completed_at ?? new Date().toISOString(),
            pass_rate: event.pass_rate ?? s.pass_rate,
          }
        : s,
    )
  }
  return prev
}

/** A frame's updates applied in order, in one pass; `completed` = a run completion was applied. */
export function applyLiveUpdates(
  sessions: LiveSessionState[],
  updates: LiveEvent[],
): { sessions: LiveSessionState[]; completed: boolean } {
  let next = sessions
  let completed = false
  for (const update of updates) {
    next = applyLiveUpdate(next, update)
    if (isRunCompletion(update)) completed = true
  }
  return { sessions: next, completed }
}
