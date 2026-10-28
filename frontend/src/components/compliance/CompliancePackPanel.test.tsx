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
  // W11 REL-11 (set-up only: the existing cases never call them)
  verify: vi.fn(),
  downloadPublicKey: vi.fn(),
}))

vi.mock('@/hooks/useCompliancePacks', () => ({
  useCompliancePacks: () => ({ packs: packsState.packs, isLoading: false, isError: false, refresh: mocks.refreshPanel }),
}))

vi.mock('@/hooks/useProjectCompliancePacks', () => ({
  refreshProjectCompliancePacks: () => mocks.refreshRail(),
}))

// W22a: role flags the pack row's Hold / Retire / Delete read; the existing cases keep their single flag.
const perms = vi.hoisted(() => ({ extra: {} as Record<string, boolean> }))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: true, ...perms.extra }),
}))

vi.mock('@/services/compliancePackService', async () => {
  const actual = await vi.importActual<typeof import('@/services/compliancePackService')>('@/services/compliancePackService')
  return {
    ...actual,
    compliancePackService: {
      generate: mocks.generate,
      download: vi.fn(),
      verify: mocks.verify,
      downloadPublicKey: mocks.downloadPublicKey,
    },
  }
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

// ── W11 REL-11: the signature chip, Verify and the public key ───────────────

const signedPack = {
  id: 'pack-9',
  release_id: 'rel-1',
  generated_at: '2026-10-21T12:00:00Z',
  bytes: 4096,
  file_count: 13,
  manifest_sha256: 'cd'.repeat(32),
  notes: null,
  signing_key_id: 'k-1a2b3c4d5e6f7a8b',
}

const verifiedAnswer = {
  pack_id: 'pack-9',
  result: 'verified',
  chain: 'intact',
  manifest_matches_record: true,
  signature: 'valid',
  signing_key_id: 'k-1a2b3c4d5e6f7a8b',
  files_checked: 12,
  files_ok: 12,
  files: [{ name: 'decision.json', status: 'ok' }],
  summary: 'Verified · 12 files · signed by key k-1a2b3c4d5e6f7a8b',
  checked_at: '2026-10-23T10:00:00Z',
}

describe('CompliancePackPanel — signing and verification (W11 REL-11)', () => {
  beforeEach(() => {
    packsState.packs = []
    vi.clearAllMocks()
  })

  it('chips a signed pack by its key and an unsigned one as tamper-evident only', () => {
    packsState.packs = [signedPack, { ...signedPack, id: 'pack-8', signing_key_id: null }]
    render(<CompliancePackPanel releaseId="rel-1" />)

    const signed = screen.getByText('Signed · k-1a2b3c4d')
    expect(signed).toHaveAttribute('title', 'k-1a2b3c4d5e6f7a8b')
    expect(screen.getByText('Unsigned (tamper-evident only)')).toBeInTheDocument()
    // Public key only where there is a key to download.
    expect(screen.getAllByRole('button', { name: /Public key/ })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Verify' })).toHaveLength(2)
  })

  it('verifies a pack and renders the answer under its row, as text', async () => {
    packsState.packs = [signedPack]
    mocks.verify.mockResolvedValue(verifiedAnswer)
    const { container } = render(<CompliancePackPanel releaseId="rel-1" />)

    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))

    await waitFor(() => expect(screen.getByText(verifiedAnswer.summary)).toBeInTheDocument())
    expect(mocks.verify).toHaveBeenCalledWith('pack-9')
    expect(screen.getByText('12 of 12 files match')).toBeInTheDocument()
    const result = container.querySelector('[data-pack-verification]')
    expect(result).not.toBeNull()
    expect(screen.getByText(verifiedAnswer.summary).className).toContain('--ui-success-ink')
  })

  it('names each tampered file, and renders file names as text', async () => {
    packsState.packs = [signedPack]
    mocks.verify.mockResolvedValue({
      ...verifiedAnswer,
      result: 'tampered',
      chain: 'broken',
      files_ok: 11,
      files: [
        { name: 'decision.json', status: 'mismatch' },
        { name: '<img src=x onerror=alert(1)>.json', status: 'unexpected' },
      ],
      summary: 'Tampered: decision.json does not match the manifest (+1 more)',
    })
    const { container } = render(<CompliancePackPanel releaseId="rel-1" />)

    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))

    await waitFor(() => expect(screen.getByText(/^Tampered: decision\.json/)).toBeInTheDocument())
    expect(screen.getByText(/^Tampered: decision\.json/).className).toContain('--ui-danger-ink')
    expect(screen.getByText('decision.json — does not match the manifest')).toBeInTheDocument()
    expect(screen.getByText('<img src=x onerror=alert(1)>.json — is not listed in the manifest')).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
  })

  it('shows an unreadable pack muted, with no file lines', async () => {
    packsState.packs = [signedPack]
    mocks.verify.mockResolvedValue({
      ...verifiedAnswer, result: 'unreadable', chain: 'broken', files: [], files_ok: 0, files_checked: 0,
      summary: 'Could not read the pack from storage',
    })
    render(<CompliancePackPanel releaseId="rel-1" />)

    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))

    await waitFor(() => expect(screen.getByText('Could not read the pack from storage')).toBeInTheDocument())
    expect(screen.getByText('Could not read the pack from storage').className).toContain('--color-text-muted')
    expect(screen.queryByText(/files match/)).toBeNull()
  })

  it('says it could not verify when the request is refused', async () => {
    packsState.packs = [signedPack]
    mocks.verify.mockRejectedValue(Object.assign(new Error('x'), { response: { data: { detail: 'You do not have access to this project' } } }))
    render(<CompliancePackPanel releaseId="rel-1" />)

    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))

    await waitFor(() => expect(
      screen.getByText('Could not verify this pack. You do not have access to this project'),
    ).toBeInTheDocument())
  })

  it('downloads the public key of a signed pack', async () => {
    packsState.packs = [signedPack]
    mocks.downloadPublicKey.mockResolvedValue(undefined)
    render(<CompliancePackPanel releaseId="rel-1" />)

    fireEvent.click(screen.getByRole('button', { name: /Public key/ }))

    await waitFor(() => expect(mocks.downloadPublicKey).toHaveBeenCalledWith('k-1a2b3c4d5e6f7a8b'))
  })

  // ── W22a: the pack row's hold chip and lifecycle controls ──────────────────

  const heldPack = {
    id: 'pack-held', release_id: 'rel-1', project_id: 'proj-1', generated_at: '2026-10-01T12:00:00Z',
    bytes: 2048, file_count: 9, manifest_sha256: 'cd'.repeat(32), notes: null,
    retention_expires_at: '2001-01-01T00:00:00Z',
    holds: [{ hold_id: 'h-1', entity_type: 'release', entity_id: 'rel-1', entity_label: 'release 2026.10',
      reason: 'Audit Q4 sentinel', expires_at: null }],
  }

  it('marks a held pack On hold and disables Retire / Delete with the Held sentence (ADMIN)', () => {
    perms.extra = { isQaLead: true, isAdmin: true }
    packsState.packs = [heldPack]
    render(<CompliancePackPanel releaseId="rel-1" />)

    expect(screen.getByText('On hold')).toHaveAttribute('title', 'Held: Audit Q4 sentinel')
    expect(screen.getByRole('button', { name: 'Hold' })).toBeEnabled()
    for (const name of ['Retire', 'Delete']) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
      expect(screen.getByRole('button', { name })).toHaveAttribute('title', 'Held: Audit Q4 sentinel')
    }
    perms.extra = {}
  })

  it('shows no lifecycle control below QA lead and no chip on an unheld pack', () => {
    perms.extra = { isQaLead: false, isAdmin: false }
    packsState.packs = [{ ...heldPack, holds: [] }]
    render(<CompliancePackPanel releaseId="rel-1" />)

    expect(screen.queryByText('On hold')).toBeNull()
    for (const name of ['Hold', 'Retire', 'Delete']) expect(screen.queryByRole('button', { name })).toBeNull()
    perms.extra = {}
  })
})
