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

import {
  exportRunningBody,
  myErasureRequestFixture,
  REQUEST_ID,
  subjectErrorBody,
  subjectJobFixture,
  subjectJobList,
  subjectRefusal,
} from '@/test/subjectRightsMocks'
import { DECLINE_SENTENCES, ERROR_SENTENCES, UI_SENTENCES } from '@/types/subjectRights'
import { formatDay } from '@/utils/formatters'

vi.mock('@/types/subjectRights', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/types/subjectRights')>()),
  JOB_POLL_MS: 40,
}))
const service = vi.hoisted(() => ({
  startMyExport: vi.fn(), listMyExports: vi.fn(), getJob: vi.fn(), downloadExport: vi.fn(),
  getMyErasureRequest: vi.fn(), fileErasureRequest: vi.fn(), withdrawErasureRequest: vi.fn(),
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
import { DOWNLOAD_LABEL, LOAD_ERASURE_REQUEST_FAILED, LOAD_EXPORTS_FAILED, newestExport } from './myDataModel'

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
const restartButton = () => screen.getByRole('button', { name: UI_SENTENCES.my_data_restart })

beforeEach(() => {
  for (const fn of Object.values(service)) fn.mockReset()
  // W38: the card's erasure block reads my newest request; none by default.
  service.getMyErasureRequest.mockResolvedValue(null)
})

describe('MyDataCard', () => {
  it('no export yet: the heading, the body, "No export yet.", an enabled start button, no Download, the erasure block', async () => {
    service.listMyExports.mockResolvedValue(subjectJobList([]))
    render(withSwr(<Harness />))
    expect(await screen.findByText(UI_SENTENCES.export_none)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: UI_SENTENCES.my_data_title })).toBeInTheDocument()
    expect(screen.getByText(UI_SENTENCES.my_data_body)).toBeInTheDocument()
    expect(startButton()).toBeEnabled()
    expect(downloadButton()).toBeNull()
    // W38 (2026-11-07): the muted "ask an administrator" line became the erasure REQUEST block (contract §4).
    expect(await screen.findByRole('button', { name: UI_SENTENCES.erasure_request_start })).toBeEnabled()
    expect(screen.queryByText(/To have your account erased, ask an administrator/)).toBeNull()
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
    // W32: with the export ready, the start button offers a NEW copy (Download fetches the ready one).
    expect(restartButton()).toBeEnabled()
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

  it('a ready export (W32, OR-10 (5)): the buttons read "Prepare a new copy" and "Download", never "Download my data"', async () => {
    const ready = subjectJobFixture({ status: 'succeeded' })
    service.listMyExports.mockResolvedValue(subjectJobList([ready]))
    render(withSwr(<Harness />))
    await waitFor(() => expect(downloadButton()).toBeEnabled())
    expect(restartButton()).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Prepare a new copy' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: UI_SENTENCES.my_data_start })).toBeNull()
  })

  it('a failed list read beside a ready export: Download is hidden, so the start button reads "Download my data"', async () => {
    const ready = subjectJobFixture({ status: 'succeeded' })
    render(withSwr(<MyDataCard latest={ready} loadError={new Error('boom')} onChanged={() => undefined} />))
    expect(await screen.findByText(LOAD_EXPORTS_FAILED)).toBeInTheDocument()
    expect(startButton()).toBeEnabled()
    expect(screen.queryByRole('button', { name: UI_SENTENCES.my_data_restart })).toBeNull()
    expect(downloadButton()).toBeNull()
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

describe('MyDataCard erasure request (W38, contract §4)', () => {
  const block = () => document.querySelector('[data-my-erasure]') as HTMLElement
  const askButton = () => screen.queryByRole('button', { name: UI_SENTENCES.erasure_request_start })
  const withdrawTrigger = () => screen.getAllByRole('button', { name: UI_SENTENCES.erasure_request_withdraw })[0]

  async function renderCard() {
    service.listMyExports.mockResolvedValue(subjectJobList([]))
    const view = render(withSwr(<Harness />))
    await screen.findByText(UI_SENTENCES.export_none)
    await waitFor(() => expect(block().getAttribute('data-erasure-request-state')).not.toBe('loading'))
    return view
  }

  it('none: the button alone; it opens the confirm IN the card (no dialog, no native confirm); Cancel sends nothing', async () => {
    const nativeConfirm = vi.spyOn(window, 'confirm')
    await renderCard()
    expect(block()).toHaveAttribute('data-erasure-request-state', 'none')
    expect(block().querySelector('[data-my-erasure-line]')).toBeNull()
    fireEvent.click(askButton() as HTMLElement)
    const strip = screen.getByTestId('my-erasure-confirm')
    expect(block().contains(strip)).toBe(true)
    expect(strip).toHaveTextContent(UI_SENTENCES.erasure_request_confirm)
    expect(screen.getByRole('button', { name: UI_SENTENCES.erasure_request_send })).toBeEnabled()
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByTestId('my-erasure-confirm')).toBeNull()
    expect(service.fileErasureRequest).not.toHaveBeenCalled()
    expect(nativeConfirm).not.toHaveBeenCalled()
    nativeConfirm.mockRestore()
  })

  it('Send request → ONE POST (no body) → pending: "You asked on <date>. …" + Withdraw request, no Ask button', async () => {
    const pending = myErasureRequestFixture()
    service.fileErasureRequest.mockResolvedValueOnce(pending)
    await renderCard()
    fireEvent.click(askButton() as HTMLElement)
    fireEvent.click(screen.getByRole('button', { name: UI_SENTENCES.erasure_request_send }))
    await waitFor(() => expect(block()).toHaveAttribute('data-erasure-request-state', 'pending'))
    expect(service.fileErasureRequest).toHaveBeenCalledTimes(1)
    expect(service.fileErasureRequest).toHaveBeenCalledWith()
    const line = `You asked on ${formatDay(pending.requested_at)}. An administrator will review it.`
    expect(screen.getByText(line)).toBeInTheDocument()
    expect(withdrawTrigger()).toBeEnabled()
    expect(askButton()).toBeNull()
    expect(screen.queryByTestId('my-erasure-confirm')).toBeNull()
  })

  it('pending → Withdraw request asks in the card → the strip sends ONE withdraw → withdrawn: the Ask button again', async () => {
    service.getMyErasureRequest.mockResolvedValue(myErasureRequestFixture())
    service.withdrawErasureRequest.mockResolvedValueOnce(myErasureRequestFixture({ status: 'withdrawn' }))
    await renderCard()
    expect(block()).toHaveAttribute('data-erasure-request-state', 'pending')
    fireEvent.click(withdrawTrigger())
    const strip = screen.getByTestId('my-erasure-withdraw-confirm')
    expect(strip).toHaveTextContent('Withdraw your erasure request?')
    expect(service.withdrawErasureRequest).not.toHaveBeenCalled()
    fireEvent.click(strip.querySelector('[data-inline-confirm-action]') as HTMLElement)
    await waitFor(() => expect(block()).toHaveAttribute('data-erasure-request-state', 'withdrawn'))
    expect(service.withdrawErasureRequest).toHaveBeenCalledTimes(1)
    expect(askButton()).toBeEnabled()
    expect(block().querySelector('[data-my-erasure-line]')).toBeNull()
  })

  it('declined: "Declined on <date>: <the reason\'s sentence>" + the Ask button again', async () => {
    const declined = myErasureRequestFixture({ status: 'declined', decline_code: 'active_contract' })
    service.getMyErasureRequest.mockResolvedValue(declined)
    await renderCard()
    expect(block()).toHaveAttribute('data-erasure-request-state', 'declined')
    expect(screen.getByText(`Declined on ${formatDay(declined.decided_at)}: ${DECLINE_SENTENCES.active_contract}`)).toBeInTheDocument()
    expect(askButton()).toBeEnabled()
  })

  it('withdrawn: the Ask button, no line', async () => {
    service.getMyErasureRequest.mockResolvedValue(myErasureRequestFixture({ status: 'withdrawn' }))
    await renderCard()
    expect(block()).toHaveAttribute('data-erasure-request-state', 'withdrawn')
    expect(askButton()).toBeEnabled()
    expect(block().querySelector('[data-my-erasure-line]')).toBeNull()
  })

  it('a refusal renders inline as its sentence (last_admin_self) and the block re-reads; never the Axios words', async () => {
    service.fileErasureRequest.mockRejectedValueOnce(subjectRefusal(409, subjectErrorBody('last_admin_self')))
    await renderCard()
    fireEvent.click(askButton() as HTMLElement)
    fireEvent.click(screen.getByRole('button', { name: UI_SENTENCES.erasure_request_send }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(ERROR_SENTENCES.last_admin_self)
    expect(block().contains(alert)).toBe(true)
    expect(screen.queryByText(/Request failed with status code/)).toBeNull()
    expect(screen.queryByTestId('my-erasure-confirm')).toBeNull()
    await waitFor(() => expect(service.getMyErasureRequest).toHaveBeenCalledTimes(2))
  })

  it('a 409 request_pending (another tab) re-reads: the block shows the pending request', async () => {
    service.getMyErasureRequest.mockResolvedValueOnce(null).mockResolvedValue(myErasureRequestFixture())
    service.fileErasureRequest.mockRejectedValueOnce(
      subjectRefusal(409, subjectErrorBody('request_pending', {}, { request_id: REQUEST_ID })),
    )
    await renderCard()
    fireEvent.click(askButton() as HTMLElement)
    fireEvent.click(screen.getByRole('button', { name: UI_SENTENCES.erasure_request_send }))
    expect(await screen.findByRole('alert')).toHaveTextContent(ERROR_SENTENCES.request_pending)
    await waitFor(() => expect(block()).toHaveAttribute('data-erasure-request-state', 'pending'))
  })

  it('the read fails: "Could not load your erasure request." and the Ask button stays offered', async () => {
    service.getMyErasureRequest.mockRejectedValue(new Error('boom'))
    await renderCard()
    expect(block()).toHaveAttribute('data-erasure-request-state', 'error')
    expect(screen.getByText(LOAD_ERASURE_REQUEST_FAILED)).toBeInTheDocument()
    expect(askButton()).toBeEnabled()
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
