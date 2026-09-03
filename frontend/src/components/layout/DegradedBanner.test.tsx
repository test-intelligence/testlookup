/**
 * Regression: the banner must say "unknown", not nothing, when the health
 * poll fails.
 *
 * Before `isUnreachable` existed, a total backend outage produced
 * `isDegraded: false` and this component returned `null` — indistinguishable
 * from a healthy system. The unreachable branch is checked FIRST because in
 * that state there are no per-check results to list, so the dependency banner
 * below it would render nothing at all.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import DegradedBanner from './DegradedBanner'
import type { SystemHealth } from '@/hooks/useSystemHealth'

const mockHealth = vi.hoisted(() => ({ value: undefined as SystemHealth | undefined }))
vi.mock('@/hooks/useSystemHealth', () => ({
  useSystemHealth: () => mockHealth.value,
}))

function health(overrides: Partial<SystemHealth> = {}): SystemHealth {
  return {
    data: null,
    unavailable: [],
    criticalUnavailable: [],
    isDegraded: false,
    isUnreachable: false,
    ...overrides,
  }
}

describe('DegradedBanner', () => {
  beforeEach(() => {
    mockHealth.value = health()
  })

  it('renders nothing when everything is healthy', () => {
    const { container } = render(<DegradedBanner />)
    expect(container).toBeEmptyDOMElement()
  })

  it('names the unreachable OPTIONAL dependencies with the fallback reassurance', () => {
    // Optional-only degradation (MinIO, ChromaDB) — a feature is lost, not the
    // data, so the "falls back to PostgreSQL" reassurance is honest here.
    mockHealth.value = health({ unavailable: ['minio', 'chromadb'], isDegraded: true })
    render(<DegradedBanner />)
    expect(screen.getByText(/MinIO, ChromaDB unreachable/)).toBeInTheDocument()
    expect(screen.getByText(/show data from PostgreSQL where possible/)).toBeInTheDocument()
    expect(screen.queryByTestId('health-critical-banner')).not.toBeInTheDocument()
  })

  it('escalates to a critical alert when a core store is unreachable', () => {
    // A critical store (postgres/mongo/redis) being down loses the data itself.
    mockHealth.value = health({
      unavailable: ['postgres', 'chromadb'],
      criticalUnavailable: ['postgres'],
      isDegraded: true,
    })
    render(<DegradedBanner />)

    const banner = screen.getByTestId('health-critical-banner')
    expect(banner).toHaveAttribute('role', 'alert')
    expect(banner).toHaveTextContent(/Critical services unreachable:\s*PostgreSQL/)
    expect(banner).toHaveTextContent(/Core data is unavailable/)
    // The critical branch must NOT promise the PostgreSQL fallback — least of
    // all when PostgreSQL is the store that is down.
    expect(screen.queryByText(/show data from PostgreSQL where possible/)).not.toBeInTheDocument()
  })

  it('does not name PostgreSQL as a fallback when PostgreSQL itself is the outage', () => {
    // The self-contradiction guard: the old single message told the operator
    // their pages would "show data from PostgreSQL" while PostgreSQL was down.
    mockHealth.value = health({
      unavailable: ['postgres'],
      criticalUnavailable: ['postgres'],
      isDegraded: true,
    })
    render(<DegradedBanner />)
    expect(screen.queryByText(/show data from PostgreSQL where possible/)).not.toBeInTheDocument()
  })

  it('says the backend is unreachable — not nothing — when the health poll failed', () => {
    mockHealth.value = health({ isUnreachable: true })
    render(<DegradedBanner />)

    const banner = screen.getByTestId('health-unreachable-banner')
    expect(banner).toBeInTheDocument()
    // The whole point: silence used to read as health.
    expect(banner).toHaveTextContent(/Health status is unknown, not healthy/i)
    expect(banner).toHaveAttribute('role', 'alert')
  })

  it('prefers the unreachable banner over a stale dependency list', () => {
    // SWR keeps the last good payload on error, so `unavailable` can still be
    // populated from a poll that is now minutes old. The louder, current fact
    // wins.
    mockHealth.value = health({ unavailable: ['redis'], isDegraded: true, isUnreachable: true })
    render(<DegradedBanner />)

    expect(screen.getByTestId('health-unreachable-banner')).toBeInTheDocument()
    expect(screen.queryByText(/Redis unreachable/)).not.toBeInTheDocument()
  })
})
