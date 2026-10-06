import { useId, useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'

const STORAGE_PREFIX = 'tl.disclosure.'

function readStored(key: string | undefined, fallback: boolean): boolean {
  if (!key) return fallback
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + key)
    return raw === null ? fallback : raw === '1'
  } catch {
    return fallback
  }
}

function writeStored(key: string | undefined, open: boolean): void {
  if (!key) return
  try {
    localStorage.setItem(STORAGE_PREFIX + key, open ? '1' : '0')
  } catch {
    // Storage blocked: the state lasts for this visit only.
  }
}

/**
 * A collapsed "Details / How this was decided / Advanced" section (UX redesign
 * P0), replacing ad-hoc `<details>`. Closed by default; with `persistKey` the
 * reader's choice is remembered in this browser.
 *
 * The content is not rendered while closed, so a closed section asks for no
 * data and draws no chart.
 */
export default function Disclosure({
  title,
  summary,
  defaultOpen = false,
  persistKey,
  children,
  className,
}: {
  title: ReactNode
  /** A one-line note on the right of the header, e.g. "3 checks". */
  summary?: ReactNode
  defaultOpen?: boolean
  persistKey?: string
  children: ReactNode
  className?: string
}) {
  const [open, setOpen] = useState(() => readStored(persistKey, defaultOpen))
  const contentId = useId()
  const toggle = () => {
    setOpen((was) => {
      writeStored(persistKey, !was)
      return !was
    })
  }
  return (
    <section data-disclosure="" data-open={open ? 'true' : 'false'} className={clsx('card !p-0', className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={toggle}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]"
      >
        <ChevronRight aria-hidden="true" className={clsx('h-4 w-4 shrink-0 transition-transform', open && 'rotate-90')} />
        <span className="min-w-0 flex-1">{title}</span>
        {summary !== undefined && <span className="shrink-0 text-xs text-[var(--color-text-muted)]">{summary}</span>}
      </button>
      {open && (
        <div id={contentId} className="border-t border-[var(--color-border)] p-4">
          {children}
        </div>
      )}
    </section>
  )
}
