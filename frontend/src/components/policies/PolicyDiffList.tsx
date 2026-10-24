/**
 * W11 GATE-02: the Compare list — one line per changed setting, the values in
 * mono (`GO threshold 20 → 25`), or `No settings differ.`. Every name (a rule's,
 * a setting's) is React text.
 */
import { NO_DIFFERENCES, type PolicyChange } from './policyDiff'

export default function PolicyDiffList({ changes }: { changes: readonly PolicyChange[] }) {
  if (changes.length === 0) {
    return <p data-policy-diff="" className="m-0 text-sm text-[var(--color-text-muted)]">{NO_DIFFERENCES}</p>
  }
  return (
    <ul data-policy-diff="" aria-label="Changed settings" className="m-0 space-y-1 pl-0 text-sm text-[var(--color-text-secondary)]">
      {changes.map((change) => (
        <li key={`${change.section}:${change.key}:${change.text}`} data-policy-change={change.section} className="list-none">
          {change.before !== null && change.after !== null && change.section !== 'rules' ? (
            <>
              <span className="text-[var(--color-text)]">{change.label}</span>{' '}
              <span className="font-mono">{change.before} → {change.after}</span>
            </>
          ) : (
            <span>{change.text}</span>
          )}
        </li>
      ))}
    </ul>
  )
}
