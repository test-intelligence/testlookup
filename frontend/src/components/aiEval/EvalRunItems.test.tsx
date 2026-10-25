/**
 * A replay run's items: status chips with the run's counts, 25 a page, and the inline expected-vs-candidate diff.
 * Model output is DATA: a `<script>` or `<img onerror>` string in an output is shown as those characters, never
 * parsed as markup.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useEvalRunItems } from '@/hooks/useAiEval'
import { ITEMS_FIXTURE, REPLAY_RUN_ID, idleAiEvalSwr } from '@/test/aiEvalMocks'
import type { EvalItemList } from '@/types/aiEval'
import EvalRunItems from './EvalRunItems'

vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())

const items = vi.mocked(useEvalRunItems)
const answer = (data: unknown, extra: Record<string, unknown> = {}) =>
  items.mockReturnValue(idleAiEvalSwr({ data, ...extra }) as unknown as ReturnType<typeof useEvalRunItems>)

beforeEach(() => {
  vi.clearAllMocks()
  answer(ITEMS_FIXTURE)
})

describe('the items table', () => {
  it('shows the status chips with the run’s counts and one row per item', () => {
    render(<EvalRunItems runId={REPLAY_RUN_ID} taskType="classification" />)
    expect(screen.getByRole('button', { name: 'All (4)' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Incorrect (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unscored (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Error (1)' })).toBeInTheDocument()
    const rows = document.querySelectorAll('[data-item-row]')
    expect(rows).toHaveLength(4)
    const wrong = rows[1] as HTMLElement
    expect(within(wrong).getByText('Incorrect')).toBeInTheDocument()
    expect(within(wrong).getByText('INFRASTRUCTURE')).toBeInTheDocument()
    expect(within(wrong).getByText('FLAKY')).toBeInTheDocument()
    expect(within(wrong).getByText('Rules')).toBeInTheDocument()
    expect(items).toHaveBeenLastCalledWith(REPLAY_RUN_ID, { limit: 25, offset: 0 }, true)
  })

  it('filters by status from the first page', () => {
    render(<EvalRunItems runId={REPLAY_RUN_ID} taskType="classification" />)
    fireEvent.click(screen.getByRole('button', { name: 'Incorrect (1)' }))
    expect(items).toHaveBeenLastCalledWith(REPLAY_RUN_ID, { status: 'incorrect', limit: 25, offset: 0 }, true)
  })

  it('pages 25 at a time', () => {
    answer({ ...ITEMS_FIXTURE, total: 60 })
    render(<EvalRunItems runId={REPLAY_RUN_ID} taskType="classification" />)
    expect(screen.getByText('1–25 of 60')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(items).toHaveBeenLastCalledWith(REPLAY_RUN_ID, { limit: 25, offset: 25 }, true)
  })
})

describe('the inline diff', () => {
  it('opens expected | candidate output and the input under the row', () => {
    render(<EvalRunItems runId={REPLAY_RUN_ID} taskType="classification" />)
    fireEvent.click(document.querySelector('[data-item-row="1"]') as HTMLElement)
    const diff = document.querySelector('[data-item-diff="1"]') as HTMLElement
    const [expected, output, input] = Array.from(diff.querySelectorAll('pre'))
    expect(expected.textContent).toBe(JSON.stringify({ failure_category: 'INFRASTRUCTURE' }, null, 2))
    expect(output.textContent).toContain('"failure_category": "FLAKY"')
    expect(input.textContent).toContain('test_login_redirect')
    expect(diff.closest('tr')).toHaveAttribute('data-in-page-row')
  })

  it('shows an errored item’s sentence and — for its missing output', () => {
    render(<EvalRunItems runId={REPLAY_RUN_ID} taskType="classification" />)
    fireEvent.click(document.querySelector('[data-item-row="3"]') as HTMLElement)
    const diff = document.querySelector('[data-item-diff="3"]') as HTMLElement
    expect(diff).toHaveTextContent('The engine raised an error (ValueError).')
    expect(diff.querySelectorAll('pre')[1].textContent).toBe('—')
  })

  it('renders markup in an output as text', () => {
    const hostile: EvalItemList = {
      items: [{
        item_index: 0, item_key: '<img src=x onerror=alert(1)>', status: 'incorrect',
        input: { test_name: 'test_x' },
        expected: { failure_category: 'PRODUCT_BUG' },
        output: { failure_category: '<script>alert(1)</script>', root_cause_summary: '<img src=x onerror=alert(2)>' },
        mode_used: 'rules', latency_ms: 4,
      }],
      total: 1,
      counts: { incorrect: 1 },
    }
    answer(hostile)
    const { container } = render(<EvalRunItems runId={REPLAY_RUN_ID} taskType="classification" />)
    // The Item cell cuts a long key in the middle; the whole key stays in its title, as text.
    expect(screen.getByTitle('<img src=x onerror=alert(1)>')).toHaveTextContent('<img src=…lert(1)>')
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument()
    fireEvent.click(container.querySelector('[data-item-row="0"]') as HTMLElement)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[data-item-diff="0"]')?.textContent).toContain('<img src=x onerror=alert(2)>')
  })
})

describe('honest states', () => {
  it('a legacy run stored no items', () => {
    answer({ items: [], total: 0, counts: {} })
    render(<EvalRunItems runId={REPLAY_RUN_ID} taskType="classification" />)
    expect(screen.getByText('A legacy label-agreement run stores no per-item outputs.')).toBeInTheDocument()
  })

  it("says it could not load the run's items, with Retry", () => {
    const mutate = vi.fn()
    answer(undefined, { error: new Error('boom'), mutate })
    render(<EvalRunItems runId={REPLAY_RUN_ID} taskType="classification" />)
    expect(screen.getByText(/Could not load the run's items\./)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalled()
  })
})
