/**
 * Regression: /agents must render DEGRADED pipelines visibly.
 *
 * History (BUG-004, S2 — live homelab run 493d5c1f, 2026-06-06): a pipeline
 * that finished with an errored stage was stored as status='partial', and the
 * page's status maps and StatusIcon had no case for it, so the row rendered
 * with blank styling and no icon and looked "missing".
 *
 * E7.1 (2026-09-11) retired `partial`: such a run is now `completed` with
 * `execution_metadata.stage_quality === 'degraded'`. The failure mode is the
 * same, so the regression test moves with the vocabulary: the page must derive
 * degradation through `isDegradedPipeline()` and render the broken tone, and
 * it must not fall back to the retired literal.
 *
 * Strategy: pull the page source via Vite's `?raw` import (no Node built-ins
 * so the production `tsc` build doesn't trip on this test).
 */
import { describe, expect, it } from 'vitest'

import pageSource from './AgentStatusPage.tsx?raw'
import { isDegradedPipeline } from '@/types/agent'

describe('AgentStatusPage — degraded pipeline rendering (BUG-004 regression, E7.1 vocabulary)', () => {
  it('never keys rendering on the retired `partial` literal', () => {
    expect(pageSource).not.toMatch(/['"]partial['"]/)
  })

  it('derives degradation through isDegradedPipeline for the pipeline card icon', () => {
    expect(pageSource).toMatch(/StatusIcon\s+status=\{publicPipelineStatus\(pipeline\)\}\s+degraded=\{isDegradedPipeline\(pipeline\)\}/)
  })

  it('keeps the degraded marker as its own tag with the broken tone (E7.5)', () => {
    expect(pageSource).toMatch(/data-testid="pipeline-quality-tag"[\s\S]{0,120}STATUS_COLOUR\.degraded[\s\S]{0,40}DEGRADED/)
  })

  it('the card chip renders only the four-value public label, never the raw status (E7.5)', () => {
    expect(pageSource).toMatch(/\{PUBLIC_PIPELINE_STATUS_LABEL\[publicPipelineStatus\(pipeline\)\]\}/)
    expect(pageSource).not.toMatch(/pipeline\.status\.toUpperCase\(\)/)
  })

  it('the degraded icon uses the broken warning tone, ahead of the completed check', () => {
    const degraded = pageSource.search(/if \(degraded\) return <AlertTriangle[^>]*text-\[var\(--status-broken\)\]/)
    const completed = pageSource.search(/status === 'completed' \|\| status === 'passed'/)
    expect(degraded).toBeGreaterThan(-1)
    expect(completed).toBeGreaterThan(degraded)
  })

  it('STATUS_COLOUR / STATUS_BG cover every internal status and the public in_progress', () => {
    for (const status of ['pending', 'running', 'retry_wait', 'in_progress', 'completed', 'passed', 'failed']) {
      expect(pageSource, `STATUS_COLOUR.${status}`).toMatch(new RegExp(`${status}:\\s*'text-\\[var\\(--`))
      expect(pageSource, `STATUS_BG.${status}`).toMatch(new RegExp(`${status}:\\s*'bg-\\[var\\(--`))
    }
  })

  it('a completed run with degraded stages is degraded; a clean one is not', () => {
    expect(isDegradedPipeline({ status: 'completed', execution_metadata: { stage_quality: 'degraded' } })).toBe(true)
    expect(isDegradedPipeline({ status: 'completed', execution_metadata: {} })).toBe(false)
    expect(isDegradedPipeline({ status: 'failed', execution_metadata: { stage_quality: 'degraded' } })).toBe(false)
    expect(isDegradedPipeline(null)).toBe(false)
  })
})
