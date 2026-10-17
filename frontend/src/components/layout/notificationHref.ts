/**
 * NTF-01: where a bell entry links, if anywhere.
 *
 * The server sets `link` only from its own allow-list of two path shapes
 * (`gate_notifications.APP_PATH_RE`): a release (`/releases/<uuid>`) and a
 * run's release decision (`/release-gate/<uuid>`). The SPA re-checks the shape
 * (a link is never built from text, and nothing else is followed) and the
 * role: `/releases` is a management route (QA lead and admin), so for anyone
 * else that entry stays text; `/release-gate/<run>` is open to every role.
 *
 * Pure. Never throws.
 */
import type { NotificationLog } from '@/types/notifications'

const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
const RELEASE_GATE_PATH = new RegExp(`^/release-gate/${UUID}$`)
const RELEASE_PATH = new RegExp(`^/releases/${UUID}$`)

export function notificationHref(
  log: Pick<NotificationLog, 'link'> | null | undefined,
  opts: { canAccessManagement: boolean },
): string | null {
  const link = log?.link
  if (typeof link !== 'string') return null
  if (RELEASE_GATE_PATH.test(link)) return link
  if (RELEASE_PATH.test(link)) return opts.canAccessManagement ? link : null
  return null
}
