/**
 * Markdown for chat answers, with GitHub tables.
 *
 * The chat used to render `ReactMarkdown` without `remark-gfm`, so every
 * answer the model formatted as a table (most lists of tests or builds)
 * arrived as rows of raw `|` characters. Styles follow the help articles
 * (`components/help/DocArticle.tsx`) with the answer's own text colour.
 */
import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link } from 'react-router-dom'

import { DEFAULT_RETURN_PATH, returnPathFromString } from '@/utils/returnPath'
import { isSafeExternalUrl } from '@/utils/safeUrl'

type Children = { children?: React.ReactNode }

const LINK_CLASS = 'text-[var(--color-accent)] underline underline-offset-2'

// A link in an answer is model output, and the model reads test logs anyone can
// write. An address that is not a safe http(s) URL is shown as plain text, and a
// link that leaves the app names its host, so the link text cannot hide where it
// goes (security audit 2026-11-18).
function externalLink(href: string | undefined, label: React.ReactNode, testId?: string) {
  if (!isSafeExternalUrl(href)) return <span>{label}</span>
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" data-testid={testId} className={LINK_CLASS}>
      {label} <span className="text-xs text-[var(--color-text-muted)]">({new URL(href).host})</span>
    </a>
  )
}

// An in-app path starts with one "/". "//host" and "/\host" leave the app, so the
// path goes through the same check as the sign-in return path before the router.
function inAppPath(href: string | undefined): string | null {
  const path = returnPathFromString(href)
  return path !== DEFAULT_RETURN_PATH || href?.startsWith(DEFAULT_RETURN_PATH) ? path : null
}

const COMPONENTS = {
  p: (p: Children) => <p className="mb-2 last:mb-0 leading-relaxed">{p.children}</p>,
  ul: (p: Children) => <ul className="list-disc pl-5 mb-2 space-y-0.5">{p.children}</ul>,
  ol: (p: Children) => <ol className="list-decimal pl-5 mb-2 space-y-0.5">{p.children}</ol>,
  h1: (p: Children) => <h3 className="text-base font-semibold mt-3 mb-1.5">{p.children}</h3>,
  h2: (p: Children) => <h3 className="text-base font-semibold mt-3 mb-1.5">{p.children}</h3>,
  h3: (p: Children) => <h4 className="text-base font-semibold mt-3 mb-1">{p.children}</h4>,
  strong: (p: Children) => <strong className="font-semibold text-[var(--color-text)]">{p.children}</strong>,
  table: (p: Children) => (
    <div className="overflow-x-auto my-2">
      <table className="w-full text-sm border border-[var(--color-border)] rounded-lg overflow-hidden">
        {p.children}
      </table>
    </div>
  ),
  thead: (p: Children) => <thead className="bg-[var(--color-bg-hover)]">{p.children}</thead>,
  th: (p: Children) => (
    <th className="text-left px-2.5 py-1.5 font-medium text-[var(--color-text)] whitespace-nowrap">{p.children}</th>
  ),
  tr: (p: Children) => <tr className="border-t border-[var(--color-border)]">{p.children}</tr>,
  td: (p: Children) => <td className="px-2.5 py-1.5 align-top text-[var(--color-text-secondary)]">{p.children}</td>,
  code: (p: Children) => (
    <code className="rounded px-1 py-0.5 text-xs bg-[var(--color-bg-hover)] text-[var(--color-text)]">
      {p.children}
    </code>
  ),
  pre: (p: Children) => (
    <pre className="my-2 overflow-x-auto rounded-lg p-3 text-xs bg-[var(--color-bg-hover)]">{p.children}</pre>
  ),
  // An image in an answer is a link, never loaded. The model's text can carry a
  // URL that an injected test log asked for (`![x](https://host/?q=<data>)`), and
  // loading it would send that data out without a click (OSS review 2026-11-12).
  img: (p: { src?: string; alt?: string }) =>
    p.src ? externalLink(p.src, p.alt ? `Image: ${p.alt}` : p.src, 'chat-image-link') : null,
  a: (p: { href?: string; children?: React.ReactNode }) => {
    const path = inAppPath(p.href)
    return path ? <Link to={path} className={LINK_CLASS}>{p.children}</Link> : externalLink(p.href, p.children)
  },
}

function ChatMarkdown({ text }: { text: string }) {
  return (
    <div className="text-base text-[var(--color-text)] break-words" data-testid="chat-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  )
}

export default memo(ChatMarkdown)
