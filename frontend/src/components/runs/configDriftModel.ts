/**
 * The run page's "Configuration drift" card, the copy (W23b FAIL-13). Pure:
 * `ConfigDriftSection` and its tests import the same strings.
 *
 * The drift is the regression-diff answer's `config_drift` — this run against
 * the Changes tab's own baseline: lockfile hashes, runtime versions, image
 * digests, the `environment` column, allowlisted env vars, and the branch /
 * namespace. Absence is not health: a run (or a baseline) whose reporter sent
 * no context reads "Not recorded by this reporter", never "No configuration
 * drift" — that sentence is said ONLY when both contexts were recorded and
 * every compared field is equal.
 */
import { DRIFT_SECTIONS, DRIFT_SENTENCES, type ConfigDrift, type DriftEntry } from '@/types/configDrift'

export const CARD_TITLE = 'Configuration drift'
export const SUMMARY_PREFIX = 'Configuration drift: '
/** How many entries the summary line names before `· and {n} more`. */
export const SUMMARY_ENTRIES = 3

const SECTION_LABELS: Record<string, string> = {
  dependencies: 'Dependencies',
  runtime: 'Runtime',
  images: 'Images',
  environment: 'Environment',
  env: 'Environment variables',
  run: 'Run',
}

/** The heading of one section's group. */
export function sectionLabel(section: string): string {
  return SECTION_LABELS[section] ?? section
}

/** `#N`, or null when the baseline build has no number. */
function baselineBuild(d: Pick<ConfigDrift, 'baseline_build_number'>): string | null {
  return d.baseline_build_number ? `#${d.baseline_build_number}` : null
}

/** Fill `{baseline}`; without a build number, "build {baseline}" reads "the baseline build". */
function withBaseline(template: string, d: Pick<ConfigDrift, 'baseline_build_number'>): string {
  const build = baselineBuild(d)
  return build ? template.replace('{baseline}', build) : template.replace('build {baseline}', 'the baseline build')
}

/** `Against build #N, the baseline above` (the Changes tab's own baseline). */
export function againstLine(d: Pick<ConfigDrift, 'baseline_build_number'>): string {
  const build = baselineBuild(d)
  return build ? `Against build ${build}, the baseline above` : 'Against the baseline above'
}

/**
 * One entry's line. Dependencies: `poetry.lock changed | added | removed`.
 * Everything else: `python 3.11.8 → 3.11.9`, `pytest 8.3.2 (added)`, `java 21 (removed)`.
 */
export function entryLine(e: DriftEntry): string {
  if (e.section === 'dependencies') return `${e.key} ${e.change}`
  if (e.change === 'added') return `${e.key} ${e.new ?? ''} (added)`
  if (e.change === 'removed') return `${e.key} ${e.old ?? ''} (removed)`
  return `${e.key} ${e.old ?? ''} → ${e.new ?? ''}`
}

export interface DriftGroup {
  section: string
  label: string
  entries: DriftEntry[]
}

/** The entries by section, in `DRIFT_SECTIONS` order; a section without entries is omitted. */
export function groupEntries(d: Pick<ConfigDrift, 'entries'>): DriftGroup[] {
  const order: readonly string[] = DRIFT_SECTIONS
  return order
    .map((section) => ({
      section,
      label: sectionLabel(section),
      entries: d.entries.filter((e) => e.section === section),
    }))
    .filter((g) => g.entries.length > 0)
}

/**
 * `Configuration drift: poetry.lock changed · python 3.11.8 → 3.11.9` — the first
 * three entries (section order), then `· and {n} more` counted against
 * `entries_total`; null when there is no entry.
 */
export function driftSummary(d: Pick<ConfigDrift, 'entries' | 'entries_total'>): string | null {
  const ordered = groupEntries(d).flatMap((g) => g.entries)
  if (ordered.length === 0) return null
  const named = ordered.slice(0, SUMMARY_ENTRIES).map(entryLine)
  const total = Math.max(d.entries_total, ordered.length)
  const more = total - named.length
  return `${SUMMARY_PREFIX}${named.join(' · ')}${more > 0 ? ` · and ${more} more` : ''}`
}

/** The context sentence for a missing context (null when both were recorded). */
export function contextSentence(d: Pick<ConfigDrift, 'context_state' | 'baseline_build_number'>): string | null {
  switch (d.context_state) {
    case 'run_missing':
      return DRIFT_SENTENCES.run_missing
    case 'baseline_missing':
      return withBaseline(DRIFT_SENTENCES.baseline_missing, d)
    case 'both_missing':
      return withBaseline(DRIFT_SENTENCES.both_missing, d)
    default:
      return null
  }
}

/** `No configuration drift against build #N.` — ONLY for a recorded pair with no entry. */
export function noDriftLine(d: Pick<ConfigDrift, 'context_state' | 'entries' | 'entries_total' | 'baseline_build_number'>): string | null {
  if (d.context_state !== 'recorded' || d.entries.length > 0 || d.entries_total > 0) return null
  return withBaseline(DRIFT_SENTENCES.no_drift, d)
}

/** Under the run-field entries when a context is missing: the rest was not recorded. */
export function onlyRunFieldsLine(d: Pick<ConfigDrift, 'context_state' | 'entries'>): string | null {
  return d.context_state !== 'recorded' && d.entries.length > 0 ? DRIFT_SENTENCES.only_run_fields : null
}

/** `Showing {n} of {entries_total}` when the server capped the list. */
export function cappedLine(d: Pick<ConfigDrift, 'entries' | 'entries_total'>): string | null {
  return d.entries_total > d.entries.length ? `Showing ${d.entries.length} of ${d.entries_total}` : null
}
