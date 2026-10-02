/**
 * Category names that are also `Object.prototype` members.
 *
 * Suite, test, release and branch names come from ingested CI files, so a
 * category can be called `constructor` or `toString`. Every lookup of a
 * display label (or a per-series formatter, or a bucket value) by such a name
 * on a PLAIN object used to read the inherited member instead of "no entry":
 * the table header became a function, the generated summary said
 * "function Object() { [native code] }", the bar label was a function that
 * `middleTruncate` choked on, and a stacked column counted a missing value as
 * an invalid one. Every lookup now reads own properties only (`ownValue`).
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { SeriesChart } from '@/lib/viz/contracts'
import { barsFromSeries, rankedModel, statusRowsFromSeries } from './BarChart.model'
import ChartTable from './ChartTable'
import { chartTableModel, formatPlainValue, formatterFor, ownLabel, ownValue, summarizeChart } from './chartText'
import { alignByReleaseStart } from './seriesAlignment'
import { buildStackedColumnModel } from './stackedColumnModel'
import { PROTOTYPE_KEY_NAMES, protoKeyNamesSeries } from './__fixtures__/protoKeyNames'

const NAMES = PROTOTYPE_KEY_NAMES

/** What an inherited member looks like once it is turned into text. */
const LEAK = /function|native code|\[object /

const categoryChart = (xLabels?: Record<string, string>): SeriesChart => ({
  kind: 'series',
  dimensions: ['suite'],
  x_type: 'category',
  series: [{ key: 'failures', label: 'Failures', points: NAMES.map((x, i) => ({ x, y: i + 1, n: i + 1 })) }],
  ...(xLabels ? { x_labels: xLabels } : {}),
})

describe('ownValue / ownLabel', () => {
  it('reads own properties only', () => {
    const labels: Record<string, string> = { a: 'A' }
    for (const name of NAMES) expect(ownValue(labels, name)).toBeUndefined()
    expect(ownValue(labels, 'a')).toBe('A')
    expect(ownValue(undefined, 'a')).toBeUndefined()
  })

  it('an own entry for a prototype name is still used (a JSON payload can carry one)', () => {
    const labels = JSON.parse('{"__proto__":"Proto suite","constructor":"Ctor suite"}') as Record<string, string>
    expect(ownLabel(labels, '__proto__')).toBe('Proto suite')
    expect(ownLabel(labels, 'constructor')).toBe('Ctor suite')
  })

  it('a label that is not text falls back to the key', () => {
    const labels = JSON.parse('{"a":5,"b":null}') as Record<string, string>
    expect(ownLabel(labels, 'a')).toBe('a')
    expect(ownLabel(labels, 'b')).toBe('b')
  })
})

describe('chartText: table and summary', () => {
  it.each([
    ['no x_labels at all', undefined],
    ['x_labels for other keys', { other: 'Other' }],
  ])('the table header is the literal name (%s)', (_case, labels) => {
    const table = chartTableModel(categoryChart(labels))
    expect(table.rows.map((row) => row.header)).toEqual([...NAMES])
    for (const row of table.rows) expect(typeof row.header).toBe('string')
  })

  it('the table uses an OWN label for a prototype name', () => {
    const labels = JSON.parse('{"constructor":"Ctor suite","toString":"String suite"}') as Record<string, string>
    const headers = chartTableModel(categoryChart(labels)).rows.map((row) => row.header)
    expect(headers).toEqual(['Ctor suite', '__proto__', 'String suite', 'hasOwnProperty'])
  })

  it('the summary names the categories, never an inherited function', () => {
    const summary = summarizeChart({ chartType: 'Bar chart', series: categoryChart({}) })
    expect(summary).not.toMatch(LEAK)
    // min at "constructor" (1), max at "hasOwnProperty" (4).
    expect(summary).toContain('(constructor)')
    expect(summary).toContain('(hasOwnProperty)')
  })

  it('a series keyed by a prototype name gets the fallback formatter, not an inherited function', () => {
    const format = { count: (v: number) => `${v} runs` }
    for (const name of NAMES) {
      expect(formatterFor(format, name, formatPlainValue)).toBe(formatPlainValue)
    }
    const chart: SeriesChart = {
      kind: 'series',
      dimensions: ['day'],
      x_type: 'category',
      series: NAMES.map((key) => ({ key, label: key, points: [{ x: 'a', y: 1234.5, n: 1 }] })),
    }
    const table = chartTableModel(chart, {}, format)
    expect(table.rows[0].cells).toEqual(NAMES.map(() => '1,234.5'))
    expect(summarizeChart({ chartType: 'Line chart', series: chart, format })).not.toMatch(LEAK)
  })
})

describe('BarChart.model: bar labels', () => {
  it('ranked bars are labelled with the literal names', () => {
    const bars = barsFromSeries(categoryChart({}))
    expect(bars.map((bar) => bar.label)).toEqual([...NAMES])
    // The model truncates labels for the axis: a function here threw.
    expect(rankedModel(bars).bars.map((bar) => bar.short)).toEqual(expect.arrayContaining([...NAMES]))
  })

  it('stacked rows are labelled with the literal names', () => {
    const chart: SeriesChart = {
      kind: 'series',
      dimensions: ['suite', 'status'],
      x_type: 'category',
      x_labels: {},
      series: [{ key: 'passed', label: 'Passed', points: NAMES.map((x) => ({ x, y: 3, n: 3 })) }],
    }
    expect(statusRowsFromSeries(chart).map((row) => row.label)).toEqual([...NAMES])
  })
})

describe('stackedColumnModel: bucket values', () => {
  it('a series named like a prototype member with no value is NOT measured, not invalid', () => {
    const model = buildStackedColumnModel({
      buckets: [{ key: 'b1', label: 'B1', values: { ok: 2 } }],
      series: [...NAMES.map((key) => ({ key, label: key })), { key: 'ok', label: 'ok' }],
      valueTitle: 'Count',
      bucketTitle: 'Bucket',
    })
    expect(model.invalid).toBe(0)
    expect(model.buckets[0].values.slice(0, NAMES.length)).toEqual(NAMES.map(() => null))
  })
})

describe('seriesAlignment: named starts', () => {
  it('a series named like a prototype member with no named start aligns on its first active day', () => {
    const result = alignByReleaseStart(
      NAMES.map((key) => ({ key, label: key, points: [{ x: '2026-03-02', y: 90, n: 5 }] })),
      { starts: {} },
    )
    expect(result.series.map((s) => s.startSource)).toEqual(NAMES.map(() => 'first-active'))
    expect(result.series.map((s) => s.start)).toEqual(NAMES.map(() => '2026-03-02'))
  })
})

describe('gallery fixture: prototype-member names', () => {
  it('tabulates, labels and summarises every name as itself', () => {
    render(<ChartTable caption="Failures by suite" series={protoKeyNamesSeries} autoFocus={false} />)
    for (const name of PROTOTYPE_KEY_NAMES) expect(screen.getByRole('rowheader', { name })).toBeInTheDocument()
    expect(barsFromSeries(protoKeyNamesSeries).map((bar) => bar.label)).toEqual([...PROTOTYPE_KEY_NAMES])
    expect(summarizeChart({ chartType: 'Ranked bars', series: protoKeyNamesSeries })).not.toMatch(LEAK)
  })
})
