/**
 * The narrowed commits, ranked (W23b FAIL-12): the commits the git host
 * listed between the test's last pass and its first failure, scored by the
 * existing suspect scorer (path overlap first, recency breaking ties).
 *
 * The rows reuse `SuspectsPanel`'s two-line layout: rank, SHA (a link only to
 * a server-built http(s) URL), author, the message truncated with a title, and
 * the score pill. Every value is React text.
 */
import type { BisectCommit } from '@/types/bisect'
import { safeCommitHref } from '@/pages/run/commitRangeReasons'
import { overlappingFiles } from './bisectModel'

export default function BisectCommitList({ commits }: { commits: BisectCommit[] }) {
  if (commits.length === 0) return null
  return (
    <ol data-bisect-commits="" className="m-0 flex list-none flex-col gap-2 p-0">
      {commits.map((c, i) => {
        const sha = (c.sha ?? '').slice(0, 8)
        const href = safeCommitHref(c.commit_url)
        const files = overlappingFiles(c.rationale)
        return (
          <li
            key={`${c.sha ?? 'nosha'}:${i}`}
            data-bisect-commit=""
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)]"
          >
            <div className="flex items-start justify-between gap-2 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="flex-none text-xs tabular-nums text-[var(--color-text-faint)]">#{i + 1}</span>
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      data-commit-link=""
                      className="flex-none font-mono text-sm font-medium text-[var(--color-accent)] hover:underline"
                    >
                      {sha || 'no sha'}
                    </a>
                  ) : (
                    <span className="flex-none font-mono text-sm font-medium text-[var(--color-text)]">{sha || 'no sha'}</span>
                  )}
                  <span className="min-w-0 truncate text-xs text-[var(--color-text-muted)]" title={c.author ?? undefined}>
                    {c.author ?? 'unknown author'}
                  </span>
                </div>
                <div className="mt-0.5 truncate text-xs text-[var(--color-text-secondary)]" title={c.message || undefined}>
                  {c.message || '(no message)'}
                </div>
                {files.length > 0 && (
                  <details className="mt-1">
                    <summary className="cursor-pointer select-none text-xs text-[var(--color-text-muted)]">
                      Overlapping files ({files.length})
                    </summary>
                    <ul className="m-0 mt-1 flex list-none flex-col gap-0.5 p-0">
                      {files.map((f) => (
                        <li key={f} className="truncate font-mono text-xs text-[var(--color-text-faint)]" title={f}>{f}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
              <span
                className="flex-none rounded border border-[var(--color-border)] px-2 py-0.5 text-xs font-semibold tabular-nums text-[var(--color-text)]"
                title="Suspect score (0–100): path overlap dominates, recency breaks ties"
              >
                {Number.isFinite(c.score) ? c.score.toFixed(0) : '–'}
              </span>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
