import { api } from './api'

export interface OnboardingStep {
  key: string
  label: string
  description: string
  status: 'pending' | 'completed' | 'skipped'
  completed_at: string | null
}

export interface OnboardingStatus {
  project_id: string
  steps: OnboardingStep[]
  completed_count: number
  total_count: number
  progress_pct: number
  is_complete: boolean
}

export const onboardingService = {
  getStatus: (projectId: string) =>
    api.get<OnboardingStatus>(`/api/v1/onboarding/${projectId}/status`).then(r => r.data),

  detectProgress: (projectId: string) =>
    api.post<OnboardingStatus>(`/api/v1/onboarding/${projectId}/detect`).then(r => r.data),

  completeStep: (projectId: string, stepKey: string) =>
    api.post<OnboardingStatus>(`/api/v1/onboarding/${projectId}/complete`, { step_key: stepKey }).then(r => r.data),

  skipStep: (projectId: string, stepKey: string) =>
    api.post<OnboardingStatus>(`/api/v1/onboarding/${projectId}/skip`, { step_key: stepKey }).then(r => r.data),

  restoreStep: (projectId: string, stepKey: string) =>
    api.post<OnboardingStatus>(`/api/v1/onboarding/${projectId}/restore`, { step_key: stepKey }).then(r => r.data),

  trackEvent: (eventName: string, projectId?: string, payload?: Record<string, unknown>) =>
    api.post('/api/v1/onboarding/track', { event_name: eventName, project_id: projectId, payload }).catch(() => {}),
}
