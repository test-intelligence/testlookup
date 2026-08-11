import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import RightRail from './RightRail'
import type { ComputeStage, ComputeEdgeEvent } from './types'

/**
 * Minimal ``ComputeStage`` fixture — only the fields ``StageBody`` reads carry
 * meaningful values; the rest are inert placeholders.
 */
function makeStage(overrides: Partial<ComputeStage> = {}): ComputeStage {
  return {
    id: 'rca',
    name: 'Root-cause analysis',
    desc: 'Explains the failure.',
    status: 'done',
    dur: '12s',
    metrics: {},
    glyph: 'bot',
    ...overrides,
  }
}

function makeEvent(kind: ComputeEdgeEvent['kind'], at: string): ComputeEdgeEvent {
  return { kind, stage: 'rca', at, what: `${kind} event` }
}

/**
 * The Activity-tab event icons must use the per-theme status tokens rather than
 * raw Tailwind palette classes — those bypass the light-theme legibility system.
 * completed → --status-passed, failed → --status-failed, retry → --status-broken.
 */
describe('RightRail activity icons', () => {
  function renderActivity() {
    const stage = makeStage()
    const events: ComputeEdgeEvent[] = [
      makeEvent('completed', '00:00:01'),
      makeEvent('failed', '00:00:02'),
      makeEvent('started', '00:00:03'),
      makeEvent('retry', '00:00:04'),
    ]
    const { container } = render(
      <RightRail selectedId="rca" stages={[stage]} decision={null} events={events} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /activity/i }))
    return container
  }

  it('renders event icons with per-theme status/accent tokens', () => {
    const container = renderActivity()
    expect(container.querySelector('[class*="var(--status-passed)"]')).not.toBeNull()
    expect(container.querySelector('[class*="var(--status-failed)"]')).not.toBeNull()
    expect(container.querySelector('[class*="var(--status-broken)"]')).not.toBeNull()
    expect(container.querySelector('[class*="var(--color-accent)"]')).not.toBeNull()
  })

  it('leaves no raw Tailwind palette classes on the event icons', () => {
    const container = renderActivity()
    // These raw palette strings are assertion guards (proving the classes are
    // absent), not UI — scoped-exempt from the design-audit token rule.
    // eslint-disable-next-line no-restricted-syntax
    for (const raw of ['text-emerald-400', 'text-red-400', 'text-amber-400']) {
      expect(container.querySelector(`[class~="${raw}"]`)).toBeNull()
    }
  })
})
