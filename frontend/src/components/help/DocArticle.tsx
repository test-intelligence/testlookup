/**
 * The documentation's Markdown renderer, shared by the Documentation page and
 * the help drawer (UX redesign P1). Moved here from `DocsPage.tsx` unchanged,
 * with one addition: `DocLinkFollowContext` lets a host take over an in-app
 * link (the drawer opens another topic in place instead of leaving the page).
 */
import { isValidElement, useContext, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Check, Copy } from 'lucide-react'
import toast from 'react-hot-toast'

import MermaidDiagram from '@/components/guide/MermaidDiagram'
import { copyTextToClipboard } from '@/utils/clipboard'
import { slugify } from '@/content/guide/slug'
import { DocLinkFollowContext } from './docLinks'


/**
 * Plain-text content of a rendered React node tree — the text a heading reads
 * as once its inline Markdown (code, bold, links) is stripped.
 *
 * A heading's id must equal `slugify` of its TEXT: that is what `anchorIds`
 * (slug.ts) computes from the raw Markdown, and what both the on-page TOC and
 * every cross-page `#fragment` link resolve against. Slugifying
 * `String(children)` instead breaks the moment a heading carries any inline
 * Markdown — `### \`defect_commander\`` arrives here as a `<code>` React
 * element, and `String(element)` is `"[object Object]"`, so the heading
 * rendered `id="object-object"` while the TOC linked `#defect_commander` and
 * the anchor scrolled nowhere (and two such headings would collide on that one
 * bogus id). Reading the node's text keeps the rendered id in lockstep with the
 * slug the TOC and fragment links target.
 */
function nodeText(node: React.ReactNode): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeText).join('')
  if (isValidElement(node)) {
    return nodeText((node.props as { children?: React.ReactNode }).children)
  }
  return ''
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
export function DocLink({ href, children }: { href?: string; children?: React.ReactNode }) {
  const navigate = useNavigate()
  const follow = useContext(DocLinkFollowContext)
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
        if (follow?.(href)) return
        navigate(href)
      }}
    >
      {children}
    </a>
  )
}

/**
 * A fenced (non-Mermaid) code block, with a copy-to-clipboard control.
 *
 * The self-host guide is largely runnable commands — `curl` ingest calls,
 * report-file uploads, environment exports. A reader following the getting-
 * started page copies those verbatim, and re-typing a multi-line `curl` by hand
 * is exactly the friction the in-app guide exists to remove. The button reuses
 * `copyTextToClipboard`, which falls back to a `<textarea>` + `execCommand`
 * path when the async Clipboard API is unavailable (an http self-host is not a
 * secure context, so `navigator.clipboard` is absent there).
 *
 * The button sits in the padding of the block, not inside the scrolling `<pre>`,
 * so it stays pinned to the corner while a wide command scrolls beneath it.
 */
function DocCodeBlock({ source }: { source: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    const ok = await copyTextToClipboard(source)
    if (!ok) {
      toast.error('Clipboard access denied — copy manually')
      return
    }
    setCopied(true)
    toast.success('Copied to clipboard')
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative mb-3">
      <button
        type="button"
        onClick={handleCopy}
        aria-label={copied ? 'Copied' : 'Copy code'}
        className="absolute top-2 right-2 z-10 rounded-md border p-1.5 transition-colors"
        style={{
          borderColor: 'var(--color-border)',
          background: 'var(--color-bg)',
          color: 'var(--color-text-muted)',
        }}
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-[var(--status-passed)]" aria-hidden="true" />
        ) : (
          <Copy className="h-3.5 w-3.5" aria-hidden="true" />
        )}
      </button>
      <pre
        className="overflow-x-auto rounded-lg border p-3 pr-12 text-[12.5px] leading-relaxed"
        style={{
          borderColor: 'var(--color-border)',
          background: 'var(--color-bg-secondary)',
          color: 'var(--color-text)',
        }}
      >
        <code>{source}</code>
      </pre>
    </div>
  )
}

const MARKDOWN_COMPONENTS = {
  h1: (p: { children?: React.ReactNode }) => (
    <h2 className="text-lg font-semibold text-[var(--color-text)] mt-1 mb-3">{p.children}</h2>
  ),
  h2: (p: { children?: React.ReactNode }) => (
    <h2
      id={slugify(nodeText(p.children))}
      className="text-base font-semibold text-[var(--color-text)] mt-6 mb-2 scroll-mt-4"
    >
      {p.children}
    </h2>
  ),
  h3: (p: { children?: React.ReactNode }) => (
    <h3
      id={slugify(nodeText(p.children))}
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

    return <DocCodeBlock source={source} />
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

/** One documentation topic's Markdown, rendered. */
export function DocMarkdown({ source, hideTitle = false }: { source: string; hideTitle?: boolean }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={hideTitle ? WITHOUT_TITLE : MARKDOWN_COMPONENTS}>
      {source}
    </ReactMarkdown>
  )
}

/** For a host that already shows the topic's title (the help drawer's header). */
const WITHOUT_TITLE = { ...MARKDOWN_COMPONENTS, h1: () => null }
