import { Link, useLocation } from 'react-router-dom'
import { Activity, LayoutDashboard, Search } from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import { usePermissions } from '@/hooks/usePermissions'

/**
 * HON-09: what an unknown in-app path shows, inside the shell (sidebar and
 * top bar stay). The catch-all used to `<Navigate to="/overview" replace />`:
 * a mistyped or retired link rewrote the URL and landed on the dashboard as if
 * it had been asked for, so neither the reader nor a test could tell a broken
 * link from a working one.
 *
 * The attempted path is rendered as text (React escapes it), never as markup
 * or a link. Activity is offered only to a role that can open it (it is a
 * management route; anyone else would be bounced to Overview).
 */
export default function NotFoundPage() {
  const { pathname, search } = useLocation()
  const { canAccessManagement } = usePermissions()
  const attempted = `${pathname}${search}`

  const links = [
    { to: '/overview', label: 'Overview', icon: LayoutDashboard },
    { to: '/search', label: 'Search', icon: Search },
    ...(canAccessManagement ? [{ to: '/activity', label: 'Activity', icon: Activity }] : []),
  ]

  return (
    <div className="space-y-4" data-testid="not-found-page">
      <PageHeader compact title="Page not found" />
      <section className="card max-w-2xl space-y-3 !p-5">
        <p className="m-0 text-[15px] font-semibold text-[var(--color-text)]">This page does not exist.</p>
        <p className="m-0 text-[13px] text-[var(--color-text-secondary)]">
          Nothing in TestLookup lives at{' '}
          <code
            data-testid="not-found-path"
            className="break-all rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-1 font-mono text-[12px]"
          >
            {attempted}
          </code>
          . The link may be mistyped, or the page may have moved.
        </p>
        <nav aria-label="Where to go instead" className="flex flex-wrap gap-2 pt-1">
          {links.map(({ to, label, icon: Icon }) => (
            <Link key={to} to={to} className="btn-secondary !py-1 text-sm inline-flex items-center gap-1.5">
              <Icon className="h-4 w-4" aria-hidden /> {label}
            </Link>
          ))}
        </nav>
      </section>
    </div>
  )
}
