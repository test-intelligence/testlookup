import { useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { BookOpen, ChartLine, CircleHelp, LifeBuoy, PanelRightOpen, Rocket } from 'lucide-react'
import { HeaderPopover } from '@/components/ui/HeaderPopover'
import { openHelp } from '@/store/helpStore'
import { helpTopicFor } from '@/components/help/helpTopics'
import AppVersionBadge from './AppVersionBadge'

const ITEM =
  'flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)] transition-colors'

const LINKS = [
  { to: '/getting-started', label: 'Getting started', icon: Rocket },
  { to: '/docs', label: 'Documentation', icon: BookOpen },
  { to: '/docs/charts', label: 'Chart guide', icon: ChartLine },
  { to: '/docs/troubleshooting', label: 'Troubleshooting', icon: LifeBuoy },
] as const

/**
 * The top bar's **?** (UX redesign P1): help for the page in front of you,
 * then the guides that left the sidebar, then which build this is (with
 * one-click copy for a bug report, moved here from the sidebar footer).
 */
export default function HelpMenu() {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const { pathname } = useLocation()

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        data-help-menu=""
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Help"
        title="Help"
        onClick={() => setOpen((v) => !v)}
        className="p-2 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] transition-colors"
      >
        <CircleHelp className="w-5 h-5" aria-hidden="true" />
      </button>
      <HeaderPopover anchorRef={triggerRef} open={open} onClose={() => setOpen(false)} width={240} ariaLabel="Help">
        <div className="py-1">
          <button
            type="button"
            role="menuitem"
            data-help-this-page=""
            className={ITEM}
            onClick={() => {
              const { topic, anchor } = helpTopicFor(pathname)
              openHelp(topic, anchor)
              setOpen(false)
            }}
          >
            <PanelRightOpen className="h-4 w-4 shrink-0" aria-hidden="true" />
            Help for this page
          </button>
          <div className="my-1 border-t border-[var(--color-border)]" />
          {LINKS.map(({ to, label, icon: Icon }) => (
            <Link key={to} to={to} role="menuitem" className={ITEM} onClick={() => setOpen(false)}>
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {label}
            </Link>
          ))}
          <div className="mt-1 border-t border-[var(--color-border)] px-0 pb-1 pt-1.5">
            <AppVersionBadge />
          </div>
        </div>
      </HeaderPopover>
    </div>
  )
}
