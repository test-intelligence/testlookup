import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import {
  GALLERY_RELEASE_OPTIONS,
  GALLERY_SUITE_OPTIONS,
} from '@/pages/dev/reportContextFixtures'
import ReportFilterBar, { ALL_PROJECTS_RELEASE_REASON, type ReportFilterBarProps } from './ReportFilterBar'

function props(overrides: Partial<ReportFilterBarProps> = {}): ReportFilterBarProps {
  return {
    releaseOptions: GALLERY_RELEASE_OPTIONS,
    suiteOptions: GALLERY_SUITE_OPTIONS,
    releaseIds: [],
    suiteNames: [],
    windowDays: 30,
    defaultWindowDays: 30,
    releaseCap: 20,
    suiteCap: 50,
    allProjects: false,
    onReleaseChange: vi.fn(),
    onSuiteChange: vi.fn(),
    onWindowChange: vi.fn(),
    ...overrides,
  }
}

const releaseTrigger = () => within(screen.getByTestId('report-filter-release')).getAllByRole('button')[0]

describe('ReportFilterBar', () => {
  it('is a named, focusable group holding Release, Suite and Window', () => {
    render(<ReportFilterBar {...props()} />)
    const group = screen.getByRole('group', { name: 'Report filters' })
    expect(group).toHaveAttribute('tabindex', '-1')
    expect(screen.getByTestId('report-filter-release')).toBeInTheDocument()
    expect(screen.getByTestId('report-filter-suite')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Window' })).toHaveValue('30')
  })

  it('highlights active controls: filled + count badge, and the window when off its default', () => {
    render(<ReportFilterBar {...props({ releaseIds: [GALLERY_RELEASE_OPTIONS[0].value], suiteNames: ['a', 'b'], windowDays: 7 })} />)
    expect(releaseTrigger()).toHaveAttribute('data-active', 'true')
    expect(screen.getByTestId('report-filter-release-count')).toHaveTextContent('1')
    expect(screen.getByTestId('report-filter-suite-count')).toHaveTextContent('2')
    expect(screen.getByRole('combobox', { name: 'Window' })).toHaveAttribute('data-active', 'true')
  })

  it('an inactive window select is not highlighted', () => {
    render(<ReportFilterBar {...props()} />)
    expect(screen.getByRole('combobox', { name: 'Window' })).toHaveAttribute('data-active', 'false')
  })

  it('reaching the cap disables the rest with "Limit reached (20)"', () => {
    function Controlled() {
      const [ids, setIds] = useState(GALLERY_RELEASE_OPTIONS.slice(0, 20).map((o) => o.value))
      return <ReportFilterBar {...props({ releaseIds: ids, onReleaseChange: setIds })} />
    }
    render(<Controlled />)
    fireEvent.click(releaseTrigger())
    const dialog = screen.getByRole('dialog', { name: 'Release options' })
    expect(within(dialog).getByRole('status')).toHaveTextContent('Limit reached (20)')
    fireEvent.click(within(dialog).getByRole('button', { name: /Show more/ }))
    const extra = within(dialog).getByRole('option', { name: /^2026\.21/ })
    expect(extra).toHaveAttribute('aria-disabled', 'true')
  })

  it('All projects: the release control is disabled with a VISIBLE reason; suites stay usable', () => {
    const onReleaseChange = vi.fn()
    render(<ReportFilterBar {...props({ allProjects: true, releaseIds: ['x'], onReleaseChange })} />)
    expect(releaseTrigger()).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText(ALL_PROJECTS_RELEASE_REASON)).toBeVisible()
    expect(releaseTrigger()).toHaveAccessibleDescription(ALL_PROJECTS_RELEASE_REASON)
    // A selection cannot show through in All-Projects mode.
    expect(releaseTrigger()).toHaveAttribute('data-active', 'false')
    fireEvent.click(releaseTrigger())
    expect(screen.queryByRole('dialog')).toBeNull()
    const suite = within(screen.getByTestId('report-filter-suite')).getAllByRole('button')[0]
    expect(suite).not.toHaveAttribute('aria-disabled')
  })

  it('changing the window reports the number of days', () => {
    const onWindowChange = vi.fn()
    render(<ReportFilterBar {...props({ onWindowChange })} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Window' }), { target: { value: '14' } })
    expect(onWindowChange).toHaveBeenCalledWith(14)
  })

  it('a stored window outside the options is still shown, not silently replaced', () => {
    render(<ReportFilterBar {...props({ windowDays: 60 })} />)
    expect(screen.getByRole('combobox', { name: 'Window' })).toHaveValue('60')
  })

  it('names dropped values in a dismissible notice (release ids resolved to names)', () => {
    const onDismissNotice = vi.fn()
    render(
      <ReportFilterBar
        {...props({
          droppedNotice: [
            { dimension: 'release', values: [GALLERY_RELEASE_OPTIONS[2].value, 'gone'], reason: 'they belong to another project' },
            { dimension: 'suite', values: ['payments'], reason: 'no runs in the new project' },
          ],
          onDismissNotice,
        })}
      />,
    )
    const notice = document.querySelector('[data-dropped-notice]') as HTMLElement
    expect(within(notice).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Removed releases 2026.03, gone from the filter: they belong to another project',
      'Removed suite payments from the filter: no runs in the new project',
    ])
    // The text is written into the always-mounted live region.
    expect(document.querySelector('[data-dropped-notice-live]')?.textContent).toBe(
      'Removed releases 2026.03, gone from the filter: they belong to another project. Removed suite payments from the filter: no runs in the new project',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss notice' }))
    expect(onDismissNotice).toHaveBeenCalledOnce()
  })

  // Fix round B (a11y m3): a live region inserted together with its text is
  // often not announced; the region is there, empty, BEFORE any notice.
  it('keeps ONE empty live region mounted without a notice, and writes the notice into it', () => {
    const { rerender } = render(<ReportFilterBar {...props()} />)
    const live = document.querySelector('[data-dropped-notice-live]') as HTMLElement
    expect(live).toHaveAttribute('role', 'status')
    expect(live.textContent).toBe('')
    rerender(
      <ReportFilterBar {...props({ droppedNotice: [{ dimension: 'suite', values: ['cart'], reason: 'over the cap' }] })} />,
    )
    // The SAME node, now holding the text.
    expect(document.querySelector('[data-dropped-notice-live]')).toBe(live)
    expect(live.textContent).toBe('Removed suite cart from the filter: over the cap')
    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1)
  })

  // Fix round B (a11y M4): the dismiss button leaves with the notice; focus
  // must go somewhere meaningful, never to <body>.
  it('dismissing the notice moves focus to the Release trigger', () => {
    function Controlled() {
      const [notice, setNotice] = useState([{ dimension: 'suite' as const, values: ['cart'], reason: 'over the cap' }])
      return <ReportFilterBar {...props({ droppedNotice: notice, onDismissNotice: () => setNotice([]) })} />
    }
    render(<Controlled />)
    const dismiss = screen.getByRole('button', { name: 'Dismiss notice' })
    dismiss.focus()
    fireEvent.click(dismiss)
    expect(screen.queryByRole('button', { name: 'Dismiss notice' })).toBeNull()
    expect(releaseTrigger()).toHaveFocus()
    expect(document.activeElement).not.toBe(document.body)
  })

  it('with the Release control disabled (All projects), dismissing focuses the bar', () => {
    function Controlled() {
      const [notice, setNotice] = useState([{ dimension: 'suite' as const, values: ['cart'], reason: 'over the cap' }])
      return (
        <ReportFilterBar {...props({ allProjects: true, droppedNotice: notice, onDismissNotice: () => setNotice([]) })} />
      )
    }
    render(<Controlled />)
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss notice' }))
    expect(screen.getByRole('group', { name: 'Report filters' })).toHaveFocus()
  })

  it('the window select has a VISIBLE label, and the Release trigger is findable by data-report-filter', () => {
    render(<ReportFilterBar {...props()} />)
    const label = screen.getByText('Window', { selector: 'label' })
    expect(label).not.toHaveClass('sr-only')
    expect(document.querySelector('[data-report-filter="release"]')).toBe(releaseTrigger())
    expect(document.querySelector('button[data-report-filter="suite"]')).not.toBeNull()
  })

  it('a stored 0 ("All time") reads "All time", never "Last 0 days"', () => {
    render(<ReportFilterBar {...props({ windowDays: 0 })} />)
    expect(screen.getByRole('combobox', { name: 'Window' })).toHaveDisplayValue('All time')
  })
})
