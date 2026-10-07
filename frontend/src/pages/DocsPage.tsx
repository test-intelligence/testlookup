/**
 * In-app user documentation.
 *
 * Content lives in `src/content/guide/*.md`, registered in `manifest.ts`. This
 * component is the shell: navigation, filtering, deep links and Markdown
 * rendering. It deliberately holds no prose.
 *
 * Why the split — the previous version carried five sections of hand-written
 * JSX. Prose inside a component is invisible to every tool that reads Markdown,
 * including this repository's Mermaid validator (which walks git-tracked
 * `*.md`), and adding a section meant editing React. Diagrams written in the
 * content files are now checked by CI for free.
 *
 * The documentation's stated values carry over unchanged: every number is read
 * out of the implementation, each claim names the module it came from, and the
 * pages say plainly what the product does NOT do. Documentation that overstates
 * the product is the same defect class as a field reporting a value nothing
 * produces.
 *
 * Routing: `/docs` shows the default page, `/docs/:docId` deep-links one. Both
 * are served by the SPA — the ingress sends the API reference to `/api-docs`.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight, Search as SearchIcon } from 'lucide-react'

import PageHeader from '@/components/ui/PageHeader'
import {
  DOC_GROUPS,
  DOC_PAGES,
  DEFAULT_DOC_ID,
  adjacentDocPages,
  findDocPage,
  type DocPage,
} from '@/content/guide/manifest'
import { DOC_SOURCES } from '@/content/guide/sources'
import { DocMarkdown } from '@/components/help/DocArticle'
import { headingsOf } from '@/components/help/docLinks'

export default function DocsPage() {
  const { docId } = useParams<{ docId?: string }>()
  const navigate = useNavigate()
  const { hash } = useLocation()
  const [filter, setFilter] = useState('')
  const articleRef = useRef<HTMLElement>(null)

  const requested = findDocPage(docId)
  const active: DocPage = requested ?? findDocPage(DEFAULT_DOC_ID) ?? DOC_PAGES[0]
  const source = DOC_SOURCES[active.id] ?? ''
  const toc = useMemo(() => headingsOf(source), [source])
  // `/docs` intentionally uses the default topic's neighbours. An invalid
  // deep link still renders that topic as a safe fallback, but has no pager:
  // advancing from a URL that does not identify a real page would disguise the
  // broken link instead of preserving the manifest helper's `{}` contract.
  const { prev, next } = adjacentDocPages(docId === undefined || requested ? active.id : undefined)

  // Scroll a `#fragment` deep link onto its heading once the Markdown is in the
  // DOM. This is what makes a cross-page anchor (e.g. a link to
  // `/docs/ingestion#suite-name-resolution` from another topic) land on the
  // section rather than the top of the page: the heading only gains its id
  // after React renders the content, which is too late for the browser's own
  // load-time scroll. Re-runs when the topic or the fragment changes, and stays
  // a no-op in environments without `scrollIntoView` (jsdom). A normal topic
  // change has no fragment, so move focus and the app's scrolling `<main>` back
  // to the article start; otherwise clicking Next at the footer would leave the
  // reader at the bottom of the newly rendered topic.
  useEffect(() => {
    const id = decodeURIComponent(hash.replace(/^#/, ''))
    if (id) {
      const el = document.getElementById(id)
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
      return
    }

    const article = articleRef.current
    if (article) {
      article.focus({ preventScroll: true })
      if (typeof article.scrollIntoView === 'function') {
        article.scrollIntoView({ behavior: 'auto', block: 'start' })
      }
    }
  }, [hash, active.id, source])

  const matches = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return DOC_PAGES
    return DOC_PAGES.filter((p) => {
      const haystack = [p.label, p.summary, ...p.keywords, DOC_SOURCES[p.id] ?? '']
        .join(' ')
        .toLowerCase()
      return haystack.includes(q)
    })
  }, [filter])

  return (
    <>
      <PageHeader
        title="Documentation"
        subtitle="How TestLookup works — the actual rules behind the numbers on every page."
      />

      <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(0, 250px) minmax(0, 1fr)' }}>
        <nav className="flex flex-col gap-2 min-w-0" aria-label="Documentation sections">
          <label className="relative block">
            <span className="sr-only">Filter documentation</span>
            <SearchIcon
              className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]"
              aria-hidden="true"
            />
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter topics…"
              className="w-full rounded-lg border pl-8 pr-2 py-1.5 text-[13px] bg-transparent"
              style={{ borderColor: 'var(--color-border)', color: 'var(--color-text)' }}
            />
          </label>

          {matches.length === 0 && (
            <p className="px-2 py-3 text-[13px] text-[var(--color-text-muted)]">
              No topic matches “{filter}”. Try a feature name such as “flaky”, “ingest” or
              “release gate”.
            </p>
          )}

          {DOC_GROUPS.map((group) => {
            const pages = matches.filter((p) => p.group === group)
            if (pages.length === 0) return null
            return (
              <div key={group}>
                <div className="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">
                  {group}
                </div>
                {pages.map((p) => {
                  const Icon = p.icon
                  const on = p.id === active.id
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => navigate(`/docs/${p.id}`)}
                      aria-current={on ? 'page' : undefined}
                      className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[13px] text-left transition-colors"
                      style={{
                        background: on
                          ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)'
                          : 'transparent',
                        color: on ? 'var(--color-accent)' : 'var(--color-text-muted)',
                      }}
                    >
                      <Icon className="h-4 w-4 flex-none" aria-hidden="true" />
                      <span className="min-w-0">{p.label}</span>
                      {on && <ChevronRight className="h-3.5 w-3.5 ml-auto flex-none" aria-hidden="true" />}
                    </button>
                  )
                })}
              </div>
            )
          })}
        </nav>

        <div className="min-w-0">
          <div className="card p-5 min-w-0">
            <p className="text-[12px] text-[var(--color-text-muted)] mb-1">
              {active.group} · {active.summary}
            </p>

            {toc.length > 2 && (
              <details className="mb-4 rounded-lg border p-3" style={{ borderColor: 'var(--color-border)' }}>
                <summary className="text-[13px] font-medium text-[var(--color-text)] cursor-pointer">
                  On this page
                </summary>
                <ul className="mt-2 space-y-1">
                  {toc.map((h) => (
                    <li key={h.id} style={{ paddingLeft: h.depth === 3 ? 12 : 0 }}>
                      <a
                        href={`#${h.id}`}
                        className="text-[13px] underline underline-offset-2"
                        style={{ color: 'var(--color-accent)' }}
                      >
                        {h.text}
                      </a>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            <article ref={articleRef} tabIndex={-1} aria-label={active.label}>
              {source ? (
                <DocMarkdown source={source} />
              ) : (
                <p className="text-[13.5px] text-[var(--color-text-muted)]">
                  This topic has no content yet.
                </p>
              )}
            </article>

            {/*
              Sequential pager. The sidebar groups topics by area, which never
              tells a reader what comes NEXT; this lets someone read the guide
              straight through — the common self-host first-read — without
              returning to the sidebar to find the following topic. Ordering is
              DOC_PAGES' reading order (see adjacentDocPages). A missing prev/next
              (first/last topic) simply leaves that side empty, keeping the
              present link on its usual edge via the spacer.
            */}
            {(prev || next) && (
              <nav
                aria-label="Documentation pages"
                className="mt-6 pt-4 border-t border-[var(--color-border)] flex items-stretch justify-between gap-3"
              >
                {prev ? (
                  <button
                    type="button"
                    onClick={() => navigate(`/docs/${prev.id}`)}
                    rel="prev"
                    className="group flex-1 min-w-0 flex items-center gap-2 rounded-lg border border-[var(--color-border)] px-3 py-2.5 text-left transition-colors hover:border-[var(--color-accent)] hover:bg-[var(--color-bg-secondary)]"
                  >
                    <ChevronLeft className="h-4 w-4 flex-none text-[var(--color-text-muted)] group-hover:text-[var(--color-accent)]" aria-hidden="true" />
                    <span className="min-w-0">
                      <span className="block text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">Previous</span>
                      <span className="block truncate text-[13px] font-medium text-[var(--color-text)]">{prev.label}</span>
                    </span>
                  </button>
                ) : (
                  <span className="flex-1" aria-hidden="true" />
                )}
                {next ? (
                  <button
                    type="button"
                    onClick={() => navigate(`/docs/${next.id}`)}
                    rel="next"
                    className="group flex-1 min-w-0 flex items-center justify-end gap-2 rounded-lg border border-[var(--color-border)] px-3 py-2.5 text-right transition-colors hover:border-[var(--color-accent)] hover:bg-[var(--color-bg-secondary)]"
                  >
                    <span className="min-w-0">
                      <span className="block text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">Next</span>
                      <span className="block truncate text-[13px] font-medium text-[var(--color-text)]">{next.label}</span>
                    </span>
                    <ChevronRight className="h-4 w-4 flex-none text-[var(--color-text-muted)] group-hover:text-[var(--color-accent)]" aria-hidden="true" />
                  </button>
                ) : (
                  <span className="flex-1" aria-hidden="true" />
                )}
              </nav>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
