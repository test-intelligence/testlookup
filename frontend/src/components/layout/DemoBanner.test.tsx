/**
 * The public demo's standing notice. AppLayout lazy-loads it only for a
 * session opened by the demo's one-click sign-in (authStore.demoSession, see
 * AppLayout.test.tsx); this pins what it says.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import DemoBanner, { GET_TESTLOOKUP_URL } from './DemoBanner'

describe('DemoBanner', () => {
  it('says it is a read-only demo and links to the product', () => {
    render(<DemoBanner />)
    const banner = screen.getByTestId('demo-banner')
    expect(banner).toHaveTextContent(/live demo/i)
    expect(banner).toHaveTextContent(/read-only/i)
    expect(banner).toHaveTextContent(/reset every night/i)
    expect(screen.getByRole('link', { name: /get testlookup/i })).toHaveAttribute('href', GET_TESTLOOKUP_URL)
  })

  it('says the demo is refreshing, calmly, while the backend is unreachable', () => {
    // The nightly rebuild takes the backend away for a minute or two; a visitor
    // with the app open saw the outage alarm "Can't reach the TestLookup backend".
    render(<DemoBanner refreshing />)
    const banner = screen.getByTestId('demo-refreshing-banner')
    expect(banner).toHaveTextContent(/The live demo is refreshing/)
    expect(banner).toHaveTextContent(/03:00 UTC/)
    expect(banner).toHaveTextContent(/Explore the demo again/)
    expect(banner).toHaveAttribute('role', 'status')
    expect(screen.queryByTestId('demo-banner')).not.toBeInTheDocument()
  })
})
