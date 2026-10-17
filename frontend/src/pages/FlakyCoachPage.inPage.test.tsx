/**
 * W27a PAN-04 (`W27a-contract.md` §2.4, assertion 6; §1.3 `/flaky`): a
 * Detected row's details open UNDER the row, and the open test rides the URL
 * as `?test=<fingerprint>`. Driven against a REAL memory router: a PUSH, a
 * REPLACE and a POP are what the history did, and every URL read waits for
 * the COMMITTED location inside the same waitFor (W08's RunsPage flake).
 *
 *  - a row click PUSHES `?test=<fp>` (the page's other params kept) and the
 *    details render in a full-width row right after it (`td[colspan="7"]`
 *    with the action column, 6 without); Back closes them;
 *  - a deep link (a refresh) opens them again once the entries are there;
 *  - a fingerprint no entry has is dropped (replace); one that is not a
 *    plain token (`a/b`) is rejected and dropped, and nothing opens;
 *  - the caller's row form (`renderBelowRow`) sits between the row and the
 *    details; Close panel and Escape close the details;
 *  - no `aria-modal`, no `role="dialog"`.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { RouterProvider, createMemoryRouter, type InitialEntry } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { FlakyCoachEntry } from '@/services/testHealthService'

vi.mock('@/hooks/useTestHealth', () => ({
  useFlakyCoach: () => ({ coach: undefined, isLoading: false, refresh: vi.fn() }),
}))
vi.mock('@/services/testHealthService', () => ({ testHealthService: { refreshFlakyCoach: vi.fn() } }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import { FlakyCoachBody } from './FlakyCoachPage'

function entry(fp: string): FlakyCoachEntry {
  return {
    test_fingerprint: fp, test_name: `test_${fp}`, suite_name: 'Checkout', failure_rate: 0.4, total_runs: 20,
    failed_runs: 8, flaky_since: null, last_failure_at: null, quarantine_recommendation: 'INVESTIGATE',
    stabilization_actions: [`Stabilise ${fp}`], impact_score: 10, status_history: ['PASSED', 'FAILED'],
    flaky_score: null,
  }
}
const ENTRIES = [entry('fp-a1'), entry('fp-b2'), entry('fp-c3')]

function setup(entries: InitialEntry[], props: Partial<Parameters<typeof FlakyCoachBody>[0]> = {}) {
  const router = createMemoryRouter(
    [
      { path: '/start', element: <p>START</p> },
      {
        path: '/flaky',
        element: (
          <FlakyCoachBody
            entries={ENTRIES}
            renderAction={(e) => <span>act {e.test_fingerprint}</span>}
            actionLabel="Quarantine"
            {...props}
          />
        ),
      },
    ],
    { initialEntries: entries, initialIndex: entries.length - 1 },
  )
  render(<RouterProvider router={router} />)
  return router
}

async function expectAt(router: ReturnType<typeof createMemoryRouter>, search: string, action?: 'PUSH' | 'REPLACE' | 'POP') {
  await waitFor(() => {
    expect(router.state.location.pathname).toBe('/flaky')
    expect(router.state.location.search).toBe(search)
    if (action) expect(router.state.historyAction).toBe(action)
  })
}

const rowOf = (fp: string) => document.querySelector(`tr[data-flaky-row="${fp}"]`) as HTMLElement
const details = (name: string) => screen.queryByRole('complementary', { name })

describe('/flaky Detected: the details under the row, ?test=<fingerprint> (W27 PAN-04)', () => {
  it('a row click PUSHES ?test=<fp> (other params kept) and the details render in td[colspan="7"] right after it; Back closes them', async () => {
    const router = setup(['/start', '/flaky?tab=detected'])
    fireEvent.click(rowOf('fp-b2'))
    await expectAt(router, '?tab=detected&test=fp-b2', 'PUSH')
    const panel = details('test_fp-b2') as HTMLElement
    expect(panel).not.toBeNull()
    const detailRow = panel.closest('tr') as HTMLElement
    expect(detailRow).toHaveAttribute('data-in-page-row')
    expect(detailRow.previousElementSibling).toBe(rowOf('fp-b2'))
    expect(detailRow.querySelector('td')).toHaveAttribute('colspan', '7')
    expect(within(panel).getByText('Stabilise fp-b2')).toBeInTheDocument()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()

    await act(() => router.navigate(-1))
    await expectAt(router, '?tab=detected', 'POP')
    expect(details('test_fp-b2')).toBeNull()
  })

  it('switching rows REPLACES (Back from the second lands on the closed list)', async () => {
    const router = setup(['/start', '/flaky'])
    fireEvent.click(within(rowOf('fp-a1')).getByRole('button', { name: 'test_fp-a1' }))
    await expectAt(router, '?test=fp-a1', 'PUSH')
    fireEvent.click(rowOf('fp-c3'))
    await expectAt(router, '?test=fp-c3', 'REPLACE')
    expect(details('test_fp-a1')).toBeNull()
    expect(details('test_fp-c3')).not.toBeNull()
    await act(() => router.navigate(-1))
    await expectAt(router, '', 'POP')
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('a deep link (a refresh) opens the details again; Close panel closes them (replace: nothing of ours to pop)', async () => {
    const router = setup(['/start', '/flaky?test=fp-c3'])
    const panel = await waitFor(() => details('test_fp-c3') as HTMLElement)
    expect((panel.closest('tr') as HTMLElement).previousElementSibling).toBe(rowOf('fp-c3'))
    fireEvent.click(within(panel).getByRole('button', { name: 'Close panel' }))
    await expectAt(router, '', 'REPLACE')
    expect(details('test_fp-c3')).toBeNull()
    expect(router.state.location.pathname).toBe('/flaky')
  })

  it('Escape inside the details closes them', async () => {
    const router = setup(['/start', '/flaky'])
    fireEvent.click(rowOf('fp-a1'))
    await expectAt(router, '?test=fp-a1', 'PUSH')
    const panel = details('test_fp-a1') as HTMLElement
    fireEvent.keyDown(within(panel).getByRole('button', { name: 'Close panel' }), { key: 'Escape' })
    await expectAt(router, '', 'POP')
    expect(details('test_fp-a1')).toBeNull()
  })

  it('a fingerprint no entry has is dropped once the entries are there (replace), and nothing opens', async () => {
    const router = setup(['/start', '/flaky?tab=detected&test=fp-gone'])
    await expectAt(router, '?tab=detected', 'REPLACE')
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it.each(['a%2Fb', '..', '%3Cimg%3E'])('a fingerprint that is not a plain token (%s) is rejected and dropped', async (raw) => {
    const router = setup(['/start', `/flaky?test=${raw}`])
    await expectAt(router, '', 'REPLACE')
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it("without an action column the details span 6 columns; the caller's row form sits between the row and its details", async () => {
    const router = setup(['/start', '/flaky'], {
      renderAction: undefined,
      renderBelowRow: (e) => (e.test_fingerprint === 'fp-b2' ? <p data-testid="row-form">form for fp-b2</p> : null),
    })
    const formRow = (screen.getByTestId('row-form').closest('tr') as HTMLElement)
    expect(formRow).toHaveAttribute('data-in-page-row')
    expect(formRow.previousElementSibling).toBe(rowOf('fp-b2'))
    expect(formRow.querySelector('td')).toHaveAttribute('colspan', '6')
    // Only that row has one.
    expect(document.querySelectorAll('tr[data-in-page-row]')).toHaveLength(1)

    fireEvent.click(rowOf('fp-b2'))
    await expectAt(router, '?test=fp-b2', 'PUSH')
    const detailRow = (details('test_fp-b2') as HTMLElement).closest('tr') as HTMLElement
    expect(detailRow.previousElementSibling).toBe(formRow)
    expect(detailRow.querySelector('td')).toHaveAttribute('colspan', '6')
  })
})
