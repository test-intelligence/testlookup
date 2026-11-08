/**
 * The public demo's standing notice: shown only when the server says it is the
 * demo (`GET /auth/options` → `demo_mode`), never on a real deployment, and
 * never when the answer is unknown.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockAuthOptions = vi.fn()
vi.mock('@/services/authService', () => ({
  getAuthOptions: () => mockAuthOptions(),
}))

import DemoBanner, { GET_TESTLOOKUP_URL } from './DemoBanner'

function renderBanner() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <DemoBanner />
    </SWRConfig>,
  )
}

describe('DemoBanner', () => {
  beforeEach(() => {
    mockAuthOptions.mockReset()
  })

  it('says it is a read-only demo and links to the product', async () => {
    mockAuthOptions.mockResolvedValue({ registration_enabled: false, demo_mode: true })
    renderBanner()
    const banner = await screen.findByTestId('demo-banner')
    expect(banner).toHaveTextContent(/live demo/i)
    expect(banner).toHaveTextContent(/read-only/i)
    expect(banner).toHaveTextContent(/reset every night/i)
    expect(screen.getByRole('link', { name: /get testlookup/i })).toHaveAttribute('href', GET_TESTLOOKUP_URL)
  })

  it.each([false, undefined])('renders nothing when demo_mode is %s', async (flag) => {
    mockAuthOptions.mockResolvedValue({ registration_enabled: false, demo_mode: flag })
    const { container } = renderBanner()
    await waitFor(() => expect(mockAuthOptions).toHaveBeenCalled())
    await act(async () => {})
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the options request fails', async () => {
    mockAuthOptions.mockRejectedValue(new Error('offline'))
    const { container } = renderBanner()
    await waitFor(() => expect(mockAuthOptions).toHaveBeenCalled())
    await act(async () => {})
    expect(container).toBeEmptyDOMElement()
  })
})
