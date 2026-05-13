import type {
  AICoverageResponse,
  AIGenerateResponse,
  AIReviewResult,
  AuditLogEntry,
  ManagedTestCase,
  PaginatedResponse,
  TestCaseComment,
  TestCaseReview,
  TestCaseVersion,
  TestPlan,
  TestPlanItem,
  TestStrategy,
} from '@/types/test-management'
import { api } from './api'
import { deleteData, getData, patchData, postData, putData } from './http'

function extractFilename(contentDisposition?: string, fallback = 'download') {
  if (!contentDisposition) return fallback
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1])
  const quotedMatch = contentDisposition.match(/filename="([^"]+)"/i)
  if (quotedMatch?.[1]) return quotedMatch[1]
  const plainMatch = contentDisposition.match(/filename=([^;]+)/i)
  if (plainMatch?.[1]) return plainMatch[1].trim()
  return fallback
}

async function downloadFile(url: string, fallbackFilename: string) {
  const response = await api.get<Blob>(url, { responseType: 'blob' })
  const blobUrl = window.URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = extractFilename(response.headers['content-disposition'], fallbackFilename)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(blobUrl)
}

export const testManagementService = {
  // Test Cases
  listCases: (projectId: string | null, params?: Record<string, unknown>): Promise<PaginatedResponse<ManagedTestCase>> =>
    getData('/api/v1/test-management/cases', { params: { ...(projectId ? { project_id: projectId } : {}), ...params } }),

  createCase: (data: Partial<ManagedTestCase>): Promise<ManagedTestCase> =>
    postData('/api/v1/test-management/cases', data),

  getCase: (id: string): Promise<ManagedTestCase> =>
    getData(`/api/v1/test-management/cases/${id}`),

  updateCase: (id: string, data: Partial<ManagedTestCase> & { change_summary?: string }): Promise<ManagedTestCase> =>
    patchData(`/api/v1/test-management/cases/${id}`, data),

  deleteCase: (id: string): Promise<void> =>
    deleteData(`/api/v1/test-management/cases/${id}`),

  getCaseHistory: (id: string): Promise<TestCaseVersion[]> =>
    getData(`/api/v1/test-management/cases/${id}/history`),

  getCaseReviews: (id: string): Promise<TestCaseReview[]> =>
    getData(`/api/v1/test-management/cases/${id}/reviews`),

  getCaseComments: (id: string): Promise<TestCaseComment[]> =>
    getData(`/api/v1/test-management/cases/${id}/comments`),

  addComment: (id: string, data: { content: string; comment_type: string; step_number?: number }): Promise<TestCaseComment> =>
    postData(`/api/v1/test-management/cases/${id}/comments`, data),

  requestReview: (id: string): Promise<TestCaseReview> =>
    postData(`/api/v1/test-management/cases/${id}/request-review`),

  reviewAction: (id: string, action: string, notes?: string): Promise<ManagedTestCase> =>
    postData(`/api/v1/test-management/cases/${id}/review-action`, { action, notes }),

  // AI operations
  aiGenerate: (data: { project_id: string; requirements: string; persist: boolean }): Promise<AIGenerateResponse> =>
    postData('/api/v1/test-management/cases/ai-generate', data),

  aiGenerateAsync: (data: { project_id: string; requirements: string; persist: boolean }): Promise<{ task_id: string; status: string }> =>
    postData('/api/v1/test-management/cases/ai-generate/async', data),

  aiTaskStatus: (taskId: string): Promise<{ task_id: string; status: string; result?: AIGenerateResponse; error?: string }> =>
    getData(`/api/v1/test-management/cases/ai-task/${taskId}`),

  aiReview: (id: string): Promise<AIReviewResult> =>
    postData(`/api/v1/test-management/cases/${id}/ai-review`),

  aiCoverage: (data: { project_id: string; requirements: string }): Promise<AICoverageResponse> =>
    postData('/api/v1/test-management/cases/ai-coverage', data),

  // Test Plans
  listPlans: (projectId: string | null, params?: Record<string, unknown>): Promise<PaginatedResponse<TestPlan>> =>
    getData('/api/v1/test-management/plans', { params: { ...(projectId ? { project_id: projectId } : {}), ...params } }),

  createPlan: (data: Partial<TestPlan>): Promise<TestPlan> =>
    postData('/api/v1/test-management/plans', data),

  getPlan: (id: string): Promise<TestPlan> =>
    getData(`/api/v1/test-management/plans/${id}`),

  updatePlan: (id: string, data: Partial<TestPlan>): Promise<TestPlan> =>
    patchData(`/api/v1/test-management/plans/${id}`, data),

  getPlanItems: (id: string): Promise<TestPlanItem[]> =>
    getData(`/api/v1/test-management/plans/${id}/items`),

  addPlanItem: (planId: string, data: { test_case_id: string; order_index?: number }): Promise<TestPlanItem> =>
    postData(`/api/v1/test-management/plans/${planId}/items`, data),

  removePlanItem: (planId: string, itemId: string): Promise<void> =>
    deleteData(`/api/v1/test-management/plans/${planId}/items/${itemId}`),

  executeItem: (planId: string, itemId: string, data: { execution_status: string; execution_notes?: string; actual_duration_minutes?: number }): Promise<TestPlanItem> =>
    postData(`/api/v1/test-management/plans/${planId}/items/${itemId}/execute`, data),

  aiCreatePlan: (data: { project_id: string; plan_name?: string; constraints?: string }): Promise<TestPlan> =>
    postData('/api/v1/test-management/plans/ai-create', data),

  aiCreatePlanAsync: (data: { project_id: string; plan_name?: string; constraints?: string }): Promise<{ task_id: string; status: string }> =>
    postData('/api/v1/test-management/plans/ai-create/async', data),

  // Strategies
  listStrategies: (projectId: string | null): Promise<TestStrategy[]> =>
    getData('/api/v1/test-management/strategies', { params: projectId ? { project_id: projectId } : {} }),

  getStrategy: (id: string): Promise<TestStrategy> =>
    getData(`/api/v1/test-management/strategies/${id}`),

  updateStrategy: (id: string, data: Partial<TestStrategy>): Promise<TestStrategy> =>
    putData(`/api/v1/test-management/strategies/${id}`, data),

  aiGenerateStrategy: (data: { project_id: string; project_context: string; strategy_name?: string }): Promise<TestStrategy> =>
    postData('/api/v1/test-management/strategies/ai-generate', data),

  aiGenerateStrategyAsync: (data: { project_id: string; project_context: string; strategy_name?: string }): Promise<{ task_id: string; status: string }> =>
    postData('/api/v1/test-management/strategies/ai-generate/async', data),

  // Audit Log
  getAuditLog: (projectId: string | null, params?: { entity_type?: string; action?: string; page?: number; size?: number }): Promise<PaginatedResponse<AuditLogEntry>> =>
    getData('/api/v1/test-management/audit', { params: { ...(projectId ? { project_id: projectId } : {}), ...params } }),

  // Export
  exportCasesExcelUrl: (projectId: string | null, params?: Record<string, unknown>): string => {
    const query = new URLSearchParams()
    if (projectId) query.set('project_id', projectId)
    if (params) Object.entries(params).forEach(([k, v]) => { if (v != null && v !== '') query.set(k, String(v)) })
    return `/api/v1/test-management/cases/export/excel?${query.toString()}`
  },

  exportPlanWordUrl: (planId: string): string =>
    `/api/v1/test-management/plans/${planId}/export/word`,

  exportPlanPdfUrl: (planId: string): string =>
    `/api/v1/test-management/plans/${planId}/export/pdf`,

  downloadPlanWord: (planId: string): Promise<void> =>
    downloadFile(
      `/api/v1/test-management/plans/${planId}/export/word`,
      `test-plan-${planId}.docx`,
    ),

  downloadPlanPdf: (planId: string): Promise<void> =>
    downloadFile(
      `/api/v1/test-management/plans/${planId}/export/pdf`,
      `test-plan-${planId}.pdf`,
    ),

  exportStrategyWordUrl: (strategyId: string): string =>
    `/api/v1/test-management/strategies/${strategyId}/export/word`,

  exportStrategyPdfUrl: (strategyId: string): string =>
    `/api/v1/test-management/strategies/${strategyId}/export/pdf`,

  downloadStrategyWord: (strategyId: string): Promise<void> =>
    downloadFile(
      `/api/v1/test-management/strategies/${strategyId}/export/word`,
      `test-strategy-${strategyId}.docx`,
    ),

  downloadStrategyPdf: (strategyId: string): Promise<void> =>
    downloadFile(
      `/api/v1/test-management/strategies/${strategyId}/export/pdf`,
      `test-strategy-${strategyId}.pdf`,
    ),

  // Test Suites
  listSuites: (projectId: string | null): Promise<Array<{
    suite_name: string
    test_count: number
    passed_count: number
    failed_count: number
    last_run_at: string | null
    last_run_id: string | null
    pass_rate: number | null
    owner_user_id?: string | null
    owner_email?: string | null
    owner_full_name?: string | null
    owner_is_fallback?: boolean
  }>> =>
    getData('/api/v1/test-management/suites', { params: projectId ? { project_id: projectId } : {} }),

  getSuiteCases: (suiteName: string, projectId: string | null): Promise<Array<{id: string; test_name: string; suite_name: string; status: string; duration_ms: number | null; class_name: string | null; package_name: string | null; created_at: string | null; execution_count?: number; last_execution_at?: string | null}>> =>
    getData(`/api/v1/test-management/suites/${encodeURIComponent(suiteName)}/cases`, { params: projectId ? { project_id: projectId } : {} }),

  // Suite Traceability (TS-5)
  getSuiteMembership: (suiteName: string, projectId: string | null, status?: string): Promise<SuiteMembershipItem[]> =>
    getData(`/api/v1/test-management/suites/${encodeURIComponent(suiteName)}/membership`, { params: { ...(projectId ? { project_id: projectId } : {}), ...(status ? { status } : {}) } }),

  getSuiteChanges: (suiteName: string, projectId: string | null, runId?: string): Promise<SuiteMembershipEventItem[]> =>
    getData(`/api/v1/test-management/suites/${encodeURIComponent(suiteName)}/changes`, { params: { ...(projectId ? { project_id: projectId } : {}), ...(runId ? { run_id: runId } : {}) } }),

  getSuiteDeleted: (suiteName: string, projectId: string | null): Promise<SuiteDeletedItem[]> =>
    getData(`/api/v1/test-management/suites/${encodeURIComponent(suiteName)}/deleted`, { params: projectId ? { project_id: projectId } : {} }),

  // Suite owners (HITL) — migration 0076
  listSuiteOwners: (projectId: string): Promise<SuiteOwnerItem[]> =>
    getData('/api/v1/test-management/suite-owners', { params: { project_id: projectId } }),

  resolveSuiteOwner: (suiteName: string, projectId: string): Promise<SuiteOwnerItem[]> =>
    getData('/api/v1/test-management/suite-owners', { params: { project_id: projectId, suite_name: suiteName } }),

  setSuiteOwner: (suiteName: string, projectId: string, ownerUserId: string | null): Promise<SuiteOwnerItem> =>
    putData(
      `/api/v1/test-management/suite-owners/${encodeURIComponent(suiteName)}`,
      { owner_user_id: ownerUserId },
      { params: { project_id: projectId } },
    ),

  // Suite reviews (HITL on AI analysis) — migration 0076
  listReviewsForRun: (testRunId: string): Promise<SuiteReviewItem[]> =>
    getData(`/api/v1/test-management/suite-reviews/by-run/${testRunId}`),

  listSuiteReviews: (projectId: string, params?: { suite_name?: string; state?: SuiteReviewState }): Promise<SuiteReviewItem[]> =>
    getData('/api/v1/test-management/suite-reviews', { params: { project_id: projectId, ...params } }),

  upsertSuiteReview: (testRunId: string, suiteName: string, state: SuiteReviewState, note?: string): Promise<SuiteReviewItem> =>
    putData(
      `/api/v1/test-management/suite-reviews/by-run/${testRunId}/${encodeURIComponent(suiteName)}`,
      { state, note },
    ),
}

export type SuiteReviewState = 'pending' | 'confirmed' | 'acknowledged' | 'review_later'

export interface SuiteOwnerItem {
  project_id: string
  suite_name: string
  owner_user_id: string | null
  owner_email: string | null
  owner_full_name: string | null
  is_fallback: boolean
}

export interface SuiteReviewItem {
  id: string
  project_id: string
  suite_name: string
  test_run_id: string
  state: SuiteReviewState
  note: string | null
  reviewer_user_id: string | null
  reviewer_email: string | null
  reviewed_at: string | null
  created_at: string
  updated_at: string
}

export interface SuiteMembershipItem {
  id: string
  suite_name: string
  test_fingerprint: string
  test_name: string
  class_name: string | null
  source: string
  status: string
  review_tag: string | null
  last_seen_run_id: string | null
  first_seen_run_id: string | null
  managed_test_case_id: string | null
  created_at: string | null
}

export interface SuiteMembershipEventItem {
  id: string
  suite_name: string
  test_fingerprint: string
  test_name: string
  event_type: 'added' | 'deleted' | 'modified' | 'restored'
  run_id: string | null
  old_values: Record<string, unknown> | null
  new_values: Record<string, unknown> | null
  details: string | null
  created_at: string | null
}

export interface SuiteDeletedItem {
  id: string
  original_suite: string
  test_fingerprint: string
  test_name: string
  class_name: string | null
  status: string
  review_tag: string | null
  deleted_at_run_id: string | null
  last_seen_run_id: string | null
  created_at: string | null
}
export interface UserSummary {
  id: string
  username: string
  full_name?: string
  email: string
}

export const usersService = {
  listUsers: (): Promise<UserSummary[]> =>
    getData('/api/v1/auth/users'),
}

export type {
  AICoverageResponse,
  AIGenerateResponse,
  AIReviewResult,
  AuditLogEntry,
  ManagedTestCase,
  PaginatedResponse,
  TestCaseComment,
  TestCaseReview,
  TestCaseVersion,
  TestPlan,
  TestPlanItem,
  TestStep,
  TestStrategy,
} from '@/types/test-management'
