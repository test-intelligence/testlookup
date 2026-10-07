import { useCallback, useEffect, useRef, type RefObject } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import SidePanel from '@/components/ui/SidePanel'
import { useHelpStore } from '@/store/helpStore'
import { DEFAULT_DOC_ID, findDocPage } from '@/content/guide/manifest'
import { DOC_SOURCES } from '@/content/guide/sources'
import { DocMarkdown } from './DocArticle'
import { DocLinkFollowContext, parseDocHref } from './docLinks'


/**
 * Contextual help (UX redesign P1): one documentation topic, beside the page.
 *
 * Opened by the Help menu's "Help for this page" and by a page title's **?**
 * (`openHelp`). Non-modal: the reader keeps using the page while it is open.
 * A link to another topic opens it here; a `#section` link scrolls here; a
 * link into the app navigates, and the drawer stays. "Open in full docs" goes
 * to the same topic and section on `/docs`.
 */
export default function HelpDrawer({ reserveSpaceIn }: { reserveSpaceIn?: RefObject<HTMLElement | null> }) {
  const topic = useHelpStore((s) => s.topic)
  const anchor = useHelpStore((s) => s.anchor)
  const open = useHelpStore((s) => s.open)
  const close = useHelpStore((s) => s.close)
  const bodyRef = useRef<HTMLDivElement>(null)

  const page = topic === null ? undefined : findDocPage(topic) ?? findDocPage(DEFAULT_DOC_ID)
  const source = page ? DOC_SOURCES[page.id] ?? '' : ''

  const scrollTo = useCallback((id: string | null) => {
    const body = bodyRef.current
    if (!body) return
    // No section: the topic's start (the panel scrolls, not this element).
    const target = id ? body.querySelector<HTMLElement>(`[id="${id}"]`) : body
    if (target && typeof target.scrollIntoView === 'function') target.scrollIntoView({ block: 'start' })
  }, [])

  // A new topic or section: land on it once the Markdown is in the DOM.
  useEffect(() => {
    if (page) scrollTo(anchor)
  }, [page, anchor, scrollTo])

  const follow = useCallback(
    (href: string) => {
      if (href.startsWith('#')) {
        scrollTo(href.slice(1))
        return true
      }
      const doc = parseDocHref(href)
      if (doc && findDocPage(doc.topic)) {
        open(doc.topic, doc.anchor)
        return true
      }
      return false
    },
    [open, scrollTo],
  )

  if (!page) return null
  const fullDocs = `/docs/${page.id}${anchor ? `#${anchor}` : ''}`

  return (
    <SidePanel
      open
      onClose={close}
      title={page.label}
      width={420}
      reserveSpaceIn={reserveSpaceIn}
      closeLabel="Close help"
      footer={
        <Link
          to={fullDocs}
          onClick={close}
          data-help-full-docs=""
          className="inline-flex items-center gap-1.5 text-[13px] font-medium text-[var(--color-accent)] hover:underline"
        >
          Open in full docs
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
      }
    >
      {/* No catalogue summary line: every topic opens with its own summary
          sentence, and the two read as the same thing said twice. */}
      <div ref={bodyRef} data-help-topic={page.id} className="min-w-0">
        <DocLinkFollowContext.Provider value={follow}>
          <DocMarkdown source={source} hideTitle />
        </DocLinkFollowContext.Provider>
      </div>
    </SidePanel>
  )
}
