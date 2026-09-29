/**
 * Deterministic `DayStrip` fixtures (VIZ-104 K5), one per strip the kit
 * replaces plus the two edge cases a baseline should hold: a dense 90-day
 * window and hostile labels. Every value is a literal and "today" is a fixed
 * day, so a screenshot never changes with the clock.
 *
 * As in `wave2Fixtures.ts`: a build id here is written "build 4181", never
 * with a leading hash — to `check:theme` a hash and three or more hex digits
 * is a colour literal.
 */
import type { DayStripProps } from '../DayStrip'
import { countTones, dayWindow, intensityLevel, type DayStripCell } from '../dayStrip.model'

export const DAY_STRIP_TODAY = '2026-09-28'

/** Executions per day, oldest first; 0 is a day with no runs. Failures on the same days. */
const EXECUTIONS = [0, 12, 30, 0, 0, 4, 55, 18, 0, 22, 7, 0, 0, 0, 0, 0, 9, 41, 3, 0, 26, 60, 0, 14, 8, 0, 33, 5, 17, 2]
const FAILED = [0, 0, 3, 0, 0, 0, 12, 0, 0, 22, 1, 0, 0, 0, 0, 0, 0, 5, 0, 0, 0, 9, 0, 0, 0, 0, 0, 5, 2, 0]

const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

function window30(): { iso: string; executions: number; failed: number }[] {
  return dayWindow(30, DAY_STRIP_TODAY).map((iso, i) => ({ iso, executions: EXECUTIONS[i], failed: FAILED[i] }))
}

/** Trends run cadence (S1): presence, mixed days, a gap edge before the 5-day silence, today. */
export function presenceStrip(): DayStripProps {
  const days = window30()
  const cells = days.map<DayStripCell>(({ iso, executions, failed }, i) => ({
    key: iso,
    label: `${iso} · ${plural(executions, 'execution')}${failed > 0 ? ` (${failed} failed)` : ''}`,
    tone: executions === 0 ? 'none' : failed > 0 ? 'mixed' : 'pass',
    marker: i === days.length - 1 ? 'today' : i === 10 ? 'gap-edge' : undefined,
  }))
  const active = cells.filter((c) => c.tone !== 'none').length
  return {
    mode: 'presence',
    cells,
    gap: 4,
    title: 'Run cadence',
    label: `Run cadence: ${plural(cells.length - active, 'empty day')}, ${plural(active, 'day')} with executions`,
  }
}

/** Coverage run cadence (S2): intensity levels, no failure cue. */
export function intensityStrip(): DayStripProps {
  const cells = window30().map<DayStripCell>(({ iso, executions }, i, all) => ({
    key: iso,
    label: `${iso} · ${plural(executions, 'execution')}`,
    tone: executions === 0 ? 'none' : 'pass',
    level: intensityLevel(executions),
    marker: i === all.length - 1 ? 'today' : undefined,
  }))
  const active = cells.filter((c) => c.tone !== 'none').length
  return {
    mode: 'intensity',
    cells,
    title: 'Run cadence',
    label: `Run cadence over the last ${cells.length} days. ${active} active days, ${cells.length - active} empty days.`,
  }
}

/** FailureAnalysis failure timeline (S3): status, failed days deepened by their failed share. */
export function severityStrip(): DayStripProps {
  const cells = window30().map<DayStripCell>(({ iso, executions, failed }) => ({
    key: iso,
    label: executions === 0 ? `${iso} · no runs` : `${iso} · ${failed > 0 ? `${failed} of ${executions} failed` : 'passing'}`,
    tone: executions === 0 ? 'none' : failed > 0 ? 'fail' : 'pass',
    severity: failed > 0 ? failed / executions : undefined,
  }))
  const failing = cells.filter((c) => c.tone === 'fail').length
  return {
    mode: 'status',
    cells,
    title: 'Failure timeline',
    label: `Failure timeline: ${plural(failing, 'day')} with failures over the last ${cells.length} days.`,
  }
}

/** FailureAnalysis run strip (S4): 14 short cells, no legend (the card prints its own counts). */
export function compactStrip(): DayStripProps {
  const cells = window30()
    .slice(-14)
    .map<DayStripCell>(({ iso, executions, failed }) => ({
      key: iso,
      label: `${iso} · ${executions === 0 ? 'not run' : failed > 0 ? 'failed' : 'passed'}`,
      tone: executions === 0 ? 'none' : failed > 0 ? 'fail' : 'pass',
    }))
  const n = countTones(cells)
  return {
    mode: 'status',
    cells,
    cellHeight: 14,
    legend: false,
    title: 'Run strip',
    label: `Run strip: ${n.fail} failed, ${n.pass} passed, ${n.none} not run.`,
    text: { none: 'Not run' },
  }
}

/** Runs build velocity (S5): cells are builds, 9 real ones padded to 14, the latest marked. */
export function buildStrip(): DayStripProps {
  const kinds = ['pass', 'pass', 'fail', 'pass', 'fail', 'fail', 'pass', 'pass', 'fail'] as const
  const cells: DayStripCell[] = kinds.map((tone, i) => ({
    key: `build ${4173 + i}`,
    label: `build ${4173 + i} · ${tone}`,
    tone,
    marker: i === kinds.length - 1 ? 'today' : undefined,
  }))
  while (cells.length < 14) cells.push({ key: `empty ${cells.length}`, label: 'no build', tone: 'none' })
  const n = countTones(cells)
  return {
    mode: 'status',
    cells,
    gap: 6,
    unit: 'build',
    endLabel: 'Now',
    title: 'Build velocity',
    label: `Build velocity over the last 14 builds: ${n.pass} passed, ${n.fail} failed, ${n.none} no-build cells.`,
    text: { none: 'No build', today: 'Latest build' },
  }
}

/** A 90-day presence window: the dense case (thin cells, the legend row under the most pressure). */
export function denseStrip(): DayStripProps {
  const cells = dayWindow(90, DAY_STRIP_TODAY).map<DayStripCell>((iso, i) => {
    const executions = EXECUTIONS[i % EXECUTIONS.length]
    const failed = FAILED[i % FAILED.length]
    return {
      key: iso,
      label: `${iso} · ${plural(executions, 'execution')}`,
      tone: executions === 0 ? 'none' : failed > 0 ? 'mixed' : 'pass',
      marker: i === 89 ? 'today' : undefined,
    }
  })
  return { mode: 'presence', cells, title: 'Run cadence', label: 'Run cadence over the last 90 days' }
}

/** Names from ingested CI files are hostile: they must reach the page as text. */
export function hostileStrip(): DayStripProps {
  const names = ['<img src=x onerror=alert(1)>', '"><script>alert(1)</script>', '</td></tr><tr><td>injected', '&amp; &lt;b&gt;']
  return {
    mode: 'status',
    unit: 'build',
    title: 'Hostile labels',
    label: 'Hostile labels <b>not bold</b>',
    cells: names.map((name, i) => ({ key: `hostile ${i}`, label: name, tone: i % 2 === 0 ? 'fail' : 'pass' })),
  }
}

/** Gallery items, in the order the gallery should show them. */
export const DAY_STRIP_FIXTURES: readonly { id: string; title: string; props: () => DayStripProps }[] = [
  { id: 'day-strip-presence', title: 'Day strip · presence (Trends run cadence)', props: presenceStrip },
  { id: 'day-strip-intensity', title: 'Day strip · intensity (Coverage run cadence)', props: intensityStrip },
  { id: 'day-strip-severity', title: 'Day strip · status with severity (failure timeline)', props: severityStrip },
  { id: 'day-strip-compact', title: 'Day strip · compact run strip, no legend', props: compactStrip },
  { id: 'day-strip-builds', title: 'Day strip · builds (build velocity)', props: buildStrip },
  { id: 'day-strip-dense', title: 'Day strip · 90 days', props: denseStrip },
  { id: 'day-strip-hostile', title: 'Day strip · hostile labels', props: hostileStrip },
]
