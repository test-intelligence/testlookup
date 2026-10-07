/**
 * Which pages render inside the settings layout (UX redesign P5).
 *
 * Apart from `settingsNav.ts` (the groups, the items, their words) on purpose:
 * `AppLayout` asks this on every page, so it is in the eager bundle, and the
 * data is not — it rides with the lazily loaded `SettingsLayout` and the
 * `/settings` index (the eager budget had 616 B of headroom). Only AppLayout
 * imports this: it imports navConfig, and a lazy chunk importing it too made
 * the bundler split the sidebar's icons into seven shared eager chunks
 * (+465 B gzip of chunk overhead). `SETTINGS_ROOT` is settingsNav's.
 */
import { ADMIN_ITEM } from './navConfig'

function under(prefix: string, pathname: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`)
}

/**
 * `/settings` and every page under it, and the admin pages the sidebar's Admin
 * item owns (`/projects`, `/users`, `/ownership`, `/activity`, `/agents`,
 * `/agents/workflows`). Not `/policies`: its home is Releases (the sidebar's
 * Releases item owns it, with its section tabs); the settings sub-nav links
 * there rather than draw a second set of chrome around it. A prefix needs its
 * separator: `/settingsfoo` is not under `/settings`.
 */
export function isSettingsAreaPath(pathname: string): boolean {
  return under('/settings', pathname) || ADMIN_ITEM.owns.some((prefix) => under(prefix, pathname))
}
