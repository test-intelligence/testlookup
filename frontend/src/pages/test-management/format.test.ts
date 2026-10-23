/**
 * W29b (the W29a follow-up): a test case's priority pill is the ONE severity
 * map's look. `PRIORITY_COLORS` is a class map (`StatusPill` takes classes), so
 * each entry must spell exactly `SEVERITY_STYLE[level]`'s text, tint and border
 * as Tailwind arbitrary values — medium is P2, the info hue, never the skipped
 * gold it was.
 */
import { describe, expect, it } from 'vitest'
import { SEVERITY_STYLE, severityLevel, type SeverityLevel } from '@/components/ui/severity'
import { PRIORITY_COLORS } from './format'

/** A CSS value as a Tailwind arbitrary value: no space after a comma, every other space an underscore. */
function arbitrary(value: string): string {
  return value.replace(/,\s+/g, ',').replace(/\s+/g, '_')
}

/** The classes a pill of `level` must carry: the map's ink, tint and border, and a border to draw it. */
function expectedClasses(level: SeverityLevel): string[] {
  const s = SEVERITY_STYLE[level]
  return [`bg-[${arbitrary(s.bg)}]`, `text-[${arbitrary(s.fg)}]`, 'border', `border-[${arbitrary(s.bd)}]`].sort()
}

describe('PRIORITY_COLORS — the one severity map (W29b)', () => {
  it.each([
    ['critical', 'P0'],
    ['high', 'P1'],
    ['medium', 'P2'],
    ['low', 'P3'],
  ] as const)('%s is %s: exactly that level’s text, tint and border', (priority, level) => {
    expect(severityLevel(priority)).toBe(level)
    expect(PRIORITY_COLORS[priority].split(/\s+/).sort()).toEqual(expectedClasses(level))
  })

  it('maps every priority the API sends and nothing else', () => {
    expect(Object.keys(PRIORITY_COLORS).sort()).toEqual(['critical', 'high', 'low', 'medium'])
  })

  it('never paints a priority with a test-result hue (medium was --status-skipped)', () => {
    for (const [priority, classes] of Object.entries(PRIORITY_COLORS)) {
      expect(classes, priority).not.toMatch(/--status-/)
    }
    expect(PRIORITY_COLORS.medium).toContain('--ui-info')
  })
})
