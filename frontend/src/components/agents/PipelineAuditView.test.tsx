/**
 * W14 AGT-05: the Audit tab (contract §2.1 test 5) — the replay document as
 * text, gaps as data in the warning tint (never an alert), a hostile event
 * detail shown as text.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PipelineAuditView from './PipelineAuditView'
import { usePipelineReplay } from '@/hooks/useAgentOps'
import { idleSwr, REPLAY_FIXTURE, REPLAY_REPLAYABLE_FIXTURE } from '@/test/agentOpsMocks'

vi.mock('@/hooks/useAgentOps', async () => (await import('@/test/agentOpsMocks')).agentOpsHookMocks())

const replayHook = vi.mocked(usePipelineReplay)

beforeEach(() => {
  replayHook.mockReset()
  replayHook.mockReturnValue(idleSwr() as never)
})

describe('PipelineAuditView', () => {
  it('reads the replay of THIS pipeline, polling only while it is live', () => {
    render(<PipelineAuditView pipelineId="pipe-1" live={false} />)
    expect(replayHook).toHaveBeenCalledWith('pipe-1', false)
  })

  it('a replayable document: Replayable ✓, the mode line, and its seven stage rows', () => {
    replayHook.mockReturnValue(idleSwr({ data: REPLAY_REPLAYABLE_FIXTURE }) as never)
    const { container } = render(<PipelineAuditView pipelineId="pipe-1" live={false} />)
    expect(container.querySelector('[data-audit-header]')).toHaveTextContent('Replayable ✓')
    expect(container.querySelector('[data-audit-header]')).toHaveTextContent('analysis mode: auto → llm')
    // The final checksum: 12 characters on screen, the whole value in its title.
    const final = screen.getByTitle('f'.repeat(64))
    expect(final).toHaveTextContent(/^f{12}$/)
    const table = container.querySelector('[data-audit-stages]') as HTMLElement
    expect(within(table).getAllByRole('row')).toHaveLength(1 + 7)
    expect(container.querySelector('[data-audit-gaps]')).toBeNull()
  })

  it('an old pipeline with gaps: the gaps panel lists each non-empty kind, as data, never an alert', () => {
    replayHook.mockReturnValue(idleSwr({ data: REPLAY_FIXTURE }) as never)
    const { container } = render(<PipelineAuditView pipelineId="pipe-1" live={false} />)
    expect(container.querySelector('[data-audit-header]')).toHaveTextContent('Not fully replayable · 2 gaps')
    const panel = screen.getByRole('region', { name: 'Audit gaps' })
    expect(within(panel).getAllByRole('listitem').map(li => li.textContent)).toEqual([
      'Missing checkpoints: summary',
      'No final-state checksum recorded.',
    ])
    expect(panel.className).toContain('--ui-warning-bg')
    expect(screen.queryByRole('alert')).toBeNull()
    // The whole document still renders under the gaps.
    expect(within(container.querySelector('[data-audit-stages]') as HTMLElement).getAllByRole('row')).toHaveLength(8)
    expect(screen.getByText('failed_count: 12')).toBeInTheDocument()
    expect(screen.getByText('mem-1')).toBeInTheDocument()
    expect(screen.getByText('stage_started × 7')).toBeInTheDocument()
  })

  it('renders an event detail holding markup as text', () => {
    const hostile = '<img src=x onerror=alert(1)>'
    replayHook.mockReturnValue(idleSwr({
      data: {
        ...REPLAY_FIXTURE,
        events: [{ event_type: 'stage_failed', stage_name: hostile, timestamp: '2026-10-20T10:00:00Z', detail: { error: hostile } }],
      },
    }) as never)
    const { container } = render(<PipelineAuditView pipelineId="pipe-1" live={false} />)
    expect(container.querySelector('img')).toBeNull()
    const event = container.querySelector('[data-audit-event]') as HTMLElement
    expect(event.textContent).toContain(hostile)
    expect(event.querySelector('pre')?.textContent).toContain('"error": "<img src=x onerror=alert(1)>"')
  })

  it('a failed read says so and offers Retry; the panel stays', () => {
    const mutate = vi.fn()
    replayHook.mockReturnValue(idleSwr({ error: new Error('boom'), mutate }) as never)
    render(<PipelineAuditView pipelineId="pipe-1" live={false} />)
    expect(screen.getByText('Could not load the audit document.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalledTimes(1)
  })
})
