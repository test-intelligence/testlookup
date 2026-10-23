import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { readSourceFile } from '@/test/readSourceFile'
import KpiStrip from './KpiStrip'
import MetricCard from './MetricCard'
import StatusBanner from './StatusBanner'

describe('StatusBanner', () => {
  it('states the verdict in words, its facts, and one link', () => {
    render(
      <MemoryRouter>
        <StatusBanner
          state="no_go"
          title="Release 2.4"
          facts={[
            { label: 'pass', value: '83.4%' },
            { label: 'new failures', value: 12 },
          ]}
          action={{ label: 'Open gate', href: '/release-gate' }}
        />
      </MemoryRouter>,
    )
    // W29a UIX-03: the one decision vocabulary, sentence case ("No-Go", was "NO-GO").
    expect(screen.getByText('No-Go')).toBeInTheDocument()
    expect(screen.getByText('83.4%')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open gate/ })).toHaveAttribute('href', '/release-gate')
  })

  it('takes its own pill words when the state word would misname the verdict; the hue stays that of the state', () => {
    const { container } = render(
      <MemoryRouter>
        <StatusBanner state="fail" pillLabel="AT RISK" title="Library health 41/100" facts={[{ label: 'Cases', value: 3 }]} />
      </MemoryRouter>,
    )
    const pill = container.querySelector('[data-banner-pill]') as HTMLElement
    expect(pill).toHaveTextContent(/^AT RISK$/)
    expect(container.querySelector('[data-status-banner]')).toHaveAttribute('data-status-banner', 'fail')
    // The pill's TEXT is the state's hue mixed toward the body text (the -ink
    // rule, W29a UIX-03): the bare hue missed 4.5:1 on its own tint.
    expect(pill.style.color).toBe('color-mix(in srgb, var(--status-failed) 75%, var(--color-text))')
  })

  it.each([
    ['go', 'Go', 'var(--status-passed)'],
    ['ok', 'OK', 'var(--status-passed)'],
    ['conditional', 'Conditional', 'var(--status-broken)'],
    ['warn', 'Warning', 'var(--status-broken)'],
    ['no_go', 'No-Go', 'var(--status-failed)'],
    ['fail', 'Failing', 'var(--status-failed)'],
    ['pending', 'Pending', 'var(--color-text-muted)'],
  ] as const)('the %s pill says "%s" in sentence case, its text mixed toward the body text', (state, word, hue) => {
    const { container } = render(<StatusBanner state={state} facts={[{ label: 'pass', value: '1%' }]} />)
    const pill = container.querySelector('[data-banner-pill]') as HTMLElement
    expect(pill).toHaveTextContent(new RegExp(`^${word}$`))
    expect(pill.className).not.toMatch(/uppercase/)
    expect(pill.style.color).toBe(`color-mix(in srgb, ${hue} 75%, var(--color-text))`)
    expect(pill.style.background).toBe(`color-mix(in srgb, ${hue} 14%, transparent)`)
    expect((pill.firstElementChild as HTMLElement).style.background).toBe(hue)
  })

  // W29b fix-kpi: on /releases at 1280 px the 12 px pill pushed "Open 2026.11 →" to a second line (+15 px). A
  // string title gives way first: its flex basis is 17em (the row breaks as if it were at most that wide), it grows
  // back to its own width (`max-w-max`) where the line has room, and its whole text is the hover text.
  it('a string title truncates before the link wraps; its whole text is the hover text', () => {
    const headline = '2026.11 is blocked — was due Wednesday'
    const { container } = render(
      <StatusBanner state="no_go" title={headline} facts={[{ label: 'Blocked', value: 1 }]} action={{ label: 'Open 2026.11', onClick: () => {} }} />,
    )
    const title = container.querySelector('[data-banner-title]') as HTMLElement
    expect(title).toHaveTextContent(headline)
    expect(title).toHaveAttribute('title', headline)
    expect(title).toHaveClass('min-w-0', 'max-w-max', 'flex-[1_1_17em]', 'truncate')
    // The pill, then the title, then the facts and the link: the order the row wraps in is unchanged.
    expect(Array.from((container.querySelector('[data-status-banner]') as HTMLElement).children).indexOf(title)).toBe(1)
  })

  it('a node title is drawn as the page built it (no basis, no truncation, no hover text)', () => {
    const { container } = render(<StatusBanner state="warn" title={<span>Flaky · 2 tests</span>} facts={[{ label: 'pass', value: '1%' }]} />)
    expect(container.querySelector('[data-banner-title]')).toBeNull()
    const title = screen.getByText('Flaky · 2 tests').parentElement as HTMLElement
    expect(title).toHaveClass('font-medium')
    expect(title).not.toHaveClass('truncate')
    expect(title).not.toHaveAttribute('title')
  })

  it('shows at most four facts', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const view = render(
      <StatusBanner state="ok" facts={[1, 2, 3, 4, 5].map((n) => ({ label: `f${n}`, value: n }))} />,
    )
    expect(view.container.querySelectorAll('[data-banner-fact]')).toHaveLength(4)
    warn.mockRestore()
  })
})

describe('KpiStrip + compact MetricCard', () => {
  it('lays out at most five compact tiles in one row', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const view = render(
      <KpiStrip>
        {[1, 2, 3, 4, 5, 6].map((n) => (
          <MetricCard key={n} title={`KPI ${n}`} metric={{ value: n }} />
        ))}
      </KpiStrip>,
    )
    const strip = view.container.querySelector('[data-kpi-strip]') as HTMLElement
    expect(strip.querySelectorAll('[data-metric-card="compact"]')).toHaveLength(5)
    // Fits the width (one row of five on a desktop page, fewer columns when
    // presentation-sized tiles cannot fit), never a fixed count that overflows.
    // W29b fix-kpi: the floor is the `--kpi-tile-min` token (10rem at the desk), which presentation mode raises.
    expect(strip.style.gridTemplateColumns).toBe('repeat(auto-fit, minmax(var(--kpi-tile-min, 10rem), 1fr))')
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  // W29b fix-kpi: the tile's label and change line are 16 px in presentation mode (the `.eyebrow` / `text-xs`
  // tokens), and five 186 px tiles at 1280 px cut "TOTAL EXECU…" and "Down … (worse)" on CI's fonts. The strip's
  // floor follows the room size: 10rem at the desk (five in a row at 1280), 16.5rem in the room (three at 1280).
  it('the strip floor is 10rem at the desk and grows with the room-sized label in presentation mode', async () => {
    const css = await readSourceFile('src/index.css')
    const declared = (selector: string) => {
      const start = css.indexOf(selector)
      expect(start, selector).toBeGreaterThanOrEqual(0)
      return /--kpi-tile-min:\s*([\d.]+)rem;/.exec(css.slice(start, css.indexOf('}', start)))?.[1]
    }
    expect(declared(':root {\n    --text-display-sm')).toBe('10')
    const room = Number(declared('[data-presentation="on"] {'))
    // "DUPLICATES AVOIDED" at 16 px in the label style is ~224 px on DejaVu Sans; 32 px of padding, 4 px spare.
    expect(room * 16).toBeGreaterThanOrEqual(224 + 32 + 4)
    // ...and five tiles still fit one row of a 1920 px page in the room (a 1616 px strip, 12 px gaps).
    expect(5 * room * 16 + 4 * 12).toBeLessThanOrEqual(1616)
  })

  it('a compact card keeps the change words and judgement, on one line', () => {
    render(
      <MetricCard
        title="Pass rate"
        metric={{ value: '91.2%', trend_direction: 'down', trend_text: '2.1 pp vs previous period' }}
      />,
    )
    // The presentation-aware stat token (24 px at the desk, 40 px in the room),
    // never a fixed `text-2xl` that presentation mode cannot raise.
    expect(screen.getByText('91.2%').className).toContain('text-[length:var(--text-stat-lg)]')
    expect(screen.getByText('91.2%').className).not.toContain('text-2xl')
    // Separate spans, so a narrow tile truncates the change's context and never "(worse)" (nor, since W29b
    // fix-kpi, the figure: "Down 2.1 pp" is its own span that does not shrink).
    expect(screen.getByTitle('Down 2.1 pp vs previous period (worse)')).toHaveTextContent(/^Down 2\.1 pp vs previous period\(worse\)$/)
    expect(screen.getByText('Down 2.1 pp')).toHaveAttribute('data-change-head')
    expect(screen.getByText('(worse)')).toBeInTheDocument()
    expect(screen.getByTitle('Down 2.1 pp vs previous period (worse)')).toHaveAttribute('data-metric-trend', 'down')
  })
})
