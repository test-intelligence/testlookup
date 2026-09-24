import { describe, expect, it } from 'vitest'
import type { EnvelopeMeta, MatrixChart, SeriesChart } from './contracts'
import {
  buildChartCsv,
  EXPORT_PAD,
  exportFilename,
  fitText,
  footerParagraphs,
  layoutExport,
  provenanceFields,
  provenanceFromMeta,
  SLUG_MAX,
  slugify,
  totalsLine,
  wrapText,
  type ChartProvenance,
  type MeasureText,
} from './chartExport'

/** A deterministic measure: every character is 6 px (at any font). */
const measure: MeasureText = (text) => Array.from(text).length * 6

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'Payments' }],
    releases: [{ id: 'r1', name: '2026.09', status: 'active' }],
    suites: ['api', 'ui'],
    window: { from: '2026-08-25', to: '2026-09-23', days: 30, timezone: 'UTC' },
  },
  totals: { matched_runs: 12, total_runs: 1500, matched_executions: 3400, total_executions: 99000 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-23T14:05:09Z',
  as_of: '2026-09-23T14:05:00Z',
}

const PROVENANCE: ChartProvenance = {
  project: 'Payments',
  releases: ['2026.09'],
  suites: ['api', 'ui'],
  window: '2026-08-25 – 2026-09-23 UTC (30 days)',
  totals: '12 of 1,500 runs · 3,400 of 99,000 executions',
  generatedAt: '2026-09-23T14:05:09Z',
  appVersion: '1.4.2',
}

/** A formula a spreadsheet would evaluate if the cell were written as is. */
const FORMULA_LABEL = '=SUM(1,2)'

describe('provenanceFromMeta', () => {
  it('states project, releases, suites, window, totals and generated-at from the envelope', () => {
    expect(provenanceFromMeta(META, '1.4.2')).toEqual(PROVENANCE)
  })

  it('an empty scope reads as "all", never as nothing', () => {
    const meta = { ...META, scope: { ...META.scope, projects: [], releases: [], suites: [] } }
    const p = provenanceFromMeta(meta, null) as ChartProvenance
    expect(p.project).toBe('All projects')
    expect(provenanceFields(p)).toEqual(
      expect.arrayContaining([
        ['Release', 'All releases'],
        ['Test Suite', 'All suites'],
      ]),
    )
    expect(provenanceFields(p).some(([label]) => label === 'App version')).toBe(false)
  })

  it('several projects are comma-joined; one day is singular', () => {
    const meta = {
      ...META,
      scope: {
        ...META.scope,
        projects: [
          { id: 'a', name: 'A' },
          { id: 'b', name: 'B' },
        ],
        window: { from: '2026-09-23', to: '2026-09-23', days: 1, timezone: 'UTC' as const },
      },
    }
    const p = provenanceFromMeta(meta, null) as ChartProvenance
    expect(p.project).toBe('A, B')
    expect(p.window).toBe('2026-09-23 – 2026-09-23 UTC (1 day)')
  })

  it('carries a local-zoom note', () => {
    expect(provenanceFromMeta(META, null, 'Zoomed to 3 Sep – 12 Sep (not the page window)')?.note).toBe(
      'Zoomed to 3 Sep – 12 Sep (not the page window)',
    )
  })

  // Review F7: the totals are the whole window's (the envelope has no per-day
  // run counts); beside a note saying the export shows the zoomed days only,
  // an unlabelled "N of M runs" reads as the zoomed days' runs.
  it('zoomed: the totals are stated as the WINDOW\'s, in the footer and the CSV', () => {
    const zoomed = provenanceFromMeta(META, null, 'Zoomed to 3 Sep – 12 Sep') as ChartProvenance
    expect(zoomed.zoomed).toBe(true)
    expect(provenanceFields(zoomed)).toContainEqual(['Window totals', '12 of 1,500 runs · 3,400 of 99,000 executions'])
    expect(provenanceFields(zoomed).some(([label]) => label === 'Totals')).toBe(false)
    expect(footerParagraphs(zoomed)[2]).toBe(
      'Window: 2026-08-25 – 2026-09-23 UTC (30 days) · Window totals: 12 of 1,500 runs · 3,400 of 99,000 executions',
    )
    const csv = buildChartCsv('Pass rate', LINE, zoomed).split('\r\n')
    expect(csv).toContain('# Window totals,"12 of 1,500 runs · 3,400 of 99,000 executions"')
    expect(csv.some((line) => line.startsWith('# Totals,'))).toBe(false)
  })

  it('not zoomed: the footer line and the CSV label are exactly as before', () => {
    const plain = provenanceFromMeta(META, null) as ChartProvenance
    expect(plain.zoomed).toBeUndefined()
    expect(footerParagraphs(plain)[2]).toBe(
      'Window: 2026-08-25 – 2026-09-23 UTC (30 days) · 12 of 1,500 runs · 3,400 of 99,000 executions',
    )
    expect(buildChartCsv('Pass rate', LINE, plain).split('\r\n')).toContain(
      '# Totals,"12 of 1,500 runs · 3,400 of 99,000 executions"',
    )
  })

  it('no meta → null (the export says "Scope unavailable")', () => {
    expect(provenanceFromMeta(null, '1')).toBeNull()
    expect(footerParagraphs(null)).toEqual(['Scope unavailable.'])
  })

  it('totalsLine is the frame footer\'s "N of M" text', () => {
    expect(totalsLine(META)).toBe('12 of 1,500 runs · 3,400 of 99,000 executions')
  })
})

describe('footerParagraphs', () => {
  it('names every field the story requires, in UTC, with the app version', () => {
    const text = footerParagraphs({ ...PROVENANCE, note: 'Zoomed' }).join('\n')
    expect(text).toContain('Project: Payments')
    expect(text).toContain('Release: 2026.09')
    expect(text).toContain('Test Suite: api, ui')
    expect(text).toContain('Window: 2026-08-25 – 2026-09-23 UTC (30 days)')
    expect(text).toContain('12 of 1,500 runs')
    expect(text).toContain('Generated 2026-09-23 14:05 UTC')
    expect(text).toContain('TestLookup 1.4.2')
    expect(text).toContain('Note: Zoomed')
  })
})

describe('exportFilename — testlookup_<chart>_<project>_<yyyymmdd-hhmm>Z.<ext>', () => {
  it('the story\'s format, from generated_at in UTC', () => {
    expect(exportFilename('Pass rate trend', 'Payments', '2026-09-23T14:05:09Z', 'png')).toBe(
      'testlookup_pass-rate-trend_payments_20260923-1405Z.png',
    )
    expect(exportFilename('x', 'p', '2026-01-02T03:04:00+00:00', 'csv')).toBe('testlookup_x_p_20260102-0304Z.csv')
  })

  it('no project → all-projects; an unparseable time → now', () => {
    const now = () => new Date('2027-12-31T23:59:00Z')
    expect(exportFilename('Chart', null, 'garbage', 'svg', now)).toBe('testlookup_chart_all-projects_20271231-2359Z.svg')
  })

  it.each([
    ['../../up/two', 'up-two'],
    ['a/b\\c', 'a-b-c'],
    ['..', 'project'],
    ['report‮fdp.exe', 'report-fdp-exe'],
    ['tab\there\u0000nul', 'tab-here-nul'],
    ['CON', 'con'],
    ['支払い サービス', '支払い-サービス'],
    ['<img src=x on-error=y>', 'img-src-x-on-error-y'],
  ])('hostile project %j → segment %j', (name, segment) => {
    const file = exportFilename('c', name, '2026-09-23T14:05:00Z', 'png')
    expect(file).toBe(`testlookup_c_${segment}_20260923-1405Z.png`)
    expect(file).not.toMatch(/[/\\:‮]|\.\./)
    expect(Array.from(file).some((ch) => (ch.codePointAt(0) ?? 0) < 0x20)).toBe(false)
  })

  it('a very long name is cut to SLUG_MAX code points', () => {
    expect(Array.from(slugify('x'.repeat(500), 'f'))).toHaveLength(SLUG_MAX)
    expect(slugify('a'.repeat(SLUG_MAX - 1) + ' bcd', 'f')).toBe('a'.repeat(SLUG_MAX - 1))
  })
})

const LINE: SeriesChart = {
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [
    {
      key: 'rate',
      label: 'Pass rate',
      points: [
        { x: '2026-09-01', y: 0.9123456, n: 10 },
        { x: '2026-09-02', y: null, n: 0 },
        { x: '2026-09-03', y: 0, n: 5 },
        { x: '2026-09-04', y: 0.5, n: 5, measured: false, reason: 'partial day' },
        { x: '2026-09-05', y: -0.25, n: 4 },
      ],
    },
    {
      key: 'runs',
      label: FORMULA_LABEL,
      points: [{ x: '2026-09-01', y: 1234567, n: 1 }],
    },
  ],
}

const HEADER = `day,Pass rate,"'${FORMULA_LABEL}"`

describe('buildChartCsv — exactly the plotted values, preceded by the scope', () => {
  const lines = (text: string) => text.split('\r\n')

  it('comment lines state the same scope as the image footer', () => {
    const csv = buildChartCsv('Pass rate', LINE, { ...PROVENANCE, note: 'Zoomed' })
    expect(lines(csv).slice(0, 10)).toEqual([
      '# TestLookup chart export,Pass rate',
      '# Project,Payments',
      '# Release,2026.09',
      '# Test Suite,"api, ui"',
      '# Window,2026-08-25 – 2026-09-23 UTC (30 days)',
      '# Totals,"12 of 1,500 runs · 3,400 of 99,000 executions"',
      '# Generated,2026-09-23 14:05 UTC',
      '# App version,1.4.2',
      '# Note,Zoomed',
      HEADER,
    ])
  })

  it('raw values at full precision; a gap and a measured:false point are EMPTY, a real 0 is 0, a negative stays a number', () => {
    const all = lines(buildChartCsv('t', LINE, PROVENANCE))
    expect(all.slice(all.indexOf(HEADER) + 1)).toEqual([
      '2026-09-01,0.9123456,1234567',
      '2026-09-02,,',
      '2026-09-03,0,',
      '2026-09-04,,',
      '2026-09-05,-0.25,',
      '',
    ])
  })

  it('the rows and columns are the table view\'s (same order, same duplicate handling)', () => {
    const dup: SeriesChart = {
      kind: 'series',
      dimensions: ['suite'],
      x_type: 'category',
      series: [
        {
          key: 's',
          label: 'Runs',
          points: [
            { x: 'b', y: 1, n: 1 },
            { x: 'a', y: 2, n: 1 },
            { x: 'b', y: 3, n: 1 },
          ],
        },
      ],
      x_labels: { a: 'Suite A' },
    }
    expect(lines(buildChartCsv('t', dup, null))).toEqual([
      '# TestLookup chart export,t',
      '# Scope,Scope unavailable',
      '# Warning,Runs has more than one value at b.',
      'suite,Runs',
      'b,1',
      'b (duplicate),3',
      'Suite A,2',
      '',
    ])
  })

  it('a matrix: a null cell and a never-sent cell are both empty; hostile labels are neutralised', () => {
    const matrix: MatrixChart = {
      kind: 'matrix',
      value_type: 'rate',
      x_labels: ['+x', 'y'],
      y_labels: ['@row'],
      cells: [{ x: 0, y: 0, value: null, n: 0 }],
    }
    expect(lines(buildChartCsv('m', matrix, null)).slice(2)).toEqual(["Row,'+x,y", "'@row,,", ''])
  })

  it('hostile provenance values are neutralised in the comment lines too', () => {
    const csv = buildChartCsv('=t', LINE, { ...PROVENANCE, project: '=HYPERLINK("x")', suites: ['-2+3'] })
    expect(csv).toContain(`# TestLookup chart export,'=t`)
    expect(csv).toContain(`# Project,"'=HYPERLINK(""x"")"`)
    expect(csv).toContain(`# Test Suite,'-2+3`)
  })
})

describe('wrapText / fitText', () => {
  it('wraps on words within the width', () => {
    expect(wrapText('aaa bbb ccc', 42, '', measure)).toEqual(['aaa bbb', 'ccc'])
  })

  it('breaks a word longer than the line by characters — nothing runs off the edge', () => {
    const lines = wrapText('x'.repeat(25), 60, '', measure)
    expect(lines).toEqual(['x'.repeat(10), 'x'.repeat(10), 'x'.repeat(5)])
    for (const line of lines) expect(measure(line, '')).toBeLessThanOrEqual(60)
  })

  it('fitText ellipsises to fit, and leaves a fitting label alone', () => {
    expect(fitText('short', 60, '', measure)).toBe('short')
    const cut = fitText('a very long legend label', 60, '', measure)
    expect(cut.endsWith('…')).toBe(true)
    expect(measure(cut, '')).toBeLessThanOrEqual(60)
  })
})

describe('layoutExport', () => {
  const base = {
    title: 'Pass rate trend',
    chart: { width: 600, height: 300 },
    legend: [
      { label: 'Passed', swatch: { width: 24, height: 10 } },
      { label: 'Failed', swatch: { width: 24, height: 10 } },
    ],
    footer: footerParagraphs(PROVENANCE),
    measure,
  }

  it('title above the chart, legend below it, footer in its own band at the bottom', () => {
    const layout = layoutExport(base)
    expect(layout.width).toBe(600 + 2 * EXPORT_PAD)
    const title = layout.text.filter((t) => t.role === 'title')
    const legend = layout.text.filter((t) => t.role === 'legend')
    const footer = layout.text.filter((t) => t.role === 'footer')
    expect(title.map((t) => t.text)).toEqual(['Pass rate trend'])
    expect(title[0].y).toBeLessThan(layout.chart.y)
    expect(legend.map((t) => t.text)).toEqual(['Passed', 'Failed'])
    for (const t of legend) expect(t.y).toBeGreaterThan(layout.chart.y + layout.chart.height)
    expect(footer.length).toBeGreaterThanOrEqual(4)
    for (const t of footer) {
      expect(t.text.trim()).not.toBe('')
      expect(t.y).toBeGreaterThan(layout.footer.y)
      expect(t.y).toBeLessThanOrEqual(layout.height)
    }
    expect(layout.footer.y).toBeGreaterThan(Math.max(...legend.map((t) => t.y)))
    expect(layout.swatches).toHaveLength(2)
  })

  it('a very long suite list WRAPS inside the image: every word is kept and no line is wider than the image', () => {
    const suites = Array.from({ length: 60 }, (_, i) => `suite-number-${i}`)
    const layout = layoutExport({ ...base, footer: footerParagraphs({ ...PROVENANCE, suites }) })
    const footer = layout.text.filter((t) => t.role === 'footer')
    for (const t of footer) expect(t.x + measure(t.text, t.font)).toBeLessThanOrEqual(layout.width - EXPORT_PAD)
    const all = footer.map((t) => t.text).join(' ')
    for (const suite of suites) expect(all).toContain(suite)
    expect(layout.height).toBeGreaterThan(layoutExport(base).height)
  })

  it('a narrow chart still gets a readable minimum width, the chart centred', () => {
    const layout = layoutExport({ ...base, chart: { width: 200, height: 100 } })
    expect(layout.width).toBe(480)
    expect(layout.chart.x).toBe((480 - 200) / 2)
  })

  it('legend items wrap into rows rather than off the edge', () => {
    const legend = Array.from({ length: 30 }, (_, i) => ({ label: `Series number ${i}`, swatch: { width: 24, height: 10 } }))
    const layout = layoutExport({ ...base, legend })
    const rows = new Set(layout.swatches.map((s) => s.y))
    expect(rows.size).toBeGreaterThan(1)
    layout.text
      .filter((t) => t.role === 'legend')
      .forEach((t) => expect(t.x + measure(t.text, t.font)).toBeLessThanOrEqual(layout.width - EXPORT_PAD + 0.001))
  })
})
