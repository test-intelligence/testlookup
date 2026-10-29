/**
 * The run page's "Configuration drift" card (W23b FAIL-13), a separate card
 * on the Changes tab under the main one: this run's runtime, dependency,
 * image, environment and branch / namespace drift against the tab's own
 * baseline ("Against build #N, the baseline above").
 *
 * Rendered only when the regression-diff answer's `config_drift` parses
 * (`parseConfigDrift`): an older server, or a fixture without the field,
 * renders exactly the tab from before. A missing context reads "Not recorded
 * by this reporter…" — never "No configuration drift", which is said only for
 * two recorded contexts with nothing different. Values are React text and
 * wrap; a long one never widens the card.
 */
import type { ConfigDrift } from '@/types/configDrift'
import {
  CARD_TITLE, againstLine, cappedLine, contextSentence, driftSummary, entryLine, groupEntries,
  noDriftLine, onlyRunFieldsLine,
} from './configDriftModel'

export default function ConfigDriftSection({ drift }: { drift: ConfigDrift }) {
  const summary = driftSummary(drift)
  const context = contextSentence(drift)
  const noDrift = noDriftLine(drift)
  const onlyRun = onlyRunFieldsLine(drift)
  const capped = cappedLine(drift)
  const groups = groupEntries(drift)
  return (
    <section className="card" aria-label={CARD_TITLE} data-config-drift={drift.context_state}>
      <h2 className="m-0 text-sm font-semibold text-[var(--color-text)]">{CARD_TITLE}</h2>
      <p className="m-0 mt-0.5 text-xs text-[var(--color-text-muted)]">{againstLine(drift)}</p>
      {summary && (
        <p className="m-0 mt-3 text-sm text-[var(--color-text)] [overflow-wrap:anywhere]" data-config-drift-summary="">
          {summary}
        </p>
      )}
      {context && (
        <p className="m-0 mt-2 text-sm text-[var(--color-text-muted)]" data-config-drift-context="">{context}</p>
      )}
      {noDrift && (
        <p className="m-0 mt-3 text-sm text-[var(--color-text-muted)]" data-config-drift-none="">{noDrift}</p>
      )}
      {groups.length > 0 && (
        <div className="mt-3 space-y-3">
          {groups.map((g) => (
            <div key={g.section} data-config-drift-section={g.section}>
              <p className="eyebrow m-0 mb-1 uppercase text-[var(--color-text-muted)]">{g.label}</p>
              <ul className="m-0 list-none space-y-0.5 p-0">
                {g.entries.map((e, i) => (
                  <li
                    key={`${e.key}:${i}`}
                    className="text-sm text-[var(--color-text-secondary)] [overflow-wrap:anywhere]"
                    data-config-drift-entry={e.change}
                  >
                    <EntryText line={entryLine(e)} entryKey={e.key} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {onlyRun && <p className="m-0 mt-2 text-xs text-[var(--color-text-muted)]" data-config-drift-only-run="">{onlyRun}</p>}
      {capped && <p className="m-0 mt-2 text-xs text-[var(--color-text-muted)]" data-config-drift-capped="">{capped}</p>}
    </section>
  )
}

/** The key in mono, the rest of the line as plain text (the line itself is the model's). */
function EntryText({ line, entryKey }: { line: string; entryKey: string }) {
  if (!line.startsWith(entryKey)) return <>{line}</>
  return (
    <>
      <code className="font-mono text-sm text-[var(--color-text)]">{entryKey}</code>
      {line.slice(entryKey.length)}
    </>
  )
}
