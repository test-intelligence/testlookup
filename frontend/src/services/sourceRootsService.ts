import type { SourceRoot, SourceRootsPreview, SourceRootsResponse } from '@/types/sourceRoots'
import { getData, postData, putData } from './http'

/**
 * Java source roots (FAIL-08), under the ownership router:
 * `GET / PUT /api/v1/projects/{project_id}/ownership/source-roots` and
 * `POST .../source-roots/preview` (read-only: what an edited list would locate).
 *
 * The PUT and the preview pass `suppressToast`: their 422 texts are the
 * server's own sentences ("Source root "../x" must be a relative path …"), and
 * the section shows that detail in ONE toast itself.
 *
 * Every path is a whole literal (not built from a shared prefix) so
 * `tests/regression/test_spa_wire_contract.py` checks each one against the
 * assembled route table.
 */
const enc = encodeURIComponent

export function getSourceRoots(projectId: string): Promise<SourceRootsResponse> {
  return getData<SourceRootsResponse>(`/api/v1/projects/${enc(projectId)}/ownership/source-roots`)
}

export function putSourceRoots(projectId: string, roots: SourceRoot[]): Promise<SourceRootsResponse> {
  return putData<SourceRootsResponse, { roots: SourceRoot[] }>(
    `/api/v1/projects/${enc(projectId)}/ownership/source-roots`,
    { roots },
    { suppressToast: true },
  )
}

export function previewSourceRoots(projectId: string, roots: SourceRoot[]): Promise<SourceRootsPreview> {
  return postData<SourceRootsPreview, { roots: SourceRoot[] }>(
    `/api/v1/projects/${enc(projectId)}/ownership/source-roots/preview`,
    { roots },
    { suppressToast: true },
  )
}
