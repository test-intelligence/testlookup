/**
 * W29a UIX-01: the Playwright theme lists name the themes of the registry.
 *
 * A Playwright file cannot import `store/themeStore` (it touches `document` at
 * import), so the specs keep their own lists; this pins those lists to the
 * registry by reading the spec sources, so a theme added to the picker cannot
 * silently skip the axe gate, the primitives smoke or the edge-contrast check.
 *
 * - `tests/lib/axe-gate.ts` `ALL_THEMES` and `tests/ci-e2e/primitives.spec.ts`
 *   `THEME_IDS`: every registry id, in picker order.
 * - `tests/ci-e2e/chart-zoom.spec.ts`, the strip-outline loop: every registry id.
 * - `tests/lib/production-pages.ts` shoots a REPRESENTATIVE set (contract §4.4,
 *   D-7): its `Theme` union holds only registry ids and includes the default
 *   (signal) and both light themes, and `THEMES_WITH_PAPER` is signal, lab, paper.
 */
import { beforeAll, describe, expect, it } from 'vitest'
import { LIGHT_THEMES, THEME_IDS } from '@/store/themeStore'
import { readSourceFile } from './readSourceFile'

const sources: Record<string, string> = {}
const FILES = [
  'tests/lib/axe-gate.ts',
  'tests/ci-e2e/primitives.spec.ts',
  'tests/ci-e2e/chart-zoom.spec.ts',
  'tests/lib/production-pages.ts',
] as const

beforeAll(async () => {
  for (const file of FILES) sources[file] = await readSourceFile(file)
})

/** The quoted strings inside the first `[...]` after the `=` that follows `marker` (past any `Theme[]` annotation). */
function arrayAfter(text: string, marker: RegExp): string[] {
  const at = text.search(marker)
  expect(at, `${marker} in the source`).toBeGreaterThanOrEqual(0)
  const open = text.indexOf('[', text.indexOf('=', at))
  const close = text.indexOf(']', open)
  return [...text.slice(open, close).matchAll(/'([\w-]+)'/g)].map((m) => m[1])
}

describe('the Playwright theme lists follow the registry (waiting for G-E until its edits land)', () => {
  it('axe-gate ALL_THEMES is every registry id, in picker order', () => {
    expect(arrayAfter(sources['tests/lib/axe-gate.ts'], /export const ALL_THEMES\s*=/)).toEqual([...THEME_IDS])
  })

  it('the primitives smoke THEME_IDS is every registry id, in picker order', () => {
    expect(arrayAfter(sources['tests/ci-e2e/primitives.spec.ts'], /const THEME_IDS\s*=/)).toEqual([...THEME_IDS])
  })

  it("chart-zoom's strip-outline loop covers every registry id", () => {
    const text = sources['tests/ci-e2e/chart-zoom.spec.ts']
    const loop = text.slice(0, text.indexOf("the strip's outline has at least 3:1"))
    const at = loop.lastIndexOf('for (const theme of [')
    expect(at, 'the strip-outline theme loop').toBeGreaterThanOrEqual(0)
    const list = [...loop.slice(at, loop.indexOf(']', at)).matchAll(/'([\w-]+)'/g)].map((m) => m[1])
    expect([...list].sort()).toEqual([...THEME_IDS].sort())
  })

  it('the production visual suite: a Theme union of registry ids with the default and both light themes, and THEMES_WITH_PAPER', () => {
    const text = sources['tests/lib/production-pages.ts']
    const union = /export type Theme\s*=\s*([^\n]+)/.exec(text)?.[1] ?? ''
    const members = [...union.matchAll(/'([\w-]+)'/g)].map((m) => m[1])
    expect(members.length).toBeGreaterThan(0)
    for (const id of members) expect(THEME_IDS as readonly string[], id).toContain(id)
    for (const id of ['signal', ...LIGHT_THEMES]) expect(members, id).toContain(id)
    expect(arrayAfter(text, /export const THEMES_WITH_PAPER\b/)).toEqual(['signal', 'lab', 'paper'])
  })
})
