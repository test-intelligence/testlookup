/**
 * The ONE severity map (W29a UIX-07): P0–P3 on the UI meaning tokens, read by
 * every severity / priority chip, dot and legend (defects, the intake form,
 * approvals, test-case priority, investigation clusters, my failures, the
 * promotion form). Every vocabulary the API and the pages use maps onto it.
 *
 * The tint (15 %) and border (30 %) are the Defects page's, so its chips move
 * only by their text, which reads the `-ink` token (>= 4.5:1 on the tint in
 * every theme). P2 is the INFO hue and P3 the neutral: a medium or low
 * severity is never orange or passed-green. Long labels ("P0 — Blocker") stay
 * page copy; a null level keeps the page's own neutral "unscored" look.
 */

export type SeverityLevel = 'P0' | 'P1' | 'P2' | 'P3'

export const SEVERITY_ORDER: readonly SeverityLevel[] = ['P0', 'P1', 'P2', 'P3']

const LEVEL_BY_WORD: Readonly<Record<string, SeverityLevel>> = {
  p0: 'P0', blocker: 'P0', critical: 'P0',
  p1: 'P1', high: 'P1', major: 'P1',
  p2: 'P2', medium: 'P2', moderate: 'P2', normal: 'P2',
  p3: 'P3', low: 'P3', minor: 'P3', trivial: 'P3',
}

/** `'critical'` / `'P0'` / `' High '` → a level; anything else → `null`. */
export function severityLevel(raw: string | null | undefined): SeverityLevel | null {
  const word = (raw ?? '').trim().toLowerCase()
  return Object.prototype.hasOwnProperty.call(LEVEL_BY_WORD, word) ? LEVEL_BY_WORD[word] : null
}

export interface SeverityStyle {
  tone: 'danger' | 'warning' | 'info' | 'neutral'
  /** Text on the tint. */
  fg: string
  bg: string
  bd: string
  /** A dot, a legend swatch, a bar. */
  dot: string
}

export const SEVERITY_STYLE: Readonly<Record<SeverityLevel, SeverityStyle>> = {
  P0: {
    tone: 'danger',
    fg: 'var(--ui-danger-ink)',
    bg: 'color-mix(in srgb, var(--ui-danger) 15%, transparent)',
    bd: 'color-mix(in srgb, var(--ui-danger) 30%, transparent)',
    dot: 'var(--ui-danger)',
  },
  P1: {
    tone: 'warning',
    fg: 'var(--ui-warning-ink)',
    bg: 'color-mix(in srgb, var(--ui-warning) 15%, transparent)',
    bd: 'color-mix(in srgb, var(--ui-warning) 30%, transparent)',
    dot: 'var(--ui-warning)',
  },
  P2: {
    tone: 'info',
    fg: 'var(--ui-info-ink)',
    bg: 'color-mix(in srgb, var(--ui-info) 15%, transparent)',
    bd: 'color-mix(in srgb, var(--ui-info) 30%, transparent)',
    dot: 'var(--ui-info)',
  },
  P3: {
    tone: 'neutral',
    fg: 'var(--color-text-muted)',
    bg: 'var(--color-bg-secondary)',
    bd: 'var(--color-border)',
    dot: 'var(--color-text-muted)',
  },
}

/** The style of a raw severity, or `null` when it names no level. */
export function severityStyle(raw: string | null | undefined): SeverityStyle | null {
  const level = severityLevel(raw)
  return level ? SEVERITY_STYLE[level] : null
}
