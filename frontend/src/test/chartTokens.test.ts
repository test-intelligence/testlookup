/**
 * VIZ-102 — the chart tokens in `src/index.css`, measured per theme.
 *
 * Parsed from the stylesheet itself (not from a copy of the values), so a
 * colour edited in index.css is what gets measured. A theme's value is the
 * merge of every rule whose selector names `[data-theme="<id>"]` — the dark
 * themes share one palette rule and keep their own midpoint and grid.
 *
 * WCAG 1.4.11: a graphical mark needs 3:1 against what it sits on — the card.
 */
import { beforeAll, describe, expect, it } from 'vitest'
import { THEMES } from '@/store/themeStore'
import { readSourceFile } from './readSourceFile'

const SERIES = Array.from({ length: 8 }, (_, i) => `chart-series-${i + 1}`)
const SEQ = Array.from({ length: 7 }, (_, i) => `chart-seq-${i + 1}`)
const DIV = Array.from({ length: 7 }, (_, i) => `chart-div-${i + 1}`)
const STATUS_MARKS = ['status-passed', 'status-failed', 'status-broken', 'status-skipped', 'status-unknown', 'status-flaky']
const REQUIRED = [...SERIES, ...SEQ, ...DIV, 'chart-grid', 'chart-axis', ...STATUS_MARKS, 'color-bg-card']
const MIN_MARK_CONTRAST = 3

/** Every custom property each theme resolves, merged across the rules that name it. */
function themeTokens(css: string): Map<string, Map<string, string>> {
  const out = new Map<string, Map<string, string>>()
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
  for (const rule of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const ids = [...rule[1].matchAll(/\[data-theme="([\w-]+)"\]/g)].map((m) => m[1])
    if (ids.length === 0) continue
    for (const decl of rule[2].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
      for (const id of ids) {
        if (!out.has(id)) out.set(id, new Map())
        out.get(id)?.set(decl[1], decl[2].trim().toLowerCase())
      }
    }
  }
  return out
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

let tokens: Map<string, Map<string, string>>

beforeAll(async () => {
  tokens = themeTokens(await readSourceFile('src/index.css'))
})

const THEME_IDS = THEMES.map((t) => t.id)

describe('chart tokens in index.css', () => {
  it('parses all six themes (a parser that finds nothing must fail, not pass)', () => {
    expect(THEME_IDS).toHaveLength(6)
    expect([...tokens.keys()].sort()).toEqual([...THEME_IDS].sort())
  })

  it.each(THEME_IDS)('%s defines every chart token as a 6-digit hex', (id) => {
    const theme = tokens.get(id)
    for (const name of REQUIRED) {
      expect(theme?.get(name), `${id} --${name}`).toMatch(/^#[0-9a-f]{6}$/)
    }
  })

  it.each(THEME_IDS)('%s: every series and status mark has >= 3:1 against the card', (id) => {
    const theme = tokens.get(id)
    const card = theme?.get('color-bg-card') ?? ''
    const failures = [...SERIES, ...STATUS_MARKS]
      .map((name) => ({ name, value: theme?.get(name) ?? '', ratio: contrast(theme?.get(name) ?? '', card) }))
      .filter((m) => !(m.ratio >= MIN_MARK_CONTRAST))
      .map((m) => `--${m.name} ${m.value} is ${m.ratio.toFixed(2)}:1 on ${card}`)
    expect(failures).toEqual([])
  })

  it.each(THEME_IDS)('%s: the axis colour is readable text (>= 4.5:1)', (id) => {
    const theme = tokens.get(id)
    expect(contrast(theme?.get('chart-axis') ?? '', theme?.get('color-bg-card') ?? '')).toBeGreaterThanOrEqual(4.5)
  })

  it.each(THEME_IDS)('%s: eight distinct series colours (never a recycled hue)', (id) => {
    const values = SERIES.map((name) => tokens.get(id)?.get(name))
    expect(new Set(values).size).toBe(SERIES.length)
  })

  it.each(THEME_IDS)('%s: the sequential ramp ends on its most salient colour ("more")', (id) => {
    const theme = tokens.get(id)
    const card = theme?.get('color-bg-card') ?? ''
    const ratios = SEQ.map((name) => contrast(theme?.get(name) ?? '', card))
    // Salience = distance from the card, so on the light theme the ramp is reversed.
    expect(ratios[ratios.length - 1]).toBeGreaterThan(ratios[0])
  })

  it.each(THEME_IDS)('%s: EVERY sequential step, the first included, is >= 3:1 against the card', (id) => {
    // A no-data heatmap cell is the card plus a hatch; if a ramp step sat
    // near the card, that cell and the lowest step would read the same.
    const theme = tokens.get(id)
    const card = theme?.get('color-bg-card') ?? ''
    const failures = SEQ.map((name) => ({ name, value: theme?.get(name) ?? '', ratio: contrast(theme?.get(name) ?? '', card) }))
      .filter((m) => !(m.ratio >= MIN_MARK_CONTRAST))
      .map((m) => `--${m.name} ${m.value} is ${m.ratio.toFixed(2)}:1 on ${card}`)
    expect(failures).toEqual([])
  })

  it.each(THEME_IDS)('%s: the active heatmap cell (text-colour outline + card halo) is >= 3:1 against every step', (id) => {
    // The emphasis ring is --color-text with a --color-bg-card halo: on any
    // step, at least one of the two rings must stand out.
    const theme = tokens.get(id)
    const outline = theme?.get('color-text') ?? ''
    const halo = theme?.get('color-bg-card') ?? ''
    expect(outline, `${id} --color-text`).toMatch(/^#[0-9a-f]{6}$/)
    const failures = SEQ.map((name) => {
      const step = theme?.get(name) ?? ''
      return { name, ratio: Math.max(contrast(outline, step), contrast(halo, step)) }
    })
      .filter((m) => !(m.ratio >= MIN_MARK_CONTRAST))
      .map((m) => `--${m.name}: best ring ${m.ratio.toFixed(2)}:1`)
    expect(failures).toEqual([])
  })

  it.each(THEME_IDS)('%s: the diverging extremes stand out more than its neutral midpoint', (id) => {
    const theme = tokens.get(id)
    const card = theme?.get('color-bg-card') ?? ''
    const mid = contrast(theme?.get('chart-div-4') ?? '', card)
    expect(contrast(theme?.get('chart-div-1') ?? '', card)).toBeGreaterThan(mid)
    expect(contrast(theme?.get('chart-div-7') ?? '', card)).toBeGreaterThan(mid)
  })

  it('shares one categorical palette across the five dark themes', () => {
    const dark = THEME_IDS.filter((id) => id !== 'lab')
    for (const name of [...SERIES, ...SEQ, 'chart-axis', 'status-unknown']) {
      expect(new Set(dark.map((id) => tokens.get(id)?.get(name))).size, name).toBe(1)
    }
  })

  it('keeps the existing status hues unchanged (VIZ-102 adds unknown, it does not repaint)', () => {
    expect(['passed', 'failed', 'broken', 'skipped', 'flaky'].map((s) => tokens.get('signal')?.get(`status-${s}`))).toEqual([
      '#34a06b', '#d1554e', '#cf8542', '#bfa14a', '#9d85d6',
    ])
    expect(['passed', 'failed', 'broken', 'skipped', 'flaky'].map((s) => tokens.get('lab')?.get(`status-${s}`))).toEqual([
      '#1f7a48', '#b3352c', '#b05816', '#91701c', '#6742ad',
    ])
  })

  it('measures what it claims: a planted low-contrast colour fails the ratio', () => {
    // Guards the arithmetic: the signal card against a near-identical grey.
    expect(contrast('#2a303a', '#181d25')).toBeLessThan(MIN_MARK_CONTRAST)
    expect(contrast('#ffffff', '#000000')).toBeCloseTo(21, 5)
  })
})
