/**
 * Regression: /agents page must render `partial` pipelines/stages.
 *
 * Bug pinned (BUG-004, S2 — live homelab run 493d5c1f, 2026-06-06):
 * the AI pipeline ran and PERSISTED (`agent_pipeline_runs` row
 * `5c378cde`, status='partial', workflow_type='offline') and JOINs
 * to its `test_runs` row, so `GET /api/v1/agents/pipelines` returns
 * it. But the page showed NO pipeline. Root cause was FRONTEND: the
 * status maps `STATUS_COLOUR` / `STATUS_BG` and the `StatusIcon`
 * component had cases for running/completed/failed/skipped but NOT
 * `partial`. A `partial` pipeline (what a pipeline gets when a stage
 * errors, e.g. errors>=1 — very common) rendered with blank styling
 * and no icon, so it looked "missing".
 *
 * Fix: treat `partial` as a visible broken/degraded state everywhere
 * status is rendered for pipelines AND stages. The degraded tone is the
 * per-theme `--status-broken` token (was raw `amber-400` before the
 * palette-token ratchet) so it stays legible on the light themes too.
 *
 * Strategy: pull the page source via Vite's `?raw` import (no Node
 * built-ins so the production `tsc` build doesn't trip on this test)
 * and assert `partial` is handled in the status maps and StatusIcon.
 * Catches an accidental revert.
 */
import { describe, expect, it } from 'vitest'

// Vite `?raw` query imports the file as a string at build time.
import pageSource from './AgentStatusPage.tsx?raw'

describe('AgentStatusPage — partial status rendering (BUG-004 regression)', () => {
  it('STATUS_COLOUR maps `partial` to the broken (degraded) token', () => {
    // e.g.  partial: 'text-[var(--status-broken)]',
    expect(pageSource).toMatch(/partial:\s*'text-\[var\(--status-broken\)\]'/)
  })

  it('STATUS_BG maps `partial` to the broken background token', () => {
    // e.g.  partial: 'bg-[var(--status-broken-bg)] border border-[var(--status-broken-bd)]',
    expect(pageSource).toMatch(/partial:\s*'bg-\[var\(--status-broken-bg\)\][^']*'/)
  })

  it('StatusIcon has an explicit `partial` branch (not the neutral fallback)', () => {
    // e.g.  if (status === 'partial') return <AlertTriangle ... />
    expect(pageSource).toMatch(/status\s*===\s*'partial'/)
  })

  it('the `partial` icon uses the broken (degraded) warning tone', () => {
    // Lock in the degraded colour on the partial icon so it stays visibly
    // degraded rather than reverting to a muted/neutral colour.
    expect(pageSource).toMatch(
      /status\s*===\s*'partial'\)\s*return\s*<AlertTriangle[^>]*text-\[var\(--status-broken\)\]/,
    )
  })
})
