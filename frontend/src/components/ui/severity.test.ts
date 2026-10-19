/**
 * W29a UIX-07: the ONE severity map. Every vocabulary the pages and the API use
 * maps onto P0–P3, and the styles name only the UI meaning tokens (a medium
 * or low severity is never orange or passed-green).
 */
import { describe, expect, it } from 'vitest'
import { SEVERITY_ORDER, SEVERITY_STYLE, severityLevel, severityStyle } from './severity'

describe('severityLevel', () => {
  it.each([
    ['P0', 'P0'], ['p0', 'P0'], ['blocker', 'P0'], ['CRITICAL', 'P0'], [' Critical ', 'P0'],
    ['P1', 'P1'], ['high', 'P1'], ['MAJOR', 'P1'],
    ['P2', 'P2'], ['medium', 'P2'], ['moderate', 'P2'], ['NORMAL', 'P2'],
    ['p3', 'P3'], ['LOW', 'P3'], ['minor', 'P3'], ['trivial', 'P3'],
  ])('%j → %s', (raw, level) => {
    expect(severityLevel(raw)).toBe(level)
  })

  it('anything else names no level', () => {
    for (const other of ['P4', 'urgent', 'unscored', '', null, undefined, 'constructor']) {
      expect(severityLevel(other), String(other)).toBeNull()
      expect(severityStyle(other), String(other)).toBeNull()
    }
  })
})

describe('SEVERITY_STYLE', () => {
  it('runs P0 → P3 in order, one tone each', () => {
    expect(SEVERITY_ORDER).toEqual(['P0', 'P1', 'P2', 'P3'])
    expect(SEVERITY_ORDER.map((l) => SEVERITY_STYLE[l].tone)).toEqual(['danger', 'warning', 'info', 'neutral'])
  })

  it('names only --ui-* / --color-* tokens: never a status hue, never a literal', () => {
    for (const level of SEVERITY_ORDER) {
      const { tone: _tone, ...colours } = SEVERITY_STYLE[level]
      for (const [part, value] of Object.entries(colours)) {
        const names = [...value.matchAll(/var\((--[\w-]+)\)/g)].map((m) => m[1])
        expect(names.length, `${level}.${part}`).toBeGreaterThan(0)
        for (const name of names) expect(name, `${level}.${part}`).toMatch(/^--(ui|color)-/)
        expect(value, `${level}.${part}`).not.toMatch(/#|\brgba?\(|--status-/)
      }
    }
  })

  it("the tints are the Defects page's 15 % / 30 % and the text is the ink", () => {
    expect(SEVERITY_STYLE.P0).toEqual({
      tone: 'danger',
      fg: 'var(--ui-danger-ink)',
      bg: 'color-mix(in srgb, var(--ui-danger) 15%, transparent)',
      bd: 'color-mix(in srgb, var(--ui-danger) 30%, transparent)',
      dot: 'var(--ui-danger)',
    })
    expect(SEVERITY_STYLE.P2.fg).toBe('var(--ui-info-ink)')
    expect(SEVERITY_STYLE.P3.dot).toBe('var(--color-text-muted)')
    expect(severityStyle('minor')).toBe(SEVERITY_STYLE.P3)
  })
})
