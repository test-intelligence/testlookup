/**
 * The answer of "Preview" (W11 REL-09): where the rule in the form would put
 * the project's recent runs, saving nothing.
 *
 * Sentences first (`attributionModel.previewSentences`), then a sample of the
 * matched runs with the release each is in today and where the rule would put
 * it (`here` / `earlier rule` / `stays (named)`). Branches, tags and release
 * names are React text. The sample scrolls inside its own box, never the page.
 */
import { Link } from 'react-router-dom'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { formatCompactDateTime } from '@/utils/formatCompactDateTime'
import type { AttributionPreviewResponse } from '@/types/attributionRules'
import { landingText, previewSentences, tagsText } from './attributionModel'

interface Props {
  preview: AttributionPreviewResponse
  /** The previewed field's label (`Branch`), for "183 carry a branch". */
  fieldLabel: string
  /** The previewed target name, for the missing-release note. */
  targetName: string
}

/** `null` is a run with no release (Unattributed); a field an older answer lacks is unknown (`—`). */
function releaseToday(name: string | null | undefined): string {
  if (name === undefined) return '—'
  return name ?? 'Unattributed'
}

export default function AttributionPreview({ preview, fieldLabel, targetName }: Props) {
  const sentences = previewSentences(preview, fieldLabel, targetName)
  return (
    <div data-attribution-preview="" className="mt-4 space-y-3" aria-live="polite">
      <ul className="m-0 list-none space-y-1 p-0 text-sm text-[var(--color-text)]">
        {sentences.map((sentence) => (
          <li key={sentence}>{sentence}</li>
        ))}
      </ul>
      {preview.sample.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-[var(--color-border)]">
          <Table density="compact" aria-label="Matched runs">
            <thead>
              <tr>
                <Th>Build</Th>
                <Th>Branch</Th>
                <Th>Environment</Th>
                <Th>Tags</Th>
                <Th>Created</Th>
                <Th>Release today</Th>
                <Th>Would land</Th>
              </tr>
            </thead>
            <tbody>
              {preview.sample.map((run) => (
                <Tr key={run.run_id} interactive={false}>
                  <Td className="whitespace-nowrap">
                    <Link to={`/runs/${encodeURIComponent(run.run_id)}`} className="text-[var(--color-accent)] hover:underline">
                      {run.build_number || run.run_id.slice(0, 8)}
                    </Link>
                  </Td>
                  <Td className="max-w-[16rem] truncate font-mono text-xs" title={run.branch ?? undefined}>
                    {run.branch || '—'}
                  </Td>
                  <Td className="whitespace-nowrap">{run.environment || '—'}</Td>
                  <Td className="max-w-[12rem] truncate text-xs" title={tagsText(run.tags)}>
                    {tagsText(run.tags)}
                  </Td>
                  <Td className="whitespace-nowrap tabular-nums">{formatCompactDateTime(run.created_at ?? null)}</Td>
                  <Td className="max-w-[10rem] truncate" title={releaseToday(run.current_release_name)}>
                    {releaseToday(run.current_release_name)}
                  </Td>
                  <Td className="whitespace-nowrap font-medium text-[var(--color-text)]">{landingText(run.would_land)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  )
}
