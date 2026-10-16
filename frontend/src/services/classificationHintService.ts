/**
 * W07 FAIL-10: a project's classification hints
 * (`/api/v1/projects/{project_id}/classification-hints`).
 *
 * Reads go through `hooks/useClassificationHints.ts` (SWR); the writes are
 * called from the modal's handlers. Every write and the preview pass
 * `suppressToast`: the modal shows a refusal's server detail in ONE toast (a
 * 409 duplicate, a 422 vocabulary or length text), and a preview that fails
 * while the reader types says so in place instead of toasting each keystroke.
 */
import type {
  ClassificationHint,
  ClassificationHintBody,
  ClassificationHintList,
  ClassificationHintPreview,
} from '@/types/classificationHints'
import { deleteData, getData, patchData, postData } from './http'

const enc = encodeURIComponent

// Every path is a whole literal (not built from a shared prefix) so
// `tests/regression/test_spa_wire_contract.py` checks each one against the
// assembled route table.
export const classificationHintService = {
  list: (projectId: string) =>
    getData<ClassificationHintList>(`/api/v1/projects/${enc(projectId)}/classification-hints`),

  preview: (projectId: string, matchField: string, pattern: string) =>
    getData<ClassificationHintPreview>(`/api/v1/projects/${enc(projectId)}/classification-hints/preview`, {
      params: { match_field: matchField, pattern },
      suppressToast: true,
    }),

  create: (projectId: string, body: ClassificationHintBody) =>
    postData<ClassificationHint, ClassificationHintBody>(
      `/api/v1/projects/${enc(projectId)}/classification-hints`, body, { suppressToast: true },
    ),

  update: (projectId: string, hintId: string, body: ClassificationHintBody) =>
    patchData<ClassificationHint, ClassificationHintBody>(
      `/api/v1/projects/${enc(projectId)}/classification-hints/${enc(hintId)}`, body, { suppressToast: true },
    ),

  remove: (projectId: string, hintId: string) =>
    deleteData<void>(`/api/v1/projects/${enc(projectId)}/classification-hints/${enc(hintId)}`, { suppressToast: true }),
}
