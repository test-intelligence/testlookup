/**
 * The Coverage verdict's Healthy sentence. A Healthy composite can still carry a dimension below target (the
 * W23a demo: "Run cadence 0 of 1 expected run" under "all signals nominal … all above target"), so the verdict
 * names those dimensions instead of claiming every dimension is above target.
 */

/** A scored dimension's label and tone (`good` is at or above target). */
export interface VerdictDimension {
  label: string
  tone: 'good' | 'warn' | 'bad'
}

/** The dimensions below target, by label, in their display order. */
export function dimensionsBelowTarget(dimensions: readonly VerdictDimension[]): string[] {
  return dimensions.filter((d) => d.tone !== 'good').map((d) => d.label)
}

/** The Healthy verdict's sentence: every dimension above target, or the ones that are not, named. */
export function healthyLede(suiteCount: number, below: readonly string[]): string {
  const solid = `Coverage signal is solid across ${suiteCount} suite${suiteCount === 1 ? '' : 's'}.`
  if (below.length === 0) return `${solid} Pass rate, tagging, cadence, and breadth are all above target.`
  const names = below.length === 1 ? below[0] : `${below.slice(0, -1).join(', ')} and ${below[below.length - 1]}`
  return `${solid} ${names} ${below.length === 1 ? 'is' : 'are'} below target.`
}

/** The Healthy verdict's short action beside the band. */
export function healthyAction(below: readonly string[]): string {
  return below.length === 0 ? 'all signals nominal' : `${below.length} dimension${below.length === 1 ? '' : 's'} below target`
}
