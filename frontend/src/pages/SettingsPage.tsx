/**
 * The settings index (UX redesign P5, `02-design-spec.md` §4): the settings
 * sub-nav's groups as compact lists, one line per page (its name and what it
 * is for), instead of 22 cards in two columns.
 *
 * The same data as the sub-nav beside it (`settingsNav.ts`), role-filtered
 * the same way, so the index can never offer a page the sub-nav does not, or
 * the reverse. The route is QA lead and admin only.
 */
import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import { usePermissions } from '@/hooks/usePermissions'
import { helpTopicParam } from '@/components/help/helpTopics'
import { settingsGroupsFor, settingsItemHref } from '@/components/layout/settingsNav'

export default function SettingsPage() {
  const { canAccessManagement, canGenerateApiKeys } = usePermissions()
  const groups = settingsGroupsFor({ canAccessManagement, canOwnApiKeys: canGenerateApiKeys, isDev: import.meta.env.DEV })

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Settings"
        subtitle="Your account, the project, its integrations and the AI"
        helpTopic={helpTopicParam('/settings')}
      />
      <div className="grid gap-4 xl:grid-cols-2" data-settings-index="">
        {groups.map((group) => (
          <section key={group.id} aria-labelledby={`settings-group-${group.id}`} className="card !p-0 overflow-hidden">
            <h2
              id={`settings-group-${group.id}`}
              className="border-b border-[var(--color-border)] px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]"
            >
              {group.label}
            </h2>
            <ul>
              {group.items.map((item) => (
                <li key={item.id} className="border-b border-[var(--color-border)] last:border-b-0">
                  <Link
                    to={settingsItemHref(item)}
                    data-settings-index-item={item.id}
                    className="flex items-center gap-3 px-4 py-2 hover:bg-[var(--color-bg-hover)]"
                  >
                    <span className="w-48 shrink-0 truncate text-[13px] font-medium text-[var(--color-text)]">{item.label}</span>
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-[var(--color-text-muted)]">{item.description}</span>
                    <ChevronRight className="h-4 w-4 shrink-0 text-[var(--color-text-faint)]" aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  )
}
