/**
 * The suite Edit form's pure model (W13 TCL-07): what changed, the rename
 * warning, the tags text, and the one write.
 *
 * `PATCH /api/v1/suites/{suite_id}` takes name / description / tags, each
 * `None` = keep (`TestSuiteUpdate`), so Save sends ONLY the changed fields;
 * the server records `suite.updated` naming them. An emptied description is
 * sent as `""` (a `null` would mean "keep").
 */
import { patchData } from '@/services/http'
import { extractErrorMessage } from '@/services/apiErrors'
import type { TestSuite, TestSuiteUpdatePayload } from '@/types/suites'

export const SUITE_NAME_MAX = 500
export const SUITE_DESCRIPTION_MAX = 50_000

/** The form's three fields, as typed. */
export interface SuiteEditDraft {
  name: string
  description: string
  tags: string
}

type EditableSuite = Pick<TestSuite, 'name' | 'description' | 'tags'>

export function draftFromSuite(suite: EditableSuite): SuiteEditDraft {
  return { name: suite.name, description: suite.description ?? '', tags: (suite.tags ?? []).join(', ') }
}

/** Comma-separated text → tags: trimmed, empties dropped, duplicates dropped (first kept, order kept). */
export function parseTags(text: string): string[] {
  const seen = new Set<string>()
  const tags: string[] = []
  for (const raw of text.split(',')) {
    const tag = raw.trim()
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    tags.push(tag)
  }
  return tags
}

function sameTags(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((tag, i) => tag === b[i])
}

/** Only the fields whose value differs from the stored suite (an empty object = nothing to save). */
export function changedFields(suite: EditableSuite, form: SuiteEditDraft): TestSuiteUpdatePayload {
  const payload: TestSuiteUpdatePayload = {}
  const name = form.name.trim()
  if (name && name !== suite.name) payload.name = name
  if (form.description !== (suite.description ?? '')) payload.description = form.description
  const tags = parseTags(form.tags)
  if (!sameTags(tags, suite.tags ?? [])) payload.tags = tags
  return payload
}

/** The note under Name while the trimmed name differs from the stored one; null otherwise. */
export function renameWarning(oldName: string, newName: string): string | null {
  const next = newName.trim()
  if (!next || next === oldName) return null
  return (
    `Past runs keep reporting “${oldName}”: their results stay under that name on Test Cases › Suites. ` +
    'A run that still sends the old name creates a suite of that name again for tests new to this project.'
  )
}

/** Why Save is not offered, or null when it is. */
export function saveBlocker(suite: EditableSuite, form: SuiteEditDraft): string | null {
  if (!form.name.trim()) return 'A suite needs a name.'
  if (form.name.trim().length > SUITE_NAME_MAX) return 'A suite name is at most 500 characters.'
  if (form.description.length > SUITE_DESCRIPTION_MAX) return 'A description is at most 50,000 characters.'
  if (Object.keys(changedFields(suite, form)).length === 0) return 'Nothing has changed.'
  return null
}

export function savedMessage(name: string): string {
  return `Suite “${name}” saved.`
}

/** The server's sentence (409 name taken, 403 Forbidden, a 422 field error), never a stack. */
export function saveErrorSentence(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return extractErrorMessage(detail, 'Could not save the suite.')
}

/**
 * The one write: the same request as `suitesService.update`, with the global
 * error toast suppressed because the form renders the server's sentence.
 */
export function saveSuiteEdit(suiteId: string, payload: TestSuiteUpdatePayload): Promise<TestSuite> {
  return patchData<TestSuite, TestSuiteUpdatePayload>(
    `/api/v1/suites/${encodeURIComponent(suiteId)}`,
    payload,
    { suppressToast: true },
  )
}
