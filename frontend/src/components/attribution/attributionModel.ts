/**
 * The attribution rules page's copy and rules, pure (W11 REL-09).
 *
 * Every sentence the page prints lives here so the tests import one string.
 * Nothing here renders HTML: patterns, branches, tags and release names are
 * handed back as plain strings and shown as React text.
 */
import type {
  AttributionPreviewResponse,
  AttributionRule,
  AttributionRuleField,
  AttributionRuleInput,
} from '@/types/attributionRules'
import { ATTRIBUTION_FIELD_FALLBACK } from '@/types/attributionRules'

export const PAGE_TITLE = 'Attribution rules'
export const PAGE_SUBTITLE = "Map a run's branch, build, environment or tag to a release when the run does not name one."
export const LOAD_ERROR = 'Could not load attribution rules.'
export const EMPTY_RULES = 'No rules yet. Runs that name no release land in the cutoff window or the active release.'
export const PROJECT_PROMPT =
  'Attribution rules belong to one project: they decide which of its releases a run that names none lands in.'
export const CREATED_ON_FIRST_MATCH = '(created on first match)'
export const PRIORITY_HELP = 'Lower runs first; rules with the same priority run in the order they were created.'
export const FOOTER_WITHOUT_PREVIEW = 'Runs with no release are listed under Runs › Release › Unattributed.'
export const CATCH_ALL = 'This rule matches every recent run.'
export const NO_RUNS_YET = 'This project has no runs to try the rule on yet.'

/** The ladder, in the order the server applies it (`release_linker.link_run_or_default`). */
export const LADDER_STEPS = [
  'The release the run names',
  'A GitHub milestone on its pull request',
  'These rules, top to bottom',
  'A release whose cutoff window holds the run',
  'The release that was active when it ran',
] as const

export const PRIORITY_MIN = 0
export const PRIORITY_MAX = 10_000
export const DEFAULT_PRIORITY = 100

/** The server's fields when it answered with any, else the fallback (same vocabulary, pinned by tests). */
export function effectiveFields(served: readonly AttributionRuleField[] | null | undefined): readonly AttributionRuleField[] {
  return served && served.length > 0 ? served : ATTRIBUTION_FIELD_FALLBACK
}

/** `Branch`, `Build number`, …; an unknown value reads as itself. */
export function fieldLabel(value: string, fields: readonly AttributionRuleField[]): string {
  return fields.find((f) => f.value === value)?.label ?? value
}

/** `Branch release/2.5*` — the table's Matches cell as one string (its `title`). */
export function matchesText(rule: Pick<AttributionRule, 'match_field' | 'match_pattern'>, fields: readonly AttributionRuleField[]): string {
  return `${fieldLabel(rule.match_field, fields)} ${rule.match_pattern}`
}

function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many
}

/** "a branch", "an environment": what a run carries, in a sentence. */
function withArticle(label: string): string {
  const lower = label.toLowerCase()
  return /^[aeiou]/.test(lower) ? `an ${lower}` : `a ${lower}`
}

/**
 * The preview's sentences, in the page's order:
 * `Matches 12 of the last 200 runs (183 carry a branch).`, then (when non-zero)
 * the shadowed and the named sentences, the missing-target note and the catch-all warning.
 * A field missing from an older answer reads its default (nothing to say).
 */
export function previewSentences(preview: AttributionPreviewResponse, label: string, targetName: string): string[] {
  if (preview.considered === 0) return [NO_RUNS_YET]
  const out = [
    `Matches ${preview.matched} of the last ${preview.considered} ${plural(preview.considered, 'run', 'runs')} ` +
      `(${preview.field_present} ${plural(preview.field_present, 'carries', 'carry')} ${withArticle(label)}).`,
  ]
  const shadowed = preview.shadowed ?? 0
  if (shadowed > 0) {
    out.push(
      shadowed === 1
        ? '1 of them already matches an earlier rule, which wins.'
        : `${shadowed} of them already match an earlier rule, which wins.`,
    )
  }
  const explicit = preview.explicit ?? 0
  if (explicit > 0) {
    out.push(
      explicit === 1
        ? '1 named its release outright; a rule does not move it.'
        : `${explicit} named their release outright; a rule does not move them.`,
    )
  }
  if (preview.target_exists === false) {
    out.push(`No release named "${targetName.trim()}" yet: the first matching run creates it.`)
  }
  if (preview.matched === preview.considered) out.push(CATCH_ALL)
  return out
}

/** A sample row's "Would land" cell. */
export function landingText(wouldLand: string | undefined): string {
  if (wouldLand === 'earlier_rule') return 'earlier rule'
  if (wouldLand === 'explicit') return 'stays (named)'
  return 'here'
}

/**
 * `(created on first match)` when no release of the project carries `name`
 * (the linker's match: trimmed, case-insensitive); `null` when one does — or
 * when the release list is not loaded, since then nothing is known.
 */
export function targetNote(name: string, releases: readonly { name: string }[] | null | undefined): string | null {
  if (!releases) return null
  const wanted = name.trim().toLowerCase()
  return releases.some((r) => r.name.trim().toLowerCase() === wanted) ? null : CREATED_ON_FIRST_MATCH
}

/** The footer after a preview: `4 of the last 200 runs have no release (Unattributed).` */
export function unattributedFooter(preview: AttributionPreviewResponse | null): { text: string; count: number } {
  if (!preview || preview.unattributed_recent === undefined) return { text: FOOTER_WITHOUT_PREVIEW, count: 0 }
  const n = preview.unattributed_recent
  return {
    text: `${n} of the last ${preview.considered} ${plural(preview.considered, 'run', 'runs')} ${plural(n, 'has', 'have')} no release (Unattributed).`,
    count: n,
  }
}

/** The form's state: the server's `AttributionRuleIn` with the priority as typed. */
export interface RuleForm {
  name: string
  match_field: string
  match_pattern: string
  target_release_name: string
  priority: string
  is_enabled: boolean
}

export function emptyForm(fields: readonly AttributionRuleField[]): RuleForm {
  return {
    name: '',
    match_field: fields[0]?.value ?? 'branch',
    match_pattern: '',
    target_release_name: '',
    priority: String(DEFAULT_PRIORITY),
    is_enabled: true,
  }
}

export function formFromRule(rule: AttributionRule): RuleForm {
  return {
    name: rule.name,
    match_field: rule.match_field,
    match_pattern: rule.match_pattern,
    target_release_name: rule.target_release_name,
    priority: String(rule.priority),
    is_enabled: rule.is_enabled,
  }
}

/** The first thing wrong with the form, or `null` (checked before Preview and Save). */
export function validateRule(form: RuleForm, fields: readonly AttributionRuleField[]): string | null {
  if (!form.name.trim()) return 'Name the rule.'
  if (!fields.some((f) => f.value === form.match_field)) return 'Choose a field to match.'
  if (!form.match_pattern.trim()) return 'Enter a pattern to match.'
  if (!form.target_release_name.trim()) return 'Name the release the rule assigns.'
  const text = form.priority.trim()
  const priority = Number(text)
  if (!/^\d+$/.test(text) || priority < PRIORITY_MIN || priority > PRIORITY_MAX) {
    return 'Priority must be a whole number from 0 to 10,000.'
  }
  return null
}

/** The request body for a valid form (names and patterns trimmed). */
export function ruleBody(form: RuleForm): AttributionRuleInput {
  return {
    name: form.name.trim(),
    match_field: form.match_field,
    match_pattern: form.match_pattern.trim(),
    target_release_name: form.target_release_name.trim(),
    priority: Number(form.priority.trim()),
    is_enabled: form.is_enabled,
  }
}

/** A sample's tags as one cell; `—` for none. */
export function tagsText(tags: readonly string[] | undefined): string {
  return tags && tags.length > 0 ? tags.join(', ') : '—'
}
