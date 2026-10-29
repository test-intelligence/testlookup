/**
 * W23b FAIL-13: configuration drift — the wire vocabulary, the shapes and their defensive parser.
 *
 * `config_drift` rides the run page's `GET /api/v1/runs/{run_id}/regression-diff` answer (the Changes tab's
 * own baseline): the run's recorded context (runtime, lockfile hashes, image digests, allowlisted env) and
 * its environment / branch / namespace, diffed against that baseline build.
 *
 * The constants mirror `backend/app/services/run_context_vocab.py` value for value (pinned by
 * `backend/tests/test_bisect_drift_vocab_parity.py`, which reads this file as TEXT — keep every constant a
 * plain literal on its own `export const` line, strings in one piece, one `key: 'value',` per record line).
 *
 * Absence is not health: a run or baseline without a context reads "Not recorded by this reporter", never
 * "no drift"; "No configuration drift" is said ONLY for `recorded` with zero entries. Keys and values are
 * rendered as React text.
 */

/** Display order, stable. */
export const DRIFT_SECTIONS = ['dependencies', 'runtime', 'images', 'environment', 'env', 'run'] as const
export type DriftSection = (typeof DRIFT_SECTIONS)[number]

export const DRIFT_CHANGES = ['changed', 'added', 'removed'] as const
export type DriftChange = (typeof DRIFT_CHANGES)[number]

export const CONTEXT_STATES = ['recorded', 'run_missing', 'baseline_missing', 'both_missing'] as const
export type ContextState = (typeof CONTEXT_STATES)[number]

/** Entries returned; `entries_total` is the full count. */
export const DRIFT_ENTRIES_MAX = 60

/** Fixed copy, shown verbatim; `{baseline}` filled with the baseline build. */
export const DRIFT_SENTENCES = {
  run_missing: 'Not recorded by this reporter: this run sent no runtime or dependency context.',
  baseline_missing: 'Not recorded for the baseline build {baseline}: its reporter sent no runtime or dependency context.',
  both_missing: 'Not recorded by this reporter: neither this run nor build {baseline} sent a runtime or dependency context.',
  no_drift: 'No configuration drift against build {baseline}.',
  only_run_fields: 'Runtime and dependency drift: not recorded by this reporter.',
  no_baseline: 'No baseline build to compare with.',
} as const
export type DriftSentenceKey = keyof typeof DRIFT_SENTENCES

/** One difference: `poetry.lock changed`, `python 3.11.8 → 3.11.9`, `branch main → feature/pay`. */
export interface DriftEntry {
  section: DriftSection
  /** "branch", "ocp_namespace", "environment", "python", "poetry.lock", an image name, an env key. */
  key: string
  change: DriftChange
  /** The baseline's value (dependencies: the first 12 hex of the hash); null when added. */
  old: string | null
  /** This run's value; null when removed. */
  new: string | null
}

export interface ConfigDrift {
  baseline_run_id: string
  baseline_build_number: string | null
  context_state: ContextState
  /** `DRIFT_SECTIONS` order, keys ascending inside a section, ≤ `DRIFT_ENTRIES_MAX`. */
  entries: DriftEntry[]
  entries_total: number
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function strOrNull(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

function oneOf<T extends string>(values: readonly T[], v: unknown): v is T {
  return typeof v === 'string' && (values as readonly string[]).includes(v)
}

function parseEntry(raw: unknown): DriftEntry | null {
  if (!isRecord(raw)) return null
  if (!oneOf(DRIFT_SECTIONS, raw.section) || !oneOf(DRIFT_CHANGES, raw.change)) return null
  if (typeof raw.key !== 'string') return null
  return { section: raw.section, key: raw.key, change: raw.change, old: strOrNull(raw.old), new: strOrNull(raw.new) }
}

/**
 * The defensive guard over the regression-diff answer's `config_drift` (parsed at the use site, so a
 * malformed server value cannot type-lie). Returns `null` — and the section is not rendered — for anything
 * that is not an object, has no baseline run id, or carries an unknown `context_state`. Rows of an unknown
 * section or change are DROPPED, never rendered; missing optional fields are defaulted.
 */
export function parseConfigDrift(raw: unknown): ConfigDrift | null {
  if (!isRecord(raw)) return null
  if (typeof raw.baseline_run_id !== 'string') return null
  if (!oneOf(CONTEXT_STATES, raw.context_state)) return null
  const entries = (Array.isArray(raw.entries) ? raw.entries : [])
    .map(parseEntry)
    .filter((e): e is DriftEntry => e !== null)
  return {
    baseline_run_id: raw.baseline_run_id,
    baseline_build_number: strOrNull(raw.baseline_build_number),
    context_state: raw.context_state,
    entries,
    entries_total:
      typeof raw.entries_total === 'number' && Number.isFinite(raw.entries_total) ? raw.entries_total : entries.length,
  }
}
