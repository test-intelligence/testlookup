/**
 * FAIL-05 pins what the retired `/intelligence` hub's links rely on: the
 * redirect to `/runs` keeps the query string, so `/intelligence?branch=main`
 * lands on the Runs list filtered by that branch (the page reads `q`,
 * `branch` and `verdict` from its URL). `legacyRedirects.tsx` needs no edit
 * for it; this test fails if it ever stops forwarding.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { IntelligenceRedirect } from './legacyRedirects'

function Where() {
  const { pathname, search } = useLocation()
  return <div data-testid="where">{`${pathname}${search}`}</div>
}

function landAt(from: string): string {
  render(
    <MemoryRouter initialEntries={[from]}>
      <Routes>
        <Route path="/intelligence" element={<IntelligenceRedirect />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
  return screen.getByTestId('where').textContent ?? ''
}

describe('/intelligence forwards its filters to /runs (FAIL-05)', () => {
  it('keeps branch and q', () => {
    expect(landAt('/intelligence?branch=main&q=7')).toBe('/runs?branch=main&q=7')
  })

  it('keeps a verdict too, and adds nothing when there is no query', () => {
    expect(landAt('/intelligence?verdict=NO_GO')).toBe('/runs?verdict=NO_GO')
  })

  it('no query: plain /runs', () => {
    expect(landAt('/intelligence')).toBe('/runs')
  })
})
