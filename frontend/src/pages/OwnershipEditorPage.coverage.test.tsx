import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import OwnershipEditorPage from './OwnershipEditorPage'

/**
 * Regression guard: the coverage badge must not render an undefined ratio
 * as "0%".
 *
 * The backend computed `matched / located`, and fell back to `0.0` when
 * `located` was 0. `locate_failure_path` leaves Java deliberately unlocated,
 * so an all-Java project — a normal TestNG/JUnit customer — had every
 * failure unlocatable and saw a permanent "Coverage 0%" that no number of
 * ownership rules could move. The tooltip beside it said "0/0 matched".
 *
 * `coverage_pct` is now `number | null`; null renders "n/a".
 *
 * The badge only renders when `path_rules > 0`, which is why every fixture
 * here sets it.
 */

const coverageMock = vi.fn()

vi.mock('@/hooks/useOwnershipRules', () => ({
  useOwnershipRules: () => ({
    rules: [], isLoading: false, isError: false, refresh: vi.fn(),
  }),
  useCodeownersCoverage: () => coverageMock(),
}))
vi.mock('@/hooks/useTeamChannels', () => ({
  useTeamChannels: () => ({ channels: [], isError: false, refresh: vi.fn() }),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: { activeProjectId: string }) => unknown) =>
    selector({ activeProjectId: 'proj-1' })),
}))
vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <OwnershipEditorPage />
    </MemoryRouter>,
  )
}

function findBadge(): HTMLSpanElement | null {
  // The badge is the element whose text starts with "Coverage".
  const els = Array.from(document.querySelectorAll('span'))
  return els.find(e => (e.textContent || '').trim().startsWith('Coverage')) || null
}

/** Same lookup, but narrows — the repo forbids non-null assertions. */
function badge(): HTMLSpanElement {
  const el = findBadge()
  if (!el) throw new Error('coverage badge not rendered')
  return el
}

describe('OwnershipEditorPage coverage badge', () => {
  it('renders n/a — not 0% — when nothing could be located', () => {
    coverageMock.mockReturnValue({
      coverage: {
        path_rules: 1, codeowners_rules: 0, sampled: 3, located: 0,
        matched: 0, coverage_pct: null, lookback_days: 30,
      },
      refresh: vi.fn(),
    })
    renderPage()

    const el = badge()
    expect(el.textContent).toContain('n/a')
    expect(el.textContent).not.toContain('0%')
    // The tooltip must explain WHY, not repeat a meaningless 0/0.
    expect(el.getAttribute('title')).toContain('Not measurable')
  })

  it('still renders a real 0% when failures were located and none matched', () => {
    coverageMock.mockReturnValue({
      coverage: {
        path_rules: 1, codeowners_rules: 0, sampled: 5, located: 4,
        matched: 0, coverage_pct: 0, lookback_days: 30,
      },
      refresh: vi.fn(),
    })
    renderPage()

    const el = badge()
    expect(el.textContent).toContain('0%')
    expect(el.textContent).not.toContain('n/a')
    expect(el.getAttribute('title')).toContain('0/4')
  })

  it('renders a real ratio unchanged', () => {
    coverageMock.mockReturnValue({
      coverage: {
        path_rules: 4, codeowners_rules: 3, sampled: 20, located: 10,
        matched: 7, coverage_pct: 70, lookback_days: 14,
      },
      refresh: vi.fn(),
    })
    renderPage()

    expect(badge().textContent).toContain('70%')
  })

  it('hides the badge entirely when there are no path rules', () => {
    coverageMock.mockReturnValue({
      coverage: {
        path_rules: 0, codeowners_rules: 0, sampled: 0, located: 0,
        matched: 0, coverage_pct: null, lookback_days: 30,
      },
      refresh: vi.fn(),
    })
    renderPage()

    expect(findBadge()).toBeNull()
  })
})
