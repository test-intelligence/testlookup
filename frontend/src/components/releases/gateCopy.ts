/**
 * The words the release page uses for a gate verdict and an evaluation
 * (REL-01): one definition for the card's Evaluate, the toolbar's Evaluate
 * all, the gate panel and the phase chips. Pure: no fetching.
 */
import { mapVerdict } from './mapping'
import type { ReleaseGateEvaluationResult } from '@/services/releasesService'

/** `GO` -> "Go"; an unknown word reads "Not evaluated", never a pass. */
export function verdictWord(verdict: string): string {
  switch (mapVerdict(verdict)) {
    case 'go': return 'Go'
    case 'no_go': return 'No-Go'
    case 'conditional': return 'Conditional Go'
    default: return 'Not evaluated'
  }
}

/** `82.456` -> `"82.5%"`; `null` (not measured) -> `"—"`, never 0. */
export function percent(value: number | null | undefined): string {
  return value == null ? '—' : `${value.toFixed(1)}%`
}

/** A signed delta: `+3`, `-2`, `+0`; with `digits`, `+1.5`. */
export function signed(value: number, digits = 0): string {
  return `${value < 0 ? '-' : '+'}${Math.abs(value).toFixed(digits)}`
}

const items = (n: number) => `${n} blocking item${n === 1 ? '' : 's'}`

/** The toast after one release was evaluated, quoting the recorded verdict. */
export function evaluationToast(name: string, result: ReleaseGateEvaluationResult): string {
  const blocking = result.blocking_reasons ?? []
  switch (mapVerdict(result.verdict)) {
    case 'go':
      return `${name}: Go — pass rate ${percent(result.scorecard?.pass_rate)} over ${result.scorecard?.evidence_count ?? 0} tests`
    case 'no_go':
      return `${name}: No-Go — ${items(blocking.length)}`
    case 'conditional':
      return `${name}: Conditional Go — ${items(blocking.length)}`
    default: {
      const reason = result.scorecard?.insufficient_reason || blocking[0] || 'the server gave no reason'
      return `${name}: Not evaluated — ${reason}`
    }
  }
}

/** The toast after "Evaluate all in progress" ran every release. */
export function evaluateAllToast(verdicts: string[]): string {
  const n = verdicts.length
  const go = verdicts.filter(v => mapVerdict(v) === 'go').length
  const noGo = verdicts.filter(v => mapVerdict(v) === 'no_go').length
  // Nothing records CONDITIONAL_GO today; a future writer's verdicts are
  // counted as what they are, not as "not evaluated".
  const conditional = verdicts.filter(v => mapVerdict(v) === 'conditional').length
  const rest = n - go - noGo - conditional
  return `Evaluated ${n} release${n === 1 ? '' : 's'}: ${go} Go, ${noGo} No-Go, `
    + `${conditional ? `${conditional} Conditional Go, ` : ''}${rest} not evaluated.`
}

/** The toast when some evaluations of "Evaluate all" failed. */
export function evaluateAllPartialToast(succeeded: number, total: number, failed: number, firstDetail: string): string {
  return `Evaluated ${succeeded} of ${total} releases; ${failed} failed — ${firstDetail}`
}
