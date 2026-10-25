/**
 * W13 "test-library" reads and writes (TCL-03 health, TCL-04 import, TCL-05
 * timeline, TCL-06 requirements). Every READ goes through a hook in
 * `hooks/useTestLibrary.ts` (SWR); components call the writes directly.
 *
 * Path ids are `encodeURIComponent`-ed; QUERY values ride Axios `params`
 * (`paramsSerializer: { indexes: null }`, so `kinds` repeats as
 * `kinds=a&kinds=b`). Writes pass `suppressToast`: the forms render the
 * server's sentence in place.
 */
import type {
  TestCaseEvidenceGapKind,
  TestCaseEvidenceGapResponse,
} from '@/types/test-management'
import type {
  LibraryHealth,
  RequirementLink,
  RequirementLinkList,
  TestCaseImportResponse,
  TestCaseTimeline,
  TraceabilityGaps,
} from '@/types/test-library'
import { deleteData, getData, postData } from './http'

// Every path is written out in full (no shared base constant): the backend's
// SPA wire-contract guard (tests/regression/test_spa_wire_contract.py) checks
// each quoted API path literal against the real routes.

export const testLibraryService = {
  getHealth: (projectId: string) =>
    getData<LibraryHealth>('/api/v1/test-management/health', { params: { project_id: projectId } }),

  getTimeline: (caseId: string, params: { kinds?: string[]; cursor?: string | null; limit?: number }) =>
    getData<TestCaseTimeline>(`/api/v1/test-management/cases/${encodeURIComponent(caseId)}/timeline`, {
      params: {
        ...(params.kinds?.length ? { kinds: params.kinds } : {}),
        ...(params.cursor ? { cursor: params.cursor } : {}),
        limit: params.limit ?? 50,
      },
    }),

  listRequirements: (caseId: string) =>
    getData<RequirementLinkList>(`/api/v1/test-management/cases/${encodeURIComponent(caseId)}/requirements`),

  addRequirement: (
    caseId: string,
    body: { requirement_key: string; requirement_title?: string | null; link_type: string },
  ) =>
    postData<RequirementLink>(`/api/v1/test-management/cases/${encodeURIComponent(caseId)}/requirements`, body, {
      suppressToast: true,
    }),

  removeRequirement: (caseId: string, linkId: string) =>
    deleteData<void>(
      `/api/v1/test-management/cases/${encodeURIComponent(caseId)}/requirements/${encodeURIComponent(linkId)}`,
      { suppressToast: true },
    ),

  getEvidenceGapPage: (projectId: string, kind: TestCaseEvidenceGapKind, page = 1, size = 25) =>
    getData<TestCaseEvidenceGapResponse>('/api/v1/test-management/cases/evidence-gaps', {
      params: { project_id: projectId, kind, page, size },
    }),

  getTraceabilityGaps: (projectId: string, page = 1, size = 25) =>
    getData<TraceabilityGaps>('/api/v1/test-management/traceability/gaps', { params: { project_id: projectId, page, size } }),

  /** Multipart; the server reads `file`. `dryRun` true = the preview (nothing written). */
  importCases: (projectId: string, file: File, dryRun: boolean) => {
    const form = new FormData()
    form.append('file', file)
    return postData<TestCaseImportResponse>('/api/v1/test-management/cases/import', form, {
      params: { project_id: projectId, dry_run: dryRun },
      // The shared client defaults Content-Type to JSON (and axios would then
      // serialise the FormData as JSON): unset it so the browser writes
      // multipart/form-data WITH its boundary (releasesService.draftPhases).
      headers: { 'Content-Type': undefined as unknown as string },
      suppressToast: true,
    })
  },
}
