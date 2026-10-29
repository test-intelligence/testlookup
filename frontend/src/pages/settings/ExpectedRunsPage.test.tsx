/**
 * W23a TRND-03: Settings › Expected runs.
 *
 * The REAL service and SWR hooks run (an isolated SWRConfig + the app's mutate bridge, so a write's
 * revalidation is real); only the HTTP helpers (`services/http`) are mocked, so every assertion on a
 * request is on the path, body and params the browser would send.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SwrMutateBridge } from '@/components/SwrMutateBridge'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { cadence, cadenceStatus, missedWindow, statusItem } from '@/test/insightCadenceMocks'
import { CADENCE_ERRORS, type RunCadence } from '@/types/runCadence'
import ExpectedRunsPage from './ExpectedRunsPage'

const { http, storeState, perms } = vi.hoisted(() => ({
  http: { getData: vi.fn(), postData: vi.fn(), patchData: vi.fn(), deleteData: vi.fn() },
  storeState: {
    activeProjectId: 'proj-1' as string | null,
    projects: [{ id: 'proj-1', name: 'Checkout' }],
    setActiveProject: vi.fn(),
  },
  perms: { isQaLead: true },
}))

vi.mock('@/services/http', () => ({
  getData: (...a: unknown[]) => http.getData(...a),
  postData: (...a: unknown[]) => http.postData(...a),
  patchData: (...a: unknown[]) => http.patchData(...a),
  deleteData: (...a: unknown[]) => http.deleteData(...a),
}))

vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  return { ...actual, useProjectStore: (sel: (s: typeof storeState) => unknown) => sel(storeState) }
})

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isQaLead: perms.isQaLead, canAccessManagement: perms.isQaLead }),
}))

// The suite suggestions read the last 30 days' runs; nothing here is about them.
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: () => ({ options: ['nightly-regression'], runs: [], isLoading: false }) }))

const BASE = '/api/v1/projects/proj-1/run-cadences'

let rows: RunCadence[] = []
let status = cadenceStatus('none')

function serve() {
  http.getData.mockImplementation((url: string) => {
    if (url === BASE) return Promise.resolve({ items: rows, total: rows.length })
    if (url === `${BASE}/status`) return Promise.resolve(status)
    return Promise.reject(new Error(`unexpected GET ${url}`))
  })
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <SwrMutateBridge />
      <MemoryRouter>
        <ExpectedRunsPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

function row(id: string) {
  const el = document.querySelector(`[data-cadence-id="${id}"]`)
  expect(el, `row ${id}`).not.toBeNull()
  return el as HTMLElement
}

beforeEach(() => {
  vi.clearAllMocks()
  storeState.activeProjectId = 'proj-1'
  perms.isQaLead = true
  rows = []
  status = cadenceStatus('none')
  serve()
})

describe('ExpectedRunsPage', () => {
  it('All Projects: the project prompt and no request', () => {
    storeState.activeProjectId = ALL_PROJECTS_ID
    renderPage()
    expect(screen.getByRole('heading', { name: 'Expected runs' })).toBeInTheDocument()
    expect(screen.getByText(/Expected runs belong to one project/)).toBeInTheDocument()
    expect(http.getData).not.toHaveBeenCalled()
  })

  it('reads the list and the last 14 days on load, and polls nothing', async () => {
    rows = [cadence({ id: 'c-1' })]
    status = cadenceStatus([statusItem({
      cadence: rows[0], expected: 14, met: 12, missed: 2,
      missed_windows: [missedWindow(1, { local_label: 'Wed 28 Oct' }), missedWindow(2, { local_label: 'Tue 27 Oct' })],
    })])
    renderPage()
    expect(await screen.findByText('Last 14 days: 12 met · 2 missed (Tue, Wed)')).toBeInTheDocument()
    expect(within(row('c-1')).getByText('Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min')).toBeInTheDocument()
    expect(http.getData.mock.calls.map((c) => [c[0], c[1]])).toEqual(expect.arrayContaining([
      [BASE, undefined], [`${BASE}/status`, { params: { days: 14 } }],
    ]))
    expect(http.getData).toHaveBeenCalledTimes(2)
  })

  it('the empty state offers Add to a QA lead', async () => {
    renderPage()
    expect(await screen.findByText('No expected cadence configured. Add one so a missing run is noticed.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Add expected cadence' }).length).toBeGreaterThan(0)
  })

  // W23a demo: with the Add form open, the empty state's own Add button under it read as a second action.
  it('the open Add form leaves one Add button, the panel header', async () => {
    renderPage()
    await screen.findByText('No expected cadence configured. Add one so a missing run is noticed.')
    fireEvent.click(screen.getAllByRole('button', { name: 'Add expected cadence' })[0])
    expect(screen.getByRole('region', { name: 'Add expected cadence' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Add expected cadence' })).toHaveLength(1)
    expect(screen.getByText('No expected cadence configured. Add one so a missing run is noticed.')).toBeInTheDocument()
  })

  it('a reader below QA lead sees the table without Add or actions', async () => {
    perms.isQaLead = false
    rows = [cadence({ id: 'c-1', suite_name: '<img src=x onerror=alert(1)>' })]
    renderPage()
    expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Add expected cadence' })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull()
    expect(within(row('c-1')).queryByRole('button')).toBeNull()
  })

  it('a failed read says so, with Retry', async () => {
    http.getData.mockImplementation((url: string) =>
      url === BASE ? Promise.reject(new Error('boom')) : Promise.resolve(status))
    renderPage()
    expect(await screen.findByText('Could not load the expected runs.')).toBeInTheDocument()
    serve()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No expected cadence configured/)).toBeInTheDocument()
  })
})

describe('ExpectedRunsPage: a QA lead’s writes, each in the page', () => {
  it('Add posts the body, closes the form and re-reads the list', async () => {
    http.postData.mockImplementation((url: string, body: Record<string, unknown>) => {
      rows = [cadence({ id: 'c-new', suite_name: body.suite_name as string })]
      return Promise.resolve(rows[0])
    })
    renderPage()
    await screen.findByText(/No expected cadence configured/)
    fireEvent.click(screen.getAllByRole('button', { name: 'Add expected cadence' })[0])
    const section = screen.getByRole('region', { name: 'Add expected cadence' })
    fireEvent.click(within(section).getByLabelText('Runs of suite'))
    fireEvent.change(within(section).getByLabelText('Suite name'), { target: { value: 'nightly-regression' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Weekdays' }))
    fireEvent.change(within(section).getByLabelText('Expected by'), { target: { value: '06:00' } })
    fireEvent.change(within(section).getByLabelText('Time zone'), { target: { value: 'UTC' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(http.postData).toHaveBeenCalledTimes(1))
    expect(http.postData).toHaveBeenCalledWith(BASE, {
      suite_name: 'nightly-regression', mode: 'weekly', weekdays: ['mon', 'tue', 'wed', 'thu', 'fri'],
      expected_by: '06:00', timezone: 'UTC', grace_minutes: 60,
    }, { suppressToast: true })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Add expected cadence' })).toBeNull())
    expect(await screen.findByText('nightly-regression')).toBeInTheDocument()
  })

  it('a 409 reads inline in the form, once', async () => {
    http.postData.mockRejectedValue(Object.assign(new Error('Request failed'), {
      isAxiosError: true,
      response: { status: 409, data: { detail: { code: 'too_many', message: CADENCE_ERRORS.too_many } } },
    }))
    renderPage()
    await screen.findByText(/No expected cadence configured/)
    fireEvent.click(screen.getAllByRole('button', { name: 'Add expected cadence' })[0])
    const section = screen.getByRole('region', { name: 'Add expected cadence' })
    fireEvent.click(within(section).getByRole('button', { name: 'Every day' }))
    fireEvent.change(within(section).getByLabelText('Expected by'), { target: { value: '02:30' } })
    fireEvent.click(within(section).getByRole('button', { name: 'Save' }))
    expect(await within(section).findByRole('alert')).toHaveTextContent(CADENCE_ERRORS.too_many)
    expect(screen.getAllByText(CADENCE_ERRORS.too_many)).toHaveLength(1)
  })

  it('Pause asks for a reason under the row, then posts until (with an offset) and the reason', async () => {
    rows = [cadence({ id: 'c-1' })]
    http.postData.mockResolvedValue(cadence({ id: 'c-1', paused: true }))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Pause nightly-regression' }))
    const form = document.querySelector('[data-cadence-pause="c-1"]') as HTMLElement
    const future = new Date(Date.now() + 2 * 86_400_000)
    const local = `${future.getFullYear()}-${String(future.getMonth() + 1).padStart(2, '0')}-${String(future.getDate()).padStart(2, '0')}T23:59`
    fireEvent.change(within(form).getByLabelText('Pause until'), { target: { value: local } })
    fireEvent.click(within(form).getByRole('button', { name: 'Pause expectations' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent(CADENCE_ERRORS.pause_reason_required)
    fireEvent.change(within(form).getByLabelText('Reason'), { target: { value: 'release freeze' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Pause expectations' }))
    await waitFor(() => expect(http.postData).toHaveBeenCalledTimes(1))
    const [url, body, config] = http.postData.mock.calls[0]
    expect(url).toBe(`${BASE}/c-1/pause`)
    expect(body.reason).toBe('release freeze')
    expect(body.until).toMatch(new RegExp(`^${local}:00[+-]\\d\\d:\\d\\d$`))
    expect(config).toEqual({ suppressToast: true })
  })

  it('Resume posts at once; Delete asks inline first', async () => {
    rows = [cadence({ id: 'c-1', paused: true, paused_until: new Date(Date.now() + 86_400_000).toISOString(), paused_reason: 'freeze' })]
    http.postData.mockResolvedValue(cadence({ id: 'c-1' }))
    http.deleteData.mockResolvedValue(undefined)
    renderPage()
    const resume = await screen.findByRole('button', { name: 'Resume nightly-regression' })
    expect(within(row('c-1')).getByText('Paused')).toBeInTheDocument()
    expect(within(row('c-1')).getByText('freeze')).toBeInTheDocument()
    fireEvent.click(resume)
    await waitFor(() => expect(http.postData).toHaveBeenCalledWith(`${BASE}/c-1/resume`, undefined, { suppressToast: true }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete nightly-regression' }))
    expect(http.deleteData).not.toHaveBeenCalled()
    const confirm = await screen.findByRole('group', { name: 'Remove this expectation? Its window history goes with it.' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(http.deleteData).toHaveBeenCalledWith(`${BASE}/c-1`, { suppressToast: true }))
  })
})
