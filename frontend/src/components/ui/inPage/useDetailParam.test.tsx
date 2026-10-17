/**
 * W27 PAN-01: a page's detail rides the URL through useDetailParam. Driven
 * against a REAL memory router, so a PUSH, a REPLACE and a POP are what the
 * history actually did; every URL read waits for the COMMITTED location in
 * the same waitFor (W08's RunsPage flake).
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RouterProvider, createMemoryRouter, type InitialEntry } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { isDetailToken } from './ids'
import { DETAIL_STATE_KEY, useDetailParam, type DetailParamOptions } from './useDetailParam'

const A = '3f2b8c1e-9a4d-4e6f-8b2a-1c0d9e8f7a65'
const B = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f'

function Probe({ options }: { options?: DetailParamOptions }) {
  const detail = useDetailParam('release', options)
  return (
    <>
      <output data-testid="id">{detail.id ?? 'none'}</output>
      <output data-testid="rejected">{detail.rejected ?? 'none'}</output>
      <button type="button" onClick={() => detail.open(A)}>open A</button>
      <button type="button" onClick={() => detail.open(B)}>open B</button>
      <button type="button" onClick={() => detail.open('fp_1:checkout')}>open token</button>
      <button type="button" onClick={() => detail.close()}>close</button>
    </>
  )
}

function setup(entries: InitialEntry[], options?: DetailParamOptions) {
  const router = createMemoryRouter(
    [
      { path: '/start', element: <p>START</p> },
      { path: '/list', element: <Probe options={options} /> },
    ],
    { initialEntries: entries, initialIndex: entries.length - 1 },
  )
  render(<RouterProvider router={router} />)
  return router
}

const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }))

async function expectAt(
  router: ReturnType<typeof createMemoryRouter>,
  pathname: string,
  search: string,
  action?: 'PUSH' | 'REPLACE' | 'POP',
) {
  await waitFor(() => {
    expect(router.state.location.pathname).toBe(pathname)
    expect(router.state.location.search).toBe(search)
    if (action) expect(router.state.historyAction).toBe(action)
  })
}

describe('useDetailParam, push-open (the default)', () => {
  it('open from closed PUSHES: Back closes the detail', async () => {
    const router = setup(['/start', '/list?tab=x&q=y'])
    click('open A')
    await expectAt(router, '/list', `?tab=x&q=y&release=${A}`, 'PUSH')
    expect(screen.getByTestId('id')).toHaveTextContent(A)

    await act(() => router.navigate(-1))
    await expectAt(router, '/list', '?tab=x&q=y', 'POP')
    expect(screen.getByTestId('id')).toHaveTextContent('none')
  })

  it('a switch REPLACES: Back from B lands on the closed list, not on A', async () => {
    const router = setup(['/start', '/list?tab=x&q=y'])
    click('open A')
    await expectAt(router, '/list', `?tab=x&q=y&release=${A}`, 'PUSH')
    click('open B')
    await expectAt(router, '/list', `?tab=x&q=y&release=${B}`, 'REPLACE')

    await act(() => router.navigate(-1))
    await expectAt(router, '/list', '?tab=x&q=y', 'POP')
  })

  it('close after an open POPS our own entry (the history index comes back)', async () => {
    const router = setup(['/start', '/list?tab=x&q=y'])
    click('open A')
    await expectAt(router, '/list', `?tab=x&q=y&release=${A}`, 'PUSH')
    click('close')
    await expectAt(router, '/list', '?tab=x&q=y', 'POP')

    // One more Back leaves the page: the pushed entry is gone, not left behind.
    await act(() => router.navigate(-1))
    await expectAt(router, '/start', '')
  })

  it('close after a switch still pops (the switch kept the push marker)', async () => {
    const router = setup(['/start', '/list?tab=x&q=y'])
    click('open A')
    await expectAt(router, '/list', `?tab=x&q=y&release=${A}`, 'PUSH')
    click('open B')
    await expectAt(router, '/list', `?tab=x&q=y&release=${B}`, 'REPLACE')
    click('close')
    await expectAt(router, '/list', '?tab=x&q=y', 'POP')
  })

  it('a double close pops once, never past the list', async () => {
    const router = setup(['/start', '/list?tab=x&q=y'])
    click('open A')
    await expectAt(router, '/list', `?tab=x&q=y&release=${A}`, 'PUSH')
    click('close')
    click('close')
    await expectAt(router, '/list', '?tab=x&q=y', 'POP')
    expect(screen.queryByText('START')).toBeNull()
  })

  it('close of a deep-linked entry REPLACES (there is nothing of ours to pop)', async () => {
    const router = setup(['/start', `/list?tab=x&release=${A}&q=y`])
    expect(screen.getByTestId('id')).toHaveTextContent(A)
    click('close')
    await expectAt(router, '/list', '?tab=x&q=y', 'REPLACE')

    await act(() => router.navigate(-1))
    await expectAt(router, '/start', '')
  })

  it('keeps location.state on every write, and marks only the pushed entry', async () => {
    const router = setup(['/start', { pathname: '/list', search: '?tab=x', state: { from: 'runs' } }])
    click('open A')
    await expectAt(router, '/list', `?tab=x&release=${A}`, 'PUSH')
    expect(router.state.location.state).toEqual({ from: 'runs', [DETAIL_STATE_KEY]: 'release' })
    click('open B')
    await expectAt(router, '/list', `?tab=x&release=${B}`, 'REPLACE')
    expect(router.state.location.state).toEqual({ from: 'runs', [DETAIL_STATE_KEY]: 'release' })
  })

  it('open() of the open id is a no-op (no history write)', async () => {
    const router = setup(['/start', `/list?release=${A}`])
    const before = router.state.location.key
    click('open A')
    await act(async () => {})
    expect(router.state.location.key).toBe(before)
    expect(router.state.location.search).toBe(`?release=${A}`)
  })

  it('rejects a value that fails accept: ../../admin is never an id', () => {
    setup(['/list?release=../../admin'])
    expect(screen.getByTestId('id')).toHaveTextContent('none')
    expect(screen.getByTestId('rejected')).toHaveTextContent('../../admin')
  })

  it('an unknown-shape value is rejected, and close() drops it by replacing', async () => {
    const router = setup(['/start', '/list?tab=x&release=not-a-uuid'])
    expect(screen.getByTestId('rejected')).toHaveTextContent('not-a-uuid')
    click('close')
    await expectAt(router, '/list', '?tab=x', 'REPLACE')
    expect(screen.getByTestId('rejected')).toHaveTextContent('none')
  })

  it('takes a custom accept (a /flaky fingerprint)', async () => {
    const router = setup(['/start', '/list'], { accept: isDetailToken })
    click('open token')
    await expectAt(router, '/list', '?release=fp_1%3Acheckout', 'PUSH')
    expect(screen.getByTestId('id')).toHaveTextContent('fp_1:checkout')
  })
})

describe("useDetailParam, history: 'replace' (/test-management)", () => {
  it('never pushes: open, switch and close all replace', async () => {
    const router = setup(['/start', '/list?tab=x&q=y'], { history: 'replace' })
    click('open A')
    await expectAt(router, '/list', `?tab=x&q=y&release=${A}`, 'REPLACE')
    expect(router.state.location.state).toBeNull()
    click('open B')
    await expectAt(router, '/list', `?tab=x&q=y&release=${B}`, 'REPLACE')
    click('close')
    await expectAt(router, '/list', '?tab=x&q=y', 'REPLACE')

    // Back leaves the page, as it always did there.
    await act(() => router.navigate(-1))
    await expectAt(router, '/start', '')
  })
})
