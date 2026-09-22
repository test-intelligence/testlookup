import { afterEach, describe, expect, it } from 'vitest'
import {
  TOOLTIP_NODE_ATTRIBUTE,
  assertSafeChartOption,
  buildTooltipNode,
  domTooltipFormatter,
  findUnsafeFormatter,
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
