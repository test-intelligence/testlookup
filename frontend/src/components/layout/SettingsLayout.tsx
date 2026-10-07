/**
 * Settings and admin, one place (UX redesign P5, `02-design-spec.md` §4): a
 * 220 px grouped sub-nav beside the page. It replaces the 22-card index as the
 * way between settings pages, and `SettingsBackBar` as the way back.
 *
 * Rendered by `AppLayout`, keyed off the route (`isSettingsAreaPath`), never
 * by the pages — the rule `SettingsBackBar` was built on (BUG-009: 14 of 23
 * sub-pages had no way back because each page had to remember one). A new
 * `/settings/<thing>` route gets the layout without its author doing
 * anything; it appears in the sub-nav once `settingsNav.ts` lists it.
 *
 * The groups are role-filtered (`settingsGroupsFor`): a viewer on their own
 * profile sees Account (and the AI pipeline pages anyone can open), never a
 * link that would redirect them away.
 *
 * "All settings" (the `/settings` index) heads the list for those who can open
 * it: BUG-009's guarantee, a way back to the index from every sub-page, kept
 * now that the breadcrumb is gone (`probe-jr-homelab-verify.spec.ts`).
 */
import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { clsx } from 'clsx'
import { usePermissions } from '@/hooks/usePermissions'
import { activeSettingsItem, SETTINGS_ROOT, settingsGroupsFor, settingsItemHref } from './settingsNav'

const LINK = 'block truncate rounded-md px-2 py-1 text-[13px] transition-colors'
const LINK_CURRENT = 'bg-[var(--color-bg-secondary)] font-medium text-[var(--color-text)]'
const LINK_IDLE = 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]'

export default function SettingsLayout({ children }: { children: ReactNode }) {
  const { pathname, search } = useLocation()
  const { canAccessManagement, canGenerateApiKeys } = usePermissions()
  const groups = settingsGroupsFor({ canAccessManagement, canOwnApiKeys: canGenerateApiKeys, isDev: import.meta.env.DEV })
  const active = activeSettingsItem(pathname, search, groups)
  const onIndex = pathname === SETTINGS_ROOT || pathname === `${SETTINGS_ROOT}/`

  return (
    <div data-settings-layout="" className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
      <nav aria-label="Settings" className="lg:sticky lg:top-0 lg:self-start">
        <div className="space-y-4">
          {canAccessManagement && (
            <Link
              to={SETTINGS_ROOT}
              data-settings-item="index"
              aria-current={onIndex ? 'page' : undefined}
              className={clsx(LINK, onIndex ? LINK_CURRENT : LINK_IDLE)}
            >
              All settings
            </Link>
          )}
          {groups.map((group) => (
            <div key={group.id} data-settings-group={group.id}>
              <p className="mb-1 px-2 text-[10.5px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                {group.label}
              </p>
              <ul className="space-y-px">
                {group.items.map((item) => {
                  const current = item === active
                  return (
                    <li key={item.id}>
                      <Link
                        to={settingsItemHref(item)}
                        data-settings-item={item.id}
                        aria-current={current ? 'page' : undefined}
                        className={clsx(LINK, current ? LINK_CURRENT : LINK_IDLE)}
                      >
                        {item.label}
                      </Link>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>
      </nav>
      <div data-settings-content="" className="min-w-0">
        {children}
      </div>
    </div>
  )
}
