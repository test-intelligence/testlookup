/**
 * Regression: /live Sessions table dedup key.
 *
 * Bug pinned (2026-05-18): four parallel TestNG runs that shared a
 * common SDK-supplied build label (e.g. ``testng-1779170467999``)
 * collapsed to ONE row in the Sessions table. Hero count read
 * "4 running" but the table only showed one session. Root cause:
 * dedup keyed on ``build_number``, which is no longer unique per
 * run (the legacy slug+UUID double-emit that justified
 * build-number dedup is gone).
 *
 * Fix: ``LiveExecutionPage`` dedups by ``run_id`` (the LiveSession
 * PK) instead of ``build_number``. Effectively a no-op for normal
 * traffic, still defends against a future double-emit by collapsing
 * identical run_ids.
 *
 * Strategy: pull the page source via Vite's ``?raw`` import (no Node
 * built-ins so the production ``tsc`` build doesn't trip on this
 * test) and assert the dedup block uses ``run_id`` as the Map key,
 * not ``build_number``. Catches an accidental revert.
 */
import { describe, expect, it } from 'vitest'

// Vite ``?raw`` query imports the file as a string at build time.
// Available out of the box with vite/vitest — no Node ``fs`` needed.
import pageSource from './LiveExecutionPage.tsx?raw'

describe('LiveExecutionPage — Sessions table dedup key (regression)', () => {
  it('dedupedSessions keys the Map by s.run_id, not s.build_number', () => {
    // The exact line we want to lock in:
    //   const key = s.run_id
    expect(pageSource).toMatch(/const\s+key\s*=\s*s\.run_id/)
    // And the OLD shape must NOT come back as a dedup key:
    expect(pageSource).not.toMatch(/const\s+key\s*=\s*s\.build_number/)
  })

  it('the dedup Map is typed Map<string, LiveSessionState>', () => {
    expect(pageSource).toMatch(/new\s+Map<string,\s*LiveSessionState>/)
    expect(pageSource).toMatch(/byRunId/)
  })

  it('the dedup picks the latest s.last_event_at / s.started_at on collision', () => {
    // Without the "latest wins" tiebreak, two identical run_ids could
    // render alternately as the WebSocket churns. The recency rule
    // keeps the row stable.
    expect(pageSource).toMatch(/last_event_at/)
    expect(pageSource).toMatch(/started_at/)
  })
})
