import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import RouteTabs, { routeTabActive, type RouteTabItem } from './RouteTabs'

const RUNS: RouteTabItem[] = [
  { to: '/runs', label: 'History', match: ['/runs/:runId'] },
  { to: '/live', label: 'Live' },
  { to: '/runs/compare', label: 'Compare' },
  { to: '/intelligence', label: 'AI verdicts', match: ['/runs/:runId/intelligence'] },
]

function activeAt(path: string): string | null {
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <RouteTabs ariaLabel="Runs" items={RUNS} />
    </MemoryRouter>,
  )
  const current = view.container.querySelector('[aria-current="page"]')
  const label = current?.textContent ?? null
  view.unmount()
  return label
}

describe('RouteTabs', () => {
  it('renders links to each route under a named nav', () => {
    render(
      <MemoryRouter initialEntries={['/runs']}>
        <RouteTabs ariaLabel="Runs" items={RUNS} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('navigation', { name: 'Runs' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Live' })).toHaveAttribute('href', '/live')
  })

  it('marks the tab that owns the current path; the most specific route wins', () => {
    expect(activeAt('/runs')).toBe('History')
    expect(activeAt('/runs/abc')).toBe('History')
    expect(activeAt('/live')).toBe('Live')
    // `/runs/compare` is under `/runs` too: the longer route wins.
    expect(activeAt('/runs/compare')).toBe('Compare')
    expect(activeAt('/intelligence')).toBe('AI verdicts')
    expect(activeAt('/somewhere-else')).toBeNull()
  })

  it('routeTabActive: exact, prefix and pattern matches; not a sibling with a shared prefix', () => {
    const history = RUNS[0]
    expect(routeTabActive(history, '/runs')).toBe(true)
    expect(routeTabActive(history, '/runs/x/tests/y')).toBe(true)
    expect(routeTabActive(history, '/runsheet')).toBe(false)
    expect(routeTabActive({ to: '/defects?tab=open', label: 'Defects' }, '/defects')).toBe(true)
  })
})
