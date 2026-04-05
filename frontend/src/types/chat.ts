export interface ChatSession {
  id: string
  project_id: string | null
  title: string | null
  created_at: string
  updated_at: string
}

export interface ChatSource {
  type: string
  id?: string
  label?: string
}

export interface ChatMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant'
  content: string
  sources: ChatSource[] | null
  created_at: string
}

export interface RunSummary {
  test_run_id: string
  project_id: string
  build_number: string
  executive_summary: string
  markdown_report: string | null
  anomaly_count: number
  is_regression: boolean
  analysis_count: number
  generated_at: string
  is_stub?: boolean
}
