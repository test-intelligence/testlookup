/**
 * The Runs list's batch reads (FAIL-04 / FAIL-03 / FAIL-05) and the Run
 * Compare picker's reads (FAIL-06), on the shared Axios base (`services/http`).
 *
 * A NEW file on purpose: `services/runsService.ts` is replaced by a
 * fixed-factory `vi.mock` in several test files, where a function added to it
 * would be `undefined`.
 *
 * Repeatable query parameters (`run_id`) go as an array: `services/api.ts`
 * serialises arrays as repeated keys (`paramsSerializer: { indexes: null }`),
 * which is what FastAPI reads. No per-call serializer.
 */
import type { AxiosRequestConfig } from 'axios'
import type {
  RunBranchList,
  RunSignatureBatch,
  RunVerdictBatch,
  TestRun,
  TestRunListResponse,
} from '@/types/runs'
import { getData } from './http'

type ReadConfig = Omit<AxiosRequestConfig, 'data' | 'method' | 'url'>

/** The Run Compare picker's search (FAIL-06). Empty values are not sent. */
export interface RunSearchParams {
  q?: string | null
  suite_name?: string | null
  page: number
  size: number
  /** 0 = all time. */
  days: number
}

export const runListService = {
  /**
   * `GET /api/v1/runs/ai-verdicts`: the stored AI-verdict inputs of 1-25 runs.
   * A row's cell renders its own "unavailable": no global toast.
   */
  getAiVerdicts: (runIds: readonly string[]) =>
    getData<RunVerdictBatch>('/api/v1/runs/ai-verdicts', {
      params: { run_id: [...runIds] },
      suppressToast: true,
    }),

  /** `GET /api/v1/runs/failure-signatures`: the failure signatures of 1-50 runs. */
  getFailureSignatures: (runIds: readonly string[]) =>
    getData<RunSignatureBatch>('/api/v1/runs/failure-signatures', {
      params: { run_id: [...runIds] },
      suppressToast: true,
    }),

  /** `GET /api/v1/runs/branches`: the project's branches in the window (0 = all time). */
  getBranches: (projectId: string, days: number) =>
    getData<RunBranchList>('/api/v1/runs/branches', {
      params: { project_id: projectId, days },
      suppressToast: true,
    }),

  /** `GET /api/v1/runs` for the picker: every release, `days` as given. */
  searchRuns: (projectId: string | null, params: RunSearchParams) => {
    const q = params.q?.trim()
    const suite = params.suite_name?.trim()
    return getData<TestRunListResponse>('/api/v1/runs', {
      params: {
        ...(projectId ? { project_id: projectId } : {}),
        ...(q ? { q } : {}),
        ...(suite ? { suite_name: suite } : {}),
        page: params.page,
        size: params.size,
        days: params.days,
      },
    })
  },

  /** `GET /api/v1/runs/{runId}` (403 / 404 for a run the reader cannot see). */
  getRun: (runId: string, config?: ReadConfig) =>
    getData<TestRun>(`/api/v1/runs/${encodeURIComponent(runId)}`, config),
}
