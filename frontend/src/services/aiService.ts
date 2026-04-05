import type { AnalysisResult, AnalyzeRequest } from '@/types/ai'
import { postData } from './http'

export const aiService = {
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
