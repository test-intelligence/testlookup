import useSWR, { mutate } from 'swr'
import { notificationService } from '../services/notificationService'

export function useNotificationPreferences() {
  return useSWR('notifications/preferences', notificationService.listPreferences, {
    revalidateOnFocus: false,
  })
}

export function useNotificationHistory(unreadOnly = false) {
  return useSWR(
    ['notifications/history', unreadOnly],
    () => notificationService.listHistory(unreadOnly),
    { refreshInterval: 30_000 },  // poll every 30s for new notifications
  )
}

export function useUnreadCount() {
  return useSWR('notifications/unread', notificationService.unreadCount, {
    refreshInterval: 30_000,
  })
}

export async function invalidateNotifications() {
  await mutate('notifications/preferences')
  await mutate('notifications/unread')
  await mutate(key => Array.isArray(key) && key[0] === 'notifications/history')
}
