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

type Children = { children?: React.ReactNode }

const COMPONENTS = {
  p: (p: Children) => <p className="mb-2 last:mb-0 leading-relaxed">{p.children}</p>,
  ul: (p: Children) => <ul className="list-disc pl-5 mb-2 space-y-0.5">{p.children}</ul>,
  ol: (p: Children) => <ol className="list-decimal pl-5 mb-2 space-y-0.5">{p.children}</ol>,
  h1: (p: Children) => <h3 className="text-[14px] font-semibold mt-3 mb-1.5">{p.children}</h3>,
  h2: (p: Children) => <h3 className="text-[14px] font-semibold mt-3 mb-1.5">{p.children}</h3>,
  h3: (p: Children) => <h4 className="text-[13.5px] font-semibold mt-3 mb-1">{p.children}</h4>,
  strong: (p: Children) => <strong className="font-semibold text-[var(--color-text)]">{p.children}</strong>,
  table: (p: Children) => (
    <div className="overflow-x-auto my-2">
      <table className="w-full text-[12.5px] border border-[var(--color-border)] rounded-lg overflow-hidden">
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
    <code className="rounded px-1 py-0.5 text-[12px] bg-[var(--color-bg-hover)] text-[var(--color-text)]">
      {p.children}
    </code>
  ),
  pre: (p: Children) => (
    <pre className="my-2 overflow-x-auto rounded-lg p-3 text-[12px] bg-[var(--color-bg-hover)]">{p.children}</pre>
  ),
  a: (p: { href?: string; children?: React.ReactNode }) =>
    p.href?.startsWith('/') ? (
      <Link to={p.href} className="text-[var(--color-accent)] underline underline-offset-2">{p.children}</Link>
    ) : (
      <a href={p.href} target="_blank" rel="noreferrer noopener" className="text-[var(--color-accent)] underline underline-offset-2">
        {p.children}
      </a>
    ),
}

function ChatMarkdown({ text }: { text: string }) {
  return (
    <div className="text-[13.5px] text-[var(--color-text)] break-words" data-testid="chat-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  )
}

export default memo(ChatMarkdown)
