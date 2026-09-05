import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TimingCell } from './TimingCell'

/** TimingCell renders a <td>, so it needs a table ancestor to be valid DOM. */
const renderCell = (ui: React.ReactNode) =>
  render(<table><tbody><tr>{ui}</tr></tbody></table>)

describe('TimingCell', () => {
  it('merges start, end and duration into one cell', () => {
    renderCell(
      <TimingCell
        started={new Date(2026, 7, 28, 14, 32)}
        end={new Date(2026, 7, 28, 14, 46)}
        durationMs={852_000}
      />,
    )
    expect(screen.getByText('Aug 28, 14:32 → 14:46')).toBeInTheDocument()
    expect(screen.getByText(/14m 12s/)).toBeInTheDocument()
  })

  it('is NEVER breakpoint-hidden', () => {
    // Regression (UX audit issue 1): the old Started/End columns carried
    // `hidden md:table-cell`, so below 768px /intelligence dropped the very
    // column it was sorted by.
    const { container } = renderCell(<TimingCell started={new Date()} end={null} />)
    const cell = container.querySelector('td')
    expect(cell).not.toBeNull()
    // The standalone `hidden` display utility (not `overflow-hidden`).
    expect(cell?.className.split(/\s+/)).not.toContain('hidden')
    expect(cell?.className).not.toMatch(/md:table-cell/)
  })

  it('keeps full ISO instants in the tooltip so no precision is lost', () => {
    const { container } = renderCell(
      <TimingCell started="2026-08-28T14:32:05.000Z" end="2026-08-28T14:46:11.000Z" />,
    )
    const title = container.querySelector('td')?.getAttribute('title') ?? ''
    expect(title).toContain('2026-08-28T14:32:05.000Z')
    expect(title).toContain('2026-08-28T14:46:11.000Z')
  })

  it('reports a live session as running rather than finished', () => {
    const { container } = renderCell(
      <TimingCell started="2026-08-28T14:32:05.000Z" end="2026-08-28T14:40:00.000Z" live />,
    )
    expect(screen.getByText(/live/)).toBeInTheDocument()
    const title = container.querySelector('td')?.getAttribute('title') ?? ''
    expect(title).toContain('Still running')
    expect(title).not.toContain('Ended')
  })

  it('renders an em dash rather than Invalid Date for a missing start', () => {
    renderCell(<TimingCell started={null} end={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('omits the duration line instead of printing a bogus 0ms', () => {
    renderCell(<TimingCell started={new Date(2026, 7, 28, 14, 32)} end={null} durationMs={null} />)
    expect(screen.queryByText(/0ms/)).not.toBeInTheDocument()
  })

  it('stacks a cross-day range onto two lines so it cannot overlap PASS RATE', () => {
    // Regression (UX audit P1-3.1): a run spanning midnight formats to a
    // ~230px string; on one nowrap line it bled LEFT over the next column in
    // the 158px cell. The two ends now sit on separate right-aligned lines.
    renderCell(
      <TimingCell
        started={new Date(2026, 7, 28, 23, 50)}
        end={new Date(2026, 7, 29, 0, 12)}
      />,
    )
    expect(screen.getByText('Aug 28, 23:50 →')).toBeInTheDocument()
    expect(screen.getByText('Aug 29, 00:12')).toBeInTheDocument()
    // The full range is NOT one text node, which is the overflow it caused.
    expect(screen.queryByText('Aug 28, 23:50 → Aug 29, 00:12')).not.toBeInTheDocument()
  })

  it('keeps a same-day range on a single line', () => {
    renderCell(
      <TimingCell started={new Date(2026, 7, 28, 14, 32)} end={new Date(2026, 7, 28, 14, 46)} />,
    )
    expect(screen.getByText('Aug 28, 14:32 → 14:46')).toBeInTheDocument()
    expect(screen.queryByText('Aug 28, 14:32 →')).not.toBeInTheDocument()
  })

  it('clamps overflow on the cell so an over-wide range clips rather than bleeding', () => {
    const { container } = renderCell(
      <TimingCell started={new Date(2026, 7, 28, 23, 50)} end={new Date(2026, 7, 29, 0, 12)} />,
    )
    expect(container.querySelector('td')?.className).toMatch(/overflow-hidden/)
  })
})
