/**
 * W06 DEF-01: defect triage (change status, assign, link a Jira issue) on the
 * shared Axios base. The row's own project is in the path, so a defect is
 * always changed inside the project that owns it.
 */
import type { AxiosRequestConfig } from 'axios'
import { patchData } from './http'
import type { DefectPatchBody, DefectPatchResponse } from '@/types/defectTriage'

type RequestConfig = Omit<AxiosRequestConfig, 'data' | 'method' | 'url'>

export function updateDefect(
  projectId: string,
  defectId: string,
  body: DefectPatchBody,
  config?: RequestConfig,
): Promise<DefectPatchResponse> {
  return patchData<DefectPatchResponse, DefectPatchBody>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/defects/${encodeURIComponent(defectId)}`,
    body,
    config,
  )
}

export const defectService = { updateDefect }
