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
import { isValidElement, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ChevronRight, Search as SearchIcon } from 'lucide-react'

import PageHeader from '@/components/ui/PageHeader'
import MermaidDiagram from '@/components/guide/MermaidDiagram'
import {
  DOC_GROUPS,
  DOC_PAGES,
  DEFAULT_DOC_ID,
  findDocPage,
  type DocPage,
} from '@/content/guide/manifest'
import { DOC_SOURCES } from '@/content/guide/sources'
import { slugify } from '@/content/guide/slug'

/** Headings for the on-page table of contents. */
function headingsOf(markdown: string): { depth: number; text: string; id: string }[] {
  const out: { depth: number; text: string; id: string }[] = []
  let inFence = false
  for (const line of markdown.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      inFence = !inFence
      continue
    }
    if (inFence) continue
    const m = /^(#{2,3})\s+(.*)$/.exec(line)
    if (m) out.push({ depth: m[1].length, text: m[2].trim(), id: slugify(m[2].trim()) })
  }
  return out
}

/**
 * Anchor renderer for documentation Markdown.
 *
 * In-app links — another topic (`/docs/…`) or a same-page section
 * (`#fragment`) — navigate client-side through react-router instead of the
 * browser's default full document load. Two reasons this matters:
 *
 *  - A full reload reboots the whole SPA (re-runs auth, refetches everything)
 *    for a link that never leaves the app. On an air-gapped self-host that is
 *    pure cost for a cross-reference between two doc pages.
 *  - A cross-page `#fragment` cannot land on its heading through a plain load:
 *    the browser tries to scroll before React has rendered the Markdown, so
 *    the id does not exist yet and the reader arrives at the top of the page.
 *    Navigating client-side keeps the fragment in `location.hash`, which
 *    `DocsPage`'s scroll effect resolves once the content is in the DOM.
 *
 * External links (anything with a scheme or protocol-relative host) open in a
 * new isolated tab. Modified clicks (⌘/Ctrl/middle) keep their native
 * open-in-new-tab behavior.
 */
function DocLink({ href, children }: { href?: string; children?: React.ReactNode }) {
  const navigate = useNavigate()
  const className = 'underline underline-offset-2'
  const style = { color: 'var(--color-accent)' }
  const internal = href != null && (href.startsWith('/') || href.startsWith('#'))
  if (!internal) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className={className} style={style}>
        {children}
      </a>
    )
  }
  return (
    <a
      href={href}
      className={className}
      style={style}
      onClick={(e) => {
        if (
          e.defaultPrevented ||
          e.button !== 0 ||
          e.metaKey ||
          e.ctrlKey ||
          e.shiftKey ||
          e.altKey
        ) {
          return
        }
        e.preventDefault()
        navigate(href)
      }}
    >
      {children}
    </a>
  )
}

const MARKDOWN_COMPONENTS = {
  h1: (p: { children?: React.ReactNode }) => (
    <h2 className="text-lg font-semibold text-[var(--color-text)] mt-1 mb-3">{p.children}</h2>
  ),
  h2: (p: { children?: React.ReactNode }) => (
    <h2
      id={slugify(String(p.children ?? ''))}
      className="text-base font-semibold text-[var(--color-text)] mt-6 mb-2 scroll-mt-4"
    >
      {p.children}
    </h2>
  ),
  h3: (p: { children?: React.ReactNode }) => (
    <h3
      id={slugify(String(p.children ?? ''))}
      className="text-[14px] font-semibold text-[var(--color-text)] mt-4 mb-2 scroll-mt-4"
    >
      {p.children}
    </h3>
  ),
  p: (p: { children?: React.ReactNode }) => (
    <p className="text-[13.5px] leading-relaxed text-[var(--color-text-muted)] mb-3">{p.children}</p>
  ),
  ul: (p: { children?: React.ReactNode }) => (
    <ul className="list-disc pl-5 mb-3 text-[13.5px] leading-relaxed text-[var(--color-text-muted)] space-y-1">
      {p.children}
    </ul>
  ),
  ol: (p: { children?: React.ReactNode }) => (
    <ol className="list-decimal pl-5 mb-3 text-[13.5px] leading-relaxed text-[var(--color-text-muted)] space-y-1">
      {p.children}
    </ol>
  ),
  strong: (p: { children?: React.ReactNode }) => (
    <strong className="font-semibold text-[var(--color-text)]">{p.children}</strong>
  ),
  blockquote: (p: { children?: React.ReactNode }) => (
    <div
      className="rounded-lg border px-3 py-2.5 mb-3 text-[13px] leading-relaxed"
      style={{
        borderColor: 'color-mix(in srgb, var(--color-accent) 35%, transparent)',
        background: 'color-mix(in srgb, var(--color-accent) 8%, transparent)',
        color: 'var(--color-text-muted)',
      }}
    >
      {p.children}
    </div>
  ),
  table: (p: { children?: React.ReactNode }) => (
    <div className="overflow-x-auto mb-3">
      <table className="w-full text-[13px] border border-[var(--color-border)] rounded-lg overflow-hidden">
        {p.children}
      </table>
    </div>
  ),
  thead: (p: { children?: React.ReactNode }) => (
    <thead className="bg-[var(--color-bg-secondary)]">{p.children}</thead>
  ),
  th: (p: { children?: React.ReactNode }) => (
    <th className="text-left px-3 py-2 font-medium text-[var(--color-text)]">{p.children}</th>
  ),
  tr: (p: { children?: React.ReactNode }) => (
    <tr className="border-t border-[var(--color-border)]">{p.children}</tr>
  ),
  td: (p: { children?: React.ReactNode }) => (
    <td className="px-3 py-2 text-[var(--color-text-muted)] align-top">{p.children}</td>
  ),
  a: (p: { href?: string; children?: React.ReactNode }) => (
    <DocLink href={p.href}>{p.children}</DocLink>
  ),
  // react-markdown renders a fenced block as <pre><code class="language-x">.
  // The BLOCK decides its own container here, rather than the inner <code>
  // rendering a second one inside the wrapper: a <figure> inside a <pre> is
  // invalid HTML (pre takes phrasing content), and so is a <pre> inside a
  // <pre>. React builds the DOM through createElement, so nothing corrects
  // this the way an HTML parser would — the bad nesting really is in the tree.
  //
  // It was not cosmetic. A <pre> makes its contents inherit MONOSPACE, so the
  // diagram host inherited monospace while Mermaid measured its labels against
  // the body's sans font, and every label came out clipped.
  pre: (p: { children?: React.ReactNode }) => {
    const child = isValidElement(p.children) ? p.children : null
    const props = (child?.props ?? {}) as { className?: string; children?: React.ReactNode }
    const language = /language-(\w+)/.exec(props.className ?? '')?.[1]
    const source = String(props.children ?? '')

    if (language === 'mermaid') return <MermaidDiagram source={source} />

    return (
      <pre
        className="overflow-x-auto rounded-lg border p-3 mb-3 text-[12.5px] leading-relaxed"
        style={{
          borderColor: 'var(--color-border)',
          background: 'var(--color-bg-secondary)',
          color: 'var(--color-text)',
        }}
      >
        <code>{source}</code>
      </pre>
    )
  },
  // Inline code only — a fenced block is handled by `pre` above.
  code: (p: { children?: React.ReactNode }) => (
    <code
      className="rounded px-1 py-0.5 text-[12.5px]"
      style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)' }}
    >
      {p.children}
    </code>
  ),
}

export default function DocsPage() {
  const { docId } = useParams<{ docId?: string }>()
  const navigate = useNavigate()
  const { hash } = useLocation()
  const [filter, setFilter] = useState('')

  const active: DocPage = findDocPage(docId) ?? findDocPage(DEFAULT_DOC_ID) ?? DOC_PAGES[0]
  const source = DOC_SOURCES[active.id] ?? ''
  const toc = useMemo(() => headingsOf(source), [source])

  // Scroll a `#fragment` deep link onto its heading once the Markdown is in the
  // DOM. This is what makes a cross-page anchor (e.g. a link to
  // `/docs/ingestion#suite-name-resolution` from another topic) land on the
  // section rather than the top of the page: the heading only gains its id
  // after React renders the content, which is too late for the browser's own
  // load-time scroll. Re-runs when the topic or the fragment changes, and stays
  // a no-op in environments without `scrollIntoView` (jsdom).
  useEffect(() => {
    const id = decodeURIComponent(hash.replace(/^#/, ''))
    if (!id) return
    const el = document.getElementById(id)
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
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

            <article aria-label={active.label}>
              {source ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
                  {source}
                </ReactMarkdown>
              ) : (
                <p className="text-[13.5px] text-[var(--color-text-muted)]">
                  This topic has no content yet.
                </p>
              )}
            </article>
          </div>
        </div>
      </div>
    </>
  )
}
