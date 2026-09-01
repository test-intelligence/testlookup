import { getData, postData } from './http'

/** Prompts a user can dismiss. Mirrors `services/ui_dismissal_service.KNOWN_DISMISSAL_KEYS`. */
export const RETENTION_ACTIVATION_NUDGE = 'retention_activation_nudge'

export interface UIDismissalList {
  dismissed: string[]
}

/**
 * Per-user UI dismissals — remembering that someone said "not now".
 *
 * Server-side on purpose. `localStorage` was cheaper and wrong: the same
 * operator on a second machine would be re-prompted to enable a destructive
 * background job they had already declined.
 *
 * POST is idempotent (`ON CONFLICT DO NOTHING`) and returns the full list, so
 * a double-clicked button cannot error and the caller can drop the response
 * straight into the SWR cache.
 *
 * Rides the shared Axios base in `services/api.ts` via the `http` helpers.
 */
export const uiDismissalService = {
  list: () => getData<UIDismissalList>('/api/v1/auth/me/dismissals'),

  dismiss: (dismissalKey: string) =>
    postData<UIDismissalList, { dismissal_key: string }>(
      '/api/v1/auth/me/dismissals',
      { dismissal_key: dismissalKey },
    ),
}
