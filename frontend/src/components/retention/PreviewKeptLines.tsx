/**
 * The purge preview's "what is kept, and what breaks" lines (W22a ACT-05 /
 * ACT-04), under the candidates table on /settings/retention:
 *
 *   • `The whole project is on hold — …` while a project hold is live;
 *   • `Kept by hold: <reason> — <n> runs, <m> packs` per live hold (its
 *     entity label muted beside it);
 *   • `Kept by the floor: …` when the keep-last floor keeps runs;
 *   • `Reference warnings:` + one line per broken link, or
 *     `Reference warnings were not measured.` (never "none");
 *   • the one note on what holds do not cover, while any hold is active.
 *
 * The copy lives in `holdsModel`; every reason and label is React text.
 * `ReferenceWarnings` is reused by the criteria-deletion preview.
 */
import { keptLines, referenceLines, type KeptLine, type PreviewKeptInput, type ReferenceLike } from './holdsModel'

export interface ReferenceWarningsProps {
  refs?: ReferenceLike[] | null
  measured?: boolean
}

/** `Reference warnings:` + the labels, or the not-measured line; nothing when nothing breaks. */
export function ReferenceWarnings({ refs, measured }: ReferenceWarningsProps) {
  const lines = referenceLines(refs, measured)
  if (!lines.length) return null
  const unmeasured = measured === false
  return (
    <div data-retention-reference-warnings="" className="space-y-0.5 text-xs">
      {lines.map((text, i) => (
        <p
          key={i}
          data-reference-line={unmeasured ? 'unmeasured' : i === 0 ? 'heading' : 'item'}
          className={
            unmeasured
              ? 'text-[var(--color-text-muted)]'
              : i === 0
                ? 'font-medium text-[var(--ui-warning-ink)]'
                : 'pl-3 text-[var(--color-text-secondary)]'
          }
        >
          {text}
        </p>
      ))}
    </div>
  )
}

function LineText({ line }: { line: KeptLine }) {
  switch (line.kind) {
    case 'project':
      return <p className="font-medium text-[var(--ui-warning-ink)]">{line.text}</p>
    case 'hold':
      return (
        <p className="flex min-w-0 items-baseline gap-2">
          <span className="min-w-0 truncate text-[var(--color-text)]" title={line.text}>
            {line.text}
          </span>
          {line.detail && (
            <span className="min-w-0 max-w-[40%] shrink-0 truncate text-[var(--color-text-muted)]" title={line.detail}>
              {line.detail}
            </span>
          )}
        </p>
      )
    case 'floor':
      return <p className="text-[var(--color-text)]">{line.text}</p>
    case 'reference-heading':
      return <p className="font-medium text-[var(--ui-warning-ink)]">{line.text}</p>
    case 'reference':
      return <p className="pl-3 text-[var(--color-text-secondary)]">{line.text}</p>
    case 'reference-unmeasured':
      return <p className="text-[var(--color-text-muted)]">{line.text}</p>
    case 'not-held-note':
      return <p className="text-[var(--color-text-faint)]">{line.text}</p>
    default:
      return <p>{line.text}</p>
  }
}

export interface PreviewKeptLinesProps {
  preview: PreviewKeptInput | null | undefined
}

/** Renders nothing for an older server that sends none of the fields. */
export default function PreviewKeptLines({ preview }: PreviewKeptLinesProps) {
  const lines = keptLines(preview)
  if (!lines.length) return null
  return (
    <div
      data-retention-preview-kept=""
      className="space-y-1 border-t border-[var(--color-border)] pt-2 text-xs"
    >
      {lines.map((line) => (
        <div key={line.key} data-kept-line={line.kind}>
          <LineText line={line} />
        </div>
      ))}
    </div>
  )
}
