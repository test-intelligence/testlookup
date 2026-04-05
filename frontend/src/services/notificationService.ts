import type {
  NotificationChannel,
  NotificationLog,
  NotificationPreference,
  NotificationPreferencePayload,
} from '@/types/notifications'
import { deleteData, getData, postData, putData } from './http'

export const notificationService = {
  listPreferences: () =>
    getData<NotificationPreference[]>('/api/v1/notifications/preferences'),

  upsertPreference: (data: NotificationPreferencePayload) =>
    postData<NotificationPreference, NotificationPreferencePayload>('/api/v1/notifications/preferences', data),

  updatePreference: (id: string, data: NotificationPreferencePayload) =>
    putData<NotificationPreference, NotificationPreferencePayload>(`/api/v1/notifications/preferences/${id}`, data),

  deletePreference: (id: string) =>
    deleteData(`/api/v1/notifications/preferences/${id}`),

  listHistory: (unreadOnly = false, limit = 50) =>
    getData<NotificationLog[]>('/api/v1/notifications/history', {
      params: { unread_only: unreadOnly, limit },
    }),

  unreadCount: () =>
    getData<{ unread: number }>('/api/v1/notifications/history/unread-count'),

  markRead: (id: string) =>
    postData(`/api/v1/notifications/history/${id}/read`),

  markAllRead: () =>
    postData('/api/v1/notifications/history/read-all'),

  sendTest: (channel: NotificationChannel, preferenceId?: string) =>
    postData('/api/v1/notifications/test', { channel, preference_id: preferenceId ?? null }),
}
export type {
  NotificationChannel,
  NotificationEventType,
  NotificationLog,
  NotificationPreference,
  NotificationPreferencePayload,
} from '@/types/notifications'
