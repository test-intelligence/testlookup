/**
 * W31 STRAT-12: "Your data" on Settings › Profile (contract §4.1, §4.4), through the REAL SWR hooks with the service
 * mocked (JOB_POLL_MS shortened to 40 ms): no export → the sentence + the button; start → ONE POST → the job polls
 * ("Preparing your archive…") → ready (size, signed, expiry) + Download with the job's id; the list read's error;
 * a 409 `export_running` resumes the live job; a refusal reads as its sentence. Also: the page mounts the card.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { exportRunningBody, subjectErrorBody, subjectJobFixture, subjectJobList, subjectRefusal } from '@/test/subjectRightsMocks'
import { ERROR_SENTENCES, UI_SENTENCES } from '@/types/subjectRights'

vi.mock('@/types/subjectRights', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/types/subjectRights')>()),
  JOB_POLL_MS: 40,
}))
const service = vi.hoisted(() => ({
  startMyExport: vi.fn(), listMyExports: vi.fn(), getJob: vi.fn(), downloadExport: vi.fn(),
}))
vi.mock('@/services/subjectRightsService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/subjectRightsService')>()),
  subjectRightsService: service,
}))
const auth = vi.hoisted(() => ({
  state: { user: { id: '1', email: 'a@example.com', username: 'alice', full_name: 'Alice Adams', role: 'TESTER', is_active: true, must_change_password: false, avatar_color: 'blue' }, setAuth: () => undefined, token: 't', refreshToken: 'r' },
}))
vi.mock('@/store/authStore', () => ({ useAuthStore: (selector: (s: typeof auth.state) => unknown) => selector(auth.state) }))
vi.mock('@/components/mfa/MfaSecuritySection', () => ({ default: () => <section data-testid="mfa-stub">Two-factor</section> }))

import { useMyExports } from '@/hooks/useSubjectRights'
import ProfilePage from '@/pages/settings/ProfilePage'
import MyDataCard from './MyDataCard'
import { DOWNLOAD_LABEL, ERASURE_BY_ADMIN, LOAD_EXPORTS_FAILED, newestExport } from './myDataModel'

/** The page's wiring, exactly: the list read's data and error handed to the card. */
function Harness() {
  const { data, error, mutate } = useMyExports()
  return <MyDataCard loadError={error} latest={newestExport(data)} onChanged={mutate} />
}

function withSwr(children: ReactNode) {
  return <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>{children}</SWRConfig>
}

const startButton = () => screen.getByRole('button', { name: UI_SENTENCES.my_data_start })
const downloadButton = () => screen.queryByRole('button', { name: DOWNLOAD_LABEL })

beforeEach(() => {
  for (const fn of Object.values(service)) fn.mockReset()
})

describe('MyDataCard', () => {
  it('no export yet: the heading, the body, "No export yet.", an enabled start button, no Download, the erasure line', async () => {
    service.listMyExports.mockResolvedValue(subjectJobList([]))
    render(withSwr(<Harness />))
    expect(await screen.findByText(UI_SENTENCES.export_none)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: UI_SENTENCES.my_data_title })).toBeInTheDocument()
    expect(screen.getByText(UI_SENTENCES.my_data_body)).toBeInTheDocument()
    expect(startButton()).toBeEnabled()
    expect(downloadButton()).toBeNull()
    expect(screen.getByText(ERASURE_BY_ADMIN)).toBeInTheDocument()
  })

  it('start → ONE POST → "Preparing your archive…" while the job polls → ready (size, signed, expiry) + Download', async () => {
    const queued = subjectJobFixture({ status: 'queued' })
    const ready = subjectJobFixture({ id: queued.id, status: 'succeeded' })
    service.listMyExports.mockResolvedValueOnce(subjectJobList([])).mockResolvedValue(subjectJobList([ready]))
    service.startMyExport.mockResolvedValue(queued)
    service.getJob
      .mockResolvedValueOnce(subjectJobFixture({ id: queued.id, status: 'running' }))
      .mockResolvedValueOnce(subjectJobFixture({ id: queued.id, status: 'running' }))
      .mockResolvedValue(ready)
    render(withSwr(<Harness />))
    fireEvent.click(await screen.findByRole('button', { name: UI_SENTENCES.my_data_start }))
    expect(await screen.findByText(UI_SENTENCES.export_queued)).toBeInTheDocument()
    expect(startButton()).toBeDisabled()
    expect(await screen.findByText(/^Ready — 1\.4 MB, signed\. Available until .+\.$/)).toBeInTheDocument()
    expect(service.startMyExport).toHaveBeenCalledTimes(1)
    expect(service.getJob.mock.calls.every(([id]) => id === queued.id)).toBe(true)
    expect(service.getJob.mock.calls.length).toBeGreaterThanOrEqual(3)
    expect(downloadButton()).toBeEnabled()
    expect(startButton()).toBeEnabled()
  })

  it('a ready export: Download streams that job (its id); a refused download reads its sentence, never the Axios words', async () => {
    const ready = subjectJobFixture({ status: 'succeeded' })
    service.listMyExports.mockResolvedValue(subjectJobList([ready]))
    service.downloadExport.mockResolvedValueOnce('testlookup-personal-data.zip')
    render(withSwr(<Harness />))
    await waitFor(() => expect(downloadButton()).toBeEnabled())
    fireEvent.click(downloadButton() as HTMLElement)
    await waitFor(() => expect(service.downloadExport).toHaveBeenCalledWith(ready.id))
    service.downloadExport.mockRejectedValueOnce(subjectRefusal(410, subjectErrorBody('export_expired')))
    fireEvent.click(downloadButton() as HTMLElement)
    expect(await screen.findByRole('alert')).toHaveTextContent(ERROR_SENTENCES.export_expired)
    expect(screen.queryByText(/Request failed with status code/)).toBeNull()
  })

  it('the list read fails: "Could not load your exports." (the error the page hands in)', async () => {
    service.listMyExports.mockRejectedValue(new Error('boom'))
    render(withSwr(<Harness />))
    expect(await screen.findByText(LOAD_EXPORTS_FAILED)).toBeInTheDocument()
    expect(screen.queryByText(UI_SENTENCES.export_none)).toBeNull()
  })

  it('a 409 export_running resumes the live job (its status, no error); a 403 reads its sentence', async () => {
    const live = subjectJobFixture({ status: 'running' })
    service.listMyExports.mockResolvedValue(subjectJobList([]))
    service.getJob.mockResolvedValue(live)
    service.startMyExport.mockRejectedValueOnce(subjectRefusal(409, exportRunningBody(live.id)))
    render(withSwr(<Harness />))
    fireEvent.click(await screen.findByRole('button', { name: UI_SENTENCES.my_data_start }))
    expect(await screen.findByText(UI_SENTENCES.export_queued)).toBeInTheDocument()
    await waitFor(() => expect(service.getJob).toHaveBeenCalledWith(live.id))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a refused start renders the server sentence as text', async () => {
    service.listMyExports.mockResolvedValue(subjectJobList([]))
    service.startMyExport.mockRejectedValueOnce(subjectRefusal(403, subjectErrorBody('session_required')))
    render(withSwr(<Harness />))
    fireEvent.click(await screen.findByRole('button', { name: UI_SENTENCES.my_data_start }))
    expect(await screen.findByRole('alert')).toHaveTextContent(ERROR_SENTENCES.session_required)
  })
})

describe('ProfilePage', () => {
  it('mounts "Your data" after two-factor, full width (outside the two-up form grid)', async () => {
    service.listMyExports.mockResolvedValue(subjectJobList([]))
    const { container } = render(withSwr(<ProfilePage />))
    const card = await screen.findByRole('region', { name: UI_SENTENCES.my_data_title })
    expect(screen.getByTestId('mfa-stub').nextElementSibling).toBe(card)
    expect((container.querySelector('[data-profile-forms]') as HTMLElement).contains(card)).toBe(false)
    expect(await screen.findByText(UI_SENTENCES.export_none)).toBeInTheDocument()
  })
})
