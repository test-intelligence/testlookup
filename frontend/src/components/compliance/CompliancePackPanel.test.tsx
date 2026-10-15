/**
 * HON-07: compliance packs are tamper-evident (a SHA-256 manifest), not
 * signed. `compliance_pack_service` has no HMAC and no PKI, and the flag's own
 * description says so; the panel promised "a signed ZIP".
 *
 * REL-04: a pack generated here shows in the /releases rail, which lists the
 * project's packs from its own SWR key; the panel revalidates that key.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CompliancePackPanel from './CompliancePackPanel'

const packsState = vi.hoisted(() => ({ packs: [] as unknown[] }))
const mocks = vi.hoisted(() => ({
  generate: vi.fn(),
  refreshRail: vi.fn(),
  refreshPanel: vi.fn(),
}))

vi.mock('@/hooks/useCompliancePacks', () => ({
  useCompliancePacks: () => ({ packs: packsState.packs, isLoading: false, isError: false, refresh: mocks.refreshPanel }),
}))

vi.mock('@/hooks/useProjectCompliancePacks', () => ({
  refreshProjectCompliancePacks: () => mocks.refreshRail(),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: true }),
}))

vi.mock('@/services/compliancePackService', async () => {
  const actual = await vi.importActual<typeof import('@/services/compliancePackService')>('@/services/compliancePackService')
  return { ...actual, compliancePackService: { generate: mocks.generate, download: vi.fn() } }
})

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

describe('CompliancePackPanel', () => {
  beforeEach(() => {
    packsState.packs = []
    vi.clearAllMocks()
  })

  it('calls the pack tamper-evident, never signed', () => {
    const { container } = render(<CompliancePackPanel releaseId="rel-1" releaseName="2026.10" />)

    expect(screen.getByText(/sealed with a tamper-evident SHA-256 manifest/)).toBeInTheDocument()
    expect(container).not.toHaveTextContent(/signed/i)
    expect(screen.getByRole('button', { name: /Generate pack/ })).toBeInTheDocument()
  })

  it('lists a generated pack with its manifest digest', () => {
    packsState.packs = [
      {
        id: 'pack-1',
        release_id: 'rel-1',
        generated_at: '2026-10-01T12:00:00Z',
        bytes: 2048,
        file_count: 9,
        manifest_sha256: 'ab'.repeat(32),
        notes: null,
      },
    ]
    const { container } = render(<CompliancePackPanel releaseId="rel-1" />)

    expect(screen.getByText(`sha256: ${'ab'.repeat(32)}`)).toBeInTheDocument()
    expect(screen.getByText(/2\.0 KB · 9 files/)).toBeInTheDocument()
    expect(container).not.toHaveTextContent(/signed/i)
  })

  it('revalidates the rail’s project pack list after a generate', async () => {
    mocks.generate.mockResolvedValue({ id: 'pack-2', bytes: 422_195 })
    render(<CompliancePackPanel releaseId="rel-1" releaseName="2026.10" />)

    fireEvent.click(screen.getByRole('button', { name: /Generate pack/ }))

    await waitFor(() => expect(mocks.refreshRail).toHaveBeenCalledTimes(1))
    expect(mocks.generate).toHaveBeenCalledWith('rel-1', { notes: undefined })
    expect(mocks.refreshPanel).toHaveBeenCalledTimes(1)
  })

  it('does not revalidate the rail when the generate is refused', async () => {
    mocks.generate.mockRejectedValue(Object.assign(new Error('x'), { response: { data: { detail: 'Compliance packs are off for this project' } } }))
    render(<CompliancePackPanel releaseId="rel-1" />)

    fireEvent.click(screen.getByRole('button', { name: /Generate pack/ }))

    await waitFor(() => expect(mocks.generate).toHaveBeenCalledTimes(1))
    expect(mocks.refreshRail).not.toHaveBeenCalled()
  })
})
