import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import AppVersionBadge from './AppVersionBadge'
import { buildTitle } from './buildInfo'
import type { SystemHealth } from '@/hooks/useSystemHealth'

// The badge reads the shared health poll rather than issuing its own request.
const mockHealth = vi.hoisted(() => ({ value: undefined as SystemHealth | undefined }))
vi.mock('@/hooks/useSystemHealth', () => ({
  useSystemHealth: () => mockHealth.value,
}))

// Clicking the badge copies the full build identity via the shared clipboard
// helper (which carries its own secure-context/execCommand fallback). Mock it
// so the copy path is deterministic and we can assert exactly what is copied.
const copyMock = vi.hoisted(() => vi.fn(async (_value: string) => true))
vi.mock('@/utils/clipboard', () => ({
  copyTextToClipboard: (v: string) => copyMock(v),
}))

function health(data: SystemHealth['data']): SystemHealth {
  return { data, unavailable: [], isDegraded: false, isUnreachable: false }
}

const FULL_SHA = '1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b'

describe('AppVersionBadge', () => {
  beforeEach(() => {
    mockHealth.value = health(null)
    copyMock.mockClear()
    copyMock.mockResolvedValue(true)
  })

  it('renders nothing when the backend is unreachable (no health payload)', () => {
    mockHealth.value = health(null)
    const { container } = render(<AppVersionBadge />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the version is the unset "unknown" placeholder', () => {
    mockHealth.value = health({
      status: 'healthy', version: 'unknown', env: 'production',
      uptime_seconds: 1, timestamp: '', checks: {},
    })
    const { container } = render(<AppVersionBadge />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the version once the backend reports one', () => {
    mockHealth.value = health({
      status: 'healthy', version: '0.1.0', env: 'production',
      uptime_seconds: 1, timestamp: '', checks: {},
    })
    render(<AppVersionBadge />)
    expect(screen.getByTestId('app-version-badge')).toHaveTextContent('v0.1.0')
  })

  it('shows the short git revision beside the version when provenance is present', () => {
    mockHealth.value = health({
      status: 'healthy', version: '0.1.0', env: 'production',
      build: { revision: FULL_SHA, built_at: '2026-08-17T09:00:00Z' },
      uptime_seconds: 1, timestamp: '', checks: {},
    })
    render(<AppVersionBadge />)
    const badge = screen.getByTestId('app-version-badge')
    // The conventional 7-char short form, not the full 40-char SHA.
    expect(badge).toHaveTextContent('v0.1.0 · 1a2b3c4')
    expect(badge.textContent).not.toContain(FULL_SHA)
  })

  it('does not show a revision chip when the build env was unset', () => {
    mockHealth.value = health({
      status: 'healthy', version: '0.1.0', env: 'development',
      build: { revision: 'unknown', built_at: 'unknown' },
      uptime_seconds: 1, timestamp: '', checks: {},
    })
    render(<AppVersionBadge />)
    const badge = screen.getByTestId('app-version-badge')
    expect(badge).toHaveTextContent('v0.1.0')
    expect(badge.textContent).not.toContain('·')
    expect(badge.textContent).not.toContain('unknown')
  })

  it('spells out the full identity in the hover title, keeping the full SHA', () => {
    mockHealth.value = health({
      status: 'healthy', version: '0.1.0', env: 'production',
      build: { revision: FULL_SHA, built_at: '2026-08-17T09:00:00Z' },
      uptime_seconds: 1, timestamp: '', checks: {},
    })
    render(<AppVersionBadge />)
    const title = screen.getByTestId('app-version-badge').getAttribute('title') ?? ''
    expect(title).toContain('Version 0.1.0')
    expect(title).toContain(`Revision ${FULL_SHA}`)
    expect(title).toContain('Built 2026-08-17T09:00:00Z')
    expect(title).toContain('Env production')
  })

  it('copies the full build identity — full SHA and all — on click', async () => {
    mockHealth.value = health({
      status: 'healthy', version: '0.1.0', env: 'production',
      build: { revision: FULL_SHA, built_at: '2026-08-17T09:00:00Z' },
      uptime_seconds: 1, timestamp: '', checks: {},
    })
    render(<AppVersionBadge />)
    fireEvent.click(screen.getByTestId('app-version-badge'))

    await waitFor(() => expect(copyMock).toHaveBeenCalledTimes(1))
    const copied = copyMock.mock.calls[0][0]
    // The clipboard payload is the full identity, not the truncated chip: an
    // operator pasting into a bug report needs the exact commit, not "1a2b3c4".
    expect(copied).toContain('Version 0.1.0')
    expect(copied).toContain(`Revision ${FULL_SHA}`)
    expect(copied).toContain('Built 2026-08-17T09:00:00Z')
    expect(copied).toContain('Env production')
    // The copy is exactly what the tooltip spells out — no "Click to copy" hint
    // leaking into the pasted text.
    expect(copied).not.toContain('Click to copy')
  })

  it('confirms the copy in the tooltip after a successful click', async () => {
    mockHealth.value = health({
      status: 'healthy', version: '0.1.0', env: 'production',
      uptime_seconds: 1, timestamp: '', checks: {},
    })
    render(<AppVersionBadge />)
    const badge = screen.getByTestId('app-version-badge')
    // Before the click the tooltip invites the action.
    expect(badge.getAttribute('title')).toContain('Click to copy')
    fireEvent.click(badge)
    await waitFor(() =>
      expect(screen.getByTestId('app-version-badge').getAttribute('title')).toBe('Build details copied'),
    )
  })

  it('surfaces a manual-copy hint when the clipboard is unavailable', async () => {
    // Regression: a plain-HTTP self-host can block both clipboard paths; the
    // click must say so rather than silently doing nothing.
    copyMock.mockResolvedValue(false)
    mockHealth.value = health({
      status: 'healthy', version: '0.1.0', env: 'production',
      uptime_seconds: 1, timestamp: '', checks: {},
    })
    render(<AppVersionBadge />)
    fireEvent.click(screen.getByTestId('app-version-badge'))
    await waitFor(() =>
      expect(screen.getByTestId('app-version-badge').getAttribute('title')).toContain('Ctrl/⌘-C'),
    )
  })
})

describe('buildTitle', () => {
  it('omits fields whose provenance is unset rather than printing "unknown"', () => {
    const title = buildTitle('0.1.0', { revision: 'unknown', built_at: 'unknown' }, 'development')
    expect(title).toBe('Version 0.1.0\nEnv development')
  })

  it('assembles every present field, newline-separated', () => {
    const title = buildTitle('2.3.4', { revision: 'abcdef0', built_at: '2026-01-02T03:04:05Z' }, 'staging')
    expect(title).toBe('Version 2.3.4\nRevision abcdef0\nBuilt 2026-01-02T03:04:05Z\nEnv staging')
  })

  it('is empty when nothing is known', () => {
    expect(buildTitle(undefined, undefined, undefined)).toBe('')
    expect(buildTitle('unknown', { revision: '', built_at: '' }, '')).toBe('')
  })
})
