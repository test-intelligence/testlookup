import useSWR from 'swr'
import { appMutate } from '@/utils/swrCacheMutate'
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
  await appMutate('notifications/preferences')
  await appMutate('notifications/unread')
  await appMutate(key => Array.isArray(key) && key[0] === 'notifications/history')
}
