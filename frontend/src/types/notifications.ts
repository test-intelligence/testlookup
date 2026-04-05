export type NotificationChannel = 'email' | 'slack' | 'teams'

export type NotificationEventType =
  | 'run_failed'
  | 'run_passed'
  | 'high_failure_rate'
  | 'ai_analysis_complete'
  | 'quality_gate_failed'
  | 'flaky_test_detected'

export interface NotificationPreference {
  id: string
  user_id: string
  project_id: string | null
  channel: NotificationChannel
  enabled: boolean
  events: NotificationEventType[]
  failure_rate_threshold: number
  email_override: string | null
  slack_webhook_url: string | null
  teams_webhook_url: string | null
  created_at: string
  updated_at: string | null
}

export interface NotificationPreferencePayload {
  project_id?: string | null
  channel: NotificationChannel
  enabled: boolean
  events: NotificationEventType[]
  failure_rate_threshold: number
  email_override?: string | null
  slack_webhook_url?: string | null
  teams_webhook_url?: string | null
}

export interface NotificationLog {
  id: string
  channel: NotificationChannel
  event_type: NotificationEventType
  title: string
  body: string
  status: 'pending' | 'sent' | 'failed'
  is_read: boolean
  sent_at: string | null
  created_at: string
}
