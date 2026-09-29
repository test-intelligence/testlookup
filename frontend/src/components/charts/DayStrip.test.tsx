import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import DayStrip from './DayStrip'
import { DAY_STRIP_FIXTURES } from './__fixtures__/dayStrip'
import { dayWindow, intensityLevel, type DayStripCell } from './dayStrip.model'

const TODAY = '2026-09-28'

/** A Trends-shaped presence window: runs, a mixed day, quiet days, today. */
function presenceCells(): DayStripCell[] {
  const days = dayWindow(14, TODAY)
  return days.map((iso, i) => ({
    key: iso,
    label: `${iso} · ${i % 3 === 0 ? 0 : 5} executions`,
    tone: i % 3 === 0 ? 'none' : i === 4 ? 'mixed' : 'pass',
    marker: i === days.length - 1 ? 'today' : i === 8 ? 'gap-edge' : undefined,
  }))
}

const cellsOf = (container: HTMLElement) => [...container.querySelectorAll<HTMLElement>('[data-day-cell]')]
const tableOf = (container: HTMLElement) => container.querySelector<HTMLTableElement>('[data-day-strip-table]')

describe('DayStrip', () => {
  it('draws one cell per day of the window', () => {
    for (const days of [1, 14, 30, 90]) {
      const cells = dayWindow(days, TODAY).map<DayStripCell>((iso) => ({ key: iso, label: iso, tone: 'none' }))
      const { container, unmount } = render(<DayStrip mode="presence" cells={cells} label="Run cadence" />)
      expect(cellsOf(container)).toHaveLength(days)
      unmount()
    }
  })

  it('is named once, by the aggregate label', () => {
    render(<DayStrip mode="presence" cells={presenceCells()} label="Run cadence: 5 empty days, 9 days with runs" />)
    const images = screen.getAllByRole('img')
    expect(images).toHaveLength(1)
    expect(images[0]).toHaveAccessibleName('Run cadence: 5 empty days, 9 days with runs')
  })

  it('has an sr-only table with one row per cell, captioned by the label', () => {
    const cells = presenceCells()
    const { container } = render(<DayStrip mode="presence" cells={cells} label="Run cadence" />)
    const table = tableOf(container)
    expect(table).not.toBeNull()
    expect(table).toHaveClass('sr-only')
    expect(table).not.toHaveAttribute('aria-hidden')
    expect(within(table as HTMLElement).getByText('Run cadence', { selector: 'caption' })).toBeInTheDocument()
    const bodyRows = (table as HTMLTableElement).tBodies[0].rows
    expect(bodyRows).toHaveLength(cells.length)
    // Row i is cell i: the label, then its state, then its marker.
    expect([...bodyRows[4].cells].map((c) => c.textContent)).toEqual([cells[4].label, 'Runs with failures', ''])
    expect([...bodyRows[13].cells].map((c) => c.textContent)).toEqual([cells[13].label, 'Runs', 'Today'])
    // Reachable by a reader: a real table, found by its role.
    expect(screen.getByRole('table', { name: 'Run cadence' })).toBe(table)
  })

  it('presence: a mixed day carries the failure cue; a clean day does not', () => {
    const { container } = render(<DayStrip mode="presence" cells={presenceCells()} label="Run cadence" />)
    const cells = cellsOf(container)
    expect(cells[4]).toHaveAttribute('data-tone', 'mixed')
    expect(cells[4].querySelector('[data-day-cue="mixed"]')).not.toBeNull()
    expect(cells[5].querySelector('[data-day-cue]')).toBeNull()
    expect(container.querySelectorAll('[data-day-cell] [data-day-cue]')).toHaveLength(1)
    // …and the legend says what the cue means, drawn with the same cue.
    const entry = container.querySelector('[data-legend-tone="mixed"]') as HTMLElement
    expect(entry).toHaveTextContent('Runs with failures')
    expect(entry.querySelector('[data-day-cue="mixed"]')).not.toBeNull()
  })

  it('intensity (Coverage): no mixed cue unless the page marks one — the difference is deliberate', () => {
    const counts = [0, 3, 12, 40, 70]
    const cells = counts.map<DayStripCell>((n, i) => ({
      key: String(i),
      label: `${n} executions`,
      tone: n === 0 ? 'none' : 'pass',
      level: intensityLevel(n),
    }))
    const { container } = render(<DayStrip mode="intensity" cells={cells} label="Run cadence" />)
    expect(container.querySelectorAll('[data-day-cue]')).toHaveLength(0)
    expect(container.querySelector('[data-day-strip-legend]')).toHaveTextContent(/Less.*More/)
  })

  it('intensity: cells take the levels 0 / 1-5 / 6-20 / 21-50 / more than 50', () => {
    const counts = [0, 1, 5, 6, 20, 21, 50, 51]
    const cells = counts.map<DayStripCell>((n, i) => ({
      key: String(i),
      label: `${n} executions`,
      tone: n === 0 ? 'none' : 'pass',
      level: intensityLevel(n),
    }))
    const { container } = render(<DayStrip mode="intensity" cells={cells} label="Run cadence" />)
    expect(cellsOf(container).map((c) => c.getAttribute('data-level'))).toEqual(['0', '1', '1', '2', '2', '3', '3', '4'])
    const states = [...(tableOf(container) as HTMLTableElement).tBodies[0].rows].map((r) => r.cells[1].textContent)
    expect(states[3]).toBe('Activity level 2 of 4 (6-20)')
    expect(states[7]).toBe('Activity level 4 of 4 (more than 50)')
  })

  it('draws the today marker on the last cell and the gap edge where the page put it', () => {
    const { container } = render(<DayStrip mode="presence" cells={presenceCells()} label="Run cadence" />)
    const cells = cellsOf(container)
    const ringed = cells.flatMap((c, i) => (c.style.boxShadow && c.style.boxShadow !== 'none' ? [i] : []))
    expect(ringed).toEqual([13])
    expect(cells[13]).toHaveAttribute('data-marker', 'today')
    expect(cells[13].style.boxShadow).toContain('var(--color-accent)')
    const dashed = cells.flatMap((c, i) => (c.style.outline.includes('dashed') ? [i] : []))
    expect(dashed).toEqual([8])
    expect(cells[8].style.outline).toContain('var(--status-broken)')
    const markers = [...(tableOf(container) as HTMLTableElement).tBodies[0].rows].map((r) => r.cells[2].textContent)
    expect(markers.filter(Boolean)).toEqual(['Last day with runs before a gap', 'Today'])
    expect(markers[8]).toBe('Last day with runs before a gap')
  })

  it('rings the marked cell even when it is not the last one (a padded build strip)', () => {
    // Runs pads a short history with empty cells AFTER the latest build.
    const cells = Array.from({ length: 14 }, (_, i): DayStripCell =>
      i < 9
        ? { key: `#${i}`, label: `#${i}`, tone: 'pass', marker: i === 8 ? 'today' : undefined }
        : { key: `pad-${i}`, label: 'no build', tone: 'none' },
    )
    const { container } = render(<DayStrip mode="status" cells={cells} label="Build velocity" unit="build" />)
    const ringed = cellsOf(container).flatMap((c, i) => (c.style.boxShadow && c.style.boxShadow !== 'none' ? [i] : []))
    expect(ringed).toEqual([8])
  })

  it('renders a hostile cell label as text, never as markup', () => {
    const hostile = '<img src=x onerror="window.__pwned=1"><b>bold</b>'
    const cells: DayStripCell[] = [{ key: 'x', label: hostile, tone: 'fail' }]
    const { container } = render(<DayStrip mode="status" cells={cells} label={`Strip ${hostile}`} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(cellsOf(container)[0]).toHaveAttribute('title', hostile)
    expect(within(tableOf(container) as HTMLElement).getByText(hostile)).toBeInTheDocument()
    expect(screen.getByRole('img')).toHaveAccessibleName(`Strip ${hostile}`)
  })

  it('status: a failed cell carries the failure cue over the whole cell', () => {
    const cells: DayStripCell[] = [
      { key: 'a', label: 'a', tone: 'pass' },
      { key: 'b', label: 'b', tone: 'fail', severity: 0.5 },
      { key: 'c', label: 'c', tone: 'none' },
    ]
    const { container } = render(<DayStrip mode="status" cells={cells} label="Failure timeline" />)
    const drawn = cellsOf(container)
    expect(drawn[1].querySelector('[data-day-cue="fail"]')).not.toBeNull()
    expect(drawn[0].querySelector('[data-day-cue]')).toBeNull()
    expect(drawn[2].querySelector('[data-day-cue]')).toBeNull()
  })

  it('walks the cells from the keyboard through the page announcer, with no live region of its own', () => {
    vi.useFakeTimers()
    try {
      const cells = presenceCells()
      const { container } = render(
        <ChartAnnouncerProvider>
          <DayStrip mode="presence" cells={cells} label="Run cadence: 5 empty days" title="Run cadence" />
        </ChartAnnouncerProvider>,
      )
      const strip = container.querySelector('[data-day-strip]') as HTMLElement
      expect(strip.querySelector('[aria-live]')).toBeNull()
      const surface = screen.getByRole('group', { name: /^Run cadence, day strip, 14 days\./ })
      expect(surface).toHaveAttribute('tabindex', '0')

      fireEvent.focus(surface)
      fireEvent.keyDown(surface, { key: 'End' })
      expect(cellsOf(container)[13]).toHaveAttribute('data-cursor-active')
      const readout = strip.querySelector('[data-chart-readout]') as HTMLElement
      expect(readout).toHaveTextContent(`${cells[13].label}. Runs. Today`)
      expect(readout).toHaveAttribute('aria-hidden', 'true')

      fireEvent.keyDown(surface, { key: 'ArrowLeft' })
      expect(cellsOf(container)[12]).toHaveAttribute('data-cursor-active')
      act(() => {
        vi.runAllTimers()
      })
      // Spoken by the page's announcer, which lives outside the strip.
      const spoken = [...document.querySelectorAll('[aria-live]')].map((n) => n.textContent).join(' ')
      expect(spoken).toContain(`Run cadence: ${cells[12].label}`)
      expect(strip.querySelector('[aria-live]')).toBeNull()

      fireEvent.keyDown(surface, { key: 'Escape' })
      expect(container.querySelector('[data-cursor-active]')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('counts builds when the unit is a build, and takes a fixed cell height', () => {
    const cells = Array.from({ length: 14 }, (_, i): DayStripCell => ({ key: `#${i}`, label: `#${i}`, tone: 'pass' }))
    const { container } = render(
      <DayStrip mode="status" cells={cells} label="Build velocity" unit="build" cellHeight={14} endLabel="Now" />,
    )
    expect(container.querySelector('[data-day-strip-legend]')).toHaveTextContent(/^14 builds ago.*Now$/)
    expect(screen.getByRole('columnheader', { name: 'Build' })).toBeInTheDocument()
    expect(cellsOf(container)[0]).toHaveStyle({ height: '14px' })
    expect(screen.getByRole('group', { name: /14 builds\./ })).toBeInTheDocument()
  })

  it('can drop the legend (the inline run strip has its own counts)', () => {
    const { container } = render(<DayStrip mode="status" cells={presenceCells()} label="Run strip" legend={false} />)
    expect(container.querySelector('[data-day-strip-legend]')).toBeNull()
    expect(tableOf(container)).not.toBeNull()
  })

  it.each(DAY_STRIP_FIXTURES.map((f) => [f.id, f.props] as const))('gallery fixture %s: cells, table rows and name agree', (_, props) => {
    const p = props()
    const { container } = render(<DayStrip {...p} />)
    expect(cellsOf(container)).toHaveLength(p.cells.length)
    expect((tableOf(container) as HTMLTableElement).tBodies[0].rows).toHaveLength(p.cells.length)
    expect(screen.getByRole('img')).toHaveAccessibleName(p.label)
    expect(container.querySelector('img, script, b')).toBeNull()
  })

  it('renders nothing for an empty window', () => {
    const { container } = render(<DayStrip mode="presence" cells={[]} label="Run cadence" />)
    expect(container).toBeEmptyDOMElement()
  })
})
