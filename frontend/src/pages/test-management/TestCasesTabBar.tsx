import { useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import Tabs from '@/components/ui/Tabs'
import { HeaderPopover } from '@/components/ui/HeaderPopover'
import { TM_MORE_TABS, TM_PRIMARY_TABS, isMoreTab, type TmTab } from './tabs'

type BarValue = (typeof TM_PRIMARY_TABS)[number]['id'] | 'more'

/**
 * Cases · Suites · Plans · Approvals · More ▾ (UX redesign P4 item 7). The
 * fifth tab is a menu: it opens a list of the four long-tail sections and
 * picking one selects it; while one of them is open, the tab is selected and
 * names it. Built from the kit's `Tabs` and `HeaderPopover` (the page
 * header's menu), not a new primitive.
 */
export default function TestCasesTabBar({ value, onChange }: { value: TmTab; onChange: (next: TmTab) => void }) {
  const barRef = useRef<HTMLDivElement>(null)
  const anchorRef = useRef<HTMLElement | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const activeMore = isMoreTab(value) ? TM_MORE_TABS.find((t) => t.id === value) : undefined

  const items = [
    ...TM_PRIMARY_TABS.map((t) => ({ id: t.id as BarValue, label: t.label })),
    {
      id: 'more' as BarValue,
      label: (
        <span data-more-tab="" className="inline-flex items-center gap-1">
          More{activeMore ? <span className="text-[var(--color-text-muted)]">: {activeMore.label}</span> : null}
          <ChevronDown aria-hidden="true" className={clsx('h-3.5 w-3.5 transition-transform', menuOpen && 'rotate-180')} />
        </span>
      ),
    },
  ]

  return (
    <div ref={barRef}>
      <Tabs<BarValue>
        ariaLabel="Test case sections"
        value={activeMore ? 'more' : (value as BarValue)}
        onChange={(next) => {
          if (next !== 'more') {
            setMenuOpen(false)
            onChange(next)
            return
          }
          // The menu hangs from the More tab itself (an event, not a render:
          // the ref is written where the tab is known to be in the DOM).
          anchorRef.current = barRef.current?.querySelector<HTMLElement>('[data-tab="more"]') ?? null
          setMenuOpen((open) => !open)
        }}
        items={items}
      />
      <HeaderPopover anchorRef={anchorRef} open={menuOpen} onClose={() => setMenuOpen(false)} width={200} ariaLabel="More sections">
        <div className="p-1">
          {TM_MORE_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="menuitem"
              aria-current={t.id === value ? 'true' : undefined}
              onClick={() => {
                setMenuOpen(false)
                onChange(t.id)
              }}
              className={clsx(
                'flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left text-[13px] hover:bg-[var(--color-bg-hover)]',
                t.id === value ? 'font-semibold text-[var(--color-text)]' : 'text-[var(--color-text-secondary)]',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </HeaderPopover>
    </div>
  )
}
