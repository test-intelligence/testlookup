export interface LiveSessionCreate {
  project_id: string
  run_id?: string
  client_name: string
  machine_id?: string
  build_number?: string
  framework?: string
  branch?: string
  commit_hash?: string
  total_tests?: number
  release_name?: string
  metadata?: Record<string, unknown>
}

export interface LiveSessionResponse {
  session_id: string
  session_token: string
  run_id: string
  project_id: string
  expires_in: number
  created_at: string
}

export interface LiveSessionState {
  run_id: string
  project_id: string
  build_number: string
  status: string
  total: number
  passed: number
  failed: number
  skipped: number
  broken: number
  pass_rate: number
  current_test?: string
  started_at?: string
  last_event_at?: string
  client_name?: string
  completed_at?: string
  release_name?: string
}

export interface ActiveSessionsResponse {
  sessions: LiveSessionState[]
  count: number
}

export interface SessionDetail extends LiveSessionState {
  session_id: string
  client_name: string
  machine_id?: string
  framework?: string
  branch?: string
  events_received: number
  completed_at?: string
}
