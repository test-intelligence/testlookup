/**
 * The labelling queue (W16 AGT-13): the two output columns as TEXT, the run link built from the run id, the four
 * verdict buttons and their bodies, the category select on root-cause pairs only, the honest empty states.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLabelShadowPair, useShadowPairs } from '@/hooks/useAiEval'
import { PROJECT_FIXTURE_ID, SHADOW_PAIRS_FIXTURE } from '@/test/aiEvalMocks'
import type { ShadowPair, ShadowPairList } from '@/types/aiEval'
import ShadowPairLabelling from './ShadowPairLabelling'

vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())

const pairsHook = vi.mocked(useShadowPairs)
const labelHook = vi.mocked(useLabelShadowPair)

function answer(data: Partial<ShadowPairList> | undefined, extra: { error?: unknown; mutate?: () => void } = {}) {
  pairsHook.mockReturnValue({
    data, error: extra.error, isLoading: false, mutate: extra.mutate ?? vi.fn(),
  } as unknown as ReturnType<typeof useShadowPairs>)
}

function renderQueue() {
  return render(
    <MemoryRouter>
      <ShadowPairLabelling projectId={PROJECT_FIXTURE_ID} />
    </MemoryRouter>,
  )
}

const card = (id: string) => document.querySelector(`[data-shadow-pair="${id}"]`) as HTMLElement

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-25T09:00:00Z'))
  answer(SHADOW_PAIRS_FIXTURE)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('the queue', () => {
  it('reads the SAME key as the tab badge, and shows each pair side by side with its run link', () => {
    renderQueue()
    expect(pairsHook).toHaveBeenCalledWith(PROJECT_FIXTURE_ID, undefined, 'pending', 0)
    expect(screen.getByRole('heading', { name: 'Label live pairs (2)' })).toBeTruthy()

    const first = within(card('spA1b2C3d4'))
    expect(first.getByText('Incumbent · llm')).toBeTruthy()
    expect(first.getByText('Candidate · slm')).toBeTruthy()
    const link = first.getByRole('link', { name: 'Run #b-118' }) as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/runs/trA1b2C3d4?tab=tests')
    expect(first.getByText('test_checkout_total')).toBeTruthy()
    expect(first.getByText('Expires in 30 days')).toBeTruthy()
    const outputs = card('spA1b2C3d4').querySelectorAll('pre[data-pair-output]')
    expect(outputs[0].textContent).toBe(JSON.stringify(SHADOW_PAIRS_FIXTURE.items[0].incumbent_output, null, 2))

    expect(within(card('spB2c3D4e5')).getByText('Source no longer available.')).toBeTruthy()
    expect(within(card('spB2c3D4e5')).queryByRole('link')).toBeNull()
  })

  it('renders a markup string in an output as text, never as an element', () => {
    const hostile: ShadowPair = {
      ...SHADOW_PAIRS_FIXTURE.items[0],
      incumbent_output: { root_cause_summary: '<img src=x onerror="window.__pwned=1">' },
      candidate_output: { root_cause_summary: '<script>window.__pwned=2</script>' },
    }
    answer({ ...SHADOW_PAIRS_FIXTURE, items: [hostile], total: 1, pending_total: 1 })
    const { container } = renderQueue()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect(card(hostile.id).textContent).toContain('<img src=x onerror=\\"window.__pwned=1\\">')
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined()
  })

  it('offers the failure category on a root-cause pair only', () => {
    const summary: ShadowPair = {
      ...SHADOW_PAIRS_FIXTURE.items[1],
      id: 'spC3d4E5f6',
      agent_id: 'agent.summary.v1',
      agent_label: 'Run summary',
      sample_key: 'summary:trA1b2C3d4',
    }
    answer({ ...SHADOW_PAIRS_FIXTURE, items: [SHADOW_PAIRS_FIXTURE.items[0], summary] })
    renderQueue()
    expect(within(card('spA1b2C3d4')).getByRole('combobox', { name: 'Failure category' })).toBeTruthy()
    expect(within(card('spC3d4E5f6')).queryByRole('combobox', { name: 'Failure category' })).toBeNull()
  })
})

describe('labelling', () => {
  it.each([
    ['Incumbent correct', 'incumbent'],
    ['Candidate correct', 'candidate'],
    ['Both correct', 'both'],
    ['Neither', 'neither'],
  ])('%s posts verdict %s; the card leaves and the count drops', async (button, verdict) => {
    const label = vi.fn(async () => ({ ...SHADOW_PAIRS_FIXTURE.items[0], label_status: 'human_labelled' }))
    labelHook.mockReturnValue(label)
    renderQueue()
    fireEvent.click(within(card('spA1b2C3d4')).getByRole('button', { name: button }))
    await waitFor(() => expect(label).toHaveBeenCalledWith('spA1b2C3d4', { verdict }))
    await waitFor(() => expect(card('spA1b2C3d4')).toBeNull())
    expect(screen.getByRole('heading', { name: 'Label live pairs (1)' })).toBeTruthy()
    expect(labelHook).toHaveBeenCalledWith(PROJECT_FIXTURE_ID)
  })

  it('sends the chosen category and trimmed notes with the verdict', async () => {
    const label = vi.fn(async () => SHADOW_PAIRS_FIXTURE.items[0])
    labelHook.mockReturnValue(label)
    renderQueue()
    const first = within(card('spA1b2C3d4'))
    fireEvent.change(first.getByRole('combobox', { name: 'Failure category' }), { target: { value: 'TEST_DATA' } })
    fireEvent.change(first.getByRole('textbox', { name: 'Notes' }), { target: { value: '  stale fixture prices ' } })
    fireEvent.click(first.getByRole('button', { name: 'Candidate correct' }))
    await waitFor(() => expect(label).toHaveBeenCalledWith('spA1b2C3d4', {
      verdict: 'candidate', failure_category: 'TEST_DATA', notes: 'stale fixture prices',
    }))
  })

  it("shows the server's sentence in the card when the label is refused, and keeps the card", async () => {
    const refused = Object.assign(new Error('Request failed with status code 409'), {
      response: { status: 409, data: { detail: { code: 'shadow_pair_already_labelled', message: 'This pair is already labelled (golden_match).' } } },
    })
    labelHook.mockReturnValue(vi.fn(async () => { throw refused }))
    renderQueue()
    fireEvent.click(within(card('spA1b2C3d4')).getByRole('button', { name: 'Both correct' }))
    expect(await within(card('spA1b2C3d4')).findByRole('alert')).toHaveProperty(
      'textContent', 'This pair is already labelled (golden_match).',
    )
    expect(screen.getByRole('heading', { name: 'Label live pairs (2)' })).toBeTruthy()
  })

  it('an agent chip narrows the queue to that agent', () => {
    renderQueue()
    fireEvent.click(screen.getByRole('button', { name: 'Run summary' }))
    expect(pairsHook).toHaveBeenLastCalledWith(PROJECT_FIXTURE_ID, 'agent.summary.v1', 'pending', 0)
  })
})

describe('empty and error states', () => {
  it('a project that never produced a pair says there is no live model tier', () => {
    answer({ items: [], total: 0, pending_total: 0, live_tier_pairs_seen: false })
    renderQueue()
    expect(screen.getByText(
      'No shadow pairs: no live model tier. Pairs are recorded only while an agent runs in shadow mode and a local SLM and LLM both answer.',
    )).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Run summary' })).toBeNull()
  })

  it('pairs seen but none pending → every live pair is labelled', () => {
    answer({ items: [], total: 0, pending_total: 0, live_tier_pairs_seen: true })
    renderQueue()
    expect(screen.getByText('Every live pair is labelled.')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Label live pairs (0)' })).toBeTruthy()
  })

  it('a failed read says so and retries; a bare answer without items never crashes', () => {
    const mutate = vi.fn()
    answer(undefined, { error: new Error('boom'), mutate })
    const first = renderQueue()
    expect(screen.getByText('Could not load the shadow pairs.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalled()
    first.unmount()

    answer({ total: 0 } as Partial<ShadowPairList>)
    renderQueue()
    expect(screen.getByText('Every live pair is labelled.')).toBeTruthy()
  })
})
