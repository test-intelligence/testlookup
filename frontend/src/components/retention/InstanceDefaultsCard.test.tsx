/**
 * W22b ACT-06 — the Instance defaults card on /settings/retention.
 *
 * The REAL hooks run (SWR on a fresh cache) over a mocked service, so "no request until Open" is the hook's null
 * key, not a mock's promise. Pins: nothing is read while collapsed; `Used by n projects`; an emptied stored value
 * is sent as `clear` and only changed values are sent; the load error sentence; the D12 phrase gate (`Turn on`
 * enabled only by the exact phrase, the body is `{confirmation, preview_hash}`); a 409 `preview_stale` re-reads the
 * preview; `Turn off` asks inline, then sends `{enabled: false}`; a non-admin sees nothing.
 */
import { createElement, type ReactNode } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import useSWR, { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { enablePreview, instancePolicy } from '@/test/retentionExportMocks'
import { POLICY_ERROR_SENTENCES } from '@/types/retentionExport'

const { service, perms } = vi.hoisted(() => ({
  service: {
    getInstancePolicy: vi.fn(),
    updateInstancePolicy: vi.fn(),
    previewInstanceEnable: vi.fn(),
    enableInstancePolicy: vi.fn(),
  },
  perms: { isAdmin: true },
}))
vi.mock('@/services/retentionInstanceService', () => ({ retentionInstanceService: service }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin: perms.isAdmin }) }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import InstanceDefaultsCard, { INSTANCE_LOAD_ERROR, TURN_OFF_QUESTION, TURN_ON_LABEL } from './InstanceDefaultsCard'

function renderCard(props: { open?: boolean } = {}) {
  const cache = new Map()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)
  return render(<InstanceDefaultsCard {...props} />, { wrapper })
}

function apiError(status: number, detail: unknown) {
  return Object.assign(new Error('request failed'), { isAxiosError: true, response: { status, data: { detail } } })
}

/** The input / select inside a field's block (its label also wraps the help lines). */
function field(name: string): HTMLElement {
  const block = document.querySelector(`[data-retention-instance-field="${name}"]`) as HTMLElement
  return block.querySelector('input, select') as HTMLElement
}

async function openCard() {
  fireEvent.click(screen.getByRole('button', { name: 'Open' }))
  await screen.findByText('Save defaults')
}

beforeEach(() => {
  vi.clearAllMocks()
  perms.isAdmin = true
  service.getInstancePolicy.mockResolvedValue(instancePolicy())
  service.previewInstanceEnable.mockResolvedValue(enablePreview(3))
})

describe('InstanceDefaultsCard — reads', () => {
  it('issues no request until Open', async () => {
    renderCard()
    expect(screen.getByText('Instance defaults')).toBeTruthy()
    expect(screen.getByText('Every project that does not set its own value uses these.')).toBeTruthy()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(service.getInstancePolicy).not.toHaveBeenCalled()
    expect(service.previewInstanceEnable).not.toHaveBeenCalled()
    await openCard()
    expect(service.getInstancePolicy).toHaveBeenCalledTimes(1)
    expect(service.previewInstanceEnable).not.toHaveBeenCalled()
  })

  it('shows each value with what it resolves to and how many projects inherit it', async () => {
    service.getInstancePolicy.mockResolvedValue(
      instancePolicy({
        runs_days: 180,
        inheriting_projects: { runs_days: 4, keep_last_runs: 1, purge_window_start_utc: 0 },
      }),
    )
    renderCard()
    await openCard()
    const runs = document.querySelector('[data-retention-instance-field="runs_days"]') as HTMLElement
    expect((within(runs).getByRole('spinbutton') as HTMLInputElement).value).toBe('180')
    expect(within(runs).getByText('180 days (instance default)')).toBeTruthy()
    expect(within(runs).getByText('Used by 4 projects')).toBeTruthy()
    const audit = document.querySelector('[data-retention-instance-field="audit_days"]') as HTMLElement
    expect((within(audit).getByRole('spinbutton') as HTMLInputElement).placeholder).toBe('default 2555')
    expect(within(audit).getByText('2555 days (default)')).toBeTruthy()
    const floor = document.querySelector('[data-retention-instance-used-by="keep_last_runs"]') as HTMLElement
    expect(floor.textContent).toBe('Used by 1 project')
    expect(screen.getByText('Off')).toBeTruthy()
  })

  it('says it could not load the defaults, never an empty form', async () => {
    service.getInstancePolicy.mockRejectedValue(apiError(500, 'boom'))
    renderCard()
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    expect(await screen.findByText(INSTANCE_LOAD_ERROR)).toBeTruthy()
    expect(screen.queryByText('Save defaults')).toBeNull()
  })

  it('renders nothing for a non-admin and reads nothing', async () => {
    perms.isAdmin = false
    const { container } = renderCard({ open: true })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(container.innerHTML).toBe('')
    expect(service.getInstancePolicy).not.toHaveBeenCalled()
  })
})

describe('InstanceDefaultsCard — save', () => {
  it('sends only the changed values and an emptied stored value as clear', async () => {
    service.getInstancePolicy.mockResolvedValue(instancePolicy({ runs_days: 180, artifacts_days: 200 }))
    service.updateInstancePolicy.mockResolvedValue(instancePolicy({ runs_days: 400 }))
    renderCard()
    await openCard()
    fireEvent.change(field('runs_days'), { target: { value: '400' } })
    fireEvent.change(field('artifacts_days'), { target: { value: '' } })
    fireEvent.change(field('purge_window_start_utc'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save defaults' }))
    await waitFor(() => expect(service.updateInstancePolicy).toHaveBeenCalledTimes(1))
    expect(service.updateInstancePolicy).toHaveBeenCalledWith({
      runs_days: 400,
      purge_window_start_utc: 5,
      clear: ['artifacts_days'],
    })
  })

  it('re-reads every cached project policy after a save, so the policy card shows the new instance default', async () => {
    // W22b local demo: runs 180 saved here, the project's card above kept "365 days (default)" until a reload.
    service.updateInstancePolicy.mockResolvedValue(instancePolicy({ runs_days: 180 }))
    const policyFetches = vi.fn(async () => ({ runs_days: null }))
    function ProjectPolicyReader() {
      useSWR('/api/v1/projects/p-sentinel/retention-policy', policyFetches)
      useSWR('/api/v1/projects/p-sentinel/storage', async () => ({}))
      return null
    }
    const cache = new Map()
    render(
      <SWRConfig value={{ provider: () => cache, dedupingInterval: 0 }}>
        <ProjectPolicyReader />
        <InstanceDefaultsCard />
      </SWRConfig>,
    )
    await waitFor(() => expect(policyFetches).toHaveBeenCalledTimes(1))
    await openCard()
    fireEvent.change(field('runs_days'), { target: { value: '180' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save defaults' }))
    await waitFor(() => expect(policyFetches).toHaveBeenCalledTimes(2))
  })

  it('shows the server sentence inline', async () => {
    service.updateInstancePolicy.mockRejectedValue(
      apiError(422, 'audit_days must be >= runs_days — audit records must outlive the runs they describe'),
    )
    renderCard()
    await openCard()
    fireEvent.change(field('runs_days'), { target: { value: '3000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save defaults' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/^audit_days must be >= runs_days/)
  })
})

describe('InstanceDefaultsCard — D12 enable', () => {
  async function openPreview() {
    renderCard()
    await openCard()
    fireEvent.click(screen.getByRole('button', { name: TURN_ON_LABEL }))
    await screen.findByText('Showing 3 of 3')
    return screen.getByRole('button', { name: 'Turn on' }) as HTMLButtonElement
  }

  it('reads the preview only when asked and lists every affected project', async () => {
    renderCard()
    await openCard()
    expect(service.previewInstanceEnable).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: TURN_ON_LABEL }))
    await screen.findByText('Showing 3 of 3')
    expect(service.previewInstanceEnable).toHaveBeenCalledTimes(1)
    const rows = document.querySelectorAll('[data-retention-instance-preview-row]')
    expect(Array.from(rows, (row) => row.querySelector('td')?.textContent)).toEqual([
      'Payments sentinel 1', 'Payments sentinel 2', 'Payments sentinel 3',
    ])
    expect(screen.getByText('Counts are before the keep-last floor and holds.')).toBeTruthy()
    expect(screen.getByText('Type "enable retention for 3 projects" to confirm')).toBeTruthy()
  })

  it('enables Turn on only on the exact phrase and sends it with the preview hash', async () => {
    service.enableInstancePolicy.mockResolvedValue(
      instancePolicy({ enabled: true, enabled_at: '2026-10-28T14:02:00Z', enabled_by_name: 'Avery Admin' }),
    )
    const turnOn = await openPreview()
    const input = screen.getByLabelText(/^Type "enable retention for 3 projects" to confirm/)
    expect(turnOn.disabled).toBe(true)
    fireEvent.change(input, { target: { value: 'Enable retention for 3 projects' } })
    expect(turnOn.disabled).toBe(true)
    fireEvent.change(input, { target: { value: 'enable retention for 3 projects ' } })
    expect(turnOn.disabled).toBe(true)
    fireEvent.change(input, { target: { value: 'enable retention for 3 projects' } })
    expect(turnOn.disabled).toBe(false)
    fireEvent.click(turnOn)
    await waitFor(() => expect(service.enableInstancePolicy).toHaveBeenCalledTimes(1))
    expect(service.enableInstancePolicy).toHaveBeenCalledWith({
      confirmation: 'enable retention for 3 projects',
      preview_hash: '5eed'.repeat(16),
    })
    expect(await screen.findByText('On since Oct 28, 2026 by Avery Admin')).toBeTruthy()
  })

  it('re-reads the preview on a 409 preview_stale and shows its sentence', async () => {
    service.enableInstancePolicy.mockRejectedValue(
      apiError(409, { code: 'preview_stale', message: POLICY_ERROR_SENTENCES.preview_stale }),
    )
    const turnOn = await openPreview()
    fireEvent.change(screen.getByLabelText(/^Type "enable/), { target: { value: 'enable retention for 3 projects' } })
    service.previewInstanceEnable.mockResolvedValue(enablePreview(4))
    fireEvent.click(turnOn)
    expect(await screen.findByText(POLICY_ERROR_SENTENCES.preview_stale)).toBeTruthy()
    await screen.findByText('Showing 4 of 4')
    expect(service.previewInstanceEnable).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Type "enable retention for 4 projects" to confirm')).toBeTruthy()
  })

  it('turns off only after the inline question, with {enabled: false}', async () => {
    service.getInstancePolicy.mockResolvedValue(
      instancePolicy({ enabled: true, enabled_at: '2026-10-28T14:02:00Z', enabled_by_name: 'Avery Admin' }),
    )
    service.updateInstancePolicy.mockResolvedValue(instancePolicy({ enabled: false }))
    renderCard()
    await openCard()
    expect(screen.getByText('On')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Turn off' }))
    expect(service.updateInstancePolicy).not.toHaveBeenCalled()
    const strip = screen.getByTestId('retention-instance-turn-off')
    expect(within(strip).getByText(TURN_OFF_QUESTION)).toBeTruthy()
    fireEvent.click(within(strip).getByRole('button', { name: 'Turn off' }))
    await waitFor(() => expect(service.updateInstancePolicy).toHaveBeenCalledWith({ enabled: false }))
    expect(await screen.findByRole('button', { name: TURN_ON_LABEL })).toBeTruthy()
  })
})
