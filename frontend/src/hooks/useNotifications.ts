import useSWR, { mutate } from 'swr'
import { notificationService } from '../services/notificationService'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export function useNotificationPreferences() {
  return useSWR('notifications/preferences', notificationService.listPreferences, {
    revalidateOnFocus: false,
  })
}

export function useNotificationHistory(unreadOnly = false) {
  return useSWR(
    ['notifications/history', unreadOnly],
    () => notificationService.listHistory(unreadOnly),
    { refreshInterval: REFRESH_INTERVALS.POLLING },  // poll every 30s for new notifications
  )
}

export function useUnreadCount() {
  return useSWR('notifications/unread', notificationService.unreadCount, {
    refreshInterval: REFRESH_INTERVALS.POLLING,
  })
}

export async function invalidateNotifications() {
  await mutate('notifications/preferences')
  await mutate('notifications/unread')
  await mutate(key => Array.isArray(key) && key[0] === 'notifications/history')
}
