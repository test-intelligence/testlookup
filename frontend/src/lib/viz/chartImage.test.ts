import { afterEach, describe, expect, it, vi } from 'vitest'
import { EXPORT_PIXEL_RATIO, type ChartProvenance, type MeasureText } from './chartExport'
import {
  buildScene,
  canvasSize,
  copyReferencedDefs,
  extractLegend,
  findChartSvg,
  inlineComputedStyles,
  LIGHT_THEME,
  paintScene,
  sceneToSvgText,
  xmlSafeText,
  type PaintContext,
  type ReadStyle,
} from './chartImage'
import { THEMES } from '@/store/themeStore'

const measure: MeasureText = (text) => Array.from(text).length * 6

/**
 * jsdom resolves neither `var()` nor presentation attributes, so the tests
 * pass what a browser's `getComputedStyle` would return: every `var(--x)`
 * resolved from a table, per theme.
 */
const TOKENS: Record<string, Record<string, string>> = {
  dark: {
    '--chart-series-1': 'rgb(230, 159, 0)',
    '--color-bg-card': 'rgb(24, 29, 37)',
    '--color-text': 'rgb(232, 235, 239)',
    '--color-text-secondary': 'rgb(185, 191, 201)',
    '--color-border': 'rgb(40, 40, 40)',
  },
  light: {
    '--chart-series-1': 'rgb(189, 130, 0)',
    '--color-bg-card': 'rgb(255, 255, 255)',
    '--color-text': 'rgb(26, 29, 35)',
    '--color-text-secondary': 'rgb(69, 74, 84)',
    '--color-border': 'rgb(217, 221, 228)',
  },
}

function fakeReadStyle(): ReadStyle {
  return (el) => {
    const theme = el.closest(`[data-theme="${LIGHT_THEME}"]`) ? 'light' : 'dark'
    const resolve = (value: string | null) =>
      value?.replace(/var\((--[\w-]+)\)/g, (_, name: string) => TOKENS[theme][name] ?? `var(${name})`) ?? ''
    return {
      getPropertyValue(property: string) {
        if (property === 'color') return resolve((el as HTMLElement).style?.color ?? '')
        if (property === 'font-family') return 'Inter, sans-serif'
        return resolve(el.getAttribute(property))
      },
    }
  }
}

const SVG_NS = 'http://www.w3.org/2000/svg'

/** `value`, or a failed test when it is missing. */
function present<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new Error('expected a value')
  return value
}

const chartSvgOf = (body: HTMLElement) => present(findChartSvg(body))

/** A chart body like Recharts + ChartLegend render, with an off-chart pattern. */
function mountBody(labels: string[] = ['Passed', 'Failed']): HTMLElement {
  const body = document.createElement('div')
  body.innerHTML = `
    <svg id="page-defs" width="0" height="0"><defs><pattern id="hatch-failed" width="4" height="4"><path d="M0 0L4 4" stroke="var(--chart-series-1)"/></pattern></defs></svg>
    <div class="recharts-wrapper">
      <svg class="recharts-surface" width="600" height="300" viewBox="0 0 600 300">
        <g class="recharts-layer"><path class="recharts-line" d="M0 0L10 10" stroke="var(--chart-series-1)" fill="none" onclick="alert(1)"/></g>
        <rect fill="url(#hatch-failed)" x="1" y="1" width="5" height="5"/>
        <line class="recharts-tooltip-cursor" x1="0" x2="0" y1="0" y2="300" stroke="var(--color-text)"/>
        <a href="https://example.invalid/"><text x="1" y="1" fill="var(--color-text)">tick</text></a>
      </svg>
    </div>
    <ul data-chart-legend=""></ul>`
  const legend = body.querySelector('[data-chart-legend]') as HTMLElement
  for (const label of labels) {
    const li = document.createElement('li')
    const swatch = document.createElementNS(SVG_NS, 'svg')
    swatch.setAttribute('width', '14')
    swatch.setAttribute('height', '14')
    const rect = document.createElementNS(SVG_NS, 'rect')
    rect.setAttribute('fill', 'url(#hatch-failed)')
    swatch.appendChild(rect)
    const span = document.createElement('span')
    span.textContent = label
    li.append(swatch, span)
    legend.appendChild(li)
  }
  document.body.appendChild(body)
  return body
}

const PROVENANCE: ChartProvenance = {
  project: 'Payments',
  releases: [],
  suites: [],
  window: '2026-08-25 – 2026-09-23 UTC (30 days)',
  totals: '12 of 1,500 runs · 3,400 of 99,000 executions',
  generatedAt: '2026-09-23T14:05:09Z',
  appVersion: '1.4.2',
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('findChartSvg / extractLegend', () => {
  it('the chart is the largest <svg>, never a legend swatch or a zero-size defs holder', () => {
    const body = mountBody()
    expect(findChartSvg(body)?.getAttribute('class')).toBe('recharts-surface')
  })

  it('reads the legend the page draws, label and swatch; a hidden series says so', () => {
    const body = mountBody()
    const toggles = document.createElement('div')
    toggles.setAttribute('data-multi-series-legend', '')
    toggles.innerHTML = '<ul><li><button data-legend-hidden="true"><svg width="24" height="10"></svg><span>Main</span></button></li></ul>'
    body.appendChild(toggles)
    expect(extractLegend(body).map((i) => [i.label, i.swatch !== null])).toEqual([
      ['Passed', true],
      ['Failed', true],
      ['Main (hidden)', true],
    ])
  })
})

describe('inlineComputedStyles — an exported SVG has no stylesheet', () => {
  it('writes the RESOLVED paint onto every element; no var(), no class, no handler, no outside link survives', () => {
    const body = mountBody()
    const clone = chartSvgOf(body).cloneNode(true) as SVGSVGElement
    body.appendChild(clone)
    inlineComputedStyles(clone, fakeReadStyle())
    const markup = new XMLSerializer().serializeToString(clone)
    expect(markup).not.toContain('var(')
    expect(markup).not.toContain('class=')
    expect(markup).not.toMatch(/\son\w+=/i)
    expect(markup).not.toContain('example.invalid')
    expect(clone.querySelector('path')?.getAttribute('style')).toContain('stroke:rgb(230, 159, 0)')
  })
})

describe('copyReferencedDefs', () => {
  it('copies a pattern the chart references from elsewhere on the page, so the hatching survives', () => {
    const body = mountBody()
    const clone = chartSvgOf(body).cloneNode(true) as SVGSVGElement
    expect(clone.querySelector('#hatch-failed')).toBeNull()
    copyReferencedDefs(clone, document)
    expect(clone.querySelector('defs #hatch-failed')).not.toBeNull()
  })
})

describe('buildScene + sceneToSvgText', () => {
  const parse = (text: string) => new DOMParser().parseFromString(text, 'image/svg+xml')

  it('a standalone SVG: correct size and viewBox, title, chart, legend and footer, all in resolved colours', () => {
    const body = mountBody()
    const scene = buildScene(
      { title: 'Pass rate', body, source: { kind: 'svg', svg: chartSvgOf(body) }, provenance: PROVENANCE },
      fakeReadStyle(),
      measure,
    )
    const doc = parse(sceneToSvgText(scene, 'Pass rate'))
    const root = doc.documentElement
    expect(root.getAttribute('width')).toBe(String(scene.layout.width))
    expect(root.getAttribute('height')).toBe(String(scene.layout.height))
    expect(root.getAttribute('viewBox')).toBe(`0 0 ${scene.layout.width} ${scene.layout.height}`)
    const texts = (role: string) => Array.from(doc.querySelectorAll(`[data-export-role="${role}"]`)).map((t) => t.textContent)
    expect(texts('title')).toEqual(['Pass rate'])
    expect(texts('legend')).toEqual(['Passed', 'Failed'])
    expect(texts('footer').join(' ')).toContain('Project: Payments')
    expect(texts('footer').join(' ')).toContain('TestLookup 1.4.2')
    // The chart is inside, sized as on screen, with the pattern it needs and no pointer cursor.
    const chart = present(root.querySelector('svg'))
    expect(chart.getAttribute('width')).toBe('600')
    expect(chart.getAttribute('viewBox')).toBe('0 0 600 300')
    expect(root.querySelector('#hatch-failed')).not.toBeNull()
    expect(sceneToSvgText(scene, 'Pass rate')).not.toContain('var(')
    expect(root.querySelector('rect')?.getAttribute('fill')).toBe('rgb(24, 29, 37)')
    // The off-screen host is gone.
    expect(document.querySelectorAll('[aria-hidden="true"][style*="-100000px"]')).toHaveLength(0)
  })

  it('removes the hover cursor: the export shows the data, not where the pointer was', () => {
    const body = mountBody()
    const scene = buildScene(
      { title: 't', body, source: { kind: 'svg', svg: chartSvgOf(body) }, provenance: null },
      fakeReadStyle(),
      measure,
    )
    expect(scene.chart.kind === 'svg' && scene.chart.svg.querySelector('line')).toBeNull()
  })

  it('"Light background" resolves the chart and the frame in the light theme without touching the page', () => {
    const body = mountBody()
    document.documentElement.setAttribute('data-theme', 'signal')
    const scene = buildScene(
      { title: 't', body, source: { kind: 'svg', svg: chartSvgOf(body) }, provenance: null, light: true },
      fakeReadStyle(),
      measure,
    )
    expect(scene.palette.background).toBe('rgb(255, 255, 255)')
    expect(scene.chart.kind === 'svg' && scene.chart.svg.querySelector('path')?.getAttribute('style')).toContain(
      'stroke:rgb(189, 130, 0)',
    )
    expect(document.documentElement.getAttribute('data-theme')).toBe('signal')
    // The id is a real theme.
    expect(THEMES.some((t) => t.id === LIGHT_THEME && /light/i.test(t.hint))).toBe(true)
  })

  it.each([
    '<img src=x onerror=alert(1)>',
    '"><script>alert(1)</script>',
    "'/><foreignObject><div xmlns=\"http://www.w3.org/1999/xhtml\">x</div></foreignObject>",
    ']]><!--',
  ])('a hostile name (%j) in the title, legend and footer stays literal text', (name) => {
    const body = mountBody([name])
    const scene = buildScene(
      {
        title: name,
        body,
        source: { kind: 'svg', svg: chartSvgOf(body) },
        provenance: { ...PROVENANCE, project: name, suites: [name], releases: [name] },
      },
      fakeReadStyle(),
      (text) => Array.from(text).length * 2, // wide enough that nothing wraps mid-name
    )
    const doc = parse(sceneToSvgText(scene, name))
    expect(doc.querySelector('parsererror')).toBeNull()
    const all = Array.from(doc.getElementsByTagName('*'))
    expect(all.filter((el) => /^(img|script|foreignObject|div|iframe)$/i.test(el.localName))).toEqual([])
    for (const el of all) for (const attr of Array.from(el.attributes)) expect(attr.name).not.toMatch(/^on/i)
    expect(doc.querySelector('title')?.textContent).toBe(name)
    expect(doc.querySelector('[data-export-role="title"]')?.textContent).toBe(name)
    expect(doc.querySelector('[data-export-role="legend"]')?.textContent).toBe(name)
    expect(Array.from(doc.querySelectorAll('[data-export-role="footer"]')).map((t) => t.textContent).join(' ')).toContain(name)
  })

  // Review A9: ESC (an ANSI colour code), U+FFFE and an unpaired surrogate
  // are not XML characters at all. XMLSerializer writes them raw, the .svg
  // does not parse, and the PNG's <img> of the same markup fails to load.
  const CONTROL_NAME = 'suite\u001b[31mred\u001b[0m \uFFFE\uD800 end'
  const CONTROL_SAFE = 'suite\uFFFD[31mred\uFFFD[0m \uFFFD\uFFFD end'
  /** The code points in `text` that XML 1.0 cannot hold — by number: a regex of control characters is a lint error. */
  const xmlIllegal = (text: string) =>
    Array.from(text)
      .map((ch) => ch.codePointAt(0) ?? 0)
      .filter((cp) => (cp < 0x20 && cp !== 0x09 && cp !== 0x0a && cp !== 0x0d) || cp === 0xfffe || cp === 0xffff || (cp >= 0xd800 && cp <= 0xdfff))

  it('a name with XML-illegal characters: the SVG still parses, the text stays readable (U+FFFD)', () => {
    const body = mountBody([CONTROL_NAME])
    const tick = present(chartSvgOf(body).querySelector('text'))
    tick.textContent = CONTROL_NAME
    tick.setAttribute('aria-label', CONTROL_NAME)
    const scene = buildScene(
      {
        title: CONTROL_NAME,
        body,
        source: { kind: 'svg', svg: chartSvgOf(body) },
        provenance: { ...PROVENANCE, project: CONTROL_NAME, suites: [CONTROL_NAME] },
      },
      fakeReadStyle(),
      (text) => Array.from(text).length * 2,
    )
    const text = sceneToSvgText(scene, CONTROL_NAME)
    expect(xmlIllegal(text)).toEqual([])
    const doc = parse(text)
    expect(doc.querySelector('parsererror')).toBeNull()
    expect(doc.querySelector('title')?.textContent).toBe(CONTROL_SAFE)
    expect(doc.querySelector('[data-export-role="title"]')?.textContent).toBe(CONTROL_SAFE)
    expect(doc.querySelector('[data-export-role="legend"]')?.textContent).toBe(CONTROL_SAFE)
    expect(Array.from(doc.querySelectorAll('[data-export-role="footer"]')).map((t) => t.textContent).join(' ')).toContain(
      CONTROL_SAFE,
    )
    // The chart clone — its text and its attributes — too.
    const chart = present(doc.documentElement.querySelector('svg'))
    expect(chart.querySelector('text')?.textContent).toBe(CONTROL_SAFE)
    expect(chart.querySelector('text')?.getAttribute('aria-label')).toBe(CONTROL_SAFE)
  })

  it('the PNG path: the chart and swatch images it loads are well-formed markup too', () => {
    const body = mountBody([CONTROL_NAME])
    present(chartSvgOf(body).querySelector('text')).textContent = CONTROL_NAME
    const scene = buildScene(
      { title: CONTROL_NAME, body, source: { kind: 'svg', svg: chartSvgOf(body) }, provenance: PROVENANCE },
      fakeReadStyle(),
      measure,
    )
    const images = [scene.chart.kind === 'svg' ? scene.chart.svg : null, ...scene.swatches]
    // (Not re-parsed here: jsdom's serializer doubles the clone's explicit
    // xmlns, which a browser does not. The e2e export spec loads it for real.)
    const markup = images.map((svg) => (svg ? new XMLSerializer().serializeToString(svg) : '')).join('\n')
    expect(xmlIllegal(markup)).toEqual([])
    expect(markup).toContain(CONTROL_SAFE)
    // …and every line it paints is the safe text.
    expect(scene.layout.text.find((line) => line.role === 'title')?.text).toBe(CONTROL_SAFE)
  })

  it('xmlSafeText keeps every legal character, astral ones included', () => {
    const legal = 'tab\there\nline\rcr 支払い 😀 \u00A0\uE000\uFFFD'
    expect(xmlSafeText(legal)).toBe(legal)
    expect(xmlSafeText('\u0000\u0008\u000B\u001F\uFFFF\uDC00')).toBe('\uFFFD'.repeat(6))
  })

  it('drops elements that could run or fetch (script, style, foreignObject, animation) from the clone', () => {
    const body = mountBody()
    const svg = chartSvgOf(body)
    const set = document.createElementNS(SVG_NS, 'set')
    set.setAttribute('attributeName', 'href')
    svg.appendChild(set)
    for (const name of ['script', 'style', 'foreignObject', 'animate']) svg.appendChild(document.createElementNS(SVG_NS, name))
    const scene = buildScene({ title: 't', body, source: { kind: 'svg', svg }, provenance: null }, fakeReadStyle(), measure)
    const doc = parse(sceneToSvgText(scene, 't'))
    for (const name of ['script', 'style', 'foreignObject', 'animate', 'set']) {
      expect(doc.getElementsByTagNameNS(SVG_NS, name), name).toHaveLength(0)
    }
  })

  it('a gradient stop coloured by a token keeps its resolved colour (review N2)', () => {
    const body = mountBody()
    const svg = chartSvgOf(body)
    const gradient = document.createElementNS(SVG_NS, 'linearGradient')
    gradient.id = 'fade'
    const stop = document.createElementNS(SVG_NS, 'stop')
    stop.setAttribute('stop-color', 'var(--chart-series-1)')
    gradient.appendChild(stop)
    svg.appendChild(gradient)
    // A browser computes stop-color for EVERY element (initial: black), not just for stops.
    const base = fakeReadStyle()
    const readStyle: ReadStyle = (el) => ({
      getPropertyValue: (property: string) =>
        base(el).getPropertyValue(property) || (property === 'stop-color' ? 'rgb(0, 0, 0)' : ''),
    })
    const scene = buildScene({ title: 't', body, source: { kind: 'svg', svg }, provenance: null }, readStyle, measure)
    const copied = scene.chart.kind === 'svg' ? scene.chart.svg.querySelector('stop') : null
    expect(copied?.getAttribute('style')).toContain('stop-color:rgb(230, 159, 0)')
    // Only a stop carries it: the other elements do not grow a stop-color.
    expect(scene.chart.kind === 'svg' && scene.chart.svg.querySelector('path')?.getAttribute('style')).not.toContain('stop-color')
  })

  it('an engine (canvas) chart is embedded as its own image', () => {
    const body = mountBody([])
    const scene = buildScene(
      {
        title: 't',
        body,
        source: { kind: 'raster', dataUrl: 'data:image/png;base64,AAAA', width: 400, height: 200 },
        provenance: PROVENANCE,
      },
      fakeReadStyle(),
      measure,
    )
    const doc = parse(sceneToSvgText(scene, 't'))
    const image = present(doc.querySelector('image'))
    expect(image.getAttribute('href')).toBe('data:image/png;base64,AAAA')
    // Older SVG readers know only xlink:href (review N6).
    expect(image.getAttributeNS('http://www.w3.org/1999/xlink', 'href')).toBe('data:image/png;base64,AAAA')
    expect(image.getAttribute('width')).toBe('400')
  })
})

/** Records every call a 2D context receives. */
function recorder() {
  const calls: { op: string; args: unknown[]; fillStyle?: unknown; font?: string }[] = []
  const ctx = {
    fillStyle: '' as unknown,
    strokeStyle: '' as unknown,
    font: '',
    textBaseline: 'alphabetic' as CanvasTextBaseline,
    lineWidth: 1,
  } as PaintContext
  for (const op of ['scale', 'fillRect', 'drawImage', 'fillText', 'beginPath', 'moveTo', 'lineTo', 'stroke'] as const) {
    ;(ctx as unknown as Record<string, unknown>)[op] = (...args: unknown[]) =>
      calls.push({ op, args, fillStyle: ctx.fillStyle, font: ctx.font })
  }
  return { ctx, calls }
}

describe('paintScene — the PNG', () => {
  it('is drawn at 2× and the FOOTER is painted into the pixels, inside its band, non-blank', () => {
    const body = mountBody()
    const scene = buildScene(
      { title: 'Pass rate', body, source: { kind: 'svg', svg: chartSvgOf(body) }, provenance: PROVENANCE },
      fakeReadStyle(),
      measure,
    )
    expect(EXPORT_PIXEL_RATIO).toBe(2)
    expect(canvasSize(scene.layout)).toEqual({ width: scene.layout.width * 2, height: scene.layout.height * 2 })

    const { ctx, calls } = recorder()
    const chartImage = {} as CanvasImageSource
    paintScene(ctx, scene, { chart: chartImage, swatches: scene.swatches.map(() => ({}) as CanvasImageSource) })
    expect(calls[0]).toMatchObject({ op: 'scale', args: [2, 2] })
    // Background first, over the whole image.
    expect(calls[1]).toMatchObject({ op: 'fillRect', args: [0, 0, scene.layout.width, scene.layout.height] })
    // The chart at its box.
    const { chart } = scene.layout
    expect(calls.find((c) => c.op === 'drawImage' && c.args[0] === chartImage)?.args).toEqual([
      chartImage,
      chart.x,
      chart.y,
      chart.width,
      chart.height,
    ])
    const texts = calls.filter((c) => c.op === 'fillText')
    const footer = texts.filter((c) => (c.args[2] as number) > scene.layout.footer.y)
    expect(footer.length).toBeGreaterThanOrEqual(4)
    for (const call of footer) {
      expect(String(call.args[0]).trim()).not.toBe('')
      expect(call.args[2] as number).toBeLessThanOrEqual(scene.layout.height)
      expect(call.fillStyle).not.toBe(scene.palette.background)
    }
    expect(footer.map((c) => c.args[0]).join(' ')).toContain('Generated 2026-09-23 14:05 UTC')
    // Title and legend are in the pixels too.
    expect(texts.map((c) => c.args[0])).toEqual(expect.arrayContaining(['Pass rate', 'Passed', 'Failed']))
  })
})

describe('exportChartImage in a browser without canvas', () => {
  it('rejects with a message (the menu shows it), never throws synchronously', async () => {
    const body = mountBody()
    const { exportChartImage } = await import('./chartImage')
    const spy = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    await expect(
      exportChartImage({ title: 't', body, source: { kind: 'svg', svg: chartSvgOf(body) }, provenance: null }, 'png'),
    ).rejects.toThrow(/cannot draw/)
    spy.mockRestore()
  })
})
