import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ReportContextPage from './ReportContextPage'
import { GALLERY_CASES } from './reportContextFixtures'

const renderAt = (search = '') =>
  render(
    <MemoryRouter initialEntries={[`/__report-context${search}`]}>
      <ReportContextPage />
    </MemoryRouter>,
  )

describe('/__report-context gallery', () => {
  it('renders every edge case, each with one chrome', () => {
    renderAt()
    expect(screen.getByRole('heading', { level: 1, name: 'Report context (dev only)' })).toBeInTheDocument()
    const cases = Array.from(document.querySelectorAll('[data-report-case]')).map((el) => el.getAttribute('data-report-case'))
    expect(cases).toEqual(GALLERY_CASES.map((c) => c.id))
    for (const section of document.querySelectorAll('[data-report-case]')) {
      expect(section.querySelectorAll('[data-report-chrome]')).toHaveLength(1)
      expect(section.querySelectorAll('[data-report-context-header]')).toHaveLength(1)
    }
    // One page-level announcer, not one per case.
    expect(document.querySelectorAll('[data-chart-announcer="polite"]')).toHaveLength(1)
    expect(document.querySelectorAll('[data-filtered-summary-live]')).toHaveLength(0)
  })

  it('covers the named edge cases', () => {
    expect(GALLERY_CASES.map((c) => c.id)).toEqual(
      expect.arrayContaining([
        'unfiltered',
        'filtered',
        'many-suites',
        'many-chips',
        'all-projects',
        'unmeasured',
        'huge-counts',
        'totals-missing',
        'ignored-filter',
        'archived-unattributed',
      ]),
    )
  })

  it('applies a valid ?theme and ignores a bogus one', () => {
    const { unmount } = renderAt('?theme=lab')
    expect(document.documentElement.getAttribute('data-theme')).toBe('lab')
    unmount()
    renderAt('?theme=%3Cscript%3E')
    expect(screen.getByTestId('report-context-gallery')).toHaveAttribute('data-gallery-theme', 'default')
  })
})
