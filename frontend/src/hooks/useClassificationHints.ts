/**
 * W07 FAIL-10: a project's classification hints, read through SWR.
 *
 *   useClassificationHints(projectId)                       GET  …/classification-hints
 *   useClassificationHintPreview(projectId, field, pattern) GET  …/classification-hints/preview
 *
 * Both take the project explicitly (the modal opens for ONE project; never
 * All Projects) and ask nothing without one. The list is read only while the
 * modal is mounted (a QA lead opened it). The preview asks only for a pattern
 * of `PREVIEW_MIN_CHARS` or more after trimming (the server's floor), and keeps
 * the previous answer on screen while the next one loads, so the count does
 * not blink while the reader types. The writes live in
 * `services/classificationHintService.ts`; callers `mutate()` the list after one.
 */
import useSWR, { type SWRResponse } from 'swr'
import { classificationHintService } from '@/services/classificationHintService'
import type { ClassificationHintList, ClassificationHintPreview } from '@/types/classificationHints'

export const HINTS_KEY = 'classification-hints'
export const HINT_PREVIEW_KEY = 'classification-hint-preview'
/** The server's pattern floor (`PATTERN_MIN`, 3 characters after strip). */
export const PREVIEW_MIN_CHARS = 3

export function useClassificationHints(projectId: string | null | undefined): SWRResponse<ClassificationHintList> {
  return useSWR<ClassificationHintList>(
    projectId ? [HINTS_KEY, projectId] : null,
    () => classificationHintService.list(projectId as string),
    { revalidateOnFocus: false },
  )
}

export function useClassificationHintPreview(
  projectId: string | null | undefined,
  matchField: string,
  pattern: string,
): SWRResponse<ClassificationHintPreview> {
  const text = pattern.trim()
  return useSWR<ClassificationHintPreview>(
    projectId && text.length >= PREVIEW_MIN_CHARS ? [HINT_PREVIEW_KEY, projectId, matchField, text] : null,
    () => classificationHintService.preview(projectId as string, matchField, text),
    { revalidateOnFocus: false, shouldRetryOnError: false, keepPreviousData: true },
  )
}
