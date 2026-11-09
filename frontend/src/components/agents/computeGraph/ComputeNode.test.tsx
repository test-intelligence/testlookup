/**
 * W40 (W29d F-4): the stage card holds its content in every state. A running /
 * failed card adds a suffix row below the metric grid, so its description
 * clamps to ONE line with the full text as that line's title; the other states
 * keep the two-line description slot. The running row carries the duration,
 * the progress bar and the ETA on one line.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import ComputeNode from './ComputeNode'
import type { GenericComputeStage, StageStatus } from './types'

const DESC = 'Gathers the evidence for the infrastructure hypothesis across every failed run of the build'

function stage(status: StageStatus, over: Partial<GenericComputeStage> = {}): GenericComputeStage {
  return {
    id: `hypothesis_${status}`,
    name: `Hypothesis ${status}`,
    desc: DESC,
    status,
    dur: '4s',
    metrics: { evidence: 3, tokens: '1.4k', cost: '$0.0050' },
    glyph: 'bot',
    ...over,
  }
}

function renderCard(s: GenericComputeStage) {
  const { container } = render(<ComputeNode stage={s} selected={false} onSelect={() => {}} position={{ x: 24, y: 24 }} />)
  const card = container.querySelector(`[data-stage-id="${s.id}"]`) as HTMLElement
  const desc = screen.getByText((_, el) => el?.tagName === 'DIV' && (el.textContent ?? '').startsWith(DESC))
  return { card, desc }
}

describe('ComputeNode — the card holds its content in every state (W40 F-4)', () => {
  it.each(['running', 'failed'] as const)('a %s card clamps its description to one line, the full text (and note) as its title', (status) => {
    const { desc } = renderCard(stage(status, { note: 'partial' }))
    expect(desc).toHaveClass('line-clamp-1')
    expect(desc).not.toHaveClass('line-clamp-2')
    expect(desc).toHaveAttribute('title', `${DESC} · partial`)
    expect(desc.style.minHeight).toBe('')
  })

  it.each(['done', 'pending', 'skipped'] as const)('a %s card keeps the two-line description slot and no title of its own', (status) => {
    const { desc } = renderCard(stage(status))
    expect(desc).toHaveClass('line-clamp-2')
    expect(desc).not.toHaveClass('line-clamp-1')
    expect(desc).not.toHaveAttribute('title')
    expect(desc).toHaveStyle({ minHeight: '28px' })
  })

  it('a skipped card still shows its skip reason as the card title (no inner title hides it)', () => {
    const { card, desc } = renderCard(stage('skipped', { skipReason: 'Not run on the LLM branch' }))
    expect(card).toHaveAttribute('title', 'Not run on the LLM branch')
    expect(desc).not.toHaveAttribute('title')
  })

  it('a running card draws the duration, the progress bar and the ETA in ONE row', () => {
    const { card } = renderCard(stage('running', { dur: '12s', startMs: 0, etaMs: 40 }))
    const eta = screen.getByText('ETA 40s total')
    const row = eta.parentElement as HTMLElement
    expect(row).toHaveClass('flex', 'items-center', 'whitespace-nowrap')
    expect(row.firstElementChild).toHaveTextContent('12s')
    // The bar (its accent track) sits inside the same row, between the duration and the ETA.
    const bar = row.querySelector('[aria-hidden]') as HTMLElement
    expect(bar).not.toBeNull()
    expect(bar.parentElement).toHaveClass('flex-1')
    expect(Array.from(row.children).indexOf(bar.parentElement as Element)).toBe(1)
    expect(card.querySelectorAll('div.h-1[aria-hidden]')).toHaveLength(1) // one bar, no second row for it
  })

  it('a failed card keeps its retry row', () => {
    const { card } = renderCard(stage('failed'))
    expect(card).toHaveTextContent('retry 1/3 queued')
  })
})
