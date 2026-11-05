/**
 * W32 / W29e F-8: line heights are the utilities' RATIOS (`architecture/FRONTEND.md`, "Line heights"). A px
 * line-height (`leading-` + a bracketed px value) keeps its desk value when presentation mode raises the text tokens
 * (`--text-xs` 12 → 16 px), so a raised line is clipped in its own box. The release calendar's bar was the last one;
 * its lanes now follow `--text-xs` instead (`ReleaseCalendar.tsx`).
 */
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

const PX_LINE_HEIGHT = /leading-\[\d+(\.\d+)?px\]/

describe('no px line-height in the SPA', () => {
  it('reads the sources (the glob is not empty)', () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100)
    expect(Object.keys(SOURCES)).toContain('/src/components/releases/ReleaseCalendar.tsx')
  })

  it('no .ts / .tsx file under src/ uses a bracketed px line-height', () => {
    const offenders = Object.entries(SOURCES)
      .filter(([, text]) => PX_LINE_HEIGHT.test(text))
      .map(([path, text]) => `${path}: ${PX_LINE_HEIGHT.exec(text)?.[0]}`)
    expect(offenders, 'use leading-tight / snug / normal / relaxed (a ratio) instead').toEqual([])
  })
})
