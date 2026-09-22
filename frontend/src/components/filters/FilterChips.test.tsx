import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useRef, useState } from 'react'
import FilterChips, { COMBINE_RULE_TEXT, FILTER_CHIP_LIMIT, NO_FILTERS_TEXT, type ReleaseChip } from './FilterChips'

function Harness({
  releases: initialReleases = [
    { id: 'r1', label: 'R1' },
    { id: 'r2', label: 'R2' },
  ],
  suites: initialSuites = ['payments'],
  windowDays: initialWindow = 30,
  ignored = [],
}: {
  releases?: ReleaseChip[]
  suites?: string[]
  windowDays?: number
  ignored?: { dimension: 'release' | 'suite' | 'window'; reason: string }[]
}) {
  const [releases, setReleases] = useState(initialReleases)
  const [suites, setSuites] = useState(initialSuites)
  const [windowDays, setWindowDays] = useState(initialWindow)
  const barRef = useRef<HTMLDivElement>(null)
  return (
    <div>
      <div ref={barRef} tabIndex={-1} data-testid="bar">
        bar
      </div>
      <FilterChips
        releases={releases}
        suites={suites}
        windowDays={windowDays}
        defaultWindowDays={30}
        ignoredFilters={ignored}
        onRemoveRelease={(id) => setReleases((r) => r.filter((x) => x.id !== id))}
        onRemoveSuite={(name) => setSuites((s) => s.filter((x) => x !== name))}
        onResetWindow={() => setWindowDays(30)}
        onClearAll={() => {
          setReleases([])
          setSuites([])
          setWindowDays(30)
        }}
        fallbackFocusRef={barRef}
      />
    </div>
  )
}

const chipTexts = () =>
  Array.from(document.querySelectorAll('[data-filter-chips] [data-chip]')).map((c) => c.textContent)

/** Keyboard activation: focus the button, press Enter (a native button clicks on Enter). */
function pressRemove(name: string) {
  const button = screen.getByRole('button', { name })
  button.focus()
  expect(document.activeElement).toBe(button)
  fireEvent.keyDown(button, { key: 'Enter' })
  fireEvent.click(button)
}

describe('FilterChips', () => {
  it('shows one chip per value, the window chip and "Clear all"', () => {
    render(<Harness />)
    expect(chipTexts()).toEqual(['Release:R1', 'Release:R2', 'Suite:payments', 'Window:last 30 days'])
    expect(screen.getByRole('button', { name: 'Remove filter Release R2' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear all' })).toBeInTheDocument()
  })

  it('removing a chip by keyboard moves focus to the NEXT chip', () => {
    render(<Harness />)
    pressRemove('Remove filter Release R2')
    expect(chipTexts()).toEqual(['Release:R1', 'Suite:payments', 'Window:last 30 days'])
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Remove filter Suite payments' }))
  })

  it('removing the last chip moves focus to "Clear all", then to the bar', () => {
    render(<Harness releases={[{ id: 'r1', label: 'R1' }]} suites={[]} windowDays={14} />)
    pressRemove('Remove filter Release R1')
    // The window is still off its default, so "Clear all" remains.
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Clear all' }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(screen.queryByRole('button', { name: 'Clear all' })).toBeNull()
    expect(document.activeElement).toBe(screen.getByTestId('bar'))
  })

  it('with nothing after it, removal falls to the bar (never <body>)', () => {
    render(<Harness releases={[]} suites={['payments']} />)
    pressRemove('Remove filter Suite payments')
    expect(document.activeElement).toBe(screen.getByTestId('bar'))
    expect(document.activeElement).not.toBe(document.body)
  })

  it('the window chip is not removable at its default and resets (not removes) off it', () => {
    const { unmount } = render(<Harness releases={[]} suites={[]} />)
    expect(screen.queryByRole('button', { name: /Window/ })).toBeNull()
    unmount()
    render(<Harness releases={[]} suites={[]} windowDays={7} />)
    expect(chipTexts()).toEqual(['Window:last 7 days'])
    pressRemove('Reset filter Window to last 30 days')
    expect(chipTexts()).toEqual(['Window:last 30 days'])
    expect(document.activeElement).toBe(screen.getByTestId('bar'))
  })

  it('says so when no release or suite filter is applied', () => {
    render(<Harness releases={[]} suites={[]} />)
    expect(screen.getByText(NO_FILTERS_TEXT)).toBeInTheDocument()
    expect(NO_FILTERS_TEXT).toBe('No filters applied — showing all releases and suites')
  })

  it(`collapses more than ${FILTER_CHIP_LIMIT} chips into "+N more", expandable`, () => {
    const releases = Array.from({ length: 7 }, (_, i) => ({ id: `r${i}`, label: `R${i}` }))
    render(<Harness releases={releases} suites={['a', 'b', 'c']} />)
    const list = screen.getByRole('list', { name: 'Active filters' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(FILTER_CHIP_LIMIT)
    const more = screen.getByRole('button', { name: '+2 more' })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(more)
    expect(within(list).getAllByRole('listitem')).toHaveLength(10)
    expect(screen.getByRole('button', { name: 'Show fewer' })).toHaveAttribute('aria-expanded', 'true')
  })

  // Fix round B (a11y m5): the revealed chips sit BEFORE the button in the
  // DOM; left on the button, a keyboard user would never meet them.
  it('"+N more" moves focus to the first chip it reveals', () => {
    const releases = Array.from({ length: 7 }, (_, i) => ({ id: `r${i}`, label: `R${i}` }))
    render(<Harness releases={releases} suites={['a', 'b', 'c']} />)
    const more = screen.getByRole('button', { name: '+2 more' })
    more.focus()
    fireEvent.click(more)
    expect(screen.getByRole('button', { name: 'Remove filter Suite b' })).toHaveFocus()
    // Collapsing again does not move focus off the toggle.
    const fewer = screen.getByRole('button', { name: 'Show fewer' })
    fewer.focus()
    fireEvent.click(fewer)
    expect(screen.getByRole('button', { name: '+2 more' })).toHaveFocus()
  })

  it('a stored 0 window reads "all time", never "last 0 days"', () => {
    render(<Harness windowDays={0} />)
    expect(document.querySelector('[data-window-chip]')?.textContent).toContain('all time')
  })

  it('exactly eight chips do not collapse', () => {
    const releases = Array.from({ length: 8 }, (_, i) => ({ id: `r${i}`, label: `R${i}` }))
    render(<Harness releases={releases} suites={[]} />)
    expect(screen.queryByRole('button', { name: /more$/ })).toBeNull()
  })

  it('marks every chip of an ignored dimension with a warning naming the reason', () => {
    render(<Harness ignored={[{ dimension: 'release', reason: 'Defects are project-wide' }]} />)
    expect(screen.getAllByRole('img', { name: 'Warning: Defects are project-wide' })).toHaveLength(2)
    const suiteChip = screen.getByRole('button', { name: 'Remove filter Suite payments' }).closest('[data-chip]')
    expect(suiteChip?.querySelector('[role="img"]')).toBeNull()
  })

  it('states AND-across / OR-within on the bar, reachable by keyboard', () => {
    render(<Harness />)
    const info = screen.getByRole('button', { name: 'How filters combine' })
    expect(info).toHaveAttribute('title', COMBINE_RULE_TEXT)
    expect(info).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(info)
    expect(screen.getByText(COMBINE_RULE_TEXT)).toBeVisible()
    expect(COMBINE_RULE_TEXT).toMatch(/AND across dimensions and OR within/)
  })

  it('a removal the parent refuses keeps focus where it is', () => {
    const refuse = vi.fn()
    render(
      <FilterChips
        releases={[{ id: 'r1', label: 'R1' }]}
        suites={['s']}
        windowDays={30}
        defaultWindowDays={30}
        onRemoveRelease={refuse}
        onRemoveSuite={vi.fn()}
        onResetWindow={vi.fn()}
        onClearAll={vi.fn()}
      />,
    )
    pressRemove('Remove filter Release R1')
    expect(refuse).toHaveBeenCalledWith('r1')
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Remove filter Release R1' }))
  })
})
