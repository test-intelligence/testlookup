import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import AppVersionBadge from './AppVersionBadge'
import { buildTitle } from './buildInfo'
import type { SystemHealth } from '@/hooks/useSystemHealth'

// The badge reads the shared health poll rather than issuing its own request.
const mockHealth = vi.hoisted(() => ({ value: undefined as SystemHealth | undefined }))
vi.mock('@/hooks/useSystemHealth', () => ({
  useSystemHealth: () => mockHealth.value,
}))

function health(data: SystemHealth['data']): SystemHealth {
  return { data, unavailable: [], isDegraded: false }
}

const FULL_SHA = '1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b'

describe('AppVersionBadge', () => {
  beforeEach(() => {
    mockHealth.value = health(null)
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
