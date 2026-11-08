import { Eye } from 'lucide-react'

/** Where a demo visitor goes to get their own copy. */
export const GET_TESTLOOKUP_URL = 'https://testlookup.app'

/**
 * The public read-only demo's standing notice. It sets expectations before the
 * first refused save: nothing a visitor does is kept, and the data is sample
 * data that is rebuilt every night.
 *
 * AppLayout lazy-loads this only when the server is the demo (`useDemoMode`),
 * so a normal deployment never downloads it (scripts/check-bundle-budget.mjs).
 */
export default function DemoBanner() {
  return (
    <div
      role="status"
      data-testid="demo-banner"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-4 py-1.5 text-xs text-[var(--color-text-secondary)]"
    >
      <Eye className="h-3.5 w-3.5 flex-shrink-0 text-[var(--color-accent)]" aria-hidden="true" />
      <span>
        <strong className="font-semibold text-[var(--color-text)]">Live demo</strong>
        {' · read-only · sample data, reset every night'}
      </span>
      <a
        href={GET_TESTLOOKUP_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="ml-auto font-medium text-[var(--color-accent)] hover:underline"
      >
        Get TestLookup
      </a>
    </div>
  )
}
