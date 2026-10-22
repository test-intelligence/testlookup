/**
 * The five result statuses' fixed encoding (label, icon, pattern, decal, dash; no colour) and the flaky marker.
 *
 * Its own module so the status vocabulary (`components/ui/statusVocabulary`, on every page from the first render)
 * reads the labels without pulling the rest of the chart tokens onto the critical path: W29b's chart text style
 * grew `tokens.ts`, and the eager bundle went 129 bytes over its budget (`scripts/check-bundle-budget.mjs`).
 * `tokens.ts` re-exports both, so the kit keeps importing them from there.
 */
import type { VizStatus } from '@/lib/viz/contracts'

export type DecalKind = 'solid' | 'diagonal' | 'crosshatch' | 'dashes' | 'dots'

export interface StatusEncoding {
  label: string
  /** lucide-react icon name. */
  icon: string
  /**
   * `<pattern id>` suffix for SVG fills (Recharts). Each chart prefixes it with
   * its own id (`patterns.tsx`), so two charts on a page never share a pattern.
   */
  patternId: string
  /** Decal id; `echartsDecal()` turns it into an ECharts decal object, `patterns.tsx` into an SVG pattern. */
  decal: DecalKind
  /** `stroke-dasharray` for a line drawn in this status; `undefined` = solid. */
  dash: string | undefined
}

export const STATUS_ENCODING: Record<VizStatus, StatusEncoding> = {
  passed: { label: 'Passed', icon: 'CircleCheck', patternId: 'chart-pattern-passed', decal: 'solid', dash: undefined },
  failed: { label: 'Failed', icon: 'CircleX', patternId: 'chart-pattern-failed', decal: 'diagonal', dash: '7 3' },
  broken: { label: 'Broken', icon: 'CircleAlert', patternId: 'chart-pattern-broken', decal: 'crosshatch', dash: '9 3 2 3' },
  skipped: { label: 'Skipped', icon: 'CircleSlash', patternId: 'chart-pattern-skipped', decal: 'dashes', dash: '2 3' },
  unknown: { label: 'Unknown', icon: 'CircleHelp', patternId: 'chart-pattern-unknown', decal: 'dots', dash: '1 4' },
}

/** Flaky is an attribute marker over a status, not a status. */
export const FLAKY_MARKER = {
  label: 'Flaky',
  icon: 'Shuffle',
  patternId: 'chart-pattern-flaky-marker',
  token: '--status-flaky',
} as const
