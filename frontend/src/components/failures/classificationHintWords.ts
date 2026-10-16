/**
 * W07 FAIL-10: the words of a classification hint, in ONE place for the
 * dialog (and anything else that names a hint). The vocabularies are plain
 * strings on the wire, validated by the server; a value the UI does not know
 * reads as itself.
 */
import type { ClassificationHint } from '@/types/classificationHints'

/** The four fields a hint can match: the select's label and the sentence's word. */
export const HINT_FIELDS = [
  { id: 'error_message', option: 'Error message', word: 'error message' },
  { id: 'test_name', option: 'Test name', word: 'test name' },
  { id: 'suite_name', option: 'Suite', word: 'suite' },
  { id: 'class_name', option: 'Class', word: 'class' },
] as const

/** The five categories a hint can set (FailureCategory minus UNKNOWN). */
export const HINT_CATEGORIES = [
  { id: 'PRODUCT_BUG', label: 'Product bug' },
  { id: 'INFRASTRUCTURE', label: 'Infrastructure' },
  { id: 'TEST_DATA', label: 'Test data' },
  { id: 'AUTOMATION_DEFECT', label: 'Automation defect' },
  { id: 'FLAKY', label: 'Flaky' },
] as const

/** The preview waits this long after the last keystroke. */
export const PREVIEW_DEBOUNCE_MS = 400

const fieldWord = (id: string) => HINT_FIELDS.find(f => f.id === id)?.word ?? id
const categoryLabel = (id: string) => HINT_CATEGORIES.find(c => c.id === id)?.label ?? id

/** `error message contains "ECONNREFUSED" → Infrastructure`: the ledger's and the decision trail's sentence. */
export function hintSentence(hint: Pick<ClassificationHint, 'match_field' | 'pattern' | 'category'>): string {
  return `${fieldWord(hint.match_field)} contains "${hint.pattern}" → ${categoryLabel(hint.category)}`
}
