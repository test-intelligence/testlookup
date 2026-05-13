export interface ApiKey {
  id: string
  name: string
  key_hint: string
  scopes: string[]
  project_id: string | null
  is_active: boolean
  expires_at: string | null
  last_used_at: string | null
  created_at: string
}

export interface ApiKeyCreatePayload {
  name: string
  scopes?: string[]
  expires_days?: number | null
  project_id?: string | null
  target_user_id?: string | null
}

export interface ApiKeyCreatedResponse extends ApiKey {
  raw_key: string
}
