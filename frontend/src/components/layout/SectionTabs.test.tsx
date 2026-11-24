import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SectionTabs from './SectionTabs'

const perms = vi.hoisted(() => ({ canAccessManagement: true, canReadReleases: true }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => perms }))
beforeEach(() => {
  perms.canAccessManagement = true
  perms.canReadReleases = true
})

function tabsAt(path: string): { labels: string[]; active: string | null } {
  render(
    <MemoryRouter initialEntries={[path]}>
      <SectionTabs />
    </MemoryRouter>,
  )
  const links = [...document.querySelectorAll('[data-route-tab]')]
  return {
    labels: links.map((a) => a.textContent ?? ''),
    active: document.querySelector('[data-route-tab][aria-current="page"]')?.textContent ?? null,
  }
}

describe('SectionTabs (UX redesign P1)', () => {
  it.each([
    ['/runs', ['History', 'Live', 'Compare'], 'History'],
    ['/runs/r1', ['History', 'Live', 'Compare'], 'History'],
    ['/runs/compare', ['History', 'Live', 'Compare'], 'Compare'],
    ['/live', ['History', 'Live', 'Compare'], 'Live'],
    ['/coverage', ['Trends', 'Coverage', 'Explorer'], 'Coverage'],
    ['/deep-investigate/r1', ['Failures', 'Defects', 'Root cause (AI)'], 'Root cause (AI)'],
    ['/value-metrics', ['Summary', 'Value'], 'Value'],
    ['/policies', ['Releases', 'Gate policies'], 'Gate policies'],
  ])('%s: the section tabs, with the page active', (path, labels, active) => {
    expect(tabsAt(path)).toEqual({ labels, active })
  })

  it.each(['/overview', '/suites', '/coverage/suite', '/release-gate', '/search', '/settings/ai', '/docs', '/flaky', '/my-failures', '/reviews'])(
    '%s: a single-page place, or no place: no tabs',
    (path) => {
      render(
        <MemoryRouter initialEntries={[path]}>
          <SectionTabs />
        </MemoryRouter>,
      )
      expect(screen.queryByRole('navigation')).toBeNull()
    },
  )

  it("a QA engineer reads Releases but is not offered Gate policies, so one tab is no tabs (D-118)", () => {
    perms.canAccessManagement = false
    expect(tabsAt('/releases').labels).toEqual([])
  })

  it('a viewer or tester is shown no Releases tabs (D-118)', () => {
    perms.canAccessManagement = false
    perms.canReadReleases = false
    expect(tabsAt('/releases').labels).toEqual([])
    expect(tabsAt('/policies').labels).toEqual([])
  })
})
