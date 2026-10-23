/**
 * MetricCard — the ONE KPI tile (W29b UIX-09). The full variant (its 30 px value, the accent map and the
 * `bg-white/5` icon tile) had no production caller and is gone; its cases are asserted here on the one tile (the
 * change line's words, the (better) / (worse) judgement, the decorative icon, no live region). The four
 * `compact` cases at the end are W29a's; the deprecated `compact` / `icon` / `accentColor` props they passed are
 * gone (W29b §6.4 step 4), so they render the default md tile they always measured.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import MetricCard from './MetricCard'

describe('MetricCard', () => {
  it('renders title and metric value', () => {
    render(<MetricCard title="Pass Rate" metric={{ value: '98.2%' }} />)

    expect(screen.getByText('Pass Rate')).toBeInTheDocument()
    expect(screen.getByText('98.2%')).toBeInTheDocument()
  })

  it('shows placeholder when metric is missing', () => {
    render(<MetricCard title="Defects" />)

    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('renders loading skeleton and hides trend while loading', () => {
    render(<MetricCard title="Stability" metric={{ value: 100, trend: 12, trend_direction: 'up' }} loading />)

    expect(screen.queryByText('100')).not.toBeInTheDocument()
    expect(screen.queryByText(/12% vs prev period/)).not.toBeInTheDocument()
    expect(screen.getByText('Loading')).toBeInTheDocument()
  })

  it('renders absolute trend value regardless of sign', () => {
    const { container } = render(<MetricCard title="Flaky" metric={{ value: 7, trend: -6, trend_direction: 'down' }} />)

    expect(container.querySelector('[data-metric-trend]')).toHaveTextContent(/^Down 6% vs prev period\(worse\)$/)
    expect(screen.getByText('Down 6%')).toHaveAttribute('data-change-head')
  })

  /** The change line's own class (it carries the judgement's hue, which paints the icon). */
  function trendRowClass(container: HTMLElement): string {
    const row = container.querySelector('[data-metric-trend]')
    if (row === null) throw new Error('no change line')
    return row.getAttribute('class') ?? ''
  }

  it('paints a rising trend with the success hue by default (up is good)', () => {
    const { container } = render(<MetricCard title="Pass Rate" metric={{ value: '98%', trend: 3, trend_direction: 'up' }} />)

    expect(trendRowClass(container)).toContain('--ui-success')
  })

  it('paints a falling trend with the success hue when positiveDirection is down (Failed/Flaky/Duration)', () => {
    // Audit 1.5: "Failed tests trending down" used to render red — the trend
    // color keyed on direction alone, not on whether the direction was good.
    const { container } = render(
      <MetricCard title="Failed" metric={{ value: 4, trend: -20, trend_direction: 'down' }} positiveDirection="down" />,
    )

    expect(trendRowClass(container)).toContain('--ui-success')
  })

  it('paints a rising trend with the danger hue when positiveDirection is down', () => {
    const { container } = render(
      <MetricCard title="Failed" metric={{ value: 9, trend: 25, trend_direction: 'up' }} positiveDirection="down" />,
    )

    expect(trendRowClass(container)).toContain('--ui-danger')
  })

  // Fix round B (a11y M1): direction and good/bad are WORDS, not only the
  // icon's shape and colour (WCAG 1.4.1 use of colour, 1.1.1 non-text).
  it('states the direction and whether it is better or worse in text; the icon is decoration', () => {
    const { container } = render(
      <MetricCard title="Failed" metric={{ value: 9, trend: 25, trend_direction: 'up' }} positiveDirection="down" />,
    )
    const row = container.querySelector('[data-metric-trend]') as HTMLElement
    expect(row.textContent).toBe('Up 25% vs prev period(worse)')
    expect(row).toHaveAttribute('title', 'Up 25% vs prev period (worse)')
    const svg = row.querySelector('svg') as SVGElement
    expect(svg).toHaveAttribute('aria-hidden', 'true')
    expect(svg.getAttribute('class')).toContain('shrink-0')
    // The words take a text token: the hue never paints text.
    for (const span of row.querySelectorAll('span')) expect(span.className).toContain('text-[var(--color-text-secondary)]')
  })

  it('takes the caller change text, e.g. percentage points', () => {
    const { container } = render(
      <MetricCard title="Pass rate" metric={{ value: '91.2%', trend: 2.9, trend_direction: 'down', trend_text: '2.9 pp vs previous period' }} />,
    )
    expect(container.querySelector('[data-metric-trend]')?.textContent).toBe('Down 2.9 pp vs previous period(worse)')
  })

  it('"none": a reason with no number, no direction word, no judgement', () => {
    const { container } = render(
      <MetricCard title="Broken" metric={{ value: 2, trend: null, trend_direction: 'none', trend_text: 'New: 0 in the previous period' }} />,
    )
    const row = container.querySelector('[data-metric-trend]') as HTMLElement
    expect(row.textContent).toBe('New: 0 in the previous period')
    expect(row.querySelector('svg')).toBeNull()
  })

  it('flat is "No change", never judged', () => {
    const { container } = render(<MetricCard title="Runs" metric={{ value: 5, trend: 0, trend_direction: 'flat' }} />)
    expect(container.querySelector('[data-metric-trend]')?.textContent).toBe('No change 0% vs prev period')
    expect(container.querySelector('[data-trend-judgement]')).toBeNull()
    expect(trendRowClass(container)).toContain('text-[var(--color-text-muted)]')
  })

  it('does not announce itself as a live region (audit 1.5: no per-card aria-live)', () => {
    // A dashboard renders 6-8 cards polling via SWR; per-card role="status"
    // re-announced every value to screen readers on every refresh.
    const { container } = render(<MetricCard title="Pass Rate" metric={{ value: '98.2%' }} />)

    expect(container.querySelector('[aria-live]')).toBeNull()
    expect(container.querySelector('[role="status"]')).toBeNull()
  })

  describe('the one tile (W29b UIX-09)', () => {
    it('md (the default) is the strip tile: data-metric-card="compact", the .card surface, the stat-lg value', () => {
      const { container } = render(<MetricCard title="Runs" metric={{ value: 412 }} />)
      const root = container.firstElementChild as HTMLElement
      expect(root).toHaveAttribute('data-metric-card', 'compact')
      expect(root.className.split(/\s+/)).toEqual(expect.arrayContaining(['card', 'min-w-0', 'h-full']))
      const value = screen.getByText('412')
      expect(value.tagName).toBe('P')
      expect(value.className.split(/\s+/)).toEqual(
        expect.arrayContaining(['tabular-nums', 'text-[length:var(--text-stat-lg)]', 'font-bold', 'text-[var(--color-text)]']),
      )
    })

    it('the label is the .eyebrow label style, in --color-text-secondary, the root\'s first <p>', () => {
      const { container } = render(<MetricCard title="Escape rate" metric={{ value: '56%' }} />)
      const label = container.querySelector('[data-metric-card] > p:first-child') as HTMLElement
      expect(label).toHaveTextContent('Escape rate')
      expect(label.className.split(/\s+/)).toEqual(expect.arrayContaining(['eyebrow', 'truncate', 'text-[var(--color-text-secondary)]']))
      expect(label.className).not.toMatch(/text-\[\d/)
    })

    it('the change line is 12 px (text-xs)', () => {
      const { container } = render(<MetricCard title="Runs" metric={{ value: 4, trend: 2, trend_direction: 'up' }} />)
      expect(trendRowClass(container).split(/\s+/)).toContain('text-xs')
    })

    it('sm is the in-panel stat: data-metric-card="stat" (never "compact"), the stat-sm value, the sunken well', () => {
      const { container } = render(<MetricCard size="sm" title="Time to fix" metric={{ value: 74 }} />)
      const root = container.firstElementChild as HTMLElement
      expect(root).toHaveAttribute('data-metric-card', 'stat')
      expect(container.querySelector('[data-metric-card="compact"]')).toBeNull()
      expect(root.className.split(/\s+/)).toEqual(
        expect.arrayContaining(['rounded-md', 'border', 'border-[var(--color-border)]', 'bg-[var(--color-bg-secondary)]', 'px-2.5', 'py-2']),
      )
      expect(root.className.split(/\s+/)).not.toContain('card')
      expect(screen.getByText('74').className.split(/\s+/)).toEqual(
        expect.arrayContaining(['tabular-nums', 'text-[length:var(--text-stat-sm)]', 'font-semibold']),
      )
      expect(root.getAttribute('style')).toBeNull()
    })

    it('note sits at the right end of the label row, not uppercase; the label still truncates', () => {
      const { container } = render(<MetricCard size="sm" title="Time to fix" note="30%" metric={{ value: 74 }} />)
      const label = container.querySelector('[data-metric-card] > p:first-child') as HTMLElement
      expect(label.className.split(/\s+/)).toEqual(expect.arrayContaining(['eyebrow', 'flex', 'justify-between']))
      expect(label.textContent).toBe('Time to fix30%')
      expect(screen.getByText('Time to fix').className).toContain('truncate')
      const note = screen.getByText('30%')
      expect(note.className.split(/\s+/)).toEqual(
        expect.arrayContaining(['shrink-0', 'normal-case', 'tracking-normal', 'text-[var(--color-text-muted)]']),
      )
    })

    it('caption is one truncated 12 px secondary line under the value, in reading order', () => {
      const { container } = render(<MetricCard size="sm" title="Failures eligible" metric={{ value: 7 }} caption="in Run #12" />)
      const caption = container.querySelector('[data-metric-caption]') as HTMLElement
      expect(caption).toHaveTextContent('in Run #12')
      expect(caption.className.split(/\s+/)).toEqual(expect.arrayContaining(['truncate', 'text-xs', 'text-[var(--color-text-secondary)]']))
      expect((container.firstElementChild as HTMLElement).textContent).toBe('Failures eligible7in Run #12')
    })

    it('bar: a width from the number in the tone hue; clamped to 0-100; null draws the empty track; absent draws none', () => {
      const { container, rerender } = render(<MetricCard size="sm" title="Score" tone="warn" bar={42} metric={{ value: 42 }} />)
      const fill = () => container.querySelector('[data-metric-bar] > i') as HTMLElement | null
      expect(container.querySelector('[data-metric-bar]')?.className.split(/\s+/)).toEqual(
        expect.arrayContaining(['h-1', 'min-w-12', 'flex-1', 'rounded-full', 'bg-[var(--color-border)]']),
      )
      expect(fill()?.style.width).toBe('42%')
      expect(fill()?.style.background).toBe('var(--ui-warning)')
      rerender(<MetricCard size="sm" title="Score" tone="good" bar={150} metric={{ value: 150 }} />)
      expect(fill()?.style.width).toBe('100%')
      expect(fill()?.style.background).toBe('var(--ui-success)')
      rerender(<MetricCard size="sm" title="Score" bar={null} />)
      expect(container.querySelector('[data-metric-bar]')).not.toBeNull()
      expect(fill()).toBeNull()
      rerender(<MetricCard size="sm" title="Score" metric={{ value: 1 }} />)
      expect(container.querySelector('[data-metric-bar]')).toBeNull()
    })

    it.each([
      ['good', '--ui-success-ink'],
      ['warn', '--ui-warning-ink'],
      ['bad', '--ui-danger-ink'],
      ['neutral', '--color-text'],
    ] as const)('tone %s paints the value with %s', (tone, token) => {
      render(<MetricCard size="sm" title="Score" tone={tone} metric={{ value: 61 }} />)
      expect(screen.getByText('61').className.split(/\s+/)).toContain(`text-[var(${token})]`)
    })

    it('data attributes reach the root, valueData reaches the value', () => {
      const { container } = render(
        <MetricCard
          size="sm"
          title="Time to fix"
          metric={{ value: '—' }}
          data-dimension="time_to_fix"
          data-measured="false"
          valueData={{ 'data-dimension-value': '' }}
        />,
      )
      const root = container.querySelector('[data-dimension="time_to_fix"]') as HTMLElement
      expect(root).toBe(container.firstElementChild)
      expect(root).toHaveAttribute('data-measured', 'false')
      expect(root.querySelector('[data-dimension-value]')).toHaveTextContent(/^—$/)
    })

    it('className is layout only, beside the surface', () => {
      const { container } = render(<MetricCard size="sm" title="Score" className="opacity-50" />)
      expect((container.firstElementChild as HTMLElement).className.split(/\s+/)).toEqual(
        expect.arrayContaining(['opacity-50', 'bg-[var(--color-bg-secondary)]']),
      )
    })

    it('no element carries a white sheen or the 16 px radius', () => {
      for (const size of ['md', 'sm'] as const) {
        const { container, unmount } = render(
          <MetricCard size={size} title="Pass Rate" metric={{ value: '98%', trend: 3, trend_direction: 'up' }} />,
        )
        for (const el of container.querySelectorAll('*')) {
          expect(el.getAttribute('class') ?? '', size).not.toMatch(/\bbg-white|\brounded-xl\b/)
          expect(el.getAttribute('style') ?? '', size).not.toMatch(/rgba\(/)
        }
        unmount()
      }
    })

    it('the sm loading state is the same role-less "Loading" text', () => {
      const { container } = render(<MetricCard size="sm" title="Score" loading metric={{ value: 3 }} />)
      expect(screen.getByText('Loading')).toBeInTheDocument()
      expect(screen.queryByText('3')).toBeNull()
      expect(container.querySelector('[aria-label],[aria-live],[role="status"]')).toBeNull()
    })
  })

  it('compact: a narrow card truncates the change text, never the judgement', () => {
    // The P0 baseline showed "Up 8% vs previous period (bet…": one truncating
    // span cut the word that says whether the change is good.
    const { container } = render(
      <MetricCard
        title="Runs"
        metric={{ value: 412, trend_direction: 'up', trend_text: '8% vs previous period' }}
      />,
    )

    const judgement = container.querySelector('[data-trend-judgement]')
    expect(judgement).toHaveTextContent('(better)')
    expect(judgement).toHaveClass('shrink-0')
    expect(judgement).not.toHaveClass('truncate')
    // W29b fix-kpi: the context truncates, never the figure before it.
    const context = judgement?.previousElementSibling
    expect(context).toHaveTextContent(/^vs previous period$/)
    expect(context).toHaveClass('truncate')
    expect(container.querySelector('[data-metric-trend]')).toHaveAttribute('title', 'Up 8% vs previous period (better)')
  })

  // W29b fix-kpi: ONE truncating span cut the figure itself where the tile is narrow for its type size: "Down 1.6
  // p… (worse)" at 1280 px and "Down … (worse)", "Up 4.2… (better)" in presentation mode, on CI's fonts. The
  // direction word and the figure are a span that never shrinks; only the context after them takes the ellipsis.
  it.each([
    ['1.6 pp vs previous 14 days', 'down', 'Down 1.6 pp', 'vs previous 14 days'],
    ['10% · later vs earlier half of window', 'down', 'Down 10%', '· later vs earlier half of window'],
    ['1022 passed · 119 failed · 21 broken', 'none', '1022 passed', '· 119 failed · 21 broken'],
    ['50% — schedule may be paused', 'none', '50%', '— schedule may be paused'],
  ] as const)('the figure of "%s" never truncates; the context after it does', (trendText, dir, head, tail) => {
    const { container } = render(<MetricCard title="Pass rate" metric={{ value: '88%', trend_direction: dir, trend_text: trendText }} />)
    const row = container.querySelector('[data-metric-trend]') as HTMLElement
    const figure = row.querySelector('[data-change-head]') as HTMLElement
    expect(figure.textContent).toBe(head)
    expect(figure).toHaveClass('shrink-0', 'whitespace-nowrap')
    expect(figure).not.toHaveClass('truncate')
    const context = figure.nextElementSibling as HTMLElement
    expect(context.textContent).toBe(tail)
    expect(context).toHaveClass('min-w-0', 'truncate')
    // The words read as one line: a space between the figure and its context, the judgement after them.
    expect(row.textContent).toBe([head, ' ', tail, dir === 'none' ? '' : '(worse)'].join(''))
  })

  it('a change with no context is one truncating span (a list, a sentence)', () => {
    const { container } = render(
      <MetricCard title="Suites" metric={{ value: 5, trend_direction: 'none', trend_text: 'Auth, Checkout, Payments, Search' }} />,
    )
    const row = container.querySelector('[data-metric-trend]') as HTMLElement
    expect(row.querySelector('[data-change-head]')).toBeNull()
    expect(row.querySelectorAll('span')).toHaveLength(1)
    expect(screen.getByText('Auth, Checkout, Payments, Search')).toHaveClass('min-w-0', 'truncate')
  })

  it('compact: an unjudged change has no judgement span', () => {
    const { container } = render(
      <MetricCard title="Duration" metric={{ value: '4m', trend_direction: 'none', trend_text: 'not measured last period' }} />,
    )

    expect(container.querySelector('[data-trend-judgement]')).toBeNull()
    expect(container.querySelector('[data-metric-trend]')).toHaveAttribute('title', 'not measured last period')
  })

  it('compact: the label is one truncated line whose hover text is the hint, or the label itself', () => {
    const { rerender } = render(<MetricCard title="Time to resolve" hint="Mean time to resolve, over the defects resolved" metric={{ value: '2.3d' }} />)
    const label = screen.getByText('Time to resolve')
    expect(label).toHaveClass('truncate')
    expect(label).toHaveAttribute('title', 'Mean time to resolve, over the defects resolved')
    rerender(<MetricCard title="Escape rate" metric={{ value: '56%' }} />)
    expect(screen.getByText('Escape rate')).toHaveAttribute('title', 'Escape rate')
  })

  it('compact: the sparkline takes what the value leaves, capped to it, and drops below the value rather than overlap it', () => {
    const { container } = render(
      <MetricCard title="Total executions" metric={{ value: '4437' }} sparkline={<svg data-testid="spark" />} />,
    )
    const aside = container.querySelector('[data-metric-aside]') as HTMLElement
    expect(aside).toContainElement(screen.getByTestId('spark'))
    // Grows into the room left (flex-1), never under 3rem (min-w-12), and caps its child to that room.
    expect(aside.className.split(/\s+/)).toEqual(expect.arrayContaining(['flex-1', 'min-w-12', '[&>*]:max-w-full']))
    // The row wraps: under 3rem left, the aside takes a line of its own.
    expect((aside.parentElement as HTMLElement).className.split(/\s+/)).toContain('flex-wrap')
  })
})
