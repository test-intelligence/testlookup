import { afterEach, describe, expect, it } from 'vitest'
import {
  MINUS,
  NO_CHANGE,
  TOOLTIP_NODE_ATTRIBUTE,
  assertSafeChartOption,
  buildTooltipNode,
  changeRow,
  domTooltipFormatter,
  findUnsafeFormatter,
  formatChange,
  orderRows,
  safeDataAttributes,
  sampleRow,
  shareRow,
  tipContent,
  tooltipText,
  type TooltipRow,
} from './tooltip'

const HOSTILE = '<img src=x onerror="window.__xss=1">'

describe('tooltip helper (ADR decision 4)', () => {
  afterEach(() => {
    delete (window as { __xss?: unknown }).__xss
  })

  it('renders hostile labels as literal text, creating no element from them', () => {
    const node = buildTooltipNode({
      title: '<b>suite</b>',
      rows: [{ label: HOSTILE, value: '<script>alert(1)</script>', color: 'var(--chart-series-1)' }],
    })
    document.body.appendChild(node)
    expect(node.textContent).toContain(HOSTILE)
    expect(node.textContent).toContain('<b>suite</b>')
    expect(node.querySelector('img, b, script')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
    node.remove()
  })

  it('returns a DOM node, never a string, and marks it as ours', () => {
    const formatter = domTooltipFormatter((params: { name: string }) => ({ rows: [{ label: params.name, value: '1' }] }))
    const out = formatter({ name: HOSTILE })
    expect(out).toBeInstanceOf(HTMLElement)
    expect(typeof out).not.toBe('string')
    expect(out.hasAttribute(TOOLTIP_NODE_ATTRIBUTE)).toBe(true)
    expect(out.querySelector('.chart-tooltip-label')?.textContent).toBe(HOSTILE)
  })

  it('omits the title row when there is no title, and the swatch when there is no colour', () => {
    const node = buildTooltipNode({ rows: [{ label: 'a', value: 'b' }] })
    expect(node.querySelector('.chart-tooltip-title')).toBeNull()
    expect(node.querySelector('[aria-hidden="true"]')).toBeNull()
    expect(node.querySelector('.chart-tooltip-value')?.textContent).toBe('b')
  })
})

describe('findUnsafeFormatter: the runtime half of the formatter rule', () => {
  const approved = domTooltipFormatter(() => ({ rows: [] }))

  it('accepts an option whose only formatters were built by domTooltipFormatter', () => {
    const option = { tooltip: { formatter: approved }, series: [{ tooltip: { formatter: approved } }], xAxis: {} }
    expect(findUnsafeFormatter(option)).toBeNull()
    expect(() => assertSafeChartOption(option)).not.toThrow()
  })

  it.each([
    ['a string template', { tooltip: { formatter: '{b}: {c}' } }, 'option.tooltip.formatter'],
    ['a hand-written function', { tooltip: { formatter: (p: { name: string }) => p.name } }, 'option.tooltip.formatter'],
    ['a function nested in a series', { series: [{ label: { formatter: () => '<b>x</b>' } }] }, 'option.series[0].label.formatter'],
    ['a differently cased key', { legend: { Formatter: () => 'x' } }, 'option.legend.Formatter'],
    ['a *Formatter suffix', { axisPointer: { label: { valueFormatter: () => 'x' } } }, 'option.axisPointer.label.valueFormatter'],
    [
      'a getter hiding the function',
      { tooltip: Object.defineProperty({}, 'formatter', { enumerable: false, get: () => () => 'x' }) },
      'option.tooltip.formatter',
    ],
  ])('refuses %s and names where it is', (_label, option, path) => {
    expect(findUnsafeFormatter(option)).toBe(path)
    expect(() => assertSafeChartOption(option)).toThrow(path)
  })

  it('refuses a helper result wrapped in a fallback, since the wrapper is not the helper', () => {
    const wrapped = (p: unknown) => approved(p) || 'x'
    expect(findUnsafeFormatter({ tooltip: { formatter: wrapped } })).toBe('option.tooltip.formatter')
  })

  it('survives a cyclic option and refuses one nested past the depth limit', () => {
    const cyclic: Record<string, unknown> = { tooltip: { formatter: approved } }
    cyclic.self = cyclic
    expect(findUnsafeFormatter(cyclic)).toBeNull()
    let deep: Record<string, unknown> = {}
    const root = deep
    for (let i = 0; i < 40; i += 1) deep = (deep.next = {}) as Record<string, unknown>
    expect(findUnsafeFormatter(root)).toMatch(/nested deeper than/)
  })

  it('ignores a null or absent formatter', () => {
    expect(findUnsafeFormatter({ tooltip: { formatter: null }, label: { formatter: undefined } })).toBeNull()
  })
})

describe('the shared content model (VIZ-601)', () => {
  it('orders rows the one way every tooltip reads: dimensions, values, n, share, change, notes', () => {
    const rows: TooltipRow[] = [
      { kind: 'note', label: 'Why', value: 'nothing ran' },
      { kind: 'change', label: 'Change', value: '+1' },
      { label: 'b', value: '2' },
      { kind: 'share', label: 'Share', value: '10%' },
      { kind: 'sample', label: 'Samples', value: '9' },
      { label: 'a', value: '1' },
      { kind: 'dimension', label: 'Release', value: 'r1' },
    ]
    expect(orderRows(rows).map((row) => row.label)).toEqual(['Release', 'b', 'a', 'Samples', 'Share', 'Change', 'Why'])
  })

  it('builds n, the share and the change in the same words everywhere', () => {
    expect(sampleRow(1234)).toMatchObject({ kind: 'sample', label: 'Samples', value: '1,234' })
    expect(shareRow(1, 8)).toMatchObject({ kind: 'share', label: 'Share of total', value: '12.5%' })
    // No whole, a negative part: no share at all, rather than a meaningless one.
    expect(shareRow(1, 0)).toBeNull()
    expect(shareRow(-1, 8)).toBeNull()
    expect(formatChange(1.25, (v) => `${v.toFixed(1)} pts`)).toBe('+1.3 pts')
    expect(formatChange(-340, (v) => `${v}ms`)).toBe(`${MINUS}340ms`)
    // A difference that rounds to nothing is no change, not "+0.0".
    expect(formatChange(0.01, (v) => `${v.toFixed(1)} pts`)).toBe(NO_CHANGE)
    expect(formatChange(0, String)).toBe(NO_CHANGE)
  })

  it('states a change only when there is one to state, and never calls an unknown one "no change"', () => {
    const fmt = (v: number) => `${v}`
    expect(changeRow({ current: 5, previous: 3, formatMagnitude: fmt })).toMatchObject({ kind: 'change', value: '+2' })
    // The first bucket has no previous one; a gap has no change of its own.
    expect(changeRow({ current: 5, previous: undefined, formatMagnitude: fmt })).toBeNull()
    expect(changeRow({ current: null, previous: 3, formatMagnitude: fmt })).toBeNull()
    expect(changeRow({ current: 5, previous: null, previousLabel: '2026-03-02', formatMagnitude: fmt })).toMatchObject({
      value: '—',
      detail: '2026-03-02 not measured',
    })
  })

  it('assembles content without the rows a builder declined, in order', () => {
    const content = tipContent('Day', [shareRow(1, 0), { label: 'v', value: '1' }, sampleRow(3), false, null])
    expect(content).toEqual({ title: 'Day', rows: [{ label: 'v', value: '1' }, sampleRow(3)] })
  })

  it('speaks the content as one line, in the order it is drawn, with each row as "Label: value (detail)"', () => {
    const content = tipContent('2026-03-02 (UTC)', [
      { label: 'Pass rate %', value: '91.2%' },
      sampleRow(120),
      { kind: 'change', label: 'Change vs previous day', value: '+1.2 pts' },
      { kind: 'note', label: 'Why', value: 'every test was skipped.' },
      { kind: 'note', label: '', value: 'Everything above the axis' },
      { label: 'search', value: '98.0%', detail: '100 samples' },
    ])
    expect(tooltipText(content)).toBe(
      '2026-03-02 (UTC). Pass rate %: 91.2%. search: 98.0% (100 samples). Samples: 120. ' +
        'Change vs previous day: +1.2 pts. Why: every test was skipped. Everything above the axis',
    )
  })

  it('sets only data-* hooks built from code, as text', () => {
    expect(
      safeDataAttributes({ 'data-tip-series': HOSTILE, onerror: 'x', 'data-x y': 'z', style: 'color:red', 'data-ok': 'v' }),
    ).toEqual([
      ['data-tip-series', HOSTILE],
      ['data-ok', 'v'],
    ])
    const node = buildTooltipNode({ rows: [{ label: 'a', value: 'b', data: { onclick: 'alert(1)', 'data-tip-series': HOSTILE } }] })
    const row = node.querySelector('.chart-tooltip-row') as HTMLElement
    expect(row.getAttribute('onclick')).toBeNull()
    expect(row.getAttribute('data-tip-series')).toBe(HOSTILE)
  })

  it('draws a note as a wrapping sentence and a detail in parentheses, all as text', () => {
    const node = buildTooltipNode({
      rows: [
        { kind: 'note', label: 'Why', value: HOSTILE },
        { label: 'cart', value: '94.0%', detail: HOSTILE, color: 'var(--chart-series-2)', mark: 'line', dash: '4 2' },
      ],
    })
    const note = node.querySelector('.chart-tooltip-note') as HTMLElement
    expect(note.querySelector('.chart-tooltip-label')?.textContent).toBe('Why: ')
    expect(note.style.whiteSpace).toBe('normal')
    expect(node.querySelector('.chart-tooltip-detail')?.textContent).toBe(`(${HOSTILE})`)
    // A line swatch carries its series' dash: never colour alone.
    expect(node.querySelector('svg line')?.getAttribute('stroke-dasharray')).toBe('4 2')
    expect(node.querySelector('img')).toBeNull()
  })
})
