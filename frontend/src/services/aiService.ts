import type { AnalysisResult, AnalyzeRequest } from '@/types/ai'
import { getData, postData } from './http'

export const aiService = {
  /** Fetch a previously stored analysis without triggering a new LLM run.
   *  Resolves to null when no analysis exists yet (404). */
  getAnalysis: (testCaseId: string): Promise<AnalysisResult | null> =>
    getData<AnalysisResult>(`/api/v1/analyze/${testCaseId}`).catch((err) => {
      if (err?.response?.status === 404) return null
      throw err
    }),

  analyze: (request: AnalyzeRequest): Promise<AnalysisResult> =>
    postData('/api/v1/analyze', request),

  createJiraTicket: (payload: {
    project_key: string
    test_case_id: string
    test_name: string
    run_id: string
    ai_summary: string
    recommended_action: string
  }) => postData<{ ticket_key: string; ticket_url: string }, {
    project_key: string
    test_case_id: string
    test_name: string
    run_id: string
    ai_summary: string
    recommended_action: string
  }>('/api/v1/integrations/jira', payload),
}
export type { AnalysisResult, AnalyzeRequest }
