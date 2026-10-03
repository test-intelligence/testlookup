/**
 * FK0 M0b: the rows panel, rendered for real (SidePanel, router links, the
 * page announcer, SWR under an isolated cache) with only `chartGet` replaced.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import envelope from '../../../../../contracts/viz/fixtures/envelope/valid/filtered.json'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import { RowsPanel } from './RowsPanel'
import { ROWS_URL, type RowsItem, type RowsPanelProps } from './RowsPanel.model'

const chartGet = vi.hoisted(() => vi.fn())
vi.mock('@/services/chartApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/chartApi')>()),
  chartGet,
}))

const meta = envelope.payload
const HOSTILE = '<img src=x onerror="window.__xss=1">'

const item = (i: number, over: Partial<RowsItem> = {}): RowsItem => ({
  id: `tc-${i}`,
  test_name: `test_${i}`,
  test_fingerprint: `fp-${i}`,
  suite: 'payments',
  status: 'failed',
  duration_ms: 1500,
  run_id: `run-${i}`,
  release: { id: 'rel-1', name: '2026.09' },
  created_at: '2026-09-12T10:00:00Z',
  failure_category: null,
  error_line: null,
  ...over,
})

const page = (items: RowsItem[], over: Record<string, unknown> = {}) => ({
  items,
  total: items.length,
  page: 1,
  size: 50,
  pages: 1,
  reconciliation: { mark_field: 'y', measure: 'rows', value: items.length },
  meta,
  ...over,
})

const BASE: RowsPanelProps = {
  selectors: [
    { dimension: 'suite', value: 'payments' },
    { dimension: 'status', value: 'failed' },
  ],
  chart: { metric: 'failures', groupBy: ['suite', 'status'] },
  scope: { project_id: 'p1', days: 30 },
  title: 'payments, failed',
  onClose: () => {},
}

function mount(props: Partial<RowsPanelProps> = {}) {
  const all = { ...BASE, ...props }
  const view = render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <ChartAnnouncerProvider>
          <RowsPanel {...all} />
        </ChartAnnouncerProvider>
      </MemoryRouter>
    </SWRConfig>,
  )
  return {
    ...view,
    rerenderWith: (next: Partial<RowsPanelProps>) =>
      view.rerender(
        <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
          <MemoryRouter>
            <ChartAnnouncerProvider>
              <RowsPanel {...all} {...next} />
            </ChartAnnouncerProvider>
          </MemoryRouter>
        </SWRConfig>,
      ),
  }
}

const state = () => document.querySelector('[data-rows-state]')?.getAttribute('data-rows-state')

beforeEach(() => {
  chartGet.mockReset()
  document.body.innerHTML = ''
})

describe('RowsPanel', () => {
  it('closed: renders nothing and asks for nothing', () => {
    mount({ selectors: [] })
    expect(screen.queryByRole('heading')).toBeNull()
    expect(document.querySelector('[data-rows-state]')).toBeNull()
    expect(chartGet).not.toHaveBeenCalled()
  })

  it('an unresolved scope: the panel is open and loading, nothing is requested yet', () => {
    mount({ scope: null })
    expect(screen.getByRole('heading', { name: 'Executions in payments, failed' })).toBeTruthy()
    expect(state()).toBe('loading')
    expect(chartGet).not.toHaveBeenCalled()
  })

  it('asks chart-data/rows with the chart’s spec, the scope and one bucket per selector', async () => {
    chartGet.mockResolvedValue({ data: page([item(1)]), requestId: 'r1' })
    mount()
    await waitFor(() => expect(state()).toBe('ready'))
    expect(chartGet).toHaveBeenCalledWith(ROWS_URL, {
      params: {
        project_id: 'p1',
        days: 30,
        metric: 'failures',
        group_by: ['suite', 'status'],
        bucket_suite: 'payments',
        bucket_status: 'failed',
        page: 1,
        size: 50,
      },
      signal: expect.any(AbortSignal),
    })
  })

  it('draws every execution as text, each test linking to its result; hostile names stay text', async () => {
    chartGet.mockResolvedValue({
      data: page([
        item(1, { test_name: HOSTILE, error_line: `Error: ${HOSTILE}`, suite: HOSTILE, release: { id: 'rel-x', name: null } }),
        item(2, { release: null, duration_ms: null, suite: null }),
      ]),
      requestId: null,
    })
    mount()
    await waitFor(() => expect(state()).toBe('ready'))
    expect(document.querySelector('[data-rows-count]')?.textContent).toBe('2 executions')
    const link = screen.getByRole('link', { name: HOSTILE })
    expect(link.getAttribute('href')).toBe('/runs/run-1/tests/tc-1')
    expect(document.querySelector('img')).toBeNull()
    const second = document.querySelector('[data-rows-item="tc-2"]') as HTMLElement
    expect(second.textContent).toContain('—')
    // A release with no name shows its id rather than nothing.
    expect(document.querySelector('[data-rows-item="tc-1"]')?.textContent).toContain('rel-x')
    expect(screen.getByRole('table')).toBeTruthy()
    expect(screen.getAllByRole('columnheader').map((th) => th.textContent)).toEqual([
      'Test', 'Status', 'Suite', 'Duration', 'Release', 'Run date',
    ])
  })

  // B0 (Wave 3): six columns are wider than the 420 px panel and "Run date" was cut off.
  it('the table scrolls sideways inside a named, focusable region, never clipped', async () => {
    chartGet.mockResolvedValue({ data: page([item(1)]), requestId: null })
    mount()
    await waitFor(() => expect(state()).toBe('ready'))
    const region = screen.getByRole('region', { name: 'Executions table' })
    expect(region).toHaveAttribute('tabIndex', '0')
    expect(region.className).toContain('overflow-x-auto')
    expect(region.className).toContain('focus-visible:ring-2')
    expect(region.querySelector('[data-rows-table]')).not.toBeNull()
    // A date or a duration never wraps a character per line (seen in the browser): the table grows and scrolls.
    expect(screen.getByRole('columnheader', { name: 'Run date' }).className).toContain('whitespace-nowrap')
    const cells = (region.querySelector('[data-rows-item="tc-1"]') as HTMLElement).querySelectorAll('td')
    expect(cells[cells.length - 1].className).toContain('whitespace-nowrap')
  })

  it('speaks once, through the page announcer, when the rows arrive', async () => {
    chartGet.mockResolvedValue({ data: page([item(1), item(2), item(3)]), requestId: null })
    mount()
    await waitFor(() => expect(state()).toBe('ready'))
    expect(document.querySelector('[data-chart-announcer="assertive"]')?.textContent).toBe('3 executions in payments, failed')
    // No live region of the panel's own: every one on the page is the announcer's.
    for (const region of document.querySelectorAll('[aria-live], [role="status"]')) {
      expect(region.hasAttribute('data-chart-announcer')).toBe(true)
    }
  })

  it('explains a total that differs from what the chart drew', async () => {
    chartGet.mockResolvedValue({ data: page([item(1)], { reconciliation: { mark_field: 'y', measure: 'rows', value: 40 } }), requestId: null })
    mount({ expected: { y: 42, n: 50, asOf: '2026-09-19T10:42:07Z' } })
    await waitFor(() => expect(state()).toBe('ready'))
    expect(document.querySelector('[data-rows-notice]')?.textContent).toBe(
      '40 executions now; the chart counted 42 at 2026-09-19 10:42:07 UTC. Results have changed since the chart loaded.',
    )
  })

  it('no notice when it adds up', async () => {
    chartGet.mockResolvedValue({ data: page([item(1)]), requestId: null })
    mount({ expected: { y: 1, n: 1, asOf: null } })
    await waitFor(() => expect(state()).toBe('ready'))
    expect(document.querySelector('[data-rows-notice]')).toBeNull()
  })

  it('pages: Previous is disabled on page 1, Next asks for page 2', async () => {
    chartGet.mockImplementation(async (_url: string, { params }: { params: { page: number } }) => ({
      data: page([item(params.page)], { total: 120, pages: 3, page: params.page }),
      requestId: null,
    }))
    mount()
    await waitFor(() => expect(state()).toBe('ready'))
    expect((screen.getByRole('button', { name: 'Previous page' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(document.querySelector('[data-rows-page]')?.textContent).toBe('Page 2 of 3'))
    expect(chartGet).toHaveBeenLastCalledWith(ROWS_URL, expect.objectContaining({ params: expect.objectContaining({ page: 2 }) }))
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    await waitFor(() => expect(document.querySelector('[data-rows-page]')?.textContent).toBe('Page 1 of 3'))
  })

  it('a new mark starts again on page 1', async () => {
    chartGet.mockImplementation(async (_url: string, { params }: { params: { page: number } }) => ({
      data: page([item(params.page)], { total: 120, pages: 3, page: params.page }),
      requestId: null,
    }))
    const view = mount()
    await waitFor(() => expect(state()).toBe('ready'))
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(document.querySelector('[data-rows-page]')?.textContent).toBe('Page 2 of 3'))
    await act(async () => view.rerenderWith({ selectors: [{ dimension: 'suite', value: 'cart' }] }))
    await waitFor(() => expect(document.querySelector('[data-rows-page]')?.textContent).toBe('Page 1 of 3'))
    expect(chartGet).toHaveBeenLastCalledWith(ROWS_URL, expect.objectContaining({ params: expect.objectContaining({ bucket_suite: 'cart', page: 1 }) }))
  })

  it('nothing matching is said, not drawn as an empty table', async () => {
    chartGet.mockResolvedValue({ data: page([], { total: 0, pages: 0, reconciliation: { mark_field: 'y', measure: 'rows', value: 0 } }), requestId: null })
    mount()
    await waitFor(() => expect(state()).toBe('empty'))
    expect(screen.queryByRole('table')).toBeNull()
    expect(document.querySelector('[data-chart-announcer="assertive"]')?.textContent).toBe('0 executions in payments, failed')
  })

  it('a server error shows the message, the request id and Retry; Retry asks again', async () => {
    const failure = Object.assign(new Error('boom'), { response: { status: 500, headers: { 'x-request-id': 'req-500' } } })
    chartGet.mockRejectedValueOnce(failure).mockResolvedValue({ data: page([item(1)]), requestId: null })
    mount()
    await waitFor(() => expect(state()).toBe('error'))
    expect(document.querySelector('[data-rows-state="error"]')?.textContent).toContain('The server could not produce this chart.')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(state()).toBe('ready'))
  })

  it('a body that fails validation is an error, never a half-drawn table', async () => {
    chartGet.mockResolvedValue({ data: { items: 'nope' }, requestId: 'req-bad' })
    mount()
    await waitFor(() => expect(state()).toBe('error'))
    expect(document.querySelector('[data-rows-state="error"]')?.textContent).toContain('req-bad')
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('403 says the reader has no access', async () => {
    chartGet.mockRejectedValue(Object.assign(new Error('no'), { response: { status: 403, headers: {} } }))
    mount()
    await waitFor(() => expect(state()).toBe('forbidden'))
  })

  it('axe finds nothing on the open panel with a page of hostile rows and the pager', async () => {
    chartGet.mockResolvedValue({
      data: page([item(1, { test_name: HOSTILE, error_line: HOSTILE }), item(2)], { total: 120, pages: 3 }),
      requestId: null,
    })
    mount({ expected: { y: 5, n: 5, asOf: null } })
    await waitFor(() => expect(state()).toBe('ready'))
    // jsdom has no layout: contrast is checked in the browser (the e2e axe runs).
    const results = await axe.run(document.body, { rules: { 'color-contrast': { enabled: false } } })
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
  })

  it('Escape and the close button close it through the host', async () => {
    chartGet.mockResolvedValue({ data: page([item(1)]), requestId: null })
    const onClose = vi.fn()
    mount({ onClose })
    await waitFor(() => expect(state()).toBe('ready'))
    fireEvent.click(screen.getByRole('button', { name: 'Close executions' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
