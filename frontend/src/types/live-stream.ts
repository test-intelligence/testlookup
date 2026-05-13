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
  launch_name?: string
  suite_name?: string
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
  /** Canonical TestRun.id this session resolves to. Use this — not `run_id`
   *  — when constructing /runs/<id> links: SDKs frequently emit non-UUID
   *  slugs (e.g. `local-abc12345`) and the backend stores the TestRun under
   *  `uuid5(NAMESPACE_DNS, run_id)`. The legacy `run_id` field is preserved
   *  for display and for deduping by build identity. */
  test_run_id?: string | null
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
  launch_name?: string
  suite_name?: string | null
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
