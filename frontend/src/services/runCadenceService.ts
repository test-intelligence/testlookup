import { deleteData, getData, patchData, postData } from './http'
import type {
  RunCadence,
  RunCadenceCreate,
  RunCadenceList,
  RunCadencePatch,
  RunCadencePause,
} from '@/types/runCadence'

/**
 * W23a TRND-03: expected run cadences of one project (TestLookup's own expectation — nothing here touches
 * CI).
 *
 * - list / status: any member of the project (a project-bound key too) — reads only; the status answer is
 *   RAW, read through `parseCadenceStatus` by `hooks/useRunCadences`;
 * - create / update / delete / pause / resume: QA lead and above, signed in (a project-bound key is
 *   refused).
 *
 * Every request path is written out in full in its call (the SPA wire-contract guard reads them). Every
 * write passes `suppressToast: true`: the caller shows `extractErrorMessage(err)` inline, where the
 * refusal happened (`detail: {code, message}` with one of `CADENCE_ERRORS`).
 */

export function listCadences(projectId: string): Promise<RunCadenceList> {
  return getData<RunCadenceList>(`/api/v1/projects/${projectId}/run-cadences`)
}

export function createCadence(projectId: string, body: RunCadenceCreate): Promise<RunCadence> {
  return postData<RunCadence, RunCadenceCreate>(`/api/v1/projects/${projectId}/run-cadences`, body, {
    suppressToast: true,
  })
}

export function updateCadence(projectId: string, cadenceId: string, body: RunCadencePatch): Promise<RunCadence> {
  return patchData<RunCadence, RunCadencePatch>(`/api/v1/projects/${projectId}/run-cadences/${cadenceId}`, body, {
    suppressToast: true,
  })
}

export function deleteCadence(projectId: string, cadenceId: string): Promise<void> {
  return deleteData<void>(`/api/v1/projects/${projectId}/run-cadences/${cadenceId}`, { suppressToast: true })
}

export function pauseCadence(projectId: string, cadenceId: string, body: RunCadencePause): Promise<RunCadence> {
  return postData<RunCadence, RunCadencePause>(`/api/v1/projects/${projectId}/run-cadences/${cadenceId}/pause`, body, {
    suppressToast: true,
  })
}

export function resumeCadence(projectId: string, cadenceId: string): Promise<RunCadence> {
  return postData<RunCadence>(`/api/v1/projects/${projectId}/run-cadences/${cadenceId}/resume`, undefined, {
    suppressToast: true,
  })
}

export function cadenceStatus(projectId: string, days: number): Promise<unknown> {
  return getData<unknown>(`/api/v1/projects/${projectId}/run-cadences/status`, { params: { days } })
}
